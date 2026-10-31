"""Ollama inbound interface for Bifröst.

Accepts POST /api/generate and POST /api/chat in native Ollama format,
normalises the request to the internal Anthropic canonical form, routes via
the shared ModelRouter, then translates the response back to Ollama format.

Ollama streaming uses newline-delimited JSON (NDJSON), not SSE.  Each line
is a complete JSON object.  Intermediate chunks have ``"done": false``; the
final chunk has ``"done": true`` and carries timing / token metadata.

This enables tools that speak native Ollama (Open WebUI, etc.) to point at
Bifröst without any configuration changes.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any

from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from bifrost.translation.models import (
    AnthropicRequest,
    AnthropicResponse,
    Message,
    extract_text_from_response,
)

# ---------------------------------------------------------------------------
# Ollama request Pydantic models
# ---------------------------------------------------------------------------


class _OllamaOptions(BaseModel):
    """Subset of Ollama /api/generate and /api/chat options we translate."""

    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    num_predict: int | None = None
    stop: list[str] | None = None


class OllamaGenerateRequest(BaseModel):
    """Pydantic model for a POST /api/generate request."""

    model: str
    prompt: str = ""
    system: str | None = None
    stream: bool = True
    options: _OllamaOptions = Field(default_factory=_OllamaOptions)
    context: list[int] | None = None
    raw: bool = False


class _OllamaChatMessage(BaseModel):
    role: str
    content: str = ""


class OllamaChatRequest(BaseModel):
    """Pydantic model for a POST /api/chat request."""

    model: str
    messages: list[_OllamaChatMessage] = Field(default_factory=list)
    stream: bool = True
    options: _OllamaOptions = Field(default_factory=_OllamaOptions)


# ---------------------------------------------------------------------------
# Error helper
# ---------------------------------------------------------------------------


def ollama_error_response(status: int, message: str) -> JSONResponse:
    """Return a JSONResponse in the Ollama error shape."""
    return JSONResponse(status_code=status, content={"error": message})


# ---------------------------------------------------------------------------
# Request translation: Ollama → Anthropic canonical
# ---------------------------------------------------------------------------


def ollama_generate_to_anthropic(req: OllamaGenerateRequest) -> AnthropicRequest:
    """Convert an Ollama /api/generate request to Anthropic canonical format.

    The prompt becomes a single ``user`` message.  The optional ``system``
    field maps directly to the Anthropic ``system`` parameter.

    Args:
        req: Parsed Ollama generate request.

    Returns:
        An ``AnthropicRequest`` ready for the routing layer.
    """
    messages = [Message(role="user", content=req.prompt)] if req.prompt else []

    return AnthropicRequest(
        model=req.model,
        max_tokens=_answer_cap(req.options.num_predict),
        messages=messages,
        system=req.system,
        temperature=req.options.temperature,
        top_p=req.options.top_p,
        top_k=req.options.top_k,
        stop_sequences=req.options.stop or None,
        stream=req.stream,
    )


def _answer_cap(num_predict: int | None) -> int | None:
    """``num_predict`` as a cap: unset, -1 (no limit) and -2 (fill the context) are none."""
    if num_predict is None or num_predict <= 0:
        return None
    return num_predict


def ollama_chat_to_anthropic(req: OllamaChatRequest) -> AnthropicRequest:
    """Convert an Ollama /api/chat request to Anthropic canonical format.

    System messages are extracted and concatenated; remaining messages become
    alternating user/assistant turns.

    Args:
        req: Parsed Ollama chat request.

    Returns:
        An ``AnthropicRequest`` ready for the routing layer.
    """
    system_parts: list[str] = []
    anthropic_messages: list[Message] = []

    for msg in req.messages:
        if msg.role == "system":
            if msg.content:
                system_parts.append(msg.content)
            continue
        if msg.role in ("user", "assistant"):
            anthropic_messages.append(Message(role=msg.role, content=msg.content))

    system: str | None = "\n\n".join(system_parts) if system_parts else None

    return AnthropicRequest(
        model=req.model,
        max_tokens=_answer_cap(req.options.num_predict),
        messages=anthropic_messages,
        system=system,
        temperature=req.options.temperature,
        top_p=req.options.top_p,
        top_k=req.options.top_k,
        stop_sequences=req.options.stop or None,
        stream=req.stream,
    )


# ---------------------------------------------------------------------------
# Response translation: Anthropic canonical → Ollama
# ---------------------------------------------------------------------------


def _done_reason(stop_reason: str | None) -> str:
    """Map Anthropic stop_reason to an Ollama done_reason string."""
    mapping = {
        "end_turn": "stop",
        "max_tokens": "length",
        "tool_use": "stop",
        "stop_sequence": "stop",
    }
    return mapping.get(stop_reason or "end_turn", "stop")


def anthropic_response_to_ollama_generate(
    response: AnthropicResponse,
    *,
    created_at: str,
    total_duration_ns: int,
) -> dict[str, Any]:
    """Convert an Anthropic response to Ollama /api/generate non-streaming format."""
    text = extract_text_from_response(response)
    return {
        "model": response.model,
        "created_at": created_at,
        "response": text,
        "done": True,
        "done_reason": _done_reason(response.stop_reason),
        "total_duration": total_duration_ns,
        "load_duration": 0,
        "prompt_eval_count": response.usage.input_tokens,
        "prompt_eval_duration": 0,
        "eval_count": response.usage.output_tokens,
        "eval_duration": total_duration_ns,
    }


def anthropic_response_to_ollama_chat(
    response: AnthropicResponse,
    *,
    created_at: str,
    total_duration_ns: int,
) -> dict[str, Any]:
    """Convert an Anthropic response to Ollama /api/chat non-streaming format."""
    text = extract_text_from_response(response)
    return {
        "model": response.model,
        "created_at": created_at,
        "message": {"role": "assistant", "content": text},
        "done": True,
        "done_reason": _done_reason(response.stop_reason),
        "total_duration": total_duration_ns,
        "load_duration": 0,
        "prompt_eval_count": response.usage.input_tokens,
        "prompt_eval_duration": 0,
        "eval_count": response.usage.output_tokens,
        "eval_duration": total_duration_ns,
    }


# ---------------------------------------------------------------------------
# Streaming translation: Anthropic SSE → Ollama NDJSON
# ---------------------------------------------------------------------------


def _ndjson(obj: dict[str, Any]) -> str:
    """Serialise *obj* as a single newline-terminated JSON line."""
    return json.dumps(obj) + "\n"


async def _parse_anthropic_sse_to_ollama(
    source: AsyncIterator[str],
    *,
    model: str,
    start: float,
    intermediate_chunk: Callable[[str, str, str], dict[str, Any]],
    final_chunk: Callable[[str, str, str, int, int, int], dict[str, Any]],
) -> AsyncIterator[str]:
    """Parse Anthropic SSE events and yield Ollama NDJSON lines.

    This is the shared core for both /api/generate and /api/chat streaming.
    The two callables control the chunk shape for each endpoint:

    - ``intermediate_chunk(model, created_at, text)`` — called for each text
      delta; should return a dict with ``"done": false``.
    - ``final_chunk(model, created_at, done_reason, total_ns, input_tokens,
      output_tokens)`` — called once after the stream ends; should return a
      dict with ``"done": true`` and timing / token metadata.

    Args:
        source: Async iterator of raw Anthropic SSE strings from the router.
        model: Model name passed through to each chunk builder.
        start: ``time.monotonic()`` value recorded before the request was sent.
        intermediate_chunk: Callable that builds intermediate NDJSON payloads.
        final_chunk: Callable that builds the terminal NDJSON payload.

    Yields:
        Newline-terminated JSON strings (NDJSON).
    """
    current_event_type = ""
    input_tokens = 0
    output_tokens = 0
    done_reason = "stop"

    async for raw in source:
        for raw_line in raw.split("\n"):
            line = raw_line.strip()

            if not line:
                current_event_type = ""
                continue

            if line.startswith("event: "):
                current_event_type = line[7:]
                continue

            if not line.startswith("data: "):
                continue

            payload_str = line[6:]
            if payload_str == "[DONE]":
                break

            try:
                payload = json.loads(payload_str)
            except json.JSONDecodeError:
                continue

            event_type = current_event_type or payload.get("type", "")

            if event_type == "message_start":
                usage = payload.get("message", {}).get("usage", {})
                input_tokens = usage.get("input_tokens", 0)

            elif event_type == "content_block_delta":
                delta = payload.get("delta", {})
                if delta.get("type") == "text_delta":
                    text = delta.get("text", "")
                    created_at = datetime.now(UTC).isoformat()
                    yield _ndjson(intermediate_chunk(model, created_at, text))

            elif event_type == "message_delta":
                delta = payload.get("delta", {})
                done_reason = _done_reason(delta.get("stop_reason"))
                usage = payload.get("usage", {})
                output_tokens = usage.get("output_tokens", output_tokens)

            elif event_type == "message_stop":
                break

    total_ns = int((time.monotonic() - start) * 1e9)
    created_at = datetime.now(UTC).isoformat()
    yield _ndjson(
        final_chunk(model, created_at, done_reason, total_ns, input_tokens, output_tokens)
    )


async def anthropic_stream_to_ollama_generate(
    source: AsyncIterator[str],
    *,
    model: str,
    start: float,
) -> AsyncIterator[str]:
    """Translate Anthropic SSE stream to Ollama /api/generate NDJSON.

    Each text delta is emitted as an intermediate chunk (``"done": false``).
    On stream completion a final chunk with ``"done": true`` and token counts
    is emitted.

    Args:
        source: Async iterator of raw Anthropic SSE strings from the router.
        model: Model name to embed in every chunk.
        start: ``time.monotonic()`` value recorded before the request was sent.

    Yields:
        NDJSON lines compatible with the Ollama /api/generate streaming format.
    """

    def _intermediate(m: str, ts: str, text: str) -> dict[str, Any]:
        return {"model": m, "created_at": ts, "response": text, "done": False}

    def _final(m: str, ts: str, dr: str, ns: int, inp: int, out: int) -> dict[str, Any]:
        return {
            "model": m,
            "created_at": ts,
            "response": "",
            "done": True,
            "done_reason": dr,
            "total_duration": ns,
            "load_duration": 0,
            "prompt_eval_count": inp,
            "prompt_eval_duration": 0,
            "eval_count": out,
            "eval_duration": ns,
        }

    async for chunk in _parse_anthropic_sse_to_ollama(
        source, model=model, start=start, intermediate_chunk=_intermediate, final_chunk=_final
    ):
        yield chunk


async def anthropic_stream_to_ollama_chat(
    source: AsyncIterator[str],
    *,
    model: str,
    start: float,
) -> AsyncIterator[str]:
    """Translate Anthropic SSE stream to Ollama /api/chat NDJSON.

    Each text delta is emitted as an intermediate chunk (``"done": false``)
    wrapping the text in a ``message`` object.  On stream completion a final
    chunk with ``"done": true`` and token counts is emitted.

    Args:
        source: Async iterator of raw Anthropic SSE strings from the router.
        model: Model name to embed in every chunk.
        start: ``time.monotonic()`` value recorded before the request was sent.

    Yields:
        NDJSON lines compatible with the Ollama /api/chat streaming format.
    """

    def _intermediate(m: str, ts: str, text: str) -> dict[str, Any]:
        return {
            "model": m,
            "created_at": ts,
            "message": {"role": "assistant", "content": text},
            "done": False,
        }

    def _final(m: str, ts: str, dr: str, ns: int, inp: int, out: int) -> dict[str, Any]:
        return {
            "model": m,
            "created_at": ts,
            "message": {"role": "assistant", "content": ""},
            "done": True,
            "done_reason": dr,
            "total_duration": ns,
            "load_duration": 0,
            "prompt_eval_count": inp,
            "prompt_eval_duration": 0,
            "eval_count": out,
            "eval_duration": ns,
        }

    async for chunk in _parse_anthropic_sse_to_ollama(
        source, model=model, start=start, intermediate_chunk=_intermediate, final_chunk=_final
    ):
        yield chunk
