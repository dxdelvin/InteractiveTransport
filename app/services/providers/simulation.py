"""Deterministic timetable simulation.

Every line gets a fleet whose positions are a function of wall-clock time and delay, so
the simulation never drifts, survives restarts, and two clients always agree.

Delay comes from live departure data when a provider has it (``live_delay_s``) and from
slow pseudo-noise otherwise. Either way it changes at most MAX_DELAY_RATE seconds per
second, so a vehicle catching up with a new delay slows down instead of jumping back.

Within a segment, trains follow  s(t) = d * (t - sin(2*pi*t) / (2*pi)),  which starts
and ends at zero speed: they visibly accelerate out of stations and brake into them.
"""
from __future__ import annotations

import bisect
import math
import time
from dataclasses import dataclass

from app.schemas.transit import Line, Vehicle
from app.services.geo import Polyline

LAYOVER_S = 90.0
MAX_DELAY_RATE = 0.6  # s of delay per s of clock; < 1 so vehicles never roll backwards


def _ease(t: float) -> float:
    return t - math.sin(2 * math.pi * t) / (2 * math.pi)


def _ease_speed(t: float) -> float:
    """Derivative of _ease, i.e. speed relative to segment average."""
    return 1 - math.cos(2 * math.pi * t)


def _noise(seed: float, t: float) -> float:
    """Smooth, deterministic, slowly varying pseudo-noise in [-1, 1].

    Kept slow on purpose: delay feeds into position, and if delay grew faster than
    one second per second a train would roll backwards.
    """
    return (
        math.sin(t / 1800.0 + seed * 1.7) * 0.5
        + math.sin(t / 1100.0 + seed * 3.1) * 0.3
        + math.sin(t / 700.0 + seed * 5.3) * 0.2
    )


@dataclass
class RunState:
    shape_dist: float
    status: str  # "stopped" | "in_transit"
    current_idx: int | None
    next_idx: int | None
    speed_mps: float


class Run:
    """One direction of travel: a sequence of stops at given shape distances."""

    def __init__(self, station_idx: list[int], dists: list[float], speed_mps: float, dwell_s: float):
        self.station_idx = station_idx
        self.dists = dists
        self.events: list[tuple[float, float]] = []  # (arrive, depart) per stop
        t = 0.0
        for k in range(len(dists)):
            if k > 0:
                t += abs(dists[k] - dists[k - 1]) / speed_mps
            arrive = t
            is_terminal = k == 0 or k == len(dists) - 1
            depart = arrive + (0.0 if is_terminal else dwell_s)
            self.events.append((arrive, depart))
            t = depart
        self.duration = t
        self._departs = [d for _, d in self.events]

    def state(self, phase: float) -> RunState:
        phase = min(max(phase, 0.0), self.duration)
        k = bisect.bisect_right(self._departs, phase)
        if k >= len(self.events):
            return RunState(self.dists[-1], "stopped", self.station_idx[-1], None, 0.0)
        arrive, depart = self.events[k]
        if phase >= arrive:  # dwelling at stop k
            nxt = self.station_idx[k + 1] if k + 1 < len(self.station_idx) else None
            return RunState(self.dists[k], "stopped", self.station_idx[k], nxt, 0.0)
        # running from stop k-1 to stop k
        prev_depart = self.events[k - 1][1]
        span = arrive - prev_depart
        f = (phase - prev_depart) / span if span > 0 else 1.0
        d0, d1 = self.dists[k - 1], self.dists[k]
        dist = d0 + (d1 - d0) * _ease(f)
        speed = abs(d1 - d0) / span * _ease_speed(f) if span > 0 else 0.0
        return RunState(dist, "in_transit", None, self.station_idx[k], speed)


class LineSimulation:
    """Fleet simulation for a single line."""

    def __init__(self, line: Line, station_names: dict[str, str], dwell_s: float, seed: int, delay_profile: float = 1.0):
        self.line = line
        self.names = station_names
        self.poly = Polyline([tuple(c) for c in line.shape])
        self.seed = seed
        self.delay_profile = delay_profile
        self.live_delay_s: float | None = None  # set by the provider from real departures
        self._applied: dict[int, tuple[float, float]] = {}  # vehicle k -> (time, delay shown)
        speed = line.avg_speed_kmh / 3.6
        offs = line.station_offsets_m
        n = len(offs)
        if line.loop:
            fwd_idx = list(range(n)) + [0]
            fwd_d = list(offs) + [line.length_m]
            rev_idx = [0] + list(range(n - 1, -1, -1))
            rev_d = [line.length_m] + [offs[i] for i in range(n - 1, -1, -1)]
            rev_d[-1] = 0.0
            self.runs = [Run(fwd_idx, fwd_d, speed, dwell_s), Run(rev_idx, rev_d, speed, dwell_s)]
            self.cycle = self.runs[0].duration + dwell_s
            per_dir = max(1, round(self.cycle / line.headway_s))
            self.fleet = [(d, k * self.cycle / per_dir) for d in (0, 1) for k in range(per_dir)]
        else:
            self.runs = [
                Run(list(range(n)), list(offs), speed, dwell_s),
                Run(list(range(n - 1, -1, -1)), [offs[i] for i in range(n - 1, -1, -1)], speed, dwell_s),
            ]
            self.cycle = self.runs[0].duration + self.runs[1].duration + 2 * LAYOVER_S
            count = max(2, round(self.cycle / line.headway_s))
            self.fleet = [(-1, k * self.cycle / count) for k in range(count)]

    def _target_delay(self, k: int, now: float) -> float:
        if self.live_delay_s is not None:
            return max(0.0, self.live_delay_s)
        raw = _noise(self.seed * 13 + k, now)
        # mostly on time, occasionally a few minutes behind
        return max(0.0, (raw - 0.35) * 420 * self.delay_profile)

    def _delay(self, k: int, now: float) -> float:
        target = self._target_delay(k, now)
        prev = self._applied.get(k)
        if prev is None or now < prev[0] or now - prev[0] > 900:
            delay = target  # first sight (or clock jumped): no history to ease from
        else:
            step = MAX_DELAY_RATE * (now - prev[0])
            delay = prev[1] + max(-step, min(step, target - prev[1]))
        self._applied[k] = (now, delay)
        return delay

    def _occupancy(self, k: int, now: float) -> str:
        hour = (time.localtime(now).tm_hour + time.localtime(now).tm_min / 60.0)
        rush = math.exp(-((hour - 8.5) ** 2) / 2) + math.exp(-((hour - 17.75) ** 2) / 2)
        level = 0.35 + 0.55 * rush + 0.25 * _noise(self.seed + k * 7, now / 3)
        return "high" if level > 0.75 else "medium" if level > 0.45 else "low"

    def running(self, now: float) -> bool:
        """Whether the timetable has this line in service at local time `now`."""
        line = self.line
        if line.service_start_s is None or line.service_end_s is None:
            return True
        lt = time.localtime(now)
        sec = lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec
        days = set(line.service_days) if line.service_days else set(range(7))
        today, yesterday = lt.tm_wday, (lt.tm_wday - 1) % 7
        # a service day can run past midnight (e.g. 25:30 for a night bus)
        return (today in days and line.service_start_s <= sec <= line.service_end_s) or (
            yesterday in days and line.service_start_s <= sec + 86400 <= line.service_end_s
        )

    def vehicles(self, now: float) -> list[Vehicle]:
        line = self.line
        out: list[Vehicle] = []
        if not self.running(now):
            return out
        for k, (fixed_dir, phase0) in enumerate(self.fleet):
            delay = self._delay(k, now)
            c = (now - delay + phase0) % self.cycle
            if fixed_dir >= 0:  # loop line
                direction = fixed_dir
                run = self.runs[direction]
                st = run.state(c) if c < run.duration else RunState(run.dists[-1], "stopped", run.station_idx[-1], run.station_idx[1], 0.0)
                st.shape_dist = st.shape_dist % line.length_m
            else:
                r0, r1 = self.runs
                if c < r0.duration:
                    direction, st = 0, r0.state(c)
                elif c < r0.duration + LAYOVER_S:
                    direction, st = 1, RunState(r1.dists[0], "stopped", r1.station_idx[0], r1.station_idx[1], 0.0)
                elif c < r0.duration + LAYOVER_S + r1.duration:
                    direction, st = 1, r1.state(c - r0.duration - LAYOVER_S)
                else:
                    direction, st = 0, RunState(r0.dists[0], "stopped", r0.station_idx[0], r0.station_idx[1], 0.0)

            (lon, lat), bearing = self.poly.point_at(st.shape_dist)
            if direction == 1:
                bearing = (bearing + 180.0) % 360.0
            if line.loop:
                dest_idx = len(line.stations) // 2
                headsign = f"Loop via {self.names[line.stations[dest_idx]]}"
            else:
                dest_idx = len(line.stations) - 1 if direction == 0 else 0
                headsign = self.names[line.stations[dest_idx]]
            out.append(
                Vehicle(
                    id=f"{line.id}-{k + 1:02d}",
                    label=f"{line.short_name} {100 + k + 1}",
                    line_id=line.id,
                    lon=round(lon, 6),
                    lat=round(lat, 6),
                    bearing=round(bearing, 1),
                    speed_kmh=round(st.speed_mps * 3.6, 1),
                    direction=direction,
                    shape_dist_m=round(st.shape_dist, 1),
                    status=st.status,  # type: ignore[arg-type]
                    current_station_id=line.stations[st.current_idx] if st.current_idx is not None else None,
                    next_station_id=line.stations[st.next_idx] if st.next_idx is not None else None,
                    destination_station_id=line.stations[dest_idx],
                    headsign=headsign,
                    occupancy=self._occupancy(k, now),  # type: ignore[arg-type]
                    delay_s=int(delay),
                    delay_source="live" if self.live_delay_s is not None else "estimated",
                    position_source="timetable",
                    updated_at=now,
                )
            )
        return out
