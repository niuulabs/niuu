"""Anthropic provider adapter — passes requests directly to the Anthropic API."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import httpx

from bifrost.domain.usage import anthropic_compaction_usage
from bifrost.ports.provider import ProviderError, ProviderPort
from bifrost.translation.models import (
    AnthropicRequest,
    AnthropicResponse,
    CompactionBlock,
    ContentBlock,
    TextBlock,
    ToolUseBlock,
    UsageInfo,
)

logger = logging.getLogger(__name__)

ANTHROPIC_API_VERSION = "2023-06-01"

# The beta each ``context_management`` edit type needs. An edit not listed is
# refused rather than sent without its beta, which the API would reject anyway.
CONTEXT_MANAGEMENT_BETAS: dict[str, str] = {
    "compact_20260112": "compact-2026-01-12",
    "clear_tool_uses_20250919": "context-management-2025-06-27",
    "clear_thinking_20251015": "context-management-2025-06-27",
}


def context_management_betas(context_management: dict | None) -> list[str]:
    """The ``anthropic-beta`` values the request's context-management edits need.

    Raises:
        ProviderError: An edit type Bifröst does not know the beta for.
    """
    betas: list[str] = []
    for edit in (context_management or {}).get("edits") or []:
        kind = edit.get("type") if isinstance(edit, dict) else None
        beta = CONTEXT_MANAGEMENT_BETAS.get(str(kind))
        if beta is None:
            raise ProviderError(
                f"context_management edit {kind!r} is not supported; known edits: "
                f"{', '.join(sorted(CONTEXT_MANAGEMENT_BETAS))}"
            )
        if beta not in betas:
            betas.append(beta)
    return betas


class AnthropicAdapter(ProviderPort):
    """Routes requests directly to the Anthropic Messages API.

    No format translation is needed; the canonical Bifröst format *is* the
    Anthropic format.  We serialise the request, forward it, and parse the
    response back into our typed models.

    That makes this the native path for Anthropic's own server-side compaction:
    ``context_management`` is forwarded with the beta header it needs,
    ``compaction`` blocks go out and come back unchanged, and the tokens the
    compaction iterations cost are added to the usage.
    """

    accepts_context_management = True

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
        # Model → maximum output tokens, as the Models API reports it.
        self._output_limits: dict[str, int] = {}

    def _headers(self, request: AnthropicRequest | None = None) -> dict[str, str]:
        headers: dict[str, str] = {
            "anthropic-version": ANTHROPIC_API_VERSION,
            "content-type": "application/json",
        }
        if self._api_key:
            headers["x-api-key"] = self._api_key
        betas = context_management_betas(request.context_management) if request else []
        if betas:
            headers["anthropic-beta"] = ",".join(betas)
        return headers

    async def _output_cap(self, model: str) -> int:
        """The model's maximum output, read from the Models API once per model.

        The Messages API requires ``max_tokens``; a caller that sent no cap gets
        the model's own limit rather than a number chosen here.
        """
        cached = self._output_limits.get(model)
        if cached is not None:
            return cached
        url = f"{self._base_url}/v1/models/{model}"
        resp = await self._client.get(url, headers=self._headers())
        if resp.status_code != httpx.codes.OK:
            raise ProviderError(
                f"the request sent no max_tokens and the output limit of {model!r} could "
                f"not be read from {url} ({resp.status_code}: {resp.text[:500]}); send "
                "max_tokens explicitly"
            )
        limit = resp.json().get("max_tokens")
        if not isinstance(limit, int) or limit <= 0:
            raise ProviderError(
                f"the request sent no max_tokens and the Models API reported no output "
                f"limit for {model!r} at {url}; send max_tokens explicitly"
            )
        self._output_limits[model] = limit
        return limit

    def _build_payload(self, request: AnthropicRequest, model: str) -> dict:
        """Serialise the request to a JSON-compatible dict for the Anthropic API."""
        payload = request.model_dump(
            exclude_none=True,
            exclude={"chat_template_kwargs", "stream", "reasoning_effort"},
        )
        payload["model"] = model
        if request._response_format is not None:
            payload["output_config"] = {
                "format": {
                    "type": "json_schema",
                    "schema": request._response_format["json_schema"]["schema"],
                }
            }
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
            elif block_type == "compaction":
                blocks.append(CompactionBlock(content=block.get("content", "")))
        return blocks

    async def complete(self, request: AnthropicRequest, model: str) -> AnthropicResponse:
        payload = self._build_payload(request, model)
        if request.max_tokens is None:
            payload["max_tokens"] = await self._output_cap(model)
        payload["stream"] = False

        resp = await self._client.post(
            f"{self._base_url}/v1/messages",
            headers=self._headers(request),
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

        usage_raw = data.get("usage", {})
        compaction = anthropic_compaction_usage(usage_raw)
        return AnthropicResponse(
            id=data.get("id", "msg_unknown"),
            content=self._parse_content(data.get("content", [])),
            model=data.get("model", model),
            stop_reason=data.get("stop_reason"),
            stop_sequence=data.get("stop_sequence"),
            usage=UsageInfo(
                input_tokens=usage_raw.get("input_tokens", 0) + compaction.input_tokens,
                output_tokens=usage_raw.get("output_tokens", 0) + compaction.output_tokens,
            ),
        )

    async def stream(self, request: AnthropicRequest, model: str) -> AsyncIterator[str]:
        payload = self._build_payload(request, model)
        if request.max_tokens is None:
            payload["max_tokens"] = await self._output_cap(model)
        payload["stream"] = True

        async with self._client.stream(
            "POST",
            f"{self._base_url}/v1/messages",
            headers=self._headers(request),
            json=payload,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line:
                    yield line + "\n"

    async def close(self) -> None:
        await self._client.aclose()
