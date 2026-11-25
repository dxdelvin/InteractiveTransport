"""Shared FastAPI dependencies."""
from __future__ import annotations

from fastapi import HTTPException, Path, Request

from app.services.assistant import TripAssistant
from app.services.hub import TransitHub
from app.services.providers.base import TransitProvider


def get_hub(request: Request) -> TransitHub:
    return request.app.state.hub


def get_assistant(request: Request) -> TripAssistant:
    return request.app.state.assistant


async def get_provider(request: Request, provider: str = Path(..., description="Data source id, e.g. augsburg or mbta")) -> TransitProvider:
    hub: TransitHub = request.app.state.hub
    if provider not in hub.providers:
        raise HTTPException(404, f"Unknown provider '{provider}'. Try one of: {', '.join(hub.providers)}")
    try:
        return await hub.ensure_fresh(provider)
    except Exception as exc:  # network down, upstream API error, ...
        p = hub.get(provider)
        p.status, p.status_detail = "error", f"{type(exc).__name__}: {exc}"[:300]
        raise HTTPException(503, f"{provider} data is unavailable right now: {p.status_detail}") from exc
