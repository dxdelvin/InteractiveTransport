def test_fleet_on_every_line(augsburg):
    assert {v.line_id for v in augsburg.vehicles.values()} == set(augsburg._idx().lines)


def test_trams_and_buses(augsburg):
    modes = {l.mode for l in augsburg._idx().lines.values()}
    assert {"tram", "bus"} <= modes


def test_vehicles_stay_on_their_line(augsburg):
    idx = augsburg._idx()
    for v in augsburg.vehicles.values():
        _, off = idx.polylines[v.line_id].project((v.lon, v.lat))
        assert off < 2.0, v.id


def test_motion_is_continuous(augsburg):
    """Step the clock 1 s at a time: no vehicle may jump or roll backwards."""
    t0 = 1_750_000_000.0
    prev = {v.id: v for s in augsburg._sims for v in s.vehicles(t0)}
    for step in range(1, 120):
        cur = {v.id: v for s in augsburg._sims for v in s.vehicles(t0 + step)}
        for vid, v in cur.items():
            p = prev[vid]
            if p.direction != v.direction:
                continue  # turnaround
            moved = v.shape_dist_m - p.shape_dist_m
            assert abs(moved) < 40, (vid, moved)
            assert (moved if v.direction == 0 else -moved) > -0.5, vid
        prev = cur


def test_station_board_sorted(augsburg):
    etas = [a.eta_s for a in augsburg.station_arrivals("koenigsplatz").arrivals]
    assert etas and etas == sorted(etas)


def test_vehicle_detail(augsburg):
    d = augsburg.vehicle_detail(next(iter(augsburg.vehicles)))
    assert 0 <= d.progress <= 1 and d.destination_name


def test_search_handles_umlauts_and_typos(augsburg):
    assert augsburg.search_stations("konigsplatz")[0].id == "koenigsplatz"
    assert augsburg.search_stations("Gögg")[0].id in {"goeggingen", "goegginger-bruecke"}
    assert augsburg.search_stations("hauptbanhof")[0].id == "hauptbahnhof"


def test_live_delay_is_eased_in(augsburg):
    """A new live delay is applied gradually: the fleet slows down, it never jumps back."""
    sim = augsburg._sims[0]
    t0 = 1_760_000_000.0
    sim.live_delay_s = None
    sim._applied.clear()
    before = {v.id: v for v in sim.vehicles(t0)}
    sim.live_delay_s = 600.0
    for step in range(1, 60):
        cur = {v.id: v for v in sim.vehicles(t0 + step)}
        for vid, v in cur.items():
            p = before[vid]
            if p.direction == v.direction:
                moved = v.shape_dist_m - p.shape_dist_m
                assert (moved if v.direction == 0 else -moved) > -0.5, vid
        before = cur
    v = next(iter(before.values()))
    assert v.delay_source == "live" and v.position_source == "timetable"
    assert 0 < v.delay_s < 600  # still catching up
    sim.live_delay_s = None
    sim._applied.clear()
