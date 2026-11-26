"""The check that a screen is a question's page before Skuld presses its keys.

It guards against pressing a digit into another screen, where the same key does
something else. It does not depend on Claude's layout: what Claude recorded is
proven afterwards from its tool result. The preview screens are Claude Code
v2.1.311 at 200 columns, a two-page form whose first question has previews.
"""

from __future__ import annotations

import pytest

from skuld.transports.tmux_interactive import TmuxInteractiveTransport
from tests.test_skuld.test_tmux_interactive_transport import _collect_events, _send_keys
from tests.test_skuld.test_tmux_native_question_consumption import (
    NativeQuestionTransport,
    receipts,
    screen,
)
from tests.test_skuld.test_tmux_side_panel import with_panel

KIT = (
    "The floor stands only fit a heavy main line shaft, and the countershafts must hang "
    "from timbers overhead. Which scene should the hero kit be?"
)
FORM = [
    {
        "question": KIT,
        "header": "Kit concept",
        "options": [
            {"label": "Mill drive bay (Recommended)", "preview": "TOP VIEW"},
            {"label": "Open display layout", "preview": "SIDE VIEW"},
            {"label": "Two modules", "preview": "MODULE 1: basement drive"},
        ],
        "multiSelect": False,
    },
    {
        "question": "Where should the kit live in Stokk?",
        "header": "Stokk",
        "options": [{"label": "New design"}, {"label": "Line Shaft Set"}],
        "multiSelect": False,
    },
]
ANSWERS = [
    {"question": KIT, "answer": "Two modules", "option_indexes": [2]},
    {"question": FORM[1]["question"], "answer": "Line Shaft Set", "option_indexes": [1]},
]
CONSUMED = {"answers": {KIT: "Two modules", FORM[1]["question"]: "Line Shaft Set"}}


def preview(name: str) -> str:
    return screen(f"preview-{name}")


def kit_plan() -> dict:
    return {
        "question": FORM[0],
        "text": KIT,
        "labels": [option["label"] for option in FORM[0]["options"]],
        "multi": False,
    }


@pytest.fixture
async def native_bridge(tmp_path):
    transport = NativeQuestionTransport(str(tmp_path))
    events = await _collect_events(transport)
    await transport.start()
    try:
        yield transport, events
    finally:
        await transport.stop()


async def answer(transport, rid, answers=ANSWERS):
    await transport.send_control("ask_user_answer", request_id=rid, answers=answers)


@pytest.mark.parametrize("panel", [False, True])
async def test_a_preview_option_is_focused_then_chosen(native_bridge, panel):
    transport, events = native_bridge

    def shown(text: str) -> str:
        return with_panel(text, width=200) if panel else text

    transport.capture_stdout = shown(preview("01-initial"))
    rid = await transport.surface(FORM)
    transport.steps.extend(
        [
            ("3", shown(preview("02-focus-3"))),
            ("Enter", shown(preview("03-second-page"))),
            ("2", shown(preview("04-review"))),
            ("1", "❯\n"),
        ]
    )
    transport.consumed = CONSUMED
    await answer(transport, rid)
    assert _send_keys(transport) == ["3", "Enter", "2", "1"]
    assert receipts(events)[-1]["accepted"] is True


def test_a_wrapped_label_beside_a_preview_is_found_at_its_position():
    assert TmuxInteractiveTransport._page_options(preview("01-initial"), kit_plan()) == {
        "Mill drive bay (Recommended)": (1, None),
        "Open display layout": (2, None),
        "Two modules": (3, None),
    }


async def test_a_typed_answer_to_a_preview_question_is_refused_before_any_key(native_bridge):
    transport, _ = native_bridge
    transport.capture_stdout = preview("01-initial")
    rid = await transport.surface(FORM)
    typed = {"question": KIT, "answer": "A mill pond", "free_text": "A mill pond"}
    with pytest.raises(ValueError, match="no typed answer"):
        await answer(transport, rid, [typed, ANSWERS[1]])
    assert _send_keys(transport) == []


PERMISSION = (
    "Do you want to proceed?\n"
    "❯ 1. Yes\n"
    "  2. Yes, and don't ask again for this command\n"
    "  3. No, and tell Claude what to do differently (esc)\n"
)


@pytest.mark.parametrize(
    "shown",
    [
        pytest.param(PERMISSION, id="a permission prompt"),
        pytest.param("❯ \n  ? for shortcuts\n", id="the chat box"),
        pytest.param(
            preview("01-initial").replace("Which scene", "Which era"), id="another question"
        ),
        pytest.param(
            preview("01-initial")
            .replace("2. Open display layout", "2. Two modules      ")
            .replace("3. Two modules", "3. Open display layout"),
            id="options in another order",
        ),
        pytest.param(
            preview("01-initial").replace("3. Two modules  ", "3. Two modules ok"),
            id="a row that is more than its label",
        ),
    ],
)
async def test_no_key_is_pressed_into_another_screen(native_bridge, shown):
    transport, _ = native_bridge
    transport.capture_stdout = shown
    rid = await transport.surface(FORM)
    with pytest.raises(ValueError, match="does not match"):
        await answer(transport, rid)
    assert _send_keys(transport) == []


async def test_a_typed_answer_without_its_row_on_screen_sends_no_key(native_bridge):
    transport, _ = native_bridge
    stokk = FORM[1]
    transport.capture_stdout = preview("03-second-page").replace("  3. Type something.\n", "")
    rid = await transport.surface([stokk])
    typed = {"question": stokk["question"], "answer": "A shed", "free_text": "A shed"}
    with pytest.raises(
        Exception, match="no row for a typed answer|answer consumption is uncertain"
    ):
        await answer(transport, rid, [typed])
    assert _send_keys(transport) == []


def test_a_page_missing_an_option_or_of_the_other_kind_is_not_this_page():
    plan = kit_plan()
    missing = preview("01-initial").replace("  3. Two modules", "  Two modules   ")
    assert TmuxInteractiveTransport._page_options(missing, plan) is None
    checkbox = screen("wrapped-01-checkbox-initial")
    single = {**plan, "labels": ["Countershaft + shifter"], "text": "Which line shaft pieces"}
    assert TmuxInteractiveTransport._page_options(checkbox, single) is None


async def test_a_typed_checkbox_answer_without_its_row_sends_no_key(native_bridge):
    transport, _ = native_bridge
    pieces = {
        "question": "Which line shaft pieces should I model first?",
        "header": "Pieces",
        "options": [
            {"label": "Countershaft + shifter"},
            {"label": "Wood split pulleys"},
            {"label": "Coupling + collars"},
        ],
        "multiSelect": True,
    }
    transport.capture_stdout = screen("wrapped-01-checkbox-initial").replace(
        "  4. [ ] Type something\n", ""
    )
    rid = await transport.surface([pieces])
    typed = {"question": pieces["question"], "answer": ["A shed"], "free_text": "A shed"}
    with pytest.raises(Exception, match="no custom answer row|answer consumption is uncertain"):
        await answer(transport, rid, [typed])
    assert _send_keys(transport) == []
