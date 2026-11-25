"""AI assistant chat shapes."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.planner import Journey


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatContext(BaseModel):
    selected_vehicle_id: str | None = None
    selected_station_id: str | None = None
    user_lat: float | None = None
    user_lon: float | None = None


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=20)
    context: ChatContext = Field(default_factory=ChatContext)


class Highlights(BaseModel):
    stations: list[str] = Field(default_factory=list)
    lines: list[str] = Field(default_factory=list)
    vehicles: list[str] = Field(default_factory=list)


class ChatResponse(BaseModel):
    reply: str
    engine: Literal["openai", "local"]
    journeys: list[Journey] = Field(default_factory=list)
    highlights: Highlights = Field(default_factory=Highlights)
    tools_used: list[str] = Field(default_factory=list)
