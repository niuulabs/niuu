"""Self-tests for the dependency-free CORE of the forge tmux harness.

These prove the harness works WITHOUT the broker:
  * the fakeagent CLI behaves (say / work / crash), driven as a subprocess;
  * the harness drives a REAL tmux session via the real transport, and a
    fakeagent Stop hook reaches a HookServer and produces a transport `result`.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

from tests.support.forge import HookServer, TmuxPage, install_fake_claude

_FAKEAGENT = Path(__file__).resolve().parents[1] / "support" / "forge" / "fakeagent.py"


def _run_fakeagent(stdin: str, *, timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_FAKEAGENT)],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_fakeagent_say_and_work_stream_to_stdout() -> None:
    result = _run_fakeagent("say:hello world\nwork:1\n")
    assert result.returncode == 0
    lines = result.stdout.splitlines()
    assert lines[0] == "fakeagent ready"
    assert "hello world" in lines
    assert any(line.startswith("...working") for line in lines)
    assert "done" in lines


def test_fakeagent_crash_exits_nonzero() -> None:
    result = _run_fakeagent("crash\n")
    assert result.returncode != 0
    assert result.returncode == 137


def test_fakeagent_unknown_line_echoes_as_say() -> None:
    result = _run_fakeagent("just some text\n")
    assert result.returncode == 0
    assert "just some text" in result.stdout.splitlines()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_tmux_say_reaches_pane_and_stop_hook(tmp_path: Path) -> None:
    if shutil.which("tmux") is None:
        pytest.skip("tmux is not installed")

    from skuld.transports.tmux_interactive import TmuxInteractiveTransport

    env = install_fake_claude(tmp_path / "bin")
    monkey_path = env["PATH"]

    hook_payloads: list[dict[str, Any]] = []

    async def handler(payload: dict[str, Any]) -> Any:
        hook_payloads.append(payload)
        # Route into the transport so it produces semantic events.
        await transport.handle_claude_hook(payload)

    events: list[dict[str, Any]] = []

    async def on_event(event: dict[str, Any]) -> None:
        events.append(event)

    hook_server = await HookServer(handler).start()
    try:
        transport = TmuxInteractiveTransport(
            workspace_dir=str(tmp_path),
            session_id=f"core-{uuid.uuid4().hex[:8]}",
            sdk_port=hook_server.port,
            turn_idle_timeout_s=0.2,
            turn_no_output_timeout_s=2.0,
            pane_poll_interval_s=0.2,
        )
        transport.on_event(on_event)

        import os

        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = monkey_path
        # Remote Control must not try to reach claude.ai in tests.
        os.environ["SKULD__TMUX_REMOTE_CONTROL"] = "0"
        try:
            await transport.start()
            page = TmuxPage(
                str(transport._socket_path),  # noqa: SLF001 - test seam
                transport.session_id or "",
            )
            await page.wait_for_text("fakeagent ready", timeout=5.0)

            await transport.send_message("say:hello")
            live_pane = await page.wait_for_text("hello", timeout=5.0)

            # Wait for the Stop hook to land and the transport to finish the turn.
            await _wait_until(
                lambda: any(p.get("hook_event_name") == "Stop" for p in hook_payloads),
                timeout=5.0,
            )
            await _wait_until(lambda: any(e["type"] == "result" for e in events), timeout=5.0)
        finally:
            await transport.stop()
            os.environ["PATH"] = old_path
            os.environ.pop("SKULD__TMUX_REMOTE_CONTROL", None)
    finally:
        await hook_server.stop()

    assert "hello" in live_pane
    assert any(p.get("hook_event_name") == "Stop" for p in hook_payloads)
    results = [e for e in events if e["type"] == "result"]
    assert results
    assert results[-1]["result"] == "hello"


async def _wait_until(predicate, timeout: float = 2.0) -> None:
    import asyncio

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not met before timeout")
