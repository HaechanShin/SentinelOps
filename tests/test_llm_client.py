from unittest.mock import AsyncMock, patch

import pytest

from agents import llm_client


@pytest.mark.asyncio
async def test_chat_completion_dispatches_to_anthropic():
    with (
        patch.object(llm_client.settings, "ai_provider", "anthropic"),
        patch.object(
            llm_client,
            "_anthropic_chat_completion",
            new=AsyncMock(return_value=llm_client.ChatResponse(content="ok")),
        ) as mocked,
    ):
        response = await llm_client.chat_completion([{"role": "user", "content": "hi"}])

    assert response.content == "ok"
    mocked.assert_awaited_once()


@pytest.mark.asyncio
async def test_chat_completion_dispatches_to_ollama():
    with (
        patch.object(llm_client.settings, "ai_provider", "ollama"),
        patch.object(
            llm_client,
            "_ollama_chat_completion",
            new=AsyncMock(return_value=llm_client.ChatResponse(content="ok")),
        ) as mocked,
    ):
        response = await llm_client.chat_completion([{"role": "user", "content": "hi"}])

    assert response.content == "ok"
    mocked.assert_awaited_once()


@pytest.mark.asyncio
async def test_chat_completion_dispatches_local_openai_backend():
    with (
        patch.object(llm_client.settings, "ai_provider", "local"),
        patch.object(llm_client.settings, "local_llm_backend", "openai"),
        patch.object(
            llm_client,
            "_openai_compatible_chat_completion",
            new=AsyncMock(return_value=llm_client.ChatResponse(content="ok")),
        ) as mocked,
    ):
        response = await llm_client.chat_completion([{"role": "user", "content": "hi"}])

    assert response.content == "ok"
    mocked.assert_awaited_once()
