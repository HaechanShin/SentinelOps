import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_anthropic():
    with patch("anthropic.AsyncAnthropic") as mock:
        client = AsyncMock()
        mock.return_value = client

        response = MagicMock()
        response.content = [MagicMock(text='{"sentiment": 0.5, "issue_tags": ["general"]}')]
        client.messages.create = AsyncMock(return_value=response)

        yield client


@pytest.fixture
def sample_posts():
    return [
        {
            "source": "steam",
            "external_id": "steam_test_001",
            "title": None,
            "content": "Game is great after update! Love the new features.",
            "author": "steam_user_1",
            "url": "https://store.steampowered.com/app/578080",
        },
        {
            "source": "steam",
            "external_id": "steam_test_002",
            "title": None,
            "content": "Server issues again, getting disconnected every game. This is unacceptable.",
            "author": "steam_user_2",
            "url": "https://store.steampowered.com/app/578080",
        },
        {
            "source": "steam",
            "external_id": "steam_test_003",
            "title": None,
            "content": "Cheaters everywhere, literally unplayable. Reported 5 aimbotters today.",
            "author": "steam_user_3",
            "url": "https://store.steampowered.com/app/578080",
        },
    ]


@pytest.fixture
def sample_alert():
    return {
        "id": "test-alert-001",
        "alert_type": "sentiment_drop",
        "severity": "high",
        "trigger_data": {
            "drop": 0.45,
            "recent_avg": -0.32,
            "earlier_avg": 0.13,
            "recent_count": 15,
            "representative_posts": [
                {
                    "id": "post-1",
                    "external_id": "steam_test_002",
                    "content": "Server issues again, getting disconnected every game.",
                    "sentiment": -0.8,
                    "url": "https://store.steampowered.com/app/578080",
                    "issue_tags": ["server"],
                }
            ],
        },
    }
