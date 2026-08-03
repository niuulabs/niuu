"""OpenAI-compatible provider adapter.

Handles any endpoint that speaks the OpenAI Chat Completions API:
- api.openai.com (OpenAI)
- vLLM, TGI, Azure OpenAI, LM Studio, llama.cpp server, etc.

Ollama extends this class with minor quirks.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx

from bifrost.ports.provider import ProviderPort
from bifrost.translation.models import AnthropicRequest, AnthropicResponse
from bifrost.translation.streaming import openai_stream_to_anthropic
from bifrost.translation.to_anthropic import openai_to_anthropic
from bifrost.translation.to_openai import anthropic_to_openai

logger = logging.getLogger(__name__)


class OpenAICompatAdapter(ProviderPort):
    """Provider adapter for OpenAI-compatible Chat Completions endpoints.

    Translates Anthropic-format requests to OpenAI format before sending
    and converts responses back to Anthropic format.
    """

    def __init__(
        self,
        *,
        base_url: str = "https://api.openai.com",
        api_key: str = "",
        timeout: float = 120.0,
        chat_template_kwargs: dict[str, Any] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._chat_template_kwargs = dict(chat_template_kwargs or {})
        self._client = httpx.AsyncClient(timeout=timeout)

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"content-type": "application/json"}
        if self._api_key:
            headers["authorization"] = f"Bearer {self._api_key}"
        return headers

    def _completions_url(self) -> str:
        return f"{self._base_url}/v1/chat/completions"

    def _prepare_payload(self, payload: dict) -> dict:
        """Hook for subclasses to modify the payload before sending.

        Override this in subclasses to strip unsupported fields or add
        provider-specific adjustments.
        """
        if self._chat_template_kwargs:
            payload["chat_template_kwargs"] = {
                **payload.get("chat_template_kwargs", {}),
                **self._chat_template_kwargs,
            }
        return payload

    async def complete(self, request: AnthropicRequest, model: str) -> AnthropicResponse:
        payload = anthropic_to_openai(request, model)
        payload["stream"] = False
        payload = self._prepare_payload(payload)

        resp = await self._client.post(
            self._completions_url(),
            headers=self._headers(),
            json=payload,
        )
        resp.raise_for_status()
        return openai_to_anthropic(resp.json(), model)

    async def stream(self, request: AnthropicRequest, model: str) -> AsyncIterator[str]:
        payload = anthropic_to_openai(request, model)
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}
        payload = self._prepare_payload(payload)

        message_id = f"msg_{uuid.uuid4().hex[:24]}"

        async with self._client.stream(
            "POST",
            self._completions_url(),
            headers=self._headers(),
            json=payload,
        ) as resp:
            resp.raise_for_status()
            raw_lines = resp.aiter_lines()
            async for event in openai_stream_to_anthropic(
                raw_lines,
                message_id=message_id,
                model=model,
                force_nonempty_content=(
                    self._chat_template_kwargs.get("force_nonempty_content") is True
                ),
            ):
                yield event

    async def close(self) -> None:
        await self._client.aclose()
