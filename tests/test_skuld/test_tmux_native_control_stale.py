"""LEX-1936: a native-control card the CLI resolved by itself must not block steering.

Replays the two field incidents:

* 5 Nov 2026 (overnight-perf, kse-node12): Claude Code's built-in "only a person may
  approve" safety prompt under bypassPermissions, nobody answered, the CLI denied the
  command after two minutes ("the permission prompt timed out, or none was shown. The
  command was NOT run", is_error=true) and no hook said so. The card stayed and every
  message was refused for 39 minutes.
* 13 Oct 2026 (QA3, kse-node3): a permission card the CLI let through anyway. Its
  PermissionRequest had no tool id bound, so its tool's result did not close it either.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import pytest

from skuld.delivery_errors import NativeControlPendingError
from skuld.transports.tmux_interactive import (
    DISMISS_BY_OPERATOR,
    DISMISS_MENU_NOT_SHOWN,
    DISMISS_RESOLVED_IN_TERMINAL,
    DISMISS_SUPERSEDED,
    MENU_GONE,
    MENU_LIVE,
    MENU_UNKNOWN,
    TmuxInteractiveTransport,
)
from tests.test_skuld.test_tmux_interactive_transport import (
    FakeTmuxInteractiveTransport,
    _ask_user_questions,
    _collect_events,
    _wait_until,
)

NATIVE = "5e4cea90-0595-4ec3-ac8a-7dbaf093e30d"
TOOL = "toolu_01r8JHXnv9"
RM = {"command": "for n in $LIST; do rm $V/$n; done", "description": "Remove old arms"}
TIMEOUT_DENIAL = (
    "Permission for this action was denied: the permission prompt timed out, or none was "
    "shown. The command was NOT run."
)
PERSON_ONLY_MENU = "\n".join(
    [
        "⏺ Bash(for n in $LIST; do rm $V/$n; done)",
        "",
        " Bash command",
        "   for n in $LIST; do rm $V/$n; done",
        " Only a person may approve such a removal",
        "",
        " Do you want to proceed?",
        " ❯ 1. Yes",
        "   2. Yes, and don't ask again for rm commands",
        "   3. No, and tell Claude what to do differently (esc)",
        "",
    ]
)
WORKING = "\n".join(
    [
        '⏺ Bash(for n in $LIST; do rm "${V:?}"/"${n:?}"; done)',
        "  ⎿  (no output)",
        "",
        "✻ Measuring… (41m 12s · ↓ 12.3k tokens)",
        "",
        "────────────────────────────────────────",
        "❯ ",
        "────────────────────────────────────────",
        "  ⏵⏵ bypass permissions on (shift+tab to cycle)",
        "",
    ]
)


class Session:
    """A fake Claude tmux session with its native transcript."""

    def __init__(self, tmp_path: Path, **kwargs: Any) -> None:
        self.transport = FakeTmuxInteractiveTransport(
            str(tmp_path), sdk_port=8081, skip_permissions=True, **kwargs
        )
        self.transport._claude_native_session_id = NATIVE
        self.transcript = tmp_path / f"{NATIVE}.jsonl"
        self.transcript.write_text("")
        self.events: list[dict[str, Any]] = []

    async def __aenter__(self) -> Session:
        self.events = await _collect_events(self.transport)
        await self.transport.start()
        self.transport.capture_stdout = WORKING
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.transport.stop()

    def record(self, record: dict[str, Any]) -> None:
        with self.transcript.open("a") as stream:
            stream.write(json.dumps({"sessionId": NATIVE, **record}) + "\n")

    async def hook(self, event: str, **payload: Any) -> None:
        await self.transport.handle_claude_hook(
            {
                "hook_event_name": event,
                "session_id": NATIVE,
                "transcript_path": str(self.transcript),
                **payload,
            }
        )

    async def person_only_prompt(self) -> str:
        """20:36:56 — the worker's unquoted rm trips Claude Code's safety check."""
        self.record(
            {
                "type": "assistant",
                "uuid": "a1",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": TOOL, "name": "Bash", "input": RM}],
                },
            }
        )
        await self.hook("PreToolUse", tool_name="Bash", tool_input=RM, tool_use_id=TOOL)
        # Claude draws the menu as soon as the hook returns; the card waits for it.
        self.transport.capture_stdout = PERSON_ONLY_MENU
        await self.hook(
            "PermissionRequest",
            tool_name="Bash",
            tool_input=RM,
            permission_mode="bypassPermissions",
        )
        [card] = _ask_user_questions(self.events)
        return card["request_id"]

    def cli_times_out_and_denies(self) -> None:
        """20:38:56 — the CLI denies the command itself; no hook reports it."""
        self.record(
            {
                "type": "user",
                "uuid": "u1",
                "parentUuid": "a1",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": TOOL,
                            "content": TIMEOUT_DENIAL,
                            "is_error": True,
                        }
                    ],
                },
            }
        )
        self.transport.capture_stdout = WORKING

    def dismissals(self) -> list[dict[str, Any]]:
        return [e for e in self.events if e.get("type") == "ask_user_resolved"]

    def typed(self) -> list[str]:
        return list(self.transport.loaded_buffers)


def _keys(transport: FakeTmuxInteractiveTransport) -> list[str]:
    return [args[-1] for args, _ in transport.commands if args and args[0] == "send-keys"]


# -- 5 Nov replay ------------------------------------------------------------------------


async def test_5nov_timed_out_safety_prompt_is_dismissed_and_steering_resumes(tmp_path):
    async with Session(tmp_path) as s:
        rid = await s.person_only_prompt()
        card = _ask_user_questions(s.events)[0]
        assert card["metadata"]["person_only"] is True
        assert card["metadata"]["auto_deny_after_seconds"] == 120.0
        assert card["metadata"]["opened_at"]

        # While the menu is really on screen, a message is refused and nothing typed:
        # Enter would confirm the highlighted "Yes".
        with pytest.raises(NativeControlPendingError) as refused:
            await s.transport.send_message("Disk headroom: you are above the floor now")
        assert refused.value.details() == {
            "code": "native_control_pending",
            "control_request_id": rid,
            "control_kind": "permission",
            "control_opened_at": card["metadata"]["opened_at"],
            "menu_state": MENU_LIVE,
        }
        assert s.typed() == [] and _keys(s.transport) == []

        s.cli_times_out_and_denies()
        assert await s.transport.sweep_native_controls() == [rid]

        [dismissal] = s.dismissals()
        assert dismissal["request_id"] == rid
        assert dismissal["dismissed"] is True
        assert dismissal["dismiss_reason"] == DISMISS_RESOLVED_IN_TERMINAL
        assert dismissal["dismiss_detail"] == "resolved in terminal"
        assert dismissal["dismiss_trigger"] == "sweep"
        assert dismissal["accepted"] is False
        assert dismissal["tool_use_id"] == TOOL
        assert dismissal["age_seconds"] >= 0
        # The tool card closes as well: the transcript's denial is the tool result.
        [result] = [
            e
            for e in s.events
            if e.get("type") == "user" and e["message"]["content"][0].get("tool_use_id") == TOOL
        ]
        assert result["message"]["content"][0]["is_error"] is True
        assert "NOT run" in result["message"]["content"][0]["content"]

        # 20:53 and 21:06 — the held coordinator messages now land.
        await s.transport.send_message("Disk headroom: you are above the floor now")
        await s.transport.send_message("Rule out the RTX shader cache first")
        assert s.typed() == [
            "Disk headroom: you are above the floor now",
            "Rule out the RTX shader cache first",
        ]
        assert s.transport.list_native_controls()["counts"] == {
            f"dismissed.{DISMISS_RESOLVED_IN_TERMINAL}": 1
        }


async def test_5nov_a_delivery_after_the_timeout_retires_the_card_itself(tmp_path):
    async with Session(tmp_path) as s:
        rid = await s.person_only_prompt()
        s.cli_times_out_and_denies()

        await s.transport.send_message("steer")

        [dismissal] = s.dismissals()
        assert (dismissal["request_id"], dismissal["dismiss_reason"]) == (
            rid,
            DISMISS_RESOLVED_IN_TERMINAL,
        )
        assert dismissal["dismiss_trigger"] == "delivery"
        assert s.typed() == ["steer"]


async def test_the_periodic_sweep_clears_the_card_without_any_delivery(tmp_path):
    async with Session(tmp_path, native_control_sweep_interval_s=0.05) as s:
        rid = await s.person_only_prompt()
        s.cli_times_out_and_denies()
        await _wait_until(lambda: bool(s.dismissals()), timeout=3.0)
        assert s.dismissals()[0]["request_id"] == rid
        assert not s.transport._pending_tty_prompts


async def test_a_failed_sweep_is_logged_counted_and_the_loop_continues(tmp_path, caplog):
    async with Session(tmp_path, native_control_sweep_interval_s=0.02) as s:
        await s.person_only_prompt()
        calls = 0
        original = s.transport.sweep_native_controls

        async def flaky() -> list[str]:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("tmux capture exploded")
            return await original()

        s.transport.sweep_native_controls = flaky
        caplog.set_level(logging.ERROR)
        s.cli_times_out_and_denies()
        await _wait_until(lambda: bool(s.dismissals()), timeout=3.0)
        assert s.transport.list_native_controls()["counts"]["sweep_failures"] == 1
        assert "Native-control sweep failed" in caplog.text


# -- 13 Oct replay -----------------------------------------------------------------------


async def test_13oct_unbound_card_the_cli_let_through_is_dismissed_once_its_menu_is_gone(
    tmp_path,
):
    """No PreToolUse bound the card; the CLI ran the command (with its own normalized
    input) and the menu never stayed. The screen check closes the card."""
    async with Session(tmp_path) as s:
        cmd = {"command": "rm -rf ./build/cache"}
        await s.hook(
            "PermissionRequest",
            tool_name="Bash",
            tool_input=cmd,
            permission_mode="bypassPermissions",
        )
        [card] = _ask_user_questions(s.events)
        rid = card["request_id"]
        assert s.transport._pending_tty_prompts[rid]["native_tool_use_id"] == ""
        await s.hook(
            "PostToolUse",
            tool_name="Bash",
            tool_input={"command": "rm -rf ./build/cache", "timeout": 120000},
            tool_use_id="toolu_other",
            tool_response={"stdout": ""},
        )
        assert rid in s.transport._pending_tty_prompts  # not this card's exact call

        # A result is no evidence (a parallel tool can finish while a menu waits).
        assert "superseded_by" not in s.transport._pending_tty_prompts[rid]

        assert await s.transport.sweep_native_controls() == [rid]
        [dismissal] = s.dismissals()
        assert dismissal["dismiss_reason"] == DISMISS_MENU_NOT_SHOWN
        assert dismissal["dismiss_detail"] == "menu no longer shown"
        assert dismissal["menu_state"] == MENU_GONE
        await s.transport.send_message("carry on")
        assert s.typed() == ["carry on"]


async def test_13oct_unbound_card_closes_on_the_main_agents_result_for_the_same_call(tmp_path):
    async with Session(tmp_path) as s:
        cmd = {"command": "rm -rf ./build/cache"}
        await s.hook("PermissionRequest", tool_name="Bash", tool_input=cmd)
        [card] = _ask_user_questions(s.events)
        await s.hook(
            "PostToolUse",
            tool_name="Bash",
            tool_input=cmd,
            tool_use_id="toolu_ran",
            tool_response={"stdout": ""},
        )
        [dismissal] = s.dismissals()
        assert dismissal["request_id"] == card["request_id"]
        assert dismissal["decision"] == "completed_elsewhere"
        assert dismissal["dismiss_reason"] == DISMISS_RESOLVED_IN_TERMINAL
        assert dismissal["dismiss_trigger"] == "tool_result"


async def test_a_subagent_result_never_closes_an_unbound_main_card(tmp_path):
    async with Session(tmp_path) as s:
        cmd = {"command": "rm -rf ./build/cache"}
        await s.hook("PermissionRequest", tool_name="Bash", tool_input=cmd)
        await s.hook(
            "PostToolUse",
            tool_name="Bash",
            tool_input=cmd,
            tool_use_id="toolu_child",
            agent_id="sub-1",
            tool_response={},
        )
        assert s.transport._pending_tty_prompts
        assert not s.dismissals()


# -- PermissionDenied, live and unknown screens --------------------------------------------


async def test_permission_denied_hook_dismisses_the_bound_card(tmp_path):
    async with Session(tmp_path) as s:
        rid = await s.person_only_prompt()
        await s.hook(
            "PermissionDenied",
            tool_name="Bash",
            tool_input=RM,
            tool_use_id=TOOL,
            reason="timed out",
        )
        [dismissal] = s.dismissals()
        assert dismissal["request_id"] == rid
        assert dismissal["dismiss_trigger"] == "PermissionDenied"


async def test_permission_denied_for_another_call_keeps_the_card(tmp_path):
    async with Session(tmp_path) as s:
        await s.person_only_prompt()
        await s.hook(
            "PermissionDenied", tool_name="Bash", tool_input={"command": "ls"}, tool_use_id="x"
        )
        assert s.transport._pending_tty_prompts and not s.dismissals()


async def test_a_live_menu_is_kept_and_its_state_reported(tmp_path):
    async with Session(tmp_path) as s:
        rid = await s.person_only_prompt()
        assert await s.transport.sweep_native_controls() == []
        [state] = [e for e in s.events if e.get("type") == "native_control_state"]
        assert state["request_id"] == rid and state["menu_state"] == MENU_LIVE
        [view] = s.transport.list_native_controls()["controls"]
        assert view["menu_state"] == MENU_LIVE and view["menu_checked_at"]
        assert view["person_only"] is True and view["auto_deny_after_seconds"] == 120.0
        # A second sweep with the same state does not repeat the event.
        await s.transport.sweep_native_controls()
        assert len([e for e in s.events if e.get("type") == "native_control_state"]) == 1


@pytest.mark.parametrize(
    "screen",
    [
        "",  # capture failed
        "❯ half-typed by someone in the terminal",  # composer holds text
        PERSON_ONLY_MENU + "\n❯ \n",  # both: never guess
    ],
)
async def test_an_unknown_screen_never_clears_a_card(tmp_path, screen):
    async with Session(tmp_path) as s:
        await s.person_only_prompt()
        s.transport.capture_stdout = screen
        assert await s.transport.sweep_native_controls() == []
        assert s.transport._pending_tty_prompts
        with pytest.raises(NativeControlPendingError):
            await s.transport.send_message("steer")
        assert s.typed() == []


QUESTION = {"questions": [{"question": "Which DB?", "options": [{"label": "PG"}]}]}
QUESTION_MENU = "Which DB?\n❯ 1. PG\n  2. Type something.\n"


async def _question_card(s: Session, *, settled: bool = True, age_s: float = 60.0) -> str:
    await s.hook(
        "PreToolUse", tool_name="AskUserQuestion", tool_input=QUESTION, tool_use_id="toolu_q"
    )
    [card] = _ask_user_questions(s.events)
    rid = card["request_id"]
    if settled:  # the menu has had its render window
        s.transport._pending_tty_prompts[rid]["opened_monotonic"] -= age_s
    return rid


def _a_sweep_interval_later(s: Session, rid: str) -> None:
    s.transport._pending_tty_prompts[rid]["absent_counted_monotonic"] -= (
        s.transport._native_control_sweep_interval_s
    )


async def test_a_question_card_closes_only_after_the_configured_absent_checks(tmp_path):
    """LEX-1979: the sweep checks question cards too (menu_checked_at was null for 6 h)."""
    async with Session(tmp_path, native_control_absent_checks=3) as s:
        # Younger than a sweep interval: a delivery alone does not close it.
        rid = await _question_card(s, age_s=5.0)
        assert await s.transport.sweep_native_controls() == []
        [view] = s.transport.list_native_controls()["controls"]
        assert view["menu_state"] == MENU_GONE and view["menu_checked_at"]
        assert view["absent_checks"] == 1
        # Refused deliveries within the same sweep interval do not add to the count:
        # a burst of messages while the menu is still drawing must not close it.
        for _ in range(3):
            with pytest.raises(NativeControlPendingError) as refused:
                await s.transport.send_message("steer")
        assert refused.value.control_kind == "question"
        assert s.transport._pending_tty_prompts[rid]["absent_checks"] == 1
        _a_sweep_interval_later(s, rid)
        with pytest.raises(NativeControlPendingError):
            await s.transport.send_message("steer")  # a delivery an interval later counts
        assert s.transport._pending_tty_prompts[rid]["absent_checks"] == 2
        _a_sweep_interval_later(s, rid)
        assert await s.transport.sweep_native_controls() == [rid]
        [dismissal] = s.dismissals()
        assert dismissal["dismiss_reason"] == DISMISS_MENU_NOT_SHOWN
        assert dismissal["control_kind"] == "question"
        await s.transport.send_message("steer")
        assert s.typed() == ["steer"]


@pytest.mark.parametrize("screen", ["", "QUESTION_MENU"])
async def test_any_check_that_is_not_gone_resets_the_absent_count(tmp_path, screen):
    async with Session(tmp_path, native_control_absent_checks=2) as s:
        rid = await _question_card(s)
        await s.transport.sweep_native_controls()
        s.transport.capture_stdout = QUESTION_MENU if screen == "QUESTION_MENU" else screen
        for _ in range(3):
            assert await s.transport.sweep_native_controls() == []
        assert s.transport._pending_tty_prompts[rid]["absent_checks"] == 0
        s.transport.capture_stdout = WORKING
        assert await s.transport.sweep_native_controls() == []
        assert rid in s.transport._pending_tty_prompts


async def test_8nov_a_question_dropped_by_compaction_closes_on_the_next_main_tool_hook(
    tmp_path,
):
    """8 Nov 02:21Z (ios-coordinator, Thor): a card opened at an auto-compaction for a
    tool call that never ran; hundreds of later PreToolUse hooks, every message refused
    for 6 h. The first later main-agent hook now closes it."""
    async with Session(tmp_path) as s:
        rid = await _question_card(s)
        await s.hook(
            "PreToolUse", tool_name="Bash", tool_input={"command": "ls"}, tool_use_id="toolu_next"
        )
        await _wait_until(lambda: bool(s.dismissals()), timeout=5.0)
        [dismissal] = s.dismissals()
        assert dismissal["request_id"] == rid
        assert dismissal["dismiss_reason"] == DISMISS_SUPERSEDED
        assert dismissal["dismiss_trigger"] == "hook"
        assert "PreToolUse for toolu_next" in dismissal["dismiss_detail"]
        await s.transport.send_message("Damien: are you there?")
        assert s.typed() == ["Damien: are you there?"]


async def test_7nov_a_prompt_typed_into_the_pane_supersedes_a_stranded_card(tmp_path):
    """7 Nov (ios-release, mini-m6): the coordinator typed into the pane to get past a
    card the broker kept; that prompt's UserPromptSubmit now closes it."""
    async with Session(tmp_path) as s:
        rid = await s.person_only_prompt()
        s.transport._pending_tty_prompts[rid]["opened_monotonic"] -= 60.0
        s.transport.capture_stdout = WORKING
        await s.hook("UserPromptSubmit", prompt="/clear")
        await _wait_until(lambda: bool(s.dismissals()), timeout=5.0)
        assert s.dismissals()[0]["dismiss_reason"] == DISMISS_SUPERSEDED


# A multi-select question with its unnumbered Submit row selected (Codex review of
# LEX-1979): no numbered selected row, but its footer and tab bar make it live.
QUESTION_SUBMIT_PAGE = (
    Path(__file__).resolve().parents[1]
    / "support/forge/screens/claude-question-mixed-07-down-submit.txt"
).read_text()


@pytest.mark.parametrize("screen", [QUESTION_MENU, QUESTION_SUBMIT_PAGE, ""])
async def test_a_superseded_card_is_kept_unless_its_menu_is_gone(tmp_path, screen):
    async with Session(tmp_path) as s:
        rid = await _question_card(s)
        s.transport.capture_stdout = screen
        await s.hook("UserPromptSubmit", prompt="typed")
        await asyncio.sleep(0.2)
        assert await s.transport.sweep_native_controls() == []
        assert not s.dismissals()
        superseded = s.transport._pending_tty_prompts[rid].get("superseded_by")
        if screen in (QUESTION_MENU, QUESTION_SUBMIT_PAGE):
            # A live menu proves the hook was not evidence; a later unknown check
            # (a failed capture) must not close it.
            assert superseded is None
            s.transport.capture_stdout = ""
            assert await s.transport.sweep_native_controls() == []
            assert rid in s.transport._pending_tty_prompts
        else:
            assert superseded["hook"] == "UserPromptSubmit"


@pytest.mark.parametrize(
    ("event", "payload", "why"),
    [
        ("PreToolUse", {"tool_use_id": "toolu_q"}, "the card's own tool call"),
        ("PreToolUse", {"tool_use_id": "toolu_sub", "agent_id": "sub-1"}, "a subagent"),
        ("PreToolUse", {"tool_use_id": "t", "session_id": "teammate-native"}, "a teammate"),
        ("PostToolUse", {"tool_use_id": "toolu_slow", "tool_response": {}}, "a parallel result"),
    ],
)
async def test_hooks_that_never_supersede_a_card(tmp_path, event, payload, why):
    async with Session(tmp_path) as s:
        rid = await _question_card(s)
        await s.hook(event, tool_name="WebFetch", tool_input={}, **payload)
        await asyncio.sleep(0.2)
        assert "superseded_by" not in s.transport._pending_tty_prompts.get(rid, {}), why


async def test_a_hook_supersedes_nothing_until_the_main_conversation_is_known(tmp_path):
    """Before Skuld knows its main session id, a teammate's hook looks like the main one."""
    async with Session(tmp_path) as s:
        rid = await _question_card(s)
        s.transport._claude_native_session_id = ""
        await s.hook("PreToolUse", tool_name="Read", tool_input={}, tool_use_id="toolu_x")
        assert "superseded_by" not in s.transport._pending_tty_prompts[rid]


async def test_a_main_hook_never_supersedes_a_background_subagents_question(tmp_path):
    async with Session(tmp_path) as s:
        await s.hook(
            "PreToolUse",
            tool_name="AskUserQuestion",
            tool_input=QUESTION,
            tool_use_id="toolu_sub_q",
            agent_id="sub-1",
            agent_type="general-purpose",  # a real subagent names its type (LEX-2025)
        )
        [card] = _ask_user_questions(s.events)
        pending = s.transport._pending_tty_prompts[card["request_id"]]
        assert pending["child"] is True
        pending["opened_monotonic"] -= 60.0
        await s.hook("PreToolUse", tool_name="Read", tool_input={}, tool_use_id="toolu_x")
        assert "superseded_by" not in pending


async def test_a_hook_after_stop_starts_no_check(tmp_path):
    async with Session(tmp_path) as s:
        rid = await _question_card(s)
        await s.transport.stop()
        await s.hook("PreToolUse", tool_name="Read", tool_input={}, tool_use_id="toolu_x")
        assert "superseded_by" not in s.transport._pending_tty_prompts.get(rid, {})
        assert not s.transport._native_control_followups


async def test_a_parallel_tool_hook_within_the_render_window_does_not_supersede(tmp_path):
    async with Session(tmp_path) as s:
        rid = await _question_card(s, settled=False)
        await s.hook("PreToolUse", tool_name="Read", tool_input={}, tool_use_id="toolu_par")
        assert "superseded_by" not in s.transport._pending_tty_prompts[rid]


async def test_a_delivery_with_no_card_types_nothing_into_a_live_submit_page(tmp_path):
    async with Session(tmp_path) as s:
        s.transport.capture_stdout = QUESTION_SUBMIT_PAGE
        with pytest.raises(NativeControlPendingError) as refused:
            await s.transport.send_message("steer")
        assert refused.value.menu_state == MENU_LIVE
        assert s.typed() == [] and _keys(s.transport) == []


async def test_a_delivery_closes_a_settled_question_whose_menu_is_gone(tmp_path):
    """LEX-1979 (b): a message is never refused for a menu that is not on screen."""
    async with Session(tmp_path) as s:
        rid = await _question_card(s, age_s=60.0)
        await s.transport.send_message("Damien: still there?")
        [dismissal] = s.dismissals()
        assert dismissal["request_id"] == rid
        assert dismissal["dismiss_reason"] == DISMISS_MENU_NOT_SHOWN
        assert dismissal["dismiss_trigger"] == "delivery"
        assert s.typed() == ["Damien: still there?"]


async def test_a_delivery_keeps_a_settled_question_whose_menu_is_live(tmp_path):
    async with Session(tmp_path) as s:
        await _question_card(s, age_s=60.0)
        s.transport.capture_stdout = QUESTION_MENU
        with pytest.raises(NativeControlPendingError):
            await s.transport.send_message("steer")
        assert s.typed() == [] and not s.dismissals()


async def test_absent_checks_must_be_positive(tmp_path):
    with pytest.raises(ValueError, match="tmux_native_control_absent_checks"):
        FakeTmuxInteractiveTransport(str(tmp_path), native_control_absent_checks=0)


async def test_an_unreadable_transcript_is_loud_and_the_screen_check_still_applies(
    tmp_path, caplog
):
    async with Session(tmp_path, question_transcript_max_bytes=16) as s:
        rid = await s.person_only_prompt()
        s.cli_times_out_and_denies()  # the result is past the 16-byte bound
        caplog.set_level(logging.ERROR)
        assert await s.transport.sweep_native_controls() == [rid]
        assert s.dismissals()[0]["dismiss_reason"] == DISMISS_MENU_NOT_SHOWN
        assert "tmux_question_transcript_max_bytes" in caplog.text
        assert s.transport.list_native_controls()["counts"]["transcript_unreadable"] == 1


async def test_a_card_being_answered_is_left_to_its_answer(tmp_path):
    async with Session(tmp_path) as s:
        rid = await s.person_only_prompt()
        s.transport._pending_tty_prompts[rid]["answer_in_flight"] = True
        s.cli_times_out_and_denies()
        assert await s.transport.sweep_native_controls() == []
        assert rid in s.transport._pending_tty_prompts


# -- Operator dismissal ----------------------------------------------------------------------


async def test_operator_dismissal_is_audited_and_closes_the_card(tmp_path, caplog):
    async with Session(tmp_path) as s:
        rid = await s.person_only_prompt()
        s.transport.capture_stdout = ""  # screen unknown: the automatic check keeps it
        caplog.set_level(logging.WARNING)
        dismissed = await s.transport.dismiss_native_control(rid, dismissed_by="dfagnou")
        assert dismissed["dismiss_reason"] == DISMISS_BY_OPERATOR
        assert dismissed["dismiss_detail"] == "dismissed by operator dfagnou"
        [event] = s.dismissals()
        assert event["decision"] == DISMISS_BY_OPERATOR and event["dismiss_trigger"] == "operator"
        assert f"AUDIT native control {rid} dismissed by operator dfagnou" in caplog.text
        with pytest.raises(LookupError):
            await s.transport.dismiss_native_control(rid, dismissed_by="dfagnou")
        with pytest.raises(ValueError):
            await s.transport.dismiss_native_control("tty-9", dismissed_by=" ")


async def test_dismissing_a_card_whose_menu_is_live_still_types_nothing(tmp_path):
    async with Session(tmp_path) as s:
        rid = await s.person_only_prompt()
        await s.transport.dismiss_native_control(rid, dismissed_by="dfagnou")
        with pytest.raises(NativeControlPendingError) as refused:
            await s.transport.send_message("steer")
        assert refused.value.control_request_id == ""
        assert refused.value.menu_state == MENU_LIVE
        assert s.typed() == [] and _keys(s.transport) == []


async def test_turn_end_still_clears_cards_with_a_reason(tmp_path):
    async with Session(tmp_path) as s:
        rid = await s.person_only_prompt()
        await s.hook("Stop", last_assistant_message="done")
        [event] = s.dismissals()
        assert event["request_id"] == rid
        assert event["dismiss_reason"] == "turn_ended" and event["dismissed"] is True


# -- Screen classification ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("screen", "state"),
    [
        (PERSON_ONLY_MENU, MENU_LIVE),
        (WORKING, MENU_GONE),
        ('❯ Try "how does foo work?"', MENU_GONE),
        ("Which DB?\n❯ 1. PG\n  2. SQLite\n", MENU_LIVE),
        ("1. first step\n2. second step\n❯ ", MENU_GONE),
        ("", MENU_UNKNOWN),
        ("⏺ thinking…", MENU_UNKNOWN),
        (QUESTION_SUBMIT_PAGE, MENU_LIVE),
        # The footer quoted in output: the composer follows it, so it is no menu.
        ("Enter to select · ↑/↓ to navigate · Esc to cancel\n❯ ", MENU_GONE),
        ("Enter to select · ↑/↓ to navigate · Esc to cancel\n❯ continue", MENU_UNKNOWN),
    ],
)
def test_native_menu_state(screen, state):
    assert TmuxInteractiveTransport._native_menu_state(screen) == state


async def test_settings_reject_non_positive_intervals(tmp_path):
    with pytest.raises(ValueError, match="tmux_native_control_sweep_interval_s"):
        FakeTmuxInteractiveTransport(str(tmp_path), native_control_sweep_interval_s=0)
    with pytest.raises(ValueError, match="tmux_person_only_prompt_timeout_s"):
        FakeTmuxInteractiveTransport(str(tmp_path), person_only_prompt_timeout_s=-1)


async def test_ordinary_permission_is_not_person_only(tmp_path):
    transport = FakeTmuxInteractiveTransport(str(tmp_path))
    events = await _collect_events(transport)
    await transport.start()
    try:
        await transport.handle_claude_hook(
            {
                "hook_event_name": "PermissionRequest",
                "tool_name": "Bash",
                "tool_input": {"command": "ls"},
                "permission_mode": "default",
            }
        )
        [card] = _ask_user_questions(events)
        assert "person_only" not in card["metadata"]
        assert "auto_deny_after_seconds" not in card["metadata"]
    finally:
        await transport.stop()
    await asyncio.sleep(0)


async def test_a_cancelled_tmux_call_kills_and_reaps_its_child(tmp_path, monkeypatch):
    """Codex re-check: a timed-out capture left the tmux child running."""
    import skuld.transports.tmux_interactive as module

    class Hung:
        returncode = None
        killed = waited = False

        async def communicate(self):
            await asyncio.sleep(3600)

        def kill(self):
            self.killed = True

        async def wait(self):
            self.waited = True

    hung = Hung()
    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", lambda *a, **k: _resolved(hung))
    transport = TmuxInteractiveTransport(str(tmp_path))
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(transport._run_tmux("capture-pane", "-p"), timeout=0.05)
    assert hung.killed and hung.waited


async def _resolved(value):
    return value
