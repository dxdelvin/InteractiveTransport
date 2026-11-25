"""Application factory."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router, ws_router
from app.core.config import STATIC_DIR, Settings, get_settings
from app.core.logging import configure_logging
from app.services.assistant import TripAssistant
from app.services.hub import TransitHub


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.hub = TransitHub(settings)
        app.state.assistant = TripAssistant(settings)
        await app.state.hub.start()
        try:
            yield
        finally:
            await app.state.hub.stop()

    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        description="Live 3D transit map: vehicle tracking, journey planning and an AI trip assistant.",
        lifespan=lifespan,
    )
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    @app.middleware("http")
    async def revalidate_static(request, call_next):
        # no build step means no hashed file names: make browsers check for a newer
        # version (cheap 304s via ETag) instead of running stale modules after an update
        response = await call_next(request)
        if request.url.path.startswith("/static/") or request.url.path == "/":
            response.headers["Cache-Control"] = "no-cache"
        return response
    app.include_router(api_router)
    app.include_router(ws_router)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app
