"""GTFS loading and EFA parsing against tiny hand-made inputs."""
import time

from app.services.gtfs import load_gtfs_network
from app.services.providers.augsburg import parse_efa_departures

FEED = {
    "stops.txt": "stop_id,stop_name,stop_lat,stop_lon,parent_station\n"
                 "P1,\"Augsburg, Königsplatz\",48.3653,10.8941,\nA1,\"Augsburg, Königsplatz\",48.3654,10.8942,P1\n"
                 "B1,\"Augsburg, Moritzplatz\",48.3685,10.8978,\nC1,\"Augsburg, Jakobertor\",48.3697,10.9090,\n",
    "routes.txt": "route_id,route_short_name,route_long_name,route_type,route_color,route_text_color\n"
                  "r1,1,,900,D7262E,FFFFFF\nr9,ICE,,2,,\n",
    "trips.txt": "route_id,service_id,trip_id,direction_id,shape_id\n"
                 "r1,s,t1,0,sh1\nr1,s,t2,0,sh1\nr1,s,t3,1,\nr9,s,t9,0,\n",
    "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
                      "t1,08:00:00,08:00:00,A1,1\nt1,08:02:00,08:02:00,B1,2\nt1,08:04:00,08:04:00,C1,3\n"
                      "t2,08:10:00,08:10:00,A1,1\nt2,08:12:00,08:12:00,B1,2\nt2,08:14:00,08:14:00,C1,3\n"
                      "t3,09:00:00,09:00:00,C1,1\nt3,09:04:00,09:04:00,A1,2\n"
                      "t9,08:00:00,08:00:00,A1,1\nt9,09:00:00,09:00:00,C1,2\n",
    "shapes.txt": "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\n"
                  "sh1,48.3653,10.8941,1\nsh1,48.3670,10.8960,2\nsh1,48.3685,10.8978,3\nsh1,48.3697,10.9090,4\n",
}


def test_gtfs_builds_tram_line(tmp_path):
    for name, body in FEED.items():
        (tmp_path / name).write_text(body, encoding="utf-8")
    net = load_gtfs_network(tmp_path, provider_id="augsburg", modes={"tram", "bus"}, center=(10.8978, 48.3668), strip_prefixes=("Augsburg, ",))
    assert [l.id for l in net.lines] == ["1"]  # the ICE is filtered out by mode
    line = net.lines[0]
    assert line.mode == "tram" and line.color == "#D7262E"
    assert line.stations == ["P1", "B1", "C1"]  # platform A1 folded into its parent
    assert {s.name for s in net.stations} == {"Königsplatz", "Moritzplatz", "Jakobertor"}
    assert len(line.shape) == 4


def test_efa_parsing():
    now = time.time()
    iso = lambda s: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(s))
    doc = {"stopEvents": [
        {"departureTimePlanned": iso(now + 300), "departureTimeEstimated": iso(now + 420),
         "transportation": {"disassembledName": "3", "destination": {"name": "Augsburg, Haunstetten Südwest"}}},
        {"departureTimePlanned": iso(now + 60), "transportation": {"number": "99", "name": "Bus 99", "destination": {"name": "Somewhere"}}},
    ]}

    class L:
        name, color = "Tram 3", "#00965E"

    arr = parse_efa_departures(doc, now, {"3": L()}, {"3": "3"})
    assert [a.line_id for a in arr] == ["99", "3"]
    assert arr[1].headsign == "Haunstetten Südwest" and 110 <= arr[1].delay_s <= 130
