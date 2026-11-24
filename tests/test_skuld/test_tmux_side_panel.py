"""Claude Code's side panel (its diff view) beside a question or permission menu.

The panel is drawn in the same tmux pane, to the right of the conversation, with a
"│" divider in one column on every row. tmux captures it as part of each line, so
before the crop every question text and menu label ended in the panel's text and
every answer was refused with "The live Claude menu does not match".
"""

from __future__ import annotations

import logging
import unicodedata

import pytest

from skuld.transports.tmux_interactive import _without_side_panel
from tests.test_skuld.test_tmux_interactive_transport import _collect_events, _send_keys
from tests.test_skuld.test_tmux_native_question_consumption import (
    SINGLE,
    NativeQuestionTransport,
    receipts,
)
from tests.test_skuld.test_tmux_question_validation import _live_permission_card

PANEL_ROWS = [
    "                                                                ✕",
    "50 files changed                                  source: Current ▾",
    "",
    ".lexi/attachments/11152F48-Screenshot.png",
    "↓ 42 more below (opt+↓ to scroll)",
    "──────────────────────────────────────────",
    ".lexi/attachments/1D9A7F58-Pasted Image.png (untracked)",
    "New file not yet staged.",
    "Stage it with git add to see line counts.",
]


def with_panel(left: str, *, width: int = 60) -> str:
    """Draw a side panel beside ``left`` the way Claude Code does: a divider at ``width``."""
    rows = left.splitlines()
    out = []
    for index, row in enumerate(rows):
        panel = PANEL_ROWS[index % len(PANEL_ROWS)]
        fill = "─" if row and set(row) == {"─"} else " "
        out.append(row + fill * (width - _width(row)) + "│" + panel)
    return "\n".join(out) + "\n"


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)


QUESTION_SCREEN = (
    "  Ran 3 shell commands\n"
    + "─" * 60
    + "\n"
    + " ☐ Label\n"
    + "\n"
    + "│ Which label should be written?\n"
    + "\n"
    + "❯ 1. Blue\n"
    + "     Paint it blue.\n"
    + "  2. Amber\n"
    + "     Paint it amber.\n"
    + "  3. Type something.\n"
    + "─" * 60
    + "\n"
    + "  4. Chat about this\n"
    + "\n"
    + "Enter to select · ↑/↓ to navigate · Esc to cancel"
)


@pytest.fixture
async def native_bridge(tmp_path):
    transport = NativeQuestionTransport(str(tmp_path))
    events = await _collect_events(transport)
    await transport.start()
    try:
        yield transport, events
    finally:
        await transport.stop()


def test_a_side_panel_is_cropped_from_every_row():
    cropped = _without_side_panel(with_panel(QUESTION_SCREEN))
    assert cropped == "\n".join(row.rstrip() for row in QUESTION_SCREEN.splitlines())


def test_wide_characters_before_the_divider_are_measured_in_terminal_columns():
    left = "☐ 标签\n│ 写哪个标签？✨\n❯ 1. Blue\n  2. Amber"
    cropped = _without_side_panel(with_panel(left, width=40))
    assert cropped == left


@pytest.mark.parametrize(
    "screen",
    [
        "",
        QUESTION_SCREEN,  # the question's own "│ " gutter is column 0, never a divider
        "a │ b\n\nc │ d",  # a blank row: nothing is drawn beside it
        "a │ b\nc   │ d",  # bars in different columns
        "abc\nde │ f",  # a row without the bar
    ],
)
def test_a_screen_without_a_full_height_divider_is_unchanged(screen):
    assert _without_side_panel(screen) == screen


async def test_a_question_beside_the_diff_panel_is_answered(native_bridge):
    transport, events = native_bridge
    transport.capture_stdout = with_panel(QUESTION_SCREEN)
    rid = await transport.surface()
    transport.steps.append(("2", "❯\n"))
    transport.consumed = {"answers": {SINGLE[0]["question"]: "Amber"}}
    await transport.send_control(
        "ask_user_answer",
        request_id=rid,
        answers=[{"question": SINGLE[0]["question"], "answer": "Amber", "option_indexes": [1]}],
    )
    assert _send_keys(transport) == ["2"]
    assert receipts(events)[-1]["accepted"] is True


async def test_a_refused_answer_logs_the_screen_it_was_judged_on(native_bridge, caplog):
    transport, _ = native_bridge
    transport.capture_stdout = with_panel(
        QUESTION_SCREEN.replace("Which label should be written?", "A different question?")
    )
    rid = await transport.surface()
    with caplog.at_level(logging.ERROR, logger="skuld.transport"):
        with pytest.raises(ValueError, match="does not match"):
            await transport.send_control(
                "ask_user_answer", request_id=rid, answers=[{"answer": "Blue"}]
            )
    assert not _send_keys(transport)
    logged = [r.getMessage() for r in caplog.records if "did not match" in r.getMessage()]
    assert logged and "A different question?" in logged[-1]
    assert "50 files changed" not in logged[-1]


async def test_a_refused_answer_with_no_question_on_screen_says_so(native_bridge, caplog):
    transport, _ = native_bridge
    transport.capture_stdout = "❯ \n"
    rid = await transport.surface()
    with caplog.at_level(logging.ERROR, logger="skuld.transport"):
        with pytest.raises(ValueError, match="does not match"):
            await transport.send_control(
                "ask_user_answer", request_id=rid, answers=[{"answer": "Blue"}]
            )
    assert any("no question header on screen" in r.getMessage() for r in caplog.records)


async def test_a_permission_menu_beside_the_diff_panel_offers_its_own_rows(native_bridge):
    transport, events = native_bridge
    menu = with_panel(
        "Do you want to proceed?\n"
        "❯ 1. Yes\n"
        "  2. Yes, and always allow access to shooter/ from this project\n"
        "  3. No, and tell Claude what to do differently (esc)",
        width=80,
    )
    _, live = await _live_permission_card(transport, events, menu)
    assert [o["label"] for o in live["questions"][0]["options"]] == [
        "Yes",
        "Yes, and always allow access to shooter/ from this project",
        "No, and tell Claude what to do differently (esc)",
    ]
    transport.steps.append(("2", "❯\n"))
    label = "Yes, and always allow access to shooter/ from this project"
    await transport.send_control(
        "ask_user_answer", request_id=live["request_id"], answers=[{"answer": label}]
    )
    assert _send_keys(transport) == ["2"]


async def test_the_composer_beside_the_diff_panel_reads_as_ready(native_bridge):
    transport, _ = native_bridge
    transport.capture_stdout = with_panel("─" * 40 + "\n❯ \n" + "─" * 40 + "\n  ? for shortcuts")
    text = transport._clean_terminal_text(await transport._capture_pane_text())
    assert transport._repl_looks_ready(text)
    assert "files changed" not in text
