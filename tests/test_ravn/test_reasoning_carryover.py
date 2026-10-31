"""Opaque reasoning (``reasoning_details``) carried across a Ravn tool loop.

A provider that keeps its reasoning private (the ChatGPT Responses backend,
relayed by Bifröst's chat-completions endpoint) returns it as
``reasoning_details``. Ravn keeps it on the assistant ``Message`` and sends it
back with that message on the next step, so the model continues its own
reasoning. Providers that return none are unaffected.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx

from ravn.adapters.llm.anthropic import _for_api
from ravn.adapters.llm.openai import OpenAICompatibleAdapter
from ravn.agent import RavnAgent
from ravn.budget import TokenEstimator
from ravn.compression import ContextCompressor
from ravn.domain.checkpoint import Checkpoint, restore_session_from_checkpoint
from ravn.domain.exceptions import LLMError, PromptBudgetExceededError
from ravn.domain.models import (
    LLMResponse,
    Message,
    Session,
    StopReason,
    StreamEvent,
    StreamEventType,
    TokenUsage,
)
from ravn.ports.llm import LLMPort
from tests.ravn.fixtures.fakes import EchoTool
from tests.test_ravn.conftest import AllowAllPermission, InMemoryChannel

_BASE_URL = "https://bifrost.test"
_MODEL = "gpt-6-sol"


def _detail(data: str, index: int = 0) -> dict:
    return {
        "type": "reasoning.encrypted",
        "data": data,
        "format": "openai-responses-v1",
        "index": index,
    }


def _chunk(delta: dict, finish_reason: str | None = None) -> str:
    return json.dumps({"choices": [{"delta": delta, "finish_reason": finish_reason}]})


def _sse(*chunks: str) -> bytes:
    parts = [f"data: {c}\n\n" for c in chunks]
    parts.append(
        f"data: {json.dumps({'usage': {'prompt_tokens': 10, 'completion_tokens': 5}})}\n\n"
    )
    parts.append("data: [DONE]\n\n")
    return "".join(parts).encode()


def _tool_step(call_id: str, data: str, summary: str = "") -> httpx.Response:
    """A streamed step: reasoning (summary + opaque payload) then a tool call."""
    chunks = []
    if summary:
        chunks.append(_chunk({"reasoning_content": summary}))
    chunks.append(_chunk({"reasoning_details": [_detail(data)]}))
    chunks.append(
        _chunk(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": call_id,
                        "function": {"name": "echo", "arguments": '{"message": "hi"}'},
                    }
                ]
            },
            finish_reason="tool_calls",
        )
    )
    return httpx.Response(200, content=_sse(*chunks))


def _text_step(text: str) -> httpx.Response:
    return httpx.Response(200, content=_sse(_chunk({"content": text}, finish_reason="stop")))


def _assistant_messages(request: httpx.Request) -> list[dict]:
    return [m for m in json.loads(request.content)["messages"] if m["role"] == "assistant"]


def _agent(llm: LLMPort, **kwargs) -> RavnAgent:
    return RavnAgent(
        llm=llm,
        tools=[EchoTool()],
        channel=InMemoryChannel(),
        permission=AllowAllPermission(),
        system_prompt="You are a test assistant.",
        model=_MODEL,
        max_tokens=1024,
        max_iterations=6,
        **kwargs,
    )


class TestAdapterCapture:
    @respx.mock
    async def test_stream_emits_the_payload_as_events(self) -> None:
        adapter = OpenAICompatibleAdapter(base_url=_BASE_URL, model=_MODEL)
        respx.post(f"{_BASE_URL}/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                content=_sse(
                    _chunk({"reasoning_details": [_detail("ENC1", 0)]}),
                    _chunk({"reasoning_details": [_detail("ENC2", 1)]}),
                    _chunk({"content": "ok"}, finish_reason="stop"),
                ),
            )
        )

        events = [
            e
            async for e in adapter.stream(
                [{"role": "user", "content": "hi"}],
                tools=[],
                system="",
                model=_MODEL,
                max_tokens=10,
            )
        ]

        details = [e for e in events if e.type == StreamEventType.REASONING_DETAILS]
        assert [e.reasoning_details for e in details] == [
            [_detail("ENC1", 0)],
            [_detail("ENC2", 1)],
        ]

    @respx.mock
    async def test_generate_returns_the_payload_on_the_response(self) -> None:
        adapter = OpenAICompatibleAdapter(base_url=_BASE_URL, model=_MODEL)
        respx.post(f"{_BASE_URL}/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "ok",
                                "reasoning_content": "thought",
                                "reasoning_details": [_detail("ENC1")],
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                },
            )
        )

        response = await adapter.generate(
            [{"role": "user", "content": "hi"}], tools=[], system="", model=_MODEL, max_tokens=10
        )

        assert response.reasoning == "thought"
        assert response.reasoning_details == [_detail("ENC1")]

    @respx.mock
    async def test_a_provider_that_returns_none_yields_none(self) -> None:
        adapter = OpenAICompatibleAdapter(base_url=_BASE_URL, model=_MODEL)
        respx.post(f"{_BASE_URL}/v1/chat/completions").mock(return_value=_text_step("ok"))

        events = [
            e
            async for e in adapter.stream(
                [{"role": "user", "content": "hi"}],
                tools=[],
                system="",
                model=_MODEL,
                max_tokens=10,
            )
        ]

        assert all(e.type != StreamEventType.REASONING_DETAILS for e in events)

    @respx.mock
    @pytest.mark.parametrize("bad", ["a string", [1, 2], {"type": "x"}])
    async def test_malformed_payload_is_an_error_not_a_dropped_field(self, bad) -> None:
        adapter = OpenAICompatibleAdapter(base_url=_BASE_URL, model=_MODEL)
        respx.post(f"{_BASE_URL}/v1/chat/completions").mock(
            return_value=httpx.Response(200, content=_sse(_chunk({"reasoning_details": bad})))
        )

        with pytest.raises(LLMError, match="reasoning_details"):
            [
                e
                async for e in adapter.stream(
                    [{"role": "user", "content": "hi"}],
                    tools=[],
                    system="",
                    model=_MODEL,
                    max_tokens=10,
                )
            ]


class TestAdapterReplay:
    def _build(self, messages: list[dict]) -> list[dict]:
        adapter = OpenAICompatibleAdapter(base_url=_BASE_URL, model=_MODEL)
        request = adapter._build_request(
            messages, tools=[], system="", model=_MODEL, max_tokens=10, stream=True
        )
        return request["messages"]

    def test_tool_call_message_sends_the_payload_and_the_summary_back(self) -> None:
        sent = self._build(
            [
                Message(
                    role="assistant",
                    content=[{"type": "tool_use", "id": "c1", "name": "echo", "input": {}}],
                    reasoning="need the echo",
                    reasoning_details=[_detail("ENC1")],
                ).to_api_dict()
            ]
        )

        assert sent[0]["reasoning_content"] == "need the echo"
        assert sent[0]["reasoning_details"] == [_detail("ENC1")]
        assert sent[0]["tool_calls"][0]["id"] == "c1"

    def test_text_message_sends_the_payload_back_too(self) -> None:
        sent = self._build(
            [
                Message(
                    role="assistant",
                    content="done",
                    reasoning="summary",
                    reasoning_details=[_detail("ENC1")],
                ).to_api_dict()
            ]
        )

        assert sent[0]["reasoning_content"] == "summary"
        assert sent[0]["reasoning_details"] == [_detail("ENC1")]

    @pytest.mark.parametrize(
        "content",
        ["done", [{"type": "tool_use", "id": "c1", "name": "echo", "input": {}}]],
    )
    def test_nothing_is_sent_when_the_provider_returned_nothing(self, content) -> None:
        sent = self._build([Message(role="assistant", content=content).to_api_dict()])

        assert "reasoning_details" not in sent[0]
        assert "reasoning_content" not in sent[0]

    def test_anthropic_adapter_does_not_forward_the_openai_payload(self) -> None:
        [cleaned] = _for_api(
            [
                Message(
                    role="assistant",
                    content="done",
                    reasoning="r",
                    reasoning_details=[_detail("ENC1")],
                ).to_api_dict()
            ]
        )

        assert cleaned == {"role": "assistant", "content": "done"}


class TestMessageModel:
    def test_api_dict_round_trip(self) -> None:
        message = Message(
            role="assistant",
            content="x",
            reasoning="r",
            reasoning_details=[_detail("ENC1")],
        )

        assert Message.from_api_dict(message.to_api_dict()) == message

    def test_plain_message_has_no_reasoning_keys(self) -> None:
        assert Message(role="user", content="q").to_api_dict() == {"role": "user", "content": "q"}

    def test_checkpoint_restore_keeps_the_payload(self) -> None:
        saved = Message(
            role="assistant", content="x", reasoning_details=[_detail("ENC1")]
        ).to_api_dict()
        checkpoint = Checkpoint(
            task_id="t",
            user_input="",
            messages=[saved],
            todos=[],
            iteration_budget_consumed=0,
            iteration_budget_total=1,
            last_tool_call=None,
            last_tool_result=None,
            partial_response="",
            interrupted_by=None,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        session = Session()

        restore_session_from_checkpoint(session, checkpoint)

        assert session.messages[0].reasoning_details == [_detail("ENC1")]


class TestBudgetAccounting:
    def test_estimator_counts_the_payload(self) -> None:
        bare = Message(role="assistant", content="x")
        with_payload = Message(
            role="assistant", content="x", reasoning_details=[_detail("A" * 4_000)]
        )

        assert (
            TokenEstimator.rough_messages([with_payload]) - TokenEstimator.rough_messages([bare])
            >= 1_000
        )

    def test_api_message_estimate_counts_the_payload(self) -> None:
        message = Message(
            role="assistant", content="x", reasoning_details=[_detail("A" * 4_000)]
        ).to_api_dict()

        assert TokenEstimator.rough_api_messages([message]) >= 1_000

    def test_prompt_section_estimate_for_history_includes_the_payload(self) -> None:
        agent = _agent(MagicMock(spec=LLMPort))
        bare = [Message(role="assistant", content="x")]
        heavy = [Message(role="assistant", content="x", reasoning_details=[_detail("A" * 8_000)])]

        light = agent._prompt_section_estimates("sys", bare)["history"]
        loaded = agent._prompt_section_estimates("sys", heavy)["history"]

        assert loaded - light >= 2_000

    async def test_a_payload_heavy_history_cannot_slip_past_the_prompt_budget(self) -> None:
        # The text alone is tiny; only the payload carried from the earlier step
        # pushes the next request past max_prompt_tokens.
        llm = MagicMock(spec=LLMPort)
        agent = _agent(llm, max_prompt_tokens=2_000)
        agent._session.messages.append(
            Message(
                role="assistant",
                content="earlier step",
                reasoning_details=[_detail("A" * 40_000)],
            )
        )

        with pytest.raises(PromptBudgetExceededError) as excinfo:
            await agent.run_turn("continue")

        assert excinfo.value.sections["history"] > excinfo.value.budget_tokens


class TestCompaction:
    async def test_compacted_messages_take_their_payload_with_them(self) -> None:
        llm = MagicMock(spec=LLMPort)
        llm.generate = AsyncMock(
            return_value=LLMResponse(
                content="## Active task\nx",
                tool_calls=[],
                stop_reason=StopReason.END_TURN,
                usage=TokenUsage(input_tokens=1, output_tokens=1),
            )
        )
        compressor = ContextCompressor(
            llm, model=_MODEL, protect_first=1, protect_last=1, compression_threshold=0.5
        )
        messages = [Message(role="user", content="start")]
        for step in range(4):
            messages.append(
                Message(
                    role="assistant",
                    content=f"step {step}",
                    reasoning_details=[_detail(f"ENC{step}" * 2_000)],
                )
            )
        messages.append(Message(role="user", content="latest"))

        compacted, result = await compressor.maybe_compress(messages, prompt_budget_tokens=4_000)

        assert result.was_compressed
        assert [m for m in compacted if m.reasoning_details] == []
        assert TokenEstimator.rough_messages(compacted) < 1_000


class TestToolLoop:
    """The multi-step behaviour that motivates all of the above."""

    @respx.mock
    async def test_each_step_sends_the_earlier_steps_reasoning_back(self) -> None:
        route = respx.post(f"{_BASE_URL}/v1/chat/completions").mock(
            side_effect=[
                _tool_step("call-1", "ENC-STEP-1", summary="first look"),
                _tool_step("call-2", "ENC-STEP-2"),
                _text_step("all done"),
            ]
        )
        agent = _agent(OpenAICompatibleAdapter(base_url=_BASE_URL, model=_MODEL))

        result = await agent.run_turn("do the thing")

        assert result.response == "all done"
        first, second, third = (call.request for call in route.calls)
        # Step 1 had nothing to continue.
        assert _assistant_messages(first) == []
        # Step 2 continues step 1's reasoning.
        [step1] = _assistant_messages(second)
        assert step1["reasoning_details"] == [_detail("ENC-STEP-1")]
        assert step1["reasoning_content"] == "first look"
        assert step1["tool_calls"][0]["id"] == "call-1"
        # Step 3 carries both, each on its own message, in order.
        carried = _assistant_messages(third)
        assert [m["reasoning_details"] for m in carried] == [
            [_detail("ENC-STEP-1")],
            [_detail("ENC-STEP-2")],
        ]
        assert [m["tool_calls"][0]["id"] for m in carried] == ["call-1", "call-2"]

    @respx.mock
    async def test_the_final_answers_reasoning_stays_in_history_for_the_next_turn(self) -> None:
        route = respx.post(f"{_BASE_URL}/v1/chat/completions").mock(
            side_effect=[
                httpx.Response(
                    200,
                    content=_sse(
                        _chunk({"reasoning_details": [_detail("ENC-ANSWER")]}),
                        _chunk({"content": "42"}, finish_reason="stop"),
                    ),
                ),
                _text_step("second"),
            ]
        )
        agent = _agent(OpenAICompatibleAdapter(base_url=_BASE_URL, model=_MODEL))

        await agent.run_turn("what is it")
        await agent.run_turn("and again")

        [carried] = _assistant_messages(route.calls[1].request)
        assert carried["content"] == "42"
        assert carried["reasoning_details"] == [_detail("ENC-ANSWER")]

    @respx.mock
    async def test_a_provider_without_reasoning_details_gets_the_same_requests_as_before(
        self,
    ) -> None:
        route = respx.post(f"{_BASE_URL}/v1/chat/completions").mock(
            side_effect=[
                httpx.Response(
                    200,
                    content=_sse(
                        _chunk(
                            {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call-1",
                                        "function": {"name": "echo", "arguments": "{}"},
                                    }
                                ]
                            },
                            finish_reason="tool_calls",
                        )
                    ),
                ),
                _text_step("done"),
            ]
        )
        agent = _agent(OpenAICompatibleAdapter(base_url=_BASE_URL, model=_MODEL))

        await agent.run_turn("go")

        [carried] = _assistant_messages(route.calls[1].request)
        assert "reasoning_details" not in carried
        assert "reasoning_content" not in carried

    async def test_stream_events_accumulate_into_the_assistant_message(self) -> None:
        async def _stream(*args, **kwargs):
            yield StreamEvent(
                type=StreamEventType.REASONING_DETAILS, reasoning_details=[_detail("A", 0)]
            )
            yield StreamEvent(
                type=StreamEventType.REASONING_DETAILS, reasoning_details=[_detail("B", 1)]
            )
            yield StreamEvent(type=StreamEventType.TEXT_DELTA, text="ok")
            yield StreamEvent(
                type=StreamEventType.MESSAGE_DONE,
                usage=TokenUsage(input_tokens=1, output_tokens=1),
            )

        llm = AsyncMock(spec=LLMPort)
        llm.stream = _stream
        agent = _agent(llm)

        await agent.run_turn("hi")

        assert agent._session.messages[-1].reasoning_details == [_detail("A", 0), _detail("B", 1)]
