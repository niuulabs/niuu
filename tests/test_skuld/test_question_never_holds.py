"""LEX-2016: a question card never holds a session.

Field incident, 9 Nov 2026 (lexi-frontend-presentation, ec2e2f48): Claude asked two
questions. Damien answered the first and left the second. Forge refused his answer
("Claude requires one explicit answer for every question") and then every message
("menu live"), so the session sat waiting on a card he could not get past. Claude
Code's own menu allows both: its review page warns "You have not answered all
questions" and still offers Submit, and every question page offers "Chat about this".

The screens under ``support/forge/screens/claude-question-partial-*`` were captured from
Claude Code 2.1.319 on Thor, and the native records below have the shapes it wrote.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from skuld.delivery_errors import NativeControlPendingError, QuestionCloseUnconfirmedError
from skuld.transports.tmux_interactive import (
    DISMISS_CLOSED_FOR_MESSAGE,
    MENU_LIVE,
    TmuxInteractiveTransport,
)
from tests.test_skuld.test_tmux_interactive_transport import (
    FakeTmuxInteractiveTransport,
    _ask_user_questions,
    _collect_events,
)

SCREENS = Path(__file__).parents[1] / "support/forge/screens"
NATIVE = "d74caf50-95b4-464c-b5ec-c3180dec851b"
QTOOL = "toolu_01HZYmar8zN45FLTdZeQMHGX"
COLOUR = "Pick a colour?"
SIZE = "Pick a size?"
QUESTIONS = [
    {
        "question": COLOUR,
        "header": "Colour",
        "multiSelect": False,
        "options": [
            {"label": "Red", "description": "Red"},
            {"label": "Blue", "description": "Blue"},
        ],
    },
    {
        "question": SIZE,
        "header": "Size",
        "multiSelect": False,
        "options": [
            {"label": "Small", "description": "Small"},
            {"label": "Large", "description": "Large"},
        ],
    },
]
COMPOSER = "\n".join(["─" * 40, "❯ ", "─" * 40, "  ⏵⏵ auto mode on (shift+tab to cycle)", ""])
PERMISSION_MENU = "\n".join(
    [
        " Bash command",
        "   ls",
        "",
        " Do you want to proceed?",
        " ❯ 1. Yes",
        "   2. Yes, and don't ask again for ls commands",
        "   3. No, and tell Claude what to do differently (esc)",
        "",
    ]
)
CHAT_RESULT = (
    "The user doesn't want to proceed with this tool use. The tool use was rejected (eg. "
    "if it was a file edit, the new_string was NOT written to the file). To tell you how "
    "to proceed, the user said:\nThe user wants to clarify these questions."
)
ESC_RESULT = (
    "The user doesn't want to proceed with this tool use. The tool use was rejected (eg. "
    "if it was a file edit, the new_string was NOT written to the file). STOP what you "
    "are doing and wait for the user to tell you how to proceed."
)


def screen(name: str) -> str:
    return (SCREENS / f"claude-question-partial-{name}.txt").read_text()


class KeyedTransport(FakeTmuxInteractiveTransport):
    """The fake tmux transport, with Claude's reaction to each press of a key, in order."""

    def __init__(self, workspace_dir: str) -> None:
        super().__init__(workspace_dir, sdk_port=8081, skip_permissions=True)
        self._menu_render_wait_s = 0.05
        self._menu_poll_step_s = 0.001
        self._question_result_wait_s = 0.05
        self.on_key: dict[str, list[Callable[[], None]]] = {}

    def react(self, key: str, step: Callable[[], None]) -> None:
        self.on_key.setdefault(key, []).append(step)

    async def _run_tmux(self, *args: str, **kwargs: Any):
        result = await super()._run_tmux(*args, **kwargs)
        if args and args[0] == "send-keys" and self.on_key.get(args[-1]):
            self.on_key[args[-1]].pop(0)()
        return result


class Session:
    def __init__(self, tmp_path: Path) -> None:
        self.transport = KeyedTransport(str(tmp_path))
        self.transport._claude_native_session_id = NATIVE
        self.transcript = tmp_path / f"{NATIVE}.jsonl"
        self.transcript.write_text("")
        self.events: list[dict[str, Any]] = []

    async def __aenter__(self) -> Session:
        self.events = await _collect_events(self.transport)
        await self.transport.start()
        self.transport.capture_stdout = COMPOSER
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

    async def ask(self, first_screen: str = "") -> str:
        """Claude asks the two questions in the turn it is running."""
        self.transport._begin_turn()
        self.record(
            {
                "type": "assistant",
                "uuid": "a1",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": QTOOL,
                            "name": "AskUserQuestion",
                            "input": {"questions": QUESTIONS},
                        }
                    ],
                },
            }
        )
        self.transport.capture_stdout = first_screen or screen("00-initial")
        await self.hook(
            "PreToolUse",
            tool_name="AskUserQuestion",
            tool_input={"questions": QUESTIONS},
            tool_use_id=QTOOL,
        )
        [card] = _ask_user_questions(self.events)
        return card["request_id"]

    def result(self, *, answers: dict | None, content: str, is_error: bool) -> None:
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
                            "tool_use_id": QTOOL,
                            "content": content,
                            "is_error": is_error,
                        }
                    ],
                },
                "toolUseResult": (
                    {"questions": QUESTIONS, "answers": answers, "annotations": {}}
                    if answers is not None
                    else "User rejected tool use"
                ),
            }
        )

    def answered(self, answers: dict) -> Callable[[], None]:
        def step() -> None:
            pairs = ", ".join(f'"{q}"="{a}"' for q, a in answers.items())
            self.result(
                answers=answers,
                content=f"Your questions have been answered: {pairs}. You can now continue "
                "with these answers in mind.",
                is_error=False,
            )
            self.transport.capture_stdout = COMPOSER

        return step

    def chat_chosen(self) -> None:
        """ "Chat about this": the tool fails, and Claude goes on in the same turn."""
        self.result(answers=None, content=CHAT_RESULT, is_error=True)
        self.transport.capture_stdout = COMPOSER

    def declined(self) -> None:
        """Esc: the tool fails, the turn is interrupted and ends with no Stop hook."""
        self.result(answers=None, content=ESC_RESULT, is_error=True)
        self.record(
            {
                "type": "user",
                "uuid": "u2",
                "parentUuid": "u1",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "[Request interrupted by user for tool use]"}
                    ],
                },
            }
        )
        self.record(
            {"type": "system", "subtype": "turn_duration", "uuid": "s1", "parentUuid": "u2"}
        )
        self.transport.capture_stdout = COMPOSER

    def screen_after(self, name: str) -> Callable[[], None]:
        def step() -> None:
            self.transport.capture_stdout = screen(name)

        return step

    def keys(self) -> list[str]:
        return [args[-1] for args, _ in self.transport.commands if args and args[0] == "send-keys"]

    def typed(self) -> list[str]:
        return list(self.transport.loaded_buffers)

    def resolved(self) -> list[dict[str, Any]]:
        return [e for e in self.events if e.get("type") == "ask_user_resolved"]

    def results(self) -> list[dict[str, Any]]:
        return [e for e in self.events if e.get("type") == "result"]


# -- Partial answers ---------------------------------------------------------------------


async def test_one_of_two_answered_submits_like_claude_s_own_menu(tmp_path):
    """The 9 Nov case: the first question answered, the second left; Submit anyway."""
    async with Session(tmp_path) as s:
        rid = await s.ask()
        s.transport.react("1", s.screen_after("03-colour-red"))
        s.transport.react("Right", s.screen_after("04-review-colour-only"))
        s.transport.react("1", s.answered({COLOUR: "Red"}))  # Submit answers

        await s.transport.send_control(
            "ask_user_answer",
            request_id=rid,
            answers=[{"question": COLOUR, "answer": "Red", "option_indexes": [0]}],
            skipped=[SIZE],
        )

        assert s.keys() == ["1", "Right", "1"]
        [receipt] = s.resolved()
        assert receipt["request_id"] == rid
        assert receipt["accepted"] is True
        assert receipt["decision"] == "answered"
        assert not s.transport._pending_tty_prompts


async def test_the_second_answered_and_the_first_skipped(tmp_path):
    async with Session(tmp_path) as s:
        rid = await s.ask()
        s.transport.react("Right", s.screen_after("01-skip-colour"))
        s.transport.react("2", s.screen_after("02-review-size-only"))
        s.transport.react("1", s.answered({SIZE: "Large"}))
        await s.transport.send_control(
            "ask_user_answer",
            request_id=rid,
            answers=[{"question": SIZE, "answer": "Large", "option_indexes": [1]}],
        )
        assert s.keys() == ["Right", "2", "1"]
        assert [r["accepted"] for r in s.resolved()] == [True]


@pytest.mark.parametrize(
    ("answers", "skipped", "message"),
    [
        ([{"answer": "Red"}], None, "must name each question"),
        ([{"question": COLOUR, "answer": "Red"}], [], "skipped questions disagree"),
        ([{"question": COLOUR, "answer": "Red"}], [COLOUR], "skipped questions disagree"),
        ([], None, "at least one question"),
        ([{"question": "Pick a shape?", "answer": "Red"}], None, "does not identify"),
    ],
)
async def test_a_partial_answer_that_does_not_say_which_question_is_refused(
    tmp_path, answers, skipped, message
):
    async with Session(tmp_path) as s:
        rid = await s.ask()
        with pytest.raises(ValueError, match=message):
            await s.transport.send_control(
                "ask_user_answer", request_id=rid, answers=answers, skipped=skipped
            )
        assert s.keys() == []
        assert rid in s.transport._pending_tty_prompts


def test_the_review_page_must_show_the_warning_exactly_when_partial():
    plan = TmuxInteractiveTransport._question_answer_plans(
        QUESTIONS, [{"question": COLOUR, "answer": "Red"}]
    )
    answered = [p for p in plan if not p["skip"]]
    partial_review = screen("04-review-colour-only")
    assert TmuxInteractiveTransport._question_review_matches(partial_review, answered, partial=True)
    # The same page without Claude's warning is not a partial review we drove to.
    no_warning = partial_review.replace("⚠ You have not answered all questions", "")
    assert not TmuxInteractiveTransport._question_review_matches(no_warning, answered, partial=True)
    # And a complete answer never submits from a page that warns.
    assert not TmuxInteractiveTransport._question_review_matches(
        partial_review, answered, partial=False
    )


# -- A message while a question is live --------------------------------------------------


async def test_a_message_closes_the_question_with_chat_about_this_and_is_delivered(tmp_path):
    async with Session(tmp_path) as s:
        rid = await s.ask()
        s.transport.react("4", s.chat_chosen)

        await s.transport.send_message("Use the brand colours; size doesn't matter yet")

        assert s.keys() == ["4", "Enter"]
        assert s.typed() == ["Use the brand colours; size doesn't matter yet"]
        [closed] = s.resolved()
        assert closed["request_id"] == rid
        assert closed["accepted"] is False
        assert closed["dismissed"] is True
        assert closed["dismiss_reason"] == DISMISS_CLOSED_FOR_MESSAGE
        assert closed["dismiss_trigger"] == "delivery"
        assert closed["control_kind"] == "question"
        assert "Chat about this" in closed["dismiss_detail"]
        assert not s.transport._pending_tty_prompts
        # Claude goes on in the same turn ("what would you like to clarify?"): the
        # message steers it, and no turn was closed for it.
        assert s.transport._turn_active
        assert not s.results()
        # The tool's native result is in the record, so no card is rebuilt from it.
        results = [
            b
            for e in s.events
            for b in (e.get("message") or {}).get("content", [])
            if isinstance(b, dict) and b.get("type") == "tool_result"
        ]
        assert [(b["tool_use_id"], b["is_error"]) for b in results] == [(QTOOL, True)]


async def test_a_message_on_the_review_page_closes_it_with_esc(tmp_path):
    async with Session(tmp_path) as s:
        rid = await s.ask(first_screen=screen("04-review-colour-only"))
        s.transport.react("Escape", s.declined)

        await s.transport.send_message("Stop, I'll tell you instead")

        assert s.keys() == ["Escape", "Enter"]
        assert s.typed() == ["Stop, I'll tell you instead"]
        [closed] = s.resolved()
        assert closed["request_id"] == rid
        assert closed["dismiss_reason"] == DISMISS_CLOSED_FOR_MESSAGE
        assert "Esc" in closed["dismiss_detail"]
        # Esc ended Claude's turn with no Stop hook; the record's turn_duration closes
        # it, and the message starts the next turn.
        [ended] = s.results()
        assert ended["stop_reason"] == "native_question_declined"
        assert s.transport._turn_active


async def test_a_retry_after_an_unconfirmed_close_never_presses_again_or_types_twice(tmp_path):
    async with Session(tmp_path) as s:
        rid = await s.ask()
        # Claude takes the key but has not written its result yet.
        s.transport.react("4", lambda: setattr(s.transport, "capture_stdout", COMPOSER))

        with pytest.raises(QuestionCloseUnconfirmedError):
            await s.transport.send_message("Use the brand colours")
        assert s.keys() == ["4"] and s.typed() == []
        assert rid in s.transport._pending_tty_prompts

        # The broker retries: still no proof, so still nothing pressed or typed.
        with pytest.raises(QuestionCloseUnconfirmedError):
            await s.transport.send_message("Use the brand colours")
        assert s.keys() == ["4"] and s.typed() == []

        # The card cannot be answered meanwhile: the key may already have closed it.
        with pytest.raises(ValueError, match="closing this Claude question"):
            await s.transport.send_control(
                "ask_user_answer",
                request_id=rid,
                answers=[{"question": COLOUR, "answer": "Red"}],
            )

        s.chat_chosen()
        await s.transport.send_message("Use the brand colours")
        assert s.keys() == ["4", "Enter"]
        assert s.typed() == ["Use the brand colours"]
        assert [r["dismiss_reason"] for r in s.resolved()] == [DISMISS_CLOSED_FOR_MESSAGE]

        # A further send is an ordinary message: no question, no key, typed once more.
        await s.transport.send_message("And keep it short")
        assert s.keys() == ["4", "Enter", "Enter"]
        assert len(s.resolved()) == 1


async def test_a_message_during_a_permission_prompt_is_still_refused(tmp_path):
    async with Session(tmp_path) as s:
        s.transport._begin_turn()
        cmd = {"command": "ls"}
        await s.hook("PreToolUse", tool_name="Bash", tool_input=cmd, tool_use_id="toolu_ls")
        s.transport.capture_stdout = PERMISSION_MENU
        await s.hook("PermissionRequest", tool_name="Bash", tool_input=cmd)
        [card] = _ask_user_questions(s.events)

        with pytest.raises(NativeControlPendingError) as refused:
            await s.transport.send_message("go ahead")
        assert refused.value.details()["control_request_id"] == card["request_id"]
        assert refused.value.details()["control_kind"] == "permission"
        assert refused.value.details()["menu_state"] == MENU_LIVE
        assert s.keys() == [] and s.typed() == []


async def test_a_screen_that_is_not_this_question_gets_no_key(tmp_path):
    async with Session(tmp_path) as s:
        rid = await s.ask()
        s.transport.capture_stdout = PERMISSION_MENU  # some other menu took the screen

        with pytest.raises(NativeControlPendingError) as refused:
            await s.transport.send_message("hello")
        assert refused.value.details()["control_request_id"] == rid
        assert s.keys() == [] and s.typed() == []
        assert rid in s.transport._pending_tty_prompts


async def test_a_question_claude_already_closed_is_not_pressed_again(tmp_path):
    """LEX-1979's path is unchanged: a card whose tool already has its result just closes."""
    async with Session(tmp_path) as s:
        await s.ask()
        s.answered({COLOUR: "Red", SIZE: "Small"})()

        await s.transport.send_message("thanks")

        assert s.keys() == ["Enter"]
        assert s.typed() == ["thanks"]
        assert [r["dismiss_reason"] for r in s.resolved()] == ["resolved_in_terminal"]


async def test_the_sweep_settles_a_question_a_message_closed_after_delivery_gave_up(tmp_path):
    """The broker stops retrying; the proof lands later. The card must not stay."""
    async with Session(tmp_path) as s:
        rid = await s.ask()
        s.transport.react("Escape", lambda: setattr(s.transport, "capture_stdout", COMPOSER))
        s.transport.capture_stdout = screen("04-review-colour-only")
        with pytest.raises(QuestionCloseUnconfirmedError):
            await s.transport.send_message("never mind")

        s.declined()
        assert await s.transport.sweep_native_controls() == [rid]

        [closed] = s.resolved()
        assert closed["dismiss_reason"] == DISMISS_CLOSED_FOR_MESSAGE
        assert closed["dismiss_trigger"] == "sweep"
        assert [r["stop_reason"] for r in s.results()] == ["native_question_declined"]
        assert s.keys() == ["Escape"] and s.typed() == []


# -- Review findings ---------------------------------------------------------------------

TOPPINGS = {
    "question": "Which toppings?",
    "header": "Toppings",
    "multiSelect": True,
    "options": [
        {"label": "Cheese", "description": "Cheese topping"},
        {"label": "Ham", "description": "Ham topping"},
        {"label": "Olives", "description": "Olives topping"},
    ],
}
SIZE_SMALL_LARGE = {
    "question": SIZE,
    "header": "Size",
    "multiSelect": False,
    "options": [
        {"label": "Small", "description": "Small size"},
        {"label": "Large", "description": "Large size"},
    ],
}


async def _ask(s: Session, questions: list, first_screen: str) -> str:
    s.transport._begin_turn()
    s.transport.capture_stdout = first_screen
    await s.hook(
        "PreToolUse",
        tool_name="AskUserQuestion",
        tool_input={"questions": questions},
        tool_use_id=QTOOL,
    )
    return _ask_user_questions(s.events)[-1]["request_id"]


async def test_a_key_that_did_not_close_the_menu_gives_the_card_back(tmp_path):
    async with Session(tmp_path) as s:
        rid = await s.ask()
        # "4" reaches Claude, but the menu stays: the key did not take.
        with pytest.raises(QuestionCloseUnconfirmedError):
            await s.transport.send_message("hello")
        assert s.keys() == ["4"]
        state = [e for e in s.events if e.get("type") == "native_control_state"][-1]
        assert state["answerable"] is False  # closing: no client may answer meanwhile

        # Past the wait, the same question is still on screen: the card is the person's again.
        with pytest.raises(NativeControlPendingError):
            await s.transport.send_message("hello")
        assert s.keys() == ["4"] and s.typed() == []
        assert s.transport.list_native_controls()["counts"]["question_close.not_taken"] == 1
        assert s.transport._native_control_view(rid, s.transport._pending_tty_prompts[rid])[
            "answerable"
        ]

        # The next message closes it anew, and this time Claude takes it.
        s.transport.react("4", s.chat_chosen)
        await s.transport.send_message("hello")
        assert s.keys() == ["4", "4", "Enter"] and s.typed() == ["hello"]


async def test_a_question_page_without_its_chat_row_gets_no_key(tmp_path):
    async with Session(tmp_path) as s:
        cropped = screen("00-initial").replace("  4. Chat about this\n", "")
        await s.ask(first_screen=cropped)
        with pytest.raises(NativeControlPendingError):
            await s.transport.send_message("hello")
        assert s.keys() == [] and s.typed() == []


async def test_a_multi_select_page_closes_with_its_own_chat_row(tmp_path):
    """Captured live: on a checkbox page "Chat about this" is row 5, and its digit closes."""
    async with Session(tmp_path) as s:
        await _ask(s, [TOPPINGS, SIZE_SMALL_LARGE], screen("10-checkbox-initial"))
        s.transport.react("5", s.chat_chosen)
        await s.transport.send_message("add mushrooms")
        assert s.keys() == ["5", "Enter"] and s.typed() == ["add mushrooms"]
        assert [r["dismiss_reason"] for r in s.resolved()] == [DISMISS_CLOSED_FOR_MESSAGE]


async def test_a_skipped_multi_select_page_is_passed_with_right(tmp_path):
    async with Session(tmp_path) as s:
        rid = await _ask(s, [TOPPINGS, SIZE_SMALL_LARGE], screen("10-checkbox-initial"))
        review = "\n".join(
            [
                "←  ☐ Toppings  ☒ Size  ✔ Submit  →",
                "",
                "Review your answers",
                "",
                "⚠ You have not answered all questions",
                "",
                " ● Pick a size?",
                "   → Large",
                "",
                "Ready to submit your answers?",
                "",
                "❯ 1. Submit answers",
                "  2. Cancel",
            ]
        )
        s.transport.react("Right", s.screen_after("11-checkbox-skipped"))
        s.transport.react("2", lambda: setattr(s.transport, "capture_stdout", review))
        s.transport.react("1", s.answered({SIZE: "Large"}))
        await s.transport.send_control(
            "ask_user_answer",
            request_id=rid,
            answers=[{"question": SIZE, "answer": "Large"}],
            skipped=["Which toppings?"],
        )
        assert s.keys() == ["Right", "2", "1"]
        assert [r["accepted"] for r in s.resolved()] == [True]


async def test_two_open_cards_refuse_before_any_key(tmp_path):
    async with Session(tmp_path) as s:
        await s.ask()
        s.transport._pending_tty_prompts["tty-other"] = {
            **next(iter(s.transport._pending_tty_prompts.values())),
            "native_tool_use_id": "toolu_other",
        }
        with pytest.raises(NativeControlPendingError):
            await s.transport.send_message("hello")
        assert s.keys() == [] and s.typed() == []


async def test_a_late_hook_settles_the_close_as_closed_for_message(tmp_path):
    async with Session(tmp_path) as s:
        rid = await s.ask()
        s.transport.react("4", lambda: setattr(s.transport, "capture_stdout", COMPOSER))
        with pytest.raises(QuestionCloseUnconfirmedError):
            await s.transport.send_message("hello")

        await s.hook(
            "PostToolUseFailure",
            tool_name="AskUserQuestion",
            tool_input={"questions": QUESTIONS},
            tool_use_id=QTOOL,
            error=CHAT_RESULT,
        )
        [closed] = s.resolved()
        assert closed["request_id"] == rid
        assert closed["dismiss_reason"] == DISMISS_CLOSED_FOR_MESSAGE
        assert closed["dismiss_trigger"] == "tool_result"


async def test_a_review_page_with_every_question_skipped_closes_with_esc(tmp_path):
    async with Session(tmp_path) as s:
        empty_review = screen("04-review-colour-only").replace(" ● Pick a colour?\n   → Red\n", "")
        await s.ask(first_screen=empty_review)
        s.transport.react("Escape", s.declined)
        await s.transport.send_message("never mind")
        assert s.keys() == ["Escape", "Enter"] and s.typed() == ["never mind"]


async def test_esc_waits_for_the_end_of_claude_s_turn_before_typing(tmp_path):
    """The declined turn is closed first, so the message never merges into it."""
    async with Session(tmp_path) as s:
        await s.ask(first_screen=screen("04-review-colour-only"))

        def result_without_turn_end() -> None:
            s.result(answers=None, content=ESC_RESULT, is_error=True)
            s.transport.capture_stdout = COMPOSER

        s.transport.react("Escape", result_without_turn_end)
        with pytest.raises(QuestionCloseUnconfirmedError):
            await s.transport.send_message("never mind")
        assert s.typed() == [] and not s.results() and not s.resolved()

        s.record(
            {
                "type": "user",
                "uuid": "u2",
                "parentUuid": "u1",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "[Request interrupted by user for tool use]"}
                    ],
                },
            }
        )
        s.record({"type": "system", "subtype": "turn_duration", "uuid": "s1", "parentUuid": "u2"})
        await s.transport.send_message("never mind")
        assert s.keys() == ["Escape", "Enter"] and s.typed() == ["never mind"]
        assert [r["stop_reason"] for r in s.results()] == ["native_question_declined"]
        assert [r["dismiss_reason"] for r in s.resolved()] == [DISMISS_CLOSED_FOR_MESSAGE]
