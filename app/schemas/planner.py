"""Journey planner request/response shapes."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Preference = Literal["fastest", "fewest_transfers", "least_walking"]


class PlaceRef(BaseModel):
    """Where a journey starts or ends: a station id, a coordinate, or free text."""

    station_id: str | None = None
    lat: float | None = None
    lon: float | None = None
    query: str | None = None
    label: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "PlaceRef":
        if not (self.station_id or self.query or (self.lat is not None and self.lon is not None)):
            raise ValueError("Give a station_id, a query, or lat+lon")
        return self


class PlanRequest(BaseModel):
    origin: PlaceRef
    destination: PlaceRef
    preference: Preference = "fastest"
    alternatives: int = Field(3, ge=1, le=5)
    use_live: bool = Field(True, description="Use live vehicle positions for the first wait")


class Leg(BaseModel):
    kind: Literal["walk", "ride"]
    from_name: str
    to_name: str
    from_station_id: str | None = None
    to_station_id: str | None = None
    duration_s: float
    wait_s: float = 0
    distance_m: float
    geometry: list[tuple[float, float]]
    line_id: str | None = None
    line_name: str | None = None
    color: str | None = None
    direction: int | None = None
    headsign: str | None = None
    stops: list[str] = Field(default_factory=list)
    next_vehicle_id: str | None = None


class Journey(BaseModel):
    legs: list[Leg]
    duration_s: float
    transfers: int
    walking_m: float
    summary: str
    tags: list[str] = Field(default_factory=list)


class PlanResponse(BaseModel):
    origin_label: str
    destination_label: str
    journeys: list[Journey]
