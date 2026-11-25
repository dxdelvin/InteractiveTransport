"""Augsburg: swa trams and city buses.

Network   - the official GTFS feed when AUGSBURG_GTFS_PATH points to one, otherwise the
            bundled approximation in app/data/augsburg.py.
Vehicles  - simulated from the timetable (swa does not publish open vehicle positions).
            When EFA is reachable, each line's simulated fleet runs at the line's real
            delay, sampled from live departure boards at a few hub stops.
Departures- live from the Bavarian EFA journey planner (bahnland-bayern.de) when it's
            reachable, falling back to the simulation.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import statistics
import time
from pathlib import Path

import httpx

from app.core.config import Settings
from app.data import augsburg as data
from app.data import scenery
from app.schemas.transit import Arrival, Network, ProviderInfo, Station, Vehicle
from app.services.delaylog import DelayLog
from app.services.geo import catmull_rom
from app.services.gtfs import load_gtfs_network
from app.services.providers.base import TransitProvider, build_line, compute_bbox
from app.services.providers.simulation import LineSimulation

log = logging.getLogger(__name__)

DWELL = {"tram": 20.0, "bus": 15.0, "metro": 25.0, "rail": 45.0}
LIVE_CACHE_S = 25.0
SAMPLE_EVERY_S = 90.0     # how often hub boards are polled for line delays
DELAY_TTL_S = 360.0       # a line's live delay is dropped if not seen again this long
MAX_HUBS = 6


def parse_efa_departures(doc: dict, now: float, lines: dict, line_by_number: dict[str, str]) -> list[Arrival]:
    """Turn an EFA rapidJSON departure monitor response into Arrivals."""
    out: list[Arrival] = []
    for ev in doc.get("stopEvents", []) or []:
        when = ev.get("departureTimeEstimated") or ev.get("departureTimePlanned")
        if not when:
            continue
        t = dt.datetime.fromisoformat(when.replace("Z", "+00:00")).timestamp()
        planned = ev.get("departureTimePlanned")
        delay = int(t - dt.datetime.fromisoformat(planned.replace("Z", "+00:00")).timestamp()) if planned else 0
        tr = ev.get("transportation") or {}
        number = str(tr.get("disassembledName") or tr.get("number") or "").strip()
        line_id = line_by_number.get(number, number)
        line = lines.get(line_id)
        dest = (tr.get("destination") or {}).get("name") or ""
        out.append(
            Arrival(
                vehicle_id="",
                line_id=line_id,
                line_name=line.name if line else (tr.get("name") or number),
                color=line.color if line else "#5A6670",
                headsign=dest.replace("Augsburg, ", "").replace("Augsburg ", ""),
                direction=0,
                eta_s=max(0.0, round(t - now, 1)),
                delay_s=max(0, delay),
            )
        )
    out.sort(key=lambda a: a.eta_s)
    return out


class AugsburgProvider(TransitProvider):
    id = "augsburg"

    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self.refresh_interval_s = settings.broadcast_interval_seconds
        self._sims: list[LineSimulation] = []
        self._scenery: dict | None = None
        self._source = "bundled"
        self._live: dict[str, tuple[float, list[Arrival]]] = {}
        self._efa_down_until = 0.0
        self._line_delay: dict[str, tuple[float, float, str]] = {}  # line -> (seen at, delay s, station)
        self._last_sample = 0.0
        self._efa_ids: dict[str, str] = {}  # our station id -> EFA stop id
        self.delay_log = DelayLog(settings.delay_log_path) if settings.delay_log_enabled and settings.efa_enabled else None
        self._log_cursor = 0
        self._sampler: asyncio.Task | None = None
        self.client = httpx.AsyncClient(timeout=4.0, headers={"User-Agent": "interactive-transport/1.0"})

    def describe(self) -> ProviderInfo:
        return ProviderInfo(
            id=self.id,
            name="Augsburg",
            city="Augsburg",
            kind="simulated",
            basemap="osm",
            center=data.CENTER,
            zoom=13.2,
            description=(
                "swa trams and buses. Vehicle positions follow the timetable; departures are live from "
                "the Bavarian EFA planner when it can be reached."
                + (" Network from the official DELFI timetable." if self._source == "gtfs" else " Network is a bundled approximation.")
            ),
            status=self.status,
            status_detail=self.status_detail,
            attribution=("Timetable: DELFI (CC BY 4.0). Tracks: OpenStreetMap" if self._source == "gtfs" else "Network: simplified approximation") + ". Live departures: bahnland-bayern.de",
        )

    async def load_network(self) -> Network:
        path = self.settings.augsburg_gtfs_path
        network = None
        if path and Path(path).exists():
            try:
                network = load_gtfs_network(
                    path, provider_id=self.id, modes={"tram", "bus"}, center=data.CENTER,
                    radius_m=self.settings.augsburg_radius_m, strip_prefixes=("Augsburg, ", "Augsburg "),
                )
                self._source = "gtfs"
            except Exception as exc:  # malformed feed -> keep working with the bundled one
                log.warning("Could not use GTFS at %s (%s); using the bundled network", path, exc)
        if network is None:
            network = self._bundled()
        names = {s.id: s.name for s in network.stations}
        self._sims = [
            LineSimulation(line, names, DWELL.get(line.mode, 20.0), seed=i + 3, delay_profile=1.6 if line.mode == "bus" else 0.9)
            for i, line in enumerate(network.lines)
        ]
        return network

    def _bundled(self) -> Network:
        coords = {sid: (lon, lat) for sid, (_, lat, lon) in data.STOPS.items()}
        lines = []
        for spec in data.LINES:
            pts = [coords[s] for s in spec["stops"]]
            lines.append(
                build_line(
                    line_id=spec["id"], name=spec["name"], short_name=spec["id"], color=spec["color"],
                    mode=spec["mode"], station_ids=spec["stops"], station_coords=pts,
                    shape=catmull_rom(pts, samples_per_segment=12), avg_speed_kmh=spec["speed"], headway_s=spec["headway"],
                )
            )
        served: dict[str, list[str]] = {}
        for line in lines:
            for sid in line.stations:
                if line.id not in served.setdefault(sid, []):
                    served[sid].append(line.id)
        stations = [Station(id=sid, name=data.STOPS[sid][0], lat=data.STOPS[sid][1], lon=data.STOPS[sid][2], lines=lids) for sid, lids in served.items()]
        return Network(provider=self.id, lines=lines, stations=stations, bbox=compute_bbox(stations, pad=0.006))

    async def fetch_vehicles(self) -> list[Vehicle]:
        now = time.time()
        self._maybe_sample(now)
        for sim in self._sims:
            seen = self._line_delay.get(sim.line.id)
            sim.live_delay_s = seen[1] if seen and now - seen[0] < DELAY_TTL_S else None
        return [v for sim in self._sims for v in sim.vehicles(now)]

    # ---- live delays ------------------------------------------------------------------
    def _hubs(self) -> list[str]:
        """A few stations that together serve every line (greedy set cover)."""
        idx = self._idx()
        todo = set(idx.lines)
        hubs: list[str] = []
        while todo and len(hubs) < MAX_HUBS:
            best = max(idx.stations.values(), key=lambda s: len(todo & set(s.lines)))
            if not todo & set(best.lines):
                break
            hubs.append(best.id)
            todo -= set(best.lines)
        return hubs

    def _maybe_sample(self, now: float) -> None:
        if not self.settings.efa_enabled or now - self._last_sample < SAMPLE_EVERY_S:
            return
        if self._sampler and not self._sampler.done():
            return
        self._last_sample = now
        self._sampler = asyncio.create_task(self._sample_hubs())

    async def _sample_hubs(self) -> None:
        for sid in self._hubs():
            await self.live_departures(sid)  # records line delays as a side effect

    def _record_delays(self, station_id: str, arrivals: list[Arrival], now: float) -> None:
        by_line: dict[str, list[int]] = {}
        for a in arrivals:
            if a.eta_s <= 1800 and a.line_id in self._idx().lines:
                by_line.setdefault(a.line_id, []).append(a.delay_s)
        for lid, ds in by_line.items():
            self._line_delay[lid] = (now, float(statistics.median(ds)), station_id)

    def delay_station(self, line_id: str) -> str | None:
        seen = self._line_delay.get(line_id)
        return seen[2] if seen and time.time() - seen[0] < DELAY_TTL_S else None

    def scenery(self) -> dict:
        if self._scenery is None:
            idx = self._idx()
            self._scenery = scenery.generate(
                center=data.CENTER,
                stations=[(s.lon, s.lat) for s in idx.network.stations],
                line_shapes=[[tuple(c) for c in l.shape] for l in idx.network.lines],
                rivers=data.RIVERS, parks=data.PARKS, density=data.DENSITY,
            )
        return self._scenery

    async def live_departures(self, station_id: str) -> list[Arrival] | None:
        """Real departures from EFA; None when unavailable (callers then use the simulation)."""
        if not self.settings.efa_enabled or time.time() < self._efa_down_until:
            return None
        idx = self._idx()
        cached = self._live.get(station_id)
        now = time.time()
        if cached and now - cached[0] < LIVE_CACHE_S:
            age = now - cached[0]
            return [a.model_copy(update={"eta_s": max(0.0, a.eta_s - age)}) for a in cached[1]]
        station = idx.stations[station_id]
        try:
            doc = await self._efa_dm(self._efa_ids.get(station_id) or f"Augsburg {station.name}")
            if "stopEvents" not in doc:
                # a name can match several places (stops, POIs); EFA then only lists them.
                # Take the best stop, remember its id, and ask again by id.
                stops = [l for l in doc.get("locations", []) if l.get("type") == "stop"]
                stops.sort(key=lambda l: (not l.get("isBest"), -(l.get("matchQuality") or 0)))
                if not stops:
                    raise ValueError(f"EFA has no stop called {station.name}")
                self._efa_ids[station_id] = stops[0]["id"]
                doc = await self._efa_dm(stops[0]["id"])
            numbers = {l.short_name: l.id for l in idx.lines.values()}
            arrivals = parse_efa_departures(doc, now, idx.lines, numbers)
        except ValueError as exc:  # this one stop is unknown to EFA; others still work
            log.info("%s", exc)
            return None
        except Exception as exc:
            log.info("EFA departures unavailable (%s); using simulated boards for 5 min", type(exc).__name__)
            self._efa_down_until = now + 300
            return None
        self._live[station_id] = (now, arrivals)
        self._record_delays(station_id, arrivals, now)
        if self.delay_log:
            self.delay_log.record(station_id, arrivals, now)
        return arrivals

    async def log_tick(self) -> None:
        """One round of the punctuality log: the hubs plus the next few stops in turn."""
        await self.ensure_network()
        ids = sorted(self._idx().stations)
        n = self.settings.delay_log_stations_per_tick
        batch = [ids[(self._log_cursor + i) % len(ids)] for i in range(min(n, len(ids)))]
        self._log_cursor = (self._log_cursor + n) % len(ids)
        for sid in dict.fromkeys(self._hubs() + batch):
            await self.live_departures(sid)

    async def _efa_dm(self, name: str) -> dict:
        resp = await self.client.get(
            f"{self.settings.efa_base_url}/XML_DM_REQUEST",
            params={
                "outputFormat": "rapidJSON", "language": "en", "type_dm": "any",
                "name_dm": name, "mode": "direct", "useRealtime": "1",
                "limit": "14", "depType": "stopEvents",
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def close(self) -> None:
        if self._sampler:
            self._sampler.cancel()
        if self.delay_log:
            self.delay_log.close()
        await self.client.aclose()
