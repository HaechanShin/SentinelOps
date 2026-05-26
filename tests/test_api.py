from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from api.main import app


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
    with patch("api.routers.alerts.get_session") as mock_session:
        session = AsyncMock()
        result_mock = AsyncMock()
        result_mock.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=result_mock)

        async def gen():
            yield session

        mock_session.return_value = gen()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/alerts")
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_list_drafts_empty():
    with patch("api.routers.drafts.get_session") as mock_session:
        session = AsyncMock()
        result_mock = AsyncMock()
        result_mock.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=result_mock)

        async def gen():
            yield session

        mock_session.return_value = gen()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/drafts")
        assert resp.status_code == 200
