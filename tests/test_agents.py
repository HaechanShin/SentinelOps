import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.sentiment_agent import analyze_sentiment


@pytest.mark.asyncio
async def test_analyze_sentiment_positive(mock_anthropic):
    mock_anthropic.messages.create.return_value.content = [
        MagicMock(text='{"sentiment": 0.8, "issue_tags": ["new-content"]}')
    ]

    with patch("agents.sentiment_agent.anthropic.AsyncAnthropic", return_value=mock_anthropic):
        result = await analyze_sentiment("This update is amazing! Best patch ever!")

    assert result["sentiment"] == 0.8
    assert "new-content" in result["issue_tags"]


@pytest.mark.asyncio
async def test_analyze_sentiment_negative(mock_anthropic):
    mock_anthropic.messages.create.return_value.content = [
        MagicMock(text='{"sentiment": -0.7, "issue_tags": ["server-stability"]}')
    ]

    with patch("agents.sentiment_agent.anthropic.AsyncAnthropic", return_value=mock_anthropic):
        result = await analyze_sentiment("Servers are down again, this game is broken!")

    assert result["sentiment"] == -0.7
    assert "server-stability" in result["issue_tags"]


@pytest.mark.asyncio
async def test_analyze_sentiment_clamps_values(mock_anthropic):
    mock_anthropic.messages.create.return_value.content = [
        MagicMock(text='{"sentiment": 1.5, "issue_tags": []}')
    ]

    with patch("agents.sentiment_agent.anthropic.AsyncAnthropic", return_value=mock_anthropic):
        result = await analyze_sentiment("test")

    assert result["sentiment"] == 1.0


@pytest.mark.asyncio
async def test_analyze_sentiment_handles_markdown(mock_anthropic):
    mock_anthropic.messages.create.return_value.content = [
        MagicMock(text='```json\n{"sentiment": 0.3, "issue_tags": ["general"]}\n```')
    ]

    with patch("agents.sentiment_agent.anthropic.AsyncAnthropic", return_value=mock_anthropic):
        result = await analyze_sentiment("How do I play this game?")

    assert result["sentiment"] == 0.3
    assert "general" in result["issue_tags"]
