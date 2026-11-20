"""LEX-2025: a side query's AskUserQuestion never becomes the session's question.

10 Nov 2026 04:47:17Z (ios-release e978690e, mini-m6): 4 s after a cron turn ended,
Claude Code's prompt-suggestion side query ran its own tools. Its PreToolUse hook
for AskUserQuestion carried ``agent_id`` with no ``agent_type`` and no SubagentStart,
and it wrote no transcript. The broker opened a card from it; the pane showed only the
idle composer with a dim suggestion, so the menu read "unknown" and nothing closed the
card. It refused the 2536 TRAIN GO for 2 h (409 native_control_pending).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from skuld.transports.tmux_interactive import DISMISS_NEVER_SHOWN
from tests.test_skuld.test_tmux_interactive_transport import (
    FakeTmuxInteractiveTransport,
    _ask_user_questions,
    _collect_events,
)

NATIVE = "3fe91051-36b5-4f96-9c77-9ba6572a514d"
SIDE = "a0a9ec517ee04652e"
PHANTOM = "toolu_014cABL9m8ca8tmcZtZ1bnSK"
QUESTIONS = {
    "questions": [
        {
            "header": "Next step",
            "question": "What next?",
            "multiSelect": False,
            "options": [
                {"label": "Keep waiting (Recommended)", "description": "Stay idle"},
                {"label": "Check the train", "description": "Look now"},
            ],
        }
    ]
}
IDLE_WITH_SUGGESTION = "\n".join(
    [
        "⏺ The check printed NONE, so mac-tests still hasn't run on mini-m4.",
        "",
        "────────────────────────────────────────",
        "❯ check the train status",  # the dim suggestion reads as typed text
        "────────────────────────────────────────",
        "  ⏵⏵ bypass permissions on (shift+tab to cycle)",
    ]
)


class Session:
    def __init__(self, tmp_path: Path) -> None:
        self.transport = FakeTmuxInteractiveTransport(
            str(tmp_path), sdk_port=8081, skip_permissions=True
        )
        self.transport._claude_native_session_id = NATIVE
        self.transcript = tmp_path / f"{NATIVE}.jsonl"
        self.transcript.write_text("")
        self.events: list[dict[str, Any]] = []

    async def __aenter__(self) -> Session:
        self.events = await _collect_events(self.transport)
        await self.transport.start()
        self.transport.capture_stdout = IDLE_WITH_SUGGESTION
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.transport.stop()

    async def hook(self, event: str, **payload: Any) -> None:
        await self.transport.handle_claude_hook(
            {
                "hook_event_name": event,
                "session_id": NATIVE,
                "transcript_path": str(self.transcript),
                **payload,
            }
        )

    def tool_uses(self) -> list[str]:
        return [
            block.get("id")
            for e in self.events
            if e.get("type") == "assistant"
            for block in (e.get("message") or {}).get("content") or []
            if block.get("type") == "tool_use"
        ]


async def test_10nov_a_side_query_question_opens_no_card_and_no_turn(tmp_path):
    async with Session(tmp_path) as s:
        await s.hook(
            "PreToolUse",
            agent_id=SIDE,
            tool_name="AskUserQuestion",
            tool_input=QUESTIONS,
            tool_use_id=PHANTOM,
        )
        await s.hook("PostToolBatch", agent_id=SIDE, tool_calls=[{"tool_name": "AskUserQuestion"}])
        await s.hook("SubagentStop", agent_id=SIDE, agent_type="")

        assert _ask_user_questions(s.events) == []
        assert PHANTOM not in s.tool_uses()
        assert s.transport._pending_tty_prompts == {}
        assert not s.transport._turn_active
        assert s.transport._native_control_counts["side_query_hooks_ignored"] == 3
        # Every hook is still logged for audit.
        assert sum(e.get("type") == "claude_hook" for e in s.events) == 3


async def test_a_registered_subagents_tools_are_still_its_own(tmp_path):
    """A Task or fork subagent names its type and starts first: nothing changes for it."""
    async with Session(tmp_path) as s:
        await s.hook("SubagentStart", agent_id="agent-1", agent_type="general-purpose")
        await s.hook(
            "PreToolUse",
            agent_id="agent-1",
            agent_type="general-purpose",
            tool_name="Bash",
            tool_input={"command": "ls"},
            tool_use_id="toolu_sub",
        )
        assert "toolu_sub" in s.tool_uses()
        assert s.transport._native_control_counts["side_query_hooks_ignored"] == 0


async def test_a_card_whose_call_never_reached_the_session_file_closes(tmp_path):
    """The backstop: menu "unknown" alone no longer keeps a never-shown card (LEX-2025)."""
    async with Session(tmp_path) as s:
        # A card from an older broker that opened before the side-query filter.
        s.transport._native_control_sweep_interval_s = 0.0
        await s.hook(
            "PreToolUse", tool_name="AskUserQuestion", tool_input=QUESTIONS, tool_use_id=PHANTOM
        )
        [card] = _ask_user_questions(s.events)
        assert await s.transport.sweep_native_controls() == []  # menu unknown: kept

        closed = await s.transport.dismiss_never_shown_question(PHANTOM, trigger="reconcile_sweep")

        assert closed == card["request_id"]
        [resolved] = [e for e in s.events if e.get("type") == "ask_user_resolved"]
        assert resolved["dismiss_reason"] == DISMISS_NEVER_SHOWN
        assert s.transport._pending_tty_prompts == {}


async def test_a_young_or_answering_card_is_not_closed_as_never_shown(tmp_path):
    async with Session(tmp_path) as s:
        s.transport._native_control_sweep_interval_s = 3600.0
        await s.hook(
            "PreToolUse", tool_name="AskUserQuestion", tool_input=QUESTIONS, tool_use_id=PHANTOM
        )
        assert await s.transport.dismiss_never_shown_question(PHANTOM, trigger="t") is None
        s.transport._native_control_sweep_interval_s = 0.0
        [pending] = s.transport._pending_tty_prompts.values()
        pending["answer_in_flight"] = True
        assert await s.transport.dismiss_never_shown_question(PHANTOM, trigger="t") is None
        assert len(s.transport._pending_tty_prompts) == 1


def test_fixture_is_the_incidents_shape():
    """The replayed hook is the one recorded at 04:47:17Z (agent_id, no agent_type)."""
    hook = {"agent_id": SIDE, "tool_name": "AskUserQuestion"}
    assert "agent_type" not in json.loads(json.dumps(hook))


async def test_a_subagents_question_is_never_closed_as_never_shown(tmp_path):
    """Review finding: after a broker restart its agent is unregistered, its call is in its
    own file (never the main one), and its menu may be live: the card must stay."""
    async with Session(tmp_path) as s:
        s.transport._native_control_sweep_interval_s = 0.0
        await s.hook(
            "PreToolUse",
            agent_id="agent-after-restart",
            agent_type="general-purpose",
            tool_name="AskUserQuestion",
            tool_input=QUESTIONS,
            tool_use_id="toolu_sub_q",
        )
        [card] = _ask_user_questions(s.events)
        assert await s.transport.dismiss_never_shown_question("toolu_sub_q", trigger="t") is None
        assert card["request_id"] in s.transport._pending_tty_prompts


def test_forge_launched_clis_make_no_prompt_suggestions_by_default(tmp_path):
    """LEX-2026: the env var for this CLI only; ~/.claude/settings.json is never touched."""
    from skuld.config import SkuldSettings
    from skuld.transports.tmux_interactive import TmuxInteractiveTransport

    assert SkuldSettings().tmux_prompt_suggestions is False
    off = TmuxInteractiveTransport(str(tmp_path))._spawn_env()
    assert off["CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION"] == "0"
    on = TmuxInteractiveTransport(str(tmp_path), prompt_suggestions=True)._spawn_env()
    assert "CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION" not in on or on[
        "CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION"
    ] == __import__("os").environ.get("CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION")
