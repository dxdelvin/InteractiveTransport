import math

from app.services.geo import Polyline, bearing_deg, catmull_rom, decode_polyline, haversine_m


def test_haversine_one_degree_latitude():
    assert math.isclose(haversine_m((0, 0), (0, 1)), 111_195, rel_tol=1e-3)


def test_bearing_cardinal():
    assert round(bearing_deg((0, 0), (0, 1))) == 0
    assert round(bearing_deg((0, 0), (1, 0))) == 90


def test_decode_polyline_reference():
    # Google's documented example
    pts = decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")
    assert pts == [(-120.2, 38.5), (-120.95, 40.7), (-126.453, 43.252)]


def test_polyline_linear_referencing_roundtrip():
    pl = Polyline([(0.0, 0.0), (0.01, 0.0), (0.01, 0.01)])
    (lon, lat), _ = pl.point_at(pl.length * 0.25)
    along, off = pl.project((lon, lat))
    assert abs(along - pl.length * 0.25) < 1
    assert off < 1


def test_slice_reverse():
    pl = Polyline([(0.0, 0.0), (0.01, 0.0), (0.02, 0.0)])
    fwd = pl.slice(100, 1500)
    rev = pl.slice(1500, 100)
    assert fwd == rev[::-1]


def test_catmull_rom_passes_through_controls():
    ctrl = [(0.0, 0.0), (1.0, 1.0), (2.0, 0.0), (3.0, 1.0)]
    out = catmull_rom(ctrl, samples_per_segment=8)
    for c in ctrl:
        assert any(math.isclose(c[0], o[0], abs_tol=1e-9) and math.isclose(c[1], o[1], abs_tol=1e-9) for o in out)
