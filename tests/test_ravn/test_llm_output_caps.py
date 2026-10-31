"""An answer cap of ``None`` sends none, and every adapter reports truncation.

``max_tokens=None`` means the provider's own limit applies: OpenAI-compatible
and Responses requests carry no cap, the Anthropic adapter (whose protocol
requires one) reads the model's maximum output from the Models API, and the
Bifröst adapter leaves that derivation to Bifröst. A stream that was cut off
says so on MESSAGE_DONE, so the agent can refuse it instead of acting on half
an answer.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from ravn.adapters.llm.anthropic import AnthropicAdapter
from ravn.adapters.llm.bifrost import BifrostAdapter
from ravn.adapters.llm.openai import OpenAICompatibleAdapter
from ravn.adapters.llm.openai_responses import OpenAIResponsesAdapter
from ravn.domain.exceptions import LLMError
from ravn.domain.models import StopReason, StreamEventType

_MESSAGES = [{"role": "user", "content": "hi"}]
_ANTHROPIC = "https://api.anthropic.test"
_RESPONSES = "http://bifrost.test/api/v1/bifrost"


def _data(*events: dict) -> bytes:
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events).encode()


async def _drain(adapter, **kwargs) -> list:
    return [
        event
        async for event in adapter.stream(
            _MESSAGES,
            tools=[],
            system="s",
            model=kwargs.pop("model", "m"),
            max_tokens=kwargs.pop("max_tokens", None),
        )
    ]


def _done_reasons(events: list) -> list:
    return [e.stop_reason for e in events if e.type == StreamEventType.MESSAGE_DONE]


# ---------------------------------------------------------------------------
# OpenAI-compatible (vLLM, llama.cpp, Bifröst chat completions)
# ---------------------------------------------------------------------------


class TestOpenAICompatibleCap:
    def test_none_sends_no_cap(self) -> None:
        adapter = OpenAICompatibleAdapter()
        for model in ("Qwen/Qwen3.8-27B", "gpt-5.2"):
            body = adapter._build_request(
                _MESSAGES, tools=[], system="s", model=model, max_tokens=None, stream=True
            )
            assert "max_tokens" not in body
            assert "max_completion_tokens" not in body

    def test_explicit_cap_is_sent(self) -> None:
        body = OpenAICompatibleAdapter()._build_request(
            _MESSAGES, tools=[], system="s", model="Qwen/Qwen3.8-27B", max_tokens=64, stream=True
        )
        assert body["max_tokens"] == 64

    @respx.mock
    async def test_length_finish_is_reported_on_the_stream(self) -> None:
        chunk = {"choices": [{"delta": {"content": "half an an"}, "finish_reason": "length"}]}
        usage = {"usage": {"prompt_tokens": 3, "completion_tokens": 64}}
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(200, content=_data(chunk, usage) + b"data: [DONE]\n\n")
        )
        events = await _drain(OpenAICompatibleAdapter(), max_tokens=64)
        assert StopReason.MAX_TOKENS in _done_reasons(events)

    @respx.mock
    async def test_truncated_tool_call_is_not_emitted(self) -> None:
        call = {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "c1",
                                "function": {"name": "bash", "arguments": '{"cm'},
                            }
                        ]
                    },
                    "finish_reason": "length",
                }
            ]
        }
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(200, content=_data(call) + b"data: [DONE]\n\n")
        )
        events = await _drain(OpenAICompatibleAdapter(), max_tokens=8)
        assert not [e for e in events if e.type == StreamEventType.TOOL_CALL]
        assert StopReason.MAX_TOKENS in _done_reasons(events)

    @respx.mock
    async def test_a_finished_stream_reports_no_truncation(self) -> None:
        chunk = {"choices": [{"delta": {"content": "done"}, "finish_reason": "stop"}]}
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(200, content=_data(chunk) + b"data: [DONE]\n\n")
        )
        events = await _drain(OpenAICompatibleAdapter())
        assert StopReason.MAX_TOKENS not in _done_reasons(events)


# ---------------------------------------------------------------------------
# Responses API
# ---------------------------------------------------------------------------


def _terminal(kind: str, *, reason: str | None = None) -> dict:
    response: dict = {"status": "incomplete" if reason else "completed"}
    if reason:
        response["incomplete_details"] = {"reason": reason}
    response["usage"] = {"input_tokens": 3, "output_tokens": 5}
    return {"type": kind, "response": response}


def _text(delta: str) -> dict:
    return {"type": "response.output_text.delta", "delta": delta}


class TestResponsesCap:
    def test_none_sends_no_max_output_tokens(self) -> None:
        body = OpenAIResponsesAdapter(base_url=_RESPONSES)._build_request(
            _MESSAGES, tools=[], system="s", model="gpt-6-sol", max_tokens=None, thinking=None
        )
        assert "max_output_tokens" not in body

    def test_explicit_cap_is_sent(self) -> None:
        body = OpenAIResponsesAdapter(base_url=_RESPONSES)._build_request(
            _MESSAGES, tools=[], system="s", model="gpt-6-sol", max_tokens=500, thinking=None
        )
        assert body["max_output_tokens"] == 500

    @respx.mock
    async def test_incomplete_for_max_output_tokens_is_max_tokens(self) -> None:
        stream = _data(
            _text("cut of"), _terminal("response.incomplete", reason="max_output_tokens")
        )
        respx.post(f"{_RESPONSES}/v1/responses").mock(
            return_value=httpx.Response(200, content=stream)
        )
        response = await OpenAIResponsesAdapter(base_url=_RESPONSES).generate(
            _MESSAGES, tools=[], system="s", model="gpt-6-sol", max_tokens=10
        )
        assert response.stop_reason == StopReason.MAX_TOKENS
        assert response.content == "cut of"

    @respx.mock
    async def test_completed_is_end_turn(self) -> None:
        stream = _data(_text("all of it"), _terminal("response.completed"))
        respx.post(f"{_RESPONSES}/v1/responses").mock(
            return_value=httpx.Response(200, content=stream)
        )
        response = await OpenAIResponsesAdapter(base_url=_RESPONSES).generate(
            _MESSAGES, tools=[], system="s", model="gpt-6-sol", max_tokens=None
        )
        assert response.stop_reason == StopReason.END_TURN

    @respx.mock
    async def test_other_incomplete_reasons_raise(self) -> None:
        stream = _data(_terminal("response.incomplete", reason="content_filter"))
        respx.post(f"{_RESPONSES}/v1/responses").mock(
            return_value=httpx.Response(200, content=stream)
        )
        with pytest.raises(LLMError, match="content_filter"):
            await OpenAIResponsesAdapter(base_url=_RESPONSES).generate(
                _MESSAGES, tools=[], system="s", model="gpt-6-sol", max_tokens=None
            )


# ---------------------------------------------------------------------------
# Anthropic: the parameter is required, so None is the model's own maximum
# ---------------------------------------------------------------------------


def _message(stop_reason: str = "end_turn") -> dict:
    return {
        "content": [{"type": "text", "text": "ok"}],
        "stop_reason": stop_reason,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


class TestAnthropicCap:
    @respx.mock
    async def test_none_reads_the_models_api_once(self) -> None:
        models = respx.get(f"{_ANTHROPIC}/v1/models/claude-opus-5-5").mock(
            return_value=httpx.Response(200, json={"id": "claude-opus-5-5", "max_tokens": 128000})
        )
        sent = respx.post(f"{_ANTHROPIC}/v1/messages").mock(
            return_value=httpx.Response(200, json=_message())
        )
        adapter = AnthropicAdapter(api_key="k", base_url=_ANTHROPIC)
        for _ in range(2):
            await adapter.generate(
                _MESSAGES, tools=[], system="s", model="claude-opus-5-5", max_tokens=None
            )
        assert models.call_count == 1
        assert json.loads(sent.calls.last.request.content)["max_tokens"] == 128000

    @respx.mock
    async def test_explicit_cap_skips_the_lookup(self) -> None:
        models = respx.get(f"{_ANTHROPIC}/v1/models/claude-opus-5-5")
        sent = respx.post(f"{_ANTHROPIC}/v1/messages").mock(
            return_value=httpx.Response(200, json=_message())
        )
        await AnthropicAdapter(api_key="k", base_url=_ANTHROPIC).generate(
            _MESSAGES, tools=[], system="s", model="claude-opus-5-5", max_tokens=700
        )
        assert not models.called
        assert json.loads(sent.calls.last.request.content)["max_tokens"] == 700

    @respx.mock
    async def test_an_unreadable_limit_raises_naming_the_knob(self) -> None:
        respx.get(f"{_ANTHROPIC}/v1/models/m").mock(return_value=httpx.Response(404, text="nope"))
        with pytest.raises(LLMError, match="llm.max_tokens"):
            await AnthropicAdapter(api_key="k", base_url=_ANTHROPIC).generate(
                _MESSAGES, tools=[], system="s", model="m", max_tokens=None
            )

    @respx.mock
    async def test_a_catalog_without_max_tokens_raises(self) -> None:
        respx.get(f"{_ANTHROPIC}/v1/models/m").mock(
            return_value=httpx.Response(200, json={"id": "m"})
        )
        with pytest.raises(LLMError, match="no max_tokens"):
            await AnthropicAdapter(api_key="k", base_url=_ANTHROPIC).generate(
                _MESSAGES, tools=[], system="s", model="m", max_tokens=None
            )

    @respx.mock
    async def test_context_window_exceeded_is_max_tokens(self) -> None:
        respx.post(f"{_ANTHROPIC}/v1/messages").mock(
            return_value=httpx.Response(200, json=_message("model_context_window_exceeded"))
        )
        response = await AnthropicAdapter(api_key="k", base_url=_ANTHROPIC).generate(
            _MESSAGES, tools=[], system="s", model="m", max_tokens=10
        )
        assert response.stop_reason == StopReason.MAX_TOKENS

    @respx.mock
    async def test_refusal_raises(self) -> None:
        respx.post(f"{_ANTHROPIC}/v1/messages").mock(
            return_value=httpx.Response(200, json=_message("refusal"))
        )
        with pytest.raises(LLMError, match="refusal"):
            await AnthropicAdapter(api_key="k", base_url=_ANTHROPIC).generate(
                _MESSAGES, tools=[], system="s", model="m", max_tokens=10
            )

    @respx.mock
    async def test_stream_reports_max_tokens_from_message_delta(self) -> None:
        stream = _data(
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "x"},
            },
            {
                "type": "message_delta",
                "delta": {"stop_reason": "max_tokens"},
                "usage": {"output_tokens": 10},
            },
            {"type": "message_stop"},
        )
        respx.post(f"{_ANTHROPIC}/v1/messages").mock(
            return_value=httpx.Response(200, content=stream)
        )
        events = await _drain(AnthropicAdapter(api_key="k", base_url=_ANTHROPIC), max_tokens=10)
        assert _done_reasons(events) == [StopReason.MAX_TOKENS]


class TestBifrostCap:
    @respx.mock
    async def test_none_is_left_to_bifrost(self) -> None:
        sent = respx.post("http://bifrost.test/v1/messages").mock(
            return_value=httpx.Response(200, json=_message())
        )
        await BifrostAdapter(base_url="http://bifrost.test").generate(
            _MESSAGES, tools=[], system="s", model="Qwen/Qwen3.8-27B", max_tokens=None
        )
        assert "max_tokens" not in json.loads(sent.calls.last.request.content)
