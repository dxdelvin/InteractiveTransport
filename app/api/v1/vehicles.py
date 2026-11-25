"""Live vehicle positions."""
from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_provider
from app.schemas.transit import VehicleDetail, VehicleSnapshot
from app.services.providers.base import TransitProvider

router = APIRouter(prefix="/{provider}/vehicles", tags=["vehicles"])


@router.get("", response_model=VehicleSnapshot)
async def vehicles(p: TransitProvider = Depends(get_provider)) -> VehicleSnapshot:
    return p.snapshot()


@router.get("/{vehicle_id}", response_model=VehicleDetail)
async def vehicle(vehicle_id: str, p: TransitProvider = Depends(get_provider)) -> VehicleDetail:
    if vehicle_id not in p.vehicles:
        raise HTTPException(404, f"Vehicle '{vehicle_id}' is not in service")
    return p.vehicle_detail(vehicle_id)
