from unittest.mock import AsyncMock, patch

import pytest

from ingestion import steam_collector


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeAsyncClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def get(self, url, params):
        self.requests.append(params.copy())
        return FakeResponse(self.responses.pop(0))


class FakeRedis:
    def __init__(self, data=None):
        self.data = data or {}
        self.closed = False

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value):
        self.data[key] = value

    async def aclose(self):
        self.closed = True


def _review(review_id: str, created: int = 1_780_000_000):
    return {
        "recommendationid": review_id,
        "review": f"Review {review_id}",
        "author": {"steamid": f"user_{review_id}"},
        "voted_up": False,
        "timestamp_created": created,
    }


@pytest.mark.asyncio
async def test_collect_reviews_starts_from_latest_and_stops_at_seen_id(monkeypatch):
    fake_client = FakeAsyncClient(
        [
            {
                "reviews": [_review("new_2"), _review("new_1"), _review("seen")],
                "cursor": "older-page",
            }
        ]
    )
    monkeypatch.setattr(
        steam_collector.httpx,
        "AsyncClient",
        lambda timeout: fake_client,
    )

    reviews, latest_review_id = await steam_collector.collect_steam_reviews(
        last_seen_review_id="seen"
    )

    assert fake_client.requests[0]["cursor"] == "*"
    assert len(fake_client.requests) == 1
    assert latest_review_id == "new_2"
    assert [review["external_id"] for review in reviews] == ["steam_new_2", "steam_new_1"]


@pytest.mark.asyncio
async def test_run_steam_collection_tracks_latest_review_per_app():
    latest_key = steam_collector._latest_review_id_key()
    fake_redis = FakeRedis({latest_key: "seen"})
    reviews = [{"external_id": "steam_new_1"}]

    with (
        patch.object(steam_collector, "_get_redis", new=AsyncMock(return_value=fake_redis)),
        patch.object(
            steam_collector,
            "collect_steam_reviews",
            new=AsyncMock(return_value=(reviews, "new_1")),
        ) as collect_mock,
        patch.object(steam_collector, "store_reviews", new=AsyncMock(return_value=1)),
    ):
        result = await steam_collector.run_steam_collection()

    assert result == {"collected": 1, "stored": 1}
    collect_mock.assert_awaited_once_with(last_seen_review_id="seen")
    assert fake_redis.data[latest_key] == "new_1"
    assert fake_redis.closed
