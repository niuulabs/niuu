"""LEX-1950: permission answers land on the live Claude Code 2.1.319 menu.

Damien answered a permission card from his phone with "Allow & don't ask again" and was
told "The requested answer does not match the live Claude menu". Two causes, both from
real menus captured from Claude Code 2.1.319 (``tests/fixtures/claude-permission-menus-2.1.319``):

- the card always offered Allow / Allow & don't ask again / Deny, but the prompt he
  answered (a person-only ``rm -rf $P2`` check under bypassPermissions) offers only
  Yes / No;
- the Bash menu writes "don’t ask again" with a typographic apostrophe, and other menus
  word the persistent row "always allow access to <dir> from this project" or "switch to
  accept edits … for this session", none of which matched.

The card now offers the live menu's own rows, and the generic answers map onto every
variant.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from skuld.transports.tmux_interactive import TmuxInteractiveTransport
from tests.test_skuld.test_tmux_interactive_transport import (
    FakeTmuxInteractiveTransport,
    _ask_user_questions,
    _collect_events,
    _send_keys,
)

MENUS = Path(__file__).parents[1] / "fixtures" / "claude-permission-menus-2.1.319"


def _menu(name: str) -> str:
    return (MENUS / f"{name}.txt").read_text(encoding="utf-8")


async def _card(tmp_path: Path, screen: str, **hook: Any):
    transport = FakeTmuxInteractiveTransport(str(tmp_path))
    events = await _collect_events(transport)
    await transport.start()
    transport.capture_stdout = screen
    await transport.handle_claude_hook(
        {
            "hook_event_name": "PermissionRequest",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            **hook,
        }
    )
    [card] = _ask_user_questions(events)
    return transport, events, card


def _labels(card: dict[str, Any]) -> list[str]:
    return [option["label"] for option in card["questions"][0]["options"]]


async def _answer(transport, card, answer: str) -> None:
    await transport.send_control(
        "ask_user_answer", request_id=card["request_id"], answers=[{"answer": answer}]
    )


@pytest.mark.parametrize(
    "menu,rows",
    [
        (
            "bash_plain",
            [
                "Yes",
                "Yes, and don’t ask again for: python3 *",
                "Yes, and switch to auto mode · auto mode handles these prompts for you",
                "No",
            ],
        ),
        (
            "bash_outside_cwd",
            [
                "Yes",
                "Yes, and always allow access to /home/thor/tmp/lex1950/outside from this project",
                "Yes, and switch to auto mode · auto mode handles these prompts for you",
                "No",
            ],
        ),
        (
            "edit",
            [
                "Yes",
                "Yes, and switch to accept edits (auto-approve file edits and common file "
                "commands) for this session (shift+tab)",
                "No",
            ],
        ),
        (
            "webfetch",
            [
                "Yes",
                "Yes, and don't ask again for example.com",
                "No, and tell Claude what to do differently (esc)",
            ],
        ),
        (
            "mcp",
            [
                "Yes",
                "Yes, and don't ask again for gbrain — Get Stats commands in "
                "/home/thor/tmp/lex1950/proj",
                "No",
            ],
        ),
        ("bypass_dangerous_rm", ["Yes", "No"]),
    ],
)
async def test_the_card_offers_the_live_menu_rows(tmp_path, menu, rows):
    transport, _, card = await _card(tmp_path, _menu(menu))
    assert _labels(card) == rows
    await transport.stop()


@pytest.mark.parametrize(
    "menu,key",
    [
        ("bash_plain", "2"),  # "don’t ask again", typographic apostrophe
        ("bash_outside_cwd", "2"),  # "always allow access … from this project", not auto mode
        ("edit", "2"),  # "switch to accept edits … for this session"
        ("webfetch", "2"),
        ("mcp", "2"),
    ],
)
async def test_generic_dont_ask_again_takes_the_persistent_row(tmp_path, menu, key):
    transport, events, card = await _card(tmp_path, _menu(menu))
    await _answer(transport, card, "Allow & don't ask again")
    assert _send_keys(transport) == [key]
    assert any(
        e.get("type") == "ask_user_resolved" and e["request_id"] == card["request_id"]
        for e in events
    )
    await transport.stop()


@pytest.mark.parametrize(
    "menu", ["bash_plain", "bash_outside_cwd", "edit", "webfetch", "mcp", "bypass_dangerous_rm"]
)
async def test_generic_allow_and_deny_map_on_every_captured_menu(tmp_path, menu):
    transport, _, card = await _card(tmp_path, _menu(menu))
    await _answer(transport, card, "Allow")
    assert _send_keys(transport) == ["1"]
    await transport.stop()

    transport, events, card = await _card(tmp_path, _menu(menu))
    await _answer(transport, card, "Deny")
    assert _send_keys(transport) == ["Escape"]
    resolved = [e for e in events if e.get("type") == "ask_user_resolved"]
    assert [e["decision"] for e in resolved] == ["deny"]
    await transport.stop()


async def test_a_live_label_is_answered_by_its_own_row(tmp_path):
    transport, events, card = await _card(tmp_path, _menu("bash_plain"))
    await _answer(transport, card, "Yes, and don’t ask again for: python3 *")
    assert _send_keys(transport) == ["2"]
    resolved = [e for e in events if e.get("type") == "ask_user_resolved"]
    assert [e["decision"] for e in resolved] == ["Yes, and don’t ask again for: python3 *"]
    await transport.stop()


async def test_the_live_no_row_declines_with_escape(tmp_path):
    transport, events, card = await _card(tmp_path, _menu("webfetch"))
    await _answer(transport, card, "No, and tell Claude what to do differently (esc)")
    assert _send_keys(transport) == ["Escape"]
    resolved = [e for e in events if e.get("type") == "ask_user_resolved"]
    assert [e["decision"] for e in resolved] == ["deny"]
    await transport.stop()


async def test_switching_to_auto_mode_is_never_the_persistent_answer(tmp_path):
    screen = "\n".join(
        [
            " Do you want to proceed?",
            " ❯ 1. Yes",
            "   2. Yes, and switch to auto mode · auto mode handles these prompts for you",
            "   3. No",
        ]
    )
    transport, _, card = await _card(tmp_path, screen)
    with pytest.raises(ValueError, match='has no "Allow & don\'t ask again"'):
        await _answer(transport, card, "Allow & don't ask again")
    assert _send_keys(transport) == []
    assert card["request_id"] in transport._pending_tty_prompts
    await transport.stop()


async def test_damiens_person_only_prompt_names_its_live_options(tmp_path):
    """The 13:46 case: "Allow & don't ask again" on a Yes / No person-only prompt."""
    transport, _, card = await _card(
        tmp_path, _menu("bypass_dangerous_rm"), permission_mode="bypassPermissions"
    )
    assert _labels(card) == ["Yes", "No"]
    with pytest.raises(ValueError) as refused:
        await _answer(transport, card, "Allow & don't ask again")
    assert str(refused.value) == (
        'The live Claude menu has no "Allow & don\'t ask again"; no keys were sent. '
        'It offers "Yes", "No": choose one of those'
    )
    assert _send_keys(transport) == []
    await _answer(transport, card, "Yes")
    assert _send_keys(transport) == ["1"]
    await transport.stop()


async def test_a_menu_drawn_late_still_maps_and_a_mismatch_resends_the_live_rows(tmp_path):
    transport = FakeTmuxInteractiveTransport(str(tmp_path))
    transport._menu_render_wait_s = 0.05
    events = await _collect_events(transport)
    await transport.start()
    await transport.handle_claude_hook(
        {"hook_event_name": "PermissionRequest", "tool_name": "Bash", "tool_input": {}}
    )
    [card] = _ask_user_questions(events)
    assert _labels(card) == ["Allow", "Allow & don't ask again", "Deny"]

    transport.capture_stdout = _menu("bypass_dangerous_rm")
    with pytest.raises(ValueError, match='It offers "Yes", "No"'):
        await _answer(transport, card, "Allow & don't ask again")
    resent = _ask_user_questions(events)
    assert len(resent) == 2
    assert resent[1]["request_id"] == card["request_id"]
    assert _labels(resent[1]) == ["Yes", "No"]

    await _answer(transport, card, "Yes")
    assert _send_keys(transport) == ["1"]
    await transport.stop()


async def test_a_card_closed_before_its_menu_rendered_is_never_sent(tmp_path):
    transport = FakeTmuxInteractiveTransport(str(tmp_path))
    events = await _collect_events(transport)
    await transport.start()
    transport.capture_stdout = _menu("bash_plain")
    # The base hook returns before the card is read, as Claude's HTTP hook does.
    await TmuxInteractiveTransport.handle_claude_hook(
        transport, {"hook_event_name": "PermissionRequest", "tool_name": "Bash", "tool_input": {}}
    )
    transport._pending_tty_prompts.clear()
    await transport.permission_cards_sent()
    assert _ask_user_questions(events) == []
    await transport.stop()
