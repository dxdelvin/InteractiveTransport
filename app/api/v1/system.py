"""Health and provider discovery."""
from fastapi import APIRouter, Depends

from app.api.deps import get_assistant, get_hub
from app.core.config import get_settings
from app.schemas.transit import ProviderInfo
from app.services.assistant import TripAssistant
from app.services.hub import TransitHub

router = APIRouter(tags=["system"])


@router.get("/health")
async def health(assistant: TripAssistant = Depends(get_assistant)) -> dict:
    s = get_settings()
    return {"status": "ok", "version": s.version, "ai": "openai" if assistant.enabled else "local", "default_provider": s.default_provider}


@router.get("/providers", response_model=list[ProviderInfo])
async def providers(hub: TransitHub = Depends(get_hub)) -> list[ProviderInfo]:
    return [p.describe() for p in hub.providers.values()]
