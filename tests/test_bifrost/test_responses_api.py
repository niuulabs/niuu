"""The OpenAI Responses API surface: what the Codex CLI speaks to the gateway."""

from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from bifrost.app import create_app
from bifrost.config import BifrostConfig, ProviderConfig
from bifrost.inbound.responses import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    ResponsesRequest,
    UnsupportedResponsesInputError,
    anthropic_response_to_responses,
    anthropic_stream_to_responses,
    responses_request_to_anthropic,
)
from bifrost.translation.models import (
    AnthropicResponse,
    ImageBlock,
    TextBlock,
    ToolChoiceAny,
    ToolChoiceTool,
    ToolResultBlock,
    ToolUseBlock,
    UsageInfo,
)


def _request(**body) -> ResponsesRequest:
    return ResponsesRequest.model_validate({"model": "llama3.1:8b", **body})


async def _async_iter(items: list[str]):
    for item in items:
        yield item


def _parse_events(body: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    name = ""
    for line in body.splitlines():
        if line.startswith("event: "):
            name = line[7:]
        elif line.startswith("data: "):
            events.append((name, json.loads(line[6:])))
    return events


# ---------------------------------------------------------------------------
# Request translation
# ---------------------------------------------------------------------------


class TestRequestTranslation:
    def test_string_input_and_instructions(self):
        req = responses_request_to_anthropic(
            _request(input="hi", instructions="Be brief.", max_output_tokens=200)
        )
        assert req.system == "Be brief."
        assert req.max_tokens == 200
        assert [m.role for m in req.messages] == ["user"]
        assert req.messages[0].content == [TextBlock(text="hi")]

    def test_default_output_budget_suits_a_coding_agent(self):
        assert responses_request_to_anthropic(_request(input="x")).max_tokens == (
            DEFAULT_MAX_OUTPUT_TOKENS
        )

    def test_codex_turn_with_a_tool_round_trip(self):
        """The shape Codex sends: developer text, user message, its earlier
        function call, the shell output, reasoning it got back, and the tools."""
        req = responses_request_to_anthropic(
            _request(
                instructions="You are Codex.",
                input=[
                    {"type": "message", "role": "developer", "content": "Sandbox: read-only."},
                    {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "list files"}],
                    },
                    {"type": "reasoning", "summary": [], "encrypted_content": "..."},
                    {
                        "type": "function_call",
                        "call_id": "call_1",
                        "name": "shell",
                        "arguments": '{"command": ["ls"]}',
                    },
                    {"type": "function_call_output", "call_id": "call_1", "output": "README.md"},
                ],
                tools=[
                    {
                        "type": "function",
                        "name": "shell",
                        "description": "Run a command",
                        "parameters": {"type": "object", "properties": {"command": {}}},
                    },
                    {"type": "web_search"},
                ],
                tool_choice="auto",
                reasoning={"effort": "high", "summary": "auto"},
                stream=True,
                store=False,
                include=["reasoning.encrypted_content"],
            )
        )
        assert req.system == "You are Codex.\n\nSandbox: read-only."
        assert [m.role for m in req.messages] == ["user", "assistant", "user"]
        assert req.messages[1].content == [
            ToolUseBlock(id="call_1", name="shell", input={"command": ["ls"]})
        ]
        assert req.messages[2].content == [
            ToolResultBlock(tool_use_id="call_1", content="README.md")
        ]
        assert req.tools is not None and [t.name for t in req.tools] == ["shell"]
        assert req.tools[0].input_schema == {"type": "object", "properties": {"command": {}}}
        assert req.stream is True
        assert req.reasoning_effort == "high"

    def test_assistant_text_and_tool_calls_share_one_message(self):
        req = responses_request_to_anthropic(
            _request(
                input=[
                    {"role": "user", "content": "q"},
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "Looking."}],
                    },
                    {"type": "function_call", "call_id": "c", "name": "shell", "arguments": "{}"},
                ]
            )
        )
        assert [m.role for m in req.messages] == ["user", "assistant"]
        assert req.messages[1].content == [
            TextBlock(text="Looking."),
            ToolUseBlock(id="c", name="shell", input={}),
        ]

    def test_tool_choice_and_none_strip_tools(self):
        tools = [{"type": "function", "name": "shell", "parameters": {}}]
        assert isinstance(
            responses_request_to_anthropic(
                _request(input="x", tools=tools, tool_choice="required")
            ).tool_choice,
            ToolChoiceAny,
        )
        assert responses_request_to_anthropic(
            _request(input="x", tools=tools, tool_choice={"type": "function", "name": "shell"})
        ).tool_choice == ToolChoiceTool(name="shell")
        assert (
            responses_request_to_anthropic(
                _request(input="x", tools=tools, tool_choice="none")
            ).tools
            is None
        )

    def test_images_come_through_as_blocks(self):
        data = base64.b64encode(b"png").decode()
        req = responses_request_to_anthropic(
            _request(
                input=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "see"},
                            {"type": "input_image", "image_url": f"data:image/png;base64,{data}"},
                        ],
                    }
                ]
            )
        )
        image = req.messages[0].content[1]
        assert isinstance(image, ImageBlock)
        assert image.source.media_type == "image/png"
        assert image.source.data == data

    def test_malformed_arguments_are_kept_raw(self):
        req = responses_request_to_anthropic(
            _request(
                input=[{"type": "function_call", "call_id": "c", "name": "f", "arguments": "{oops"}]
            )
        )
        assert req.messages[0].content == [ToolUseBlock(id="c", name="f", input={"raw": "{oops"})]

    @pytest.mark.parametrize(
        "body, message",
        [
            ({"input": "x", "previous_response_id": "resp_1"}, "previous_response_id"),
            ({"input": [{"type": "computer_call"}]}, "unsupported input item type"),
            ({"input": [{"role": "tool", "content": "x"}]}, "unsupported message role"),
            (
                {"input": [{"role": "user", "content": [{"type": "input_file"}]}]},
                "unsupported user content part",
            ),
            (
                {
                    "input": [
                        {
                            "role": "user",
                            "content": [{"type": "input_image", "image_url": "ftp://x"}],
                        }
                    ]
                },
                "input_image needs",
            ),
        ],
    )
    def test_unsupported_features_are_refused_not_dropped(self, body, message):
        with pytest.raises(UnsupportedResponsesInputError, match=message):
            responses_request_to_anthropic(_request(**body))


# ---------------------------------------------------------------------------
# Response translation
# ---------------------------------------------------------------------------


class TestResponseTranslation:
    def test_text_and_function_call_items(self):
        response = AnthropicResponse(
            id="msg_x",
            content=[
                TextBlock(text="Running."),
                ToolUseBlock(id="call_9", name="shell", input={"command": ["ls"]}),
            ],
            model="llama3.1:8b",
            stop_reason="tool_use",
            usage=UsageInfo(input_tokens=12, output_tokens=7),
        )
        out = anthropic_response_to_responses(response, response_id="resp_1", created_at=5)
        assert out["object"] == "response"
        assert out["status"] == "completed"
        assert out["incomplete_details"] is None
        assert out["output"][0] == {
            "id": "msg_resp_1",
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Running.", "annotations": []}],
        }
        assert out["output"][1]["type"] == "function_call"
        assert out["output"][1]["call_id"] == "call_9"
        assert json.loads(out["output"][1]["arguments"]) == {"command": ["ls"]}
        assert out["usage"]["total_tokens"] == 19
        assert out["usage"]["output_tokens_details"] == {"reasoning_tokens": 0}

    def test_output_budget_exhausted_is_incomplete(self):
        response = AnthropicResponse(
            id="m", content=[TextBlock(text="…")], model="m", stop_reason="max_tokens"
        )
        out = anthropic_response_to_responses(response, response_id="r", created_at=1)
        assert out["status"] == "incomplete"
        assert out["incomplete_details"] == {"reason": "max_output_tokens"}


# ---------------------------------------------------------------------------
# Streaming translation
# ---------------------------------------------------------------------------


def _anthropic_events(*payloads: dict) -> list[str]:
    return [f"event: {p['type']}\ndata: {json.dumps(p)}\n\n" for p in payloads]


class TestStreamTranslation:
    @pytest.mark.asyncio
    async def test_text_then_tool_call_stream(self):
        source = _anthropic_events(
            {"type": "message_start", "message": {"id": "m", "usage": {"input_tokens": 40}}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Let me "},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "look."},
            },
            {"type": "content_block_stop", "index": 0},
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "tool_use", "id": "call_1", "name": "shell"},
            },
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "input_json_delta", "partial_json": '{"command": '},
            },
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "input_json_delta", "partial_json": '["ls"]}'},
            },
            {"type": "content_block_stop", "index": 1},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use"},
                "usage": {"output_tokens": 9},
            },
            {"type": "message_stop"},
        )
        body = "".join(
            [
                chunk
                async for chunk in anthropic_stream_to_responses(
                    _async_iter(source), response_id="resp_1", model="llama3.1:8b"
                )
            ]
        )
        events = _parse_events(body)
        names = [name for name, _ in events]
        assert names == [
            "response.created",
            "response.in_progress",
            "response.output_item.added",
            "response.content_part.added",
            "response.output_text.delta",
            "response.output_text.delta",
            "response.output_text.done",
            "response.content_part.done",
            "response.output_item.done",
            "response.output_item.added",
            "response.function_call_arguments.delta",
            "response.function_call_arguments.delta",
            "response.function_call_arguments.done",
            "response.output_item.done",
            "response.completed",
        ]
        assert [payload["sequence_number"] for _, payload in events] == list(range(len(events)))
        text_done = dict(events)["response.output_text.done"]
        assert text_done["text"] == "Let me look."
        fc_done = events[13][1]["item"]
        assert fc_done == {
            "id": "fc_resp_1_1",
            "type": "function_call",
            "status": "completed",
            "call_id": "call_1",
            "name": "shell",
            "arguments": '{"command": ["ls"]}',
        }
        completed = events[-1][1]["response"]
        assert completed["id"] == "resp_1"
        assert completed["status"] == "completed"
        assert [item["type"] for item in completed["output"]] == ["message", "function_call"]
        assert completed["usage"] == {
            "input_tokens": 40,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens": 9,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 49,
        }

    @pytest.mark.asyncio
    async def test_thinking_is_not_forwarded_and_max_tokens_is_incomplete(self):
        source = _anthropic_events(
            {"type": "message_start", "message": {"id": "m", "usage": {"input_tokens": 1}}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}},
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "hmm"},
            },
            {"type": "content_block_stop", "index": 0},
            {"type": "content_block_start", "index": 1, "content_block": {"type": "text"}},
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "text_delta", "text": "a"},
            },
            {"type": "content_block_stop", "index": 1},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "max_tokens"},
                "usage": {"output_tokens": 1},
            },
            {"type": "message_stop"},
        )
        events = _parse_events(
            "".join(
                [
                    chunk
                    async for chunk in anthropic_stream_to_responses(
                        _async_iter(source), response_id="r", model="m"
                    )
                ]
            )
        )
        assert "hmm" not in json.dumps([payload for _, payload in events])
        assert events[-1][0] == "response.incomplete"
        assert events[-1][1]["response"]["incomplete_details"] == {"reason": "max_output_tokens"}
        assert events[-1][1]["response"]["output"][0]["content"][0]["text"] == "a"

    @pytest.mark.asyncio
    async def test_upstream_failure_ends_the_stream_with_response_failed(self):
        async def _broken():
            yield _anthropic_events(
                {"type": "message_start", "message": {"id": "m", "usage": {"input_tokens": 1}}}
            )[0]
            raise RuntimeError("Client error '400 Bad Request' for url 'http://ollama/v1/chat'")

        events = _parse_events(
            "".join(
                [
                    chunk
                    async for chunk in anthropic_stream_to_responses(
                        _broken(), response_id="r", model="m"
                    )
                ]
            )
        )
        assert [name for name, _ in events] == [
            "response.created",
            "response.in_progress",
            "response.failed",
        ]
        failed = events[-1][1]["response"]
        assert failed["status"] == "failed"
        assert "400 Bad Request" in failed["error"]["message"]


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


def _client() -> TestClient:
    config = BifrostConfig(providers={"local": ProviderConfig(models=["llama3.1:8b"])})
    return TestClient(create_app(config))


class TestResponsesEndpoint:
    @patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock)
    def test_non_streaming_response(self, mock_complete: AsyncMock):
        mock_complete.return_value = AnthropicResponse(
            id="m",
            content=[TextBlock(text="NIUU-42")],
            model="llama3.1:8b",
            stop_reason="end_turn",
            usage=UsageInfo(input_tokens=3, output_tokens=2),
        )
        resp = _client().post(
            "/v1/responses",
            json={"model": "llama3.1:8b", "input": "Reply with NIUU-42", "instructions": "terse"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["object"] == "response"
        assert body["id"].startswith("resp_")
        assert body["output"][0]["content"][0]["text"] == "NIUU-42"
        sent = mock_complete.call_args[0][0]
        assert sent.system == "terse"
        assert sent.messages[0].content == [TextBlock(text="Reply with NIUU-42")]

    @patch("bifrost.router.ModelRouter.stream")
    def test_streaming_response(self, mock_stream):
        mock_stream.return_value = _async_iter(
            _anthropic_events(
                {"type": "message_start", "message": {"id": "m", "usage": {"input_tokens": 2}}},
                {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "NIUU-42"},
                },
                {"type": "content_block_stop", "index": 0},
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn"},
                    "usage": {"output_tokens": 4},
                },
                {"type": "message_stop"},
            )
        )
        resp = _client().post(
            "/v1/responses", json={"model": "llama3.1:8b", "input": "hi", "stream": True}
        )
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"].startswith("text/event-stream")
        events = _parse_events(resp.text)
        assert events[0][0] == "response.created"
        assert events[-1][0] == "response.completed"
        assert events[-1][1]["response"]["output"][0]["content"][0]["text"] == "NIUU-42"

    def test_bad_body_says_why(self):
        resp = _client().post("/v1/responses", json={"input": "hi"})
        assert resp.status_code == 422
        assert "model" in resp.json()["error"]["message"]

    def test_stateful_requests_are_refused(self):
        resp = _client().post(
            "/v1/responses",
            json={"model": "llama3.1:8b", "input": "hi", "previous_response_id": "resp_0"},
        )
        assert resp.status_code == 400
        assert "previous_response_id" in resp.json()["error"]["message"]
