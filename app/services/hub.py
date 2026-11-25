"""Provider registry + realtime fan-out.

Each provider gets one background task that refreshes it on its own cadence. Refreshes
are demand-driven: a provider with no WebSocket subscribers and no recent REST traffic
goes idle, so an unused live feed is never polled.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time

from fastapi import WebSocket

from app.core.config import Settings
from app.services.providers import AugsburgProvider, MBTAProvider, TransitProvider

log = logging.getLogger(__name__)

IDLE_AFTER_S = 120.0


class TransitHub:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.providers: dict[str, TransitProvider] = {
            "augsburg": AugsburgProvider(settings),
            "mbta": MBTAProvider(settings),
        }
        self.subscribers: dict[str, set[WebSocket]] = {pid: set() for pid in self.providers}
        self._last_used: dict[str, float] = {pid: 0.0 for pid in self.providers}
        self._tasks: list[asyncio.Task] = []
        self._wake: dict[str, asyncio.Event] = {pid: asyncio.Event() for pid in self.providers}

    # ---- lifecycle ------------------------------------------------------------------
    async def start(self) -> None:
        for pid in self.providers:
            self._tasks.append(asyncio.create_task(self._run(pid), name=f"provider:{pid}"))
            if getattr(self.providers[pid], "delay_log", None):
                self._tasks.append(asyncio.create_task(self._log_delays(pid), name=f"delaylog:{pid}"))

    async def _log_delays(self, pid: str) -> None:
        """Runs whether or not anyone is watching, so the punctuality log has no gaps."""
        p = self.providers[pid]
        while True:
            try:
                await p.log_tick()  # type: ignore[attr-defined]
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("%s delay log tick failed: %s", pid, exc)
            await asyncio.sleep(self.settings.delay_log_interval_s)

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        for p in self.providers.values():
            await p.close()

    # ---- access ---------------------------------------------------------------------
    def get(self, pid: str) -> TransitProvider:
        return self.providers[pid]

    def touch(self, pid: str) -> None:
        self._last_used[pid] = time.time()
        self._wake[pid].set()

    def _active(self, pid: str) -> bool:
        return bool(self.subscribers[pid]) or time.time() - self._last_used[pid] < IDLE_AFTER_S

    async def ensure_fresh(self, pid: str) -> TransitProvider:
        """Make sure a provider has a network and at least one vehicle refresh."""
        self.touch(pid)
        p = self.providers[pid]
        if not p.last_refresh:
            await p.refresh()
        return p

    # ---- realtime -------------------------------------------------------------------
    async def subscribe(self, pid: str, ws: WebSocket) -> None:
        self.subscribers[pid].add(ws)
        self.touch(pid)

    def unsubscribe(self, pid: str, ws: WebSocket) -> None:
        self.subscribers[pid].discard(ws)

    async def _broadcast(self, pid: str) -> None:
        subs = list(self.subscribers[pid])
        if not subs:
            return
        p = self.providers[pid]
        payload = {"type": "vehicles", **p.snapshot().model_dump(mode="json"), "status": p.status}
        dead = []
        for ws in subs:
            try:
                await ws.send_json(payload)
            except Exception:  # client went away mid-send
                dead.append(ws)
        for ws in dead:
            self.unsubscribe(pid, ws)

    async def _run(self, pid: str) -> None:
        p = self.providers[pid]
        backoff = 0.0
        while True:
            if not self._active(pid):
                self._wake[pid].clear()
                await self._wake[pid].wait()
            try:
                await p.refresh()
                backoff = 0.0
                await self._broadcast(pid)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                backoff = min(60.0, max(5.0, backoff * 2))
                p.status = "error"
                p.status_detail = f"{type(exc).__name__}: {exc}"[:300]
                log.warning("%s refresh failed (%s); retrying in %.0fs", pid, p.status_detail, backoff)
                for ws in list(self.subscribers[pid]):
                    with contextlib.suppress(Exception):
                        await ws.send_json({"type": "status", "provider": pid, "status": "error", "detail": p.status_detail})
            await asyncio.sleep(backoff or p.refresh_interval_s)
