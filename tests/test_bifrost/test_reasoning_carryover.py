"""Opaque reasoning carried across tool-loop steps, through Bifröst's chat-completions surface.

The ChatGPT backend returns each reasoning item's ``encrypted_content``; Bifröst
hands it to the caller as ``reasoning_details`` and, when the caller sends the
assistant message back, replays it as a ``reasoning`` input item so the next
step continues the reasoning instead of re-deriving it.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from bifrost.adapters.chatgpt_subscription import DEFAULT_BASE_URL
from bifrost.app import create_app
from bifrost.config import BifrostConfig, ProviderConfig
from bifrost.inbound.chat_completions import (
    InvalidReasoningDetailsError,
    OpenAIChatRequest,
    anthropic_response_to_openai,
    anthropic_stream_to_openai,
    openai_request_to_anthropic,
)
from bifrost.translation.models import (
    AnthropicRequest,
    AnthropicResponse,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    UsageInfo,
)
from bifrost.translation.responses_stream import responses_stream_to_anthropic

ADAPTER_PATH = "bifrost.adapters.chatgpt_subscription.ChatGPTSubscriptionAdapter"
AUTH_PATH = "niuu.adapters.outbound.http_auth.StaticBearerTokenAuthAdapter"
BROKER = "http://volundr.test"
TOKEN_URL = f"{BROKER}/api/v1/internal/credentials/codex/tokens"
RESPONSES_URL = f"{DEFAULT_BASE_URL}/responses"
FIXTURES = Path(__file__).parent / "fixtures" / "chatgpt_responses"
SIGNATURE = "REDACTED-FINAL"


def _detail(data: str = "ENC", index: int = 0) -> dict:
    return {
        "type": "reasoning.encrypted",
        "data": data,
        "format": "openai-responses-v1",
        "index": index,
    }


def _request(messages: list[dict]) -> OpenAIChatRequest:
    return OpenAIChatRequest.model_validate({"model": "gpt-6-sol", "messages": messages})


def _assistant(**fields) -> dict:
    return {"role": "assistant", **fields}


def _call(call_id: str = "call_1") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'},
    }


async def _lines(text: str):
    for line in text.splitlines():
        yield line


class TestInbound:
    def test_details_become_a_signed_thinking_block_ahead_of_everything_else(self):
        request = openai_request_to_anthropic(
            _request(
                [
                    {"role": "user", "content": "go"},
                    _assistant(
                        content="checking",
                        reasoning_content="need the weather",
                        reasoning_details=[_detail("ENC1")],
                        tool_calls=[_call()],
                    ),
                ]
            )
        )

        blocks = request.messages[1].content
        assert [type(b) for b in blocks] == [ThinkingBlock, TextBlock, ToolUseBlock]
        assert blocks[0] == ThinkingBlock(thinking="need the weather", signature="ENC1")

    def test_details_alone_still_make_a_signed_block(self):
        request = openai_request_to_anthropic(
            _request(
                [
                    {"role": "user", "content": "go"},
                    _assistant(
                        content=None, reasoning_details=[_detail("ENC1")], tool_calls=[_call()]
                    ),
                ]
            )
        )

        blocks = request.messages[1].content
        assert blocks[0] == ThinkingBlock(thinking="", signature="ENC1")
        assert isinstance(blocks[1], ToolUseBlock)

    def test_details_with_text_only_are_not_flattened_to_a_string(self):
        request = openai_request_to_anthropic(
            _request(
                [
                    {"role": "user", "content": "go"},
                    _assistant(content="done", reasoning_details=[_detail("ENC1")]),
                ]
            )
        )

        assert request.messages[1].content == [
            ThinkingBlock(thinking="", signature="ENC1"),
            TextBlock(text="done"),
        ]

    def test_details_with_nothing_else_keep_the_message(self):
        request = openai_request_to_anthropic(
            _request(
                [
                    {"role": "user", "content": "go"},
                    _assistant(content=None, reasoning_details=[_detail("ENC1")]),
                ]
            )
        )

        assert request.messages[1].content == [ThinkingBlock(thinking="", signature="ENC1")]

    def test_several_details_make_one_block_each_and_the_text_rides_on_the_first(self):
        request = openai_request_to_anthropic(
            _request(
                [
                    {"role": "user", "content": "go"},
                    _assistant(
                        reasoning_content="summary",
                        reasoning_details=[_detail("ENC1", 0), _detail("ENC2", 1)],
                        tool_calls=[_call()],
                    ),
                ]
            )
        )

        thinking = request.messages[1].content[:2]
        assert thinking == [
            ThinkingBlock(thinking="summary", signature="ENC1"),
            ThinkingBlock(thinking="", signature="ENC2"),
        ]

    def test_reasoning_text_without_details_stays_an_unsigned_block(self):
        request = openai_request_to_anthropic(
            _request(
                [
                    {"role": "user", "content": "go"},
                    _assistant(reasoning_content="plain", content="x"),
                ]
            )
        )

        assert request.messages[1].content[0] == ThinkingBlock(thinking="plain")

    @pytest.mark.parametrize(
        ("details", "message"),
        [
            (
                [{"type": "reasoning.summary", "summary": "s"}],
                r"reasoning_details\[0\].*unsupported",
            ),
            ([{"data": "x"}], r"reasoning_details\[0\].*unsupported type None"),
            ([_detail("OK"), {"type": "weird", "data": "x"}], r"reasoning_details\[1\]"),
            (["not-an-object"], r"must be an object, got str"),
            ([{"type": "reasoning.encrypted"}], r"non-empty string 'data'"),
            ([{"type": "reasoning.encrypted", "data": ""}], r"non-empty string 'data'"),
            ([{"type": "reasoning.encrypted", "data": 5}], r"non-empty string 'data'"),
        ],
    )
    def test_malformed_or_unknown_details_fail_loudly(self, details, message):
        request = _request(
            [{"role": "user", "content": "go"}, _assistant(content="x", reasoning_details=details)]
        )

        with pytest.raises(InvalidReasoningDetailsError, match=message):
            openai_request_to_anthropic(request)

    def test_a_non_assistant_message_with_details_is_ignored_like_any_extra_field(self):
        request = openai_request_to_anthropic(
            _request([{"role": "user", "content": "go", "reasoning_details": [_detail()]}])
        )

        assert request.messages[0].content == "go"


class TestOutbound:
    def test_response_carries_the_summary_and_the_opaque_payload(self):
        response = AnthropicResponse(
            id="msg_1",
            content=[
                ThinkingBlock(thinking="need the weather", signature="ENC1"),
                ToolUseBlock(id="call_1", name="get_weather", input={"city": "Paris"}),
            ],
            model="gpt-6-sol",
            stop_reason="tool_use",
            usage=UsageInfo(),
        )

        message = anthropic_response_to_openai(response)["choices"][0]["message"]

        assert message["reasoning_content"] == "need the weather"
        assert message["reasoning_details"] == [_detail("ENC1", 0)]

    def test_a_signed_block_without_a_summary_still_has_details(self):
        response = AnthropicResponse(
            id="msg_1",
            content=[ThinkingBlock(thinking="", signature="ENC1"), TextBlock(text="hi")],
            model="gpt-6-sol",
            usage=UsageInfo(),
        )

        message = anthropic_response_to_openai(response)["choices"][0]["message"]

        assert "reasoning_content" not in message
        assert message["reasoning_details"] == [_detail("ENC1", 0)]

    def test_unsigned_thinking_has_no_details(self):
        response = AnthropicResponse(
            id="msg_1",
            content=[ThinkingBlock(thinking="plain"), TextBlock(text="hi")],
            model="gpt-6-sol",
            usage=UsageInfo(),
        )

        message = anthropic_response_to_openai(response)["choices"][0]["message"]

        assert "reasoning_details" not in message

    async def test_stream_emits_a_details_delta_for_each_signature(self):
        events = [
            "event: message_start\ndata: "
            + json.dumps({"type": "message_start", "message": {"id": "msg_1"}})
            + "\n\n",
            "event: content_block_delta\ndata: "
            + json.dumps(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "signature_delta", "signature": "ENC1"},
                }
            )
            + "\n\n",
            "event: content_block_delta\ndata: "
            + json.dumps(
                {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {"type": "signature_delta", "signature": "ENC2"},
                }
            )
            + "\n\n",
            "event: content_block_delta\ndata: "
            + json.dumps(
                {
                    "type": "content_block_delta",
                    "index": 2,
                    "delta": {"type": "signature_delta", "signature": ""},
                }
            )
            + "\n\n",
            "event: message_stop\ndata: " + json.dumps({"type": "message_stop"}) + "\n\n",
        ]

        async def source():
            for event in events:
                yield event

        chunks = [
            json.loads(chunk[6:])
            async for chunk in anthropic_stream_to_openai(source(), message_id="m", model="gpt-6")
            if chunk != "data: [DONE]\n\n"
        ]

        details = [
            c["choices"][0]["delta"]["reasoning_details"]
            for c in chunks
            if "reasoning_details" in c["choices"][0]["delta"]
        ]
        assert details == [[_detail("ENC1", 0)], [_detail("ENC2", 1)]]

    async def test_recorded_backend_stream_reaches_the_client_as_reasoning_details(self):
        text = (FIXTURES / "reasoning_tool_call.sse").read_text()
        anthropic_sse = responses_stream_to_anthropic(
            _lines(text), message_id="msg_1", model="gpt-6-sol", include_thinking=True
        )

        chunks = [
            json.loads(chunk[6:])
            async for chunk in anthropic_stream_to_openai(
                anthropic_sse, message_id="m", model="gpt-6-sol"
            )
            if chunk != "data: [DONE]\n\n"
        ]

        deltas = [c["choices"][0]["delta"] for c in chunks]
        assert [d["reasoning_content"] for d in deltas if "reasoning_content" in d] == [
            "**Checking both weather forecasts**"
        ]
        assert [d["reasoning_details"] for d in deltas if "reasoning_details" in d] == [
            [_detail(SIGNATURE, 0)]
        ]


class TestEndpoint:
    def _client(self, **kwargs) -> TestClient:
        provider = ProviderConfig(
            adapter=ADAPTER_PATH,
            models=["gpt-6-sol"],
            kwargs={
                "broker_base_url": BROKER,
                "auth_adapter": AUTH_PATH,
                "auth_kwargs": {"token": "workload-jwt"},
            },
        )
        return TestClient(create_app(BifrostConfig(providers={"chatgpt": provider})))

    def _backend(self, respx_mock, *fixtures: str):
        respx_mock.post(TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json={"access_token": "a", "chatgpt_account_id": "acct", "expires_in": 1300},
            )
        )
        return respx_mock.post(RESPONSES_URL).mock(
            side_effect=[
                httpx.Response(200, content=(FIXTURES / f"{name}.sse").read_bytes())
                for name in fixtures
            ]
        )

    def test_unknown_reasoning_details_are_a_400_with_the_openai_error_shape(self, respx_mock):
        route = self._backend(respx_mock, "text")
        with self._client() as client:
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-6-sol",
                    "messages": [
                        {"role": "user", "content": "go"},
                        _assistant(content="x", reasoning_details=[{"type": "mystery"}]),
                    ],
                },
            )

        assert response.status_code == 400
        error = response.json()["error"]
        assert error["type"] == "invalid_request_error"
        assert "reasoning_details[0]" in error["message"]
        assert route.call_count == 0

    def test_a_tool_loop_step_continues_the_reasoning_of_the_step_before(self, respx_mock):
        route = self._backend(respx_mock, "reasoning_tool_call", "text")
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Weather",
                    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
                },
            }
        ]
        thinking = {"type": "enabled", "budget_tokens": 1024}
        history = [{"role": "user", "content": "Paris or Rome for a picnic?"}]
        with self._client() as client:
            first = client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-6-sol",
                    "messages": history,
                    "tools": tools,
                    "thinking": thinking,
                },
            ).json()["choices"][0]["message"]

            assert first["reasoning_details"] == [_detail(SIGNATURE, 0)]
            assert len(first["tool_calls"]) == 2

            history.append(
                _assistant(
                    content=first["content"],
                    reasoning_content=first["reasoning_content"],
                    reasoning_details=first["reasoning_details"],
                    tool_calls=first["tool_calls"],
                )
            )
            history.extend(
                {"role": "tool", "tool_call_id": call["id"], "content": "14C and raining"}
                for call in first["tool_calls"]
            )
            second = client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-6-sol",
                    "messages": history,
                    "tools": tools,
                    "thinking": thinking,
                },
            )

        assert second.status_code == 200
        replayed = json.loads(route.calls[1].request.content)["input"]
        assert [item["type"] for item in replayed] == [
            "message",
            "reasoning",
            "function_call",
            "function_call",
            "function_call_output",
            "function_call_output",
        ]
        assert replayed[1] == {
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": "**Checking both weather forecasts**"}],
            "encrypted_content": SIGNATURE,
        }

    def test_without_returned_reasoning_nothing_is_replayed(self, respx_mock):
        # A provider that returned no reasoning_details (Qwen, vLLM, ...) is unaffected.
        route = self._backend(respx_mock, "text")
        with self._client() as client:
            client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-6-sol",
                    "messages": [
                        {"role": "user", "content": "hi"},
                        _assistant(content="hello", reasoning_content="thought"),
                        {"role": "user", "content": "again"},
                    ],
                },
            )

        sent = json.loads(route.calls[0].request.content)["input"]
        assert "reasoning" not in [item["type"] for item in sent]

    def test_the_streaming_endpoint_delivers_the_payload_to_the_client(self, respx_mock):
        self._backend(respx_mock, "reasoning_tool_call")
        with self._client() as client:
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-6-sol",
                    "stream": True,
                    "messages": [{"role": "user", "content": "go"}],
                    "thinking": {"type": "enabled", "budget_tokens": 1024},
                },
            )

        chunks = [
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: ") and line != "data: [DONE]"
        ]
        details = [
            d["reasoning_details"]
            for d in (c["choices"][0]["delta"] for c in chunks)
            if "reasoning_details" in d
        ]
        assert details == [[_detail(SIGNATURE, 0)]]


class TestAnthropicFormatPassThrough:
    def test_a_signature_survives_a_round_trip_through_the_canonical_models(self):
        request = AnthropicRequest.model_validate(
            {
                "model": "claude",
                "messages": [
                    {"role": "user", "content": "q"},
                    {
                        "role": "assistant",
                        "content": [{"type": "thinking", "thinking": "t", "signature": "SIG"}],
                    },
                ],
            }
        )

        dumped = request.model_dump(exclude_none=True)

        assert dumped["messages"][1]["content"][0]["signature"] == "SIG"

    def test_a_response_signature_is_kept(self):
        response = AnthropicResponse.model_validate(
            {
                "id": "m",
                "model": "claude",
                "content": [{"type": "thinking", "thinking": "t", "signature": "SIG"}],
            }
        )

        assert response.content[0].signature == "SIG"
