"""AI trip assistant."""
from fastapi import APIRouter, Depends

from app.api.deps import get_assistant, get_provider
from app.schemas.assistant import ChatRequest, ChatResponse
from app.services.assistant import TripAssistant
from app.services.providers.base import TransitProvider

router = APIRouter(prefix="/{provider}", tags=["assistant"])


@router.post("/assistant", response_model=ChatResponse)
async def chat(req: ChatRequest, p: TransitProvider = Depends(get_provider), assistant: TripAssistant = Depends(get_assistant)) -> ChatResponse:
    return await assistant.chat(p, req)
