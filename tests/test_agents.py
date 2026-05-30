from unittest.mock import AsyncMock, patch

import pytest

from agents.sentiment_agent import analyze_sentiment


@pytest.mark.asyncio
async def test_analyze_sentiment_positive():
    with patch(
        "agents.sentiment_agent.complete_text",
        new=AsyncMock(return_value='{"sentiment": 0.8, "issue_tags": ["new-content"]}'),
    ):
        result = await analyze_sentiment("This update is amazing! Best patch ever!")

    assert result["sentiment"] == 0.8
    assert "new-content" in result["issue_tags"]


@pytest.mark.asyncio
async def test_analyze_sentiment_negative():
    with patch(
        "agents.sentiment_agent.complete_text",
        new=AsyncMock(return_value='{"sentiment": -0.7, "issue_tags": ["server-stability"]}'),
    ):
        result = await analyze_sentiment("Servers are down again, this game is broken!")

    assert result["sentiment"] == -0.7
    assert "server-stability" in result["issue_tags"]


@pytest.mark.asyncio
async def test_analyze_sentiment_clamps_values():
    with patch(
        "agents.sentiment_agent.complete_text",
        new=AsyncMock(return_value='{"sentiment": 1.5, "issue_tags": []}'),
    ):
        result = await analyze_sentiment("test")

    assert result["sentiment"] == 1.0


@pytest.mark.asyncio
async def test_analyze_sentiment_handles_markdown():
    with patch(
        "agents.sentiment_agent.complete_text",
        new=AsyncMock(return_value='```json\n{"sentiment": 0.3, "issue_tags": ["general"]}\n```'),
    ):
        result = await analyze_sentiment("How do I play this game?")

    assert result["sentiment"] == 0.3
    assert "general" in result["issue_tags"]
