"""Native Fable widget fixtures and exact tool-result acceptance regressions."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections import deque
from pathlib import Path

import pytest

from skuld.control_errors import ControlRecoveryError
from tests.test_skuld.test_tmux_interactive_transport import (
    FakeTmuxInteractiveTransport,
    _ask_user_questions,
    _collect_events,
    _send_keys,
)

SCREENS = Path(__file__).parents[1] / "support/forge/screens"
NATIVE_ID = "9ff04f26-c88b-4cc1-a6a4-904b2e1a23d0"
TOOL_ID = "toolu_native_question"
SINGLE = [
    {
        "header": "Label",
        "question": "Which label should be written?",
        "options": [{"label": "Blue"}, {"label": "Amber"}],
        "multiSelect": False,
    }
]
MULTI = [
    {
        "header": "Tone",
        "question": "Which tone?",
        "options": [{"label": "Blue"}, {"label": "Amber"}],
    },
    {
        "header": "Texture",
        "question": "Which texture?",
        "options": [{"label": "Matte"}, {"label": "Glossy"}],
    },
]
CHECKBOX = [
    {
        "header": "Colors",
        "question": "Which colors?",
        "options": [{"label": "Blue"}, {"label": "Amber"}, {"label": "Violet"}],
        "multiSelect": True,
    }
]
MIXED = [
    {
        "header": "Features",
        "question": "Which features?",
        "options": [{"label": "Auth first"}, {"label": "Search second"}],
        "multiSelect": True,
    }
]


def screen(name):
    return (SCREENS / f"claude-question-{name}.txt").read_text()


class NativeQuestionTransport(FakeTmuxInteractiveTransport):
    """Key-driven captured widgets; a result exists only after its native submit."""

    def __init__(self, workspace_dir):
        super().__init__(workspace_dir, resume_session_id=NATIVE_ID)
        self._menu_render_wait_s = 0.02
        self._menu_poll_step_s = 0.001
        self._question_result_wait_s = 0.02
        self.native_path = Path(workspace_dir) / f"{NATIVE_ID}.jsonl"
        self.native_path.write_text("")
        self.steps = deque()
        self.capture_sequence = deque()
        self.consumed = None
        self.on_submit = None

    def append_result(self, answers, *, is_error=False, tool_id=TOOL_ID, native_id=NATIVE_ID):
        block = {
            "type": "tool_result",
            "tool_use_id": tool_id,
            "content": "Native rejected answer"
            if is_error
            else json.dumps(answers, ensure_ascii=False),
            "is_error": is_error,
        }
        record = {
            "type": "user",
            "sessionId": native_id,
            "uuid": str(uuid.uuid4()),
            "message": {"content": [block]},
            "toolUseResult": {"answers": answers},
        }
        with self.native_path.open("a") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        return block

    async def _run_tmux(self, *args, **kwargs):
        if args and args[0] == "capture-pane" and self.capture_sequence:
            self.capture_stdout = self.capture_sequence.popleft()
        result = await super()._run_tmux(*args, **kwargs)
        action = (
            args[-1]
            if args and args[0] == "send-keys"
            else "paste"
            if args and args[0] == "paste-buffer"
            else None
        )
        if action is not None and self.steps:
            expected, next_screen = self.steps.popleft()
            assert action == expected, f"Unexpected native action {action!r}; wanted {expected!r}"
            self.capture_stdout = next_screen
            if not self.steps:
                if self.consumed is not None:
                    self.append_result(**self.consumed)
                if self.on_submit is not None:
                    await self.on_submit()
        elif action is not None:
            raise AssertionError(f"Unexpected extra native action: {action}")
        return result

    async def surface(self, questions=SINGLE):
        await self.handle_claude_hook(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "AskUserQuestion",
                "tool_input": {"questions": questions},
                "tool_use_id": TOOL_ID,
                "session_id": NATIVE_ID,
                "transcript_path": str(self.native_path),
            }
        )
        return next(iter(self._pending_tty_prompts))


@pytest.fixture
async def native_bridge(tmp_path):
    transport = NativeQuestionTransport(str(tmp_path))
    events = await _collect_events(transport)
    await transport.start()
    try:
        yield transport, events
    finally:
        await transport.stop()


def tool_results(events):
    return [
        block
        for e in events
        for block in e.get("message", {}).get("content", [])
        if block.get("type") == "tool_result"
    ]


def receipts(events):
    return [event for event in events if event.get("type") == "ask_user_resolved"]


async def test_native_other_waits_for_editor_paste_and_exact_consumption(native_bridge):
    transport, events = native_bridge
    transport.capture_stdout = screen("other-before-digit")
    rid = await transport.surface()
    transport.steps.extend(
        [
            ("3", screen("other-after-digit-250ms")),
            ("paste", screen("other-after-paste")),
            ("Enter", "❯\n"),
        ]
    )
    text = "Copper-transition café 東京"
    transport.consumed = {"answers": {SINGLE[0]["question"]: text}}
    await transport.send_control(
        "ask_user_answer",
        request_id=rid,
        answers=[{"answer": text, "free_text": text, "option_indexes": []}],
    )
    assert _send_keys(transport) == ["3", "Enter"]
    assert transport.loaded_buffers == [text]
    assert not transport.steps and not transport._confirm_submit_tasks
    assert len(tool_results(events)) == 1
    assert receipts(events) == [
        {
            "type": "ask_user_resolved",
            "event_type": "ask_user.resolved",
            "request_id": rid,
            "decision": text,
            "accepted": True,
            "metadata": {"source": "tmux_tty_bridge"},
        }
    ]
    assert events.index(
        next(e for e in events if e.get("message", {}).get("content") == tool_results(events))
    ) < events.index(receipts(events)[0])


async def test_native_two_pages_review_every_answer_before_submit(native_bridge):
    transport, events = native_bridge
    transport.capture_stdout = screen("multi-00-initial")
    rid = await transport.surface(MULTI)
    transport.steps.extend(
        [
            ("2", screen("multi-01-tone-digit")),
            ("3", screen("multi-02-texture-other-digit")),
            ("paste", screen("multi-03-texture-paste")),
            ("Enter", screen("multi-04-texture-enter")),
            ("1", "❯\n"),
        ]
    )
    text = "Copper multi café 東京"
    transport.consumed = {"answers": {"Which tone?": "Amber", "Which texture?": text}}
    # Explicit identities permit a client to serialize the answers in a different order.
    await transport.send_control(
        "ask_user_answer",
        request_id=rid,
        answers=[
            {"question": "Which texture?", "answer": text, "free_text": text},
            {"question": "Which tone?", "answer": "amber"},
        ],
    )
    assert _send_keys(transport) == ["2", "3", "Enter", "1"]
    assert transport.loaded_buffers == [text]
    assert receipts(events)[0]["accepted"] is True
    assert len(tool_results(events)) == 1


async def test_native_checkbox_toggles_wait_focus_and_review_selected_set(native_bridge):
    transport, events = native_bridge
    transport.capture_stdout = screen("multi-06-checkbox-initial")
    rid = await transport.surface(CHECKBOX)
    transport.steps.extend(
        [
            ("1", screen("multi-07-checkbox-blue")),
            ("3", screen("multi-08-checkbox-violet")),
            ("Down", screen("multi-11-checkbox-down1")),
            ("Down", screen("multi-12-checkbox-down2")),
            ("Down", screen("multi-13-checkbox-down3")),
            ("Down", screen("multi-14-checkbox-down4")),
            ("Enter", screen("multi-15-checkbox-submit")),
            ("1", "❯\n"),
        ]
    )
    transport.consumed = {"answers": {"Which colors?": "Violet, Blue"}}
    await transport.send_control(
        "ask_user_answer",
        request_id=rid,
        answers=[{"answer": ["Blue", "Violet"], "option_indexes": [0, 2]}],
    )
    assert _send_keys(transport) == ["1", "3", "Down", "Down", "Down", "Down", "Enter", "1"]
    assert receipts(events)[0]["accepted"] is True


async def test_other_never_pastes_or_submits_before_editor_transition(native_bridge):
    transport, events = native_bridge
    transport.capture_stdout = screen("other-before-digit")
    rid = await transport.surface()
    transport.steps.append(("3", screen("other-before-digit")))
    with pytest.raises(ControlRecoveryError, match="uncertain"):
        await transport.send_control(
            "ask_user_answer", request_id=rid, answers=[{"answer": "Copper", "free_text": "Copper"}]
        )
    assert _send_keys(transport) == ["3"]
    assert not transport.loaded_buffers and not receipts(events)
    assert _ask_user_questions(events)[-1]["metadata"]["answerable"] is False
    with pytest.raises(ControlRecoveryError, match="previous answer"):
        await transport.send_control(
            "ask_user_answer", request_id=rid, answers=[{"answer": "Amber"}]
        )
    assert _send_keys(transport) == ["3"]


async def test_other_does_not_submit_text_that_native_editor_did_not_render(native_bridge):
    transport, events = native_bridge
    transport.capture_stdout = screen("other-before-digit")
    rid = await transport.surface()
    transport.steps.extend(
        [("3", screen("other-after-digit-250ms")), ("paste", screen("other-after-digit-250ms"))]
    )
    with pytest.raises(ControlRecoveryError):
        await transport.send_control(
            "ask_user_answer", request_id=rid, answers=[{"answer": "Copper", "free_text": "Copper"}]
        )
    assert _send_keys(transport) == ["3"] and not receipts(events)


@pytest.mark.parametrize(
    "native_result",
    [
        {"answers": {"Which label should be written?": "Blue"}},
        {"answers": {"Other question": "Amber"}},
        {"answers": {}, "is_error": True},
    ],
)
async def test_native_negative_or_wrong_answer_never_gets_positive_receipt(
    native_bridge, native_result
):
    transport, events = native_bridge
    transport.capture_stdout = screen("other-before-digit")
    rid = await transport.surface()
    transport.steps.append(("2", "❯\n"))
    transport.consumed = native_result
    with pytest.raises(ValueError, match="did not consume"):
        await transport.send_control(
            "ask_user_answer", request_id=rid, answers=[{"answer": "Amber"}]
        )
    assert receipts(events)[0]["accepted"] is False
    assert rid not in transport._pending_tty_prompts
    assert len(tool_results(events)) == 1


@pytest.mark.parametrize("foreign", [{"tool_id": "another-call"}, {"native_id": str(uuid.uuid4())}])
async def test_foreign_native_result_does_not_prove_consumption(native_bridge, foreign):
    transport, events = native_bridge
    transport.capture_stdout = screen("other-before-digit")
    rid = await transport.surface()
    transport.steps.append(("2", "❯\n"))
    transport.consumed = {"answers": {SINGLE[0]["question"]: "Amber"}, **foreign}
    with pytest.raises(ControlRecoveryError):
        await transport.send_control(
            "ask_user_answer", request_id=rid, answers=[{"answer": "Amber"}]
        )
    assert not receipts(events) and not tool_results(events)


async def test_native_rejection_before_answer_is_reconciled_without_keys_and_finishes_idle(
    native_bridge,
):
    transport, events = native_bridge
    rid = await transport.surface()
    transport._turn_active = True
    transport.append_result({}, is_error=True)
    rejected = json.loads(transport.native_path.read_text().splitlines()[-1])
    interruption_id = str(uuid.uuid4())
    with transport.native_path.open("a") as stream:
        stream.write(
            json.dumps(
                {
                    "type": "user",
                    "sessionId": NATIVE_ID,
                    "uuid": interruption_id,
                    "parentUuid": rejected["uuid"],
                    "message": {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "[Request interrupted by user for tool use]"}
                        ],
                    },
                }
            )
            + "\n"
        )
        stream.write(
            json.dumps(
                {
                    "type": "system",
                    "subtype": "turn_duration",
                    "sessionId": NATIVE_ID,
                    "parentUuid": interruption_id,
                }
            )
            + "\n"
        )
    transport.capture_stdout = "User declined to answer questions\n❯\n"
    with pytest.raises(ValueError, match="already completed"):
        await transport.send_control(
            "ask_user_answer", request_id=rid, answers=[{"answer": "Amber"}]
        )
    assert not _send_keys(transport)
    assert receipts(events)[0]["accepted"] is False
    assert tool_results(events)[0]["is_error"] is True
    assert not transport._turn_active
    result = next(e for e in events if e.get("type") == "result")
    assert result["is_error"] is True and result["stop_reason"] == "native_question_rejected"


async def test_stop_hook_during_consumption_does_not_emit_false_cleanup_receipt(native_bridge):
    transport, events = native_bridge
    transport.capture_stdout = screen("other-before-digit")
    rid = await transport.surface()
    transport.steps.append(("2", "❯\n"))
    transport.consumed = {"answers": {SINGLE[0]["question"]: "Amber"}}

    async def stop_hook():
        await transport.handle_claude_hook(
            {
                "hook_event_name": "Stop",
                "last_assistant_message": "Native consumed Amber",
                "session_id": NATIVE_ID,
            }
        )

    transport.on_submit = stop_hook
    results = await asyncio.gather(
        *(
            transport.send_control("ask_user_answer", request_id=rid, answers=[{"answer": "Amber"}])
            for _ in range(2)
        ),
        return_exceptions=True,
    )
    assert results[0] is None and isinstance(results[1], ValueError)
    assert _send_keys(transport) == ["2"]
    assert len(receipts(events)) == 1 and receipts(events)[0]["accepted"] is True


async def test_transcript_fallback_and_late_hook_emit_one_result(native_bridge):
    transport, events = native_bridge
    rid = await transport.surface()
    pending = transport._pending_tty_prompts[rid]
    answers = {SINGLE[0]["question"]: "Amber"}
    transport.append_result(answers)
    await asyncio.gather(*(transport._native_question_result(pending) for _ in range(5)))
    await transport.handle_claude_hook(
        {
            "hook_event_name": "PostToolUse",
            "tool_use_id": TOOL_ID,
            "session_id": NATIVE_ID,
            "tool_response": {"answers": answers},
        }
    )
    assert len(tool_results(events)) == 1


async def test_failed_persistence_retries_proof_without_replaying_native_keys(native_bridge):
    transport, events = native_bridge
    rid = await transport.surface()
    pending = transport._pending_tty_prompts[rid]
    transport.append_result({SINGLE[0]["question"]: "Amber"})
    original = transport._emit
    calls = 0

    async def unreliable(frame):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("persistence unavailable")
        await original(frame)

    transport._emit = unreliable
    with pytest.raises(RuntimeError):
        await transport._native_question_result(pending)
    await asyncio.gather(*(transport._native_question_result(pending) for _ in range(5)))
    assert calls == 2 and len(tool_results(events)) == 1 and not _send_keys(transport)


@pytest.mark.parametrize("message", [None, "invalid", 42, {}])
async def test_malformed_native_message_does_not_hide_later_exact_result(native_bridge, message):
    transport, _ = native_bridge
    rid = await transport.surface()
    transport.native_path.write_text(
        json.dumps({"type": "user", "sessionId": NATIVE_ID, "message": message}) + "\nnot-json\n"
    )
    transport.append_result({SINGLE[0]["question"]: "Amber"})
    assert transport._read_native_question_result(transport._pending_tty_prompts[rid])[
        "answers"
    ] == {SINGLE[0]["question"]: "Amber"}


@pytest.mark.parametrize("change", ["inode", "truncate", "cap", "fifo"])
async def test_native_proof_rejects_changed_or_unbounded_file(native_bridge, change):
    transport, _ = native_bridge
    transport.native_path.write_text("old\n")
    rid = await transport.surface()
    pending = transport._pending_tty_prompts[rid]
    if change in {"inode", "fifo"}:
        transport.native_path.rename(transport.native_path.with_suffix(".old"))
        if change == "fifo":
            os.mkfifo(transport.native_path)
        else:
            transport.native_path.write_text("new\n")
    elif change == "truncate":
        transport.native_path.write_text("")
    else:
        transport._question_transcript_max_bytes = 2
        transport.append_result({SINGLE[0]["question"]: "Amber"})
    with pytest.raises(ControlRecoveryError):
        await asyncio.wait_for(
            asyncio.to_thread(transport._read_native_question_result, pending), 1
        )


@pytest.mark.parametrize(
    "answers",
    [
        [],
        [{"answer": "Amber"}],
        [
            {"question": "Which tone?", "answer": "Amber"},
            {"question": "Which tone?", "answer": "Blue"},
        ],
        [{"question": "Wrong question", "answer": "Amber"}, {"answer": "Matte"}],
        [{"answer": ["Blue", "Amber"]}, {"answer": "Matte"}],
        [{"answer": "Amber", "option_indexes": [0]}, {"answer": "Matte"}],
    ],
)
async def test_invalid_multi_answers_reject_before_terminal_access(native_bridge, answers):
    transport, events = native_bridge
    rid = await transport.surface(MULTI)
    before = list(transport.commands)
    with pytest.raises(ValueError):
        await transport.send_control("ask_user_answer", request_id=rid, answers=answers)
    assert transport.commands == before and not receipts(events)


async def test_wrong_review_answer_never_submits(native_bridge):
    transport, events = native_bridge
    transport.capture_stdout = screen("multi-00-initial")
    rid = await transport.surface(MULTI)
    review = screen("multi-04-texture-enter").replace("→ Amber", "→ Blue")
    transport.steps.extend([("2", screen("multi-01-tone-digit")), ("2", review)])
    with pytest.raises(ControlRecoveryError):
        await transport.send_control(
            "ask_user_answer", request_id=rid, answers=[{"answer": "Amber"}, {"answer": "Glossy"}]
        )
    assert _send_keys(transport) == ["2", "2"] and not receipts(events)


async def test_delayed_first_native_page_is_waited_for_before_keys(native_bridge):
    transport, events = native_bridge
    rid = await transport.surface()
    transport.capture_sequence.extend(["", "", screen("other-before-digit")])
    transport.steps.append(("2", "❯\n"))
    transport.consumed = {"answers": {SINGLE[0]["question"]: "Amber"}}
    await transport.send_control("ask_user_answer", request_id=rid, answers=[{"answer": "Amber"}])
    assert _send_keys(transport) == ["2"] and receipts(events)[0]["accepted"] is True


@pytest.mark.parametrize(
    "answer",
    [
        {"answer": "Blue", "free_text": "Copper"},
        {"answer": "Blue", "option_indexes": [0, 0]},
        {"answer": "Blue", "option_indexes": 0},
        {"answer": "Blue", "option_indexes": [True]},
        {"answer": "line\nbreak", "free_text": "line\nbreak"},
    ],
)
async def test_malformed_answer_metadata_rejects_before_keys(native_bridge, answer):
    transport, events = native_bridge
    rid = await transport.surface()
    before = list(transport.commands)
    with pytest.raises(ValueError):
        await transport.send_control("ask_user_answer", request_id=rid, answers=[answer])
    assert transport.commands == before and not receipts(events)


async def test_explicit_other_preserves_case_even_when_label_casefold_matches(native_bridge):
    transport, events = native_bridge
    transport.capture_stdout = screen("other-before-digit")
    rid = await transport.surface()
    populated = screen("other-after-paste").replace("Copper-transition café 東京", "blue")
    transport.steps.extend(
        [("3", screen("other-after-digit-250ms")), ("paste", populated), ("Enter", "❯\n")]
    )
    transport.consumed = {"answers": {SINGLE[0]["question"]: "blue"}}
    await transport.send_control(
        "ask_user_answer",
        request_id=rid,
        answers=[{"answer": "blue", "free_text": "blue", "option_indexes": []}],
    )
    assert _send_keys(transport) == ["3", "Enter"]
    assert transport.loaded_buffers == ["blue"] and receipts(events)[0]["decision"] == "blue"


def test_mixed_checkbox_and_custom_answer_keeps_native_option_indexes_separate():
    transport = NativeQuestionTransport
    plan = transport._question_answer_plans(
        CHECKBOX, [{"answer": ["Blue", "Copper"], "free_text": "Copper", "option_indexes": [0]}]
    )[0]
    assert plan["values"] == ["Blue", "Copper"] and plan["custom"] == "Copper"
    assert transport._question_answer_matches("Copper, Blue", plan)
    with pytest.raises(ValueError, match="options must be a list"):
        transport._question_answer_plans([{**SINGLE[0], "options": None}], [{"answer": "Blue"}])


def test_checkbox_custom_label_collision_is_rejected_before_keys():
    with pytest.raises(ValueError, match="duplicates a declared label"):
        NativeQuestionTransport._question_answer_plans(
            CHECKBOX, [{"answer": ["Blue"], "free_text": "Blue", "option_indexes": []}]
        )


@pytest.mark.parametrize("accepted", [True, False])
async def test_resolution_callback_failure_retains_unanswerable_native_tombstone(
    native_bridge, accepted
):
    transport, events = native_bridge
    transport.capture_stdout = screen("other-before-digit")
    rid = await transport.surface()
    transport.steps.append(("2", "❯\n"))
    transport.consumed = {"answers": {SINGLE[0]["question"]: "Amber" if accepted else "Blue"}}
    original = transport._emit
    failed = False

    async def fail_resolution(frame):
        nonlocal failed
        if frame.get("type") == "ask_user_resolved" and not failed:
            failed = True
            raise RuntimeError("receipt persistence unavailable")
        await original(frame)

    transport._emit = fail_resolution
    with pytest.raises(ControlRecoveryError):
        await transport.send_control(
            "ask_user_answer", request_id=rid, answers=[{"answer": "Amber"}]
        )
    assert rid in transport._pending_tty_prompts
    assert _ask_user_questions(events)[-1]["metadata"]["answerable"] is False
    with pytest.raises(ControlRecoveryError):
        await transport.send_control(
            "ask_user_answer", request_id=rid, answers=[{"answer": "Amber"}]
        )
    assert _send_keys(transport) == ["2"] and not receipts(events)


@pytest.mark.parametrize(
    "boundary", ["foreign_parent", "foreign_session", "sidechain", "ordinary_user", "missing"]
)
async def test_rejected_result_requires_its_own_native_turn_boundary(native_bridge, boundary):
    transport, events = native_bridge
    rid = await transport.surface()
    transport._turn_active = True
    transport.append_result({}, is_error=True)
    rejected = json.loads(transport.native_path.read_text().splitlines()[-1])
    record = {
        "type": "system",
        "subtype": "turn_duration",
        "parentUuid": rejected["uuid"],
        "sessionId": NATIVE_ID,
    }
    if boundary == "foreign_parent":
        record["parentUuid"] = str(uuid.uuid4())
    if boundary == "foreign_session":
        record["sessionId"] = str(uuid.uuid4())
    if boundary == "sidechain":
        record["isSidechain"] = True
    with transport.native_path.open("a") as stream:
        if boundary == "ordinary_user":
            user_id = str(uuid.uuid4())
            stream.write(
                json.dumps(
                    {
                        "type": "user",
                        "uuid": user_id,
                        "parentUuid": rejected["uuid"],
                        "sessionId": NATIVE_ID,
                        "message": {
                            "role": "user",
                            "content": [{"type": "text", "text": "A new unrelated prompt"}],
                        },
                    }
                )
                + "\n"
            )
            record["parentUuid"] = user_id
        if boundary != "missing":
            stream.write(json.dumps(record) + "\n")
    # This pane is deliberately deceptive: the composer exists during active work.
    transport.capture_stdout = "User declined to answer questions\n✻ Nucleating…\n❯\n"
    with pytest.raises(ValueError):
        await transport.send_control(
            "ask_user_answer", request_id=rid, answers=[{"answer": "Amber"}]
        )
    assert transport._turn_active and not any(e.get("type") == "result" for e in events)
    assert receipts(events)[0]["accepted"] is False


async def test_other_transaction_excludes_concurrent_message_and_terminal_writers(native_bridge):
    transport, events = native_bridge
    transport.capture_stdout = screen("other-before-digit")
    rid = await transport.surface()
    transport.steps.append(("3", screen("other-after-digit-250ms")))
    opened = asyncio.Event()
    release = asyncio.Event()
    original = transport._enter_question_text

    async def pause_editor(plan, digit, *, pane_id):
        opened.set()
        await release.wait()
        await original(plan, digit, pane_id=pane_id)

    transport._enter_question_text = pause_editor
    answering = asyncio.create_task(
        transport.send_control(
            "ask_user_answer",
            request_id=rid,
            answers=[
                {
                    "answer": "Copper-transition café 東京",
                    "free_text": "Copper-transition café 東京",
                }
            ],
        )
    )
    await opened.wait()
    delivering = asyncio.create_task(transport.send_message("Foreign follow-up"))
    terminal = asyncio.create_task(transport.send_control("terminal_key", keys=["Down"]))
    await asyncio.sleep(0)
    assert not delivering.done() and not terminal.done() and not transport.loaded_buffers
    transport.steps.extend([("paste", screen("other-after-paste")), ("Enter", "❯\n")])
    # Missing native proof keeps the editor attempt uncertain; queued normal
    # delivery must fail without writing into that native control.
    release.set()
    with pytest.raises(ControlRecoveryError):
        await answering
    from skuld.delivery_errors import DeliveryNotAcceptedError

    with pytest.raises(DeliveryNotAcceptedError):
        await delivering
    # Raw terminal recovery is explicitly allowed once the answer transaction ends.
    result = await asyncio.gather(terminal, return_exceptions=True)
    assert isinstance(result[0], AssertionError)  # Harness rejects unplanned recovery key.
    assert transport.loaded_buffers == ["Copper-transition café 東京"]
    assert not receipts(events)


async def test_checkbox_other_focus_and_paste_never_send_toggle_enter(native_bridge):
    transport, events = native_bridge
    transport.capture_stdout = screen("mixed-00-initial")
    rid = await transport.surface(MIXED)
    transport.steps.extend(
        [
            ("1", screen("mixed-01-auth")),
            ("Down", screen("mixed-02-down-search")),
            ("Down", screen("mixed-03-down-other")),
            ("paste", screen("mixed-04-paste")),
            ("Down", screen("mixed-07-down-submit")),
            ("Enter", screen("mixed-08-review")),
            ("1", "❯\n"),
        ]
    )
    text = "Copper focus café 東京"
    transport.consumed = {"answers": {"Which features?": "Auth first, " + text}}
    await transport.send_control(
        "ask_user_answer",
        request_id=rid,
        answers=[{"answer": ["Auth first", text], "free_text": text, "option_indexes": [0]}],
    )
    assert _send_keys(transport) == ["1", "Down", "Down", "Down", "Enter", "1"]
    assert transport.loaded_buffers == [text]
    assert len(receipts(events)) == 1 and receipts(events)[0]["accepted"] is True
    assert len(tool_results(events)) == 1


async def test_checkbox_other_waits_for_actual_focus_before_paste(native_bridge):
    transport, events = native_bridge
    transport.capture_stdout = screen("mixed-00-initial")
    rid = await transport.surface(MIXED)
    transport.steps.extend([("1", screen("mixed-01-auth")), ("Down", screen("mixed-01-auth"))])
    with pytest.raises(ControlRecoveryError):
        await transport.send_control(
            "ask_user_answer",
            request_id=rid,
            answers=[
                {"answer": ["Auth first", "Copper"], "free_text": "Copper", "option_indexes": [0]}
            ],
        )
    assert _send_keys(transport) == ["1", "Down"]
    assert not transport.loaded_buffers and not receipts(events)


async def test_checkbox_unchecked_custom_text_is_never_submitted(native_bridge):
    transport, events = native_bridge
    transport.capture_stdout = screen("mixed-00-initial")
    rid = await transport.surface(MIXED)
    transport.steps.extend(
        [
            ("1", screen("mixed-01-auth")),
            ("Down", screen("mixed-02-down-search")),
            ("Down", screen("mixed-03-down-other")),
            ("paste", screen("mixed-05-enter-text")),
        ]
    )
    text = "Copper focus café 東京"
    with pytest.raises(ControlRecoveryError):
        await transport.send_control(
            "ask_user_answer",
            request_id=rid,
            answers=[{"answer": ["Auth first", text], "free_text": text, "option_indexes": [0]}],
        )
    assert _send_keys(transport) == ["1", "Down", "Down"]
    assert transport.loaded_buffers == [text] and not receipts(events)


async def test_checkbox_unknown_focus_is_rejected_without_typing(native_bridge):
    transport, events = native_bridge
    transport.capture_stdout = screen("mixed-01-auth").replace("❯", " ")
    rid = await transport.surface(MIXED)
    with pytest.raises(ControlRecoveryError):
        await transport.send_control(
            "ask_user_answer",
            request_id=rid,
            answers=[
                {"answer": ["Auth first", "Copper"], "free_text": "Copper", "option_indexes": [0]}
            ],
        )
    assert not _send_keys(transport) and not transport.loaded_buffers and not receipts(events)
