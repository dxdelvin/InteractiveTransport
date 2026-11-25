"""Application settings, loaded from environment variables and an optional .env file."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

APP_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = APP_DIR / "static"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Interactive Transport"
    version: str = "1.0.0"

    host: str = "127.0.0.1"
    port: int = 8000
    reload: bool = True
    log_level: str = "info"

    default_provider: str = "augsburg"
    broadcast_interval_seconds: float = 1.0

    mbta_api_key: str = ""
    mbta_base_url: str = "https://api-v3.mbta.com"
    mbta_poll_seconds: float = 10.0
    mbta_route_types: str = "0,1"  # 0 = light rail, 1 = subway, 2 = commuter rail, 3 = bus

    # Augsburg
    augsburg_gtfs_path: str = "app/data/gtfs/augsburg.zip"
    augsburg_radius_m: float = 12_000
    efa_enabled: bool = True
    efa_base_url: str = "https://bahnland-bayern.de/efa"

    # AI assistant (OpenAI)
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"
    openai_base_url: str = ""  # optional: any OpenAI-compatible endpoint
    ai_max_tool_rounds: int = 8


@lru_cache
def get_settings() -> Settings:
    return Settings()
