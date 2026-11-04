"""LEX-1412: structured F11b questions keep the ordinary durable notify path."""

from __future__ import annotations

import copy
import json

import httpx
import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from niuu.domain.notifications import (
    MAX_ASK_BYTES,
    NotificationAsk,
    NotificationDraft,
    NotificationSource,
    build_notification_turn,
    draft_from_log_payload,
    draft_payload,
)
from niuu.forge_mcp.guidance import QUESTION_GUIDANCE
from niuu.forge_mcp.rest_client import RestForgeClient
from skuld.forge_mcp.skill import forge_notify_skill_markdown
from tests.test_adapters.test_postgres_notifications import _notification_row, _pool
from tests.test_niuu.test_forge_mcp_tools import SELF, _feed_toolbox, _toolbox
from tests.test_niuu.test_question_signals import _CONTEXT
from volundr.adapters.inbound.rest_notifications import NotificationResponse
from volundr.adapters.outbound.postgres_notifications import PostgresNotificationRepository
from volundr.domain.notifications import Notification
from volundr.domain.services.notifications import NotificationService

ASK = {
    "items": [
        {
            "id": "database",
            "kind": "question",
            "text": "Which database should the test use?",
            "header": "Database",
            "options": [
                {"label": "Postgres", "description": "Matches the deployed service."},
                {"label": "SQLite"},
            ],
            "multi_select": False,
            "allow_text": True,
        },
        {"id": "access", "kind": "action", "text": "Grant the test account access."},
    ]
}


def draft(**overrides):
    return NotificationDraft.model_validate(
        {
            "kind": "attention",
            "title": "Two decisions needed",
            "question": True,
            "ask": copy.deepcopy(ASK),
            **overrides,
        }
    )


def item(**overrides):
    return {"id": "q", "kind": "question", "text": "Pick one.", **overrides}


def test_mixed_questions_actions_and_omitted_defaults_round_trip():
    parsed = draft()
    assert parsed.ask.wire() == ASK
    assert draft_payload(parsed)["ask"] == ASK
    assert NotificationAsk.model_validate_json(parsed.ask.model_dump_json()).wire() == ASK
    assert "multi_select" not in parsed.ask.wire()["items"][1]
    assert "allow_text" not in parsed.ask.wire()["items"][1]


def test_legacy_question_and_announcements_keep_their_old_payloads():
    legacy = draft(ask=None)
    assert legacy.question_metadata() == {"question": True}
    assert "ask" not in draft_payload(legacy)
    announcement = NotificationDraft(kind="milestone", title="Done")
    assert announcement.question_metadata() == {}
    assert "ask" not in draft_payload(announcement)


def test_structured_text_and_exact_choice_identities_preserve_whitespace():
    ask = {
        "items": [
            item(
                text="  Keep the indentation.\n",
                header="  Exact heading  ",
                options=[
                    {"label": "A", "description": "  Description stays exact.\n"},
                    {"label": " A "},
                ],
            )
        ]
    }
    parsed = draft(ask=ask)
    assert parsed.ask.wire() == ask
    turn = build_notification_turn(
        parsed, turn_id="exact", session_id=_CONTEXT.session_id, created_at="2026-10-23T00:00:00Z"
    )
    _, restored = draft_from_log_payload({"type": "conversation.turn", "turn": turn})
    assert restored.ask.wire() == ask


@pytest.mark.parametrize("question", [None, False, 0, 1, "true"])
def test_ask_requires_question_exactly_true(question):
    with pytest.raises(ValidationError, match="requires question=true"):
        draft(question=question)


@pytest.mark.parametrize(
    "ask",
    [
        {},
        {"items": []},
        {"items": [item()] * 9},
        {"items": [item(), item()]},
        {"items": [item(id="")]},
        {"items": [item(id="x" * 65)]},
        {"items": [item(id="has space")]},
        {"items": [item(id="bad:id")]},
        {"items": [item(kind="execute")]},
        {"items": [item(text=" ")]},
        {"items": [item(text="x" * 2001)]},
        {"items": [item(text=2)]},
        {"items": [item(text="contains\x00nul")]},
        {"items": [item(header=" ")]},
        {"items": [item(header="x" * 81)]},
        {"items": [item(options=[])]},
        {"items": [item(options=[{"label": str(i)} for i in range(9)])]},
        {"items": [item(options=[{"label": "A"}, {"label": "A"}])]},
        {"items": [item(options=[{"label": " "}])]},
        {"items": [item(options=[{"label": "x" * 121}])]},
        {"items": [item(options=[{"label": "A", "description": "x" * 501}])]},
        {"items": [item(options=[{"label": "A", "description": "\x00"}])]},
        {"items": [item(options=[{"label": "A", "command": "execute"}])]},
        {"items": [item(multi_select="false")]},
        {"items": [item(allow_text=1)]},
        {"items": [item(allow_text=False)]},
        {"items": [item(header=None)]},
        {"items": [item(options=None)]},
        {"items": [item(multi_select=None)]},
        {"items": [item(allow_text=None)]},
        {"items": [item(extra=True)]},
        {"items": [item()], "extra": True},
    ],
)
def test_malformed_structured_payload_is_refused(ask):
    with pytest.raises(ValidationError):
        draft(ask=ask)


def test_total_payload_limit_counts_utf8_bytes_not_characters():
    ask = {"items": [item(id=f"q{i}", text="😀" * 2000) for i in range(5)]}
    encoded = json.dumps(ask, ensure_ascii=False, separators=(",", ":"))
    assert len(encoded) < MAX_ASK_BYTES < len(encoded.encode())
    with pytest.raises(ValidationError, match="32768 UTF-8 bytes"):
        draft(ask=ask)


def test_protocol_limits_accept_their_boundaries():
    parsed = draft(
        ask={
            "items": [
                item(
                    id="q" * 64,
                    text="x" * 2000,
                    header="h" * 80,
                    options=[
                        {"label": str(i) + "x" * 119, "description": "d" * 500} for i in range(8)
                    ],
                    multi_select=True,
                    allow_text=False,
                )
            ]
        }
    )
    assert len(parsed.ask.items[0].options) == 8
    assert len(draft(ask={"items": [item(id=f"q{i}") for i in range(8)]}).ask.items) == 8


@pytest.mark.parametrize("feed", [False, True])
def test_both_tool_schemas_and_source_skill_describe_structured_questions(feed):
    toolbox = (_feed_toolbox() if feed else _toolbox())[0]
    spec = next(t for t in toolbox.list_tools() if t["name"] == "notify")
    schema = spec["inputSchema"]
    payload = {"kind": "attention", "title": "Pick", "question": True, "ask": ASK}
    if feed:
        payload["idempotency_key"] = "question-1"
    validator = Draft202012Validator(schema)
    assert list(validator.iter_errors(payload)) == []
    # Cross-field validation belongs to the typed handler: a root conditional
    # makes native Claude silently omit notify from its connected MCP catalog.
    assert "requires question=true" in schema["properties"]["ask"]["description"]
    assert schema["properties"]["ask"]["properties"]["items"]["maxItems"] == 8
    assert QUESTION_GUIDANCE in spec["description"]
    skill = forge_notify_skill_markdown("forge")
    assert QUESTION_GUIDANCE in skill
    assert "ask={items:[...]}" in skill
    assert "never authorizes the app to execute" in skill


async def test_transcript_and_feed_tools_forward_the_same_typed_ask():
    toolbox, _, host = _toolbox()
    result = await toolbox.call("notify", draft_payload(draft()))
    assert not result.is_error
    assert host.drafts[0].ask.wire() == ASK
    feed, client = _feed_toolbox()
    result = await feed.call("notify", {**draft_payload(draft()), "idempotency_key": "ask-1"})
    assert not result.is_error
    name, (session_id, sent, key, _) = client.calls[-1]
    assert (name, session_id, key) == ("submit_notification", SELF, "ask-1")
    assert sent.ask.wire() == ASK


async def test_tool_validation_refuses_before_host_or_submit():
    for factory in (_toolbox, _feed_toolbox):
        values = factory()
        toolbox, client = values[:2]
        args = {**draft_payload(draft()), "question": False}
        if len(values) == 2:
            args["idempotency_key"] = "ask-1"
        assert (await toolbox.call("notify", args)).is_error
        assert client.calls == []
        if len(values) == 3:
            assert values[2].drafts == []


@pytest.mark.parametrize("feed", [False, True])
@pytest.mark.parametrize("question", [None, False, 0, 1, "true", "omitted"])
async def test_mcp_compatible_schema_keeps_strict_question_gate_before_side_effects(feed, question):
    values = (_feed_toolbox if feed else _toolbox)()
    toolbox, client = values[:2]
    args = {**draft_payload(draft()), "question": question}
    if question == "omitted":
        args.pop("question")
    if feed:
        args["idempotency_key"] = "must-not-publish"
    result = await toolbox.call("notify", args)
    assert result.is_error
    assert "requires question=true" in result.text
    assert client.calls == []
    if not feed:
        assert values[2].drafts == []


async def test_rest_client_carries_the_ask_unchanged():
    seen = []

    async def factory():
        def handle(request):
            seen.append(json.loads(request.content))
            return httpx.Response(201, json={"id": "n1", "seq": 1})

        return httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url="http://forge")

    client = RestForgeClient(http_client=factory, headers={}, timeout_s=5)
    await client.submit_notification("s-1", draft(), idempotency_key="ask-1", instance_id=None)
    assert seen[0]["ask"] == ASK


async def test_durable_turn_projector_jsonb_and_read_model_keep_the_same_ask():
    turn = build_notification_turn(
        draft(), turn_id="t", session_id=_CONTEXT.session_id, created_at="2026-10-23T00:00:00Z"
    )
    assert turn["metadata"]["notification"]["ask"] == ASK
    assert turn["parts"][0]["input"]["ask"] == ASK
    _, parsed = draft_from_log_payload(
        json.loads(json.dumps({"type": "conversation.turn", "turn": turn}))
    )
    candidate = NotificationService._candidate(
        _CONTEXT,
        dedupe_key="turn:t",
        draft=parsed,
        source=NotificationSource.AGENT,
        session_seq=1,
        metadata={"turn_id": "t"},
    )
    pool, conn = _pool()
    conn.fetchrow.return_value = _notification_row(candidate, 1)
    [stored] = await PostgresNotificationRepository(pool).project([candidate])
    assert stored.metadata["ask"] == ASK
    args = conn.fetchrow.await_args.args
    assert json.loads(args[18])["ask"] == ASK  # $18 is metadata
    restored = Notification.model_validate_json(stored.model_dump_json())
    assert restored.wire()["ask"] == ASK
    assert NotificationResponse.build(restored, 0).model_dump(mode="json")["ask"] == ASK


def test_action_can_report_done_without_choices_or_text():
    parsed = draft(ask={"items": [item(kind="action", allow_text=False)]})
    assert parsed.ask.items[0].allow_text is False
