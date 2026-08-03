"""Tests for the OpenAI Chat Completions inbound interface."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from bifrost.app import create_app
from bifrost.config import BifrostConfig, ProviderConfig
from bifrost.inbound.chat_completions import (
    OpenAIChatRequest,
    anthropic_response_to_openai,
    anthropic_stream_to_openai,
    openai_error_response,
    openai_request_to_anthropic,
)
from bifrost.translation.models import (
    AnthropicResponse,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    UsageInfo,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config() -> BifrostConfig:
    return BifrostConfig(
        providers={"openai": ProviderConfig(models=["gpt-4o"])},
    )


def _make_anthropic_response(
    text: str = "Hello!",
    stop_reason: str = "end_turn",
    input_tokens: int = 10,
    output_tokens: int = 5,
) -> AnthropicResponse:
    return AnthropicResponse(
        id="msg_test123",
        content=[TextBlock(text=text)],
        model="gpt-4o",
        stop_reason=stop_reason,
        usage=UsageInfo(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def _parse_sse_chunks(body: str) -> list[dict]:
    """Parse an SSE response body into a list of JSON payloads."""
    chunks = []
    for line in body.splitlines():
        if line.startswith("data: ") and line[6:] != "[DONE]":
            chunks.append(json.loads(line[6:]))
    return chunks


async def _async_iter(items: list[str]):
    for item in items:
        yield item


# ---------------------------------------------------------------------------
# Request translation: OpenAI → Anthropic
# ---------------------------------------------------------------------------


class TestOpenAIRequestToAnthropic:
    def _req(self, **kwargs) -> OpenAIChatRequest:
        defaults = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hello"}],
        }
        defaults.update(kwargs)
        return OpenAIChatRequest.model_validate(defaults)

    def test_simple_user_message(self):
        req = self._req()
        result = openai_request_to_anthropic(req)
        assert result.model == "gpt-4o"
        assert len(result.messages) == 1
        assert result.messages[0].role == "user"
        assert result.messages[0].content == "Hello"

    def test_system_message_extracted(self):
        req = self._req(
            messages=[
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Hello"},
            ]
        )
        result = openai_request_to_anthropic(req)
        assert result.system == "You are helpful."
        assert len(result.messages) == 1
        assert result.messages[0].role == "user"

    def test_multiple_system_messages_joined(self):
        req = self._req(
            messages=[
                {"role": "system", "content": "Part 1."},
                {"role": "system", "content": "Part 2."},
                {"role": "user", "content": "Hi"},
            ]
        )
        result = openai_request_to_anthropic(req)
        assert result.system == "Part 1.\n\nPart 2."

    def test_no_system_message(self):
        req = self._req()
        result = openai_request_to_anthropic(req)
        assert result.system is None

    def test_assistant_text_message(self):
        req = self._req(
            messages=[
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there!"},
            ]
        )
        result = openai_request_to_anthropic(req)
        assert result.messages[1].role == "assistant"
        assert result.messages[1].content == "Hi there!"

    def test_assistant_reasoning_message_becomes_thinking_block(self):
        req = self._req(
            messages=[
                {"role": "user", "content": "Hello"},
                {
                    "role": "assistant",
                    "reasoning_content": "I should inspect first.",
                    "content": "I found it.",
                },
            ]
        )

        content = openai_request_to_anthropic(req).messages[1].content
        assert isinstance(content, list)
        assert isinstance(content[0], ThinkingBlock)
        assert content[0].thinking == "I should inspect first."
        assert isinstance(content[1], TextBlock)

    def test_chat_template_kwargs_forwarded(self):
        req = self._req(
            chat_template_kwargs={
                "enable_thinking": True,
                "force_nonempty_content": True,
            }
        )

        result = openai_request_to_anthropic(req)

        assert result.chat_template_kwargs == {
            "enable_thinking": True,
            "force_nonempty_content": True,
        }

    def test_assistant_tool_calls_become_tool_use_blocks(self):
        req = self._req(
            messages=[
                {"role": "user", "content": "What's the weather?"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_abc",
                            "type": "function",
                            "function": {
                                "name": "get_weather",
                                "arguments": '{"city": "Oslo"}',
                            },
                        }
                    ],
                },
            ]
        )
        result = openai_request_to_anthropic(req)
        asst_msg = result.messages[1]
        assert asst_msg.role == "assistant"
        content = asst_msg.content
        assert isinstance(content, list)
        assert isinstance(content[0], ToolUseBlock)
        assert content[0].id == "call_abc"
        assert content[0].name == "get_weather"
        assert content[0].input == {"city": "Oslo"}

    def test_tool_role_becomes_tool_result_block(self):
        req = self._req(
            messages=[
                {"role": "user", "content": "Hello"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "lookup", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call_1", "content": "Result text"},
            ]
        )
        result = openai_request_to_anthropic(req)
        # Tool results become a user message
        tool_msg = result.messages[2]
        assert tool_msg.role == "user"
        content = tool_msg.content
        assert isinstance(content, list)
        from bifrost.translation.models import ToolResultBlock

        assert isinstance(content[0], ToolResultBlock)
        assert content[0].tool_use_id == "call_1"
        assert content[0].content == "Result text"

    def test_multiple_tool_results_merged_into_one_user_message(self):
        req = self._req(
            messages=[
                {"role": "user", "content": "Lookup two things"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "fn1", "arguments": "{}"},
                        },
                        {
                            "id": "call_2",
                            "type": "function",
                            "function": {"name": "fn2", "arguments": "{}"},
                        },
                    ],
                },
                {"role": "tool", "tool_call_id": "call_1", "content": "Result 1"},
                {"role": "tool", "tool_call_id": "call_2", "content": "Result 2"},
            ]
        )
        result = openai_request_to_anthropic(req)
        # Multiple tool results should collapse into one user message
        assert len(result.messages) == 3
        tool_msg = result.messages[2]
        assert tool_msg.role == "user"
        content = tool_msg.content
        assert isinstance(content, list)
        assert len(content) == 2

    def test_tool_definitions_translated(self):
        req = self._req(
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "Get weather",
                        "parameters": {
                            "type": "object",
                            "properties": {"city": {"type": "string"}},
                        },
                    },
                }
            ]
        )
        result = openai_request_to_anthropic(req)
        assert result.tools is not None
        assert len(result.tools) == 1
        assert result.tools[0].name == "get_weather"
        assert result.tools[0].description == "Get weather"

    def test_tool_choice_auto(self):
        req = self._req(
            tools=[
                {
                    "type": "function",
                    "function": {"name": "fn", "description": "", "parameters": {}},
                }
            ],
            tool_choice="auto",
        )
        result = openai_request_to_anthropic(req)
        from bifrost.translation.models import ToolChoiceAuto

        assert isinstance(result.tool_choice, ToolChoiceAuto)

    def test_tool_choice_required_maps_to_any(self):
        req = self._req(
            tools=[
                {
                    "type": "function",
                    "function": {"name": "fn", "description": "", "parameters": {}},
                }
            ],
            tool_choice="required",
        )
        result = openai_request_to_anthropic(req)
        from bifrost.translation.models import ToolChoiceAny

        assert isinstance(result.tool_choice, ToolChoiceAny)

    def test_tool_choice_specific_function(self):
        req = self._req(
            tools=[
                {
                    "type": "function",
                    "function": {"name": "my_fn", "description": "", "parameters": {}},
                }
            ],
            tool_choice={"type": "function", "function": {"name": "my_fn"}},
        )
        result = openai_request_to_anthropic(req)
        from bifrost.translation.models import ToolChoiceTool

        assert isinstance(result.tool_choice, ToolChoiceTool)
        assert result.tool_choice.name == "my_fn"

    def test_tool_choice_none_strips_tools(self):
        req = self._req(
            tools=[
                {
                    "type": "function",
                    "function": {"name": "fn", "description": "", "parameters": {}},
                }
            ],
            tool_choice="none",
        )
        result = openai_request_to_anthropic(req)
        assert result.tools is None
        assert result.tool_choice is None

    def test_stop_string_converted_to_list(self):
        req = self._req(stop="END")
        result = openai_request_to_anthropic(req)
        assert result.stop_sequences == ["END"]

    def test_stop_list_forwarded(self):
        req = self._req(stop=["END", "STOP"])
        result = openai_request_to_anthropic(req)
        assert result.stop_sequences == ["END", "STOP"]

    def test_temperature_forwarded(self):
        req = self._req(temperature=0.7)
        result = openai_request_to_anthropic(req)
        assert result.temperature == 0.7

    def test_top_p_forwarded(self):
        req = self._req(top_p=0.95)
        result = openai_request_to_anthropic(req)
        assert result.top_p == 0.95

    def test_max_tokens_forwarded(self):
        req = self._req(max_tokens=512)
        result = openai_request_to_anthropic(req)
        assert result.max_tokens == 512

    def test_max_tokens_defaults_to_1024(self):
        req = self._req()
        result = openai_request_to_anthropic(req)
        assert result.max_tokens == 1024

    def test_stream_flag_forwarded(self):
        req = self._req(stream=True)
        result = openai_request_to_anthropic(req)
        assert result.stream is True

    def test_assistant_text_and_tool_calls(self):
        req = self._req(
            messages=[
                {"role": "user", "content": "Go"},
                {
                    "role": "assistant",
                    "content": "Calling tool...",
                    "tool_calls": [
                        {
                            "id": "call_x",
                            "type": "function",
                            "function": {"name": "do_it", "arguments": "{}"},
                        }
                    ],
                },
            ]
        )
        result = openai_request_to_anthropic(req)
        content = result.messages[1].content
        assert isinstance(content, list)
        assert isinstance(content[0], TextBlock)
        assert isinstance(content[1], ToolUseBlock)

    def test_invalid_tool_call_json_handled(self):
        req = self._req(
            messages=[
                {"role": "user", "content": "Go"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_bad",
                            "type": "function",
                            "function": {"name": "bad_fn", "arguments": "not-json"},
                        }
                    ],
                },
            ]
        )
        result = openai_request_to_anthropic(req)
        content = result.messages[1].content
        assert isinstance(content, list)
        block = content[0]
        assert isinstance(block, ToolUseBlock)
        assert block.input == {"raw": "not-json"}

    def test_list_content_parts_extracted(self):
        req = self._req(
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Hello "},
                        {"type": "text", "text": "world"},
                    ],
                }
            ]
        )
        result = openai_request_to_anthropic(req)
        assert result.messages[0].content == "Hello world"


# ---------------------------------------------------------------------------
# Response translation: Anthropic → OpenAI
# ---------------------------------------------------------------------------


class TestAnthropicResponseToOpenAI:
    def test_basic_text_response(self):
        resp = _make_anthropic_response("Hello!")
        result = anthropic_response_to_openai(resp)
        assert result["object"] == "chat.completion"
        assert result["id"] == "msg_test123"
        assert result["model"] == "gpt-4o"
        assert result["choices"][0]["message"]["role"] == "assistant"
        assert result["choices"][0]["message"]["content"] == "Hello!"
        assert result["choices"][0]["finish_reason"] == "stop"

    def test_usage_fields_translated(self):
        resp = _make_anthropic_response(input_tokens=20, output_tokens=10)
        result = anthropic_response_to_openai(resp)
        assert result["usage"]["prompt_tokens"] == 20
        assert result["usage"]["completion_tokens"] == 10
        assert result["usage"]["total_tokens"] == 30

    def test_stop_reason_end_turn_maps_to_stop(self):
        resp = _make_anthropic_response(stop_reason="end_turn")
        result = anthropic_response_to_openai(resp)
        assert result["choices"][0]["finish_reason"] == "stop"

    def test_stop_reason_tool_use_maps_to_tool_calls(self):
        resp = AnthropicResponse(
            id="msg_t",
            content=[ToolUseBlock(id="t1", name="fn", input={})],
            model="gpt-4o",
            stop_reason="tool_use",
            usage=UsageInfo(),
        )
        result = anthropic_response_to_openai(resp)
        assert result["choices"][0]["finish_reason"] == "tool_calls"

    def test_stop_reason_max_tokens_maps_to_length(self):
        resp = _make_anthropic_response(stop_reason="max_tokens")
        result = anthropic_response_to_openai(resp)
        assert result["choices"][0]["finish_reason"] == "length"

    def test_tool_use_blocks_become_tool_calls(self):
        resp = AnthropicResponse(
            id="msg_t",
            content=[ToolUseBlock(id="call_1", name="get_weather", input={"city": "Oslo"})],
            model="gpt-4o",
            stop_reason="tool_use",
            usage=UsageInfo(),
        )
        result = anthropic_response_to_openai(resp)
        msg = result["choices"][0]["message"]
        assert "tool_calls" in msg
        tc = msg["tool_calls"][0]
        assert tc["id"] == "call_1"
        assert tc["type"] == "function"
        assert tc["function"]["name"] == "get_weather"
        assert json.loads(tc["function"]["arguments"]) == {"city": "Oslo"}

    def test_thinking_block_uses_reasoning_content(self):
        resp = AnthropicResponse(
            id="msg_t",
            content=[ThinkingBlock(thinking="Let me think."), TextBlock(text="Answer.")],
            model="gpt-4o",
            stop_reason="end_turn",
            usage=UsageInfo(),
        )
        result = anthropic_response_to_openai(resp)
        message = result["choices"][0]["message"]
        assert message["reasoning_content"] == "Let me think."
        assert message["content"] == "Answer."

    def test_empty_text_gives_null_content(self):
        resp = AnthropicResponse(
            id="msg_t",
            content=[ToolUseBlock(id="t1", name="fn", input={})],
            model="gpt-4o",
            stop_reason="tool_use",
            usage=UsageInfo(),
        )
        result = anthropic_response_to_openai(resp)
        assert result["choices"][0]["message"]["content"] is None

    def test_created_is_integer(self):
        resp = _make_anthropic_response()
        result = anthropic_response_to_openai(resp)
        assert isinstance(result["created"], int)

    def test_logprobs_null(self):
        resp = _make_anthropic_response()
        result = anthropic_response_to_openai(resp)
        assert result["choices"][0]["logprobs"] is None


# ---------------------------------------------------------------------------
# Streaming translation: Anthropic SSE → OpenAI delta SSE
# ---------------------------------------------------------------------------


class TestAnthropicStreamToOpenAI:
    @pytest.mark.asyncio
    async def test_thinking_delta_becomes_reasoning_content(self):
        events = [
            "event: message_start\ndata: "
            + json.dumps(
                {
                    "type": "message_start",
                    "message": {"id": "msg_reasoning", "usage": {"input_tokens": 5}},
                }
            )
            + "\n\n",
            "event: content_block_start\ndata: "
            + json.dumps(
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "thinking", "thinking": ""},
                }
            )
            + "\n\n",
            "event: content_block_delta\ndata: "
            + json.dumps(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "thinking_delta", "thinking": "Inspect first."},
                }
            )
            + "\n\n",
            "event: message_stop\ndata: " + json.dumps({"type": "message_stop"}) + "\n\n",
        ]

        chunks = [
            chunk
            async for chunk in anthropic_stream_to_openai(
                _async_iter(events), message_id="chatcmpl-reasoning", model="gpt-4o"
            )
        ]
        parsed = [json.loads(chunk[6:]) for chunk in chunks if chunk != "data: [DONE]\n\n"]

        reasoning = [
            item["choices"][0]["delta"]["reasoning_content"]
            for item in parsed
            if "reasoning_content" in item["choices"][0]["delta"]
        ]
        assert reasoning == ["Inspect first."]

    @pytest.mark.asyncio
    async def test_basic_text_stream(self):
        events = [
            "event: message_start\ndata: "
            + json.dumps(
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg_123",
                        "model": "gpt-4o",
                        "usage": {"input_tokens": 5, "output_tokens": 0},
                    },
                }
            )
            + "\n\n",
            "event: content_block_start\ndata: "
            + json.dumps(
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                }
            )
            + "\n\n",
            "event: content_block_delta\ndata: "
            + json.dumps(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "Hello"},
                }
            )
            + "\n\n",
            "event: content_block_delta\ndata: "
            + json.dumps(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": " world"},
                }
            )
            + "\n\n",
            "event: content_block_stop\ndata: "
            + json.dumps({"type": "content_block_stop", "index": 0})
            + "\n\n",
            "event: message_delta\ndata: "
            + json.dumps(
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn"},
                    "usage": {"output_tokens": 10},
                }
            )
            + "\n\n",
            "event: message_stop\ndata: " + json.dumps({"type": "message_stop"}) + "\n\n",
        ]

        chunks = []
        async for line in anthropic_stream_to_openai(
            _async_iter(events), message_id="chatcmpl-test", model="gpt-4o"
        ):
            chunks.append(line)

        # Verify [DONE] is last
        assert chunks[-1] == "data: [DONE]\n\n"

        # Parse non-DONE chunks
        parsed = [json.loads(c[6:]) for c in chunks if c != "data: [DONE]\n\n"]

        # First chunk should have role
        assert parsed[0]["choices"][0]["delta"] == {"role": "assistant", "content": ""}

        # Text delta chunks (exclude role chunk with content="")
        text_chunks = [p for p in parsed if p["choices"][0]["delta"].get("content")]
        assert len(text_chunks) == 2
        assert text_chunks[0]["choices"][0]["delta"]["content"] == "Hello"
        assert text_chunks[1]["choices"][0]["delta"]["content"] == " world"

        # Finish chunk
        finish_chunk = [p for p in parsed if p["choices"][0].get("finish_reason") is not None]
        assert len(finish_chunk) == 1
        assert finish_chunk[0]["choices"][0]["finish_reason"] == "stop"

    @pytest.mark.asyncio
    async def test_tool_use_stream(self):
        events = [
            "event: message_start\ndata: "
            + json.dumps(
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg_tool",
                        "usage": {"input_tokens": 10},
                    },
                }
            )
            + "\n\n",
            "event: content_block_start\ndata: "
            + json.dumps(
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {
                        "type": "tool_use",
                        "id": "call_abc",
                        "name": "get_weather",
                    },
                }
            )
            + "\n\n",
            "event: content_block_delta\ndata: "
            + json.dumps(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "input_json_delta", "partial_json": '{"city":'},
                }
            )
            + "\n\n",
            "event: content_block_delta\ndata: "
            + json.dumps(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": '"Oslo"}',
                    },
                }
            )
            + "\n\n",
            "event: content_block_stop\ndata: "
            + json.dumps({"type": "content_block_stop", "index": 0})
            + "\n\n",
            "event: message_delta\ndata: "
            + json.dumps(
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "tool_use"},
                    "usage": {"output_tokens": 8},
                }
            )
            + "\n\n",
            "event: message_stop\ndata: " + json.dumps({"type": "message_stop"}) + "\n\n",
        ]

        chunks = []
        async for line in anthropic_stream_to_openai(
            _async_iter(events), message_id="chatcmpl-tool", model="gpt-4o"
        ):
            chunks.append(line)

        parsed = [json.loads(c[6:]) for c in chunks if c != "data: [DONE]\n\n"]

        # Tool call start chunk
        tool_start = [
            p
            for p in parsed
            if "tool_calls" in p["choices"][0]["delta"]
            and p["choices"][0]["delta"]["tool_calls"][0].get("id")
        ]
        assert len(tool_start) == 1
        tc = tool_start[0]["choices"][0]["delta"]["tool_calls"][0]
        assert tc["id"] == "call_abc"
        assert tc["function"]["name"] == "get_weather"
        assert tc["index"] == 0

        # Argument delta chunks
        arg_chunks = [
            p
            for p in parsed
            if "tool_calls" in p["choices"][0]["delta"]
            and "arguments" in p["choices"][0]["delta"]["tool_calls"][0].get("function", {})
            and not p["choices"][0]["delta"]["tool_calls"][0].get("id")
        ]
        assert len(arg_chunks) == 2

        # Finish reason = tool_calls
        finish = [p for p in parsed if p["choices"][0].get("finish_reason")]
        assert finish[-1]["choices"][0]["finish_reason"] == "tool_calls"

    @pytest.mark.asyncio
    async def test_usage_in_finish_chunk(self):
        events = [
            "event: message_start\ndata: "
            + json.dumps(
                {
                    "type": "message_start",
                    "message": {"id": "m", "usage": {"input_tokens": 7}},
                }
            )
            + "\n\n",
            "event: message_delta\ndata: "
            + json.dumps(
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn"},
                    "usage": {"output_tokens": 3},
                }
            )
            + "\n\n",
            "event: message_stop\ndata: " + json.dumps({"type": "message_stop"}) + "\n\n",
        ]

        chunks = []
        async for line in anthropic_stream_to_openai(
            _async_iter(events), message_id="cid", model="gpt-4o"
        ):
            chunks.append(line)

        parsed = [json.loads(c[6:]) for c in chunks if c != "data: [DONE]\n\n"]
        finish = [p for p in parsed if "usage" in p]
        assert len(finish) == 1
        assert finish[0]["usage"]["prompt_tokens"] == 7
        assert finish[0]["usage"]["completion_tokens"] == 3
        assert finish[0]["usage"]["total_tokens"] == 10

    @pytest.mark.asyncio
    async def test_done_sentinel_terminates_stream(self):
        events = [
            "data: [DONE]\n\n",
            "data: " + json.dumps({"type": "message_start"}) + "\n\n",
        ]
        chunks = []
        async for line in anthropic_stream_to_openai(
            _async_iter(events), message_id="cid", model="gpt-4o"
        ):
            chunks.append(line)
        assert chunks == ["data: [DONE]\n\n"]

    @pytest.mark.asyncio
    async def test_ping_events_ignored(self):
        events = [
            "event: ping\ndata: " + json.dumps({"type": "ping"}) + "\n\n",
            "event: message_start\ndata: "
            + json.dumps(
                {
                    "type": "message_start",
                    "message": {"id": "m", "usage": {"input_tokens": 1}},
                }
            )
            + "\n\n",
            "event: message_stop\ndata: " + json.dumps({"type": "message_stop"}) + "\n\n",
        ]
        chunks = []
        async for line in anthropic_stream_to_openai(
            _async_iter(events), message_id="cid", model="gpt-4o"
        ):
            chunks.append(line)
        # No chunk from ping
        parsed = [json.loads(c[6:]) for c in chunks if c != "data: [DONE]\n\n"]
        assert all("ping" not in str(p) for p in parsed)

    @pytest.mark.asyncio
    async def test_individual_lines_format(self):
        """AnthropicAdapter yields individual lines rather than full events."""
        lines = [
            "event: message_start\n",
            "data: "
            + json.dumps(
                {
                    "type": "message_start",
                    "message": {"id": "msg_x", "usage": {"input_tokens": 2}},
                }
            )
            + "\n",
            "\n",
            "event: message_delta\n",
            "data: "
            + json.dumps(
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn"},
                    "usage": {"output_tokens": 1},
                }
            )
            + "\n",
            "\n",
            "event: message_stop\n",
            "data: " + json.dumps({"type": "message_stop"}) + "\n",
        ]

        chunks = []
        async for line in anthropic_stream_to_openai(
            _async_iter(lines), message_id="cid", model="gpt-4o"
        ):
            chunks.append(line)

        assert "data: [DONE]\n\n" in chunks

    @pytest.mark.asyncio
    async def test_malformed_json_skipped(self):
        events = [
            "data: not-json\n\n",
            "event: message_stop\ndata: " + json.dumps({"type": "message_stop"}) + "\n\n",
        ]
        chunks = []
        async for line in anthropic_stream_to_openai(
            _async_iter(events), message_id="cid", model="gpt-4o"
        ):
            chunks.append(line)
        assert "data: [DONE]\n\n" in chunks

    @pytest.mark.asyncio
    async def test_empty_stream_emits_done(self):
        chunks = []
        async for line in anthropic_stream_to_openai(
            _async_iter([]), message_id="cid", model="gpt-4o"
        ):
            chunks.append(line)
        assert chunks == ["data: [DONE]\n\n"]

    @pytest.mark.asyncio
    async def test_message_id_from_upstream_event(self):
        events = [
            "event: message_start\ndata: "
            + json.dumps(
                {
                    "type": "message_start",
                    "message": {
                        "id": "upstream_id_xyz",
                        "usage": {"input_tokens": 1},
                    },
                }
            )
            + "\n\n",
            "event: message_stop\ndata: " + json.dumps({"type": "message_stop"}) + "\n\n",
        ]
        chunks = []
        async for line in anthropic_stream_to_openai(
            _async_iter(events), message_id="fallback_id", model="gpt-4o"
        ):
            chunks.append(line)

        parsed = [json.loads(c[6:]) for c in chunks if c != "data: [DONE]\n\n"]
        assert all(p["id"] == "upstream_id_xyz" for p in parsed)


# ---------------------------------------------------------------------------
# /v1/chat/completions endpoint integration tests
# ---------------------------------------------------------------------------


class TestChatCompletionsEndpoint:
    def _client(self) -> TestClient:
        return TestClient(create_app(_make_config()))

    @patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock)
    def test_basic_request_returns_openai_format(self, mock_complete):
        mock_complete.return_value = _make_anthropic_response("World!")
        with self._client() as client:
            resp = client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": "Hello"}],
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["object"] == "chat.completion"
        assert body["choices"][0]["message"]["content"] == "World!"
        assert body["choices"][0]["finish_reason"] == "stop"
        assert "usage" in body
        assert body["usage"]["prompt_tokens"] == 10
        assert body["usage"]["completion_tokens"] == 5

    @patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock)
    def test_system_message_translated(self, mock_complete):
        mock_complete.return_value = _make_anthropic_response()
        with self._client() as client:
            resp = client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o",
                    "messages": [
                        {"role": "system", "content": "Be helpful."},
                        {"role": "user", "content": "Hi"},
                    ],
                },
            )
        assert resp.status_code == 200
        # The system message should have been extracted and the AnthropicRequest
        # should have a system field — verified by checking the call args.
        call_args = mock_complete.call_args
        anthropic_req = call_args[0][0]
        assert anthropic_req.system == "Be helpful."

    def test_invalid_body_returns_422(self):
        with self._client() as client:
            resp = client.post(
                "/v1/chat/completions",
                json={"not_a_valid_field": True},
            )
        assert resp.status_code == 422

    def test_malformed_json_returns_422(self):
        with self._client() as client:
            resp = client.post(
                "/v1/chat/completions",
                content=b"not json",
                headers={"content-type": "application/json"},
            )
        assert resp.status_code == 422

    @patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock)
    def test_router_error_returns_502(self, mock_complete):
        from bifrost.router import RouterError

        mock_complete.side_effect = RouterError("no provider")
        with self._client() as client:
            resp = client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": "Hi"}],
                },
            )
        assert resp.status_code == 502

    def test_unknown_model_returns_502(self):
        with self._client() as client:
            resp = client.post(
                "/v1/chat/completions",
                json={
                    "model": "no-such-model-xyz",
                    "messages": [{"role": "user", "content": "Hi"}],
                },
            )
        assert resp.status_code == 502

    @patch("bifrost.router.ModelRouter.stream")
    def test_streaming_returns_event_stream(self, mock_stream):
        async def _fake_stream(request):
            yield (
                "event: message_start\ndata: "
                + json.dumps(
                    {
                        "type": "message_start",
                        "message": {"id": "m", "usage": {"input_tokens": 1}},
                    }
                )
                + "\n\n"
            )
            yield ("event: message_stop\ndata: " + json.dumps({"type": "message_stop"}) + "\n\n")

        mock_stream.return_value = _fake_stream(None)

        with self._client() as client:
            resp = client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": "Hi"}],
                    "stream": True,
                },
            )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        assert "data: [DONE]" in resp.text

    @patch("bifrost.router.ModelRouter.stream")
    def test_streaming_chunks_have_openai_format(self, mock_stream):
        async def _fake_stream(request):
            yield (
                "event: message_start\ndata: "
                + json.dumps(
                    {
                        "type": "message_start",
                        "message": {"id": "msg_s", "usage": {"input_tokens": 2}},
                    }
                )
                + "\n\n"
            )
            yield (
                "event: content_block_delta\ndata: "
                + json.dumps(
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": "Hi!"},
                    }
                )
                + "\n\n"
            )
            yield (
                "event: message_delta\ndata: "
                + json.dumps(
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "end_turn"},
                        "usage": {"output_tokens": 1},
                    }
                )
                + "\n\n"
            )
            yield ("event: message_stop\ndata: " + json.dumps({"type": "message_stop"}) + "\n\n")

        mock_stream.return_value = _fake_stream(None)

        with self._client() as client:
            resp = client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": "Hi"}],
                    "stream": True,
                },
            )

        chunks = _parse_sse_chunks(resp.text)
        assert all(c["object"] == "chat.completion.chunk" for c in chunks)
        text_chunks = [c for c in chunks if c["choices"][0]["delta"].get("content")]
        assert any(c["choices"][0]["delta"]["content"] == "Hi!" for c in text_chunks)

    @patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock)
    def test_correlation_id_echoed(self, mock_complete):
        mock_complete.return_value = _make_anthropic_response()
        with self._client() as client:
            resp = client.post(
                "/v1/chat/completions",
                json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}]},
                headers={"X-Correlation-ID": "test-cid-123"},
            )
        assert resp.headers.get("X-Correlation-ID") == "test-cid-123"

    @patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock)
    def test_max_tokens_forwarded(self, mock_complete):
        mock_complete.return_value = _make_anthropic_response()
        with self._client() as client:
            client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": "Hi"}],
                    "max_tokens": 256,
                },
            )
        call_args = mock_complete.call_args
        anthropic_req = call_args[0][0]
        assert anthropic_req.max_tokens == 256

    @patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock)
    def test_temperature_forwarded(self, mock_complete):
        mock_complete.return_value = _make_anthropic_response()
        with self._client() as client:
            client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": "Hi"}],
                    "temperature": 0.5,
                },
            )
        call_args = mock_complete.call_args
        anthropic_req = call_args[0][0]
        assert anthropic_req.temperature == 0.5

    def test_invalid_body_has_openai_error_shape(self):
        with self._client() as client:
            resp = client.post(
                "/v1/chat/completions",
                json={"not_a_valid_field": True},
            )
        body = resp.json()
        assert "error" in body
        assert "message" in body["error"]
        assert body["error"]["type"] == "invalid_request_error"
        assert body["error"]["param"] is None
        assert body["error"]["code"] is None

    @patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock)
    def test_router_error_has_openai_error_shape(self, mock_complete):
        from bifrost.router import RouterError

        mock_complete.side_effect = RouterError("all providers failed")
        with self._client() as client:
            resp = client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": "Hi"}],
                },
            )
        assert resp.status_code == 502
        body = resp.json()
        assert "error" in body
        assert body["error"]["message"] == "Upstream routing failed."
        assert body["error"]["type"] == "server_error"

    def test_unknown_model_error_has_openai_shape(self):
        with self._client() as client:
            resp = client.post(
                "/v1/chat/completions",
                json={
                    "model": "no-such-model-xyz",
                    "messages": [{"role": "user", "content": "Hi"}],
                },
            )
        assert resp.status_code == 502
        body = resp.json()
        assert "error" in body
        assert body["error"]["type"] == "server_error"


# ---------------------------------------------------------------------------
# openai_error_response helper
# ---------------------------------------------------------------------------


class TestOpenAIErrorResponse:
    def test_status_code(self):
        resp = openai_error_response(422, "bad input", "invalid_request_error")
        assert resp.status_code == 422

    def test_body_shape(self):
        import json as _json

        resp = openai_error_response(502, "upstream failed", "server_error")
        body = _json.loads(resp.body)
        assert body["error"]["message"] == "upstream failed"
        assert body["error"]["type"] == "server_error"
        assert body["error"]["param"] is None
        assert body["error"]["code"] is None
