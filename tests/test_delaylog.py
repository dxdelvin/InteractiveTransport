from app.schemas.transit import Arrival
from app.services.delaylog import DelayLog


def arr(line, eta, delay):
    return Arrival(vehicle_id="", line_id=line, line_name=line, color="#000", headsign="X", direction=0, eta_s=eta, delay_s=delay)


def test_later_observation_wins_and_summary(tmp_path):
    log = DelayLog(tmp_path / "d.sqlite")
    t = 1_800_000_000.0
    # same departure (planned t+600) seen twice: 10 min out with +0, 1 min out with +240
    log.record("a", [arr("2", 600, 0)], now=t)
    log.record("a", [arr("2", 60 + 240, 240)], now=t + 540)
    log.record("a", [arr("3", 30, 0), arr("3", 5000, 0)], now=t)  # far-off one is skipped
    s = log.summary(days=1, now=t + 2000)
    rows = {r["line_id"]: r for r in s["lines"]}
    assert rows["2"]["departures"] == 1 and rows["2"]["avg_delay_s"] == 240
    assert rows["2"]["punctual_pct"] == 0 and rows["3"]["punctual_pct"] == 100
    assert s["departures"] == 2
    log.close()
