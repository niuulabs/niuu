"""The ``speak`` intent on agent notifications (LEX-1536): contract and Forge MCP."""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import ValidationError

from niuu.domain.notifications import (
    AGENT_KINDS,
    MAX_SPEAK_TEXT_CHARS,
    NotificationDraft,
    NotificationKind,
    NotificationSpeak,
    SpeakPriority,
    build_notification_turn,
    draft_from_log_payload,
    draft_payload,
)
from niuu.forge_mcp.rest_client import RestForgeClient
from niuu.forge_mcp.tools import _notification_summary
from tests.test_niuu.test_forge_mcp_tools import SELF, _feed_toolbox, _toolbox


def _notify_tool(toolbox) -> dict:
    return next(tool for tool in toolbox.list_tools() if tool["name"] == "notify")


class TestValidation:
    def test_text_is_trimmed_and_priority_defaults_to_now(self) -> None:
        speak = NotificationSpeak(text="  Tests pass.\n")
        assert (speak.text, speak.priority) == ("Tests pass.", SpeakPriority.NOW)

    def test_both_bounds_are_counted_after_trimming(self) -> None:
        longest = "a" * MAX_SPEAK_TEXT_CHARS
        assert NotificationSpeak(text=f"  {longest}  ").text == longest
        assert NotificationSpeak(text=" a ").text == "a"
        with pytest.raises(ValidationError, match="at most 600"):
            NotificationSpeak(text=longest + "a")
        for blank in ("", "   ", "\n\t"):
            with pytest.raises(ValidationError, match="at least 1"):
                NotificationSpeak(text=blank)

    @pytest.mark.parametrize("priority", ["later", "NOW", "", None])
    def test_unknown_priority_is_refused(self, priority) -> None:
        with pytest.raises(ValidationError, match="speak.priority"):
            NotificationDraft(kind="info", title="t", speak={"text": "hi", "priority": priority})

    def test_unknown_fields_and_nul_are_refused(self) -> None:
        with pytest.raises(ValidationError, match="speak.voice"):
            NotificationDraft(kind="info", title="t", speak={"text": "hi", "voice": "x"})
        with pytest.raises(ValidationError, match="NUL"):
            NotificationSpeak(text="a\x00b")

    @pytest.mark.parametrize("kind", sorted(AGENT_KINDS))
    def test_every_agent_kind_may_carry_it(self, kind) -> None:
        draft = NotificationDraft(
            kind=kind, title="t", speak={"text": "hi", "priority": "next_window"}
        )
        assert draft.speak == NotificationSpeak(text="hi", priority=SpeakPriority.NEXT_WINDOW)

    def test_reply_ready_may_not(self) -> None:
        with pytest.raises(ValidationError, match="speak is not allowed on reply_ready"):
            NotificationDraft(kind=NotificationKind.REPLY_READY, title="t", speak={"text": "hi"})


class TestWire:
    def test_payload_carries_speak_only_when_set(self) -> None:
        plain = draft_payload(NotificationDraft(kind="info", title="t"))
        assert "speak" not in plain
        spoken = draft_payload(NotificationDraft(kind="info", title="t", speak={"text": " hi "}))
        assert spoken["speak"] == {"text": "hi", "priority": "now"}

    def test_turn_round_trip_keeps_the_intent(self) -> None:
        draft = NotificationDraft(
            kind="decision", title="t", speak={"text": "Postgres.", "priority": "next_window"}
        )
        turn = build_notification_turn(
            draft, turn_id="nt-1", session_id=SELF, created_at="2026-10-27T10:00:00Z"
        )
        assert turn["metadata"]["notification"]["speak"] == {
            "text": "Postgres.",
            "priority": "next_window",
        }
        parsed = draft_from_log_payload({"type": "conversation.turn", "turn": turn})
        assert parsed is not None and parsed[1].speak == draft.speak


class TestMcpSchema:
    def test_transcript_and_feed_schemas_describe_speak(self) -> None:
        for toolbox in (_toolbox()[0], _feed_toolbox()[0]):
            speak = _notify_tool(toolbox)["inputSchema"]["properties"]["speak"]
            assert speak["type"] == "object" and speak["additionalProperties"] is False
            assert speak["required"] == ["text"]
            text = speak["properties"]["text"]
            assert (text["minLength"], text["maxLength"]) == (1, MAX_SPEAK_TEXT_CHARS)
            priority = speak["properties"]["priority"]
            assert priority["enum"] == ["now", "next_window"]
            assert priority["default"] == "now"
            assert "only records the intent" in speak["description"]


class TestMcpNotify:
    async def test_transcript_notify_hands_speak_to_the_host(self) -> None:
        toolbox, _, host = _toolbox()
        result = await toolbox.call(
            "notify", {"kind": "milestone", "title": "Done", "speak": {"text": "All done."}}
        )
        assert not result.is_error
        assert host.drafts[0].speak == NotificationSpeak(text="All done.")

    @pytest.mark.parametrize(
        ("speak", "message"),
        [
            ({"text": " "}, "speak.text"),
            ({"text": "a" * (MAX_SPEAK_TEXT_CHARS + 1)}, "speak.text"),
            ({"text": "hi", "priority": "soon"}, "speak.priority"),
            ({"priority": "now"}, "speak.text"),
        ],
    )
    async def test_bad_speak_is_refused_with_the_reason(self, speak, message) -> None:
        toolbox, _, host = _toolbox()
        result = await toolbox.call("notify", {"kind": "info", "title": "x", "speak": speak})
        assert result.is_error and message in result.text
        assert host.drafts == []

    async def test_feed_notify_submits_speak(self) -> None:
        toolbox, client = _feed_toolbox()
        result = await toolbox.call(
            "notify",
            {
                "kind": "attention",
                "title": "Blocked",
                "idempotency_key": "k-speak",
                "speak": {"text": "I am blocked.", "priority": "next_window"},
            },
        )
        assert not result.is_error
        name, (session_id, draft, key, _) = client.calls[-1]
        assert (name, session_id, key) == ("submit_notification", SELF, "k-speak")
        assert draft.speak == NotificationSpeak(text="I am blocked.", priority="next_window")

    async def test_rest_client_sends_the_wire_form(self) -> None:
        seen: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content))
            return httpx.Response(201, json={"id": "n1", "seq": 1})

        async def factory() -> httpx.AsyncClient:
            return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://f")

        client = RestForgeClient(http_client=factory, headers={}, timeout_s=5)
        draft = NotificationDraft(kind="info", title="t", speak={"text": "hi"})
        await client.submit_notification("s-1", draft, idempotency_key="k", instance_id=None)
        assert seen[0]["speak"] == {"text": "hi", "priority": "now"}

    def test_list_summaries_include_the_intent(self) -> None:
        speak = {"text": "hi", "priority": "now"}
        summary = _notification_summary(
            {"id": "n1", "kind": "info", "title": "t", "speak": speak}, max_body_chars=10
        )
        assert summary["speak"] == speak
        plain = _notification_summary({"id": "n1", "kind": "info", "title": "t"}, max_body_chars=10)
        assert "speak" not in plain
