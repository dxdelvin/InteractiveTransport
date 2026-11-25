"""Live provider for Boston using the public MBTA V3 API (https://api-v3.mbta.com).

No key is needed for light use (~20 requests/min); set MBTA_API_KEY for more.

Network: one Line per *typical* route pattern in direction 0, so branches such as
Red Line Ashmont / Braintree or the Green Line B/C/D/E become separate lines that
share stations. Vehicles are matched to the nearest pattern of their route.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any

import httpx

from app.core.config import Settings
from app.schemas.transit import Network, ProviderInfo, Station, Vehicle
from app.services.geo import Polyline, decode_polyline
from app.services.providers.base import TransitProvider, build_line, compute_bbox

log = logging.getLogger(__name__)

MODE_BY_TYPE = {0: "light_rail", 1: "metro", 2: "rail", 3: "bus", 4: "ferry"}
SPEED_BY_TYPE = {0: 22.0, 1: 32.0, 2: 55.0, 3: 16.0, 4: 20.0}
HEADWAY_BY_TYPE = {0: 420.0, 1: 360.0, 2: 1800.0, 3: 900.0, 4: 1800.0}
STATUS_MAP = {"STOPPED_AT": "stopped", "INCOMING_AT": "incoming", "IN_TRANSIT_TO": "in_transit"}
OCCUPANCY_MAP = {
    "MANY_SEATS_AVAILABLE": "low",
    "FEW_SEATS_AVAILABLE": "medium",
    "STANDING_ROOM_ONLY": "high",
    "CRUSHED_STANDING_ROOM_ONLY": "high",
    "FULL": "high",
}


def _rel_id(resource: dict, name: str) -> str | None:
    data = (resource.get("relationships", {}).get(name) or {}).get("data")
    return data.get("id") if isinstance(data, dict) else None


def _rel_ids(resource: dict, name: str) -> list[str]:
    data = (resource.get("relationships", {}).get(name) or {}).get("data") or []
    return [d["id"] for d in data if isinstance(d, dict)]


def _parse_ts(value: str | None) -> float:
    if not value:
        return dt.datetime.now(dt.timezone.utc).timestamp()
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def parse_network(routes_doc: dict, patterns_doc: dict, platforms_doc: dict) -> tuple[Network, dict[str, str]]:
    """Turn MBTA JSON:API documents into a Network plus a platform->station map.

    Kept free of I/O so it can be unit-tested with fixture documents.
    """
    routes = {r["id"]: r for r in routes_doc.get("data", [])}
    included = {(i["type"], i["id"]): i for i in patterns_doc.get("included", [])}

    platform_to_station: dict[str, str] = {}
    station_meta: dict[str, dict[str, Any]] = {}
    for stop in platforms_doc.get("data", []) + [i for i in patterns_doc.get("included", []) if i["type"] == "stop"]:
        parent = _rel_id(stop, "parent_station") or stop["id"]
        platform_to_station[stop["id"]] = parent
        a = stop["attributes"]
        if parent not in station_meta and a.get("latitude") is not None:
            station_meta[parent] = {"name": a.get("name") or parent, "lat": a["latitude"], "lon": a["longitude"]}

    lines = []
    for pat in patterns_doc.get("data", []):
        attrs = pat["attributes"]
        if attrs.get("direction_id", 0) != 0 or attrs.get("typicality", 1) != 1:
            continue
        route_id = _rel_id(pat, "route")
        trip = included.get(("trip", _rel_id(pat, "representative_trip") or ""))
        route = routes.get(route_id or "")
        if not (trip and route):
            continue
        shape_doc = included.get(("shape", _rel_id(trip, "shape") or ""))
        stop_ids = _rel_ids(trip, "stops")
        station_ids: list[str] = []
        for sid in stop_ids:
            st = platform_to_station.get(sid, sid)
            if st in station_meta and (not station_ids or station_ids[-1] != st):
                station_ids.append(st)
        if len(station_ids) < 2:
            continue
        coords = [(station_meta[s]["lon"], station_meta[s]["lat"]) for s in station_ids]
        shape = decode_polyline(shape_doc["attributes"]["polyline"]) if shape_doc else coords
        if len(shape) < 2:
            shape = coords
        ra = route["attributes"]
        rtype = ra.get("type", 1)
        pattern_name = attrs.get("name") or ra.get("long_name")
        same_route = [p for p in patterns_doc["data"] if _rel_id(p, "route") == route_id and p["attributes"].get("direction_id", 0) == 0 and p["attributes"].get("typicality", 1) == 1]
        name = ra.get("long_name") or route_id
        if len(same_route) > 1:
            name = f"{name} - {pattern_name.split(' - ')[-1]}"
        lines.append(
            build_line(
                line_id=pat["id"],
                name=name,
                short_name=(ra.get("short_name") or route_id or "?")[:8],
                color=f"#{ra.get('color') or '888888'}",
                text_color=f"#{ra.get('text_color') or 'FFFFFF'}",
                mode=MODE_BY_TYPE.get(rtype, "metro"),
                station_ids=station_ids,
                station_coords=coords,
                shape=shape,
                avg_speed_kmh=SPEED_BY_TYPE.get(rtype, 30.0),
                headway_s=HEADWAY_BY_TYPE.get(rtype, 600.0),
                route_group=route_id,
            )
        )

    served: dict[str, list[str]] = {}
    for line in lines:
        for sid in line.stations:
            served.setdefault(sid, [])
            if line.id not in served[sid]:
                served[sid].append(line.id)
    stations = [
        Station(id=sid, name=station_meta[sid]["name"], lon=station_meta[sid]["lon"], lat=station_meta[sid]["lat"], lines=lids)
        for sid, lids in served.items()
    ]
    if not stations:
        raise ValueError("MBTA returned no usable route patterns")
    return Network(provider="mbta", lines=lines, stations=stations, bbox=compute_bbox(stations)), platform_to_station


def parse_vehicles(
    doc: dict,
    lines_by_route: dict[str, list[str]],
    polylines: dict[str, Polyline],
    platform_to_station: dict[str, str],
    station_lines: dict[str, set[str]],
) -> list[Vehicle]:
    trips = {(i["type"], i["id"]): i for i in doc.get("included", [])}
    out: list[Vehicle] = []
    for v in doc.get("data", []):
        a = v["attributes"]
        if a.get("latitude") is None or a.get("longitude") is None:
            continue
        route_id = _rel_id(v, "route")
        candidates = lines_by_route.get(route_id or "", [])
        if not candidates:
            continue
        point = (a["longitude"], a["latitude"])
        station = platform_to_station.get(_rel_id(v, "stop") or "", None)
        # prefer patterns that actually serve the stop the vehicle is heading to
        preferred = [c for c in candidates if station and c in station_lines.get(station, set())] or candidates
        best_line, best_along, best_off = None, 0.0, float("inf")
        for lid in preferred:
            along, off = polylines[lid].project(point)
            if off < best_off:
                best_line, best_along, best_off = lid, along, off
        if best_line is None or best_off > 1500:
            continue
        trip = trips.get(("trip", _rel_id(v, "trip") or ""))
        status = STATUS_MAP.get(a.get("current_status") or "", "in_transit")
        speed = a.get("speed")
        out.append(
            Vehicle(
                id=v["id"],
                label=str(a.get("label") or v["id"]),
                line_id=best_line,
                lon=point[0],
                lat=point[1],
                bearing=float(a.get("bearing") or 0.0),
                speed_kmh=round(float(speed) * 3.6, 1) if speed is not None else 0.0,
                direction=0 if a.get("direction_id", 0) == 0 else 1,
                shape_dist_m=round(best_along, 1),
                status=status,  # type: ignore[arg-type]
                current_station_id=station if status == "stopped" else None,
                next_station_id=station if status != "stopped" else None,
                destination_station_id=None,
                headsign=(trip or {}).get("attributes", {}).get("headsign") or "",
                occupancy=OCCUPANCY_MAP.get(a.get("occupancy_status") or "", "unknown"),  # type: ignore[arg-type]
                delay_s=0,
                updated_at=_parse_ts(a.get("updated_at")),
            )
        )
    return out


class MBTAProvider(TransitProvider):
    id = "mbta"

    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self.refresh_interval_s = max(3.0, settings.mbta_poll_seconds)
        headers = {"Accept": "application/vnd.api+json", "User-Agent": "interactive-transport/1.0"}
        if settings.mbta_api_key:
            headers["x-api-key"] = settings.mbta_api_key
        self.client = httpx.AsyncClient(base_url=settings.mbta_base_url, headers=headers, timeout=15.0)
        self.route_types = settings.mbta_route_types
        self._platform_to_station: dict[str, str] = {}
        self._lines_by_route: dict[str, list[str]] = {}
        self._station_lines: dict[str, set[str]] = {}

    def describe(self) -> ProviderInfo:
        return ProviderInfo(
            id=self.id,
            name="Boston - MBTA (live)",
            city="Boston",
            kind="live",
            basemap="osm",
            center=(-71.0636, 42.3555),
            zoom=12.3,
            description="Real-time subway and light rail positions from the public MBTA V3 API.",
            status=self.status,
            status_detail=self.status_detail,
            attribution="Data: MBTA V3 API",
        )

    async def _get(self, path: str, params: dict[str, str]) -> dict:
        resp = await self.client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    async def load_network(self) -> Network:
        routes = await self._get("/routes", {"filter[type]": self.route_types})
        route_ids = ",".join(r["id"] for r in routes.get("data", []))
        patterns = await self._get(
            "/route_patterns",
            {
                "filter[route]": route_ids,
                "filter[direction_id]": "0",
                "include": "representative_trip.shape,representative_trip.stops",
            },
        )
        platforms = await self._get("/stops", {"filter[route_type]": self.route_types, "filter[location_type]": "0"})
        network, self._platform_to_station = parse_network(routes, patterns, platforms)
        self._lines_by_route = {}
        self._station_lines = {}
        for line in network.lines:
            self._lines_by_route.setdefault(line.route_group or line.id, []).append(line.id)
            for sid in line.stations:
                self._station_lines.setdefault(sid, set()).add(line.id)
        return network

    async def fetch_vehicles(self) -> list[Vehicle]:
        idx = self._idx()
        doc = await self._get("/vehicles", {"filter[route_type]": self.route_types, "include": "trip"})
        return parse_vehicles(doc, self._lines_by_route, idx.polylines, self._platform_to_station, self._station_lines)

    async def close(self) -> None:
        await self.client.aclose()
