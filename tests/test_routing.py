import pytest

from app.schemas.planner import PlaceRef, PlanRequest
from app.services.routing import Planner, PlanningError


@pytest.fixture(scope="module")
def planner(augsburg):
    return Planner(augsburg)


def _plan(planner, o, d, **kw):
    return planner.plan(PlanRequest(origin=PlaceRef(query=o), destination=PlaceRef(query=d), **kw))


def test_direct_tram(planner):
    best = _plan(planner, "Hauptbahnhof", "Haunstetten Südwest").journeys[0]
    assert best.transfers == 0
    assert [l.line_id for l in best.legs if l.kind == "ride"] == ["3"]


def test_transfer_needed(planner):
    res = _plan(planner, "Oberhausen Nord", "Universität")
    assert res.journeys[0].transfers >= 1 and "Fastest" in res.journeys[0].tags


def test_bus_routes_are_used(planner):
    best = _plan(planner, "Firnhaberau", "Lechhausen").journeys[0]
    assert best.legs[0].line_id == "41"


def test_durations_add_up(planner):
    best = _plan(planner, "Stadtbergen", "Messe").journeys[0]
    assert sum(l.duration_s + l.wait_s for l in best.legs) == pytest.approx(best.duration_s, abs=3)


def test_coordinates_origin_walks_first(planner):
    st = planner.idx.stations["moritzplatz"]
    res = planner.plan(PlanRequest(origin=PlaceRef(lat=st.lat + 0.003, lon=st.lon, label="Home"), destination=PlaceRef(query="Göggingen")))
    first = res.journeys[0].legs[0]
    assert first.kind == "walk" and first.from_name == "Home"


def test_unknown_stop(planner):
    with pytest.raises(PlanningError):
        _plan(planner, "Atlantis", "Königsplatz")


def test_avoid_lines_puts_other_routes_first(planner):
    normal = _plan(planner, "Hauptbahnhof", "Moritzplatz", alternatives=4).journeys
    used = {l.line_id for l in normal[0].legs if l.kind == "ride"}
    avoid = sorted(used)[:1]
    js = _plan(planner, "Hauptbahnhof", "Moritzplatz", alternatives=4, avoid_lines=avoid).journeys
    assert js and not any(l.line_id in avoid for l in js[0].legs)
