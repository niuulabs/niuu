"""Tests for Skuld message channel abstraction."""

import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import WebSocketDisconnect

from skuld.channels import (
    ChannelRegistry,
    MessageChannel,
    TelegramChannel,
    WebSocketChannel,
    format_telegram_event,
    render_telegram_html,
    split_message,
)

# ---------------------------------------------------------------------------
# WebSocketChannel
# ---------------------------------------------------------------------------


class TestWebSocketChannel:
    """Tests for WebSocketChannel."""

    @pytest.fixture
    def mock_ws(self):
        ws = AsyncMock()
        ws.send_text = AsyncMock()
        ws.close = AsyncMock()
        return ws

    @pytest.fixture
    def channel(self, mock_ws):
        return WebSocketChannel(mock_ws)

    def test_channel_type(self, channel):
        assert channel.channel_type == "browser"

    def test_is_open_initially(self, channel):
        assert channel.is_open is True

    def test_ws_property(self, channel, mock_ws):
        assert channel.ws is mock_ws

    @pytest.mark.asyncio
    async def test_send_event(self, channel, mock_ws):
        event = {"type": "assistant", "content": "hello"}
        await channel.send_event(event)
        mock_ws.send_text.assert_called_once_with(json.dumps(event))

    @pytest.mark.asyncio
    async def test_send_event_marks_channel_closed_on_disconnect(self, channel, mock_ws):
        mock_ws.send_text = AsyncMock(side_effect=WebSocketDisconnect())
        await channel.send_event({"type": "assistant", "content": "hello"})
        assert channel.is_open is False

    @pytest.mark.asyncio
    async def test_send_event_after_close_is_noop(self, channel, mock_ws):
        await channel.close()
        await channel.send_event({"type": "test"})
        mock_ws.send_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_close(self, channel, mock_ws):
        await channel.close()
        assert channel.is_open is False
        mock_ws.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_idempotent(self, channel, mock_ws):
        await channel.close()
        await channel.close()
        # Only called once
        mock_ws.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_handles_exception(self, mock_ws):
        mock_ws.close = AsyncMock(side_effect=Exception("ws error"))
        channel = WebSocketChannel(mock_ws)
        await channel.close()
        assert channel.is_open is False


# ---------------------------------------------------------------------------
# Internal-message filtering on WebSocketChannel
# ---------------------------------------------------------------------------


class TestWebSocketChannelInternalFilter:
    """The WebSocketChannel default hides tool_use / tool_result events,
    matching the browser toggle which also defaults to hide. The browser
    flips the channel's preference via the ``set_internal_visibility``
    control message."""

    @pytest.fixture
    def mock_ws(self):
        ws = AsyncMock()
        ws.send_text = AsyncMock()
        ws.close = AsyncMock()
        return ws

    def test_default_is_hide(self, mock_ws):
        ch = WebSocketChannel(mock_ws)
        assert ch.show_internal is False

    @pytest.mark.asyncio
    async def test_drops_tool_use_content_block_start(self, mock_ws):
        ch = WebSocketChannel(mock_ws)
        await ch.send_event(
            {"type": "content_block_start", "content_block": {"type": "tool_use", "id": "t1"}}
        )
        mock_ws.send_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_drops_tool_result_content_block_start(self, mock_ws):
        ch = WebSocketChannel(mock_ws)
        await ch.send_event(
            {
                "type": "content_block_start",
                "content_block": {"type": "tool_result", "tool_use_id": "t1", "content": "ok"},
            }
        )
        mock_ws.send_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_drops_delta_inside_tool_use_block(self, mock_ws):
        ch = WebSocketChannel(mock_ws)
        # Open a tool_use block (filtered out)
        await ch.send_event(
            {"type": "content_block_start", "content_block": {"type": "tool_use", "id": "t1"}}
        )
        # Delta inside that block should also be filtered
        await ch.send_event(
            {
                "type": "content_block_delta",
                "delta": {"type": "input_json_delta", "partial_json": "{}"},
            }
        )
        # And the closing stop too
        await ch.send_event({"type": "content_block_stop"})
        mock_ws.send_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_passes_through_text_block_around_tool_use(self, mock_ws):
        ch = WebSocketChannel(mock_ws)
        # Text block opens, streams, closes — all forwarded
        await ch.send_event({"type": "content_block_start", "content_block": {"type": "text"}})
        await ch.send_event(
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hi"}}
        )
        await ch.send_event({"type": "content_block_stop"})
        # Then a tool_use block — fully suppressed
        await ch.send_event(
            {"type": "content_block_start", "content_block": {"type": "tool_use", "id": "t1"}}
        )
        await ch.send_event(
            {
                "type": "content_block_delta",
                "delta": {"type": "input_json_delta", "partial_json": "{"},
            }
        )
        await ch.send_event({"type": "content_block_stop"})
        # Then another text block — forwarded again
        await ch.send_event({"type": "content_block_start", "content_block": {"type": "text"}})
        # 4 sends: text-start, text-delta, text-stop, second text-start
        assert mock_ws.send_text.call_count == 4

    @pytest.mark.asyncio
    async def test_strips_tool_use_blocks_from_assistant_event(self, mock_ws):
        ch = WebSocketChannel(mock_ws)
        await ch.send_event(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "hello"},
                        {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}},
                    ]
                },
            }
        )
        mock_ws.send_text.assert_called_once()
        sent = json.loads(mock_ws.send_text.call_args.args[0])
        assert sent["message"]["content"] == [{"type": "text", "text": "hello"}]

    @pytest.mark.asyncio
    async def test_drops_assistant_event_with_only_tool_use(self, mock_ws):
        ch = WebSocketChannel(mock_ws)
        await ch.send_event(
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]
                },
            }
        )
        mock_ws.send_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_drops_user_event_with_only_tool_results(self, mock_ws):
        ch = WebSocketChannel(mock_ws)
        await ch.send_event(
            {
                "type": "user",
                "message": {
                    "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "out"}]
                },
            }
        )
        mock_ws.send_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_show_internal_forwards_everything(self, mock_ws):
        ch = WebSocketChannel(mock_ws, show_internal=True)
        await ch.send_event(
            {"type": "content_block_start", "content_block": {"type": "tool_use", "id": "t1"}}
        )
        await ch.send_event({"type": "content_block_delta", "delta": {"type": "input_json_delta"}})
        await ch.send_event({"type": "content_block_stop"})
        assert mock_ws.send_text.call_count == 3

    @pytest.mark.asyncio
    async def test_set_show_internal_toggles_filter(self, mock_ws):
        ch = WebSocketChannel(mock_ws)
        # Default hidden — drops tool_use
        await ch.send_event(
            {"type": "content_block_start", "content_block": {"type": "tool_use", "id": "t1"}}
        )
        assert mock_ws.send_text.call_count == 0

        # Toggle to show
        ch.set_show_internal(True)
        await ch.send_event(
            {"type": "content_block_start", "content_block": {"type": "tool_use", "id": "t2"}}
        )
        assert mock_ws.send_text.call_count == 1


# ---------------------------------------------------------------------------
# format_telegram_event
# ---------------------------------------------------------------------------


class TestFormatTelegramEvent:
    """Tests for CLI event formatting for Telegram."""

    def test_content_block_delta_with_text(self):
        event = {
            "type": "content_block_delta",
            "delta": {"text": "Hello world"},
        }
        assert format_telegram_event(event) == "Hello world"

    def test_content_block_delta_empty(self):
        event = {
            "type": "content_block_delta",
            "delta": {"text": ""},
        }
        assert format_telegram_event(event) is None

    def test_assistant_text_block(self):
        event = {
            "type": "assistant",
            "content": [{"type": "text", "text": "Here is the answer"}],
        }
        assert format_telegram_event(event) == "Here is the answer"

    def test_assistant_tool_use_with_command(self):
        event = {
            "type": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "name": "Bash",
                    "input": {"command": "ls -la"},
                }
            ],
        }
        result = format_telegram_event(event)
        assert "[tool] Bash: ls -la" in result

    def test_assistant_tool_use_with_file_path(self):
        event = {
            "type": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "name": "Read",
                    "input": {"file_path": "src/main.py"},
                }
            ],
        }
        result = format_telegram_event(event)
        assert "[tool] Read: src/main.py" in result

    def test_assistant_tool_use_no_detail(self):
        event = {
            "type": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "name": "SomeCustomTool",
                    "input": {"foo": "bar"},
                }
            ],
        }
        result = format_telegram_event(event)
        assert result == "[tool] SomeCustomTool"

    def test_assistant_thinking_block_skipped(self):
        event = {
            "type": "assistant",
            "content": [{"type": "thinking", "text": "thinking..."}],
        }
        assert format_telegram_event(event) is None

    def test_assistant_mixed_content(self):
        event = {
            "type": "assistant",
            "content": [
                {"type": "thinking", "text": "thinking..."},
                {"type": "text", "text": "Result"},
                {
                    "type": "tool_use",
                    "name": "Bash",
                    "input": {"command": "echo hi"},
                },
            ],
        }
        result = format_telegram_event(event)
        assert "Result" in result
        assert "[tool] Bash: echo hi" in result
        assert "thinking" not in result

    def test_assistant_non_list_content(self):
        event = {"type": "assistant", "content": "just a string"}
        assert format_telegram_event(event) is None

    def test_error_event_string(self):
        event = {"type": "error", "content": "Something failed"}
        result = format_telegram_event(event)
        assert "[error]" in result
        assert "Something failed" in result

    def test_error_event_dict(self):
        event = {"type": "error", "error": {"message": "Bad request"}}
        result = format_telegram_event(event)
        assert "[error]" in result
        assert "Bad request" in result

    def test_result_event_skipped(self):
        event = {"type": "result", "stop_reason": "end_turn"}
        assert format_telegram_event(event) is None

    def test_system_event(self):
        event = {"type": "system", "content": "Connected"}
        result = format_telegram_event(event)
        assert "[system] Connected" in result

    def test_system_event_no_content(self):
        event = {"type": "system"}
        assert format_telegram_event(event) is None

    def test_unknown_event(self):
        event = {"type": "some_unknown_type"}
        assert format_telegram_event(event) is None

    def test_assistant_message_nested_content(self):
        """Test assistant event with content nested under message key."""
        event = {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "Nested hello"}]},
        }
        result = format_telegram_event(event)
        assert result == "Nested hello"

    def test_user_confirmed_event(self):
        event = {"type": "user_confirmed", "content": "Kick off the run"}
        assert format_telegram_event(event) == "[prompt] Kick off the run"

    def test_user_confirmed_external_source_skipped(self):
        event = {
            "type": "user_confirmed",
            "content": "Who is in this flock?",
            "source": "telegram",
            "metadata": {"source_platform": "telegram"},
        }
        assert format_telegram_event(event) is None

    def test_user_confirmed_source_platform_skipped(self):
        event = {
            "type": "user_confirmed",
            "content": "Confirm this step",
            "source": "browser",
            "metadata": {"source_platform": "slack"},
        }
        assert format_telegram_event(event) is None

    def test_user_confirmed_without_content_skipped(self):
        event = {
            "type": "user_confirmed",
            "content": "",
            "source": "browser",
        }
        assert format_telegram_event(event) is None

    def test_room_message_public(self):
        event = {
            "type": "room_message",
            "participant": {"persona": "coder", "display_name": "coder"},
            "content": "I found the issue.",
            "visibility": "public",
        }
        assert format_telegram_event(event) == "[coder] I found the issue."

    def test_room_message_internal_skipped(self):
        event = {
            "type": "room_message",
            "participant": {"persona": "coder"},
            "content": "internal detail",
            "visibility": "internal",
        }
        assert format_telegram_event(event) is None

    def test_room_message_without_content_skipped(self):
        event = {
            "type": "room_message",
            "participant": {"persona": "coder"},
            "content": "",
            "visibility": "public",
        }
        assert format_telegram_event(event) is None

    def test_room_message_outcome_block_is_left_to_typed_outcome_event(self):
        event = {
            "type": "room_message",
            "participant": {"persona": "claude-mimir-researcher"},
            "visibility": "public",
            "content": (
                "---outcome---\n"
                "summary: Wrote research/agent-mimir-page-standards.md\n"
                "page_path: research/agent-mimir-page-standards.md\n"
                "---end---"
            ),
        }
        assert format_telegram_event(event) is None

    def test_room_notification_help_needed(self):
        event = {
            "type": "room_notification",
            "notificationType": "help_needed",
            "participant": {"persona": "reviewer"},
            "summary": "Need human input",
            "reason": "merge conflict",
            "attempted": ["replayed the failed merge"],
            "recommendation": "decide which patch to keep",
        }
        result = format_telegram_event(event)
        assert "reviewer needs your input" in result
        assert "Need human input" in result
        assert "Why: merge conflict" in result
        assert "Already tried:\n- replayed the failed merge" in result
        assert "Suggested next step: decide which patch to keep" in result

    def test_room_outcome_rendered(self):
        event = {
            "type": "room_outcome",
            "participant": {"persona": "verifier"},
            "eventType": "verification.completed",
            "verdict": "conditional",
            "summary": "One issue found",
            "fields": {"checks_passed": 12, "checks_failed": 1},
        }
        result = format_telegram_event(event)
        assert "verifier — Verification completed" in result
        assert "Verdict: Conditional" in result
        assert "Checks passed: 12" in result

    def test_judgment_outcome_is_projected_as_operator_card(self):
        event = {
            "type": "room_outcome",
            "participant": {"persona": "Ivaldi"},
            "eventType": "valkyrie.judgment.proposed",
            "verdict": "judged",
            "fields": {
                "decision": "investigate",
                "operational_state": "nominal",
                "wakefulness": "wakeful",
                "tier": "present",
                "action_authority": "autonomous",
                "action_capability": "none",
                "signal_refs": ["skuld:directed_message"],
                "rationale": "One verified learned tool is available.",
                "evidence": [
                    "Catalog metadata reports verification=passed.",
                    "The tool has tests.",
                ],
                "recommended_action": "Use the verified tool when its capability matches.",
                "target_surfaces": ["learned_tools"],
                "confidence": 0.91,
                "working_state": {
                    "attempts": ["This verbose audit state must stay out of Telegram."]
                },
            },
        }

        result = format_telegram_event(event)

        assert result.startswith("🔎 **Ivaldi — Judgment: Investigating**")
        assert "**Recommended action**" in result
        assert "**Why**" in result
        assert ">! One verified learned tool is available." in result
        assert "**Evidence (2)**" in result
        assert ">! • Catalog metadata reports verification=passed." in result
        assert ">! Status: Present · Nominal · Wakeful" in result
        assert ">! Authority: Autonomous" in result
        assert ">! Confidence: 91%" in result
        assert "Verdict: Judged" not in result
        assert "Action capability: none" not in result
        assert "working_state" not in result
        assert "verbose audit state" not in result

    def test_judgment_operator_card_stays_within_one_telegram_message(self):
        long_text = "diagnostic evidence " * 200
        event = {
            "type": "room_outcome",
            "participant": {"persona": "Ivaldi"},
            "eventType": "valkyrie.judgment.proposed",
            "summary": long_text,
            "fields": {
                "decision": "investigate",
                "question": long_text,
                "recommended_action": long_text,
                "rationale": long_text,
                "capability_gap": long_text,
                "tool_evolution_plan": long_text,
                "open_questions": [long_text] * 5,
                "evidence": [long_text] * 5,
                "target_surfaces": [long_text] * 5,
            },
        }

        rendered = render_telegram_html(format_telegram_event(event) or "")

        assert len(rendered) <= 4096
        assert "<b>Evidence (2 of 5)</b>" in rendered

    def test_non_judgment_outcome_with_rationale_keeps_generic_format(self):
        event = {
            "type": "room_outcome",
            "participant": {"persona": "verifier"},
            "eventType": "verification.completed",
            "verdict": "conditional",
            "fields": {"rationale": "One issue remains."},
        }

        result = format_telegram_event(event)

        assert result.startswith("verifier — Verification completed")
        assert "Verdict: Conditional" in result
        assert "Rationale: One issue remains." in result

    def test_room_outcome_serializes_list_fields(self):
        event = {
            "type": "room_outcome",
            "participant": {"persona": "verifier"},
            "fields": {"affected_files": ["a.py", "b.py"]},
        }
        result = format_telegram_event(event)
        assert "verifier — Outcome" in result
        assert "Affected files:\n- a.py\n- b.py" in result
        assert '["a.py", "b.py"]' not in result

    def test_room_outcome_renders_nested_fields_without_json(self):
        event = {
            "type": "room_outcome",
            "participant": {"persona": "resident"},
            "fields": {
                "working_state": {
                    "unknowns": ["Whether the service recovered."],
                }
            },
        }
        result = format_telegram_event(event)
        assert "Working state:" in result
        assert "Unknowns:" in result
        assert "- Whether the service recovered." in result
        assert '{"unknowns"' not in result

    def test_help_outcome_is_left_to_answerable_notification(self):
        event = {
            "type": "room_outcome",
            "participant": {"persona": "resident"},
            "verdict": "help_needed",
            "fields": {
                "continuation": "ask_operator",
                "question": "Can you inspect the affected service?",
            },
        }
        assert format_telegram_event(event) is None

    def test_silent_outcome_stays_in_room_instead_of_paging_telegram(self):
        event = {
            "type": "room_outcome",
            "participant": {"persona": "resident"},
            "fields": {
                "decision": "watch",
                "tier": "silent",
                "state_summary": "No operator attention required.",
            },
        }
        assert format_telegram_event(event) is None

    def test_room_outcome_renders_non_dict_fields(self):
        event = {
            "type": "room_outcome",
            "participant": {"persona": "verifier"},
            "fields": "fallback payload",
        }
        result = format_telegram_event(event)
        assert "fallback payload" in result

    def test_room_mesh_message_rendered(self):
        event = {
            "type": "room_mesh_message",
            "participant": {"persona": "coder"},
            "eventType": "review.completed",
            "direction": "delegate",
            "preview": "Please verify the route parity checklist changes.",
        }
        result = format_telegram_event(event)
        assert "[coder] delegate: review.completed" in result
        assert "Please verify the route parity checklist changes." in result

    def test_render_telegram_html_formats_markdown_table(self):
        rendered = render_telegram_html(
            "[Skuld] | Peer | Status |\n"
            "|------|--------|\n"
            "| **Skuld** | idle |\n"
            "| `coder` | blocked |"
        )
        assert rendered.startswith("<pre>")
        assert "Peer" in rendered
        assert "blocked" in rendered

    def test_render_telegram_html_formats_bold_code_and_links(self):
        rendered = render_telegram_html(
            "[Skuld] **blocked** on `README.md`; see [docs](https://example.com)"
        )
        assert "<b>blocked</b>" in rendered
        assert "<code>README.md</code>" in rendered
        assert '<a href="https://example.com">docs</a>' in rendered

    def test_render_telegram_html_formats_headings_lists_and_quotes(self):
        rendered = render_telegram_html("# Heading\n- bullet\n1. ordered\n> quoted")
        assert "<b>Heading</b>" in rendered
        assert "• bullet" in rendered
        assert "1. ordered" in rendered
        assert "<blockquote>quoted</blockquote>" in rendered

    def test_render_telegram_html_formats_expandable_details(self):
        rendered = render_telegram_html(
            "**Evidence**\n"
            ">! • First supporting item\n"
            ">! • Second supporting item\n"
            "\n"
            "**Details**\n"
            ">! Status: Present · Wakeful"
        )
        assert "<b>Evidence</b>" in rendered
        assert (
            "<blockquote expandable>• First supporting item\n• Second supporting item</blockquote>"
        ) in rendered
        assert "<blockquote expandable>Status: Present · Wakeful</blockquote>" in rendered


# ---------------------------------------------------------------------------
# split_message
# ---------------------------------------------------------------------------


class TestSplitMessage:
    """Tests for message splitting."""

    def test_short_message_not_split(self):
        text = "Hello world"
        assert split_message(text) == ["Hello world"]

    def test_exact_limit_not_split(self):
        text = "a" * 4096
        assert split_message(text) == [text]

    def test_long_message_split_at_newline(self):
        line = "x" * 100 + "\n"
        text = line * 50  # 5050 chars
        chunks = split_message(text, max_length=4096)
        assert len(chunks) == 2
        assert all(len(c) <= 4096 for c in chunks)

    def test_long_message_hard_break(self):
        text = "x" * 8192  # no newlines
        chunks = split_message(text, max_length=4096)
        assert len(chunks) == 2
        assert chunks[0] == "x" * 4096
        assert chunks[1] == "x" * 4096

    def test_empty_string(self):
        assert split_message("") == [""]

    def test_custom_max_length(self):
        text = "hello world how are you"
        chunks = split_message(text, max_length=10)
        assert all(len(c) <= 10 for c in chunks)
        assert "".join(chunks) == text


# ---------------------------------------------------------------------------
# TelegramChannel (without python-telegram-bot)
# ---------------------------------------------------------------------------


class TestTelegramChannelWithoutLib:
    """Tests for TelegramChannel when python-telegram-bot is not installed."""

    def test_raises_without_telegram_lib(self):
        with patch("skuld.channels.HAS_TELEGRAM", False):
            with pytest.raises(RuntimeError, match="python-telegram-bot"):
                TelegramChannel(bot_token="test", chat_id="123")


# ---------------------------------------------------------------------------
# TelegramChannel (with mocked telegram lib)
# ---------------------------------------------------------------------------


class TestTelegramChannelMocked:
    """Tests for TelegramChannel with mocked telegram bot."""

    @pytest.fixture
    def channel(self):
        with patch("skuld.channels.HAS_TELEGRAM", True):
            ch = TelegramChannel.__new__(TelegramChannel)
            ch._bot_token = "test-token"
            ch._chat_id = "12345"
            ch._notify_only = True
            ch._topic_mode = "shared_chat"
            ch._message_thread_id = None
            ch._inbound_chat_ids = {"12345"}
            ch._allow_any_inbound_chat = False
            ch._topic_name = "Volundr session"
            ch._on_message = None
            ch._bot = AsyncMock()
            ch._application = None
            ch._started = True
            ch._closed = False
            ch._text_buffer = []
            ch._flush_task = None
            ch._reply_targets = {}
            ch._delivered_event_ids = {}
            ch._active_failure_keys = {}
        return ch

    def test_channel_type(self, channel):
        assert channel.channel_type == "telegram"

    def test_is_open(self, channel):
        assert channel.is_open is True

    def test_is_open_when_closed(self, channel):
        channel._closed = True
        assert channel.is_open is False

    @pytest.mark.asyncio
    async def test_send_event_skips_raw_assistant_text(self, channel):
        event = {
            "type": "assistant",
            "content": [{"type": "text", "text": "Hello"}],
        }
        await channel.send_event(event)
        channel._bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_event_room_message_to_thread(self, channel):
        channel._message_thread_id = 77
        event = {
            "type": "room_message",
            "participant": {"persona": "resident"},
            "content": "Hello",
            "visibility": "public",
        }
        await channel.send_event(event)
        channel._bot.send_message.assert_called_once_with(
            chat_id="12345",
            text="[resident] Hello",
            parse_mode="HTML",
            message_thread_id=77,
        )

    @pytest.mark.asyncio
    async def test_send_event_room_message_uses_html_parse_mode(self, channel):
        event = {
            "type": "room_message",
            "participant": {"persona": "Skuld", "display_name": "Skuld"},
            "content": "**Blocked** on `README.md`; see [docs](https://example.com)",
            "visibility": "public",
        }
        await channel.send_event(event)
        _, kwargs = channel._bot.send_message.call_args
        assert kwargs["chat_id"] == "12345"
        assert kwargs["parse_mode"] == "HTML"
        assert "<b>Blocked</b>" in kwargs["text"]
        assert "<code>README.md</code>" in kwargs["text"]
        assert '<a href="https://example.com">docs</a>' in kwargs["text"]

    @pytest.mark.asyncio
    async def test_send_event_projects_judgment_as_expandable_operator_card(self, channel):
        await channel.send_event(
            {
                "type": "room_outcome",
                "participantId": "ivaldi",
                "participant": {"persona": "Ivaldi"},
                "eventType": "valkyrie.judgment.proposed",
                "verdict": "judged",
                "fields": {
                    "decision": "investigate",
                    "tier": "present",
                    "rationale": "The catalog contains one verified learned tool.",
                    "recommended_action": "Use it when the capability matches.",
                    "evidence": ["verification=passed", "has_tests=true"],
                    "working_state": {"attempts": ["internal detail"]},
                },
            }
        )

        channel._bot.send_message.assert_awaited_once()
        _, kwargs = channel._bot.send_message.call_args
        assert kwargs["parse_mode"] == "HTML"
        assert kwargs["text"].startswith("🔎 <b>Ivaldi — Judgment: Investigating</b>")
        assert "<b>Recommended action</b>" in kwargs["text"]
        assert (
            "<blockquote expandable>The catalog contains one verified learned tool.</blockquote>"
        ) in kwargs["text"]
        assert "working_state" not in kwargs["text"]
        assert "internal detail" not in kwargs["text"]

    @pytest.mark.asyncio
    async def test_send_event_room_message_table_uses_preformatted_block(self, channel):
        event = {
            "type": "room_message",
            "participant": {"persona": "Skuld", "display_name": "Skuld"},
            "content": (
                "| Peer | Status |\n|------|--------|\n| **Skuld** | idle |\n| `coder` | blocked |"
            ),
            "visibility": "public",
        }
        await channel.send_event(event)
        _, kwargs = channel._bot.send_message.call_args
        assert kwargs["parse_mode"] == "HTML"
        assert "<pre>" in kwargs["text"]
        assert "Peer" in kwargs["text"]
        assert "blocked" in kwargs["text"]

    @pytest.mark.asyncio
    async def test_help_needed_notification_uses_reply_markup(self, channel):
        event = {
            "type": "room_notification",
            "notificationType": "help_needed",
            "participant": {"persona": "resident"},
            "summary": "Need operator input",
            "reason": "needs_context",
            "recommendation": "Reply with the missing facts.",
        }

        await channel.send_event(event)

        _, kwargs = channel._bot.send_message.call_args
        assert kwargs["chat_id"] == "12345"
        assert kwargs["parse_mode"] == "HTML"
        assert "reply_markup" in kwargs

    @pytest.mark.asyncio
    async def test_exact_source_event_redelivery_is_sent_once(self, channel):
        event = {
            "type": "room_notification",
            "notificationType": "help_needed",
            "sourceEventId": "help-event-1",
            "participant": {"persona": "resident"},
            "summary": "Need operator input",
        }

        await channel.send_event(event)
        await channel.send_event(dict(event))

        channel._bot.send_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_repeated_backend_failure_is_sent_once_until_peer_recovers(self, channel):
        failure = {
            "type": "room_message",
            "participantId": "ivaldi",
            "participant": {"persona": "Ivaldi"},
            "content": "LLM backend unavailable",
            "visibility": "public",
            "error": True,
            "failureKind": "LLMError",
        }

        await channel.send_event({**failure, "sourceEventId": "failure-1"})
        await channel.send_event({**failure, "sourceEventId": "failure-2"})

        assert channel._bot.send_message.await_count == 1

        await channel.send_event(
            {
                "type": "room_outcome",
                "participantId": "ivaldi",
                "participant": {"persona": "Ivaldi"},
                "sourceEventId": "outcome-1",
                "outcome": {"decision": "watch", "state_summary": "Recovered"},
            }
        )
        await channel.send_event({**failure, "sourceEventId": "failure-3"})

        assert channel._bot.send_message.await_count == 3

    @pytest.mark.asyncio
    async def test_help_force_reply_retains_exact_peer_and_trace_context(self, channel):
        sent = MagicMock()
        sent.message_id = 321
        sent.chat.id = 12345
        channel._bot.send_message.return_value = sent
        event = {
            "type": "room_notification",
            "notificationType": "help_needed",
            "participantId": "ivaldi",
            "participant": {"persona": "Ivaldi"},
            "summary": "Need operator input",
            "reason": "needs_context",
            "recommendation": "Reply with the missing facts.",
            "trace_context": {"traceparent": "00-abc-def-01"},
        }
        await channel.send_event(event)

        on_message = AsyncMock()
        channel._on_message = on_message
        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.text = "The code is a slicer release-count mismatch."
        update.message.message_id = 400
        update.message.reply_to_message.message_id = 321
        update.message.message_thread_id = None

        await channel._handle_text_message(update, None)

        payload = on_message.await_args.args[0]
        assert payload["reply_to_message_id"] == 321
        assert payload["target_peer_id"] == "ivaldi"
        assert payload["trace_context"] == {"traceparent": "00-abc-def-01"}
        assert payload["reply_context"]["participant_id"] == "ivaldi"
        assert "Need operator input" in payload["reply_context"]["content"]

    @pytest.mark.asyncio
    async def test_room_message_reply_retains_peer_trace_and_prior_content(self, channel):
        sent = MagicMock()
        sent.message_id = 322
        sent.chat.id = 12345
        channel._bot.send_message.return_value = sent
        await channel.send_event(
            {
                "type": "room_message",
                "participantId": "ivaldi",
                "participant": {"persona": "Ivaldi"},
                "content": "**decision:** ignore\nrationale: transport check only",
                "visibility": "public",
                "trace_context": {"traceparent": "00-room-parent-01"},
            }
        )

        on_message = AsyncMock()
        channel._on_message = on_message
        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.text = "ignore indeed"
        update.message.message_id = 401
        update.message.reply_to_message.message_id = 322
        update.message.message_thread_id = None

        await channel._handle_text_message(update, None)

        payload = on_message.await_args.args[0]
        assert payload["target_peer_id"] == "ivaldi"
        assert payload["trace_context"] == {"traceparent": "00-room-parent-01"}
        assert payload["reply_context"] == {
            "event_type": "room_message",
            "content": "[Ivaldi] **decision:** ignore\nrationale: transport check only",
            "participant_id": "ivaldi",
        }

    @pytest.mark.asyncio
    async def test_send_event_skips_none_format(self, channel):
        event = {"type": "result", "stop_reason": "end_turn"}
        await channel.send_event(event)
        channel._bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_event_when_closed(self, channel):
        channel._closed = True
        await channel.send_event({"type": "system", "content": "hi"})
        channel._bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_event_when_not_started(self, channel):
        channel._started = False
        await channel.send_event({"type": "system", "content": "hi"})
        channel._bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_text_splits_long_messages(self, channel):
        long_text = "x" * 8192
        await channel._send_text(long_text)
        assert channel._bot.send_message.call_count == 2

    @pytest.mark.asyncio
    async def test_send_text_handles_error(self, channel):
        channel._bot.send_message = AsyncMock(side_effect=Exception("API error"))
        # Should not raise
        await channel._send_text("test")

    @pytest.mark.asyncio
    async def test_send_text_no_bot(self, channel):
        channel._bot = None
        await channel._send_text("test")
        # No error raised

    @pytest.mark.asyncio
    async def test_close(self, channel):
        await channel.close()
        assert channel.is_open is False
        assert channel._bot is None

    @pytest.mark.asyncio
    async def test_close_idempotent(self, channel):
        await channel.close()
        await channel.close()
        assert channel.is_open is False

    @pytest.mark.asyncio
    async def test_streaming_delta_is_not_sent_to_telegram(self, channel):
        delta_event = {
            "type": "content_block_delta",
            "delta": {"text": "chunk1"},
        }
        await channel.send_event(delta_event)
        channel._bot.send_message.assert_not_called()
        assert channel._text_buffer == []

    @pytest.mark.asyncio
    async def test_flush_buffer_empty(self, channel):
        """Flushing empty buffer should be a noop."""
        await channel._flush_buffer()
        channel._bot.send_message.assert_not_called()

    def test_validate_chat_correct(self, channel):
        update = MagicMock()
        update.effective_chat.id = 12345
        assert channel._validate_chat(update) is True

    def test_validate_chat_wrong_id(self, channel):
        update = MagicMock()
        update.effective_chat.id = 99999
        assert channel._validate_chat(update) is False

    def test_validate_chat_extra_inbound_id(self, channel):
        channel._inbound_chat_ids.add("99999")
        update = MagicMock()
        update.effective_chat.id = 99999
        assert channel._validate_chat(update) is True

    def test_validate_chat_allow_any_inbound(self, channel):
        channel._allow_any_inbound_chat = True
        update = MagicMock()
        update.effective_chat.id = 99999
        assert channel._validate_chat(update) is True

    def test_validate_chat_no_chat(self, channel):
        update = MagicMock()
        update.effective_chat = None
        assert channel._validate_chat(update) is False

    def test_validate_chat_no_attr(self, channel):
        update = object()
        assert channel._validate_chat(update) is False

    @pytest.mark.asyncio
    async def test_cmd_status_valid_chat(self, channel):
        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.reply_text = AsyncMock()
        await channel._cmd_status(update, None)
        update.message.reply_text.assert_called_once_with("[status] Session active")

    @pytest.mark.asyncio
    async def test_cmd_status_invalid_chat(self, channel):
        update = MagicMock()
        update.effective_chat.id = 99999
        update.message.reply_text = AsyncMock()
        await channel._cmd_status(update, None)
        update.message.reply_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_cmd_interrupt_with_callback(self, channel):
        on_message = AsyncMock()
        channel._on_message = on_message
        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.reply_text = AsyncMock()
        await channel._cmd_interrupt(update, None)
        on_message.assert_called_once_with({"type": "interrupt"})
        update.message.reply_text.assert_called_once_with("[interrupt] Interrupt signal sent")

    @pytest.mark.asyncio
    async def test_cmd_interrupt_no_callback(self, channel):
        channel._on_message = None
        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.reply_text = AsyncMock()
        await channel._cmd_interrupt(update, None)
        update.message.reply_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_cmd_model_with_name(self, channel):
        on_message = AsyncMock()
        channel._on_message = on_message
        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.text = "/model gpt-4"
        update.message.reply_text = AsyncMock()
        await channel._cmd_model(update, None)
        on_message.assert_called_once_with({"type": "set_model", "model": "gpt-4"})
        update.message.reply_text.assert_called_once_with("[model] Switching to gpt-4")

    @pytest.mark.asyncio
    async def test_cmd_model_no_name(self, channel):
        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.text = "/model"
        update.message.reply_text = AsyncMock()
        await channel._cmd_model(update, None)
        update.message.reply_text.assert_called_once_with("Usage: /model <model_name>")

    @pytest.mark.asyncio
    async def test_cmd_model_invalid_chat(self, channel):
        update = MagicMock()
        update.effective_chat.id = 99999
        update.message.reply_text = AsyncMock()
        await channel._cmd_model(update, None)
        update.message.reply_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_text_message_with_callback(self, channel):
        on_message = AsyncMock()
        channel._on_message = on_message
        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.text = "hello bot"
        await channel._handle_text_message(update, None)
        on_message.assert_called_once()
        payload = on_message.call_args.args[0]
        assert payload["type"] == "message"
        assert payload["content"] == "hello bot"
        assert payload["chat_id"] == "12345"

    @pytest.mark.asyncio
    async def test_handle_text_message_empty(self, channel):
        on_message = AsyncMock()
        channel._on_message = on_message
        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.text = ""
        await channel._handle_text_message(update, None)
        on_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_text_message_invalid_chat(self, channel):
        on_message = AsyncMock()
        channel._on_message = on_message
        update = MagicMock()
        update.effective_chat.id = 99999
        update.message.text = "hello"
        await channel._handle_text_message(update, None)
        on_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_callback_query_allow(self, channel):
        on_message = AsyncMock()
        channel._on_message = on_message
        query = AsyncMock()
        query.data = "perm:allow:req-123-abc"
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        update = MagicMock()
        update.callback_query = query
        await channel._handle_callback_query(update, None)
        on_message.assert_called_once_with(
            {
                "type": "permission_response",
                "request_id": "req-123-abc",
                "behavior": "allowOnce",
            }
        )
        query.answer.assert_called_once_with("Permission allowed")

    @pytest.mark.asyncio
    async def test_handle_callback_query_deny(self, channel):
        on_message = AsyncMock()
        channel._on_message = on_message
        query = AsyncMock()
        query.data = "perm:deny:req-456"
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        update = MagicMock()
        update.callback_query = query
        await channel._handle_callback_query(update, None)
        on_message.assert_called_once_with(
            {
                "type": "permission_response",
                "request_id": "req-456",
                "behavior": "deny",
            }
        )

    @pytest.mark.asyncio
    async def test_handle_callback_query_no_query(self, channel):
        update = MagicMock()
        update.callback_query = None
        await channel._handle_callback_query(update, None)

    @pytest.mark.asyncio
    async def test_handle_callback_query_not_perm(self, channel):
        query = MagicMock()
        query.data = "other:data"
        update = MagicMock()
        update.callback_query = query
        await channel._handle_callback_query(update, None)

    @pytest.mark.asyncio
    async def test_handle_callback_query_incomplete_data(self, channel):
        query = MagicMock()
        query.data = "perm:allow"
        update = MagicMock()
        update.callback_query = query
        await channel._handle_callback_query(update, None)

    @pytest.mark.asyncio
    async def test_handle_callback_query_no_on_message(self, channel):
        channel._on_message = None
        query = AsyncMock()
        query.data = "perm:allow:req-789"
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        update = MagicMock()
        update.callback_query = query
        await channel._handle_callback_query(update, None)
        query.answer.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_permission_request_no_bot(self, channel):
        channel._bot = None
        await channel.send_permission_request("req-1", "Bash", {"command": "ls"})

    @pytest.mark.asyncio
    async def test_send_permission_request_with_command(self, channel):
        ch_mod = sys.modules["skuld.channels"]

        orig_has = ch_mod.HAS_TELEGRAM
        ch_mod.HAS_TELEGRAM = True
        ch_mod.InlineKeyboardMarkup = MagicMock()
        ch_mod.InlineKeyboardButton = MagicMock()
        try:
            await channel.send_permission_request("req-1", "Bash", {"command": "ls -la"})
            channel._bot.send_message.assert_called_once()
        finally:
            ch_mod.HAS_TELEGRAM = orig_has

    @pytest.mark.asyncio
    async def test_send_permission_request_uses_thread(self, channel):
        ch_mod = sys.modules["skuld.channels"]

        orig_has = ch_mod.HAS_TELEGRAM
        ch_mod.HAS_TELEGRAM = True
        ch_mod.InlineKeyboardMarkup = MagicMock()
        ch_mod.InlineKeyboardButton = MagicMock()
        channel._message_thread_id = 55
        try:
            await channel.send_permission_request("req-1", "Bash", {"command": "ls -la"})
            _, kwargs = channel._bot.send_message.call_args
            assert kwargs["chat_id"] == "12345"
            assert kwargs["message_thread_id"] == 55
        finally:
            ch_mod.HAS_TELEGRAM = orig_has

    @pytest.mark.asyncio
    async def test_send_permission_request_with_file_path(self, channel):
        ch_mod = sys.modules["skuld.channels"]

        orig_has = ch_mod.HAS_TELEGRAM
        ch_mod.HAS_TELEGRAM = True
        ch_mod.InlineKeyboardMarkup = MagicMock()
        ch_mod.InlineKeyboardButton = MagicMock()
        try:
            await channel.send_permission_request("req-2", "Read", {"file_path": "foo.py"})
            channel._bot.send_message.assert_called_once()
        finally:
            ch_mod.HAS_TELEGRAM = orig_has

    @pytest.mark.asyncio
    async def test_send_permission_request_no_detail(self, channel):
        ch_mod = sys.modules["skuld.channels"]

        orig_has = ch_mod.HAS_TELEGRAM
        ch_mod.HAS_TELEGRAM = True
        ch_mod.InlineKeyboardMarkup = MagicMock()
        ch_mod.InlineKeyboardButton = MagicMock()
        try:
            await channel.send_permission_request("req-3", "Custom", {"other": "data"})
            channel._bot.send_message.assert_called_once()
        finally:
            ch_mod.HAS_TELEGRAM = orig_has

    @pytest.mark.asyncio
    async def test_send_permission_request_error(self, channel):
        ch_mod = sys.modules["skuld.channels"]

        orig_has = ch_mod.HAS_TELEGRAM
        ch_mod.HAS_TELEGRAM = True
        ch_mod.InlineKeyboardMarkup = MagicMock()
        ch_mod.InlineKeyboardButton = MagicMock()
        channel._bot.send_message = AsyncMock(side_effect=Exception("API fail"))
        try:
            await channel.send_permission_request("req-4", "Bash", {"command": "rm -rf /"})
        finally:
            ch_mod.HAS_TELEGRAM = orig_has

    @pytest.mark.asyncio
    async def test_close_with_application(self, channel):
        app = AsyncMock()
        app.stop = AsyncMock()
        app.shutdown = AsyncMock()
        app.updater = AsyncMock()
        app.updater.stop = AsyncMock()
        channel._application = app
        await channel.close()
        app.updater.stop.assert_called_once()
        app.stop.assert_called_once()
        app.shutdown.assert_called_once()
        assert channel._application is None

    @pytest.mark.asyncio
    async def test_close_with_application_error(self, channel):
        app = AsyncMock()
        app.stop = AsyncMock(side_effect=Exception("stop error"))
        app.updater = AsyncMock()
        app.updater.stop = AsyncMock()
        channel._application = app
        await channel.close()
        assert channel._application is None
        assert channel.is_open is False

    @pytest.mark.asyncio
    async def test_close_with_application_no_updater(self, channel):
        app = AsyncMock()
        app.stop = AsyncMock()
        app.shutdown = AsyncMock()
        app.updater = None
        channel._application = app
        await channel.close()
        app.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_flush_buffer_with_content(self, channel):
        channel._text_buffer = ["hello ", "world"]
        await channel._flush_buffer()
        channel._bot.send_message.assert_called_once_with(
            chat_id="12345",
            text="hello world",
        )
        assert channel._text_buffer == []

    @pytest.mark.asyncio
    async def test_flush_buffer_with_content_to_thread(self, channel):
        channel._message_thread_id = 66
        channel._text_buffer = ["hello ", "world"]
        await channel._flush_buffer()
        channel._bot.send_message.assert_called_once_with(
            chat_id="12345",
            text="hello world",
            message_thread_id=66,
        )
        assert channel._text_buffer == []

    @pytest.mark.asyncio
    async def test_flush_buffer_cancels_pending_task(self, channel):
        task = MagicMock()
        task.done.return_value = False
        task.cancel = MagicMock()
        channel._flush_task = task
        channel._text_buffer = ["data"]
        await channel._flush_buffer()
        task.cancel.assert_called_once()
        assert channel._flush_task is None

    @pytest.mark.asyncio
    async def test_flush_buffer_whitespace_only(self, channel):
        channel._text_buffer = ["  ", "\n"]
        await channel._flush_buffer()
        channel._bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_start_creates_forum_topic_for_session_mode(self):
        with patch("skuld.channels.HAS_TELEGRAM", True), patch("skuld.channels.Bot") as bot_cls:
            bot = AsyncMock()
            bot.create_forum_topic = AsyncMock(return_value=MagicMock(message_thread_id=321))
            bot_cls.return_value = bot

            channel = TelegramChannel(
                bot_token="token",
                chat_id="12345",
                notify_only=True,
                topic_mode="topic_per_session",
                topic_name="niu-768 · 1234abcd",
            )

            await channel.start()

            bot.create_forum_topic.assert_awaited_once_with(
                chat_id="12345",
                name="niu-768 · 1234abcd",
            )
            assert channel._message_thread_id == 321

    @pytest.mark.asyncio
    async def test_start_fixed_topic_without_thread_falls_back(self):
        with patch("skuld.channels.HAS_TELEGRAM", True), patch("skuld.channels.Bot") as bot_cls:
            bot = AsyncMock()
            bot_cls.return_value = bot

            channel = TelegramChannel(
                bot_token="token",
                chat_id="12345",
                notify_only=True,
                topic_mode="fixed_topic",
            )

            await channel.start()

            assert channel._topic_mode == "shared_chat"
            bot.create_forum_topic.assert_not_called()

    @pytest.mark.asyncio
    async def test_scheduled_flush(self, channel):
        channel._text_buffer = ["buffered text"]
        with patch("skuld.channels.asyncio.sleep", new_callable=AsyncMock):
            await channel._scheduled_flush()
        channel._bot.send_message.assert_called_once_with(
            chat_id="12345",
            text="buffered text",
        )

    @pytest.mark.asyncio
    async def test_streaming_does_not_create_flush_task_for_telegram(self, channel):
        with patch("skuld.channels.asyncio.create_task") as mock_create:
            mock_create.return_value = MagicMock(done=MagicMock(return_value=False))
            delta_event = {
                "type": "content_block_delta",
                "delta": {"text": "chunk"},
            }
            await channel.send_event(delta_event)
            mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_raw_assistant_tool_call_is_not_sent_to_telegram(self, channel):
        active_task = MagicMock()
        active_task.done.return_value = False
        channel._flush_task = active_task
        event = {
            "type": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "name": "Bash",
                    "input": {"command": "uv run pytest"},
                }
            ],
        }

        await channel.send_event(event)

        channel._bot.send_message.assert_not_called()


class TestFormatTelegramEventExtra:
    """Additional tests for format_telegram_event edge cases."""

    def test_tool_use_with_pattern(self):
        event = {
            "type": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "name": "Grep",
                    "input": {"pattern": "TODO"},
                }
            ],
        }
        result = format_telegram_event(event)
        assert result == "[tool] Grep: TODO"

    def test_error_event_fallback_key(self):
        event = {"type": "error"}
        result = format_telegram_event(event)
        assert result == "[error] Unknown error"

    def test_system_event_empty_content(self):
        event = {"type": "system", "content": ""}
        assert format_telegram_event(event) is None

    def test_content_block_delta_no_delta(self):
        event = {"type": "content_block_delta"}
        assert format_telegram_event(event) is None


# ---------------------------------------------------------------------------
# ChannelRegistry
# ---------------------------------------------------------------------------


class TestChannelRegistry:
    """Tests for the channel registry."""

    @pytest.fixture
    def registry(self):
        return ChannelRegistry()

    @pytest.fixture
    def mock_channel(self):
        ch = AsyncMock(spec=MessageChannel)
        ch.channel_type = "test"
        ch.is_open = True
        return ch

    def test_empty_registry(self, registry):
        assert registry.count == 0
        assert registry.channels == []

    def test_add_channel(self, registry, mock_channel):
        registry.add(mock_channel)
        assert registry.count == 1
        assert mock_channel in registry.channels

    def test_remove_channel(self, registry, mock_channel):
        registry.add(mock_channel)
        registry.remove(mock_channel)
        assert registry.count == 0

    def test_remove_nonexistent_channel(self, registry, mock_channel):
        # Should not raise
        registry.remove(mock_channel)
        assert registry.count == 0

    def test_by_type(self, registry):
        ch1 = AsyncMock(spec=MessageChannel)
        ch1.channel_type = "browser"
        ch1.is_open = True
        ch2 = AsyncMock(spec=MessageChannel)
        ch2.channel_type = "telegram"
        ch2.is_open = True
        ch3 = AsyncMock(spec=MessageChannel)
        ch3.channel_type = "browser"
        ch3.is_open = True

        registry.add(ch1)
        registry.add(ch2)
        registry.add(ch3)

        browsers = registry.by_type("browser")
        assert len(browsers) == 2
        telegrams = registry.by_type("telegram")
        assert len(telegrams) == 1

    @pytest.mark.asyncio
    async def test_broadcast(self, registry):
        ch1 = AsyncMock(spec=MessageChannel)
        ch1.channel_type = "test1"
        ch1.is_open = True
        ch2 = AsyncMock(spec=MessageChannel)
        ch2.channel_type = "test2"
        ch2.is_open = True

        registry.add(ch1)
        registry.add(ch2)

        event = {"type": "test", "data": "hello"}
        await registry.broadcast(event)

        ch1.send_event.assert_called_once_with(event)
        ch2.send_event.assert_called_once_with(event)

    @pytest.mark.asyncio
    async def test_broadcast_removes_failed_channels(self, registry):
        ch1 = AsyncMock(spec=MessageChannel)
        ch1.channel_type = "good"
        ch1.is_open = True
        ch2 = AsyncMock(spec=MessageChannel)
        ch2.channel_type = "bad"
        ch2.is_open = True
        ch2.send_event = AsyncMock(side_effect=Exception("send failed"))

        registry.add(ch1)
        registry.add(ch2)

        await registry.broadcast({"type": "test"})

        assert registry.count == 1
        assert ch1 in registry.channels

    @pytest.mark.asyncio
    async def test_broadcast_removes_closed_channels(self, registry):
        ch1 = AsyncMock(spec=MessageChannel)
        ch1.channel_type = "open"
        ch1.is_open = True
        ch2 = AsyncMock(spec=MessageChannel)
        ch2.channel_type = "closed"
        ch2.is_open = False

        registry.add(ch1)
        registry.add(ch2)

        await registry.broadcast({"type": "test"})

        assert registry.count == 1
        ch2.send_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_close_all(self, registry):
        ch1 = AsyncMock(spec=MessageChannel)
        ch1.channel_type = "test"
        ch1.is_open = True
        ch2 = AsyncMock(spec=MessageChannel)
        ch2.channel_type = "test"
        ch2.is_open = True

        registry.add(ch1)
        registry.add(ch2)

        await registry.close_all()

        ch1.close.assert_called_once()
        ch2.close.assert_called_once()
        assert registry.count == 0

    @pytest.mark.asyncio
    async def test_close_all_handles_errors(self, registry):
        ch1 = AsyncMock(spec=MessageChannel)
        ch1.channel_type = "test"
        ch1.is_open = True
        ch1.close = AsyncMock(side_effect=Exception("close error"))

        registry.add(ch1)
        await registry.close_all()
        assert registry.count == 0

    def test_channels_returns_copy(self, registry, mock_channel):
        registry.add(mock_channel)
        channels = registry.channels
        channels.clear()
        assert registry.count == 1


def test_room_message_with_whitespace_only_content_is_suppressed() -> None:
    """A model that returned no answer must not page the operator.

    Whitespace-only content is as empty as "" to a reader, but it passes a
    falsy check and used to render as a bare "[Ivaldi]" with nothing after it.
    """
    for blank in ("", " ", "\n", "  \n\t "):
        event = {
            "type": "room_message",
            "participant": {"display_name": "Ivaldi"},
            "content": blank,
        }
        assert format_telegram_event(event) is None, f"blank content {blank!r} was delivered"


def test_room_message_with_real_content_still_sends() -> None:
    event = {
        "type": "room_message",
        "participant": {"display_name": "Ivaldi"},
        "content": "printer stalled on layer 480",
    }
    assert format_telegram_event(event) == "[Ivaldi] printer stalled on layer 480"
