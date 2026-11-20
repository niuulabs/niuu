"""G10 / F12d: Forge's work facts agree with the control plane's derived-state goldens.

For every golden with log rows, folding them with ``niuu.domain.work_facts`` gives the
reference's ``(last_work_at, tool_open)``; and the fields-only path (what the Code tab
reads from Forge's session object) gives each golden's expected state.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from niuu.domain.work_facts import (
    WorkFacts,
    fold_rows,
    hook_name,
    is_side_query_hook,
    is_work_row,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "control_plane"
CASES = json.loads((FIXTURES / "derived-state.json").read_text())["cases"]


def _reference():
    spec = importlib.util.spec_from_file_location(
        "derived_state_reference", FIXTURES / "derived_state_reference.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REF = _reference()


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def forge_row(row: dict) -> tuple[str, dict, datetime]:
    """A golden row as Forge stores it: kind = frame type; hooks carry hook_event_name."""
    payload: dict = {"type": row["kind"]}
    if row.get("hook"):
        payload["hook_event_name"] = row["hook"]
    return row["kind"], payload, _ts(row["ts"])


def forge_fields(case: dict) -> dict:
    """The session object Forge serves: the golden's Forge fields plus F12d's facts."""
    session = dict(case["session"])
    if case["rows"] is not None:
        facts = fold_rows(forge_row(r) for r in case["rows"])
        session["last_work_at"] = facts.last_work_at.isoformat() if facts.last_work_at else None
        session["tool_open"] = facts.tool_open
    return session


def test_twenty_goldens():
    assert len(CASES) == 20


@pytest.mark.parametrize(
    "case", [c for c in CASES if c["rows"] is not None], ids=lambda c: c["name"][:60]
)
def test_fold_matches_reference_work_facts(case):
    ref_last, ref_open = REF.work_facts(case["rows"])
    facts = fold_rows(forge_row(r) for r in case["rows"])
    assert facts.last_work_at == ref_last
    assert facts.tool_open == ref_open


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["name"][:60])
def test_fields_only_path_gives_expected_state(case):
    got = REF.derive(forge_fields(case), None, case["open_native_asks"], _ts(case["now"]))
    expect = case["expect"]
    assert got["state"] == expect["state"]
    if "basis" in expect:
        assert got["basis"] == expect["basis"]
    if "last_work_at" in expect:
        assert got["last_work_at"] == _ts(expect["last_work_at"]).isoformat()


def test_predicate_matches_reference():
    kinds = sorted(REF.WORK_KINDS | {"system", "capabilities", "user_confirmed", "terminal_frame"})
    for kind in kinds:
        assert is_work_row(kind, {}) == REF.is_work({"kind": kind})
    for hook in sorted(REF.WORK_HOOKS | {"Notification", "PreCompact", "TeammateIdle"}):
        payload = {"type": "claude_hook", "hook_event_name": hook}
        assert is_work_row("claude_hook", payload) == REF.is_work(
            {"kind": "claude_hook", "hook": hook}
        )


def test_hook_name_shapes():
    assert hook_name({"hook_event_name": "Stop"}) == "Stop"
    assert hook_name({"payload": {"hook_event_name": "PreToolUse"}}) == "PreToolUse"
    assert hook_name({"payload": {"hookEventName": "PostToolUse"}}) == ""
    assert hook_name({"hook": "Stop"}) == ""
    assert hook_name({"payload": "x"}) == ""
    assert hook_name(None) == ""


def test_newest_source_time_wins_and_close_never_goes_negative():
    t1 = datetime(2026, 10, 21, 10, 59, tzinfo=UTC)
    t0 = datetime(2026, 10, 21, 9, 10, tzinfo=UTC)
    hook = {"type": "claude_hook", "hook_event_name": "PostToolUse"}
    facts = WorkFacts().fold("assistant", {}, t1).fold("claude_hook", hook, t0)
    assert facts.last_work_at == t1
    assert facts.open_tools == 0
    stop = {"type": "claude_hook", "hook_event_name": "Stop"}
    pre = {"type": "claude_hook", "hook_event_name": "PreToolUse"}
    facts = facts.fold("claude_hook", pre, t1).fold("claude_hook", pre, t1)
    assert facts.open_tools == 2
    assert facts.fold("claude_hook", stop, t1).open_tools == 0
    assert facts.fold("result", {}, t1).open_tools == 0
    assert facts.fold("terminal_frame", {}, t1) is facts


def _hook_frame(event: str, **inner) -> dict:
    return {"type": "claude_hook", "hook_event_name": event, "payload": inner}


def test_side_query_hooks_are_never_work():
    """LEX-2026: a prompt-suggestion side query's tools are always denied, so its
    PreToolUse never gets a PostToolUse; counted, it held a tool open until the next turn."""
    side = _hook_frame("PreToolUse", agent_id="a0a9ec517ee04652e", tool_name="Bash")
    stop = _hook_frame("SubagentStop", agent_id="a0a9ec517ee04652e", agent_type="")
    sub = _hook_frame("PreToolUse", agent_id="a1", agent_type="general-purpose")
    start = _hook_frame("SubagentStart", agent_id="a1")
    main = _hook_frame("PreToolUse", tool_name="Bash")
    assert is_side_query_hook(side) and is_side_query_hook(stop)
    assert not any(is_side_query_hook(f) for f in (sub, start, main, {"payload": "x"}, None))
    assert not is_work_row("claude_hook", side) and not is_work_row("claude_hook", stop)
    assert is_work_row("claude_hook", sub) and is_work_row("claude_hook", main)
    at = datetime(2026, 11, 10, 4, 47, 17, tzinfo=UTC)
    facts = fold_rows([("claude_hook", side, at), ("claude_hook", stop, at)])
    assert facts.last_work_at is None and not facts.tool_open


# Row-for-row with Forge's _SIDE_QUERY_HOOK_SQL (checked against PostgreSQL on Thor,
# LEX-2026 review): only non-empty JSON strings count as an agent id or type.
_PARITY = [
    ({"hook_event_name": "PreToolUse", "payload": {"agent_id": "a0a9"}}, True),
    ({"hook_event_name": "PreToolUse", "payload": {"agent_id": "a1", "agent_type": "Plan"}}, False),
    ({"hook_event_name": "PreToolUse", "payload": {"tool_name": "Bash"}}, False),
    ({"hook_event_name": "SubagentStart", "payload": {"agent_id": "a2"}}, False),
    ({"hook_event_name": "SubagentStop", "payload": {"agent_id": "a3", "agent_type": ""}}, True),
    ({"payload": {"hook_event_name": "PreToolUse", "agent_id": "a4"}}, True),
    ({"hook_event_name": "PreToolUse", "payload": "a string"}, False),
    ({"hook_event_name": "PreToolUse", "payload": ["x"]}, False),
    ({"hook_event_name": "PreToolUse", "payload": {"agent_id": 0}}, False),
    ({"hook_event_name": "PreToolUse", "payload": {"agent_id": ""}}, False),
    ({"hook_event_name": "PreToolUse", "payload": {"agent_id": "a5", "agent_type": 0}}, True),
    ({"hook_event_name": "PreToolUse", "payload": {"agent_id": "a6", "agent_type": False}}, True),
    ({"hook_event_name": "PreToolUse", "payload": {"agent_id": "a7", "agent_type": None}}, True),
    ({"hook_event_name": "PreToolUse", "payload": {"agent_id": ["x"]}}, False),
    ({"hook_event_name": "PreToolUse"}, False),
]


@pytest.mark.parametrize(("frame", "side"), _PARITY)
def test_side_query_rule_matches_the_ingest_sql(frame, side):
    assert is_side_query_hook({"type": "claude_hook", **frame}) is side
