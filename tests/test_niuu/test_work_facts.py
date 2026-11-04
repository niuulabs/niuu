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

from niuu.domain.work_facts import WorkFacts, fold_rows, hook_name, is_work_row

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
