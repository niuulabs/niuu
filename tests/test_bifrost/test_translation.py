"""Parametrised tests for the Anthropic ↔ OpenAI translation layer."""

from __future__ import annotations

import json

import pytest

from bifrost.translation.models import (
    AnthropicRequest,
    AnthropicResponse,
    CacheControl,
    Message,
    TextBlock,
    ThinkingBlock,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
)
from bifrost.translation.to_anthropic import openai_to_anthropic
from bifrost.translation.to_openai import anthropic_to_openai

# ---------------------------------------------------------------------------
# Anthropic → OpenAI
# ---------------------------------------------------------------------------


class TestAnthropicToOpenAI:
    def _simple_request(self, **kwargs) -> AnthropicRequest:
        defaults = {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 1024,
            "messages": [Message(role="user", content="Hello")],
        }
        defaults.update(kwargs)
        return AnthropicRequest.model_validate(defaults)

    def test_simple_text_message(self):
        req = self._simple_request()
        payload = anthropic_to_openai(req, "gpt-4o")
        assert payload["model"] == "gpt-4o"
        assert payload["messages"][-1] == {"role": "user", "content": "Hello"}
        assert payload["max_tokens"] == 1024

    def test_chat_template_kwargs_forwarded(self):
        req = self._simple_request(
            chat_template_kwargs={
                "enable_thinking": True,
                "force_nonempty_content": True,
            }
        )

        payload = anthropic_to_openai(req, "gpt-4o")

        assert payload["chat_template_kwargs"] == {
            "enable_thinking": True,
            "force_nonempty_content": True,
        }

    def test_assistant_thinking_block_becomes_reasoning_content(self):
        req = self._simple_request(
            messages=[
                Message(
                    role="assistant",
                    content=[
                        ThinkingBlock(thinking="Inspect first."),
                        TextBlock(text="Done."),
                    ],
                )
            ]
        )

        payload = anthropic_to_openai(req, "gpt-4o")

        assert payload["messages"][0] == {
            "role": "assistant",
            "content": "Done.",
            "reasoning_content": "Inspect first.",
        }

    def test_system_prompt_string(self):
        req = self._simple_request(system="You are a pirate.")
        payload = anthropic_to_openai(req, "gpt-4o")
        assert payload["messages"][0] == {"role": "system", "content": "You are a pirate."}

    def test_system_prompt_blocks(self):
        req = self._simple_request(
            system=[{"type": "text", "text": "Block 1."}, {"type": "text", "text": " Block 2."}]
        )
        payload = anthropic_to_openai(req, "gpt-4o")
        assert payload["messages"][0]["content"] == "Block 1. Block 2."

    def test_no_system_prompt(self):
        req = self._simple_request()
        payload = anthropic_to_openai(req, "gpt-4o")
        assert payload["messages"][0]["role"] == "user"

    def test_multi_turn_conversation(self):
        req = self._simple_request(
            messages=[
                Message(role="user", content="Hello"),
                Message(role="assistant", content="Hi"),
                Message(role="user", content="Bye"),
            ]
        )
        payload = anthropic_to_openai(req, "gpt-4o")
        roles = [m["role"] for m in payload["messages"]]
        assert roles == ["user", "assistant", "user"]

    def test_content_blocks_text_only(self):
        req = self._simple_request(
            messages=[
                Message(
                    role="user",
                    content=[
                        {"type": "text", "text": "Part 1."},
                        {"type": "text", "text": " Part 2."},
                    ],
                )
            ]
        )
        payload = anthropic_to_openai(req, "gpt-4o")
        assert payload["messages"][0]["content"] == "Part 1. Part 2."

    def test_tool_use_block_in_assistant_message(self):
        req = self._simple_request(
            messages=[
                Message(role="user", content="What's the weather?"),
                Message(
                    role="assistant",
                    content=[
                        {
                            "type": "tool_use",
                            "id": "tool_1",
                            "name": "get_weather",
                            "input": {"city": "Oslo"},
                        }
                    ],
                ),
            ]
        )
        payload = anthropic_to_openai(req, "gpt-4o")
        asst_msg = payload["messages"][1]
        assert "tool_calls" in asst_msg
        tc = asst_msg["tool_calls"][0]
        assert tc["id"] == "tool_1"
        assert tc["function"]["name"] == "get_weather"
        assert json.loads(tc["function"]["arguments"]) == {"city": "Oslo"}

    def test_tool_result_block_becomes_tool_role_message(self):
        req = self._simple_request(
            messages=[
                Message(
                    role="user",
                    content=[
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool_1",
                            "content": "Sunny, 22°C",
                        }
                    ],
                )
            ]
        )
        payload = anthropic_to_openai(req, "gpt-4o")
        msg = payload["messages"][0]
        assert msg["role"] == "tool"
        assert msg["tool_call_id"] == "tool_1"
        assert msg["content"] == "Sunny, 22°C"

    def test_tool_result_with_text_preserves_both(self):
        req = self._simple_request(
            messages=[
                Message(
                    role="user",
                    content=[
                        {"type": "text", "text": "Here are the results:"},
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool_1",
                            "content": "Sunny, 22°C",
                        },
                    ],
                )
            ]
        )
        payload = anthropic_to_openai(req, "gpt-4o")
        # Text should appear as a user message, tool result as a tool message.
        assert payload["messages"][0]["role"] == "user"
        assert payload["messages"][0]["content"] == "Here are the results:"
        assert payload["messages"][1]["role"] == "tool"
        assert payload["messages"][1]["tool_call_id"] == "tool_1"

    def test_tools_definition(self):
        req = self._simple_request(
            tools=[
                ToolDefinition(
                    name="get_weather",
                    description="Get weather",
                    input_schema={
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                )
            ]
        )
        payload = anthropic_to_openai(req, "gpt-4o")
        assert "tools" in payload
        tool = payload["tools"][0]
        assert tool["type"] == "function"
        assert tool["function"]["name"] == "get_weather"
        assert tool["function"]["parameters"]["properties"]["city"]["type"] == "string"

    def test_tool_choice_auto(self):
        req = self._simple_request(
            tools=[ToolDefinition(name="foo")],
            tool_choice={"type": "auto"},
        )
        payload = anthropic_to_openai(req, "gpt-4o")
        assert payload["tool_choice"] == "auto"

    def test_tool_choice_any_maps_to_required(self):
        req = self._simple_request(
            tools=[ToolDefinition(name="foo")],
            tool_choice={"type": "any"},
        )
        payload = anthropic_to_openai(req, "gpt-4o")
        assert payload["tool_choice"] == "required"

    def test_tool_choice_specific_tool(self):
        req = self._simple_request(
            tools=[ToolDefinition(name="my_tool")],
            tool_choice={"type": "tool", "name": "my_tool"},
        )
        payload = anthropic_to_openai(req, "gpt-4o")
        assert payload["tool_choice"] == {"type": "function", "function": {"name": "my_tool"}}

    def test_temperature_forwarded(self):
        req = self._simple_request(temperature=0.7)
        payload = anthropic_to_openai(req, "gpt-4o")
        assert payload["temperature"] == 0.7

    def test_top_p_forwarded(self):
        req = self._simple_request(top_p=0.9)
        payload = anthropic_to_openai(req, "gpt-4o")
        assert payload["top_p"] == 0.9

    def test_stop_sequences_forwarded(self):
        req = self._simple_request(stop_sequences=["<end>"])
        payload = anthropic_to_openai(req, "gpt-4o")
        assert payload["stop"] == ["<end>"]

    def test_stream_flag(self):
        req = self._simple_request(stream=True)
        payload = anthropic_to_openai(req, "gpt-4o")
        assert payload["stream"] is True

    @pytest.mark.parametrize("no_temp", [True])
    def test_temperature_absent_when_not_set(self, no_temp):
        req = self._simple_request()
        payload = anthropic_to_openai(req, "gpt-4o")
        assert "temperature" not in payload


# ---------------------------------------------------------------------------
# OpenAI → Anthropic
# ---------------------------------------------------------------------------


class TestOpenAIToAnthropic:
    def _openai_response(self, **kwargs) -> dict:
        defaults = {
            "id": "chatcmpl-abc123",
            "object": "chat.completion",
            "created": 1234567890,
            "model": "gpt-4o",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Hello there!"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        }
        defaults.update(kwargs)
        return defaults

    def test_basic_text_response(self):
        resp = openai_to_anthropic(self._openai_response(), "gpt-4o")
        assert isinstance(resp, AnthropicResponse)
        assert resp.id == "chatcmpl-abc123"
        assert len(resp.content) == 1
        assert isinstance(resp.content[0], TextBlock)
        assert resp.content[0].text == "Hello there!"

    def test_stop_reason_mapping_stop(self):
        resp = openai_to_anthropic(self._openai_response(), "gpt-4o")
        assert resp.stop_reason == "end_turn"

    def test_stop_reason_mapping_length(self):
        r = self._openai_response()
        r["choices"][0]["finish_reason"] = "length"
        resp = openai_to_anthropic(r, "gpt-4o")
        assert resp.stop_reason == "max_tokens"

    def test_stop_reason_mapping_tool_calls(self):
        r = self._openai_response()
        r["choices"][0]["finish_reason"] = "tool_calls"
        resp = openai_to_anthropic(r, "gpt-4o")
        assert resp.stop_reason == "tool_use"

    def test_usage_conversion(self):
        resp = openai_to_anthropic(self._openai_response(), "gpt-4o")
        assert resp.usage.input_tokens == 10
        assert resp.usage.output_tokens == 5

    def test_tool_call_response(self):
        r = self._openai_response()
        r["choices"][0]["message"] = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "arguments": '{"city": "Oslo"}',
                    },
                }
            ],
        }
        r["choices"][0]["finish_reason"] = "tool_calls"
        resp = openai_to_anthropic(r, "gpt-4o")
        assert resp.stop_reason == "tool_use"
        tool_block = resp.content[0]
        assert isinstance(tool_block, ToolUseBlock)
        assert tool_block.id == "call_1"
        assert tool_block.name == "get_weather"
        assert tool_block.input == {"city": "Oslo"}

    def test_tool_call_invalid_json_arguments(self):
        r = self._openai_response()
        r["choices"][0]["message"] = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_2",
                    "type": "function",
                    "function": {"name": "bad_tool", "arguments": "not-json"},
                }
            ],
        }
        resp = openai_to_anthropic(r, "gpt-4o")
        tool_block = resp.content[0]
        assert isinstance(tool_block, ToolUseBlock)
        assert tool_block.input == {"raw": "not-json"}

    def test_empty_choices(self):
        r = self._openai_response()
        r["choices"] = []
        resp = openai_to_anthropic(r, "gpt-4o")
        assert resp.content == []
        assert resp.stop_reason == "end_turn"

    def test_model_preserved(self):
        resp = openai_to_anthropic(self._openai_response(), "gpt-4o-mini")
        # model comes from the response JSON
        assert resp.model == "gpt-4o"

    def test_missing_usage(self):
        r = self._openai_response()
        del r["usage"]
        resp = openai_to_anthropic(r, "gpt-4o")
        assert resp.usage.input_tokens == 0
        assert resp.usage.output_tokens == 0

    def test_text_and_tool_in_same_response(self):
        r = self._openai_response()
        r["choices"][0]["message"] = {
            "role": "assistant",
            "content": "Checking...",
            "tool_calls": [
                {
                    "id": "call_3",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": "{}"},
                }
            ],
        }
        resp = openai_to_anthropic(r, "gpt-4o")
        types = [type(b).__name__ for b in resp.content]
        assert "TextBlock" in types
        assert "ToolUseBlock" in types

    def test_thinking_tags_extracted(self):
        r = self._openai_response()
        r["choices"][0]["message"]["content"] = (
            "<thinking>Let me think about this.</thinking>The answer is 42."
        )
        resp = openai_to_anthropic(r, "gpt-4o")
        assert isinstance(resp.content[0], ThinkingBlock)
        assert resp.content[0].thinking == "Let me think about this."
        assert isinstance(resp.content[1], TextBlock)
        assert resp.content[1].text == "The answer is 42."

    def test_reasoning_content_extracted(self):
        response = self._openai_response()
        response["choices"][0]["message"]["content"] = "The answer is 42."
        response["choices"][0]["message"]["reasoning_content"] = "Let me think."

        result = openai_to_anthropic(response, "gpt-4o")

        assert isinstance(result.content[0], ThinkingBlock)
        assert result.content[0].thinking == "Let me think."
        assert isinstance(result.content[1], TextBlock)

    def test_thinking_only_no_text(self):
        r = self._openai_response()
        r["choices"][0]["message"]["content"] = "<thinking>Just thinking.</thinking>"
        resp = openai_to_anthropic(r, "gpt-4o")
        assert len(resp.content) == 1
        assert isinstance(resp.content[0], ThinkingBlock)

    def test_no_thinking_tags(self):
        r = self._openai_response()
        r["choices"][0]["message"]["content"] = "Plain response."
        resp = openai_to_anthropic(r, "gpt-4o")
        assert len(resp.content) == 1
        assert isinstance(resp.content[0], TextBlock)
        assert resp.content[0].text == "Plain response."


# ---------------------------------------------------------------------------
# cache_control handling
# ---------------------------------------------------------------------------


class TestCacheControl:
    """Tests for cache_control field on TextBlock and ToolResultBlock."""

    def _simple_request(self, **kwargs) -> AnthropicRequest:
        defaults = {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 1024,
            "messages": [Message(role="user", content="Hello")],
        }
        defaults.update(kwargs)
        return AnthropicRequest.model_validate(defaults)

    # --- model round-trip (field persists in Pydantic) ---------------------

    def test_text_block_cache_control_roundtrip(self):
        block = TextBlock(text="cached", cache_control=CacheControl(type="ephemeral"))
        assert block.cache_control is not None
        assert block.cache_control.type == "ephemeral"
        dumped = block.model_dump(exclude_none=True)
        assert dumped["cache_control"] == {"type": "ephemeral"}

    def test_tool_result_block_cache_control_roundtrip(self):
        block = ToolResultBlock(
            tool_use_id="call_1",
            content="result",
            cache_control=CacheControl(type="ephemeral"),
        )
        assert block.cache_control is not None
        dumped = block.model_dump(exclude_none=True)
        assert dumped["cache_control"] == {"type": "ephemeral"}

    def test_text_block_without_cache_control_excluded(self):
        block = TextBlock(text="no cache")
        dumped = block.model_dump(exclude_none=True)
        assert "cache_control" not in dumped

    def test_system_block_cache_control_roundtrip(self):
        req = self._simple_request(
            system=[{"type": "text", "text": "Sys.", "cache_control": {"type": "ephemeral"}}]
        )
        assert req.system is not None
        assert isinstance(req.system, list)
        assert req.system[0].cache_control is not None
        assert req.system[0].cache_control.type == "ephemeral"

    # --- Anthropic adapter passthrough (model_dump carries cache_control) --

    def test_anthropic_request_dump_includes_cache_control(self):
        req = self._simple_request(
            messages=[
                Message(
                    role="user",
                    content=[
                        TextBlock(
                            text="cached content",
                            cache_control=CacheControl(type="ephemeral"),
                        )
                    ],
                )
            ]
        )
        payload = req.model_dump(exclude_none=True, exclude={"stream"})
        block = payload["messages"][0]["content"][0]
        assert block["cache_control"] == {"type": "ephemeral"}
        assert block["text"] == "cached content"

    # --- OpenAI translation: cache_control is stripped ----------------------

    def test_cache_control_stripped_from_text_block_to_openai(self):
        req = self._simple_request(
            messages=[
                Message(
                    role="user",
                    content=[
                        TextBlock(
                            text="cached",
                            cache_control=CacheControl(type="ephemeral"),
                        )
                    ],
                )
            ]
        )
        payload = anthropic_to_openai(req, "gpt-4o")
        msg = payload["messages"][0]
        assert msg["content"] == "cached"
        assert "cache_control" not in msg

    def test_cache_control_stripped_from_system_block_to_openai(self):
        req = self._simple_request(
            system=[{"type": "text", "text": "Sys.", "cache_control": {"type": "ephemeral"}}]
        )
        payload = anthropic_to_openai(req, "gpt-4o")
        sys_msg = payload["messages"][0]
        assert sys_msg["role"] == "system"
        assert sys_msg["content"] == "Sys."
        assert "cache_control" not in sys_msg

    def test_cache_control_stripped_from_tool_result_to_openai(self):
        req = self._simple_request(
            messages=[
                Message(
                    role="user",
                    content=[
                        ToolResultBlock(
                            tool_use_id="call_1",
                            content="result data",
                            cache_control=CacheControl(type="ephemeral"),
                        )
                    ],
                )
            ]
        )
        payload = anthropic_to_openai(req, "gpt-4o")
        tool_msg = payload["messages"][0]
        assert tool_msg["role"] == "tool"
        assert tool_msg["content"] == "result data"
        assert "cache_control" not in tool_msg

    def test_multiple_blocks_mixed_cache_control_stripped(self):
        """Verify cache_control is stripped even when only some blocks have it."""
        req = self._simple_request(
            messages=[
                Message(
                    role="user",
                    content=[
                        TextBlock(text="part1", cache_control=CacheControl(type="ephemeral")),
                        TextBlock(text=" part2"),
                    ],
                )
            ]
        )
        payload = anthropic_to_openai(req, "gpt-4o")
        msg = payload["messages"][0]
        assert msg["content"] == "part1 part2"
        assert "cache_control" not in msg
