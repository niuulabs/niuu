"""LEX-1670: a tmux turn that reaches turn_max_seconds keeps the agent's reply.

On 2026-10-29/30 the Forge conversation recorded "The agent stopped with an error."
for healthy sessions while their terminals showed a complete reply: the hook-mode
watchdog ended any turn without a Stop hook after an hour with an EMPTY error result.
A Stop goes missing in two real ways: the main agent waits on background agents after
replying (lexi-macos), or the Stop is not attributed to the main pane (a broker from
before #1072 after /clear, niuu-consolidation). The cap now ends the turn with the text
it displayed (or the terminal shows), not as an error, and says so loudly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from niuu.domain.transcript_rebuild import rebuild_turns
from skuld.transports.tmux_interactive import TURN_CAP_SUBTYPE, TURN_MAX_SECONDS_ENV
from tests.test_skuld.test_tmux_interactive_transport import (
    FakeTmuxInteractiveTransport,
    _collect_events,
    _wait_until,
)
from volundr.domain.models import SessionLogEntry

REPLY = "Both packages landed; the report is in the ticket."


def _results(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e["type"] == "result"]


def _cap_frames(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e["type"] == "system" and e.get("subtype") == TURN_CAP_SUBTYPE]


async def _turn_waiting_on_a_background_agent(transport: FakeTmuxInteractiveTransport) -> None:
    """The lexi-macos shape: reply displayed, then background agents, and no Stop."""
    await transport.handle_claude_hook({"hook_event_name": "UserPromptSubmit", "prompt": "go"})
    await transport.handle_claude_hook(
        {"hook_event_name": "SubagentStart", "agent_id": "bg-1", "agent_type": "general-purpose"}
    )
    await transport.handle_claude_hook(
        {
            "hook_event_name": "MessageDisplay",
            "message_id": "m-1",
            "index": 0,
            "final": True,
            "delta": REPLY,
        }
    )
    await transport.handle_claude_hook(
        {
            "hook_event_name": "PreToolUse",
            "agent_id": "bg-1",
            "tool_name": "Bash",
            "tool_use_id": "bg-tool",
        }
    )


@pytest.mark.asyncio
async def test_capped_turn_keeps_the_displayed_reply_and_is_not_an_error(tmp_path: Path) -> None:
    transport = FakeTmuxInteractiveTransport(str(tmp_path), sdk_port=8081)
    events = await _collect_events(transport)
    try:
        transport._turn_max_seconds = 0.05
        await _turn_waiting_on_a_background_agent(transport)

        await _wait_until(lambda: bool(_results(events)))

        (result,) = _results(events)
        assert result["result"] == REPLY
        assert result["is_error"] is False
        assert result["stop_reason"] == "timeout"
        (cap,) = _cap_frames(events)
        assert cap["knob"] == TURN_MAX_SECONDS_ENV and cap["had_text"] is True
        assert not transport.is_turn_active
        # The reply was already streamed by MessageDisplay: no twin assistant frame.
        texts = [
            block.get("text")
            for e in events
            if e["type"] == "assistant"
            for block in e["message"]["content"]
            if isinstance(block, dict)
        ]
        assert texts.count(REPLY) == 1
    finally:
        await transport.stop()


@pytest.mark.asyncio
async def test_capped_turn_falls_back_to_the_terminal_text(tmp_path: Path, monkeypatch) -> None:
    transport = FakeTmuxInteractiveTransport(str(tmp_path), sdk_port=8081)
    events = await _collect_events(transport)

    async def screen() -> str:
        return "Reply visible only on the terminal."

    monkeypatch.setattr(transport, "_capture_turn_text", screen)
    try:
        transport._turn_max_seconds = 0.05
        await transport.handle_claude_hook({"hook_event_name": "UserPromptSubmit", "prompt": "go"})

        await _wait_until(lambda: bool(_results(events)))

        (result,) = _results(events)
        assert result["result"] == "Reply visible only on the terminal."
        assert result["is_error"] is False
    finally:
        await transport.stop()


@pytest.mark.asyncio
async def test_capped_turn_without_any_text_is_still_not_an_error(tmp_path: Path) -> None:
    transport = FakeTmuxInteractiveTransport(str(tmp_path), sdk_port=8081)
    events = await _collect_events(transport)
    try:
        transport._turn_max_seconds = 0.05
        await transport.handle_claude_hook({"hook_event_name": "UserPromptSubmit", "prompt": "go"})

        await _wait_until(lambda: bool(_results(events)))

        (result,) = _results(events)
        assert result["is_error"] is False and result["result"] == ""
        assert _cap_frames(events)[0]["had_text"] is False
    finally:
        await transport.stop()


@pytest.mark.asyncio
async def test_a_late_stop_after_the_cap(tmp_path: Path) -> None:
    transport = FakeTmuxInteractiveTransport(str(tmp_path), sdk_port=8081)
    events = await _collect_events(transport)
    try:
        transport._turn_max_seconds = 0.05
        await _turn_waiting_on_a_background_agent(transport)
        await _wait_until(lambda: bool(_results(events)))

        # The same reply again: already carried by the capped turn.
        await transport.handle_claude_hook(
            {"hook_event_name": "Stop", "last_assistant_message": REPLY}
        )
        assert len(_results(events)) == 1

        # The agent said more after the cap: that text is kept too.
        await transport.handle_claude_hook(
            {"hook_event_name": "Stop", "last_assistant_message": "Background agent done."}
        )
        assert [r["result"] for r in _results(events)] == [REPLY, "Background agent done."]
    finally:
        await transport.stop()


@pytest.mark.asyncio
async def test_a_real_stop_failure_is_still_an_error(tmp_path: Path) -> None:
    transport = FakeTmuxInteractiveTransport(str(tmp_path), sdk_port=8081)
    events = await _collect_events(transport)
    try:
        await transport.handle_claude_hook({"hook_event_name": "UserPromptSubmit", "prompt": "go"})
        await transport.handle_claude_hook(
            {"hook_event_name": "StopFailure", "error": "API Error: 529 overloaded"}
        )
        (result,) = _results(events)
        assert result["is_error"] is True
        assert result["result"] == "API Error: 529 overloaded"
    finally:
        await transport.stop()


def _log(session_id, events: list[dict[str, Any]]) -> list[SessionLogEntry]:
    """Durable rows as the broker writes them (one per transport frame)."""
    start = datetime(2026, 10, 29, 10, 25, tzinfo=UTC)
    user = {"type": "user", "message": {"content": "go"}}
    rows = [SessionLogEntry(session_id=session_id, seq=1, kind="user", payload=user, ts=start)]
    for n, event in enumerate(events, start=2):
        role = event["type"] if event["type"] in {"user", "assistant"} else None
        rows.append(
            SessionLogEntry(
                session_id=session_id,
                seq=n,
                kind=event["type"],
                payload=event,
                ts=start + timedelta(seconds=n),
                role=role,
            )
        )
    return rows


def _last_agent_turn(rows: list[SessionLogEntry]) -> dict[str, Any]:
    return [t for t in rebuild_turns(rows).turns if t["role"] == "assistant"][-1]


@pytest.mark.asyncio
async def test_replayed_background_agent_wait_shows_the_reply(tmp_path: Path) -> None:
    """Replay the captured shape through the durable-log rebuild Forge serves."""
    transport = FakeTmuxInteractiveTransport(str(tmp_path), sdk_port=8081)
    events = await _collect_events(transport)
    try:
        transport._turn_max_seconds = 0.05
        await _turn_waiting_on_a_background_agent(transport)
        await _wait_until(lambda: bool(_results(events)))
    finally:
        await transport.stop()
    session_id = uuid4()

    fixed = _last_agent_turn(_log(session_id, events))
    assert REPLY in fixed["content"]
    assert "stopped with an error" not in fixed["content"]
    assert not fixed.get("metadata", {}).get("is_error")

    # The frame the old watchdog wrote, for contrast: the reply is replaced by the error.
    old = [e for e in events if e["type"] != "result"] + [
        {"type": "result", "result": "", "is_error": True, "stop_reason": "timeout"}
    ]
    broken = _last_agent_turn(_log(session_id, old))
    assert broken.get("metadata", {}).get("is_error") is True
