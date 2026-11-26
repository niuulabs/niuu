"""A typed ("Type something") answer long enough to wrap onto a second line.

The screens are Claude Code v2.1.311 at 200 columns, recorded while answering a
two-question form: a checkbox page ("Next" under its options) whose typed answer
wraps, then a single-select page. Before the fix Skuld compared the typed text
with one screen line, looked only for "Submit" under the checkboxes and expected
the typed answer bare, while Claude quotes it on a checkbox page. After the
keys were sent, any of those made the answer uncertain and the card refused
every later answer.
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

CUSTOM = (
    "The ones I want are: hanger style belt tightener, counter-weighted belt tightener, "
    "bolted flange couplings, plain mule stand, shaft collars, steel rim split pulleys of "
    "different sizes, extension wall brackets, pillow blocks of different sizes, the belt "
    "repair bench."
)
FORM = [
    {
        "question": "Which line shaft pieces should I model first?",
        "header": "Pieces",
        "options": [
            {"label": "Countershaft + shifter"},
            {"label": "Wood split pulleys"},
            {"label": "Coupling + collars"},
        ],
        "multiSelect": True,
    },
    {
        "question": "Where should they live in Stokk?",
        "header": "Stokk",
        "options": [{"label": "New design per item"}, {"label": "One 'Line Shaft Set' design"}],
        "multiSelect": False,
    },
]
BELT = [
    {
        "question": "Which belt should the shifter carry?",
        "header": "Belt",
        "options": [{"label": "Leather"}, {"label": "Canvas"}],
        "multiSelect": False,
    }
]
BELT_TEXT = (
    "A plain flat leather belt, double-ply, laced with rawhide at the joint, wide enough to "
    "cover the whole face of the tight pulley and the loose pulley beside it on the "
    "countershaft, as the catalogue shows."
)


def wrapped(name: str) -> str:
    return screen(f"wrapped-{name}")


@pytest.fixture
async def native_bridge(tmp_path):
    transport = NativeQuestionTransport(str(tmp_path))
    events = await _collect_events(transport)
    await transport.start()
    try:
        yield transport, events
    finally:
        await transport.stop()


async def test_a_wrapped_checkbox_answer_goes_through_next_and_the_review(native_bridge):
    transport, events = native_bridge
    transport.capture_stdout = wrapped("01-checkbox-initial")
    rid = await transport.surface(FORM)
    transport.steps.extend(
        [
            ("Down", wrapped("02-checkbox-down1")),
            ("Down", wrapped("03-checkbox-down2")),
            ("Down", wrapped("04-checkbox-other")),
            ("paste", wrapped("05-checkbox-after-paste")),
            ("Down", wrapped("06-checkbox-next")),
            ("Enter", wrapped("07-second-page")),
            ("2", wrapped("08-review")),
            ("1", "❯\n"),
        ]
    )
    # What Claude recorded for this form: the checkbox page's typed answer quoted.
    transport.consumed = {
        "answers": {
            FORM[0]["question"]: f'"{CUSTOM}"',
            FORM[1]["question"]: FORM[1]["options"][1]["label"],
        }
    }
    await transport.send_control(
        "ask_user_answer",
        request_id=rid,
        answers=[
            {"question": FORM[0]["question"], "answer": [CUSTOM], "free_text": CUSTOM},
            {
                "question": FORM[1]["question"],
                "answer": "One 'Line Shaft Set' design",
                "option_indexes": [1],
            },
        ],
    )
    assert _send_keys(transport) == ["Down", "Down", "Down", "Down", "Enter", "2", "1"]
    assert transport.loaded_buffers == [CUSTOM]
    assert not transport.steps
    assert receipts(events)[-1]["accepted"] is True


async def test_a_wrapped_single_select_answer_is_typed_and_committed(native_bridge):
    transport, events = native_bridge
    other = wrapped("11-single-other")
    transport.capture_stdout = other.replace("❯ 3.", "  3.").replace("  1. Leather", "❯ 1. Leather")
    rid = await transport.surface(BELT)
    transport.steps.extend(
        [("3", other), ("paste", wrapped("12-single-after-paste")), ("Enter", "❯\n")]
    )
    transport.consumed = {"answers": {BELT[0]["question"]: BELT_TEXT}}
    await transport.send_control(
        "ask_user_answer",
        request_id=rid,
        answers=[{"answer": BELT_TEXT, "free_text": BELT_TEXT, "option_indexes": []}],
    )
    assert _send_keys(transport) == ["3", "Enter"]
    assert receipts(events)[-1] == {
        "type": "ask_user_resolved",
        "event_type": "ask_user.resolved",
        "request_id": rid,
        "decision": BELT_TEXT,
        "accepted": True,
        "metadata": {"source": "tmux_tty_bridge"},
    }


def test_a_wrapped_row_is_read_whole_and_an_option_without_its_description():
    after_paste = wrapped("05-checkbox-after-paste")
    assert TmuxInteractiveTransport._row_text(after_paste, 4, focused=True) == CUSTOM
    assert TmuxInteractiveTransport._row_text(after_paste, 1, focused=True) is None
    plan = {
        "text": FORM[0]["question"],
        "labels": [option["label"] for option in FORM[0]["options"]],
        "multi": True,
    }
    assert TmuxInteractiveTransport._page_options(after_paste, plan) == {
        "Countershaft + shifter": (1, False),
        "Wood split pulleys": (2, False),
        "Coupling + collars": (3, False),
    }
    assert TmuxInteractiveTransport._ticked_answers(after_paste, plan) == {CUSTOM}


def test_different_typed_text_does_not_pass_for_the_wrapped_answer():
    after_paste = wrapped("05-checkbox-after-paste")
    shown = TmuxInteractiveTransport._row_text(after_paste, 4, focused=True)
    assert shown is not None
    assert shown.split() != CUSTOM.replace("pillow blocks", "pillow boxes").split()


@pytest.mark.parametrize(
    ("actual", "accepted"),
    [
        (f'"{CUSTOM}"', True),  # Claude's own record of a typed checkbox answer
        (f'"{CUSTOM} extra"', False),
        (f'Countershaft + shifter, "{CUSTOM}"', False),  # an option nobody chose
    ],
)
def test_a_typed_checkbox_answer_is_proven_in_claude_s_quoted_form(actual, accepted):
    plan = {"values": [CUSTOM], "custom": CUSTOM, "multi": True}
    assert TmuxInteractiveTransport._question_answer_matches(actual, plan) is accepted
