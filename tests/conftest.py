import asyncio

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.factory import create_app
from app.services.providers.augsburg import AugsburgProvider

# Tests never touch the network: no AI key, no EFA, no GTFS file.
TEST_SETTINGS = dict(openai_api_key="", efa_enabled=False, augsburg_gtfs_path="", _env_file=None)


@pytest.fixture()
def client():
    app = create_app(Settings(**TEST_SETTINGS))
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def augsburg():
    p = AugsburgProvider(Settings(**TEST_SETTINGS))
    asyncio.run(p.refresh())
    return p
