from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from config import settings

logger = structlog.get_logger()


class AIProviderError(RuntimeError):
    """Raised when the configured AI provider cannot respond."""


@dataclass
class ChatResponse:
    content: str
    finish_reason: str | None = None


def is_anthropic_provider() -> bool:
    return settings.ai_provider.strip().lower() in {"anthropic", "claude"}


def _chat_completions_url() -> str:
    base_url = settings.local_llm_base_url.rstrip("/")
    if base_url.endswith("/chat/completions"):
        return base_url
    return f"{base_url}/chat/completions"


def _ollama_chat_url() -> str:
    base_url = settings.local_llm_base_url.rstrip("/")
    if base_url.endswith("/v1"):
        base_url = base_url.removesuffix("/v1")
    if base_url.endswith("/api/chat"):
        return base_url
    return f"{base_url}/api/chat"


async def chat_completion(
    messages: list[dict[str, Any]],
    *,
    max_tokens: int = 512,
    temperature: float | None = None,
    response_format: dict[str, Any] | None = None,
) -> ChatResponse:
    provider = settings.ai_provider.strip().lower()

    if provider in {"anthropic", "claude"}:
        return await _anthropic_chat_completion(
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    if provider in {"ollama", "local-ollama"} or (
        provider == "local" and settings.local_llm_backend.strip().lower() == "ollama"
    ):
        return await _ollama_chat_completion(
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format=response_format,
        )

    if provider in {"openai", "openai-compatible", "local-openai"} or provider == "local":
        return await _openai_compatible_chat_completion(
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format=response_format,
        )

    raise AIProviderError("Unsupported AI_PROVIDER. Use anthropic, ollama, openai, or local.")


async def _anthropic_chat_completion(
    messages: list[dict[str, Any]],
    *,
    max_tokens: int,
    temperature: float | None,
) -> ChatResponse:
    from anthropic import AsyncAnthropic

    system_parts = []
    anthropic_messages = []
    for message in messages:
        role = message.get("role")
        content = str(message.get("content") or "")
        if role == "system":
            system_parts.append(content)
        elif role in {"user", "assistant"}:
            anthropic_messages.append({"role": role, "content": content})

    request: dict[str, Any] = {
        "model": settings.anthropic_model,
        "max_tokens": max_tokens,
        "temperature": 0.2 if temperature is None else temperature,
        "messages": anthropic_messages,
    }
    if system_parts:
        request["system"] = "\n\n".join(system_parts)

    client = AsyncAnthropic(
        api_key=settings.anthropic_api_key,
        max_retries=settings.anthropic_max_retries,
    )
    response = await client.messages.create(**request)

    content = "".join(
        getattr(block, "text", "")
        for block in response.content
        if getattr(block, "type", "text") == "text"
    )
    return ChatResponse(content=content.strip(), finish_reason=response.stop_reason)


async def _ollama_chat_completion(
    messages: list[dict[str, Any]],
    *,
    max_tokens: int,
    temperature: float | None,
    response_format: dict[str, Any] | None,
) -> ChatResponse:
    payload: dict[str, Any] = {
        "model": settings.local_llm_model,
        "messages": messages,
        "stream": False,
        "think": settings.local_llm_think,
        "keep_alive": settings.local_llm_keep_alive,
        "options": {
            "temperature": settings.local_llm_temperature if temperature is None else temperature,
            "num_ctx": settings.local_llm_context_tokens,
            "num_predict": max_tokens,
            "top_p": settings.local_llm_top_p,
            "top_k": settings.local_llm_top_k,
            "min_p": settings.local_llm_min_p,
            "repeat_penalty": settings.local_llm_repeat_penalty,
            "presence_penalty": settings.local_llm_presence_penalty,
        },
    }
    if response_format and response_format.get("type") == "json_object":
        payload["format"] = "json"

    last_error: Exception | None = None
    for attempt in range(settings.local_llm_max_retries):
        try:
            timeout = httpx.Timeout(settings.local_llm_timeout_seconds)
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(_ollama_chat_url(), json=payload)
                response.raise_for_status()
                data = response.json()

            message = data.get("message") or {}
            return ChatResponse(
                content=str(message.get("content") or "").strip(),
                finish_reason=data.get("done_reason"),
            )
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            last_error = exc
            if attempt + 1 >= settings.local_llm_max_retries:
                break
            await asyncio.sleep(2**attempt)

    logger.error("local_llm_request_failed", error=str(last_error))
    raise AIProviderError(f"Local LLM request failed: {last_error}") from last_error


async def _openai_compatible_chat_completion(
    messages: list[dict[str, Any]],
    *,
    max_tokens: int,
    temperature: float | None,
    response_format: dict[str, Any] | None,
) -> ChatResponse:
    payload: dict[str, Any] = {
        "model": settings.local_llm_model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": settings.local_llm_temperature if temperature is None else temperature,
        "stream": False,
    }
    if response_format is not None:
        payload["response_format"] = response_format

    headers = {"Content-Type": "application/json"}
    if settings.local_llm_api_key:
        headers["Authorization"] = f"Bearer {settings.local_llm_api_key}"

    last_error: Exception | None = None
    for attempt in range(settings.local_llm_max_retries):
        try:
            timeout = httpx.Timeout(settings.local_llm_timeout_seconds)
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    _chat_completions_url(),
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()

            choice = data["choices"][0]
            message = choice.get("message") or {}
            content = message.get("content") or ""
            if isinstance(content, list):
                content = "".join(
                    part.get("text", "") if isinstance(part, dict) else str(part)
                    for part in content
                )
            return ChatResponse(
                content=str(content).strip(),
                finish_reason=choice.get("finish_reason"),
            )
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            last_error = exc
            if attempt + 1 >= settings.local_llm_max_retries:
                break
            await asyncio.sleep(2**attempt)

    logger.error("local_llm_request_failed", error=str(last_error))
    raise AIProviderError(f"Local LLM request failed: {last_error}") from last_error


async def complete_text(
    *,
    system: str,
    user: str,
    max_tokens: int = 512,
    temperature: float | None = None,
    response_format: dict[str, Any] | None = None,
) -> str:
    response = await chat_completion(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=max_tokens,
        temperature=temperature,
        response_format=response_format,
    )
    return response.content


def extract_json_object(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    start = cleaned.index("{")
    depth = 0
    end = start
    for i, ch in enumerate(cleaned[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    return cleaned[start:end]


def loads_json_object(text: str) -> dict[str, Any]:
    return json.loads(extract_json_object(text))
