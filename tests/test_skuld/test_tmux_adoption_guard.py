"""LEX-1670 guard: a broker never adopts a tmux session whose Claude reports elsewhere.

Claude reads its hook URL and secret once, at launch, and the secret is new for every
broker start. A tmux session left behind by an earlier broker (a stop that did not
finish, a broker restarted after a Forge switch) keeps posting hooks to that broker: on
2026-10-30 ios-fixes' Stop got HTTP 401 and its replies were lost. The transport stamps
each session it creates with its hook binding and replaces any session without its stamp.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from skuld.transports.tmux_interactive import (
    HOOK_BINDING_OPTION,
    STALE_SESSION_REPLACED_SUBTYPE,
    _TmuxResult,
)
from tests.test_skuld.test_tmux_interactive_transport import (
    FakeTmuxInteractiveTransport,
    _collect_events,
)


class StampingTmux(FakeTmuxInteractiveTransport):
    """The fake tmux, plus session options and kill-session."""

    def __init__(self, workspace_dir: str, **kwargs: Any) -> None:
        super().__init__(workspace_dir, **kwargs)
        self.options: dict[str, str] = {}
        self.primary_dead = False

    async def _run_tmux(self, *args: str, check: bool = True, env=None) -> _TmuxResult:
        if args[:1] == ("kill-session",):
            self.commands.append((args, env))
            self.session_exists = False
            self.options.clear()
            return _TmuxResult(0)
        if args[:1] == ("set-option",) and args[-2] == HOOK_BINDING_OPTION:
            self.commands.append((args, env))
            self.options[HOOK_BINDING_OPTION] = args[-1]
            return _TmuxResult(0)
        if args[:1] == ("list-panes",) and args[-1] == "#{pane_dead}":
            self.commands.append((args, env))
            return _TmuxResult(0, "1\n" if self.primary_dead else "0\n")
        if args[:1] == ("show-options",) and args[-1] == HOOK_BINDING_OPTION:
            self.commands.append((args, env))
            if HOOK_BINDING_OPTION not in self.options:
                return _TmuxResult(1, "", "invalid option")
            return _TmuxResult(0, self.options[HOOK_BINDING_OPTION] + "\n")
        return await super()._run_tmux(*args, check=check, env=env)


def _new_sessions(transport: StampingTmux) -> int:
    return sum(1 for args, _ in transport.commands if args[:1] == ("new-session",))


def _replaced(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e.get("subtype") == STALE_SESSION_REPLACED_SUBTYPE]


@pytest.mark.asyncio
async def test_a_created_session_is_stamped_with_this_brokers_binding(tmp_path: Path) -> None:
    transport = StampingTmux(str(tmp_path), sdk_port=8081)
    try:
        await transport._ensure_started()
        assert _new_sessions(transport) == 1
        assert transport.options[HOOK_BINDING_OPTION] == transport._hook_binding()
    finally:
        await transport.stop()


@pytest.mark.asyncio
async def test_a_session_with_our_stamp_is_adopted(tmp_path: Path) -> None:
    transport = StampingTmux(str(tmp_path), sdk_port=8081)
    events = await _collect_events(transport)
    transport.session_exists = True
    transport.options[HOOK_BINDING_OPTION] = transport._hook_binding()
    try:
        await transport._ensure_started()
        assert _new_sessions(transport) == 0
        assert not _replaced(events)
    finally:
        await transport.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("stamp", [None, "another-broker"])
async def test_a_session_from_another_broker_is_replaced(tmp_path: Path, stamp) -> None:
    transport = StampingTmux(str(tmp_path), sdk_port=8081)
    events = await _collect_events(transport)
    transport.session_exists = True
    if stamp is not None:
        transport.options[HOOK_BINDING_OPTION] = stamp
    try:
        await transport._ensure_started()
        kills = [args for args, _ in transport.commands if args[:1] == ("kill-session",)]
        assert len(kills) == 1
        assert _new_sessions(transport) == 1
        assert transport.options[HOOK_BINDING_OPTION] == transport._hook_binding()
        (event,) = _replaced(events)
        assert event["type"] == "system"
    finally:
        await transport.stop()


def test_the_binding_changes_with_the_port_and_the_secret(tmp_path: Path) -> None:
    one = StampingTmux(str(tmp_path), sdk_port=8081)
    other_port = StampingTmux(str(tmp_path), sdk_port=8082)
    assert one._hook_binding() != other_port._hook_binding()


@pytest.mark.asyncio
async def test_our_session_with_a_dead_claude_is_replaced(tmp_path: Path) -> None:
    """LEX-1699: a corpse kept by remain-on-exit is never adopted, even with our stamp."""
    transport = StampingTmux(str(tmp_path), sdk_port=8081)
    events = await _collect_events(transport)
    transport.session_exists = True
    transport.options[HOOK_BINDING_OPTION] = transport._hook_binding()
    transport.primary_dead = True
    try:
        await transport._ensure_started()
        assert _new_sessions(transport) == 1
        assert len(_replaced(events)) == 1
    finally:
        await transport.stop()
