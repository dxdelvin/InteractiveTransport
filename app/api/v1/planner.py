"""Journey planning."""
from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_provider
from app.schemas.planner import PlanRequest, PlanResponse
from app.services.providers.base import TransitProvider
from app.services.routing import Planner, PlanningError

router = APIRouter(prefix="/{provider}", tags=["planner"])


@router.post("/plan", response_model=PlanResponse)
async def plan(req: PlanRequest, p: TransitProvider = Depends(get_provider)) -> PlanResponse:
    try:
        return Planner(p).plan(req)
    except PlanningError as exc:
        raise HTTPException(422, str(exc)) from exc
