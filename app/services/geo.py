"""Geometry helpers: distances, bearings, polylines with linear referencing.

Coordinates are always (lon, lat) tuples, matching GeoJSON order.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from typing import Iterable, Sequence

EARTH_RADIUS_M = 6_371_008.8
LonLat = tuple[float, float]


def haversine_m(a: LonLat, b: LonLat) -> float:
    lon1, lat1 = map(math.radians, a)
    lon2, lat2 = map(math.radians, b)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def bearing_deg(a: LonLat, b: LonLat) -> float:
    """Initial compass bearing from a to b (0 = north, 90 = east)."""
    lon1, lat1 = map(math.radians, a)
    lon2, lat2 = map(math.radians, b)
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def offset_km(origin: LonLat, east_km: float, north_km: float) -> LonLat:
    """Move from origin by a small planar offset in kilometres."""
    lon0, lat0 = origin
    dlat = north_km / 111.32
    dlon = east_km / (111.32 * math.cos(math.radians(lat0)))
    return (lon0 + dlon, lat0 + dlat)


class LocalProjection:
    """Equirectangular projection around an origin - accurate for city-sized areas."""

    def __init__(self, origin: LonLat):
        self.lon0, self.lat0 = origin
        self.kx = 111_320.0 * math.cos(math.radians(self.lat0))
        self.ky = 110_574.0

    def to_xy(self, p: LonLat) -> tuple[float, float]:
        return ((p[0] - self.lon0) * self.kx, (p[1] - self.lat0) * self.ky)

    def to_lonlat(self, x: float, y: float) -> LonLat:
        return (self.lon0 + x / self.kx, self.lat0 + y / self.ky)


def catmull_rom(points: Sequence[LonLat], samples_per_segment: int = 10, closed: bool = False) -> list[LonLat]:
    """Smooth a polyline through the given control points (centripetal-ish uniform Catmull-Rom)."""
    if len(points) < 3:
        return list(points)
    pts = list(points)
    n = len(pts)
    out: list[LonLat] = []
    seg_count = n if closed else n - 1
    for i in range(seg_count):
        if closed:
            p0, p1, p2, p3 = pts[(i - 1) % n], pts[i], pts[(i + 1) % n], pts[(i + 2) % n]
        else:
            p0 = pts[i - 1] if i > 0 else pts[i]
            p1, p2 = pts[i], pts[i + 1]
            p3 = pts[i + 2] if i + 2 < n else pts[i + 1]
        for s in range(samples_per_segment):
            t = s / samples_per_segment
            t2, t3 = t * t, t * t * t
            out.append(
                tuple(  # type: ignore[arg-type]
                    0.5
                    * (
                        2 * p1[k]
                        + (-p0[k] + p2[k]) * t
                        + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t2
                        + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * t3
                    )
                    for k in range(2)
                )
            )
    out.append(pts[0] if closed else pts[-1])
    return out


def decode_polyline(encoded: str, precision: int = 5) -> list[LonLat]:
    """Decode a Google encoded polyline (used by the MBTA shapes API)."""
    coords: list[LonLat] = []
    index = lat = lon = 0
    factor = 10**precision
    length = len(encoded)
    while index < length:
        for which in range(2):
            shift = result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if which == 0:
                lat += delta
            else:
                lon += delta
        coords.append((lon / factor, lat / factor))
    return coords


@dataclass
class Polyline:
    """A polyline with cumulative distances for linear referencing."""

    coords: list[LonLat]
    cumulative: list[float] = field(init=False)
    _proj: LocalProjection = field(init=False, repr=False)
    _xy: list[tuple[float, float]] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if len(self.coords) < 2:
            raise ValueError("A polyline needs at least two points")
        self.cumulative = [0.0]
        for a, b in zip(self.coords, self.coords[1:]):
            self.cumulative.append(self.cumulative[-1] + haversine_m(a, b))
        self._proj = LocalProjection(self.coords[0])
        self._xy = [self._proj.to_xy(c) for c in self.coords]

    @property
    def length(self) -> float:
        return self.cumulative[-1]

    def point_at(self, distance: float) -> tuple[LonLat, float]:
        """Return the coordinate and forward bearing at a distance along the line."""
        d = min(max(distance, 0.0), self.length)
        i = max(0, min(bisect.bisect_right(self.cumulative, d) - 1, len(self.coords) - 2))
        seg = self.cumulative[i + 1] - self.cumulative[i]
        t = 0.0 if seg <= 0 else (d - self.cumulative[i]) / seg
        a, b = self.coords[i], self.coords[i + 1]
        point = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        return point, bearing_deg(a, b)

    def project(self, p: LonLat) -> tuple[float, float]:
        """Linear-reference a point: (distance along line, perpendicular offset in metres)."""
        px, py = self._proj.to_xy(p)
        best = (0.0, float("inf"))
        for i in range(len(self._xy) - 1):
            (ax, ay), (bx, by) = self._xy[i], self._xy[i + 1]
            dx, dy = bx - ax, by - ay
            l2 = dx * dx + dy * dy
            t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
            cx, cy = ax + dx * t, ay + dy * t
            off = math.hypot(px - cx, py - cy)
            if off < best[1]:
                along = self.cumulative[i] + (self.cumulative[i + 1] - self.cumulative[i]) * t
                best = (along, off)
        return best

    def slice(self, d0: float, d1: float) -> list[LonLat]:
        """Coordinates between two distances. If d0 > d1 the result runs backwards."""
        reverse = d0 > d1
        lo, hi = (d1, d0) if reverse else (d0, d1)
        lo, hi = max(0.0, lo), min(self.length, hi)
        start, _ = self.point_at(lo)
        end, _ = self.point_at(hi)
        inner = [c for c, cd in zip(self.coords, self.cumulative) if lo < cd < hi]
        out = [start, *inner, end]
        return out[::-1] if reverse else out


def nearest(points: Iterable[tuple[str, LonLat]], target: LonLat, k: int = 3) -> list[tuple[str, float]]:
    scored = sorted(((pid, haversine_m(p, target)) for pid, p in points), key=lambda x: x[1])
    return scored[:k]
