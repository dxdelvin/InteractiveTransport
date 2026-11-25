"""Static network: lines, stations, basemap scenery, line status."""
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from app.api.deps import get_provider
from app.schemas.transit import LineStatus, Network, Station, StationDetail
from app.services.providers.base import TransitProvider

router = APIRouter(prefix="/{provider}", tags=["network"])


@router.get("/network", response_model=Network)
async def network(p: TransitProvider = Depends(get_provider)) -> Network:
    return p._idx().network


@router.get("/scenery")
async def scenery(p: TransitProvider = Depends(get_provider)) -> JSONResponse:
    return JSONResponse(p.scenery(), headers={"Cache-Control": "public, max-age=3600"})


@router.get("/lines/status", response_model=list[LineStatus])
async def lines_status(p: TransitProvider = Depends(get_provider)) -> list[LineStatus]:
    return [p.line_status(lid) for lid in p._idx().lines]


@router.get("/lines/{line_id}/status", response_model=LineStatus)
async def line_status(line_id: str, p: TransitProvider = Depends(get_provider)) -> LineStatus:
    if line_id not in p._idx().lines:
        raise HTTPException(404, f"Unknown line '{line_id}'")
    return p.line_status(line_id)


@router.get("/stations/search", response_model=list[Station])
async def search(q: str = Query(..., min_length=1, max_length=80), limit: int = Query(8, ge=1, le=25), p: TransitProvider = Depends(get_provider)) -> list[Station]:
    return p.search_stations(q, limit)


@router.get("/stations/nearest")
async def nearest(lat: float, lon: float, k: int = Query(3, ge=1, le=10), p: TransitProvider = Depends(get_provider)) -> list[dict]:
    return [{"station": s.model_dump(), "distance_m": round(d)} for s, d in p.nearest_stations(lon, lat, k)]


@router.get("/stations/{station_id}", response_model=StationDetail)
async def station(station_id: str, limit: int = Query(12, ge=1, le=40), p: TransitProvider = Depends(get_provider)) -> StationDetail:
    if station_id not in p._idx().stations:
        raise HTTPException(404, f"Unknown station '{station_id}'")
    detail = p.station_arrivals(station_id, limit)
    live = await p.live_departures(station_id)
    if live:
        detail.arrivals, detail.source = live[:limit], "live"
    return detail
