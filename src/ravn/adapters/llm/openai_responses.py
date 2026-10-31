"""OpenAIResponsesAdapter — LLMPort for the OpenAI Responses API.

Speaks ``POST {base_url}/v1/responses`` natively: OpenAI's Responses API, the
ChatGPT/Codex backend, or Bifröst's ``/v1/responses`` surface — which, for a
provider with ``passthrough: true``, forwards the request to the backend
without translating it to the Anthropic format and back.

What that buys over the Chat Completions adapter is the provider's own state:

- every output item of a turn — ``reasoning`` with its ``encrypted_content``,
  ``message`` (with its ``phase``), ``function_call`` and ``compaction`` — is
  kept verbatim on the assistant message (``provider_items``) and sent back as
  is, so the model resumes its own reasoning instead of re-deriving it;
- **native compaction**: with ``context_management.compaction: native`` for a
  model, each request asks the provider to compact above a threshold
  (``context_management: [{"type": "compaction", "compact_threshold": N}]``).
  The provider then emits an opaque ``compaction`` item; the request is cut to
  start at the latest one (the documented way to continue — everything before
  it is inside it), and Ravn's own compactor never runs.

Requests are stateless (``store: false``) and always stream; :meth:`generate`
consumes the stream. Tools arrive in Anthropic shape (``name``/
``description``/``input_schema``) and are sent as Responses function tools.

Configure it::

    llm:
      model: gpt-6-sol
      provider:
        adapter: ravn.adapters.llm.openai_responses.OpenAIResponsesAdapter
        kwargs:
          base_url: http://niuu-bifrost.volundr.svc.cluster.local/api/v1/bifrost
          agent_id: regin
          auth_adapter: niuu.adapters.outbound.http_auth.WorkloadIdentityBearerTokenAuthAdapter
          auth_kwargs: {...}
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from niuu.observability import get_observability
from niuu.ports.http_auth import HttpAuthPort
from niuu.utils import import_class
from ravn.adapters.llm.provider_items import (
    COMPACTION_TYPE,
    OPENAI_RESPONSES,
    latest_compaction_index,
    provider_items_of,
    refuse_foreign_compaction,
)
from ravn.adapters.llm.transient import (
    GatewayOutage,
    StreamIncompleteError,
    post_within_window,
    resumable_stream,
)
from ravn.domain.exceptions import ConfigurationError, LLMError
from ravn.domain.models import (
    LLMResponse,
    StopReason,
    StreamEvent,
    StreamEventType,
    TokenUsage,
    ToolCall,
)
from ravn.ports.llm import LLMPort, SystemPrompt
from ravn.ports.native_compaction import NativeCompactionPort

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://api.openai.com"

# Asks for each reasoning item's opaque payload: with ``store: false`` it is the
# only way to carry reasoning from one request to the next.
_REASONING_ENCRYPTED_INCLUDE = "reasoning.encrypted_content"

# The OpenRouter-shaped opaque reasoning Bifröst's Chat Completions surface
# returns for this same backend; a history written through that adapter can be
# continued here.
_REASONING_DETAIL_TYPE = "reasoning.encrypted"
_REASONING_DETAIL_FORMAT = "openai-responses-v1"

_TERMINAL_EVENTS = frozenset({"response.completed", "response.incomplete"})
_FAILED_EVENTS = frozenset({"response.failed", "error"})
# ``incomplete_details.reason`` when the answer ran out of output room. Any
# other incomplete reason (a content filter) is not an answer either, and fails.
_INCOMPLETE_MAX_OUTPUT = "max_output_tokens"


def _system_text(system: SystemPrompt) -> str:
    if isinstance(system, str):
        return system
    return "\n\n".join(block.get("text", "") for block in system if block.get("text"))


def _tool(tool: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "name": tool["name"],
        "description": tool.get("description", ""),
        "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
        "strict": False,
    }


def _text_part(role: str, text: str) -> dict[str, Any]:
    return {"type": "input_text" if role != "assistant" else "output_text", "text": text}


def _message_item(role: str, parts: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "message", "role": role, "content": parts}


def _tool_result_output(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise LLMError(f"tool_result content must be text, got {type(content).__name__}")
    texts: list[str] = []
    for part in content:
        if not (isinstance(part, dict) and part.get("type") == "text"):
            raise LLMError("tool_result content for the Responses API may hold only text parts")
        texts.append(str(part.get("text", "")))
    return "\n".join(texts)


def _image_part(block: dict[str, Any]) -> dict[str, Any]:
    source = block.get("source") or {}
    if source.get("type") == "url" and source.get("url"):
        return {"type": "input_image", "image_url": source["url"]}
    if source.get("type") == "base64" and source.get("data") and source.get("media_type"):
        return {
            "type": "input_image",
            "image_url": f"data:{source['media_type']};base64,{source['data']}",
        }
    raise LLMError("image block needs a url source or a base64 source with media_type")


def _user_items(content: Any) -> list[dict[str, Any]]:
    """A user turn as input items; ``tool_result`` blocks become call outputs."""
    if isinstance(content, str):
        return [_message_item("user", [_text_part("user", content)])] if content else []
    items: list[dict[str, Any]] = []
    parts: list[dict[str, Any]] = []

    def flush() -> None:
        if parts:
            items.append(_message_item("user", list(parts)))
            parts.clear()

    for block in content or []:
        kind = block.get("type") if isinstance(block, dict) else None
        if kind == "text":
            if block.get("text"):
                parts.append(_text_part("user", str(block["text"])))
        elif kind == "image":
            parts.append(_image_part(block))
        elif kind == "tool_result":
            flush()
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": str(block.get("tool_use_id", "")),
                    "output": _tool_result_output(block.get("content", "")),
                }
            )
        else:
            raise LLMError(f"a user message cannot carry a {kind!r} block for the Responses API")
    flush()
    return items


def _reasoning_items(details: list[Any]) -> list[dict[str, Any]]:
    """Opaque reasoning received through Bifröst's Chat Completions surface.

    Only payloads labelled as this backend's are replayable; reasoning another
    provider produced cannot be resumed here and is left to be re-derived.
    """
    return [
        {"type": "reasoning", "summary": [], "encrypted_content": detail["data"]}
        for detail in details
        if isinstance(detail, dict)
        and detail.get("type") == _REASONING_DETAIL_TYPE
        and detail.get("format") == _REASONING_DETAIL_FORMAT
        and detail.get("data")
    ]


def _tool_use_ids(content: Any) -> list[str]:
    if not isinstance(content, list):
        return []
    return [
        str(block.get("id", ""))
        for block in content
        if isinstance(block, dict) and block.get("type") == "tool_use"
    ]


def _assistant_items(message: dict[str, Any]) -> list[dict[str, Any]]:
    """An assistant turn as input items: verbatim when the provider's own, else rebuilt.

    Raises:
        LLMError: The verbatim items name different tool calls than the
            message's content — the history was edited after the provider
            answered, and replaying it would pair outputs with the wrong calls.
    """
    native = provider_items_of(message, OPENAI_RESPONSES)
    content = message.get("content")
    if native:
        calls = [
            str(item.get("call_id", "")) for item in native if item.get("type") == "function_call"
        ]
        if calls != _tool_use_ids(content):
            raise LLMError(
                "assistant message's Responses items name tool calls "
                f"{calls} but its content holds {_tool_use_ids(content)}; the history was "
                "changed after the provider answered and cannot be replayed"
            )
        return [copy.deepcopy(item) for item in native]

    items = _reasoning_items(message.get("reasoning_details") or [])
    if isinstance(content, str):
        if content:
            items.append(_message_item("assistant", [_text_part("assistant", content)]))
        return items
    parts: list[dict[str, Any]] = []

    def flush() -> None:
        if parts:
            items.append(_message_item("assistant", list(parts)))
            parts.clear()

    for block in content or []:
        kind = block.get("type") if isinstance(block, dict) else None
        if kind == "text":
            if block.get("text"):
                parts.append(_text_part("assistant", str(block["text"])))
        elif kind == "tool_use":
            flush()
            items.append(
                {
                    "type": "function_call",
                    "call_id": str(block.get("id", "")),
                    "name": str(block.get("name", "")),
                    "arguments": json.dumps(block.get("input") or {}),
                }
            )
        else:
            raise LLMError(
                f"an assistant message cannot carry a {kind!r} block for the Responses API"
            )
    flush()
    return items


def from_latest_compaction(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cut *items* to start at the latest ``compaction`` item.

    Everything before that item is inside it; sending it again only costs
    tokens. A ``function_call`` cut away whose output is kept goes back right
    after the compaction item: the backend answers an output with no call with
    an empty response (verified against the ChatGPT Codex backend).
    """
    latest = None
    for index, item in enumerate(items):
        if item.get("type") == COMPACTION_TYPE:
            latest = index
    if latest is None:
        return items
    kept = items[latest:]
    answered = {item.get("call_id") for item in kept if item.get("type") == "function_call_output"}
    present = {item.get("call_id") for item in kept if item.get("type") == "function_call"}
    restored = [
        item
        for item in items[:latest]
        if item.get("type") == "function_call" and item.get("call_id") in answered - present
    ]
    return [kept[0], *restored, *kept[1:]]


def input_items(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ravn's api-dict history as Responses input items, from the latest compaction on."""
    items: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        if role == "assistant":
            items.extend(_assistant_items(message))
        elif role == "user":
            items.extend(_user_items(message.get("content")))
        else:
            raise LLMError(f"unsupported message role {role!r} for the Responses API")
    return from_latest_compaction(items)


def _usage(raw: dict[str, Any] | None) -> TokenUsage:
    raw = raw or {}
    input_details = raw.get("input_tokens_details") or {}
    output_details = raw.get("output_tokens_details") or {}
    return TokenUsage(
        input_tokens=int(raw.get("input_tokens") or 0),
        output_tokens=int(raw.get("output_tokens") or 0),
        cache_read_tokens=int(input_details.get("cached_tokens") or 0),
        cache_write_tokens=0,
        thinking_tokens=int(output_details.get("reasoning_tokens") or 0),
    )


def _tool_call(item: dict[str, Any]) -> ToolCall:
    raw = item.get("arguments") or ""
    try:
        arguments = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise LLMError(
            f"model produced arguments for {item.get('name')!r} that are not valid JSON: {exc}"
        ) from exc
    if not isinstance(arguments, dict):
        raise LLMError(f"model produced arguments for {item.get('name')!r} that are not an object")
    return ToolCall(
        id=str(item.get("call_id", "")), name=str(item.get("name", "")), input=arguments
    )


def _failure(event: dict[str, Any]) -> str:
    error = (event.get("response") or {}).get("error") or event.get("error") or event
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or error)
    return str(error)


class OpenAIResponsesAdapter(LLMPort, NativeCompactionPort):
    """Calls an OpenAI Responses API endpoint, keeping the provider's items verbatim.

    Constructor kwargs are forwarded from config via the dynamic adapter pattern.
    """

    @property
    def supports_thinking(self) -> bool:
        return True

    def __init__(
        self,
        *,
        api_key: str = "",
        base_url: str = _DEFAULT_BASE_URL,
        model: str = "gpt-6-sol",
        retry_base_delay: float = 1.0,
        timeout: float = 120.0,
        gateway_outage_window_seconds: float = 120.0,
        gateway_outage_max_backoff_seconds: float = 10.0,
        reasoning_effort: str = "",
        reasoning_summary: str = "auto",
        parallel_tool_calls: bool = True,
        request_options: dict | None = None,
        agent_id: str = "",
        session_id: str = "",
        auth_adapter: str = "",
        auth_kwargs: dict | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._default_model = model
        self._retry_base_delay = retry_base_delay
        self._timeout = timeout
        self._gateway_outage_window_seconds = gateway_outage_window_seconds
        self._gateway_outage_max_backoff_seconds = gateway_outage_max_backoff_seconds
        self._reasoning_effort = reasoning_effort
        self._reasoning_summary = reasoning_summary
        self._parallel_tool_calls = parallel_tool_calls
        self._request_options = copy.deepcopy(request_options or {})
        self._agent_id = agent_id
        self._session_id = session_id
        self._auth: HttpAuthPort | None = (
            import_class(auth_adapter)(**(auth_kwargs or {})) if auth_adapter else None
        )
        # Model → compaction threshold, for the models set to native compaction.
        self._compaction_thresholds: dict[str, int] = {}

    # ------------------------------------------------------------------
    # NativeCompactionPort
    # ------------------------------------------------------------------

    def enable_native_compaction(self, *, model: str, threshold_tokens: int) -> None:
        if threshold_tokens <= 0:
            raise ConfigurationError(
                f"native compaction for {model!r} needs a positive threshold, got "
                f"{threshold_tokens}"
            )
        self._compaction_thresholds[model] = threshold_tokens

    def compaction_window_start(self, messages: list[dict]) -> int:
        return latest_compaction_index(messages, OPENAI_RESPONSES)

    # ------------------------------------------------------------------
    # Request
    # ------------------------------------------------------------------

    async def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json", "accept": "text/event-stream"}
        if self._api_key:
            headers["authorization"] = f"Bearer {self._api_key}"
        if self._agent_id:
            headers["x-agent-id"] = self._agent_id
        if self._session_id:
            headers["x-session-id"] = self._session_id
        headers.update(get_observability().inject())
        if self._auth is None:
            return headers
        # Off the event loop: a token mint is a network round trip.
        return {**headers, **await asyncio.to_thread(self._auth.headers)}

    def _build_request(
        self,
        messages: list[dict],
        *,
        tools: list[dict],
        system: SystemPrompt,
        model: str,
        max_tokens: int | None,
        thinking: dict | None,
    ) -> dict[str, Any]:
        refuse_foreign_compaction(messages, OPENAI_RESPONSES, type(self).__name__)
        effective_model = model or self._default_model
        body: dict[str, Any] = copy.deepcopy(self._request_options)
        body.update(
            {
                "model": effective_model,
                "input": input_items(messages),
                "stream": True,
                "store": False,
                "include": [_REASONING_ENCRYPTED_INCLUDE],
            }
        )
        # No cap means none on the wire: the model's own maximum output applies.
        if max_tokens is not None:
            body["max_output_tokens"] = max_tokens
        instructions = _system_text(system)
        if instructions:
            body["instructions"] = instructions
        if tools:
            body["tools"] = [_tool(tool) for tool in tools]
            body["tool_choice"] = "auto"
            body["parallel_tool_calls"] = self._parallel_tool_calls
        reasoning: dict[str, str] = {}
        if self._reasoning_effort:
            reasoning["effort"] = self._reasoning_effort
        if thinking is not None and self._reasoning_summary:
            reasoning["summary"] = self._reasoning_summary
        if reasoning:
            body["reasoning"] = reasoning
        threshold = self._compaction_thresholds.get(effective_model)
        if threshold is not None:
            body["context_management"] = [{"type": COMPACTION_TYPE, "compact_threshold": threshold}]
        return body

    def _new_outage(self, payload: dict) -> GatewayOutage:
        """The retry bound of one model call (``llm.gateway_outage_window_seconds``)."""
        return GatewayOutage(
            provider="openai_responses",
            window_seconds=self._gateway_outage_window_seconds,
            base_delay=self._retry_base_delay,
            max_backoff_seconds=self._gateway_outage_max_backoff_seconds,
            context=f"model={payload.get('model')}, endpoint={self._base_url}/v1/responses",
        )

    async def _open_stream(
        self,
        client: httpx.AsyncClient,
        payload: dict,
        outage: GatewayOutage | None = None,
    ) -> httpx.Response:
        """POST the request and return the open streaming response.

        A gateway outage (refused connection, 429 or 5xx) is ridden out for up to
        the configured window, with freshly minted credentials on every attempt.
        A stream that is cut after this returns is sent again by :meth:`stream`.
        """
        return await post_within_window(
            client,
            f"{self._base_url}/v1/responses",
            payload,
            stream=True,
            outage=outage or self._new_outage(payload),
            headers=self._headers,
        )

    # ------------------------------------------------------------------
    # LLMPort
    # ------------------------------------------------------------------

    async def stream(
        self,
        messages: list[dict],
        *,
        tools: list[dict],
        system: SystemPrompt,
        model: str,
        max_tokens: int | None,
        thinking: dict | None = None,
    ) -> AsyncIterator[StreamEvent]:
        payload = self._build_request(
            messages,
            tools=tools,
            system=system,
            model=model,
            max_tokens=max_tokens,
            thinking=thinking,
        )
        effective_model = payload["model"]
        async with httpx.AsyncClient(timeout=self._timeout) as client:

            async def open_response(outage: GatewayOutage) -> httpx.Response:
                return await self._open_stream(client, payload, outage)

            # A stream the gateway cuts mid-answer is sent again within the
            # window; the consumer discards the partial answer on STREAM_RESTART.
            async for event in resumable_stream(
                outage=self._new_outage(payload),
                open_response=open_response,
                read_events=lambda response: self._events(response, effective_model),
            ):
                yield event

    async def _events(self, response: httpx.Response, model: str) -> AsyncIterator[StreamEvent]:
        if response.status_code != httpx.codes.OK:
            body = await response.aread()
            raise LLMError(
                f"Responses API error {response.status_code}: {body.decode()[:2000]}",
                status_code=response.status_code,
            )
        output: list[dict[str, Any]] = []
        usage: TokenUsage | None = None
        terminal: dict[str, Any] | None = None
        async for line in response.aiter_lines():
            if not line.startswith("data: "):
                continue
            raw = line[len("data: ") :]
            if raw == "[DONE]":
                break
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            kind = event.get("type")
            if kind in _FAILED_EVENTS:
                raise LLMError(f"Responses stream for {model!r} failed: {_failure(event)}")
            if kind == "response.output_text.delta" and event.get("delta"):
                yield StreamEvent(type=StreamEventType.TEXT_DELTA, text=str(event["delta"]))
            elif kind == "response.reasoning_summary_text.delta" and event.get("delta"):
                yield StreamEvent(type=StreamEventType.THINKING, text=str(event["delta"]))
            elif kind == "response.output_item.done" and isinstance(event.get("item"), dict):
                item = event["item"]
                output.append(item)
                if item.get("type") == "function_call":
                    yield StreamEvent(type=StreamEventType.TOOL_CALL, tool_call=_tool_call(item))
            elif kind in _TERMINAL_EVENTS:
                terminal = event.get("response") or {}
                usage = _usage(terminal.get("usage"))
                break
        if terminal is None:
            raise StreamIncompleteError(
                f"Responses stream for {model!r} ended before completion (no "
                "response.completed); the server aborted the response"
            )
        incomplete = (terminal.get("incomplete_details") or {}).get("reason")
        logger.info(
            "openai-responses: stream done model=%s status=%s%s items=%s usage=%s",
            model,
            terminal.get("status"),
            f" ({incomplete})" if incomplete else "",
            [item.get("type") for item in output],
            {"input": usage.input_tokens, "output": usage.output_tokens} if usage else None,
        )
        compactions = sum(1 for item in output if item.get("type") == COMPACTION_TYPE)
        if compactions:
            logger.info(
                "openai-responses: provider compacted the context natively (%d compaction "
                "item(s)) model=%s input_tokens=%d",
                compactions,
                model,
                usage.input_tokens if usage else 0,
            )
        if incomplete and incomplete != _INCOMPLETE_MAX_OUTPUT:
            raise LLMError(
                f"Responses stream for {model!r} ended incomplete ({incomplete}); the "
                "provider withheld the answer"
            )
        yield StreamEvent(
            type=StreamEventType.PROVIDER_ITEMS,
            provider_items=output,
            provider_protocol=OPENAI_RESPONSES,
        )
        yield StreamEvent(
            type=StreamEventType.MESSAGE_DONE,
            usage=usage,
            stop_reason=StopReason.MAX_TOKENS if incomplete else None,
        )

    async def generate(
        self,
        messages: list[dict],
        *,
        tools: list[dict],
        system: SystemPrompt,
        model: str,
        max_tokens: int | None,
        thinking: dict | None = None,
    ) -> LLMResponse:
        text: list[str] = []
        reasoning: list[str] = []
        tool_calls: list[ToolCall] = []
        items: list[dict[str, Any]] = []
        usage = TokenUsage(input_tokens=0, output_tokens=0)
        truncated = False
        async for event in self.stream(
            messages,
            tools=tools,
            system=system,
            model=model,
            max_tokens=max_tokens,
            thinking=thinking,
        ):
            match event.type:
                case StreamEventType.TEXT_DELTA:
                    text.append(event.text or "")
                case StreamEventType.THINKING:
                    reasoning.append(event.text or "")
                case StreamEventType.TOOL_CALL if event.tool_call is not None:
                    tool_calls.append(event.tool_call)
                case StreamEventType.PROVIDER_ITEMS:
                    items.extend(event.provider_items or [])
                case StreamEventType.MESSAGE_DONE:
                    usage = event.usage or usage
                    truncated = truncated or event.stop_reason == StopReason.MAX_TOKENS
                case StreamEventType.STREAM_RESTART:
                    # The answer so far belonged to a connection that dropped; the
                    # re-issued request starts it over.
                    text.clear()
                    reasoning.clear()
                    tool_calls.clear()
                    items.clear()
                    usage = TokenUsage(input_tokens=0, output_tokens=0)
                    truncated = False
        stop_reason = StopReason.TOOL_USE if tool_calls else StopReason.END_TURN
        return LLMResponse(
            content="".join(text),
            tool_calls=tool_calls,
            # Cut off for max_output_tokens: the answer — and a tool call in it —
            # is incomplete, whatever else it carries.
            stop_reason=StopReason.MAX_TOKENS if truncated else stop_reason,
            usage=usage,
            reasoning="".join(reasoning),
            provider_items=items,
            provider_protocol=OPENAI_RESPONSES,
        )
