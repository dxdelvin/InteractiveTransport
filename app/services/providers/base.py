"""Provider contract plus the logic every provider shares (ETAs, line status, search).

A provider owns two things:
  * a static ``Network`` (lines with shapes, stations), and
  * a live dict of ``Vehicle`` objects refreshed by ``refresh()``.

Everything else - arrivals boards, vehicle detail, line health - is derived here from
those two, so a new data source only has to implement loading and refreshing.
"""
from __future__ import annotations

import abc
import difflib
import logging
import time
import unicodedata
from dataclasses import dataclass

from app.schemas.transit import (
    Alert,
    Arrival,
    Line,
    LineStatus,
    Network,
    ProviderInfo,
    Station,
    StationDetail,
    StopEta,
    Vehicle,
    VehicleDetail,
    VehicleSnapshot,
)
from app.services.geo import LonLat, Polyline, haversine_m, nearest

log = logging.getLogger(__name__)

DEFAULT_DWELL_S = 25.0
DEFAULT_LAYOVER_S = 120.0
ETA_HORIZON_S = 3600.0


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(text.lower().replace("-", " ").replace("'", "").split())


@dataclass
class NetworkIndex:
    """Fast lookups over a Network."""

    network: Network
    lines: dict[str, Line]
    stations: dict[str, Station]
    polylines: dict[str, Polyline]

    @classmethod
    def build(cls, network: Network) -> "NetworkIndex":
        return cls(
            network=network,
            lines={l.id: l for l in network.lines},
            stations={s.id: s for s in network.stations},
            polylines={l.id: Polyline([tuple(c) for c in l.shape]) for l in network.lines},
        )


def build_line(
    *,
    line_id: str,
    name: str,
    short_name: str,
    color: str,
    mode: str,
    station_ids: list[str],
    station_coords: list[LonLat],
    shape: list[LonLat],
    avg_speed_kmh: float,
    headway_s: float,
    loop: bool = False,
    text_color: str = "#ffffff",
    route_group: str | None = None,
) -> Line:
    """Create a Line, linear-referencing each station onto the shape (monotonic)."""
    poly = Polyline(shape)
    offsets: list[float] = []
    last = -1.0
    for coord in station_coords:
        along, _ = poly.project(coord)
        if along < last:  # shapes can double back near terminals; keep order monotonic
            along = last
        offsets.append(along)
        last = along
    return Line(
        id=line_id,
        name=name,
        short_name=short_name,
        color=color,
        text_color=text_color,
        mode=mode,  # type: ignore[arg-type]
        loop=loop,
        stations=station_ids,
        shape=[(round(x, 6), round(y, 6)) for x, y in shape],
        station_offsets_m=[round(o, 1) for o in offsets],
        length_m=round(poly.length, 1),
        avg_speed_kmh=avg_speed_kmh,
        headway_s=headway_s,
        route_group=route_group,
    )


def compute_bbox(stations: list[Station], pad: float = 0.01) -> tuple[float, float, float, float]:
    lons = [s.lon for s in stations]
    lats = [s.lat for s in stations]
    return (min(lons) - pad, min(lats) - pad, max(lons) + pad, max(lats) + pad)


class TransitProvider(abc.ABC):
    """Base class for all data sources."""

    id: str = "base"
    refresh_interval_s: float = 1.0

    def __init__(self) -> None:
        self.index: NetworkIndex | None = None
        self.vehicles: dict[str, Vehicle] = {}
        self.last_refresh: float = 0.0
        self.status: str = "starting"
        self.status_detail: str | None = None

    # ---- to implement ---------------------------------------------------------------
    @abc.abstractmethod
    def describe(self) -> ProviderInfo: ...

    @abc.abstractmethod
    async def load_network(self) -> Network: ...

    @abc.abstractmethod
    async def fetch_vehicles(self) -> list[Vehicle]: ...

    def scenery(self) -> dict:
        """Optional procedural basemap (GeoJSON FeatureCollection)."""
        return {"type": "FeatureCollection", "features": []}

    async def live_departures(self, station_id: str) -> list[Arrival] | None:
        """Real departures from an external source, or None to use the derived board."""
        return None

    async def close(self) -> None:  # pragma: no cover - default no-op
        return None

    # ---- lifecycle ------------------------------------------------------------------
    @property
    def ready(self) -> bool:
        return self.index is not None

    async def ensure_network(self) -> NetworkIndex:
        if self.index is None:
            network = await self.load_network()
            self.index = NetworkIndex.build(network)
            log.info("%s: network loaded (%d lines, %d stations)", self.id, len(network.lines), len(network.stations))
        return self.index

    async def refresh(self) -> None:
        await self.ensure_network()
        vehicles = await self.fetch_vehicles()
        self.vehicles = {v.id: v for v in vehicles}
        self.last_refresh = time.time()
        self.status = "ok"
        self.status_detail = None

    def snapshot(self) -> VehicleSnapshot:
        return VehicleSnapshot(provider=self.id, timestamp=self.last_refresh or time.time(), vehicles=list(self.vehicles.values()))

    # ---- shared derived views -------------------------------------------------------
    def _idx(self) -> NetworkIndex:
        if self.index is None:
            raise RuntimeError(f"Provider {self.id} has no network loaded yet")
        return self.index

    def search_stations(self, query: str, limit: int = 8) -> list[Station]:
        idx = self._idx()
        q = normalize(query)
        if not q:
            return []
        scored: list[tuple[float, Station]] = []
        for st in idx.stations.values():
            name = normalize(st.name)
            if name == q:
                score = 3.0
            elif name.startswith(q):
                score = 2.0 + len(q) / max(len(name), 1)
            elif q in name:
                score = 1.5 + len(q) / max(len(name), 1)
            else:
                score = difflib.SequenceMatcher(None, q, name).ratio()
                if score < 0.55:
                    continue
            scored.append((score, st))
        scored.sort(key=lambda x: (-x[0], x[1].name))
        return [s for _, s in scored[:limit]]

    def resolve_station(self, query: str) -> Station | None:
        idx = self._idx()
        if query in idx.stations:
            return idx.stations[query]
        hits = self.search_stations(query, limit=1)
        return hits[0] if hits else None

    def nearest_stations(self, lon: float, lat: float, k: int = 3) -> list[tuple[Station, float]]:
        idx = self._idx()
        res = nearest(((s.id, (s.lon, s.lat)) for s in idx.stations.values()), (lon, lat), k=k)
        return [(idx.stations[sid], d) for sid, d in res]

    def _dwell(self, line: Line) -> float:
        return {"rail": 45.0, "tram": 20.0, "bus": 15.0}.get(line.mode, DEFAULT_DWELL_S)

    def _upcoming(self, v: Vehicle, horizon_s: float = ETA_HORIZON_S, through_turnaround: bool = True) -> list[tuple[int, int, float]]:
        """Predict (station_index, direction, eta_s) for the stations a vehicle will reach."""
        idx = self._idx()
        line = idx.lines.get(v.line_id)
        if line is None:
            return []
        offs = line.station_offsets_m
        n = len(offs)
        speed = max(line.avg_speed_kmh, 5.0) / 3.6
        dwell = self._dwell(line)
        out: list[tuple[int, int, float]] = []
        pos, direction, t = v.shape_dist_m, v.direction, 0.0
        eps = 5.0

        if line.loop:
            L = line.length_m
            order = list(range(n)) if direction == 0 else list(range(n - 1, -1, -1))
            # distance travelled to each station going around
            dists = []
            for i in order:
                d = (offs[i] - pos) % L if direction == 0 else (pos - offs[i]) % L
                if d < eps:
                    d += L if v.status != "stopped" else 0.0
                dists.append((d, i))
            dists.sort()
            stops_passed = 0
            for d, i in dists:
                eta = d / speed + stops_passed * dwell
                out.append((i, direction, eta))
                stops_passed += 1
            return [o for o in out if o[2] <= horizon_s]

        # linear line: run to the end of this direction, then (optionally) come back
        legs = 2 if through_turnaround else 1
        for leg in range(legs):
            # on the return run the vehicle departs from the terminus it just reached
            at_platform = (leg == 0 and v.status == "stopped") or leg > 0
            if direction == 0:
                seq = [i for i in range(n) if offs[i] > pos + eps or (at_platform and abs(offs[i] - pos) <= eps)]
            else:
                seq = [i for i in range(n - 1, -1, -1) if offs[i] < pos - eps or (at_platform and abs(offs[i] - pos) <= eps)]
            last_pos = pos
            for k, i in enumerate(seq):
                t += abs(offs[i] - last_pos) / speed + (dwell if k else 0.0)
                last_pos = offs[i]
                if t > horizon_s:
                    return out
                out.append((i, direction, t))
            # turnaround: lay over at the terminus, then run back from it
            t += DEFAULT_LAYOVER_S
            direction = 1 - direction
            pos = offs[-1] if direction == 1 else offs[0]
        return out

    def terminus_for(self, line: Line, direction: int) -> Station:
        idx = self._idx()
        if line.loop:
            # loops have no terminus - name the station half-way round as "via"
            sid = line.stations[len(line.stations) // 2]
            return idx.stations[sid]
        sid = line.stations[-1] if direction == 0 else line.stations[0]
        return idx.stations[sid]

    def station_arrivals(self, station_id: str, limit: int = 12) -> StationDetail:
        idx = self._idx()
        station = idx.stations[station_id]
        arrivals: list[Arrival] = []
        for v in self.vehicles.values():
            line = idx.lines.get(v.line_id)
            if line is None or station_id not in line.stations:
                continue
            positions = [i for i, sid in enumerate(line.stations) if sid == station_id]
            for st_i, direction, eta in self._upcoming(v):
                if st_i in positions:
                    if not line.loop and ((direction == 0 and st_i == len(line.stations) - 1) or (direction == 1 and st_i == 0)):
                        continue  # arriving at terminus - not boardable
                    term = self.terminus_for(line, direction)
                    arrivals.append(
                        Arrival(
                            vehicle_id=v.id,
                            line_id=line.id,
                            line_name=line.name,
                            color=line.color,
                            headsign=("Loop via " + term.name) if line.loop else term.name,
                            direction=direction,
                            eta_s=round(eta, 1),
                            delay_s=v.delay_s,
                        )
                    )
                    break
        arrivals.sort(key=lambda a: a.eta_s)
        return StationDetail(station=station, arrivals=arrivals[:limit])

    def next_departure(self, line_id: str, station_id: str, direction: int) -> tuple[str, float] | None:
        """Soonest (vehicle_id, eta_s) of a vehicle on a line, in a direction, at a station."""
        idx = self._idx()
        line = idx.lines[line_id]
        targets = {i for i, sid in enumerate(line.stations) if sid == station_id}
        best: tuple[str, float] | None = None
        for v in self.vehicles.values():
            if v.line_id != line_id:
                continue
            for st_i, d, eta in self._upcoming(v):
                if st_i in targets and d == direction:
                    if best is None or eta < best[1]:
                        best = (v.id, eta)
                    break
        return best

    def vehicle_detail(self, vehicle_id: str) -> VehicleDetail:
        idx = self._idx()
        v = self.vehicles[vehicle_id]
        line = idx.lines[v.line_id]
        upcoming = [
            StopEta(station_id=line.stations[i], name=idx.stations[line.stations[i]].name, eta_s=round(eta, 1))
            for i, d, eta in self._upcoming(v, through_turnaround=False)
            if d == v.direction
        ]
        dest = self.terminus_for(line, v.direction)
        frac = v.shape_dist_m / line.length_m if line.length_m else 0.0
        progress = frac if v.direction == 0 else 1 - frac
        nxt = idx.stations.get(v.next_station_id) if v.next_station_id else None
        return VehicleDetail(
            vehicle=v,
            line_name=line.name,
            color=line.color,
            mode=line.mode,
            destination_name=dest.name,
            next_station_name=nxt.name if nxt else (upcoming[0].name if upcoming else None),
            upcoming=upcoming,
            progress=round(max(0.0, min(1.0, progress)), 4),
        )

    def line_status(self, line_id: str) -> LineStatus:
        idx = self._idx()
        line = idx.lines[line_id]
        vs = [v for v in self.vehicles.values() if v.line_id == line_id]
        avg_delay = sum(v.delay_s for v in vs) / len(vs) if vs else 0.0
        headway = None
        if vs:
            speed = max(line.avg_speed_kmh, 5) / 3.6
            cycle = line.length_m / speed if line.loop else 2 * line.length_m / speed
            per_dir = max(1, len(vs) // 2) if line.loop else len(vs)
            headway = round(cycle / per_dir, 0)
        if not vs:
            health = "no_service"
        elif avg_delay < 90:
            health = "good"
        elif avg_delay < 300:
            health = "minor_delays"
        else:
            health = "major_delays"
        return LineStatus(
            line_id=line.id,
            name=line.name,
            color=line.color,
            mode=line.mode,
            vehicles_active=len(vs),
            avg_delay_s=round(avg_delay, 1),
            effective_headway_s=headway,
            health=health,  # type: ignore[arg-type]
        )

    def delay_station(self, line_id: str) -> str | None:
        """Station where a line's delay was observed (providers with live boards override)."""
        return None

    def alerts(self) -> list[Alert]:
        """Late or idle lines, worst first. Thresholds match line_status."""
        idx = self._idx()
        out: list[Alert] = []
        for line in idx.lines.values():
            vs = [v for v in self.vehicles.values() if v.line_id == line.id]
            label = f"{'Bus' if line.mode == 'bus' else 'Tram' if line.mode == 'tram' else 'Line'} {line.short_name}"
            if not vs:
                out.append(Alert(line_id=line.id, severity="no_service", text=f"{label} is not running now"))
                continue
            worst = max(vs, key=lambda v: v.delay_s)
            if worst.delay_s < 120:
                continue
            sid = self.delay_station(line.id) or worst.next_station_id or worst.current_station_id
            st = idx.stations.get(sid) if sid else None
            mins = round(worst.delay_s / 60)
            out.append(
                Alert(
                    line_id=line.id,
                    severity="major" if worst.delay_s >= 300 else "minor",
                    delay_s=worst.delay_s,
                    station_id=st.id if st else None,
                    station_name=st.name if st else None,
                    source=worst.delay_source,
                    text=f"{label} +{mins} min" + (f" at {st.name}" if st else ""),
                )
            )
        rank = {"major": 0, "minor": 1, "no_service": 2}
        out.sort(key=lambda a: (rank[a.severity], -a.delay_s))
        return out

    def station_distance(self, a: str, b: str) -> float:
        idx = self._idx()
        sa, sb = idx.stations[a], idx.stations[b]
        return haversine_m((sa.lon, sa.lat), (sb.lon, sb.lat))
