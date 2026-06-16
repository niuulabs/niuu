"""Tests for the tmux-backed interactive Claude transport."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from skuld.transports.tmux_interactive import (
    TmuxInteractiveTransport,
    _TmuxResult,
)


class FakeTmuxInteractiveTransport(TmuxInteractiveTransport):
    """Tmux transport with an in-memory command runner for deterministic tests."""

    def __init__(self, workspace_dir: str, **kwargs: Any) -> None:
        super().__init__(
            workspace_dir=workspace_dir,
            session_id="test-session",
            model="claude-sonnet-4-6",
            turn_idle_timeout_s=0.05,
            turn_no_output_timeout_s=0.2,
            pane_poll_interval_s=999,
            frame_interval_s=0.01,
            **kwargs,
        )
        self.commands: list[tuple[tuple[str, ...], dict[str, str] | None]] = []
        self.loaded_buffers: list[str] = []
        self.session_exists = False
        self.capture_stdout = ""
        self.pane_lines = ["%1\t0\tmain\t1\tclaude\t200\t50\t2\t47"]

    def _tmux_binary_exists(self) -> bool:
        return True

    async def _run_tmux(
        self,
        *args: str,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> _TmuxResult:
        self.commands.append((args, env))
        command = args[0] if args else ""
        if command == "has-session":
            return _TmuxResult(0 if self.session_exists else 1)
        if command == "new-session":
            self.session_exists = True
            return _TmuxResult(0)
        if command == "list-panes":
            return _TmuxResult(0, "\n".join(self.pane_lines) + "\n")
        if command == "load-buffer":
            self.loaded_buffers.append(Path(args[-1]).read_text(encoding="utf-8"))
            return _TmuxResult(0)
        if command == "capture-pane":
            return _TmuxResult(0, self.capture_stdout)
        return _TmuxResult(0)


async def _collect_events(
    transport: TmuxInteractiveTransport,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    async def on_event(event: dict[str, Any]) -> None:
        events.append(event)

    transport.on_event(on_event)
    return events


async def _wait_until(predicate: Callable[[], bool], timeout: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not met before timeout")


def _command_names(transport: FakeTmuxInteractiveTransport) -> list[str]:
    return [command[0][0] for command in transport.commands if command[0]]


@pytest.mark.asyncio
async def test_start_creates_session_emits_init_and_pane(tmp_path: Path) -> None:
    transport = FakeTmuxInteractiveTransport(str(tmp_path), sdk_port=8081)
    events = await _collect_events(transport)

    await transport.start()
    await transport.stop()

    names = _command_names(transport)
    assert "new-session" in names
    new_session = next(args for args, _ in transport.commands if args[0] == "new-session")
    assert "-c" in new_session
    assert str(tmp_path) in new_session
    assert "--" in new_session
    argv = new_session[new_session.index("--") + 1 :]
    assert argv[:2] == ("claude", "--model")
    assert argv[2] == "claude-sonnet-4-6"
    assert "--settings" in argv
    settings_path = Path(argv[argv.index("--settings") + 1])
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert "Stop" in settings["hooks"]
    assert "PreToolUse" in settings["hooks"]
    assert "MessageDisplay" not in settings["hooks"]

    event_types = [event["type"] for event in events]
    assert "terminal_pane_opened" in event_types
    init = next(event for event in events if event["type"] == "system")
    assert init["subtype"] == "init"
    assert init["terminal"]["transport"] == "tmux_interactive"
    assert init["terminal"]["hook_endpoint"] == "http://127.0.0.1:8081/api/claude/hooks"
    assert any(command["name"] == "/compact" for command in init["slash_commands"])


@pytest.mark.asyncio
async def test_send_message_pastes_text_and_synthesizes_chat_turn(tmp_path: Path) -> None:
    transport = FakeTmuxInteractiveTransport(str(tmp_path))
    events = await _collect_events(transport)
    await transport.start()

    send_task = asyncio.create_task(transport.send_message("hello Claude"))
    await _wait_until(lambda: any(args[0] == "paste-buffer" for args, _ in transport.commands))
    transport.capture_stdout = "\n".join(
        [
            "❯ hello Claude",
            "",
            "✶ Gesticulating...",
            "● Claude says hi",
            "  with clean spacing",
            "",
            "❯ ",
        ]
    )
    await transport._handle_pane_output(  # noqa: SLF001 - direct event simulation
        transport._panes["%1"],  # noqa: SLF001
        b"\x1b[32mClaude says hi\x1b[0m\r\n",
    )
    _ = await send_task
    await transport.stop()

    command_names = _command_names(transport)
    assert "load-buffer" in command_names
    assert "paste-buffer" in command_names
    assert ("send-keys", "-t", "%1", "Enter") in [args for args, _ in transport.commands]

    event_types = [event["type"] for event in events]
    assert "terminal_frame" in event_types
    assert "terminal_output" not in event_types
    assert "assistant" in event_types
    assert "content_block_delta" in event_types
    result = next(event for event in events if event["type"] == "result")
    assert result["stop_reason"] == "terminal_idle"
    assert result["result"] == "Claude says hi\nwith clean spacing"


@pytest.mark.asyncio
async def test_terminal_controls_send_keys_input_and_resize(tmp_path: Path) -> None:
    transport = FakeTmuxInteractiveTransport(str(tmp_path))
    events = await _collect_events(transport)
    await transport.start()

    await transport.send_control("terminal_key", key="Up")
    await transport.send_control("terminal_input", data="/help", enter=True)
    await transport.send_control("terminal_resize", cols=120, rows=40)
    await transport.stop()

    commands = [args for args, _ in transport.commands]
    assert ("send-keys", "-t", "%1", "Up") in commands
    assert ("send-keys", "-t", "%1", "Enter") in commands
    assert ("resize-pane", "-t", "%1", "-x", "120", "-y", "40") in commands

    event_types = [event["type"] for event in events]
    assert "terminal_key_sent" in event_types
    assert "terminal_input_sent" in event_types
    assert "terminal_resized" in event_types


@pytest.mark.asyncio
async def test_terminal_input_strips_carriage_returns(tmp_path: Path) -> None:
    transport = FakeTmuxInteractiveTransport(str(tmp_path))
    events = await _collect_events(transport)
    await transport.start()

    await transport.send_control("terminal_input", data="first\r\nsecond\r", enter=False)
    await transport.stop()

    load_buffer = next(args for args, _ in transport.commands if args[0] == "load-buffer")
    input_path = Path(load_buffer[-1])
    assert not input_path.exists()
    input_event = next(event for event in events if event["type"] == "terminal_input_sent")
    assert input_event["bytes"] == len(b"first\nsecond")


@pytest.mark.asyncio
async def test_slash_command_control_pastes_terminal_input_without_chat_turn(
    tmp_path: Path,
) -> None:
    transport = FakeTmuxInteractiveTransport(str(tmp_path))
    events = await _collect_events(transport)
    await transport.start()

    await transport.send_control(
        "slash_command",
        command="workflows",
        arguments="--all",
        pane_id="%1",
    )
    await transport.stop()

    assert "/workflows --all" in transport.loaded_buffers
    assert ("send-keys", "-t", "%1", "Enter") in [args for args, _ in transport.commands]
    assert any(event["type"] == "slash_command_sent" for event in events)
    assert not any(event["type"] == "assistant" for event in events)
    assert not any(event["type"] == "result" for event in events)


@pytest.mark.asyncio
async def test_discover_slash_commands_scrapes_terminal_menu(tmp_path: Path) -> None:
    transport = FakeTmuxInteractiveTransport(str(tmp_path))
    events = await _collect_events(transport)
    transport.capture_stdout = "\n".join(
        [
            "❯ /",
            "────────────────",
            "/deep-research                [dynamic workflow] Deep research harness",
            "                              with wrapped details",
            "/workflows                    Browse running and completed workflows",
            "/compact                      Free up context",
        ]
    )
    await transport.start()

    commands = await transport.discover_slash_commands(refresh=True)
    await transport.stop()

    assert {(command["name"], command["kind"], command["source"]) for command in commands} >= {
        ("/deep-research", "workflow", "tmux_autocomplete"),
        ("/workflows", "command", "tmux_autocomplete"),
        ("/compact", "command", "tmux_autocomplete"),
    }
    deep_research = next(command for command in commands if command["name"] == "/deep-research")
    assert "wrapped details" in deep_research["description"]
    sent_keys = [args for args, _ in transport.commands if args and args[0] == "send-keys"]
    assert ("send-keys", "-t", "%1", "/") in sent_keys
    assert ("send-keys", "-t", "%1", "Down") in sent_keys
    assert any(event["type"] == "slash_commands" for event in events)


@pytest.mark.asyncio
async def test_refresh_panes_emits_new_agent_team_pane(tmp_path: Path) -> None:
    transport = FakeTmuxInteractiveTransport(str(tmp_path))
    events = await _collect_events(transport)
    await transport.start()

    transport.pane_lines.append("%2\t1\tagent-1\t0\tclaude\t100\t40\t0\t0")
    await transport._refresh_panes(emit_events=True)  # noqa: SLF001 - direct pane simulation
    await transport.stop()

    opened = [event for event in events if event["type"] == "terminal_pane_opened"]
    assert {event["pane_id"] for event in opened} == {"%1", "%2"}
    assert any(event["window_name"] == "agent-1" for event in opened)
    pipe_targets = [
        args[2]
        for args, _ in transport.commands
        if len(args) >= 3 and args[0] == "pipe-pane" and args[1] == "-t"
    ]
    assert "%2" in pipe_targets


@pytest.mark.asyncio
async def test_interrupt_finishes_active_turn(tmp_path: Path) -> None:
    transport = FakeTmuxInteractiveTransport(str(tmp_path))
    events = await _collect_events(transport)
    await transport.start()

    send_task = asyncio.create_task(transport.send_message("long task"))
    await _wait_until(lambda: transport.is_turn_active)
    await transport.send_control("interrupt")
    _ = await send_task
    await transport.stop()

    assert ("send-keys", "-t", "%1", "C-c") in [args for args, _ in transport.commands]
    result = next(event for event in events if event["type"] == "result")
    assert result["is_error"] is True
    assert result["stop_reason"] == "interrupted"


def test_capabilities_advertise_interactive_terminal_controls(tmp_path: Path) -> None:
    transport = FakeTmuxInteractiveTransport(str(tmp_path))
    caps = transport.capabilities

    assert caps.interrupt is True
    assert caps.slash_commands is True
    assert caps.steer is True
    assert caps.terminal_output is True
    assert caps.terminal_input is True
    assert caps.terminal_keys is True
    assert caps.terminal_resize is True
    assert caps.terminal_panes is True


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_tmux_smoke_with_fake_claude(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise real tmux plumbing without requiring Claude credentials."""
    if shutil.which("tmux") is None:
        pytest.skip("tmux is not installed")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_claude = bin_dir / "claude"
    fake_claude.write_text(
        """#!/usr/bin/env bash
printf 'Fake Claude ready\\n'
while IFS= read -r line; do
  printf 'assistant: %s\\n' "$line"
done
""",
        encoding="utf-8",
    )
    fake_claude.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '')}")

    transport = TmuxInteractiveTransport(
        workspace_dir=str(tmp_path),
        session_id=f"fake-{uuid.uuid4().hex}",
        turn_idle_timeout_s=0.1,
        turn_no_output_timeout_s=1.0,
        pane_poll_interval_s=30.0,
    )
    events = await _collect_events(transport)

    await transport.start()
    await transport.send_message("ping")
    await transport.stop()

    terminal_frames = [
        "\n".join(event.get("rows", [])) for event in events if event["type"] == "terminal_frame"
    ]
    results = [event for event in events if event["type"] == "result"]
    assert any("assistant: ping" in frame for frame in terminal_frames)
    assert results
    assert "assistant: ping" in results[-1]["result"]


def test_extract_assistant_response_filters_claude_terminal_chrome() -> None:
    rows = [
        " ▐▛███▜▌   Claude Code v2.1.172",
        "❯ I want to learn about you, tell me 5 words about your dream",
        "✶ Gesticulating...",
        "⎿ Tip: Use /feedback to help us improve!",
        "● Five words about my dream:",
        "",
        "  Code, clarity, curiosity, craft, connection.",
        "* Lollygagging… (4s · ↓ 167 tokens)",
        "✻ Cogitated for 4s",
        "◉ xhigh · /effort",
        "❯ ",
        "? for shortcuts · ← for agents",
    ]

    assert (
        TmuxInteractiveTransport._extract_assistant_response(rows)  # noqa: SLF001
        == "Five words about my dream:\n\nCode, clarity, curiosity, craft, connection."
    )


@pytest.mark.asyncio
async def test_claude_stop_hook_emits_semantic_result(tmp_path: Path) -> None:
    transport = FakeTmuxInteractiveTransport(str(tmp_path), sdk_port=8081)
    events = await _collect_events(transport)

    handled = await transport.handle_claude_hook(
        {
            "hook_event_name": "Stop",
            "session_id": "claude-session",
            "transcript_path": "/tmp/transcript.jsonl",
            "last_assistant_message": "Structured final answer.",
        }
    )

    assert handled is True
    assert [event["type"] for event in events] == [
        "claude_hook",
        "assistant",
        "result",
    ]
    assert events[1]["message"]["content"] == [{"type": "text", "text": "Structured final answer."}]
    assert events[2]["result"] == "Structured final answer."
    assert events[2]["metadata"]["source"] == "claude_hook"


@pytest.mark.asyncio
async def test_claude_tool_hooks_emit_sdk_shaped_tool_events(tmp_path: Path) -> None:
    transport = FakeTmuxInteractiveTransport(str(tmp_path), sdk_port=8081)
    events = await _collect_events(transport)

    await transport.handle_claude_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_use_id": "tool-1",
            "tool_input": {"command": "npm test"},
        }
    )
    await transport.handle_claude_hook(
        {
            "hook_event_name": "PostToolUse",
            "tool_use_id": "tool-1",
            "tool_response": {"stdout": "ok", "stderr": "", "interrupted": False},
        }
    )

    assistant = next(event for event in events if event["type"] == "assistant")
    result = next(event for event in events if event["type"] == "user")
    assert assistant["message"]["content"][0] == {
        "type": "tool_use",
        "id": "tool-1",
        "name": "Bash",
        "input": {"command": "npm test"},
    }
    assert result["message"]["content"][0]["type"] == "tool_result"
    assert result["message"]["content"][0]["tool_use_id"] == "tool-1"


@pytest.mark.asyncio
async def test_hook_enabled_turn_waits_for_stop_not_terminal_idle(tmp_path: Path) -> None:
    transport = FakeTmuxInteractiveTransport(str(tmp_path), sdk_port=8081)
    events = await _collect_events(transport)
    await transport.start()

    send_task = asyncio.create_task(transport.send_message("hello"))
    await _wait_until(lambda: transport.is_turn_active)
    await transport._handle_pane_output(  # noqa: SLF001
        transport._panes["%1"],  # noqa: SLF001
        b"terminal redraw\n",
    )
    await asyncio.sleep(0.08)
    assert not send_task.done()

    await transport.handle_claude_hook(
        {
            "hook_event_name": "Stop",
            "last_assistant_message": "done from hook",
        }
    )
    _ = await send_task
    await transport.stop()

    assert any(
        event["type"] == "result" and event["result"] == "done from hook" for event in events
    )
