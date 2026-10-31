"""Tests for the shared Forge notification contract."""

import uuid

import pytest
from pydantic import ValidationError

from niuu.domain.notifications import (
    AGENT_KINDS,
    MAX_BODY_CHARS,
    MAX_LINKS,
    MAX_TITLE_CHARS,
    NOTIFICATION_TOOL_NAME,
    NotificationDraft,
    NotificationKind,
    NotificationSeverity,
    build_notification_turn,
    draft_from_log_payload,
    notification_id,
    severity_rank,
    summarize_reply,
    turn_dedupe_key,
)

SESSION_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")


def _draft(**overrides) -> NotificationDraft:
    data = {"kind": "milestone", "title": "Tests green", "body": "All 42 pass."}
    data.update(overrides)
    return NotificationDraft.model_validate(data)


def test_notification_id_is_deterministic_per_key():
    key = turn_dedupe_key(SESSION_ID, "turn-1", NotificationKind.MILESTONE)
    assert notification_id(key) == notification_id(key)
    other = turn_dedupe_key(SESSION_ID, "turn-2", NotificationKind.MILESTONE)
    assert notification_id(key) != notification_id(other)


def test_dedupe_key_includes_kind():
    a = turn_dedupe_key(SESSION_ID, "t", NotificationKind.REPLY_READY)
    b = turn_dedupe_key(SESSION_ID, "t", NotificationKind.MILESTONE)
    assert a != b
    assert a == f"turn:{SESSION_ID}:t:reply_ready"


def test_draft_normalizes_title_whitespace_and_strips_body():
    draft = _draft(title="  Build \n  passed  ", body="  done \n")
    assert draft.title == "Build passed"
    assert draft.body == "done"


@pytest.mark.parametrize(
    "overrides",
    [
        {"title": "   "},
        {"title": "x" * (MAX_TITLE_CHARS + 1)},
        {"body": "x" * (MAX_BODY_CHARS + 1)},
        {"kind": "nonsense"},
        {"severity": "loud"},
        {"links": [{"label": "l", "url": "https://x"}] * (MAX_LINKS + 1)},
    ],
)
def test_draft_rejects_out_of_contract_values(overrides):
    with pytest.raises(ValidationError):
        _draft(**overrides)


def test_severity_rank_orders_and_tolerates_unknown():
    assert severity_rank("info") < severity_rank("success")
    assert severity_rank(NotificationSeverity.WARNING) < severity_rank("critical")
    assert severity_rank("unknown") == 0


def test_reply_ready_is_not_an_agent_kind():
    assert NotificationKind.REPLY_READY not in AGENT_KINDS
    assert NotificationKind.MILESTONE in AGENT_KINDS


def test_build_turn_round_trips_through_log_payload():
    draft = _draft(links=[{"label": "PR", "url": "https://example.test/pr/1", "kind": "pr"}])
    turn = build_notification_turn(
        draft, turn_id="nt_1", session_id=SESSION_ID, created_at="2026-09-23T00:00:00+00:00"
    )
    assert turn["role"] == "assistant"
    assert turn["content"] == "Tests green"
    assert turn["visibility"] == "public"
    assert "final_output" not in turn["metadata"]
    part = turn["parts"][0]
    assert part["name"] == NOTIFICATION_TOOL_NAME
    expected_id = notification_id(turn_dedupe_key(SESSION_ID, "nt_1", draft.kind))
    assert part["input"]["notification_id"] == str(expected_id)

    parsed = draft_from_log_payload({"type": "conversation.turn", "turn": turn})
    assert parsed is not None
    turn_id, parsed_draft = parsed
    assert turn_id == "nt_1"
    assert parsed_draft == draft


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {"type": "assistant"},
        {"type": "conversation.turn", "turn": "nope"},
        {"type": "conversation.turn", "turn": {"id": "t", "metadata": {"kind": "other"}}},
        {"type": "conversation.turn", "turn": {"id": "", "metadata": {"kind": "notification"}}},
        {
            "type": "conversation.turn",
            "turn": {"id": "t", "metadata": {"kind": "notification", "notification": "x"}},
        },
        {
            "type": "conversation.turn",
            "turn": {
                "id": "t",
                "metadata": {"kind": "notification", "notification": {"kind": "milestone"}},
            },
        },
        {
            "type": "conversation.turn",
            "turn": {
                "id": "t",
                "metadata": {
                    "kind": "notification",
                    "notification": {"kind": "reply_ready", "title": "spoofed"},
                },
            },
        },
    ],
)
def test_draft_from_log_payload_ignores_non_notifications(payload):
    assert draft_from_log_payload(payload) is None


def test_summarize_reply_uses_first_line_and_continues_after_it():
    title, body = summarize_reply(
        "\n\n## Done: shipped\nmore detail here", title_chars=50, body_chars=12
    )
    assert title == "Done: shipped"
    assert body == "more detail…"
    assert len(body) == 12


def test_summarize_reply_one_line_reply_has_no_repeated_body():
    assert summarize_reply("done", title_chars=50, body_chars=100) == ("done", "")


def test_summarize_reply_keeps_full_text_when_title_is_shortened():
    title, body = summarize_reply("a" * 30 + "\nnext", title_chars=10, body_chars=100)
    assert title == "a" * 9 + "…"
    assert body == "a" * 30 + "\nnext"


def test_summarize_reply_handles_empty_and_zero_limits():
    title, body = summarize_reply("   ", title_chars=10, body_chars=0)
    assert title == "Reply ready"
    assert body == ""
    title, _ = summarize_reply("x" * 30, title_chars=5, body_chars=5)
    assert title == "xxxx…"
