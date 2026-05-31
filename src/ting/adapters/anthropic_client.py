"""Shared Anthropic-compatible Messages API call helper.

Used by BifrostAdapter and RavnDispatcher to avoid duplicating HTTP
infrastructure for the same /v1/messages endpoint.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

ANTHROPIC_API_VERSION = "2023-06-01"


async def anthropic_messages_call(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    api_key: str,
    model: str,
    max_tokens: int,
    messages: list[dict[str, Any]],
    system: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """POST to the Anthropic-compatible /v1/messages endpoint.

    Returns a ``(text, usage)`` tuple where *usage* contains
    ``input_tokens``, ``output_tokens``, and ``latency_ms``.
    """
    headers: dict[str, str] = {
        "anthropic-version": ANTHROPIC_API_VERSION,
        "content-type": "application/json",
    }
    if api_key:
        headers["x-api-key"] = api_key

    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system:
        payload["system"] = system

    t0 = time.monotonic()
    resp = await client.post(f"{base_url}/v1/messages", headers=headers, json=payload)
    latency_ms = (time.monotonic() - t0) * 1000
    resp.raise_for_status()

    data = resp.json()
    content_blocks = data.get("content", [])
    text_parts = [b["text"] for b in content_blocks if b.get("type") == "text"]
    raw_usage = data.get("usage", {})
    usage: dict[str, Any] = {
        "input_tokens": int(raw_usage.get("input_tokens", 0)),
        "output_tokens": int(raw_usage.get("output_tokens", 0)),
        "latency_ms": latency_ms,
    }
    return "".join(text_parts), usage
