"""MBTA adapter parsing, against hand-built JSON:API documents (no network)."""
from app.services.geo import Polyline
from app.services.providers.mbta import parse_network, parse_vehicles


def _stop(sid, name, lat, lon, parent):
    return {"type": "stop", "id": sid, "attributes": {"name": name, "latitude": lat, "longitude": lon},
            "relationships": {"parent_station": {"data": {"type": "stop", "id": parent}}}}


ROUTES = {"data": [{"type": "route", "id": "Red", "attributes": {"long_name": "Red Line", "short_name": "", "color": "DA291C", "text_color": "FFFFFF", "type": 1}}]}
STOPS = [
    _stop("70061", "Alewife", 42.3954, -71.1422, "place-alfcl"),
    _stop("70063", "Davis", 42.3967, -71.1218, "place-davis"),
    _stop("70065", "Porter", 42.3884, -71.1191, "place-portr"),
]
PATTERNS = {
    "data": [
        {"type": "route_pattern", "id": "Red-1-0", "attributes": {"direction_id": 0, "typicality": 1, "name": "Alewife - Porter"},
         "relationships": {"route": {"data": {"type": "route", "id": "Red"}}, "representative_trip": {"data": {"type": "trip", "id": "t1"}}}},
        {"type": "route_pattern", "id": "Red-9-0", "attributes": {"direction_id": 0, "typicality": 4, "name": "odd"},
         "relationships": {"route": {"data": {"type": "route", "id": "Red"}}, "representative_trip": {"data": {"type": "trip", "id": "t1"}}}},
    ],
    "included": [
        {"type": "trip", "id": "t1", "attributes": {"headsign": "Porter"},
         "relationships": {"shape": {"data": None}, "stops": {"data": [{"type": "stop", "id": s["id"]} for s in STOPS]}}},
        *STOPS,
    ],
}


def test_parse_network_builds_typical_patterns_only():
    net, p2s = parse_network(ROUTES, PATTERNS, {"data": []})
    assert [l.id for l in net.lines] == ["Red-1-0"]
    line = net.lines[0]
    assert line.stations == ["place-alfcl", "place-davis", "place-portr"]
    assert line.color == "#DA291C" and line.mode == "metro"
    assert p2s["70063"] == "place-davis"
    assert line.station_offsets_m == sorted(line.station_offsets_m)


def test_parse_vehicles_snaps_to_pattern():
    net, p2s = parse_network(ROUTES, PATTERNS, {"data": []})
    polys = {l.id: Polyline([tuple(c) for c in l.shape]) for l in net.lines}
    doc = {
        "data": [
            {"type": "vehicle", "id": "R-5468", "attributes": {"latitude": 42.3960, "longitude": -71.1300, "bearing": 90, "current_status": "IN_TRANSIT_TO",
                                                               "direction_id": 0, "label": "1850", "speed": None, "updated_at": "2026-11-25T10:00:00-05:00", "occupancy_status": None},
             "relationships": {"route": {"data": {"id": "Red"}}, "stop": {"data": {"id": "70063"}}, "trip": {"data": {"id": "t1"}}}},
            {"type": "vehicle", "id": "far", "attributes": {"latitude": 40.0, "longitude": -70.0, "direction_id": 0},
             "relationships": {"route": {"data": {"id": "Red"}}, "stop": {"data": None}, "trip": {"data": None}}},
        ],
        "included": [{"type": "trip", "id": "t1", "attributes": {"headsign": "Porter"}}],
    }
    vs = parse_vehicles(doc, {"Red": ["Red-1-0"]}, polys, p2s, {"place-davis": {"Red-1-0"}})
    assert [v.id for v in vs] == ["R-5468"]
    v = vs[0]
    assert v.headsign == "Porter" and v.next_station_id == "place-davis" and v.status == "in_transit"
    assert 0 < v.shape_dist_m < net.lines[0].length_m
