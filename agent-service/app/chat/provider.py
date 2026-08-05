"""Model provider port: streaming LLM deltas.

``ModelProvider`` is a Protocol so the service never depends on a concrete client.
Production wires an OpenAI-compatible ``/chat/completions`` stream from settings; the
``DeterministicProvider`` is used when no provider is configured (local dev, E2E) and
in tests, where determinism matters.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.core.config import Settings
from app.core.errors import ApiError

# The deterministic fallback text used when no provider is configured. Tests assert on it.
DEFAULT_FAKE_TEXT = "Hello from the Goudan agent!"


@dataclass(frozen=True)
class ProviderDelta:
    """A single chunk of generated text."""

    text: str


@dataclass(frozen=True)
class ProviderMessage:
    """A conversation turn handed to the model."""

    role: str
    content: str


class ModelProvider(Protocol):
    def stream(self, messages: list[ProviderMessage]) -> AsyncIterator[ProviderDelta]: ...

    async def complete(self, messages: list[ProviderMessage], *, json_schema: bool = False) -> str: ...



class DeterministicProvider:
    """An offline provider that yields a fixed token sequence.

    ``delay_seconds`` makes the stream observable (and interruptible) in local dev and
    E2E; tests pass ``delay_seconds=0``.
    """

    def __init__(
        self,
        text: str = DEFAULT_FAKE_TEXT,
        chunk_size: int = 8,
        delay_seconds: float = 0.0,
        *,
        complete_response: str | None = None,
        complete_fn: Callable[[list[ProviderMessage]], str] | None = None,
    ) -> None:
        self._text = text
        self._chunk_size = chunk_size
        self._delay_seconds = delay_seconds
        # Default: a chitchat intent with no draft — keeps existing chat tests green.
        self._complete_response = complete_response or json.dumps({"intent": "chitchat"})
        self._complete_fn = complete_fn

    async def stream(self, messages: list[ProviderMessage]) -> AsyncIterator[ProviderDelta]:
        for i in range(0, len(self._text), self._chunk_size):
            if self._delay_seconds > 0:
                await asyncio.sleep(self._delay_seconds)
            yield ProviderDelta(text=self._text[i : i + self._chunk_size])

    async def complete(self, messages: list[ProviderMessage], *, json_schema: bool = False) -> str:
        if self._complete_fn is not None:
            return self._complete_fn(messages)
        return self._complete_response


class OpenAICompatibleProvider:
    """A minimal OpenAI-compatible chat-completions streaming client."""

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model

    async def stream(self, messages: list[ProviderMessage]) -> AsyncIterator[ProviderDelta]:
        payload = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
        }
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                async with client.stream(
                    "POST", f"{self._base_url}/chat/completions", json=payload, headers=headers
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data = line[len("data: ") :].strip()
                        if data == "[DONE]":
                            return
                        try:
                            delta = json.loads(data)["choices"][0]["delta"].get("content")
                        except (ValueError, KeyError, IndexError, TypeError):
                            continue
                        if delta:
                            yield ProviderDelta(text=delta)
        except httpx.HTTPError as exc:
            raise ApiError("MODEL_UNAVAILABLE", "Model provider is unavailable.", True) from exc

    async def complete(self, messages: list[ProviderMessage], *, json_schema: bool = False) -> str:
        payload = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
        }
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        if json_schema:
            payload["response_format"] = {"type": "json_object"}
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions", json=payload, headers=headers
                )
                if response.status_code == 400 and json_schema:
                    # Some OpenAI-compatible endpoints reject response_format; retry plain.
                    payload.pop("response_format", None)
                    response = await client.post(
                        f"{self._base_url}/chat/completions", json=payload, headers=headers
                    )
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise ApiError("MODEL_UNAVAILABLE", "Model provider is unavailable.", True) from exc


def build_provider(settings: Settings) -> ModelProvider:
    """Return the configured provider, or the deterministic fallback when unset."""
    if settings.provider_base_url and settings.provider_api_key:
        return OpenAICompatibleProvider(
            settings.provider_base_url, settings.provider_api_key, settings.provider_model
        )
    # A per-chunk delay keeps the dev/E2E stream observable and interruptible.
    return DeterministicProvider(delay_seconds=0.25)
