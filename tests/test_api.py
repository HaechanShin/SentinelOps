from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from api.main import app
from db.engine import get_session


def _empty_session_override():
    session = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=result_mock)

    async def override():
        yield session

    return override


@pytest.mark.asyncio
async def test_health():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "sentinelops"


@pytest.mark.asyncio
async def test_list_alerts_empty():
    app.dependency_overrides[get_session] = _empty_session_override()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/alerts")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_list_drafts_empty():
    app.dependency_overrides[get_session] = _empty_session_override()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/drafts")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
