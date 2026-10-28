"""A model call survives the gateway restarting under it.

The incident: a Bifröst pod stopped during a rollout, an in-flight streamed
answer ended with ``RemoteProtocolError: peer closed connection without sending
complete message body``, and the resident's whole task failed. These tests cut
real HTTP streams (a partial body, then the error httpx raises) through each of
the LLM adapters and assert the request is sent again, the partial answer is
discarded rather than duplicated, a tool call runs once, every re-issue is
logged and counted, and running out of the window is loud and names its knob.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from ravn.adapters.llm import transient
from ravn.adapters.llm.anthropic import AnthropicAdapter
from ravn.adapters.llm.bifrost import BifrostAdapter
from ravn.adapters.llm.openai import OpenAICompatibleAdapter
from ravn.adapters.llm.openai_responses import OpenAIResponsesAdapter
from ravn.adapters.llm.transient import (
    GATEWAY_OUTAGE_KNOB,
    RETRYABLE_STATUS_CODES,
    GatewayOutage,
    StreamIncompleteError,
    TransientStatusError,
    post_within_window,
    resumable_stream,
)
from ravn.cap_telemetry import cap_hit_counts, retry_counts
from ravn.domain.exceptions import LLMError
from ravn.domain.models import (
    LLMResponse,
    StopReason,
    StreamEvent,
    StreamEventType,
    TokenUsage,
    ToolCall,
)
from ravn.ports.llm import LLMPort
from tests.ravn.conftest import make_agent
from tests.ravn.fixtures.fakes import EchoTool

# ---------------------------------------------------------------------------
# Wire fixtures: one partial body and one complete body per protocol
# ---------------------------------------------------------------------------


class _Cut(httpx.AsyncByteStream):
    """A response body that delivers *body* and then fails the way a killed pod does."""

    def __init__(self, body: bytes, error: Exception) -> None:
        self._body = body
        self._error = error

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self._body
        raise self._error


def _cut(body: bytes, error: Exception | None = None) -> httpx.Response:
    error = error or httpx.RemoteProtocolError(
        "peer closed connection without sending complete message body (incomplete chunked read)"
    )
    return httpx.Response(
        200, stream=_Cut(body, error), headers={"content-type": "text/event-stream"}
    )


def _ok(body: bytes) -> httpx.Response:
    return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})


def _data(*events: dict) -> bytes:
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events).encode()


_TOOL_INPUT = {"message": "hi"}


def _anthropic_body(text: list[str], *, finish: bool, stop_reason: str = "tool_use") -> bytes:
    events: list[dict] = [
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}
    ]
    events += [
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": chunk},
        }
        for chunk in text
    ]
    events += [
        {"type": "content_block_stop", "index": 0},
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {"type": "tool_use", "id": "t1", "name": "echo"},
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": json.dumps(_TOOL_INPUT)},
        },
        {"type": "content_block_stop", "index": 1},
    ]
    if finish:
        events.append(
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason},
                "usage": {"input_tokens": 7, "output_tokens": 3},
            }
        )
        events.append({"type": "message_stop"})
    return _data(*events)


def _openai_body(text: list[str], *, finish: bool) -> bytes:
    def chunk(delta: dict, finish_reason: str | None = None) -> dict:
        return {"choices": [{"delta": delta, "finish_reason": finish_reason}]}

    events = [chunk({"content": piece}) for piece in text]
    events.append(
        chunk(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "t1",
                        "function": {"name": "echo", "arguments": json.dumps(_TOOL_INPUT)},
                    }
                ]
            },
            "tool_calls",
        )
    )
    body = _data(*events)
    if finish:
        body += _data({"usage": {"prompt_tokens": 7, "completion_tokens": 3}})
        body += b"data: [DONE]\n\n"
    return body


def _responses_body(text: list[str], *, finish: bool) -> bytes:
    events: list[dict] = [{"type": "response.output_text.delta", "delta": piece} for piece in text]
    events.append(
        {
            "type": "response.output_item.done",
            "item": {
                "type": "function_call",
                "call_id": "t1",
                "name": "echo",
                "arguments": json.dumps(_TOOL_INPUT),
            },
        }
    )
    if finish:
        events.append(
            {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "usage": {"input_tokens": 7, "output_tokens": 3},
                },
            }
        )
    return _data(*events)


@dataclass(frozen=True)
class Wire:
    """What differs per adapter: how to build it, where it posts, what it streams."""

    name: str
    build: Callable[..., LLMPort]
    url: str
    body: Callable[..., bytes]
    poison: bytes  # a deterministic failure delivered inside a 200 stream
    incomplete: bytes  # a stream that ends cleanly without its terminator
    model: str

    @property
    def path(self) -> str:
        return f"llm.{self.name}"


def _wire_adapters() -> list[Wire]:
    return [
        Wire(
            name="anthropic",
            build=lambda **kw: AnthropicAdapter(api_key="k", base_url="http://gw", **kw),
            url="http://gw/v1/messages",
            body=_anthropic_body,
            # A refusal is an answer the agent cannot act on, not an outage.
            poison=_data(
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "refusal"},
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
            ),
            incomplete=_anthropic_body(["Hel"], finish=False),
            model="claude-sonnet-4-6",
        ),
        Wire(
            name="anthropic",
            build=lambda **kw: BifrostAdapter(base_url="http://gw", **kw),
            url="http://gw/v1/messages",
            body=_anthropic_body,
            poison=_data(
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "refusal"},
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
            ),
            incomplete=_anthropic_body(["Hel"], finish=False),
            model="claude-sonnet-4-6",
        ),
        Wire(
            name="openai",
            build=lambda **kw: OpenAICompatibleAdapter(api_key="k", base_url="http://gw", **kw),
            url="http://gw/v1/chat/completions",
            body=_openai_body,
            poison=_data({"error": {"message": "context length exceeded"}}),
            incomplete=_data({"choices": [{"delta": {"content": "Hel"}, "finish_reason": None}]}),
            model="gpt-4o",
        ),
        Wire(
            name="openai_responses",
            build=lambda **kw: OpenAIResponsesAdapter(base_url="http://gw", **kw),
            url="http://gw/v1/responses",
            body=_responses_body,
            poison=_data({"type": "response.failed", "response": {"error": {"message": "no"}}}),
            incomplete=_responses_body(["Hel"], finish=False),
            model="gpt-6-sol",
        ),
    ]


WIRES = _wire_adapters()
WIRE_IDS = ["anthropic", "bifrost", "openai", "openai_responses"]


def _wire_param():
    return pytest.mark.parametrize("wire", WIRES, ids=WIRE_IDS)


_FAST = dict(retry_base_delay=0.001, gateway_outage_window_seconds=5.0)


async def _stream(adapter: LLMPort, wire: Wire) -> list[StreamEvent]:
    return [
        event
        async for event in adapter.stream(
            [{"role": "user", "content": "go"}],
            tools=[],
            system="s",
            model=wire.model,
            max_tokens=100,
        )
    ]


def _answer(events: list[StreamEvent]) -> tuple[str, list[ToolCall]]:
    """What a consumer ends up with: everything after the last restart."""
    start = 0
    for index, event in enumerate(events):
        if event.type == StreamEventType.STREAM_RESTART:
            start = index + 1
    kept = events[start:]
    text = "".join(e.text or "" for e in kept if e.type == StreamEventType.TEXT_DELTA)
    calls = [e.tool_call for e in kept if e.type == StreamEventType.TOOL_CALL and e.tool_call]
    return text, calls


# ---------------------------------------------------------------------------
# The incident: the stream is cut after part of the answer has been delivered
# ---------------------------------------------------------------------------


@_wire_param()
class TestStreamCutMidAnswer:
    @respx.mock
    async def test_the_request_is_reissued_and_the_answer_is_not_duplicated(self, wire) -> None:
        route = respx.post(wire.url).mock(
            side_effect=[
                _cut(wire.body(["Hel"], finish=False)),
                _ok(wire.body(["Hello ", "world"], finish=True)),
            ]
        )

        events = await _stream(wire.build(**_FAST), wire)

        assert route.call_count == 2
        # The two attempts carry the same request.
        assert route.calls[0].request.content == route.calls[1].request.content
        types = [e.type for e in events]
        assert types.count(StreamEventType.STREAM_RESTART) == 1
        text, calls = _answer(events)
        assert text == "Hello world"
        assert [(c.id, c.name, c.input) for c in calls] == [("t1", "echo", _TOOL_INPUT)]
        # The consumer was told to drop the first attempt's tool call.
        before_restart = events[: types.index(StreamEventType.STREAM_RESTART)]
        assert [e.type for e in before_restart].count(StreamEventType.TOOL_CALL) == 1

    @respx.mock
    async def test_each_reissue_is_logged_with_attempt_elapsed_and_cause_and_counted(
        self, wire, caplog
    ) -> None:
        respx.post(wire.url).mock(
            side_effect=[
                _cut(wire.body(["Hel"], finish=False)),
                _ok(wire.body(["done"], finish=True)),
            ]
        )

        with caplog.at_level(logging.WARNING, logger="ravn.cap_telemetry"):
            await _stream(wire.build(**_FAST), wire)

        assert retry_counts() == {wire.path: 1}
        message = caplog.records[0].getMessage()
        assert caplog.records[0].levelno == logging.WARNING
        assert wire.path in message
        assert "RemoteProtocolError" in message
        assert "incomplete chunked read" in message
        assert "retry 1," in message
        assert "0.0s into the 5s llm.gateway_outage_window_seconds window" in message
        assert cap_hit_counts() == {}

    @respx.mock
    async def test_no_restart_is_signalled_when_nothing_had_been_delivered(self, wire) -> None:
        respx.post(wire.url).mock(
            side_effect=[_cut(b""), _ok(wire.body(["done"], finish=True))],
        )

        events = await _stream(wire.build(**_FAST), wire)

        assert StreamEventType.STREAM_RESTART not in [e.type for e in events]
        assert _answer(events)[0] == "done"

    @respx.mock
    @pytest.mark.parametrize(
        "error",
        [
            httpx.ReadError("connection reset by peer"),
            httpx.RemoteProtocolError("incomplete chunked read"),
            httpx.ReadTimeout("read timed out"),
        ],
        ids=["ReadError", "RemoteProtocolError", "ReadTimeout"],
    )
    async def test_every_transport_failure_mid_stream_is_resumed(self, wire, error) -> None:
        route = respx.post(wire.url).mock(
            side_effect=[
                _cut(wire.body(["Hel"], finish=False), error),
                _ok(wire.body(["fine"], finish=True)),
            ]
        )

        events = await _stream(wire.build(**_FAST), wire)

        assert route.call_count == 2
        assert _answer(events)[0] == "fine"

    @respx.mock
    @pytest.mark.parametrize(
        "unavailable",
        [
            httpx.ConnectError("connection refused"),
            httpx.Response(502, text="bad gateway"),
            httpx.Response(503, text="unavailable"),
            httpx.Response(504, text="gateway timeout"),
        ],
        ids=["ConnectError", "502", "503", "504"],
    )
    async def test_a_gateway_that_is_still_down_when_reissued_keeps_being_waited_on(
        self, wire, unavailable
    ) -> None:
        # The replacement pod is not ready yet: the re-issue meets a refused
        # connection or a 5xx, and the window (not a count) decides to wait on.
        route = respx.post(wire.url).mock(
            side_effect=[
                _cut(wire.body(["Hel"], finish=False)),
                unavailable,
                unavailable,
                unavailable,
                unavailable,
                _ok(wire.body(["back"], finish=True)),
            ]
        )

        events = await _stream(wire.build(**_FAST), wire)

        assert route.call_count == 6
        assert _answer(events)[0] == "back"
        assert retry_counts() == {wire.path: 5}

    @respx.mock
    async def test_running_out_of_the_window_raises_naming_the_knob_and_is_counted(
        self, wire, caplog
    ) -> None:
        route = respx.post(wire.url).mock(
            side_effect=lambda request: _cut(wire.body(["Hel"], finish=False))
        )
        adapter = wire.build(retry_base_delay=0.01, gateway_outage_window_seconds=0.05)

        with caplog.at_level(logging.WARNING), pytest.raises(LLMError) as raised:
            await _stream(adapter, wire)

        message = str(raised.value)
        assert GATEWAY_OUTAGE_KNOB in message
        assert "0.05s" in message
        assert "raise llm.gateway_outage_window_seconds" in message
        assert "RemoteProtocolError" in message
        assert isinstance(raised.value.__cause__, httpx.RemoteProtocolError)
        assert cap_hit_counts() == {GATEWAY_OUTAGE_KNOB: 1}
        assert retry_counts()[wire.path] >= 1
        assert route.call_count == retry_counts()[wire.path] + 1

    @respx.mock
    async def test_a_zero_window_disables_reissuing_and_says_so(self, wire) -> None:
        route = respx.post(wire.url).mock(
            side_effect=[_cut(wire.body(["Hel"], finish=False)), _ok(b"")]
        )

        with pytest.raises(LLMError, match="disables retrying it") as raised:
            await _stream(wire.build(retry_base_delay=0.001, gateway_outage_window_seconds=0), wire)

        assert GATEWAY_OUTAGE_KNOB in str(raised.value)
        assert route.call_count == 1
        assert cap_hit_counts() == {GATEWAY_OUTAGE_KNOB: 1}
        assert retry_counts() == {}

    @respx.mock
    async def test_a_status_exhausting_the_window_is_carried_on_the_error(self, wire) -> None:
        calls = {"n": 0}

        def respond(request):
            calls["n"] += 1
            if calls["n"] == 1:
                return _cut(wire.body(["Hel"], finish=False))
            return httpx.Response(503, text="down")

        respx.post(wire.url).mock(side_effect=respond)
        adapter = wire.build(retry_base_delay=0.01, gateway_outage_window_seconds=0.05)

        with pytest.raises(LLMError) as raised:
            await _stream(adapter, wire)

        assert raised.value.status_code == 503
        assert "HTTP 503" in str(raised.value)


@_wire_param()
class TestDeterministicFailuresAreNeverRetried:
    @respx.mock
    async def test_a_failure_the_stream_reports_is_raised_once(self, wire) -> None:
        route = respx.post(wire.url).mock(side_effect=lambda request: _ok(wire.poison))

        with pytest.raises(LLMError):
            await _stream(wire.build(**_FAST), wire)

        assert route.call_count == 1
        assert retry_counts() == {}

    @respx.mock
    async def test_a_client_error_when_reissuing_is_raised_not_waited_out(self, wire) -> None:
        route = respx.post(wire.url).mock(
            side_effect=[
                _cut(wire.body(["Hel"], finish=False)),
                httpx.Response(400, text="bad request"),
            ]
        )

        with pytest.raises(LLMError) as raised:
            await _stream(wire.build(**_FAST), wire)

        assert raised.value.status_code == 400
        assert route.call_count == 2
        assert retry_counts() == {wire.path: 1}
        assert cap_hit_counts() == {}

    @respx.mock
    async def test_a_client_error_on_the_first_request_is_raised_once(self, wire) -> None:
        route = respx.post(wire.url).mock(return_value=httpx.Response(401, text="no"))

        with pytest.raises(LLMError) as raised:
            await _stream(wire.build(**_FAST), wire)

        assert raised.value.status_code == 401
        assert route.call_count == 1

    @respx.mock
    async def test_a_complete_stream_is_not_reissued(self, wire) -> None:
        route = respx.post(wire.url).mock(
            return_value=_ok(wire.body(["whole answer"], finish=True))
        )

        events = await _stream(wire.build(**_FAST), wire)

        assert route.call_count == 1
        assert StreamEventType.STREAM_RESTART not in [e.type for e in events]


class TestTruncatedAnswerIsAnAnswerNotAnOutage:
    @respx.mock
    async def test_max_tokens_is_reported_to_the_caller_and_not_resent(self) -> None:
        wire = WIRES[0]
        route = respx.post(wire.url).mock(
            return_value=_ok(wire.body(["cut"], finish=True, stop_reason="max_tokens"))
        )

        events = await _stream(wire.build(**_FAST), wire)

        assert route.call_count == 1
        done = [e for e in events if e.type == StreamEventType.MESSAGE_DONE]
        assert done[-1].stop_reason == StopReason.MAX_TOKENS


# ---------------------------------------------------------------------------
# The connection phase is under the same bound: a call made while the gateway is
# down rides out the rollout instead of failing after three quick attempts
# ---------------------------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(transient, "time", SimpleNamespace(monotonic=fake.monotonic))
    monkeypatch.setattr(transient, "asyncio", SimpleNamespace(sleep=fake.sleep))
    return fake


# The old connection-phase retry gave up after 3 retries: 1s + 2s + 4s of waiting.
_OLD_CONNECTION_RETRY_SECONDS = 7.0


@_wire_param()
class TestConnectionPhaseOutage:
    @respx.mock
    @pytest.mark.parametrize(
        "unavailable",
        [httpx.ConnectError("connection refused"), httpx.Response(503, text="unavailable")],
        ids=["ConnectError", "503"],
    )
    async def test_an_outage_longer_than_the_old_retries_recovers_within_the_window(
        self, wire, unavailable, clock, caplog
    ) -> None:
        # Six failures: 1+2+4+8+10+10 = 35s of waiting, five times what three retries covered.
        route = respx.post(wire.url).mock(
            side_effect=[unavailable] * 6 + [_ok(wire.body(["back"], finish=True))]
        )
        adapter = wire.build(gateway_outage_window_seconds=120.0, retry_base_delay=1.0)

        with caplog.at_level(logging.WARNING, logger="ravn.cap_telemetry"):
            events = await _stream(adapter, wire)

        assert route.call_count == 7
        assert _answer(events)[0] == "back"
        assert clock.slept == [1.0, 2.0, 4.0, 8.0, 10.0, 10.0]
        assert sum(clock.slept) > _OLD_CONNECTION_RETRY_SECONDS
        assert retry_counts() == {wire.path: 6}
        assert cap_hit_counts() == {}
        first = caplog.records[0].getMessage()
        assert "retry 1" in first
        assert "0.0s into the 120s llm.gateway_outage_window_seconds window" in first

    @respx.mock
    async def test_the_outage_window_is_spent_by_a_gateway_that_stays_down(
        self, wire, clock
    ) -> None:
        route = respx.post(wire.url).mock(side_effect=httpx.ConnectError("connection refused"))
        adapter = wire.build(gateway_outage_window_seconds=30.0, retry_base_delay=1.0)

        with pytest.raises(LLMError) as raised:
            await _stream(adapter, wire)

        message = str(raised.value)
        assert "llm.gateway_outage_window_seconds=30s" in message
        assert "raise llm.gateway_outage_window_seconds".lower() in message.lower()
        assert "ConnectError" in message
        assert f"endpoint={wire.url}" in message
        assert isinstance(raised.value.__cause__, httpx.ConnectError)
        assert cap_hit_counts() == {GATEWAY_OUTAGE_KNOB: 1}
        assert clock.slept == [1.0, 2.0, 4.0, 8.0, 10.0, 5.0]
        assert route.call_count == 7
        assert "llm.max_retries" not in message

    @respx.mock
    async def test_a_status_that_outlasts_the_window_is_carried_on_the_error(
        self, wire, clock
    ) -> None:
        respx.post(wire.url).mock(return_value=httpx.Response(504, text="gateway timeout"))

        with pytest.raises(LLMError) as raised:
            await _stream(wire.build(gateway_outage_window_seconds=5.0), wire)

        assert raised.value.status_code == 504
        assert "status=504" in str(raised.value)

    @respx.mock
    async def test_a_client_error_is_raised_at_once_not_waited_out(self, wire, clock) -> None:
        route = respx.post(wire.url).mock(return_value=httpx.Response(401, text="no"))

        with pytest.raises(LLMError) as raised:
            await _stream(wire.build(), wire)

        assert raised.value.status_code == 401
        assert route.call_count == 1
        assert clock.slept == []


class TestNonStreamingCallsRideOutTheOutageToo:
    _OK = httpx.Response(
        200,
        json={
            "content": [{"type": "text", "text": "ok"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        },
    )

    @respx.mock
    async def test_anthropic_generate_recovers_after_a_long_outage(self, clock) -> None:
        adapter = AnthropicAdapter(api_key="k", base_url="http://gw", retry_base_delay=1.0)
        route = respx.post("http://gw/v1/messages").mock(
            side_effect=[httpx.ConnectError("refused")] * 3
            + [httpx.Response(503, text="down")] * 3
            + [self._OK]
        )

        result = await adapter.generate([], tools=[], system="", model="m", max_tokens=10)

        assert result.content == "ok"
        assert route.call_count == 7
        assert sum(clock.slept) == 1 + 2 + 4 + 8 + 10 + 10
        assert retry_counts() == {"llm.anthropic": 6}

    @respx.mock
    async def test_openai_generate_recovers_after_a_long_outage(self, clock) -> None:
        adapter = OpenAICompatibleAdapter(api_key="k", base_url="http://gw", retry_base_delay=1.0)
        route = respx.post("http://gw/v1/chat/completions").mock(
            side_effect=[httpx.Response(502, text="bad gateway")] * 5
            + [
                httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "message": {"role": "assistant", "content": "ok"},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                    },
                )
            ]
        )

        result = await adapter.generate([], tools=[], system="", model="m", max_tokens=10)

        assert result.content == "ok"
        assert route.call_count == 6
        assert sum(clock.slept) == 1 + 2 + 4 + 8 + 10

    @respx.mock
    async def test_generate_exhaustion_names_the_window_and_chains_the_cause(self) -> None:
        adapter = AnthropicAdapter(
            api_key="k",
            base_url="http://gw",
            gateway_outage_window_seconds=0.05,
            retry_base_delay=0.01,
        )
        respx.post("http://gw/v1/messages").mock(side_effect=httpx.ConnectError("refused"))

        with pytest.raises(LLMError, match="llm.gateway_outage_window_seconds") as raised:
            await adapter.generate([], tools=[], system="", model="m", max_tokens=10)

        assert isinstance(raised.value.__cause__, httpx.ConnectError)
        assert retry_counts()["llm.anthropic"] >= 1
        assert cap_hit_counts() == {GATEWAY_OUTAGE_KNOB: 1}


# ---------------------------------------------------------------------------
# A stream that ends without its terminator is a cut answer, not an answer
# ---------------------------------------------------------------------------


@_wire_param()
class TestIncompleteEndIsResumed:
    @respx.mock
    async def test_a_clean_end_without_the_terminator_is_sent_again(self, wire) -> None:
        route = respx.post(wire.url).mock(
            side_effect=[_ok(wire.incomplete), _ok(wire.body(["Hello ", "world"], finish=True))]
        )

        events = await _stream(wire.build(**_FAST), wire)

        assert route.call_count == 2
        assert [e.type for e in events].count(StreamEventType.STREAM_RESTART) == 1
        text, calls = _answer(events)
        assert text == "Hello world"
        assert [c.id for c in calls] == ["t1"]
        assert retry_counts() == {wire.path: 1}

    @respx.mock
    async def test_an_answer_that_never_completes_exhausts_the_window_loudly(self, wire) -> None:
        route = respx.post(wire.url).mock(side_effect=lambda request: _ok(wire.incomplete))
        adapter = wire.build(retry_base_delay=0.01, gateway_outage_window_seconds=0.05)

        with pytest.raises(LLMError) as raised:
            await _stream(adapter, wire)

        assert GATEWAY_OUTAGE_KNOB in str(raised.value)
        assert "StreamIncompleteError" in str(raised.value)
        assert isinstance(raised.value.__cause__, StreamIncompleteError)
        assert route.call_count > 1
        assert cap_hit_counts() == {GATEWAY_OUTAGE_KNOB: 1}


class TestAnthropicStreamErrors:
    @respx.mock
    @pytest.mark.parametrize("kind", ["overloaded_error", "api_error"])
    async def test_an_overloaded_upstream_inside_the_stream_is_sent_again(self, kind) -> None:
        wire = WIRES[0]
        error = _data({"type": "error", "error": {"type": kind, "message": "try later"}})
        route = respx.post(wire.url).mock(
            side_effect=[_ok(error), _ok(wire.body(["fine"], finish=True))]
        )

        events = await _stream(wire.build(**_FAST), wire)

        assert route.call_count == 2
        assert _answer(events)[0] == "fine"

    @respx.mock
    async def test_a_request_the_upstream_rejects_is_raised_not_resent(self) -> None:
        wire = WIRES[0]
        error = _data(
            {"type": "error", "error": {"type": "invalid_request_error", "message": "bad tool"}}
        )
        route = respx.post(wire.url).mock(return_value=_ok(error))

        with pytest.raises(LLMError, match="invalid_request_error: bad tool"):
            await _stream(wire.build(**_FAST), wire)

        assert route.call_count == 1
        assert retry_counts() == {}

    @respx.mock
    async def test_message_stop_alone_completes_a_stream(self) -> None:
        wire = WIRES[0]
        route = respx.post(wire.url).mock(return_value=_ok(_data({"type": "message_stop"})))

        events = await _stream(wire.build(**_FAST), wire)

        assert events == []
        assert route.call_count == 1


# ---------------------------------------------------------------------------
# resumable_stream: the window arithmetic, on a fake clock
# ---------------------------------------------------------------------------


def _text(piece: str) -> StreamEvent:
    return StreamEvent(type=StreamEventType.TEXT_DELTA, text=piece)


class _Responses:
    """Open/read callables scripted per attempt: a list of events then an optional error."""

    def __init__(self, *attempts: tuple[list[StreamEvent], BaseException | None]) -> None:
        self._attempts = list(attempts)
        self.opened: list[GatewayOutage] = []
        self.closed = 0
        self._current = -1

    async def open(self, outage: GatewayOutage) -> httpx.Response:
        self.opened.append(outage)
        self._current += 1
        response = AsyncMock(spec=httpx.Response)

        async def aclose() -> None:
            self.closed += 1

        response.aclose = aclose
        response.attempt = self._current
        return response

    async def read(self, response) -> AsyncIterator[StreamEvent]:
        events, error = self._attempts[response.attempt]
        for event in events:
            yield event
        if error is not None:
            raise error


async def _drive(script: _Responses, **kw) -> list[StreamEvent]:
    return [
        event
        async for event in resumable_stream(
            outage=GatewayOutage(
                provider="test",
                window_seconds=kw.pop("window_seconds", 30.0),
                base_delay=kw.pop("base_delay", 1.0),
                max_backoff_seconds=kw.pop("max_backoff_seconds", 10.0),
            ),
            open_response=script.open,
            read_events=script.read,
        )
    ]


class TestResumableStreamWindow:
    async def test_backoff_doubles_to_its_ceiling_and_never_overruns_the_window(
        self, clock
    ) -> None:
        down = httpx.ConnectError("refused")
        script = _Responses(*[([], down)] * 20)

        with pytest.raises(LLMError, match="llm.gateway_outage_window_seconds=30s"):
            await _drive(script)

        # 1+2+4+8+10 = 25s, then the last 5s of the 30s window, then it is over.
        assert clock.slept == [1.0, 2.0, 4.0, 8.0, 10.0, 5.0]
        assert len(script.opened) == 7
        assert script.closed == 7
        assert cap_hit_counts() == {GATEWAY_OUTAGE_KNOB: 1}
        assert retry_counts() == {"llm.test": 6}

    async def test_the_window_is_measured_from_the_first_failure_not_from_the_call(
        self, clock
    ) -> None:
        attempts = iter([True, False])

        async def read(response):
            if next(attempts):
                clock.now += 600.0  # the stream ran ten minutes before it was cut
                yield _text("a")
                raise httpx.ReadError("reset")
            yield _text("done")

        script = _Responses(([], None), ([], None))
        script.read = read  # type: ignore[method-assign]

        # A 30s window is not spent by the 600s the first attempt already ran.
        events = await _drive(script)

        assert [e.type for e in events] == [
            StreamEventType.TEXT_DELTA,
            StreamEventType.STREAM_RESTART,
            StreamEventType.TEXT_DELTA,
        ]
        assert clock.slept == [1.0]

    async def test_delivering_after_a_reissue_resets_the_backoff(self, clock) -> None:
        cut = httpx.RemoteProtocolError("cut")
        script = _Responses(
            ([_text("a")], cut),
            ([], cut),
            ([_text("b")], cut),  # recovered (delivered), then a second outage
            ([_text("c")], None),
        )

        events = await _drive(script)

        assert [e.type for e in events] == [
            StreamEventType.TEXT_DELTA,
            StreamEventType.STREAM_RESTART,
            StreamEventType.TEXT_DELTA,
            StreamEventType.STREAM_RESTART,
            StreamEventType.TEXT_DELTA,
        ]
        # 1s, 2s into the first outage; the second outage starts again at 1s.
        assert clock.slept == [1.0, 2.0, 1.0]
        assert retry_counts() == {"llm.test": 3}

    async def test_time_spent_failing_accumulates_across_outages_within_one_call(
        self, clock
    ) -> None:
        cut = httpx.RemoteProtocolError("cut")
        script = _Responses(
            ([_text("a")], cut),
            ([_text("b")], cut),
            ([_text("c")], None),
        )

        events = await _drive(script, window_seconds=1.5)

        assert events[-1].text == "c"
        # 1s spent failing the first time leaves 0.5s for the second.
        assert clock.slept == [1.0, 0.5]

    async def test_a_gateway_that_flaps_cannot_keep_a_call_alive_forever(self, clock) -> None:
        cut = httpx.RemoteProtocolError("cut")
        script = _Responses(*[([_text("x")], cut)] * 50)

        with pytest.raises(LLMError, match="llm.gateway_outage_window_seconds=3s"):
            await _drive(script, window_seconds=3.0)

        # Every cycle delivered something and every cycle still cost its backoff.
        assert sum(clock.slept) == 3.0
        assert clock.slept == [1.0] * 3
        assert len(script.opened) == 4

    async def test_an_incomplete_stream_is_sent_again(self, clock) -> None:
        script = _Responses(
            ([_text("a")], StreamIncompleteError("no terminator")), ([_text("b")], None)
        )

        events = await _drive(script)

        assert [e.type for e in events] == [
            StreamEventType.TEXT_DELTA,
            StreamEventType.STREAM_RESTART,
            StreamEventType.TEXT_DELTA,
        ]
        assert clock.slept == [1.0]

    async def test_other_exceptions_pass_through_unretried(self, clock) -> None:
        script = _Responses(([_text("a")], ValueError("bug")))

        with pytest.raises(ValueError, match="bug"):
            await _drive(script)

        assert clock.slept == []
        assert script.closed == 1
        assert retry_counts() == {}

    async def test_an_llm_error_from_the_reader_is_not_retried(self, clock) -> None:
        script = _Responses(([_text("a")], LLMError("model refused", status_code=400)))

        with pytest.raises(LLMError, match="model refused"):
            await _drive(script)

        assert clock.slept == []


def _outage(window: float = 30.0) -> GatewayOutage:
    return GatewayOutage(
        provider="test", window_seconds=window, base_delay=1.0, max_backoff_seconds=10.0
    )


class TestPostWithinWindow:
    @respx.mock
    async def test_every_attempt_sends_freshly_minted_headers(self, clock) -> None:
        route = respx.post("http://gw/v1/x").mock(
            side_effect=[httpx.ConnectError("down"), httpx.Response(200, text="ok")]
        )
        minted = iter(["Bearer old", "Bearer new"])

        async def headers() -> dict[str, str]:
            return {"authorization": next(minted)}

        async with httpx.AsyncClient() as client:
            response = await post_within_window(
                client, "http://gw/v1/x", {"a": 1}, stream=False, outage=_outage(), headers=headers
            )

        assert response.status_code == 200
        assert [c.request.headers["authorization"] for c in route.calls] == [
            "Bearer old",
            "Bearer new",
        ]
        assert json.loads(route.calls[1].request.content) == {"a": 1}

    @respx.mock
    @pytest.mark.parametrize("status", sorted(RETRYABLE_STATUS_CODES))
    @pytest.mark.parametrize("stream", [True, False])
    async def test_unavailable_statuses_are_waited_out(self, status, stream, clock) -> None:
        route = respx.post("http://gw/v1/x").mock(
            side_effect=[httpx.Response(status), httpx.Response(200, text="ok")]
        )
        async with httpx.AsyncClient() as client:
            response = await post_within_window(
                client, "http://gw/v1/x", {}, stream=stream, outage=_outage()
            )
            if stream:
                await response.aread()
                await response.aclose()

        assert response.status_code == 200
        assert route.call_count == 2
        assert clock.slept == [1.0]

    @respx.mock
    @pytest.mark.parametrize("status", [200, 400, 401, 404, 422])
    async def test_other_statuses_are_returned_for_the_caller_to_judge(self, status, clock) -> None:
        respx.post("http://gw/v1/x").mock(return_value=httpx.Response(status))
        async with httpx.AsyncClient() as client:
            response = await post_within_window(
                client, "http://gw/v1/x", {}, stream=False, outage=_outage()
            )
        assert response.status_code == status
        assert clock.slept == []

    @respx.mock
    async def test_a_spent_window_raises_with_the_status(self, clock) -> None:
        respx.post("http://gw/v1/x").mock(return_value=httpx.Response(503))
        async with httpx.AsyncClient() as client:
            with pytest.raises(LLMError) as raised:
                await post_within_window(
                    client, "http://gw/v1/x", {}, stream=True, outage=_outage(window=3.0)
                )
        assert raised.value.status_code == 503
        assert "llm.gateway_outage_window_seconds=3s" in str(raised.value)

    def test_the_retryable_statuses_are_the_unavailable_ones(self) -> None:
        assert RETRYABLE_STATUS_CODES == {429, 500, 502, 503, 504}


class TestDescribeFailure:
    def test_a_failure_with_no_message_is_named_by_type(self) -> None:
        assert transient.describe_failure(httpx.ReadError("")) == "ReadError"

    def test_a_status_is_named_by_code(self) -> None:
        assert transient.describe_failure(TransientStatusError(503)) == "HTTP 503"


# ---------------------------------------------------------------------------
# The agent: a restarted answer replaces the partial one, and a tool runs once
# ---------------------------------------------------------------------------


class _ScriptedStreamLLM(LLMPort):
    """Streams scripted event lists, one list per call."""

    def __init__(self, *calls: list[StreamEvent]) -> None:
        self._calls = iter(calls)

    async def generate(self, messages, *, tools, system, model, max_tokens, thinking=None):
        raise AssertionError("the agent streams")

    async def stream(
        self, messages, *, tools, system, model, max_tokens, thinking=None
    ) -> AsyncIterator[StreamEvent]:
        for event in next(self._calls):
            yield event


def _done(stop: StopReason | None = None) -> StreamEvent:
    return StreamEvent(
        type=StreamEventType.MESSAGE_DONE,
        usage=TokenUsage(input_tokens=5, output_tokens=2),
        stop_reason=stop,
    )


def _tool(call_id: str = "c1") -> StreamEvent:
    return StreamEvent(
        type=StreamEventType.TOOL_CALL,
        tool_call=ToolCall(id=call_id, name="echo", input={"message": "once"}),
    )


def _restart() -> StreamEvent:
    return StreamEvent(type=StreamEventType.STREAM_RESTART)


class TestAgentHonoursStreamRestart:
    async def test_the_partial_turn_is_discarded_and_the_tool_runs_exactly_once(self) -> None:
        tool = EchoTool()
        tool.execute = AsyncMock(wraps=tool.execute)  # type: ignore[method-assign]
        llm = _ScriptedStreamLLM(
            [
                _text("I will ech"),
                StreamEvent(type=StreamEventType.THINKING, text="hmm"),
                _tool("c1"),
                _done(),
                _restart(),
                _text("Echoing now."),
                _tool("c2"),
                _done(),
            ],
            [_text("All done."), _done()],
        )
        agent, channel = make_agent(llm, tools=[tool])

        result = await agent.run_turn("echo once")

        tool.execute.assert_awaited_once()
        assert [c.id for c in result.tool_calls] == ["c2"]
        assert result.response == "All done."
        # What the model said before the cut is in no message the next call sees.
        history = [str(m.content) for m in agent._session.messages]
        assert not any("I will ech" in text for text in history)
        assert any("Echoing now." in text for text in history)

    async def test_the_live_stream_is_told_when_displayed_text_is_being_redone(self) -> None:
        llm = _ScriptedStreamLLM(
            [_text("Part"), _restart(), _text("Whole answer"), _done()],
        )
        agent, channel = make_agent(llm)

        result = await agent.run_turn("hi")

        assert result.response == "Whole answer"
        thoughts = [e.payload["text"] for e in channel.events if e.type.value == "thought"]
        assert thoughts[0] == "Part"
        assert "restarting this response" in thoughts[1]
        assert thoughts[2] == "Whole answer"

    async def test_a_restart_before_anything_was_shown_adds_no_notice(self) -> None:
        llm = _ScriptedStreamLLM([_restart(), _text("Answer"), _done()])
        agent, channel = make_agent(llm)

        result = await agent.run_turn("hi")

        assert result.response == "Answer"
        thoughts = [e.payload["text"] for e in channel.events if e.type.value == "thought"]
        assert thoughts == ["Answer"]

    async def test_usage_and_truncation_of_the_dropped_attempt_do_not_leak(self) -> None:
        llm = _ScriptedStreamLLM(
            [
                _text("x"),
                _done(StopReason.MAX_TOKENS),
                _restart(),
                _text("fine"),
                _done(),
            ],
        )
        agent, _ = make_agent(llm)

        result = await agent.run_turn("hi")

        assert result.response == "fine"
        assert result.usage.input_tokens == 5


class RotatingTokenAuth:
    """Mints a different bearer token on every call, like a short-lived workload token."""

    def __init__(self) -> None:
        self._minted = 0

    def headers(self) -> dict[str, str]:
        self._minted += 1
        return {"authorization": f"Bearer token-{self._minted}"}

    def invalidate(self) -> bool:
        return False


class TestRetryCarriesAFreshCredential:
    @respx.mock
    @pytest.mark.parametrize("wire", WIRES[2:], ids=WIRE_IDS[2:])
    async def test_a_reissue_minutes_later_does_not_reuse_the_first_token(self, wire) -> None:
        route = respx.post(wire.url).mock(
            side_effect=[
                _cut(wire.body(["Hel"], finish=False)),
                _ok(wire.body(["done"], finish=True)),
            ]
        )
        adapter = wire.build(
            auth_adapter="tests.ravn.unit.test_llm_stream_resume.RotatingTokenAuth",
            **_FAST,
        )

        await _stream(adapter, wire)

        sent = [call.request.headers["authorization"] for call in route.calls]
        assert sent == ["Bearer token-1", "Bearer token-2"]


class TestOpenAIResponsesGenerateHonoursStreamRestart:
    @respx.mock
    async def test_generate_returns_only_the_reissued_answer(self) -> None:
        wire = WIRES[3]
        respx.post(wire.url).mock(
            side_effect=[
                _cut(wire.body(["Hel"], finish=False)),
                _ok(wire.body(["Hello ", "world"], finish=True)),
            ]
        )

        response = await wire.build(**_FAST).generate(
            [{"role": "user", "content": "go"}],
            tools=[],
            system="s",
            model=wire.model,
            max_tokens=100,
        )

        assert isinstance(response, LLMResponse)
        assert response.content == "Hello world"
        assert [c.id for c in response.tool_calls] == ["t1"]
        assert response.stop_reason == StopReason.TOOL_USE
        assert len(response.provider_items) == 1
        assert response.usage.input_tokens == 7
