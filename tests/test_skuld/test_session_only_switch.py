"""LEX-2022: a running Claude session switches model and effort for itself alone.

A typed `/model X` or `/effort X` saves X as the default for every new Claude Code
session on the host. The pickers' "s" (this session only) does not, so the switch
drives the pickers. Screens are Claude Code 2.1.321 captures.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from skuld.transports.tmux_interactive import (
    SESSION_SWITCH_MODELS,
    TmuxInteractiveTransport,
    _confirm_dialog,
    _picker_rows,
    _slider_level,
)

PICKER = """\
❯ /model
────
  Select model
  Switch between Claude models. Your pick becomes the default for new sessions.
    1. Default (recommended)  Use the default model (currently Opus 5.5) · $4/$20 per Mtok
  {c2} 2. Opus ✔                 Opus 5.5 · Best for everyday, complex tasks · $4/$20 per Mtok
  {c3} 3. Fable                  Fable 5.2 · Most capable for your hardest tasks · $8/$40 per Mtok
    4. Sonnet                 Sonnet 5.5 · Efficient for routine tasks · $2/$10 per Mtok
  ◉ xHigh effort ←/→ to adjust
  Enter to set as default · s to use this session only · Esc to cancel
"""

SLIDER = """\
❯ /effort
────
  Effort
                         Faster                             Smarter
                         {bar}      Ultracode  off
                         low     medium     high     xhigh      max      Tab to toggle
  ←/→ to adjust · Enter to confirm · s for this session only · Esc to cancel
"""
_BAR_XHIGH = "──────────────────────────────▲───────────"
_BAR_HIGH = "────────────────────▲─────────────────────"


def _picker(cursor: int) -> str:
    return PICKER.format(c2="❯" if cursor == 2 else " ", c3="❯" if cursor == 3 else " ")


class _Pane:
    """Feeds the transport the screens a real CLI would show after each key."""

    def __init__(self, screens: list[str]) -> None:
        self.screens = screens
        self.keys: list[str] = []

    async def capture(self, *_a, **_k) -> str:
        return self.screens[0] if len(self.screens) == 1 else self.screens.pop(0)

    async def key(self, key: str, **_k) -> None:
        self.keys.append(key)


def _claude(tmp_path, pane: _Pane, model="claude-opus-5-5") -> TmuxInteractiveTransport:
    claude = TmuxInteractiveTransport(str(tmp_path), model=model, reasoning_effort="xhigh")
    claude._wait_for_repl_ready = AsyncMock()
    claude._send_slash_command = AsyncMock()
    claude._capture_pane_text = pane.capture
    claude._send_key = pane.key
    claude._pane_poll_interval_s = 0
    claude._effort_confirm_settle = 0
    return claude


def test_screens_parse():
    assert [(r[0], r[1]) for r in _picker_rows(_picker(2))] == [
        (False, 1),
        (True, 2),
        (False, 3),
        (False, 4),
    ]
    assert _slider_level(SLIDER.format(bar=_BAR_XHIGH)) == "xhigh"
    assert _slider_level(SLIDER.format(bar=_BAR_HIGH)) == "high"
    assert _slider_level("no slider here") is None


async def test_model_switch_uses_the_picker_session_only_key(tmp_path):
    ack = "❯ /model\n  ⎿  Set model to Fable 5.2 for this session only\n  ● high · /effort"
    pane = _Pane([_picker(2), _picker(3), ack])
    claude = _claude(tmp_path, pane)

    await claude.send_control("set_runtime_options", options={"model": "claude-fable-5-2"})

    claude._send_slash_command.assert_awaited_once_with("model")
    assert pane.keys == ["Down", "s"]
    assert claude._model == "claude-fable-5-2"
    assert claude._reasoning_effort == "high"  # Fable 5.2 brought its own effort


async def test_effort_switch_uses_the_slider_session_only_key(tmp_path):
    ack = "❯ /effort\n  ⎿  Set effort level to high (this session only): Comprehensive"
    pane = _Pane([SLIDER.format(bar=_BAR_XHIGH), SLIDER.format(bar=_BAR_HIGH), ack])
    claude = _claude(tmp_path, pane, model="claude-fable-5-2")

    await claude._set_effort("high")

    claude._send_slash_command.assert_awaited_once_with("effort")
    assert pane.keys == ["Left", "s"]
    assert (await claude.get_effort())["current"] == "high"


async def test_a_saved_default_is_a_hard_error(tmp_path):
    saved = "❯ /model\n  ⎿  Set model to Fable 5.2 and saved as your default for new sessions"
    pane = _Pane([_picker(2), _picker(3), saved])
    claude = _claude(tmp_path, pane)

    with pytest.raises(RuntimeError, match="saved the /model change as the host default"):
        await claude._set_model("claude-fable-5-2")


async def test_a_picker_without_the_model_is_closed_and_refused(tmp_path):
    old = _picker(2).replace("Fable 5.2", "Fable 5.1")
    pane = _Pane([old, old, "❯ /model\n  ⎿  Kept model as Opus 5.5"])
    claude = _claude(tmp_path, pane)

    with pytest.raises(RuntimeError, match="offers no Fable 5.2 row"):
        await claude._set_model("claude-fable-5-2")
    assert pane.keys == ["Escape"]


async def test_runtime_options_offer_only_session_switchable_models(tmp_path):
    claude = _claude(tmp_path, _Pane([""]), model="claude-fable-5-1")

    state = await claude.get_runtime_options()

    assert [m["id"] for m in state["models"]] == list(SESSION_SWITCH_MODELS)
    assert {m["id"]: m["default_effort"] for m in state["models"]} == {
        "claude-opus-5-5": "xhigh",
        "claude-fable-5-2": "high",
    }
    assert state["current"]["model"] == "claude-fable-5-1"
    with pytest.raises(ValueError, match="needs the session's model set"):
        await claude.send_control("set_runtime_options", options={"model": "claude-sonnet-5"})


def test_parsing_ignores_text_above_the_open_picker():
    """A model list or level names earlier in the conversation never steer the picker."""
    old_text = (
        "  ❯ 3. Fable                  Fable 5.2 · quoted in an earlier answer\n"
        "low     medium     high     xhigh      max\n"
    )
    assert [r[1] for r in _picker_rows(old_text + _picker(2)) if r[0]] == [2]
    assert _slider_level(old_text + SLIDER.format(bar=_BAR_XHIGH)) == "xhigh"


async def test_an_older_identical_ack_never_counts(tmp_path):
    """Only an acknowledgement under the latest /model echo proves this switch."""
    old = "❯ /model\n  ⎿  Set model to Fable 5.2 for this session only\n"
    pane = _Pane([old + _picker(2), old + _picker(3)])  # "s" taken, no new ack
    claude = _claude(tmp_path, pane)
    claude._effort_control_timeout = 0.05

    with pytest.raises(RuntimeError, match="did not acknowledge /model"):
        await claude._set_model("claude-fable-5-2")
    assert claude._model == "claude-opus-5-5"
    assert pane.keys[-1] == "Escape"


async def test_a_slow_redraw_is_waited_for(tmp_path):
    """The cursor check polls: a capture taken before Ink redraws is not a failure."""
    ack = "❯ /model\n  ⎿  Set model to Fable 5.2 for this session only"
    pane = _Pane([_picker(2), _picker(2), _picker(2), _picker(3), ack])
    claude = _claude(tmp_path, pane)

    await claude._set_model("claude-fable-5-2")

    assert pane.keys == ["Down", "s"]


_SUBSCRIPTION = (
    Path(__file__).resolve().parents[1]
    / "support/forge/screens/claude-model-picker-subscription.txt"
).read_text()


async def test_a_claude_ai_login_picker_is_read_by_its_model_labels(tmp_path):
    """Live LEX-2022 QA: a claude.ai login labels rows "Opus 5.5" with prose descriptions,
    and its Default row's description starts "Opus 5.5 ·". The switch must take row 2,
    never Default (where "s" does nothing)."""
    rows = _picker_rows(_SUBSCRIPTION)
    assert [(r[1], r[2]) for r in rows][:3] == [
        (1, "Default (recommended)"),
        (2, "Opus 5.5"),
        (3, "Fable 5.2"),
    ]
    moved = _SUBSCRIPTION.replace("  ❯ 3.  Fable", "    3.  Fable").replace(
        "    2.  Opus 5.5 ", "  ❯ 2.  Opus 5.5 "
    )
    ack = "❯ /model\n  ⎿  Set model to Opus 5.5 for this session only"
    pane = _Pane([_SUBSCRIPTION, moved, ack])
    claude = _claude(tmp_path, pane, model="claude-fable-5-2")

    await claude._set_model("claude-opus-5-5")

    assert pane.keys == ["Up", "s"]
    assert claude._model == "claude-opus-5-5"


async def test_a_picker_that_will_not_close_is_an_error(tmp_path):
    old = _picker(2).replace("Fable 5.2", "Fable 5.1")
    pane = _Pane([old])
    claude = _claude(tmp_path, pane)
    claude._effort_control_timeout = 0.05

    with pytest.raises(RuntimeError, match="offers no Fable 5.2 row.*still open after Esc"):
        await claude._set_model("claude-fable-5-2")


_CONFIRM = """\
❯ /model
────
  Switch model?
  Your next response will be slower and use more tokens
  This conversation is cached for the current model. Switching to Opus 5.5 means…
  ❯ 1. Yes, switch to {choice}
    2. No, go back
"""


async def test_the_cache_confirm_dialog_is_answered_only_for_the_requested_choice(tmp_path):
    """With history, Claude asks "Switch model?" after "s" (live QA, claude.ai login)."""
    moved = _SUBSCRIPTION.replace("  ❯ 3.  Fable", "    3.  Fable").replace(
        "    2.  Opus 5.5 ", "  ❯ 2.  Opus 5.5 "
    )
    ack = "❯ /model\n  ⎿  Set model to Opus 5.5 for this session only"
    dialog = _CONFIRM.format(choice="Opus 5.5")
    pane = _Pane([_SUBSCRIPTION, moved, dialog, dialog, ack])
    claude = _claude(tmp_path, pane, model="claude-fable-5-2")

    await claude._set_model("claude-opus-5-5")

    assert pane.keys == ["Up", "s", "Enter"]
    assert claude._model == "claude-opus-5-5"


async def test_a_confirm_dialog_for_another_choice_is_closed_not_answered(tmp_path):
    moved = _SUBSCRIPTION.replace("  ❯ 3.  Fable", "    3.  Fable").replace(
        "    2.  Opus 5.5 ", "  ❯ 2.  Opus 5.5 "
    )
    hook = (
        _CONFIRM.format(choice="Opus 5.5")
        .replace("❯ 1. Yes", "  1. Yes")
        .replace("    2. No", "  ❯ 2. No")
    )
    pane = _Pane(
        [_SUBSCRIPTION, moved, hook, hook, hook, _SUBSCRIPTION, "❯ /model\n  ⎿  Cancelled"]
    )
    claude = _claude(tmp_path, pane, model="claude-fable-5-2")

    with pytest.raises(RuntimeError, match="asked to confirm the /model change"):
        await claude._set_model("claude-opus-5-5")

    assert pane.keys == ["Up", "s", "Escape", "Escape"]  # dialog -> picker -> prompt
    assert claude._model == "claude-fable-5-2"


async def test_a_half_drawn_dialog_is_waited_for_not_refused(tmp_path):
    """Mini, Claude Code 2.1.322, 2026-11-15: the broker read "Switch model?" drawn over
    the picker's rows, whose cursor row is not "Yes, switch to …", refused the switch
    and closed the dialog 60 ms after it opened. Only a settled dialog is judged."""
    moved = _moved_to_opus()
    half = "\n".join(
        "  Switch model?" if "Select model" in line else line for line in moved.splitlines()
    )
    dialog = _CONFIRM.format(choice="Opus 5.5")
    pane = _Pane([_SUBSCRIPTION, moved, half, dialog, dialog, _ACK_OPUS])
    claude = _claude(tmp_path, pane, model="claude-fable-5-2")

    await claude._set_model("claude-opus-5-5")

    assert pane.keys == ["Up", "s", "Enter"]
    assert claude._model == "claude-opus-5-5"


def _moved_to_opus() -> str:
    return _SUBSCRIPTION.replace("  ❯ 3.  Fable", "    3.  Fable").replace(
        "    2.  Opus 5.5 ", "  ❯ 2.  Opus 5.5 "
    )


_ACK_OPUS = "❯ /model\n  ⎿  Set model to Opus 5.5 for this session only"


async def test_a_boxed_dialog_that_replaced_the_echo_is_still_answered(tmp_path):
    """The dialog is read from the live screen, border or not, echo or not."""
    boxed = "\n".join(
        f"│ {line.strip():<70} │"
        for line in _CONFIRM.format(choice="Opus 5.5").splitlines()
        if line.strip() not in ("❯ /model", "────")
    )
    pane = _Pane([_SUBSCRIPTION, _moved_to_opus(), boxed, boxed, _ACK_OPUS])
    claude = _claude(tmp_path, pane, model="claude-fable-5-2")

    await claude._set_model("claude-opus-5-5")

    assert pane.keys == ["Up", "s", "Enter"]
    assert claude._model == "claude-opus-5-5"


async def test_an_enter_the_dialog_did_not_take_is_sent_again(tmp_path):
    """Mini 2026-11-10: the first Enter was lost and the switch was cancelled on timeout."""
    dialog = _CONFIRM.format(choice="Opus 5.5")
    pane = _Pane([_SUBSCRIPTION, _moved_to_opus(), dialog, dialog, dialog, _ACK_OPUS])
    claude = _claude(tmp_path, pane, model="claude-fable-5-2")

    await claude._set_model("claude-opus-5-5")

    assert pane.keys == ["Up", "s", "Enter", "Enter"]
    assert claude._model == "claude-opus-5-5"


async def test_a_dialog_is_not_answered_before_it_settles(tmp_path):
    dialog = _CONFIRM.format(choice="Opus 5.5")
    pane = _Pane([_SUBSCRIPTION, _moved_to_opus(), dialog])
    claude = _claude(tmp_path, pane, model="claude-fable-5-2")
    claude._effort_confirm_settle = 60
    claude._effort_control_timeout = 0.05

    with pytest.raises(RuntimeError, match="did not acknowledge /model"):
        await claude._set_model("claude-opus-5-5")

    assert "Enter" not in pane.keys
    assert pane.keys[:3] == ["Up", "s", "Escape"]
    assert claude._model == "claude-fable-5-2"


def test_prose_that_quotes_the_title_is_not_a_dialog():
    assert _confirm_dialog("● It asked Switch model? and I said yes\n❯ ") is None
    assert _confirm_dialog(
        "  Switch model?\n  ❯ 1. Yes, switch to Fable 5.2\n    2. No\n ⏵⏵ 3s"
    ) == [
        "Switch model?",
        "❯ 1. Yes, switch to Fable 5.2",
        "2. No",
    ]
