"""Public data shapes for the transit network and live vehicles."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Mode = Literal["metro", "tram", "rail", "light_rail", "bus", "ferry"]


class ProviderInfo(BaseModel):
    id: str
    name: str
    city: str
    kind: Literal["simulated", "live"]
    basemap: Literal["procedural", "osm"]
    center: tuple[float, float]
    zoom: float
    description: str
    status: str = "ok"
    status_detail: str | None = None
    attribution: str = ""


class Station(BaseModel):
    id: str
    name: str
    lon: float
    lat: float
    lines: list[str] = Field(default_factory=list)


class Line(BaseModel):
    id: str
    name: str
    short_name: str
    color: str
    text_color: str = "#ffffff"
    mode: Mode
    loop: bool = False
    stations: list[str]
    shape: list[tuple[float, float]]
    station_offsets_m: list[float] = Field(description="Distance of each station along the shape")
    length_m: float
    avg_speed_kmh: float
    headway_s: float
    route_group: str | None = Field(None, description="Lines that are branches of one route share this")
    service_days: list[int] | None = Field(None, description="Weekdays with service, 0 = Monday (from the timetable)")
    service_start_s: float | None = Field(None, description="First departure, seconds after midnight")
    service_end_s: float | None = Field(None, description="Last arrival; above 86400 means after midnight")


class Network(BaseModel):
    provider: str
    lines: list[Line]
    stations: list[Station]
    bbox: tuple[float, float, float, float]


class Vehicle(BaseModel):
    id: str
    label: str
    line_id: str
    lon: float
    lat: float
    bearing: float
    speed_kmh: float
    direction: int = Field(description="0 = along station order, 1 = against it")
    shape_dist_m: float
    status: Literal["in_transit", "stopped", "incoming"]
    current_station_id: str | None = None
    next_station_id: str | None = None
    destination_station_id: str | None = None
    headsign: str
    occupancy: Literal["low", "medium", "high", "unknown"] = "unknown"
    delay_s: int = 0
    delay_source: Literal["live", "estimated", "none"] = Field("none", description="Where delay_s comes from")
    position_source: Literal["gps", "timetable"] = Field("gps", description="'timetable' = estimated, not tracked")
    updated_at: float


class VehicleSnapshot(BaseModel):
    provider: str
    timestamp: float
    vehicles: list[Vehicle]


class Arrival(BaseModel):
    vehicle_id: str
    line_id: str
    line_name: str
    color: str
    headsign: str
    direction: int
    eta_s: float
    delay_s: int = 0


class StopEta(BaseModel):
    station_id: str
    name: str
    eta_s: float


class VehicleDetail(BaseModel):
    vehicle: Vehicle
    line_name: str
    color: str
    mode: Mode
    destination_name: str
    next_station_name: str | None
    upcoming: list[StopEta]
    progress: float = Field(description="0..1 progress of this run")


class StationDetail(BaseModel):
    station: Station
    arrivals: list[Arrival]
    source: Literal["live", "simulated"] = "simulated"


class LineStatus(BaseModel):
    line_id: str
    name: str
    color: str
    mode: Mode
    vehicles_active: int
    avg_delay_s: float
    effective_headway_s: float | None
    health: Literal["good", "minor_delays", "major_delays", "no_service"]


class Alert(BaseModel):
    """A line that is late or not running - what riders most want to know first."""

    line_id: str
    severity: Literal["minor", "major", "no_service"]
    delay_s: int = 0
    station_id: str | None = Field(None, description="Where the delay was seen, if known")
    station_name: str | None = None
    source: Literal["live", "estimated", "none"] = "none"
    text: str
