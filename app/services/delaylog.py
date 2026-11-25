"""A local log of observed departure delays, for punctuality statistics.

Every live departure we see (from EFA boards) is stored once per stop, line and
planned time. Later observations overwrite earlier ones, so each row ends up holding
the delay seen closest to departure, the most accurate one.

The data never leaves this machine; summaries are served by /api/v1/{provider}/punctuality.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from app.schemas.transit import Arrival

RECORD_WITHIN_S = 20 * 60   # ignore far-off predictions; they're mostly the plan
ON_TIME_S = 60              # "on time": less than a minute late
PUNCTUAL_S = 180            # common German urban threshold: under three minutes late


class DelayLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute(
                """CREATE TABLE IF NOT EXISTS departures (
                       station_id TEXT NOT NULL, line_id TEXT NOT NULL, headsign TEXT NOT NULL,
                       planned_ts INTEGER NOT NULL, delay_s INTEGER NOT NULL,
                       eta_s REAL NOT NULL, observed_at REAL NOT NULL,
                       PRIMARY KEY (station_id, line_id, headsign, planned_ts))"""
            )
            self._db.execute("CREATE INDEX IF NOT EXISTS dep_planned ON departures (planned_ts)")
            self._db.commit()

    def record(self, station_id: str, arrivals: list[Arrival], now: float | None = None) -> int:
        now = now or time.time()
        rows = [
            (station_id, a.line_id, a.headsign, round(now + a.eta_s - a.delay_s), a.delay_s, a.eta_s, now)
            for a in arrivals
            if a.eta_s <= RECORD_WITHIN_S
        ]
        if not rows:
            return 0
        with self._lock:
            self._db.executemany(
                """INSERT INTO departures VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (station_id, line_id, headsign, planned_ts) DO UPDATE SET
                     delay_s = excluded.delay_s, eta_s = excluded.eta_s, observed_at = excluded.observed_at
                   WHERE excluded.eta_s <= departures.eta_s""",
                rows,
            )
            self._db.commit()
        return len(rows)

    def summary(self, days: float = 30, lines: set[str] | None = None, now: float | None = None) -> dict:
        now = now or time.time()
        since = now - days * 86400
        with self._lock:
            cur = self._db.execute(
                """SELECT line_id, COUNT(*), AVG(delay_s), SUM(delay_s < ?), SUM(delay_s < ?), SUM(delay_s >= 300), MAX(delay_s),
                          MIN(planned_ts), MAX(planned_ts)
                   FROM departures WHERE planned_ts BETWEEN ? AND ? GROUP BY line_id""",
                (ON_TIME_S, PUNCTUAL_S, since, now),
            )
            rows = cur.fetchall()
        out = []
        for line_id, n, avg, on_time, punctual, late5, worst, first, last in rows:
            if lines is not None and line_id not in lines:
                continue
            out.append({
                "line_id": line_id,
                "departures": n,
                "avg_delay_s": round(avg or 0, 1),
                "on_time_pct": round(100 * on_time / n, 1),
                "punctual_pct": round(100 * punctual / n, 1),
                "late_5min_pct": round(100 * late5 / n, 1),
                "worst_delay_s": worst,
            })
        out.sort(key=lambda r: r["punctual_pct"])
        total = sum(r["departures"] for r in out)
        return {
            "days": days,
            "departures": total,
            "punctual_pct": round(sum(r["punctual_pct"] * r["departures"] for r in out) / total, 1) if total else None,
            "thresholds": {"on_time_s": ON_TIME_S, "punctual_s": PUNCTUAL_S},
            "first_observation": min((r[7] for r in rows), default=None),
            "last_observation": max((r[8] for r in rows), default=None),
            "lines": out,
        }

    def close(self) -> None:
        with self._lock:
            self._db.close()
