"""Anthropic provider adapter — passes requests directly to the Anthropic API."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import httpx

from bifrost.ports.provider import ProviderPort
from bifrost.translation.models import (
    AnthropicRequest,
    AnthropicResponse,
    ContentBlock,
    TextBlock,
    ToolUseBlock,
    UsageInfo,
)

logger = logging.getLogger(__name__)

ANTHROPIC_API_VERSION = "2023-06-01"


class AnthropicAdapter(ProviderPort):
    """Routes requests directly to the Anthropic Messages API.

    No format translation is needed; the canonical Bifröst format *is* the
    Anthropic format.  We serialise the request, forward it, and parse the
    response back into our typed models.
    """

    def __init__(
        self,
        *,
        base_url: str = "https://api.anthropic.com",
        api_key: str = "",
        timeout: float = 120.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._client = httpx.AsyncClient(timeout=timeout)

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "anthropic-version": ANTHROPIC_API_VERSION,
            "content-type": "application/json",
        }
        if self._api_key:
            headers["x-api-key"] = self._api_key
        return headers

    def _build_payload(self, request: AnthropicRequest, model: str) -> dict:
        """Serialise the request to a JSON-compatible dict for the Anthropic API."""
        payload = request.model_dump(
            exclude_none=True,
            exclude={"chat_template_kwargs", "stream"},
        )
        payload["model"] = model
        return payload

    def _parse_content(self, raw_content: list[dict]) -> list[ContentBlock]:
        blocks: list[ContentBlock] = []
        for block in raw_content:
            block_type = block.get("type")
            if block_type == "text":
                blocks.append(TextBlock(text=block.get("text", "")))
            elif block_type == "tool_use":
                blocks.append(
                    ToolUseBlock(
                        id=block.get("id", ""),
                        name=block.get("name", ""),
                        input=block.get("input", {}),
                    )
                )
        return blocks

    async def complete(self, request: AnthropicRequest, model: str) -> AnthropicResponse:
        payload = self._build_payload(request, model)
        payload["stream"] = False

        resp = await self._client.post(
            f"{self._base_url}/v1/messages",
            headers=self._headers(),
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

        usage_raw = data.get("usage", {})
        return AnthropicResponse(
            id=data.get("id", "msg_unknown"),
            content=self._parse_content(data.get("content", [])),
            model=data.get("model", model),
            stop_reason=data.get("stop_reason"),
            stop_sequence=data.get("stop_sequence"),
            usage=UsageInfo(
                input_tokens=usage_raw.get("input_tokens", 0),
                output_tokens=usage_raw.get("output_tokens", 0),
            ),
        )

    async def stream(self, request: AnthropicRequest, model: str) -> AsyncIterator[str]:
        payload = self._build_payload(request, model)
        payload["stream"] = True

        async with self._client.stream(
            "POST",
            f"{self._base_url}/v1/messages",
            headers=self._headers(),
            json=payload,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line:
                    yield line + "\n"

    async def close(self) -> None:
        await self._client.aclose()
