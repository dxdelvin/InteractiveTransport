"""Build app/data/gtfs/augsburg.zip: the full swa network with real track geometry.

Sources (both open data):
  * Timetable - DELFI's nationwide GTFS feed (https://gtfs.de, CC BY 4.0). We keep only
    the swa operator ("Augsburger Verkehrsgesellschaft"): trams 1-6 and every city bus.
    The free feed has no shapes.txt, so lines would otherwise be straight stop-to-stop.
  * Geometry and line colours - OpenStreetMap route relations (ODbL), via Overpass.
    Each stop pattern gets the OSM route whose track passes closest to its stops, cut
    between the first and last stop.

Usage:
    curl -L -o nv_free.zip https://download.gtfs.de/germany/nv_free/latest.zip
    python scripts/build_augsburg_gtfs.py nv_free.zip            # -> app/data/gtfs/augsburg.zip
    python scripts/build_augsburg_gtfs.py nv_free.zip --osm cached.json   # reuse an Overpass reply
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.services.geo import Polyline, haversine_m  # noqa: E402

AGENCY_NAME = "Augsburger Verkehrsgesellschaft"
BBOX = (48.22, 10.72, 48.48, 11.06)  # south, west, north, east
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
MAX_MEAN_OFFSET_M = 70.0  # an OSM route further than this from the stops is the wrong one
FALLBACK_COLOR = {"0": "E3000F", "3": "0098D4"}


def read(z: zipfile.ZipFile, name: str):
    return csv.DictReader(io.TextIOWrapper(z.open(name), encoding="utf-8-sig"))


def write(out: zipfile.ZipFile, name: str, fields: list[str], rows) -> int:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    n = 0
    for r in rows:
        w.writerow(r)
        n += 1
    out.writestr(name, buf.getvalue())
    return n


def overpass() -> dict:
    s, w, n, e = BBOX
    q = f'[out:json][timeout:180];relation["type"="route"]["route"~"^(tram|bus)$"]["network"~"Augsburg"]({s},{w},{n},{e});out geom;'
    for url in OVERPASS:
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, data=urllib.parse.urlencode({"data": q}).encode(), headers={"User-Agent": "interactive-transport-build/1.0"})
                with urllib.request.urlopen(req, timeout=240) as r:
                    return json.load(r)
            except Exception as exc:  # busy server: back off, then try the mirror
                print(f"  overpass {url} failed ({exc}); retrying", file=sys.stderr)
                time.sleep(5 * 2**attempt)
    raise SystemExit("Overpass is unreachable; try again later or pass --osm")


def stitch(rel: dict) -> list[tuple[float, float]]:
    """Member ways of a route relation, oriented and joined into one polyline."""
    ways = [[(p["lon"], p["lat"]) for p in m["geometry"]] for m in rel.get("members", [])
            if m["type"] == "way" and m.get("role", "") in ("", "forward", "backward") and m.get("geometry")]
    if not ways:
        return []
    line = list(ways[0])
    if len(ways) > 1:
        nxt = ways[1]
        if min(haversine_m(line[0], nxt[0]), haversine_m(line[0], nxt[-1])) < min(haversine_m(line[-1], nxt[0]), haversine_m(line[-1], nxt[-1])):
            line.reverse()
    for way in ways[1:]:
        if haversine_m(line[-1], way[-1]) < haversine_m(line[-1], way[0]):
            way = way[::-1]
        line.extend(way[1:] if haversine_m(line[-1], way[0]) < 1 else way)
    return line


def fit(poly: Polyline, pts: list[tuple[float, float]]) -> tuple[float, list[float]]:
    projs = [poly.project(p) for p in pts]
    mean_off = sum(o for _, o in projs) / len(projs)
    along = [d for d, _ in projs]
    backwards = sum(1 for a, b in zip(along, along[1:]) if b < a - 30)
    return mean_off + 40 * backwards, along


def cut(poly: Polyline, a: float, b: float) -> list[tuple[float, float]]:
    pts = [poly.point_at(a)[0]]
    for c, d in zip(poly.coords, poly.cumulative):
        if a < d < b:
            pts.append(c)
    pts.append(poly.point_at(b)[0])
    return pts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("delfi_zip", type=Path)
    ap.add_argument("--osm", type=Path, help="cached Overpass JSON (saved next to the output otherwise)")
    ap.add_argument("--out", type=Path, default=ROOT / "app/data/gtfs/augsburg.zip")
    args = ap.parse_args()
    src = zipfile.ZipFile(args.delfi_zip)

    agencies = [r for r in read(src, "agency.txt") if r["agency_name"].strip() == AGENCY_NAME]
    if not agencies:
        raise SystemExit(f"No agency named {AGENCY_NAME!r} in the feed")
    agency_ids = {a["agency_id"] for a in agencies}
    routes = {r["route_id"]: r for r in read(src, "routes.txt") if r["agency_id"] in agency_ids}
    trips = {r["trip_id"]: r for r in read(src, "trips.txt") if r["route_id"] in routes}
    print(f"swa: {len(routes)} routes, {len(trips)} trips")

    print("reading stop_times (large, takes a minute)")
    stop_times = [r for r in read(src, "stop_times.txt") if r["trip_id"] in trips]
    stop_ids = {r["stop_id"] for r in stop_times}
    all_stops = {r["stop_id"]: r for r in read(src, "stops.txt")}
    parents = {all_stops[s].get("parent_station") for s in stop_ids if s in all_stops} - {"", None}
    stops = {k: v for k, v in all_stops.items() if k in stop_ids or k in parents}
    services = {t["service_id"] for t in trips.values()}

    # ---- geometry from OSM
    if args.osm and args.osm.exists():
        osm = json.loads(args.osm.read_text(encoding="utf-8"))
    else:
        print("fetching OSM route relations")
        osm = overpass()
        (args.osm or args.out.with_suffix(".osm.json")).write_text(json.dumps(osm), encoding="utf-8")
    rels = defaultdict(list)  # (mode, ref) -> [(polyline, colour)]
    for el in osm["elements"]:
        t = el.get("tags", {})
        line = stitch(el)
        if len(line) >= 2:
            rels[(t.get("route"), t.get("ref", "").strip())].append((Polyline(line), t.get("colour", "")))

    seqs = defaultdict(list)
    for r in stop_times:
        seqs[r["trip_id"]].append((int(r["stop_sequence"]), r["stop_id"]))
    pattern_of: dict[str, tuple] = {}
    for tid, seq in seqs.items():
        pattern_of[tid] = (trips[tid]["route_id"], tuple(s for _, s in sorted(seq)))

    shapes: dict[tuple, str] = {}
    shape_rows = []
    colours: dict[str, Counter] = defaultdict(Counter)
    missing = Counter()
    for n, (route_id, pat) in enumerate(sorted(set(pattern_of.values()))):
        route = routes[route_id]
        mode = "tram" if route["route_type"] in ("0", "900") else "bus"
        pts = [(float(stops[s]["stop_lon"]), float(stops[s]["stop_lat"])) for s in pat if s in stops]
        best = None
        for poly, colour in rels.get((mode, route["route_short_name"].strip()), []):
            score, along = fit(poly, pts)
            if best is None or score < best[0]:
                best = (score, poly, along, colour)
        if not best or best[0] > MAX_MEAN_OFFSET_M:
            missing[route["route_short_name"]] += 1
            continue
        _, poly, along, colour = best
        if colour:
            colours[route_id][colour.lstrip("#").upper()] += 1
        if pat[0] == pat[-1] or abs(along[-1] - along[0]) < 200:
            coords = list(poly.coords)  # a circular route: keep the whole loop
        else:
            a, b = min(along[0], along[-1]), max(along[0], along[-1])
            coords = cut(poly, a, b)
            if along[0] > along[-1]:
                coords.reverse()
        sid = f"s{n}"
        shapes[(route_id, pat)] = sid
        shape_rows += [{"shape_id": sid, "shape_pt_lat": f"{lat:.6f}", "shape_pt_lon": f"{lon:.6f}", "shape_pt_sequence": i} for i, (lon, lat) in enumerate(coords)]
    if missing:
        print("no OSM geometry (drawn stop-to-stop):", dict(missing))

    for tid, t in trips.items():
        t["shape_id"] = shapes.get(pattern_of.get(tid), "")
    for rid, r in routes.items():
        c = colours[rid].most_common(1)
        r["route_color"] = c[0][0] if c else (r.get("route_color") or FALLBACK_COLOR.get(r["route_type"], "0098D4"))
        r["route_text_color"] = "FFFFFF"

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED) as out:
        write(out, "agency.txt", list(agencies[0].keys()), agencies)
        write(out, "routes.txt", ["route_id", "agency_id", "route_short_name", "route_long_name", "route_type", "route_color", "route_text_color"], routes.values())
        write(out, "trips.txt", ["route_id", "service_id", "trip_id", "trip_headsign", "direction_id", "shape_id"], trips.values())
        write(out, "stop_times.txt", ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"], stop_times)
        write(out, "stops.txt", ["stop_id", "stop_name", "stop_lat", "stop_lon", "location_type", "parent_station"], stops.values())
        write(out, "shapes.txt", ["shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence"], shape_rows)
        for name in ("calendar.txt", "calendar_dates.txt"):
            if name in src.namelist():
                rows = [r for r in read(src, name) if r["service_id"] in services]
                if rows:
                    write(out, name, list(rows[0].keys()), rows)
        out.writestr("attribution.txt", "Timetable: DELFI e.V. (www.delfi.de), via opendata-oepnv.de, CC BY 4.0.\nRoute geometry and colours: (c) OpenStreetMap contributors, ODbL.\n")
    print(f"wrote {args.out} ({args.out.stat().st_size // 1024} KB, {len(shapes)} shapes)")


if __name__ == "__main__":
    main()
