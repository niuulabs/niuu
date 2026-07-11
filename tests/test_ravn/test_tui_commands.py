"""Unit tests for Ravn TUI command mode parser and dispatcher."""

from __future__ import annotations

import pytest

from ravn.tui.commands import (
    Command,
    CommandDispatcher,
    CommandParseError,
    complete_command,
    parse_command,
)

# ---------------------------------------------------------------------------
# parse_command
# ---------------------------------------------------------------------------


def test_parse_simple_command() -> None:
    cmd = parse_command("quit")
    assert cmd.name == "quit"
    assert cmd.args == []


def test_parse_command_with_args() -> None:
    cmd = parse_command("connect localhost:7477")
    assert cmd.name == "connect"
    assert cmd.args == ["localhost:7477"]


def test_parse_command_multiple_args() -> None:
    cmd = parse_command("view chat tanngrisnir")
    assert cmd.name == "view"
    assert cmd.args == ["chat", "tanngrisnir"]


def test_parse_command_quoted_args() -> None:
    cmd = parse_command('broadcast "hello world"')
    assert cmd.name == "broadcast"
    assert cmd.args == ["hello world"]


def test_parse_command_uppercase_normalised() -> None:
    cmd = parse_command("QUIT")
    assert cmd.name == "quit"


def test_parse_empty_command_raises() -> None:
    with pytest.raises(CommandParseError):
        parse_command("")


def test_parse_whitespace_only_raises() -> None:
    with pytest.raises(CommandParseError):
        parse_command("   ")


def test_parse_command_stores_raw() -> None:
    cmd = parse_command("view events")
    assert cmd.raw == "view events"


# ---------------------------------------------------------------------------
# complete_command
# ---------------------------------------------------------------------------


def test_complete_empty_returns_all_commands() -> None:
    completions = complete_command(":")
    assert len(completions) > 0
    assert ":quit" in completions or any("quit" in c for c in completions)


def test_complete_partial_command_name() -> None:
    completions = complete_command(":qu")
    assert any("quit" in c for c in completions)


def test_complete_view_returns_view_types() -> None:
    completions = complete_command(":view ")
    assert "flokk" in completions
    assert "chat" in completions
    assert "events" in completions


def test_complete_view_with_prefix() -> None:
    completions = complete_command(":view ch")
    assert "chat" in completions
    assert "checkpoints" in completions
    assert "flokk" not in completions


def test_complete_layout_returns_subcommands() -> None:
    completions = complete_command(":layout ")
    assert "save" in completions
    assert "load" in completions
    assert "list" in completions
    assert "delete" in completions


def test_removed_dead_commands_are_not_advertised() -> None:
    completions = complete_command(":")
    assert all(command not in completions for command in (":spawn", ":pipe", ":yank", ":filter"))


# ---------------------------------------------------------------------------
# CommandDispatcher
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatcher_registers_and_dispatches() -> None:
    dispatcher = CommandDispatcher()
    results = []

    def handler(arg: str) -> None:
        results.append(arg)

    dispatcher.register("test", handler)
    cmd = Command(name="test", args=["hello"], raw="test hello")
    await dispatcher.dispatch(cmd)
    assert results == ["hello"]


@pytest.mark.asyncio
async def test_dispatcher_async_handler() -> None:
    dispatcher = CommandDispatcher()
    results = []

    async def async_handler(arg: str) -> str:
        results.append(arg)
        return "ok"

    dispatcher.register("async_test", async_handler)
    cmd = Command(name="async_test", args=["world"], raw="async_test world")
    result = await dispatcher.dispatch(cmd)
    assert result == "ok"
    assert results == ["world"]


@pytest.mark.asyncio
async def test_dispatcher_unknown_command_raises() -> None:
    dispatcher = CommandDispatcher()
    cmd = Command(name="unknown", args=[], raw="unknown")
    with pytest.raises(CommandParseError, match="unknown command"):
        await dispatcher.dispatch(cmd)


@pytest.mark.asyncio
async def test_dispatcher_no_args_handler() -> None:
    dispatcher = CommandDispatcher()
    called = []

    def handler() -> None:
        called.append(True)

    dispatcher.register("noargs", handler)
    cmd = Command(name="noargs", args=[], raw="noargs")
    await dispatcher.dispatch(cmd)
    assert called == [True]
