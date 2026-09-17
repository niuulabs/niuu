"""Regressions for the Fable catalog's question/permission double receipt."""

from __future__ import annotations

import copy
import json

import pytest

from tests.test_skuld.test_tmux_interactive_transport import (
    _ask_user_questions,
    _collect_events,
    _send_keys,
)
from tests.test_skuld.test_tmux_native_question_consumption import (
    NATIVE_ID,
    TOOL_ID,
    NativeQuestionTransport,
)

QUESTIONS = [
    {
        "header": "Accent",
        "question": "Which trace accent should be recorded in accent.json?",
        "options": [
            {"label": "Blue", "description": 'Record the accent as "blue".'},
            {"label": "Amber", "description": 'Record the accent as "amber".'},
        ],
        "multiSelect": False,
    }
]
QUESTION_MENU = """☐ Accent
Which trace accent should be recorded in accent.json?
❯ 1. Blue
     Record the accent as "blue".
  2. Amber
     Record the accent as "amber".
  3. Type something.
  4. Chat about this
Enter to select · ↑/↓ to navigate · Esc to cancel
"""
PERMISSION_MENU = """Do you want to proceed?
❯ 1. Yes
  2. Yes, and don't ask again for AskUserQuestion
  3. No, and tell Claude what to do differently (esc)
"""


@pytest.fixture
async def bridge(tmp_path):
    transport = NativeQuestionTransport(str(tmp_path))
    events = await _collect_events(transport)
    await transport.start()
    try:
        yield transport, events
    finally:
        await transport.stop()


async def hook(transport, event, *, tool="AskUserQuestion", questions=None):
    await transport.handle_claude_hook(
        {
            "hook_event_name": event,
            "tool_name": tool,
            "tool_input": {"questions": QUESTIONS if questions is None else questions},
            "tool_use_id": TOOL_ID,
            "session_id": NATIVE_ID,
            "transcript_path": str(transport.native_path),
        }
    )


@pytest.mark.parametrize("bad_answer", ["amber", "not-an-option", "no thanks", "always"])
async def test_catalog_permission_rejects_unlisted_answer_before_any_keys(bridge, bad_answer):
    transport, events = bridge
    transport.capture_stdout = QUESTION_MENU
    await hook(transport, "PreToolUse")
    await hook(transport, "PermissionRequest")
    actual, permission = _ask_user_questions(events)
    assert actual["metadata"]["control_kind"] == "question"
    assert permission["metadata"]["control_kind"] == "permission"

    before = list(transport.commands)
    with pytest.raises(ValueError, match="declared Claude permission option"):
        await transport.send_control(
            "ask_user_answer",
            request_id=permission["request_id"],
            answers=[{"question": "Allow AskUserQuestion?", "answer": bad_answer}],
        )
    assert transport.commands == before  # No capture or terminal write for invalid input.
    assert not any(e.get("type") == "ask_user_resolved" for e in events)

    transport.steps.append(("2", "❯\n"))
    transport.consumed = {"answers": {QUESTIONS[0]["question"]: "Amber"}}
    await transport.send_control(
        "ask_user_answer", request_id=actual["request_id"], answers=[{"answer": "amber"}]
    )
    assert _send_keys(transport) == ["2"]
    resolved = [e for e in events if e.get("type") == "ask_user_resolved"]
    assert [(e["request_id"], e["decision"]) for e in resolved] == [(actual["request_id"], "Amber")]
    assert permission["request_id"] in transport._pending_tty_prompts


async def test_catalog_bypass_duplicate_has_one_card_and_one_receipt(bridge):
    transport, events = bridge
    transport._skip_permissions = True
    transport.capture_stdout = QUESTION_MENU
    await hook(transport, "PreToolUse")
    await hook(transport, "PermissionRequest")
    questions = _ask_user_questions(events)
    assert len(questions) == 1
    assert any(e.get("type") == "claude_permission_request" for e in events)
    transport.steps.append(("2", "❯\n"))
    transport.consumed = {"answers": {QUESTIONS[0]["question"]: "Amber"}}
    await transport.send_control(
        "ask_user_answer", request_id=questions[0]["request_id"], answers=[{"answer": "Amber"}]
    )
    await hook(transport, "PostToolUse")
    assert _send_keys(transport) == ["2"]
    assert len([e for e in events if e.get("type") == "ask_user_resolved"]) == 1


@pytest.mark.parametrize("condition", ["different", "uncertain", "other_tool", "no_pending"])
async def test_bypass_never_suppresses_unproven_authorization(bridge, condition):
    transport, events = bridge
    transport._skip_permissions = True
    if condition != "no_pending":
        await hook(transport, "PreToolUse")
    if condition == "uncertain":
        next(iter(transport._pending_tty_prompts.values()))["answer_uncertain"] = True
    questions = copy.deepcopy(QUESTIONS)
    if condition == "different":
        questions[0]["question"] = "A different request"
    await hook(
        transport,
        "PermissionRequest",
        tool="Bash" if condition == "other_tool" else "AskUserQuestion",
        questions=questions,
    )
    assert _ask_user_questions(events)[-1]["metadata"]["control_kind"] == "permission"


@pytest.mark.parametrize("answer", ["Allow", "Allow & don't ask again", "Deny"])
async def test_permission_on_unrelated_question_menu_sends_nothing(bridge, answer):
    transport, events = bridge
    transport.capture_stdout = QUESTION_MENU
    await hook(transport, "PermissionRequest")
    rid = _ask_user_questions(events)[0]["request_id"]
    with pytest.raises(ValueError, match="live Claude menu"):
        await transport.send_control(
            "ask_user_answer", request_id=rid, answers=[{"answer": answer}]
        )
    assert _send_keys(transport) == []
    assert rid in transport._pending_tty_prompts
    assert not any(e.get("type") == "ask_user_resolved" for e in events)


@pytest.mark.parametrize(
    "answer,keys", [("Allow", ["1"]), ("Allow & don't ask again", ["2"]), ("Deny", ["Escape"])]
)
async def test_permission_options_survive_json_roundtrip_and_select_native_row(
    bridge, answer, keys
):
    transport, events = bridge
    transport.capture_stdout = PERMISSION_MENU
    await hook(transport, "PermissionRequest")
    frame = json.loads(json.dumps(_ask_user_questions(events)[0]))
    rid = frame["request_id"]
    pending = json.loads(json.dumps(transport._pending_tty_prompts[rid]))
    assert pending["questions"] == frame["questions"]
    assert pending["kind"] == frame["metadata"]["control_kind"]
    transport._pending_tty_prompts[rid] = pending
    transport.steps.append((keys[0], "❯\n"))
    await transport.send_control("ask_user_answer", request_id=rid, answers=[{"answer": answer}])
    assert _send_keys(transport) == keys


@pytest.mark.parametrize(
    "menu",
    ["❯ 1. Amber\n  2. Remove everything\n", "❯ 1. Blue\n  2. Amber warning\n"],
)
async def test_question_requires_own_declared_options_in_current_menu(bridge, menu):
    transport, events = bridge
    transport.capture_stdout = menu
    await hook(transport, "PreToolUse")
    rid = _ask_user_questions(events)[0]["request_id"]
    with pytest.raises(ValueError, match="pending question"):
        await transport.send_control(
            "ask_user_answer", request_id=rid, answers=[{"answer": "Amber"}]
        )
    assert _send_keys(transport) == []


async def test_last_visible_menu_wins_over_stale_matching_rows(bridge):
    transport, events = bridge
    transport.capture_stdout = QUESTION_MENU + PERMISSION_MENU
    await hook(transport, "PreToolUse")
    rid = _ask_user_questions(events)[0]["request_id"]
    with pytest.raises(ValueError, match="pending question"):
        await transport.send_control(
            "ask_user_answer", request_id=rid, answers=[{"answer": "Amber"}]
        )
    assert _send_keys(transport) == []
    captures = [args for args, _ in transport.commands if args[0] == "capture-pane"]
    assert captures and "-S" not in captures[-1]


@pytest.mark.parametrize("freeform_kind", ["explicit", "no_options"])
async def test_native_other_row_preserves_explicit_free_text(bridge, freeform_kind):
    transport, events = bridge
    questions = copy.deepcopy(QUESTIONS)
    text = "A custom accent $(not a shell command) ✨"
    answer = {"answer": text}
    if freeform_kind == "explicit":
        answer.update(free_text=text, option_indexes=[])
        transport.capture_stdout = QUESTION_MENU
    else:
        questions[0]["options"] = []
        transport.capture_stdout = (
            "☐ Accent\n" + questions[0]["question"] + "\n❯ 1. Type something.\n"
        )
    await hook(transport, "PreToolUse", questions=questions)
    rid = _ask_user_questions(events)[0]["request_id"]
    digit = "3" if freeform_kind == "explicit" else "1"
    editor = (
        transport.capture_stdout.replace("❯ 1. Blue", "  1. Blue").replace(
            f"  {digit}. Type something.", f"❯ {digit}. Type something."
        )
        + "\nctrl+g to edit in Vim\n"
    )
    transport.steps.extend(
        [(digit, editor), ("paste", editor.replace("Type something.", text)), ("Enter", "❯\n")]
    )
    transport.consumed = {"answers": {questions[0]["question"]: text}}
    await transport.send_control("ask_user_answer", request_id=rid, answers=[answer])
    assert _send_keys(transport) == [digit, "Enter"]
    assert transport.loaded_buffers == [text]
    assert not transport._confirm_submit_tasks
    resolved = [e for e in events if e.get("type") == "ask_user_resolved"]
    assert resolved[-1]["decision"] == text
    assert resolved[-1]["accepted"] is True


@pytest.mark.parametrize(
    "answer",
    [
        {"answer": "Orange"},
        {"answer": "Orange", "free_text": "different"},
        {"answer": "Orange", "free_text": "Orange", "option_indexes": [0]},
    ],
)
async def test_non_option_is_not_silently_treated_as_free_text(bridge, answer):
    transport, events = bridge
    transport.capture_stdout = QUESTION_MENU
    await hook(transport, "PreToolUse")
    rid = _ask_user_questions(events)[0]["request_id"]
    with pytest.raises(ValueError, match="declared Claude question option|custom text disagrees"):
        await transport.send_control("ask_user_answer", request_id=rid, answers=[answer])
    assert _send_keys(transport) == []
    assert transport.loaded_buffers == []


async def test_free_text_requires_native_other_row_before_any_key(bridge):
    transport, events = bridge
    transport.capture_stdout = "❯ 1. Blue\n  2. Amber\n"
    await hook(transport, "PreToolUse")
    rid = _ask_user_questions(events)[0]["request_id"]
    with pytest.raises(ValueError, match="live Claude menu"):
        await transport.send_control(
            "ask_user_answer",
            request_id=rid,
            answers=[{"answer": "Orange", "free_text": "Orange"}],
        )
    assert _send_keys(transport) == []
    assert transport.loaded_buffers == []


@pytest.mark.parametrize("plain", ["Yes", "Yes, allow this time"])
async def test_allow_cannot_silently_select_broader_native_grant(bridge, plain):
    transport, events = bridge
    transport.capture_stdout = f"❯ 1. {plain}\n  2. Yes, allow for this session\n  3. No\n"
    await hook(transport, "PermissionRequest")
    rid = _ask_user_questions(events)[0]["request_id"]
    transport.steps.append(("1", "❯\n"))
    await transport.send_control("ask_user_answer", request_id=rid, answers=[{"answer": "Allow"}])
    assert _send_keys(transport) == ["1"]


async def test_unknown_native_allow_scope_is_not_mapped_to_one_time_permission(bridge):
    transport, events = bridge
    transport.capture_stdout = "❯ 1. Yes, allow for this session\n  2. No\n"
    await hook(transport, "PermissionRequest")
    rid = _ask_user_questions(events)[0]["request_id"]
    with pytest.raises(ValueError, match="live Claude menu"):
        await transport.send_control(
            "ask_user_answer", request_id=rid, answers=[{"answer": "Allow"}]
        )
    assert _send_keys(transport) == []
