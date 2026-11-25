"""Offline basemap: rivers, parks, streets and massing blocks, generated procedurally.

The app normally draws real streets and buildings from OpenStreetMap vector tiles. When
those tiles can't be reached, the frontend falls back to this layer so the city still
reads as a city. It's schematic, and the UI says so; it never replaces the real map.
"""
from __future__ import annotations

import math
import random
from collections import defaultdict

from app.services.geo import LonLat, LocalProjection

BLOCK_M = 120.0
STREET_M = 24.0
TRACK_CLEARANCE_M = 30.0


def _pip(x: float, y: float, poly: list[tuple[float, float]]) -> bool:
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi:
            inside = not inside
        j = i
    return inside


class _SegmentIndex:
    """Spatial hash of polyline segments (metres) for clearance checks."""

    CELL = 250.0

    def __init__(self, polylines: list[tuple[list[tuple[float, float]], float]]):
        self.cells: dict[tuple[int, int], list[tuple[float, float, float, float, float]]] = defaultdict(list)
        for pl, half_width in polylines:
            pad = half_width + 60
            for (ax, ay), (bx, by) in zip(pl, pl[1:]):
                for cx in range(int((min(ax, bx) - pad) // self.CELL), int((max(ax, bx) + pad) // self.CELL) + 1):
                    for cy in range(int((min(ay, by) - pad) // self.CELL), int((max(ay, by) + pad) // self.CELL) + 1):
                        self.cells[(cx, cy)].append((ax, ay, bx, by, half_width))

    def near(self, x: float, y: float, extra: float) -> bool:
        for ax, ay, bx, by, hw in self.cells.get((int(x // self.CELL), int(y // self.CELL)), ()):
            dx, dy = bx - ax, by - ay
            l2 = dx * dx + dy * dy
            t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / l2))
            if math.hypot(x - ax - dx * t, y - ay - dy * t) < hw + extra:
                return True
        return False


def _buffer(line: list[tuple[float, float]], half: float) -> list[tuple[float, float]]:
    """Polygon around a polyline (metres); good enough for gently curving rivers."""
    left, right = [], []
    for i, (x, y) in enumerate(line):
        a = line[max(0, i - 1)]
        b = line[min(len(line) - 1, i + 1)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        n = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / n, dx / n
        left.append((x + nx * half, y + ny * half))
        right.append((x - nx * half, y - ny * half))
    return left + right[::-1]


def generate(
    *,
    center: LonLat,
    stations: list[LonLat],
    line_shapes: list[list[LonLat]],
    rivers: list[dict],
    parks: list[list[LonLat]],
    density: list[tuple[float, float, float]],
    extent_km: float = 7.0,
    seed: int = 1276,
) -> dict:
    rng = random.Random(seed)
    proj = LocalProjection(center)

    def ll(x: float, y: float) -> list[float]:
        lon, lat = proj.to_lonlat(x, y)
        return [round(lon, 5), round(lat, 5)]

    def ring(poly_m: list[tuple[float, float]]) -> list[list[float]]:
        pts = [ll(x, y) for x, y in poly_m]
        return pts + [pts[0]]

    features: list[dict] = []
    river_m = [([proj.to_xy(p) for p in r["line"]], r["width"] / 2) for r in rivers]
    for line, half in river_m:
        features.append({"type": "Feature", "properties": {"kind": "water"}, "geometry": {"type": "Polygon", "coordinates": [ring(_buffer(line, half))]}})
    parks_m = [[proj.to_xy(p) for p in park] for park in parks]
    for park in parks_m:
        features.append({"type": "Feature", "properties": {"kind": "park"}, "geometry": {"type": "Polygon", "coordinates": [ring(park)]}})

    water = _SegmentIndex(river_m)
    tracks = _SegmentIndex([([proj.to_xy(c) for c in shape], TRACK_CLEARANCE_M) for shape in line_shapes])
    centres = [(*proj.to_xy((lon, lat)), w) for lon, lat, w in density]
    stops_m = [proj.to_xy(s) for s in stations]

    def dens(x: float, y: float) -> float:
        v = 0.0
        for cx, cy, w in centres:
            v = max(v, w * math.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * 1300.0**2)))
        for sx, sy in stops_m:
            v = max(v, 0.35 * math.exp(-((x - sx) ** 2 + (y - sy) ** 2) / (2 * 450.0**2)))
        return min(1.0, v)

    angle = math.radians(rng.uniform(-8, 8))
    ca, sa = math.cos(angle), math.sin(angle)

    def world(u: float, v: float) -> tuple[float, float]:
        return (u * ca - v * sa, u * sa + v * ca)

    pitch = BLOCK_M + STREET_M
    reach = int(extent_km * 1000 / pitch)
    for iu in range(-reach, reach):
        for iv in range(-reach, reach):
            cx, cy = world((iu + 0.5) * pitch, (iv + 0.5) * pitch)
            d = dens(cx, cy)
            if d < 0.12 or rng.random() > d * 1.3:
                continue
            lots = 1 if d < 0.3 else rng.choice((2, 2, 3))
            lot = BLOCK_M / lots
            rows = 2 if d > 0.4 else 1
            lot_v = BLOCK_M / rows
            for su in range(lots):
                for sv in range(rows):
                    inset = rng.uniform(3, 10)
                    w, h = lot - 2 * inset, lot_v - 2 * inset
                    if w < 10 or h < 10:
                        continue
                    u0, v0 = iu * pitch + STREET_M / 2 + su * lot + inset, iv * pitch + STREET_M / 2 + sv * lot_v + inset
                    mx, my = world(u0 + w / 2, v0 + h / 2)
                    r = max(w, h) / 2
                    if tracks.near(mx, my, r) or water.near(mx, my, r + 10) or any(_pip(mx, my, p) for p in parks_m):
                        continue
                    corners = [world(u0 + a, v0 + b) for a, b in ((0, 0), (w, 0), (w, h), (0, h))]
                    height = 7 + d**1.6 * rng.uniform(8, 26) + (rng.uniform(25, 60) if rng.random() < 0.015 * d else 0)
                    features.append({
                        "type": "Feature",
                        "properties": {"kind": "building", "height": round(height, 1)},
                        "geometry": {"type": "Polygon", "coordinates": [ring(corners)]},
                    })

    for iu in range(-reach, reach + 1):
        for iv in range(-reach, reach):
            for a, b in (((iu * pitch, iv * pitch), (iu * pitch, (iv + 1) * pitch)), ((iv * pitch, iu * pitch), ((iv + 1) * pitch, iu * pitch))):
                (ax, ay), (bx, by) = world(*a), world(*b)
                mx, my = (ax + bx) / 2, (ay + by) / 2
                if dens(mx, my) < 0.18 or water.near(mx, my, 5):
                    continue
                features.append({"type": "Feature", "properties": {"kind": "road", "major": iu % 6 == 0},
                                 "geometry": {"type": "LineString", "coordinates": [ll(ax, ay), ll(bx, by)]}})

    return {"type": "FeatureCollection", "features": features}
