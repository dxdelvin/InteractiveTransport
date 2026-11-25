"""Live-aware journey planner.

Graph
-----
  station hub  --board-->  platform(line, stop#, direction)  --ride-->  next platform
       ^                                   |
       +---------------alight--------------+
  station hub  --walk-->  nearby station hub        (short walking transfers)
  SOURCE --walk--> station hubs near the origin; station hubs near the destination --walk--> SINK

Search
------
A label-setting search (Dijkstra) over a *generalised cost* that also carries the clock.
Boarding waits are time-dependent: when we reach a platform at time t, the wait is the gap
until the next vehicle predicted to arrive there (from live positions), falling back to
half the headway beyond the prediction horizon. That is what makes "the 3-minute walk to
the other line is faster because its train is about to arrive" fall out naturally.
"""
from __future__ import annotations

import bisect
import heapq
import itertools
from dataclasses import dataclass, field

from app.schemas.planner import Journey, Leg, PlaceRef, PlanRequest, PlanResponse, Preference
from app.schemas.transit import Line
from app.services.geo import haversine_m
from app.services.providers.base import TransitProvider

WALK_SPEED_MPS = 1.3
AVOID_PENALTY_S = 1800.0  # a line to avoid is still used if nothing else gets there
WALK_DETOUR = 1.25  # street networks are not straight lines
MAX_ACCESS_M = 1600.0
MAX_TRANSFER_WALK_M = 450.0
DIRECT_WALK_MAX_M = 1800.0

WEIGHTS: dict[Preference, dict[str, float]] = {
    "fastest": {"transfer": 90.0, "walk": 1.0},
    "fewest_transfers": {"transfer": 900.0, "walk": 1.2},
    "least_walking": {"transfer": 120.0, "walk": 4.0},
}

Node = tuple  # ("src",) | ("dst",) | ("st", sid) | ("pl", line_id, stop_index, direction)


class PlanningError(ValueError):
    pass


@dataclass
class Access:
    station_id: str
    walk_m: float


@dataclass
class _Label:
    cost: float
    time: float
    node: Node
    prev: "_Label | None" = None
    edge: dict = field(default_factory=dict)
    boardings: int = 0


class Planner:
    def __init__(self, provider: TransitProvider):
        self.p = provider
        self.idx = provider._idx()
        self._predictions = self._build_predictions()
        # lines the timetable has out of service right now (night and school buses by day)
        running = {v.line_id for v in provider.vehicles.values()}
        self._idle = set(self.idx.lines) - running if running else set()
        self._walk_links = self._build_walk_links()

    # ---- precomputation -------------------------------------------------------------
    def _build_predictions(self) -> dict[tuple[str, int, int], list[tuple[float, str]]]:
        preds: dict[tuple[str, int, int], list[tuple[float, str]]] = {}
        for v in self.p.vehicles.values():
            for stop_i, direction, eta in self.p._upcoming(v):
                preds.setdefault((v.line_id, stop_i, direction), []).append((eta, v.id))
        for lst in preds.values():
            lst.sort()
        return preds

    def _build_walk_links(self) -> dict[str, list[tuple[str, float]]]:
        stations = list(self.idx.stations.values())
        links: dict[str, list[tuple[str, float]]] = {s.id: [] for s in stations}
        for a, b in itertools.combinations(stations, 2):
            d = haversine_m((a.lon, a.lat), (b.lon, b.lat)) * WALK_DETOUR
            if d <= MAX_TRANSFER_WALK_M:
                links[a.id].append((b.id, d))
                links[b.id].append((a.id, d))
        return links

    # ---- helpers --------------------------------------------------------------------
    def _wait(self, line: Line, stop_i: int, direction: int, t: float, use_live: bool) -> tuple[float, str | None]:
        if use_live:
            lst = self._predictions.get((line.id, stop_i, direction))
            if lst:
                k = bisect.bisect_left(lst, (t, ""))
                if k < len(lst):
                    return lst[k][0] - t, lst[k][1]
        return line.headway_s / 2, None

    def _next_stop(self, line: Line, i: int, direction: int) -> int | None:
        n = len(line.stations)
        if line.loop:
            return (i + 1) % n if direction == 0 else (i - 1) % n
        j = i + 1 if direction == 0 else i - 1
        return j if 0 <= j < n else None

    def _ride_time(self, line: Line, i: int, j: int, direction: int) -> tuple[float, float]:
        offs = line.station_offsets_m
        d = abs(offs[j] - offs[i])
        if line.loop and ((direction == 0 and j < i) or (direction == 1 and j > i)):
            d = line.length_m - d
        speed = max(line.avg_speed_kmh, 5) / 3.6
        return d / speed + self.p._dwell(line), d

    def resolve(self, ref: PlaceRef) -> tuple[str, list[Access], tuple[float, float]]:
        """Return (label, access stations, coordinate) for a place reference."""
        if ref.station_id or (ref.query and ref.lat is None):
            st = self.p.resolve_station(ref.station_id or ref.query or "")
            if st is None:
                raise PlanningError(f"No station matches '{ref.station_id or ref.query}'")
            return st.name, [Access(st.id, 0.0)], (st.lon, st.lat)
        assert ref.lat is not None and ref.lon is not None
        near = self.p.nearest_stations(ref.lon, ref.lat, k=5)
        access = [Access(s.id, d * WALK_DETOUR) for s, d in near if d * WALK_DETOUR <= MAX_ACCESS_M]
        if not access and near:
            s, d = near[0]
            access = [Access(s.id, d * WALK_DETOUR)]
        label = ref.label or f"{ref.lat:.4f}, {ref.lon:.4f}"
        return label, access, (ref.lon, ref.lat)

    # ---- search ---------------------------------------------------------------------
    def _search(
        self,
        origin: list[Access],
        dest: list[Access],
        pref: Preference,
        use_live: bool,
        line_penalty: dict[str, float] | None = None,
    ) -> _Label | None:
        w = WEIGHTS[pref]
        line_penalty = line_penalty or {}
        dest_map = {a.station_id: a.walk_m for a in dest}
        counter = itertools.count()
        start = _Label(0.0, 0.0, ("src",))
        heap: list[tuple[float, int, _Label]] = [(0.0, next(counter), start)]
        settled: set[Node] = set()

        def push(label: _Label) -> None:
            if label.node not in settled:
                heapq.heappush(heap, (label.cost, next(counter), label))

        while heap:
            _, _, cur = heapq.heappop(heap)
            if cur.node in settled:
                continue
            settled.add(cur.node)
            kind = cur.node[0]
            if kind == "dst":
                return cur
            if kind == "src":
                for a in origin:
                    t = a.walk_m / WALK_SPEED_MPS
                    push(_Label(cur.cost + t * w["walk"], cur.time + t, ("st", a.station_id), cur, {"type": "walk", "m": a.walk_m, "s": t}))
                continue
            if kind == "st":
                sid = cur.node[1]
                if sid in dest_map:
                    t = dest_map[sid] / WALK_SPEED_MPS
                    push(_Label(cur.cost + t * w["walk"], cur.time + t, ("dst",), cur, {"type": "walk", "m": dest_map[sid], "s": t}, cur.boardings))
                came_by_walk = cur.edge.get("type") == "walk" and cur.prev is not None and cur.prev.node[0] == "st"
                if not came_by_walk:  # no chaining of walking transfers
                    for other, m in self._walk_links.get(sid, []):
                        t = m / WALK_SPEED_MPS
                        push(_Label(cur.cost + t * w["walk"], cur.time + t, ("st", other), cur, {"type": "walk", "m": m, "s": t}, cur.boardings))
                for lid in self.idx.stations[sid].lines:
                    if use_live and lid in self._idle:
                        continue
                    line = self.idx.lines[lid]
                    for i, s in enumerate(line.stations):
                        if s != sid:
                            continue
                        for direction in (0, 1):
                            if self._next_stop(line, i, direction) is None:
                                continue
                            wait, vid = self._wait(line, i, direction, cur.time, use_live)
                            penalty = (w["transfer"] if cur.boardings else 0.0) + line_penalty.get(lid, 0.0)
                            push(
                                _Label(
                                    cur.cost + wait + penalty,
                                    cur.time + wait,
                                    ("pl", lid, i, direction),
                                    cur,
                                    {"type": "board", "wait": wait, "vehicle": vid},
                                    cur.boardings + 1,
                                )
                            )
                continue
            if kind == "pl":
                _, lid, i, direction = cur.node
                line = self.idx.lines[lid]
                # alight (only if we actually rode at least one stop)
                if cur.edge.get("type") == "ride":
                    push(_Label(cur.cost, cur.time, ("st", line.stations[i]), cur, {"type": "alight"}, cur.boardings))
                j = self._next_stop(line, i, direction)
                if j is not None:
                    t, m = self._ride_time(line, i, j, direction)
                    push(_Label(cur.cost + t, cur.time + t, ("pl", lid, j, direction), cur, {"type": "ride", "s": t, "m": m}, cur.boardings))
        return None

    # ---- journey assembly -----------------------------------------------------------
    def _geometry(self, line: Line, i: int, j: int, direction: int) -> list[tuple[float, float]]:
        poly = self.idx.polylines[line.id]
        a, b = line.station_offsets_m[i], line.station_offsets_m[j]
        if line.loop and direction == 0 and b < a:
            return poly.slice(a, line.length_m) + poly.slice(0, b)[1:]
        if line.loop and direction == 1 and b > a:
            return poly.slice(a, 0) + poly.slice(line.length_m, b)[1:]
        return poly.slice(a, b)

    def _to_journey(self, end: _Label, origin_label: str, dest_label: str, o_xy, d_xy) -> Journey:
        chain: list[_Label] = []
        node: _Label | None = end
        while node is not None:
            chain.append(node)
            node = node.prev
        chain.reverse()

        legs: list[Leg] = []
        stations = self.idx.stations

        def place(n: Node) -> tuple[str, str | None, tuple[float, float]]:
            if n[0] == "src":
                return origin_label, None, o_xy
            if n[0] == "dst":
                return dest_label, None, d_xy
            sid = n[1] if n[0] == "st" else self.idx.lines[n[1]].stations[n[2]]
            s = stations[sid]
            return s.name, s.id, (s.lon, s.lat)

        k = 1
        while k < len(chain):
            lab = chain[k]
            etype = lab.edge.get("type")
            if etype == "walk":
                fname, fid, fxy = place(lab.prev.node)  # type: ignore[union-attr]
                tname, tid, txy = place(lab.node)
                if lab.edge["m"] > 1:
                    legs.append(Leg(kind="walk", from_name=fname, to_name=tname, from_station_id=fid, to_station_id=tid,
                                    duration_s=round(lab.edge["s"]), distance_m=round(lab.edge["m"]), geometry=[fxy, txy]))
                k += 1
            elif etype == "board":
                _, lid, i0, direction = lab.node
                line = self.idx.lines[lid]
                wait, vid = lab.edge["wait"], lab.edge["vehicle"]
                ride_s = ride_m = 0.0
                stops = [stations[line.stations[i0]].name]
                geom: list[tuple[float, float]] = []
                i_prev = i0
                k += 1
                while k < len(chain) and chain[k].edge.get("type") == "ride":
                    _, _, j, _ = chain[k].node
                    ride_s += chain[k].edge["s"]
                    ride_m += chain[k].edge["m"]
                    seg = self._geometry(line, i_prev, j, direction)
                    geom.extend(seg if not geom else seg[1:])
                    stops.append(stations[line.stations[j]].name)
                    i_prev = j
                    k += 1
                if k < len(chain) and chain[k].edge.get("type") == "alight":
                    k += 1
                term = self.p.terminus_for(line, direction)
                legs.append(
                    Leg(
                        kind="ride",
                        from_name=stops[0],
                        to_name=stops[-1],
                        from_station_id=line.stations[i0],
                        to_station_id=line.stations[i_prev],
                        duration_s=round(ride_s),
                        wait_s=round(wait),
                        distance_m=round(ride_m),
                        geometry=[(round(x, 6), round(y, 6)) for x, y in geom],
                        line_id=line.id,
                        line_name=line.name,
                        color=line.color,
                        direction=direction,
                        headsign=("Loop via " + term.name) if line.loop else term.name,
                        stops=stops,
                        next_vehicle_id=vid,
                    )
                )
            else:
                k += 1

        rides = [l for l in legs if l.kind == "ride"]
        walking = sum(l.distance_m for l in legs if l.kind == "walk")
        summary = " → ".join(l.line_name or "" for l in rides) if rides else "Walk"
        return Journey(
            legs=legs,
            duration_s=round(end.time),
            transfers=max(0, len(rides) - 1),
            walking_m=round(walking),
            summary=summary,
        )

    def plan(self, req: PlanRequest) -> PlanResponse:
        o_label, o_access, o_xy = self.resolve(req.origin)
        d_label, d_access, d_xy = self.resolve(req.destination)
        if {a.station_id for a in o_access} & {a.station_id for a in d_access} and all(a.walk_m == 0 for a in o_access + d_access):
            raise PlanningError("Origin and destination are the same station")

        journeys: list[Journey] = []
        seen: set[tuple] = set()

        def add(j: Journey, tag: str) -> None:
            key = tuple((l.line_id, l.from_station_id, l.to_station_id) for l in j.legs if l.kind == "ride")
            if key in seen:
                for existing in journeys:
                    if tuple((l.line_id, l.from_station_id, l.to_station_id) for l in existing.legs if l.kind == "ride") == key and tag not in existing.tags:
                        existing.tags.append(tag)
                return
            seen.add(key)
            j.tags.append(tag)
            journeys.append(j)

        prefs: list[Preference] = [req.preference] + [p for p in ("fastest", "fewest_transfers", "least_walking") if p != req.preference]  # type: ignore[list-item]
        tag_for = {"fastest": "Fastest", "fewest_transfers": "Fewest transfers", "least_walking": "Least walking"}
        best_lines: list[str] = []
        avoid = {lid: AVOID_PENALTY_S for lid in req.avoid_lines}
        for pref in prefs:
            end = self._search(o_access, d_access, pref, req.use_live, avoid or None)
            if end is None:
                continue
            j = self._to_journey(end, o_label, d_label, o_xy, d_xy)
            add(j, tag_for[pref])
            if not best_lines:
                best_lines = [l.line_id for l in j.legs if l.kind == "ride" and l.line_id]

        # an alternative that avoids the main line(s) of the best option
        if best_lines and len(journeys) < req.alternatives:
            end = self._search(o_access, d_access, "fastest", req.use_live, {**{lid: 900.0 for lid in best_lines}, **avoid})
            if end is not None:
                add(self._to_journey(end, o_label, d_label, o_xy, d_xy), "Alternative")

        direct = haversine_m(o_xy, d_xy) * WALK_DETOUR
        if direct <= DIRECT_WALK_MAX_M:
            secs = direct / WALK_SPEED_MPS
            add(
                Journey(
                    legs=[Leg(kind="walk", from_name=o_label, to_name=d_label, duration_s=round(secs), distance_m=round(direct), geometry=[o_xy, d_xy])],
                    duration_s=round(secs), transfers=0, walking_m=round(direct), summary="Walk",
                ),
                "Walk only",
            )

        if not journeys:
            raise PlanningError("No route found between those places")
        journeys.sort(key=lambda j: j.duration_s)
        if journeys and "Fastest" not in journeys[0].tags:
            for j in journeys:
                if "Fastest" in j.tags:
                    j.tags.remove("Fastest")
            journeys[0].tags.insert(0, "Fastest")
        if req.avoid_lines:
            # "find another way": trips without the avoided lines first, the rest as a fallback
            avoided = set(req.avoid_lines)
            journeys.sort(key=lambda j: any(l.line_id in avoided for l in j.legs))
        return PlanResponse(origin_label=o_label, destination_label=d_label, journeys=journeys[: req.alternatives])
