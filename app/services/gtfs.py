"""Build a Network from a static GTFS feed (zip or folder).

Use it with the official Augsburg data: the AVV publishes GTFS for the region, and
the nationwide DELFI feed (gtfs.de) covers it too. Set AUGSBURG_GTFS_PATH to the file.

For every route we take the most frequent stop pattern in direction 0 as the line,
its shape from shapes.txt (or the stop positions when the feed has no shapes), and a
headway estimated from how often that pattern runs.
"""
from __future__ import annotations

import csv
import io
import logging
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterator

from app.schemas.transit import Network, Station
from app.services.geo import haversine_m
from app.services.providers.base import build_line, compute_bbox

log = logging.getLogger(__name__)

# GTFS basic + extended (HVT) route types -> our modes
def mode_for(route_type: int) -> str | None:
    if route_type in (0,) or 900 <= route_type <= 906:
        return "tram"
    if route_type in (3, 11) or 700 <= route_type <= 716 or route_type == 800:
        return "bus"
    if route_type == 1 or 400 <= route_type <= 404:
        return "metro"
    if route_type == 2 or 100 <= route_type <= 117:
        return "rail"
    return None


SPEED = {"tram": 20.0, "bus": 18.0, "metro": 32.0, "rail": 55.0}
COLOR = {"tram": "#D7262E", "bus": "#0098D4", "metro": "#0069B4", "rail": "#5A6670"}


def _secs(hms: str) -> float | None:
    """GTFS time (may exceed 24:00:00) -> seconds after midnight."""
    try:
        h, m, sec = hms.strip().split(":")
        return int(h) * 3600 + int(m) * 60 + int(sec)
    except ValueError:
        return None


class _Feed:
    def __init__(self, path: Path):
        self.path = path
        self.zip = zipfile.ZipFile(path) if path.suffix == ".zip" else None

    def rows(self, name: str) -> Iterator[dict[str, str]]:
        if self.zip:
            names = {Path(n).name: n for n in self.zip.namelist()}
            if name not in names:
                return iter(())
            raw = self.zip.open(names[name])
            return csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
        f = self.path / name
        if not f.exists():
            return iter(())
        return csv.DictReader(open(f, encoding="utf-8-sig", newline=""))


def load_gtfs_network(
    path: str | Path,
    *,
    provider_id: str,
    modes: set[str],
    center: tuple[float, float],
    radius_m: float = 12_000,
    strip_prefixes: tuple[str, ...] = (),
) -> Network:
    feed = _Feed(Path(path))

    def clean(name: str) -> str:
        for p in strip_prefixes:
            if name.startswith(p):
                return name[len(p):].strip()
        return name.strip()

    # stops -> stations (parent station when present)
    stops: dict[str, tuple[str, float, float]] = {}
    parent: dict[str, str] = {}
    for r in feed.rows("stops.txt"):
        try:
            lat, lon = float(r["stop_lat"]), float(r["stop_lon"])
        except (KeyError, ValueError):
            continue
        stops[r["stop_id"]] = (clean(r.get("stop_name", r["stop_id"])), lat, lon)
        if r.get("parent_station"):
            parent[r["stop_id"]] = r["parent_station"]

    def station_of(stop_id: str) -> str:
        return parent.get(stop_id, stop_id)

    routes: dict[str, dict] = {}
    for r in feed.rows("routes.txt"):
        try:
            mode = mode_for(int(r.get("route_type", "-1")))
        except ValueError:
            continue
        if mode in modes:
            routes[r["route_id"]] = {**r, "mode": mode}

    trips: dict[str, tuple[str, int, str, str]] = {}
    for r in feed.rows("trips.txt"):
        if r["route_id"] in routes:
            trips[r["trip_id"]] = (r["route_id"], int(r.get("direction_id") or 0), r.get("shape_id", ""), r.get("service_id", ""))
    if not trips:
        raise ValueError("GTFS feed has no trips for the requested modes")

    seqs: dict[str, list[tuple[int, str]]] = defaultdict(list)
    window: dict[str, list[float]] = {}  # route -> [first departure, last arrival] in seconds
    for r in feed.rows("stop_times.txt"):
        tid = r["trip_id"]
        if tid in trips:
            seqs[tid].append((int(r["stop_sequence"]), r["stop_id"]))
            t = _secs(r.get("departure_time") or r.get("arrival_time") or "")
            if t is not None:
                w = window.setdefault(trips[tid][0], [t, t])
                w[0], w[1] = min(w[0], t), max(w[1], t)

    # weekdays each route runs on, from calendar.txt (calendar_dates exceptions are ignored)
    day_cols = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    service_days = {r["service_id"]: {i for i, c in enumerate(day_cols) if r.get(c) == "1"} for r in feed.rows("calendar.txt")}
    route_days: dict[str, set[int]] = defaultdict(set)
    for route_id, _, _, service in trips.values():
        route_days[route_id] |= service_days.get(service, set(range(7)))

    patterns: dict[tuple[str, int], Counter] = defaultdict(Counter)
    example: dict[tuple, str] = {}
    services: dict[tuple, set[str]] = defaultdict(set)
    for tid, seq in seqs.items():
        route_id, direction, _, service = trips[tid]
        stations = []
        for _, sid in sorted(seq):
            st = station_of(sid)
            if st not in stations[-1:]:
                stations.append(st)
        key = tuple(stations)
        patterns[(route_id, direction)][key] += 1
        example.setdefault((route_id, direction, key), tid)
        services[(route_id, direction, key)].add(service)

    # station table from parents (or the stops themselves)
    def station_info(st: str) -> tuple[str, float, float] | None:
        if st in stops:
            return stops[st]
        children = [stops[s] for s, p in parent.items() if p == st and s in stops]
        return children[0] if children else None

    # shapes, only for the trips we use
    chosen: list[tuple[str, tuple[str, ...], str]] = []
    for (route_id, direction), counter in patterns.items():
        if direction != 0 and (route_id, 0) in patterns:
            continue
        key, count = counter.most_common(1)[0]
        chosen.append((route_id, key, example[(route_id, direction, key)]))
    shape_ids = {trips[tid][2] for _, _, tid in chosen if trips[tid][2]}
    shapes: dict[str, list[tuple[int, float, float]]] = defaultdict(list)
    for r in feed.rows("shapes.txt"):
        if r["shape_id"] in shape_ids:
            shapes[r["shape_id"]].append((int(r["shape_pt_sequence"]), float(r["shape_pt_lon"]), float(r["shape_pt_lat"])))

    lines = []
    used_ids: set[str] = set()
    info_cache: dict[str, tuple[str, float, float]] = {}
    for route_id, key, tid in chosen:
        route = routes[route_id]
        infos = []
        for st in key:
            if st not in info_cache:
                inf = station_info(st)
                if inf is None:
                    break
                info_cache[st] = inf
            infos.append(info_cache[st])
        if len(infos) != len(key) or len(key) < 2:
            continue
        inside = sum(1 for _, lat, lon in infos if haversine_m((lon, lat), center) <= radius_m)
        if inside / len(infos) < 0.7:
            continue
        coords = [(lon, lat) for _, lat, lon in infos]
        shape_pts = sorted(shapes.get(trips[tid][2], []))
        shape = [(lon, lat) for _, lon, lat in shape_pts] if len(shape_pts) >= 2 else coords
        mode = route["mode"]
        short = (route.get("route_short_name") or route.get("route_long_name") or route_id).strip()
        line_id = short if short not in used_ids else route_id
        used_ids.add(line_id)
        count = patterns[(route_id, trips[tid][1])][key]
        days = max(1, len(services[(route_id, trips[tid][1], key)]))
        per_day = count / days
        headway = min(3600.0, max(180.0, 17 * 3600 / per_day)) if per_day else 1200.0
        color = route.get("route_color") or ""
        text = route.get("route_text_color") or ""
        loop = len(key) > 3 and key[0] == key[-1]
        st_ids, st_coords = (list(key[:-1]), coords[:-1]) if loop else (list(key), coords)
        lines.append(
            build_line(
                line_id=line_id,
                name=f"{'Tram' if mode == 'tram' else 'Bus' if mode == 'bus' else 'Line'} {short}",
                short_name=short[:6],
                color=f"#{color}" if color else COLOR[mode],
                text_color=f"#{text}" if text else "#ffffff",
                mode=mode,
                station_ids=st_ids,
                station_coords=st_coords,
                shape=shape,
                loop=loop,
                avg_speed_kmh=SPEED[mode],
                headway_s=round(headway),
                route_group=route_id,
            )
        )

    for line in lines:
        rid = line.route_group or ""
        if rid in window:
            line.service_start_s, line.service_end_s = window[rid]
        if service_days and route_days.get(rid):
            line.service_days = sorted(route_days[rid])

    served: dict[str, list[str]] = defaultdict(list)
    for line in lines:
        for st in line.stations:
            if line.id not in served[st]:
                served[st].append(line.id)
    stations = [Station(id=st, name=info_cache[st][0], lat=info_cache[st][1], lon=info_cache[st][2], lines=lids) for st, lids in served.items()]
    if not stations:
        raise ValueError("GTFS feed produced no lines inside the city area")
    log.info("GTFS: %d lines, %d stations from %s", len(lines), len(stations), path)
    return Network(provider=provider_id, lines=lines, stations=stations, bbox=compute_bbox(stations))
