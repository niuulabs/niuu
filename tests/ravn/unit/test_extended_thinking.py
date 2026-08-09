"""Tests for extended thinking integration (NIU-497).

Covers:
- ExtendedThinkingConfig in config
- TokenUsage.thinking_tokens tracking and arithmetic
- StreamEventType.THINKING
- RavnEvent.thinking() factory method
- AnthropicAdapter: thinking param in request, THINKING stream events,
  thinking_tokens in usage, headers, generate() with thinking blocks
  it for non-supporting providers
- LLMPort.supports_thinking property
- Agent: _parse_think_flag, configured reasoning activation,
  THINKING events emitted in _call_llm_streaming
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from ravn.adapters.llm.anthropic import AnthropicAdapter
from ravn.agent import RavnAgent, _parse_think_flag
from ravn.config import ExtendedThinkingConfig
from ravn.domain.events import RavnEvent, RavnEventType
from ravn.domain.models import (
    LLMResponse,
    StopReason,
    StreamEvent,
    StreamEventType,
    TokenUsage,
)
from ravn.ports.llm import LLMPort
from tests.ravn.conftest import MockLLM, make_text_response
from tests.ravn.fixtures.fakes import InMemoryChannel

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_extended_thinking_config_defaults():
    cfg = ExtendedThinkingConfig()
    assert cfg.enabled is False
    assert cfg.budget_tokens == 8000
    assert cfg.auto_trigger is True
    assert cfg.auto_trigger_on_retry is True


def test_extended_thinking_config_custom():
    cfg = ExtendedThinkingConfig(
        enabled=True,
        budget_tokens=4000,
        auto_trigger=False,
        auto_trigger_on_retry=False,
    )
    assert cfg.enabled is True
    assert cfg.budget_tokens == 4000
    assert cfg.auto_trigger is False
    assert cfg.auto_trigger_on_retry is False


# ---------------------------------------------------------------------------
# TokenUsage — thinking_tokens
# ---------------------------------------------------------------------------


def test_token_usage_thinking_tokens_default():
    usage = TokenUsage(input_tokens=10, output_tokens=20)
    assert usage.thinking_tokens == 0


def test_token_usage_thinking_tokens_set():
    usage = TokenUsage(input_tokens=10, output_tokens=20, thinking_tokens=50)
    assert usage.thinking_tokens == 50


def test_token_usage_add_propagates_thinking_tokens():
    a = TokenUsage(input_tokens=5, output_tokens=10, thinking_tokens=30)
    b = TokenUsage(input_tokens=3, output_tokens=7, thinking_tokens=20)
    result = a + b
    assert result.thinking_tokens == 50
    assert result.input_tokens == 8
    assert result.output_tokens == 17


# ---------------------------------------------------------------------------
# StreamEventType.THINKING
# ---------------------------------------------------------------------------


def test_stream_event_type_thinking_exists():
    assert StreamEventType.THINKING == "thinking"


def test_thinking_stream_event():
    evt = StreamEvent(type=StreamEventType.THINKING, text="I need to think about this.")
    assert evt.type == StreamEventType.THINKING
    assert evt.text == "I need to think about this."


# ---------------------------------------------------------------------------
# RavnEvent.thinking factory
# ---------------------------------------------------------------------------


def test_ravn_event_thinking_type():
    evt = RavnEvent.thinking("ravn-test", "some reasoning", "corr-1", "sess-1")
    assert evt.type == RavnEventType.THOUGHT
    assert evt.payload["text"] == "some reasoning"
    assert evt.payload.get("thinking") is True


def test_ravn_event_thought_no_thinking_flag():
    evt = RavnEvent.thought("ravn-test", "text delta", "corr-1", "sess-1")
    assert evt.type == RavnEventType.THOUGHT
    assert evt.payload.get("thinking") is None


# ---------------------------------------------------------------------------
# LLMPort.supports_thinking property
# ---------------------------------------------------------------------------


def test_llm_port_supports_thinking_default_false():
    llm = MockLLM([make_text_response()])
    assert llm.supports_thinking is False


def test_anthropic_adapter_supports_thinking_true():
    adapter = AnthropicAdapter(api_key="test")
    assert adapter.supports_thinking is True


# ---------------------------------------------------------------------------
# _parse_think_flag helper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "user_input, expected_flag, expected_text",
    [
        ("think: write a plan", True, "write a plan"),
        ("think:write a plan", True, "write a plan"),
        ("THINK: hello", True, "hello"),
        ("normal message", False, "normal message"),
        # Standalone --think: collapses to a single space between words
        ("please --think about this", True, "please about this"),
        ("--think", True, ""),
        # --thinking must NOT trigger the flag
        ("explain --thinking process", False, "explain --thinking process"),
        # mid-text --think with no surrounding whitespace must not trigger
        ("I do not --thinking this works", False, "I do not --thinking this works"),
    ],
)
def test_parse_think_flag(user_input, expected_flag, expected_text):
    flag, text = _parse_think_flag(user_input)
    assert flag == expected_flag
    assert text.strip() == expected_text.strip()


# ---------------------------------------------------------------------------
# AnthropicAdapter — _headers with thinking enabled
# ---------------------------------------------------------------------------


def test_anthropic_adapter_headers_without_thinking():
    adapter = AnthropicAdapter(api_key="sk-test")
    headers = adapter._headers(thinking_enabled=False)
    assert "interleaved-thinking-2025-05-14" not in headers["anthropic-beta"]
    assert "prompt-caching-2024-07-31" in headers["anthropic-beta"]


def test_anthropic_adapter_headers_with_thinking():
    adapter = AnthropicAdapter(api_key="sk-test")
    headers = adapter._headers(thinking_enabled=True)
    assert "interleaved-thinking-2025-05-14" in headers["anthropic-beta"]
    assert "prompt-caching-2024-07-31" in headers["anthropic-beta"]


# ---------------------------------------------------------------------------
# AnthropicAdapter — _build_request with thinking
# ---------------------------------------------------------------------------


def test_anthropic_adapter_build_request_no_thinking():
    adapter = AnthropicAdapter(api_key="sk-test")
    req = adapter._build_request(
        [],
        tools=[],
        system="",
        model="claude-sonnet-4-6",
        max_tokens=1024,
        stream=False,
        thinking=None,
    )
    assert "thinking" not in req


def test_anthropic_adapter_build_request_with_thinking():
    adapter = AnthropicAdapter(api_key="sk-test")
    thinking = {"type": "enabled", "budget_tokens": 4000}
    req = adapter._build_request(
        [],
        tools=[],
        system="",
        model="claude-sonnet-4-6",
        max_tokens=8192,
        stream=False,
        thinking=thinking,
    )
    assert req["thinking"] == thinking


def test_anthropic_adapter_does_not_send_prior_reasoning_as_message_field():
    adapter = AnthropicAdapter(api_key="sk-test")
    req = adapter._build_request(
        [{"role": "assistant", "content": "answer", "reasoning": "internal"}],
        tools=[],
        system="",
        model="claude-sonnet-4-6",
        max_tokens=1024,
        stream=False,
        thinking=None,
    )

    assert req["messages"] == [{"role": "assistant", "content": "answer"}]


# ---------------------------------------------------------------------------
# AnthropicAdapter — stream() with thinking blocks
# ---------------------------------------------------------------------------


def _make_sse_lines(*event_dicts: dict) -> str:
    """Build SSE data lines from a list of event dicts."""
    return "\n".join(f"data: {json.dumps(d)}" for d in event_dicts) + "\n"


@pytest.mark.asyncio
async def test_anthropic_adapter_stream_emits_thinking_events():
    """THINKING stream events are emitted for thinking_delta blocks."""
    adapter = AnthropicAdapter(api_key="sk-test", base_url="https://api.test")

    sse_body = _make_sse_lines(
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}},
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "I should consider..."},
        },
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "text_delta", "text": "Answer."},
        },
        {"type": "content_block_stop", "index": 1},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 25},
        },
    )

    with respx.mock(base_url="https://api.test") as mock:
        mock.post("/v1/messages").mock(return_value=httpx.Response(200, text=sse_body))

        events: list[StreamEvent] = []
        async for evt in adapter.stream(
            [{"role": "user", "content": "hi"}],
            tools=[],
            system="",
            model="claude-sonnet-4-6",
            max_tokens=8192,
            thinking={"type": "enabled", "budget_tokens": 4000},
        ):
            events.append(evt)

    thinking_events = [e for e in events if e.type == StreamEventType.THINKING]
    text_events = [e for e in events if e.type == StreamEventType.TEXT_DELTA]
    done_events = [e for e in events if e.type == StreamEventType.MESSAGE_DONE]

    assert len(thinking_events) == 1
    assert thinking_events[0].text == "I should consider..."
    assert len(text_events) == 1
    assert text_events[0].text == "Answer."
    assert len(done_events) == 1
    # thinking_tokens should be char_count // 4
    assert done_events[0].usage.thinking_tokens == len("I should consider...") // 4


@pytest.mark.asyncio
async def test_anthropic_adapter_stream_no_thinking_events_without_param():
    """No THINKING events are emitted when thinking param is None."""
    adapter = AnthropicAdapter(api_key="sk-test", base_url="https://api.test")

    sse_body = _make_sse_lines(
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Hello."},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 5},
        },
    )

    with respx.mock(base_url="https://api.test") as mock:
        mock.post("/v1/messages").mock(return_value=httpx.Response(200, text=sse_body))

        events: list[StreamEvent] = []
        async for evt in adapter.stream(
            [{"role": "user", "content": "hi"}],
            tools=[],
            system="",
            model="claude-sonnet-4-6",
            max_tokens=8192,
        ):
            events.append(evt)

    thinking_events = [e for e in events if e.type == StreamEventType.THINKING]
    assert len(thinking_events) == 0


# ---------------------------------------------------------------------------
# AnthropicAdapter — generate() with thinking blocks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anthropic_adapter_generate_with_thinking_blocks():
    adapter = AnthropicAdapter(api_key="sk-test", base_url="https://api.test")

    response_body = {
        "content": [
            {"type": "thinking", "thinking": "Let me reason about this carefully." * 10},
            {"type": "text", "text": "Here is my answer."},
        ],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 50, "output_tokens": 120},
    }

    with respx.mock(base_url="https://api.test") as mock:
        mock.post("/v1/messages").mock(return_value=httpx.Response(200, json=response_body))

        result = await adapter.generate(
            [{"role": "user", "content": "hard question"}],
            tools=[],
            system="",
            model="claude-sonnet-4-6",
            max_tokens=8192,
            thinking={"type": "enabled", "budget_tokens": 4000},
        )

    assert result.content == "Here is my answer."
    thinking_text = "Let me reason about this carefully." * 10
    assert result.reasoning == thinking_text
    assert result.usage.thinking_tokens == len(thinking_text) // 4
    assert result.usage.input_tokens == 50
    assert result.usage.output_tokens == 120


@pytest.mark.asyncio
async def test_anthropic_adapter_generate_without_thinking():
    adapter = AnthropicAdapter(api_key="sk-test", base_url="https://api.test")

    response_body = {
        "content": [{"type": "text", "text": "Simple answer."}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }

    with respx.mock(base_url="https://api.test") as mock:
        mock.post("/v1/messages").mock(return_value=httpx.Response(200, json=response_body))

        result = await adapter.generate(
            [{"role": "user", "content": "question"}],
            tools=[],
            system="",
            model="claude-sonnet-4-6",
            max_tokens=8192,
        )

    assert result.content == "Simple answer."
    assert result.usage.thinking_tokens == 0


# ---------------------------------------------------------------------------


class ThinkingCapableLLM(LLMPort):
    """Mock LLM that records whether thinking was passed."""

    def __init__(self) -> None:
        self.last_thinking: dict | None = None

    @property
    def supports_thinking(self) -> bool:
        return True

    async def generate(
        self,
        messages: list[dict],
        *,
        tools: list[dict],
        system,
        model: str,
        max_tokens: int,
        thinking: dict | None = None,
    ) -> LLMResponse:
        self.last_thinking = thinking
        return LLMResponse(
            content="ok",
            tool_calls=[],
            stop_reason=StopReason.END_TURN,
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        )

    async def stream(
        self,
        messages: list[dict],
        *,
        tools: list[dict],
        system,
        model: str,
        max_tokens: int,
        thinking: dict | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self.last_thinking = thinking
        yield StreamEvent(
            type=StreamEventType.MESSAGE_DONE,
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        )


class NonThinkingLLM(LLMPort):
    """Mock LLM that does not support thinking."""

    def __init__(self) -> None:
        self.last_thinking: dict | None = "NOT_SET"

    @property
    def supports_thinking(self) -> bool:
        return False

    async def generate(
        self,
        messages: list[dict],
        *,
        tools: list[dict],
        system,
        model: str,
        max_tokens: int,
        thinking: dict | None = None,
    ) -> LLMResponse:
        self.last_thinking = thinking
        return LLMResponse(
            content="ok",
            tool_calls=[],
            stop_reason=StopReason.END_TURN,
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        )

    async def stream(
        self,
        messages: list[dict],
        *,
        tools: list[dict],
        system,
        model: str,
        max_tokens: int,
        thinking: dict | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self.last_thinking = thinking
        yield StreamEvent(
            type=StreamEventType.MESSAGE_DONE,
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        )


class ThinkingRecordingLLM(LLMPort):
    """Records thinking params passed to stream() for test assertions."""

    def __init__(self, response: LLMResponse) -> None:
        self._response = response
        self.thinking_params: list[dict | None] = []

    @property
    def supports_thinking(self) -> bool:
        return True

    async def generate(
        self,
        messages: list[dict],
        *,
        tools: list[dict],
        system,
        model: str,
        max_tokens: int,
        thinking: dict | None = None,
    ) -> LLMResponse:
        self.thinking_params.append(thinking)
        return self._response

    async def stream(
        self,
        messages: list[dict],
        *,
        tools: list[dict],
        system,
        model: str,
        max_tokens: int,
        thinking: dict | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self.thinking_params.append(thinking)
        if self._response.content:
            yield StreamEvent(type=StreamEventType.TEXT_DELTA, text=self._response.content)
        yield StreamEvent(type=StreamEventType.MESSAGE_DONE, usage=self._response.usage)


def make_thinking_agent(
    budget_tokens: int = 4000,
    auto_trigger: bool = True,
    auto_trigger_on_retry: bool = True,
) -> tuple[RavnAgent, InMemoryChannel, ThinkingRecordingLLM]:
    et_config = ExtendedThinkingConfig(
        enabled=True,
        budget_tokens=budget_tokens,
        auto_trigger=auto_trigger,
        auto_trigger_on_retry=auto_trigger_on_retry,
    )
    response = make_text_response("Done.")
    llm = ThinkingRecordingLLM(response)
    ch = InMemoryChannel()
    from ravn.adapters.permission.allow_deny import AllowAllPermission

    agent = RavnAgent(
        llm=llm,
        tools=[],
        channel=ch,
        permission=AllowAllPermission(),
        system_prompt="test",
        model="m",
        max_tokens=8192,
        max_iterations=5,
        extended_thinking=et_config,
    )
    return agent, ch, llm


@pytest.mark.asyncio
async def test_agent_explicit_think_prefix_activates_thinking():
    agent, ch, llm = make_thinking_agent()
    await agent.run_turn("think: design a better system")
    assert llm.thinking_params[0] == {"type": "enabled", "budget_tokens": 4000}


@pytest.mark.asyncio
async def test_agent_explicit_think_flag_activates_thinking():
    agent, ch, llm = make_thinking_agent()
    await agent.run_turn("--think how do I fix this")
    assert llm.thinking_params[0] == {"type": "enabled", "budget_tokens": 4000}


@pytest.mark.asyncio
async def test_agent_no_thinking_without_config():
    """When extended_thinking is None, no thinking param is passed."""
    response = make_text_response("Done.")
    llm = ThinkingRecordingLLM(response)
    ch = InMemoryChannel()
    from ravn.adapters.permission.allow_deny import AllowAllPermission

    agent = RavnAgent(
        llm=llm,
        tools=[],
        channel=ch,
        permission=AllowAllPermission(),
        system_prompt="test",
        model="m",
        max_tokens=8192,
        max_iterations=5,
        extended_thinking=None,
    )
    await agent.run_turn("normal message")
    assert llm.thinking_params[0] is None


@pytest.mark.asyncio
async def test_agent_auto_trigger_applies_to_ordinary_input():
    agent, ch, llm = make_thinking_agent(auto_trigger=True)
    await agent.run_turn("read the file foo.py")
    assert llm.thinking_params[0] == {"type": "enabled", "budget_tokens": 4000}


@pytest.mark.asyncio
async def test_agent_no_auto_trigger_when_disabled():
    agent, ch, llm = make_thinking_agent(auto_trigger=False)
    await agent.run_turn("read the file foo.py")
    assert llm.thinking_params[0] is None


@pytest.mark.asyncio
async def test_agent_thinking_events_emitted_to_channel():
    """THINKING stream events are emitted as THOUGHT events with thinking=True."""

    class ThinkingStreamLLM(LLMPort):
        @property
        def supports_thinking(self) -> bool:
            return True

        async def generate(self, messages, *, tools, system, model, max_tokens, thinking=None):
            return make_text_response()

        async def stream(
            self,
            messages,
            *,
            tools,
            system,
            model,
            max_tokens,
            thinking=None,
        ) -> AsyncIterator[StreamEvent]:
            yield StreamEvent(type=StreamEventType.THINKING, text="reasoning step")
            yield StreamEvent(type=StreamEventType.TEXT_DELTA, text="answer")
            yield StreamEvent(
                type=StreamEventType.MESSAGE_DONE,
                usage=TokenUsage(input_tokens=5, output_tokens=10),
            )

    et = ExtendedThinkingConfig(enabled=True, budget_tokens=4000)
    llm = ThinkingStreamLLM()
    ch = InMemoryChannel()
    from ravn.adapters.permission.allow_deny import AllowAllPermission

    agent = RavnAgent(
        llm=llm,
        tools=[],
        channel=ch,
        permission=AllowAllPermission(),
        system_prompt="test",
        model="m",
        max_tokens=8192,
        max_iterations=5,
        extended_thinking=et,
    )
    await agent.run_turn("think: do something")

    thinking_events = [
        e for e in ch.events if e.type == RavnEventType.THOUGHT and e.payload.get("thinking")
    ]
    assert len(thinking_events) == 1
    assert thinking_events[0].payload["text"] == "reasoning step"
    assert agent._session.messages[-1].reasoning == "reasoning step"


@pytest.mark.asyncio
async def test_agent_disabled_extended_thinking_no_thinking_param():
    """When enabled=False, thinking param is never passed even with think: prefix."""
    et = ExtendedThinkingConfig(enabled=False)
    response = make_text_response("Done.")
    llm = ThinkingRecordingLLM(response)
    ch = InMemoryChannel()
    from ravn.adapters.permission.allow_deny import AllowAllPermission

    agent = RavnAgent(
        llm=llm,
        tools=[],
        channel=ch,
        permission=AllowAllPermission(),
        system_prompt="test",
        model="m",
        max_tokens=8192,
        max_iterations=5,
        extended_thinking=et,
    )
    await agent.run_turn("think: do something")
    assert llm.thinking_params[0] is None
