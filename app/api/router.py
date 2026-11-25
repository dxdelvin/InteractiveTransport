"""Mounts every versioned router."""
from fastapi import APIRouter

from app.api.v1 import assistant, network, planner, realtime, system, vehicles

api_router = APIRouter(prefix="/api/v1")
# system routes first so /providers is not captured by /{provider}/...
api_router.include_router(system.router)
api_router.include_router(network.router)
api_router.include_router(vehicles.router)
api_router.include_router(planner.router)
api_router.include_router(assistant.router)

ws_router = APIRouter()
ws_router.include_router(realtime.router)
