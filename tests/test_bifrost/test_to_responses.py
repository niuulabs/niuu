"""Anthropic request -> OpenAI Responses request translation."""

from __future__ import annotations

import json

import pytest

from bifrost.ports.provider import ProviderError
from bifrost.translation.models import AnthropicRequest
from bifrost.translation.to_responses import (
    ResponsesTranslationError,
    anthropic_to_responses,
    thinking_requested,
)

MODEL = "gpt-6-sol"


def _req(**kwargs) -> AnthropicRequest:
    kwargs.setdefault("model", MODEL)
    kwargs.setdefault("messages", [{"role": "user", "content": "hi"}])
    return AnthropicRequest(**kwargs)


def _translate(**kwargs) -> dict:
    options = {k: kwargs.pop(k) for k in list(kwargs) if k.startswith("opt_")}
    return anthropic_to_responses(
        _req(**kwargs), MODEL, **{k.removeprefix("opt_"): v for k, v in options.items()}
    )


def _user_text(text: str) -> dict:
    return {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}


class TestEnvelope:
    def test_backend_required_flags(self):
        payload = _translate()

        assert payload["model"] == MODEL
        assert payload["stream"] is True
        assert payload["store"] is False

    def test_plain_user_message(self):
        assert _translate()["input"] == [_user_text("hi")]

    def test_max_tokens_becomes_max_output_tokens(self):
        assert _translate(max_tokens=321)["max_output_tokens"] == 321

    def test_sampling_hints_are_not_forwarded(self):
        # The backend refuses `temperature` ("Unsupported parameter").
        payload = _translate(temperature=0.2, top_p=0.9, top_k=5)

        assert not {"temperature", "top_p", "top_k"} & payload.keys()

    def test_stop_sequences_are_refused_not_dropped(self):
        with pytest.raises(ResponsesTranslationError, match="stop_sequences"):
            _translate(stop_sequences=["END"])

    def test_translation_error_is_a_provider_error(self):
        assert issubclass(ResponsesTranslationError, ProviderError)


class TestInstructions:
    def test_string_system(self):
        assert _translate(system="Be terse.")["instructions"] == "Be terse."

    def test_block_system_is_joined(self):
        system = [{"type": "text", "text": "One."}, {"type": "text", "text": "Two."}]

        assert _translate(system=system)["instructions"] == "One.\n\nTwo."

    @pytest.mark.parametrize("system", [None, "", []])
    def test_no_system_omits_instructions(self, system):
        assert "instructions" not in _translate(system=system)


class TestConversation:
    def test_multi_turn(self):
        payload = _translate(
            messages=[
                {"role": "user", "content": "q1"},
                {"role": "assistant", "content": "a1"},
                {"role": "user", "content": [{"type": "text", "text": "q2"}]},
            ]
        )

        assert payload["input"] == [
            _user_text("q1"),
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "a1"}],
            },
            _user_text("q2"),
        ]

    def test_empty_text_is_skipped(self):
        payload = _translate(
            messages=[
                {"role": "user", "content": [{"type": "text", "text": ""}]},
                {"role": "assistant", "content": ""},
                {"role": "user", "content": "x"},
            ]
        )

        assert payload["input"] == [_user_text("x")]

    def test_tool_use_and_tool_result(self):
        payload = _translate(
            messages=[
                {"role": "user", "content": "weather?"},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Checking."},
                        {
                            "type": "tool_use",
                            "id": "call_1",
                            "name": "get_weather",
                            "input": {"city": "Paris"},
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "call_1", "content": "14C"},
                        {"type": "text", "text": "thanks"},
                    ],
                },
            ]
        )

        assert payload["input"][1:] == [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Checking."}],
            },
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "get_weather",
                "arguments": json.dumps({"city": "Paris"}),
            },
            {"type": "function_call_output", "call_id": "call_1", "output": "14C"},
            _user_text("thanks"),
        ]

    def test_text_blocks_around_a_tool_result_keep_their_order(self):
        payload = _translate(
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "before"},
                        {"type": "tool_result", "tool_use_id": "c", "content": "r"},
                        {"type": "text", "text": "after"},
                    ],
                }
            ]
        )

        assert [item["type"] for item in payload["input"]] == [
            "message",
            "function_call_output",
            "message",
        ]

    def test_tool_result_with_block_content(self):
        payload = _translate(
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "c",
                            "content": [
                                {"type": "text", "text": "a"},
                                {"type": "text", "text": "b"},
                            ],
                        }
                    ],
                }
            ]
        )

        assert payload["input"][0]["output"] == "a\n\nb"

    def test_unsigned_thinking_in_history_is_dropped(self):
        payload = _translate(
            messages=[
                {"role": "user", "content": "q"},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "hmm"},
                        {"type": "text", "text": "a"},
                    ],
                },
            ]
        )

        assert len(payload["input"]) == 2
        assert payload["input"][1]["content"] == [{"type": "output_text", "text": "a"}]

    def test_user_turn_cannot_carry_tool_use(self):
        with pytest.raises(ResponsesTranslationError, match="tool_use"):
            _translate(
                messages=[
                    {
                        "role": "user",
                        "content": [{"type": "tool_use", "id": "c", "name": "n", "input": {}}],
                    }
                ]
            )

    def test_assistant_turn_cannot_carry_an_image(self):
        image = {"type": "image", "source": {"type": "url", "url": "https://x/y.png"}}

        with pytest.raises(ResponsesTranslationError, match="image"):
            _translate(
                messages=[
                    {"role": "user", "content": "q"},
                    {"role": "assistant", "content": [image]},
                ]
            )


class TestReasoningReplay:
    """Signed thinking blocks come back as `reasoning` input items (verified live)."""

    def _turn(self, *blocks: dict) -> list[dict]:
        return _translate(
            messages=[
                {"role": "user", "content": "q"},
                {"role": "assistant", "content": list(blocks)},
            ]
        )["input"][1:]

    def test_the_request_asks_for_encrypted_reasoning(self):
        assert _translate()["include"] == ["reasoning.encrypted_content"]

    def test_signed_thinking_becomes_a_reasoning_item_before_the_tool_call(self):
        items = self._turn(
            {"type": "thinking", "thinking": "need the weather", "signature": "ENC1"},
            {"type": "tool_use", "id": "c1", "name": "get_weather", "input": {"city": "Paris"}},
        )

        assert items == [
            {
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": "need the weather"}],
                "encrypted_content": "ENC1",
            },
            {
                "type": "function_call",
                "call_id": "c1",
                "name": "get_weather",
                "arguments": '{"city": "Paris"}',
            },
        ]

    def test_reasoning_item_has_exactly_the_fields_the_backend_requires(self):
        # Live: omitting `summary` is a 400 and `id` is not needed under store=false.
        [item, _] = self._turn(
            {"type": "thinking", "thinking": "t", "signature": "ENC"},
            {"type": "text", "text": "a"},
        )

        assert set(item) == {"type", "summary", "encrypted_content"}

    def test_a_summaryless_reasoning_item_keeps_an_empty_summary_list(self):
        [item, _] = self._turn(
            {"type": "thinking", "thinking": "", "signature": "ENC"},
            {"type": "text", "text": "a"},
        )

        assert item["summary"] == []
        assert item["encrypted_content"] == "ENC"

    def test_blocks_keep_their_order_around_text(self):
        items = self._turn(
            {"type": "text", "text": "first"},
            {"type": "thinking", "thinking": "t", "signature": "ENC"},
            {"type": "tool_use", "id": "c", "name": "n", "input": {}},
        )

        assert [item["type"] for item in items] == ["message", "reasoning", "function_call"]

    def test_several_signed_blocks_are_all_replayed_in_order(self):
        items = self._turn(
            {"type": "thinking", "thinking": "one", "signature": "ENC1"},
            {"type": "thinking", "thinking": "two", "signature": "ENC2"},
            {"type": "text", "text": "a"},
        )

        assert [i.get("encrypted_content") for i in items[:2]] == ["ENC1", "ENC2"]

    def test_unsigned_thinking_is_dropped_next_to_signed(self):
        items = self._turn(
            {"type": "thinking", "thinking": "plain"},
            {"type": "thinking", "thinking": "signed", "signature": "ENC"},
            {"type": "text", "text": "a"},
        )

        assert [i["type"] for i in items] == ["reasoning", "message"]

    def test_user_turn_still_cannot_carry_thinking(self):
        with pytest.raises(ResponsesTranslationError, match="thinking"):
            _translate(
                messages=[
                    {
                        "role": "user",
                        "content": [{"type": "thinking", "thinking": "t", "signature": "S"}],
                    }
                ]
            )


class TestImages:
    def test_base64_image_becomes_a_data_url(self):
        image = {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"},
        }
        payload = _translate(
            messages=[
                {"role": "user", "content": [{"type": "text", "text": "what?"}, image]},
            ]
        )

        assert payload["input"][0]["content"] == [
            {"type": "input_text", "text": "what?"},
            {"type": "input_image", "image_url": "data:image/png;base64,QUJD"},
        ]

    def test_url_image(self):
        image = {"type": "image", "source": {"type": "url", "url": "https://x/y.png"}}
        payload = _translate(messages=[{"role": "user", "content": [image]}])

        assert payload["input"][0]["content"] == [
            {"type": "input_image", "image_url": "https://x/y.png"}
        ]

    @pytest.mark.parametrize(
        "source",
        [
            {"type": "url"},
            {"type": "base64", "media_type": "image/png"},
            {"type": "base64", "data": "QUJD"},
        ],
    )
    def test_incomplete_image_is_refused(self, source):
        with pytest.raises(ResponsesTranslationError, match="image block"):
            _translate(
                messages=[{"role": "user", "content": [{"type": "image", "source": source}]}]
            )


class TestTools:
    TOOL = {
        "name": "get_weather",
        "description": "Weather",
        "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}},
    }

    def test_tool_definition(self):
        payload = _translate(tools=[self.TOOL])

        assert payload["tools"] == [
            {
                "type": "function",
                "name": "get_weather",
                "description": "Weather",
                "parameters": self.TOOL["input_schema"],
                "strict": False,
            }
        ]
        assert payload["tool_choice"] == "auto"
        assert payload["parallel_tool_calls"] is True

    def test_empty_schema_gets_an_object_schema(self):
        payload = _translate(tools=[{"name": "ping"}])

        assert payload["tools"][0]["parameters"] == {"type": "object", "properties": {}}

    @pytest.mark.parametrize(
        ("choice", "expected"),
        [
            ({"type": "auto"}, "auto"),
            ({"type": "any"}, "required"),
            ({"type": "tool", "name": "get_weather"}, {"type": "function", "name": "get_weather"}),
        ],
    )
    def test_tool_choice(self, choice, expected):
        assert _translate(tools=[self.TOOL], tool_choice=choice)["tool_choice"] == expected

    def test_parallel_tool_calls_is_configurable(self):
        payload = _translate(tools=[self.TOOL], opt_parallel_tool_calls=False)

        assert payload["parallel_tool_calls"] is False

    def test_no_tools_means_no_tool_fields(self):
        payload = _translate(tool_choice={"type": "any"})

        assert not {"tools", "tool_choice", "parallel_tool_calls"} & payload.keys()


class TestReasoning:
    def test_no_effort_and_no_thinking_omits_reasoning(self):
        assert "reasoning" not in _translate()

    def test_request_effort_wins_over_default(self):
        payload = _translate(reasoning_effort="high", opt_default_reasoning_effort="low")

        assert payload["reasoning"] == {"effort": "high"}

    def test_default_effort(self):
        assert _translate(opt_default_reasoning_effort="medium")["reasoning"] == {
            "effort": "medium"
        }

    def test_summary_is_requested_only_with_thinking(self):
        payload = _translate(thinking={"type": "enabled", "budget_tokens": 1024})

        assert payload["reasoning"] == {"summary": "auto"}

    def test_summary_mode_is_configurable_and_can_be_disabled(self):
        thinking = {"type": "enabled", "budget_tokens": 1024}

        assert _translate(thinking=thinking, opt_reasoning_summary="detailed")["reasoning"] == {
            "summary": "detailed"
        }
        assert "reasoning" not in _translate(thinking=thinking, opt_reasoning_summary="")

    def test_thinking_requested(self):
        assert not thinking_requested(_req())
        assert not thinking_requested(_req(thinking={"type": "disabled"}))
        assert thinking_requested(_req(thinking={"type": "enabled", "budget_tokens": 1024}))
        assert thinking_requested(_req(thinking={"type": "adaptive"}))


class TestResponseFormat:
    @staticmethod
    def _with_format(response_format: dict) -> AnthropicRequest:
        request = _req()
        request._response_format = response_format
        return request

    def test_json_schema(self):
        request = self._with_format(
            {"type": "json_schema", "json_schema": {"name": "pick", "schema": {"type": "object"}}}
        )

        payload = anthropic_to_responses(request, MODEL)

        assert payload["text"] == {
            "format": {
                "type": "json_schema",
                "name": "pick",
                "schema": {"type": "object"},
                "strict": True,
            }
        }

    @pytest.mark.parametrize(
        "response_format",
        [
            {"type": "json_object"},
            {"type": "json_schema", "json_schema": {"schema": {"type": "object"}}},
            {"type": "json_schema", "json_schema": {"name": "pick"}},
        ],
    )
    def test_unsupported_formats_are_refused(self, response_format):
        with pytest.raises(ResponsesTranslationError):
            anthropic_to_responses(self._with_format(response_format), MODEL)
