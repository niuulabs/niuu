"""F11b: the notify tool's question signals (cp-api NotifyQuestionFields, design §V12.2).

The control plane applies the producer rules (only the asking session may remind,
replace, resolve or withdraw; any project session may relay). Forge's job is to accept
the fields, refuse a bare uuid, and carry them intact to the read wire. The model's ask
cases (A1–A27, incl. R7-2b and R7-2c) are replayed through Forge's real path.
"""

from __future__ import annotations

import importlib.util
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from niuu.domain.notifications import (
    AboutQuestion,
    NotificationDraft,
    NotificationKind,
    NotificationSource,
    build_notification_turn,
    draft_from_log_payload,
)
from niuu.forge_mcp.guidance import QUESTION_GUIDANCE
from niuu.forge_mcp.tools import (
    _FEED_NOTIFY_DESCRIPTION,
    _FEED_NOTIFY_SCHEMA,
    TOOL_SPECS,
    _FeedNotifyArgs,
    _NotifyArgs,
)
from volundr.domain.notifications import Notification
from volundr.domain.services.notifications import NotificationService, _SessionContext

MODEL = Path(__file__).resolve().parents[1] / "fixtures" / "control_plane" / "model"
_spec = importlib.util.spec_from_file_location("cp_model_reference", MODEL / "reference.py")
REF = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(REF)
ASK_CASES = json.loads((MODEL / "asks.json").read_text())["cases"]

THOR_Q = "question:thor:" + str(uuid.UUID(int=30))


# ------------------------------------------------------------------ the wire shape


def test_host_qualified_ref_is_accepted_and_bare_uuid_refused():
    """R7-2c: question:thor:<uuid> validates; a bare uuid is refused."""
    assert AboutQuestion(id=THOR_Q, action="relay").id == THOR_Q
    with pytest.raises(ValidationError):
        AboutQuestion(id=str(uuid.UUID(int=30)), action="relay")
    with pytest.raises(ValidationError):
        AboutQuestion(id="question:thor:n1", action="relay")
    with pytest.raises(ValidationError):
        AboutQuestion(id=THOR_Q, action="close")
    with pytest.raises(ValidationError):
        AboutQuestion(id=THOR_Q, action="relay", host="thor")


def test_notify_args_accept_question_fields():
    args = _NotifyArgs.model_validate(
        {
            "kind": "attention",
            "title": "Pick one",
            "question": True,
            "about_question": {"id": THOR_Q, "action": "relay"},
        }
    )
    assert args.question is True
    assert args.about_question.action == "relay"
    feed = _FeedNotifyArgs.model_validate(
        {"kind": "info", "title": "Answered", "idempotency_key": "k-1", "question": False}
    )
    assert feed.question is False
    with pytest.raises(ValidationError):
        _NotifyArgs.model_validate(
            {"kind": "info", "title": "x", "about_question": {"id": "n1", "action": "relay"}}
        )


def test_tool_schema_and_description_state_the_producer_rule():
    session_notify = next(s for s in TOOL_SPECS if s.name == "notify").to_mcp()
    feed_notify = {"inputSchema": _FEED_NOTIFY_SCHEMA, "description": _FEED_NOTIFY_DESCRIPTION}
    for mcp in (session_notify, feed_notify):
        props = mcp["inputSchema"]["properties"]
        assert props["question"]["type"] == "boolean"
        about = props["about_question"]
        assert about["required"] == ["id", "action"]
        assert about["properties"]["id"]["pattern"] == "^question:[^:]+:[0-9a-f-]{36}$"
        assert about["properties"]["action"]["enum"] == [
            "remind",
            "replace",
            "relay",
            "resolved",
            "withdrawn",
        ]
        assert QUESTION_GUIDANCE in mcp["description"]
    assert "only the session that asked may remind, replace, resolved or withdrawn" in (
        QUESTION_GUIDANCE
    )
    assert "any session filed in the same project, on any host, may relay" in QUESTION_GUIDANCE
    assert "a bare uuid is refused" in QUESTION_GUIDANCE


def test_announcements_carry_no_question_keys():
    turn = build_notification_turn(
        NotificationDraft(kind="milestone", title="Shipped"),
        turn_id="t1",
        session_id=uuid.UUID(int=1),
        created_at="2026-10-22T00:00:00Z",
    )
    payload = turn["metadata"]["notification"]
    assert "question" not in payload and "about_question" not in payload


# ------------------------------------------------------------------ Forge's real path

_CONTEXT = _SessionContext(
    session_id=uuid.UUID(int=99),
    session_name="s",
    owner_id="damien",
    tenant_id=None,
    project_id=None,
    engine=None,
    model=None,
)


def _uuid_for(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"cp-model-id:{name}"))


def through_forge(step: dict) -> dict:
    """Send a model notice through Forge and rebuild the notice the control plane reads."""
    names: dict[str, str] = {}
    about = step.get("about_ask")
    draft_fields: dict = {
        "kind": step.get("kind", "attention"),
        "title": step.get("title") or "untitled",
        "correlation_id": step.get("correlation_id"),
    }
    if "ask" in step:
        draft_fields["question"] = step["ask"]
    if about:
        raw = str(about["id"])
        if raw.startswith("question:"):
            _, qhost, qid = raw.split(":", 2)
        else:
            qhost, qid = about.get("host", step.get("host", "thor")), raw
        names[_uuid_for(qid)] = qid
        draft_fields["about_question"] = {
            "id": f"question:{qhost}:{_uuid_for(qid)}",
            "action": about["action"],
        }
    draft = NotificationDraft.model_validate(draft_fields)
    turn = build_notification_turn(
        draft, turn_id="t", session_id=_CONTEXT.session_id, created_at="2026-10-22T00:00:00Z"
    )
    parsed = draft_from_log_payload({"type": "conversation.turn", "turn": turn})
    assert parsed is not None
    candidate = NotificationService._candidate(
        _CONTEXT,
        dedupe_key=f"turn:{step['id']}",
        draft=parsed[1],
        source=NotificationSource.AGENT,
        session_seq=1,
        metadata={"turn_id": "t"},
    )
    stored = Notification(
        **candidate.model_dump(exclude={"id"}),
        id=candidate.id,
        seq=1,
        created_at=datetime.now(UTC),
    )
    wire = stored.wire()
    out = {k: v for k, v in step.items() if k not in ("ask", "about_ask", "kind", "title")}
    out["kind"] = NotificationKind(wire["kind"]).value
    out["title"] = wire["title"]
    if wire["question"] is not None:
        out["ask"] = wire["question"]
    if wire["about_question"] is not None:
        _, qhost, qid = wire["about_question"]["id"].split(":", 2)
        out["about_ask"] = {
            "id": f"question:{qhost}:{names[qid]}",
            "action": wire["about_question"]["action"],
        }
    if "kind" not in step:
        out.pop("kind")
    return out


def _match(actual, expected, path="$"):
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{path}: expected an object, got {actual!r}"
        for key, value in expected.items():
            assert key in actual, f"{path}.{key}: missing"
            _match(actual[key], value, f"{path}.{key}")
        return
    if isinstance(expected, list) and any(isinstance(e, dict) for e in expected):
        assert isinstance(actual, list) and len(actual) == len(expected), path
        for i, (a, e) in enumerate(zip(actual, expected, strict=True)):
            _match(a, e, f"{path}[{i}]")
        return
    assert actual == expected, f"{path}: expected {expected!r}, got {actual!r}"


@pytest.mark.parametrize("case", ASK_CASES, ids=lambda c: f"{c['id']} {c['title'][:50]}")
def test_model_ask_case_through_forge(case):
    model = REF.Asks()
    for i, (step, expect) in enumerate(zip(case["steps"], case["expect"], strict=True)):
        model.apply(through_forge(step) if step["t"] == "notice" else step)
        if expect is not None:
            _match(model.view(), expect, f"step[{i}]")


def test_r7_2b_unrelated_session_cannot_resolve():
    model = REF.Asks()
    s20, s21 = str(uuid.UUID(int=20)), str(uuid.UUID(int=21))
    model.apply(through_forge({"t": "notice", "at": 0, "session": s20, "id": "q", "ask": True}))
    model.apply(
        through_forge(
            {
                "t": "notice",
                "at": 1,
                "session": s21,
                "id": "r",
                "about_ask": {"id": "q", "action": "resolved"},
            }
        )
    )
    view = model.view()
    assert view["asks"]["question:thor:q"]["resolution"] == "open"
    assert any(e.get("reason") == "not_asking_session" for e in view["errors"])


def test_r7_2c_m6_coordinator_relays_a_thor_question():
    model = REF.Asks()
    model.apply(
        through_forge(
            {"t": "notice", "at": 0, "host": "thor", "session": "s20", "id": "q", "ask": True}
        )
    )
    model.apply(
        through_forge(
            {
                "t": "notice",
                "at": 1,
                "host": "mini-m6",
                "session": "s22",
                "id": "r",
                "about_ask": {"id": "question:thor:q", "action": "relay"},
            }
        )
    )
    assert model.view()["members"].get("question:thor:q")
