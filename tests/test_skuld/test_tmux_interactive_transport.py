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
async def test_send_message_pastes_text_and_streams_turn(tmp_path: Path) -> None:
    transport = FakeTmuxInteractiveTransport(str(tmp_path))
    events = await _collect_events(transport)
    await transport.start()

    # Delivery is non-blocking: send_message returns once the text is typed into
    # the live CLI; the response streams + completes asynchronously.
    await transport.send_message("hello Claude")
    assert transport.is_turn_active

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
    # The watchdog detects terminal idle and finishes the turn on its own.
    await _wait_until(lambda: any(event["type"] == "result" for event in events))
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
    # send_message is non-blocking: wait for the fake CLI to echo and the turn to
    # close (scraping the pane) before stop() tears the tmux session down — a bare
    # send→stop races the paste→echo→frame→result pipeline and is flaky per host.
    try:
        await _wait_until(
            lambda: any(
                event["type"] == "result" and "assistant: ping" in event.get("result", "")
                for event in events
            ),
            timeout=10.0,
        )
    finally:
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
    # BUG-2: a completed hook turn carries a best-effort usage estimate so message_count
    # advances (the broker's /usage path early-returns on an empty modelUsage).
    usage = events[2]["modelUsage"]
    assert usage, "completed turn must report non-empty modelUsage"
    out_tokens = next(iter(usage.values()))["outputTokens"]
    assert out_tokens >= 1


def test_remote_control_on_by_default_adds_flag(tmp_path: Path, monkeypatch) -> None:
    # Hybrid sessions: Remote Control is ON by default so a Forge tmux session is also
    # drivable from claude.ai/code + phone (label = the friendly Forge session name).
    monkeypatch.delenv("SKULD__TMUX_REMOTE_CONTROL", raising=False)
    monkeypatch.delenv("SKULD__CLAUDE_AUTH", raising=False)
    monkeypatch.setenv("SKULD__SESSION__NAME", "lexi-presentation")
    transport = FakeTmuxInteractiveTransport(str(tmp_path))
    argv = transport._interactive_argv()
    assert "--remote-control" in argv
    assert argv[argv.index("--remote-control") + 1] == "lexi-presentation"


def test_remote_control_disabled_by_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SKULD__TMUX_REMOTE_CONTROL", "0")
    transport = FakeTmuxInteractiveTransport(str(tmp_path))
    assert "--remote-control" not in transport._interactive_argv()


def test_remote_control_force_off_under_api_key_auth(tmp_path: Path, monkeypatch) -> None:
    # API-key auth can't register a session to the claude.ai account, and would block on
    # the interactive "use this API key?" chooser — so RC must auto-disable there even if
    # explicitly requested, to never wedge session startup.
    monkeypatch.setenv("SKULD__TMUX_REMOTE_CONTROL", "1")
    monkeypatch.setenv("SKULD__CLAUDE_AUTH", "api_key")
    transport = FakeTmuxInteractiveTransport(str(tmp_path))
    assert "--remote-control" not in transport._interactive_argv()


@pytest.mark.asyncio
async def test_deliver_user_text_raises_when_send_lock_is_wedged(tmp_path: Path) -> None:
    # BUG-3: after a WS crash/reconnect a wedged prior delivery used to hold the send
    # lock forever, so every later steering message blocked SILENTLY ("I type and nothing
    # happens"). The bounded acquire must now raise a clear error the broker turns into a
    # user_delivery_failed instead of hanging.
    transport = FakeTmuxInteractiveTransport(str(tmp_path))
    transport._alive = True
    transport._deliver_timeout_s = 0.05
    await transport._send_lock.acquire()  # simulate a stuck prior delivery
    try:
        with pytest.raises(RuntimeError, match="busy"):
            await transport._deliver_user_text("please steer the session")
    finally:
        transport._send_lock.release()
    # the lock is reusable once the wedged holder releases — no permanent deadlock
    assert not transport._send_lock.locked()


@pytest.mark.asyncio
async def test_estimate_model_usage_is_nonzero_and_keyed_by_model(tmp_path: Path) -> None:
    transport = FakeTmuxInteractiveTransport(str(tmp_path))
    usage = transport._estimate_model_usage(in_chars=40, out_chars=80)
    bucket = usage["claude-sonnet-4-6"]
    assert bucket["inputTokens"] == 10
    assert bucket["outputTokens"] == 20
    # never zero, even for a one-character turn -> guarantees the +1 advance
    tiny = transport._estimate_model_usage(in_chars=0, out_chars=1)
    assert next(iter(tiny.values()))["outputTokens"] >= 1


@pytest.mark.asyncio
async def test_claude_stop_hook_empty_message_keeps_empty_usage(tmp_path: Path) -> None:
    transport = FakeTmuxInteractiveTransport(str(tmp_path), sdk_port=8081)
    events = await _collect_events(transport)

    await transport.handle_claude_hook(
        {
            "hook_event_name": "Stop",
            "session_id": "claude-session",
            "transcript_path": "/tmp/transcript.jsonl",
            "last_assistant_message": "   ",
        }
    )
    results = [e for e in events if e["type"] == "result"]
    assert results, "Stop hook should still emit a result frame"
    # empty content -> {} so the broker does NOT count a phantom turn
    assert results[-1]["modelUsage"] == {}


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
async def test_subagent_tool_hooks_carry_parent_attribution(tmp_path: Path) -> None:
    """Subagent inner tool hooks carry parent_tool_use_id/agent_id; main-agent tools do not.

    Whole-truth unification: this is what lets iOS nest a subagent's work under the agent. A
    Task BLOCKS its parent, so every NON-Task hook between the Task PreToolUse and its
    PostToolUse belongs to that subagent (the active-subagent stack top). Main-agent frames
    stay byte-identical (no keys).
    """
    transport = FakeTmuxInteractiveTransport(str(tmp_path), sdk_port=8081)
    events = await _collect_events(transport)

    # 1) Task tool spawns a subagent (registers it + pushes the active-subagent stack).
    await transport.handle_claude_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Task",
            "tool_use_id": "task-1",
            "tool_input": {"description": "Review the diff", "subagent_type": "code-reviewer"},
        }
    )
    # 2) The subagent runs its own tool — must nest under task-1.
    await transport.handle_claude_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Grep",
            "tool_use_id": "child-1",
            "tool_input": {"pattern": "foo"},
        }
    )
    await transport.handle_claude_hook(
        {
            "hook_event_name": "PostToolUse",
            "tool_use_id": "child-1",
            "tool_response": {"stdout": "match", "stderr": "", "interrupted": False},
        }
    )
    # 3) The Task finishes (pops the stack).
    await transport.handle_claude_hook(
        {
            "hook_event_name": "PostToolUse",
            "tool_use_id": "task-1",
            "tool_response": {"stdout": "done", "stderr": "", "interrupted": False},
        }
    )
    # 4) A main-agent tool AFTER the subagent finished — no attribution.
    await transport.handle_claude_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_use_id": "main-2",
            "tool_input": {"command": "ls"},
        }
    )

    by_id = {
        e["message"]["content"][0]["id"]: e["message"]["content"][0]
        for e in events
        if e["type"] == "assistant" and e["message"]["content"][0].get("type") == "tool_use"
    }
    # The Task's OWN tool_use is a main-agent action -> no parent.
    assert "parent_tool_use_id" not in by_id["task-1"]
    # The subagent's inner tool nests under the Task id.
    assert by_id["child-1"]["parent_tool_use_id"] == "task-1"
    assert by_id["child-1"]["agent_id"] == "task-1"
    # A main-agent tool after the subagent finished carries NO attribution (byte-identical frame).
    assert "parent_tool_use_id" not in by_id["main-2"]
    assert "agent_id" not in by_id["main-2"]
    # The child's tool_result also nests under the Task id.
    child_result = next(
        e["message"]["content"][0]
        for e in events
        if e["type"] == "user" and e["message"]["content"][0].get("tool_use_id") == "child-1"
    )
    assert child_result["parent_tool_use_id"] == "task-1"


@pytest.mark.asyncio
async def test_hook_enabled_turn_completes_on_stop_not_terminal_idle(tmp_path: Path) -> None:
    transport = FakeTmuxInteractiveTransport(str(tmp_path), sdk_port=8081)
    events = await _collect_events(transport)
    await transport.start()

    # Non-blocking delivery — the turn is tracked but send_message returns.
    await transport.send_message("hello")
    assert transport.is_turn_active
    await transport._handle_pane_output(  # noqa: SLF001
        transport._panes["%1"],  # noqa: SLF001
        b"terminal redraw\n",
    )
    # In hook mode the terminal-idle watchdog must NOT end the turn; only the
    # Stop hook does. Wait past the idle timeout and confirm the turn is alive.
    await asyncio.sleep(0.08)
    assert transport.is_turn_active
    assert not any(event["type"] == "result" for event in events)

    await transport.handle_claude_hook(
        {
            "hook_event_name": "Stop",
            "last_assistant_message": "done from hook",
        }
    )
    await _wait_until(lambda: not transport.is_turn_active)
    await transport.stop()

    assert any(
        event["type"] == "result" and event["result"] == "done from hook" for event in events
    )


@pytest.mark.asyncio
async def test_capabilities_advertise_native_steering(tmp_path: Path) -> None:
    transport = FakeTmuxInteractiveTransport(str(tmp_path))
    caps = transport.capabilities
    assert caps.steer is True
    assert caps.steering_mode == "native"


@pytest.mark.asyncio
async def test_send_message_is_non_blocking(tmp_path: Path) -> None:
    transport = FakeTmuxInteractiveTransport(str(tmp_path), sdk_port=8081)
    await _collect_events(transport)
    await transport.start()

    # Must return promptly even though the turn is still running (hook mode:
    # only the Stop hook ends it). Old behavior blocked here for the whole turn.
    await asyncio.wait_for(transport.send_message("hello"), timeout=0.5)
    assert transport.is_turn_active
    await transport.stop()


@pytest.mark.asyncio
async def test_mid_turn_message_steers_without_stopping_turn(tmp_path: Path) -> None:
    transport = FakeTmuxInteractiveTransport(str(tmp_path), sdk_port=8081)
    events = await _collect_events(transport)
    await transport.start()

    await transport.send_message("first")
    assert transport.is_turn_active
    pastes_before = sum(1 for args, _ in transport.commands if args[0] == "paste-buffer")

    # A second message mid-turn is real steering: it types into the live CLI and
    # must NOT emit a result / finish the running turn (no disruptive restart).
    await transport.send_control("steer", content="actually do X instead")

    assert transport.is_turn_active
    assert not any(event["type"] == "result" for event in events)
    pastes_after = sum(1 for args, _ in transport.commands if args[0] == "paste-buffer")
    assert pastes_after == pastes_before + 1
    await transport.stop()


# ──────────────────── CLI-mode questions bridge (2026-06-21) ────────────────────
#
# The tmux transport surfaces TTY permission gates + the AskUserQuestion tool as a structured
# `ask_user_question` (so a remote client reuses its existing answer card), and translates the
# structured `ask_user_answer` back into the pane keystroke that drives the live menu.


def _ask_user_questions(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e.get("type") == "ask_user_question"]


def _send_keys(transport: FakeTmuxInteractiveTransport) -> list[str]:
    """The key arguments of every `tmux send-keys` issued (in order)."""
    return [args[-1] for args, _ in transport.commands if args and args[0] == "send-keys"]


_PERMISSION_MENU = "\n".join(
    [
        "Do you want to proceed?",
        "❯ 1. Yes",
        "  2. Yes, and don't ask again for Bash commands",
        "  3. No, and tell Claude what to do differently (esc)",
        "",
    ]
)


@pytest.mark.asyncio
async def test_permission_hook_surfaces_structured_ask_user_question(tmp_path: Path) -> None:
    transport = FakeTmuxInteractiveTransport(str(tmp_path))
    events = await _collect_events(transport)
    await transport.start()

    await transport.handle_claude_hook(
        {
            "hook_event_name": "PermissionRequest",
            "tool_name": "Bash",
            "tool_input": {"command": "npm test"},
            "permission_suggestions": [],
        }
    )

    # The advisory frame still fires AND a structured ask_user_question is surfaced.
    assert any(e.get("type") == "claude_permission_request" for e in events)
    questions = _ask_user_questions(events)
    assert len(questions) == 1
    q = questions[0]
    assert q["event_type"] == "ask_user_question"  # flips the broker to awaiting_input
    assert q["request_id"]
    opts = [o["label"] for o in q["questions"][0]["options"]]
    assert opts == ["Allow", "Allow & don't ask again", "Deny"]
    assert "npm test" in q["questions"][0]["question"]
    await transport.stop()


@pytest.mark.asyncio
async def test_answer_allow_presses_first_menu_digit(tmp_path: Path) -> None:
    transport = FakeTmuxInteractiveTransport(str(tmp_path))
    events = await _collect_events(transport)
    await transport.start()
    transport.capture_stdout = _PERMISSION_MENU

    await transport.handle_claude_hook(
        {
            "hook_event_name": "PermissionRequest",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
        }
    )
    rid = _ask_user_questions(events)[0]["request_id"]

    await transport.send_control("ask_user_answer", request_id=rid, answers=[{"answer": "Allow"}])

    assert _send_keys(transport)[-1] == "1"  # affirmative row
    assert any(e.get("type") == "ask_user_resolved" and e["request_id"] == rid for e in events)
    await transport.stop()


@pytest.mark.asyncio
async def test_answer_allow_always_matches_dont_ask_row(tmp_path: Path) -> None:
    transport = FakeTmuxInteractiveTransport(str(tmp_path))
    events = await _collect_events(transport)
    await transport.start()
    transport.capture_stdout = _PERMISSION_MENU

    await transport.handle_claude_hook(
        {
            "hook_event_name": "PermissionRequest",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
        }
    )
    rid = _ask_user_questions(events)[0]["request_id"]

    await transport.send_control(
        "ask_user_answer", request_id=rid, answers=[{"answer": "Allow & don't ask again"}]
    )
    assert _send_keys(transport)[-1] == "2"  # the "…don't ask again" row
    await transport.stop()


@pytest.mark.asyncio
async def test_answer_deny_presses_escape(tmp_path: Path) -> None:
    transport = FakeTmuxInteractiveTransport(str(tmp_path))
    events = await _collect_events(transport)
    await transport.start()
    transport.capture_stdout = _PERMISSION_MENU

    await transport.handle_claude_hook(
        {
            "hook_event_name": "PermissionRequest",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
        }
    )
    rid = _ask_user_questions(events)[0]["request_id"]

    await transport.send_control("ask_user_answer", request_id=rid, answers=[{"answer": "Deny"}])
    assert _send_keys(transport)[-1] == "Escape"  # universal cancel
    await transport.stop()


@pytest.mark.asyncio
async def test_ask_user_question_tool_surfaces_and_answers(tmp_path: Path) -> None:
    transport = FakeTmuxInteractiveTransport(str(tmp_path))
    events = await _collect_events(transport)
    await transport.start()
    transport.capture_stdout = "\n".join(["❯ 1. Postgres", "  2. SQLite", ""])

    questions = [
        {
            "header": "Database",
            "question": "Which DB?",
            "options": [{"label": "Postgres"}, {"label": "SQLite"}],
            "multiSelect": False,
        }
    ]
    await transport.handle_claude_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "AskUserQuestion",
            "tool_input": {"questions": questions},
        }
    )

    surfaced = _ask_user_questions(events)
    assert len(surfaced) == 1
    assert surfaced[0]["questions"] == questions  # pass-through of the agent's options
    rid = surfaced[0]["request_id"]
    # The tool_use is still emitted for the transcript.
    assert any(
        e.get("type") == "assistant"
        and any(b.get("name") == "AskUserQuestion" for b in e["message"]["content"])
        for e in events
    )

    await transport.send_control("ask_user_answer", request_id=rid, answers=[{"answer": "SQLite"}])
    keys = _send_keys(transport)
    assert keys[-2:] == ["2", "Enter"]  # select row 2 + confirm
    await transport.stop()


@pytest.mark.asyncio
async def test_turn_end_resolves_stale_prompt(tmp_path: Path) -> None:
    transport = FakeTmuxInteractiveTransport(str(tmp_path))
    events = await _collect_events(transport)
    await transport.start()

    await transport.handle_claude_hook(
        {
            "hook_event_name": "PermissionRequest",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
        }
    )
    rid = _ask_user_questions(events)[0]["request_id"]

    # The turn finishes (e.g. answered in-terminal) → the pending prompt is resolved so a remote
    # client dismisses its card instead of stranding it.
    await transport._finish_hook_turn(content="done", reason="stop")  # noqa: SLF001
    resolved = [e for e in events if e.get("type") == "ask_user_resolved"]
    assert any(e["request_id"] == rid and e["decision"] == "turn_ended" for e in resolved)
    await transport.stop()


@pytest.mark.asyncio
async def test_initial_prompt_waits_for_repl_ready_then_delivers(tmp_path: Path) -> None:
    # The seed prompt must land only once the REPL prompt has rendered — pasting it
    # into a still-booting Claude made it parse as a slash command (/You...).
    transport = FakeTmuxInteractiveTransport(str(tmp_path), initial_prompt="seed prompt here")
    transport.capture_stdout = "Claude Code v2\n❯ "  # readiness marker present
    await transport.start()
    await transport.stop()
    assert any("seed prompt here" in buf for buf in transport.loaded_buffers), (
        "the seed prompt must be delivered after the REPL prompt rendered"
    )


@pytest.mark.asyncio
async def test_initial_prompt_falls_through_if_repl_never_signals(
    tmp_path: Path, monkeypatch
) -> None:
    # Best-effort: a missing readiness marker must never wedge startup — after the
    # bounded timeout the seed prompt is delivered anyway.
    monkeypatch.setenv("SKULD__TMUX_REPL_READY_TIMEOUT_SECONDS", "0.2")
    transport = FakeTmuxInteractiveTransport(str(tmp_path), initial_prompt="seed anyway")
    transport.capture_stdout = "still booting, no prompt yet"  # no readiness marker
    await transport.start()
    await transport.stop()
    assert any("seed anyway" in buf for buf in transport.loaded_buffers)
