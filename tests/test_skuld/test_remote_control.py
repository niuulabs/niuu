"""Tests for the Remote Control transports.

The transport launches ``claude remote-control``, scrapes the pairing URL from
the ANSI TUI, and emits a structured ``remote_control`` event.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
from unittest.mock import patch

import pytest

from skuld.transports.remote_control import (
    _ANSI_RE,
    _URL_RE,
    RemoteControlTransport,
)


async def _fast_timeout_wait_for(coro, timeout):
    """Stand-in for ``asyncio.wait_for`` that times out immediately.

    Mirrors the real timeout behaviour (cancel the inner coroutine, then
    raise) without the test actually sleeping for the production 5s timeout.
    """
    del timeout
    task = asyncio.ensure_future(coro)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    raise TimeoutError


class TestCommandConstruction:
    def test_name_carries_session_token(self):
        transport = RemoteControlTransport(
            "/tmp", session_id="abcdef12-3456-7890", session_name="my-session"
        )

        cmd = transport._build_command()

        assert cmd[:2] == ["claude", "remote-control"]
        assert "--name" in cmd
        name = cmd[cmd.index("--name") + 1]
        assert name == "my-session-abcdef12-345"
        assert "--spawn" in cmd
        assert "same-dir" in cmd

    def test_permission_mode_follows_skip_permissions(self):
        bypass = RemoteControlTransport("/tmp", skip_permissions=True)._build_command()
        default = RemoteControlTransport("/tmp", skip_permissions=False)._build_command()

        assert bypass[bypass.index("--permission-mode") + 1] == "bypassPermissions"
        assert default[default.index("--permission-mode") + 1] == "default"

    def test_permission_mode_override(self):
        cmd = RemoteControlTransport(
            "/tmp", skip_permissions=True, remote_control_permission_mode="acceptEdits"
        )._build_command()

        assert cmd[cmd.index("--permission-mode") + 1] == "acceptEdits"

    def test_cli_binary_override(self):
        cmd = RemoteControlTransport("/tmp", cli_binary="claude-custom")._build_command()
        assert cmd[0] == "claude-custom"

    def test_capabilities_disable_send_message(self):
        caps = RemoteControlTransport("/tmp").capabilities
        assert caps.send_message is False


class TestPairingUrlScrape:
    def test_url_regex_matches_pairing_link(self):
        line = "Open https://claude.ai/code?environment=env_AbC-123_xyz to pair"
        match = _URL_RE.search(line)
        assert match is not None
        assert match.group(0) == "https://claude.ai/code?environment=env_AbC-123_xyz"

    def test_ansi_sequences_are_stripped_before_scraping(self):
        decorated = "\x1b[1m\x1b[32mhttps://claude.ai/code?environment=env_tok\x1b[0m"
        clean = _ANSI_RE.sub("", decorated)
        assert _URL_RE.search(clean) is not None

    @pytest.mark.asyncio
    async def test_reader_emits_remote_control_event(self):
        transport = RemoteControlTransport("/tmp", session_id="tok12345")
        events: list[dict] = []

        async def collect(event: dict) -> None:
            events.append(event)

        transport.on_event(collect)

        class _Stdout:
            def __init__(self):
                self._lines = [
                    b"\x1b[2J booting remote control...\n",
                    b"pair here: https://claude.ai/code?environment=env_test_123\n",
                    b"",
                ]

            async def readline(self):
                return self._lines.pop(0)

        class _Proc:
            stdout = _Stdout()
            returncode = None

            async def wait(self):
                self.returncode = 0
                return 0

        transport._process = _Proc()
        await transport._read_output()

        assert transport.remote_control_url == ("https://claude.ai/code?environment=env_test_123")
        paired = [e for e in events if e.get("type") == "remote_control"]
        assert paired and paired[0]["url"].endswith("env_test_123")


class TestSpawnEnv:
    @pytest.mark.asyncio
    async def test_start_strips_api_key_auth_vars(self, monkeypatch):
        """Remote Control refuses to start under API-key auth — the spawn env
        must drop ANTHROPIC_API_KEY/AUTH_TOKEN so OAuth is used."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok")
        transport = RemoteControlTransport("/tmp", session_id="tok12345")
        captured: dict = {}

        async def fake_exec(*cmd, **kwargs):
            captured["env"] = kwargs.get("env", {})

            class _P:
                returncode = None
                stdout = None
                pid = 4242

                async def wait(self):
                    return 0

            return _P()

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            await transport.start()

        assert "ANTHROPIC_API_KEY" not in captured["env"]
        assert "ANTHROPIC_AUTH_TOKEN" not in captured["env"]
        if transport._reader_task is not None:
            transport._reader_task.cancel()


class TestSweepKill:
    def test_sweep_targets_only_token_processes(self):
        transport = RemoteControlTransport("/tmp", session_id="uniquetok999")
        seen: list[int] = []

        def fake_kill(pid: int, sig: int) -> None:
            seen.append(pid)

        process_table = {
            101: "claude remote-control --name volundr-uniquetok999 --spawn same-dir",
            102: "claude remote-control --name volundr-othertoken --spawn same-dir",
            103: "claude daemon",
        }

        with (
            patch.object(transport, "_iter_processes", return_value=process_table.items()),
            patch("os.kill", side_effect=fake_kill),
        ):
            killed = transport._sweep_kill(15)

        assert killed == 1
        assert seen == [101]


class TestProcessIteration:
    def test_ps_fallback_parses_pid_and_command(self):
        fake_ps = "  101 claude remote-control --name volundr-tok\n  bad line\n 103 claude daemon\n"
        with (
            patch("skuld.transports.remote_control.glob.glob", return_value=[]),
            patch(
                "skuld.transports.remote_control.subprocess.run",
                return_value=type("R", (), {"stdout": fake_ps})(),
            ),
        ):
            entries = RemoteControlTransport._iter_processes()

        assert (101, "claude remote-control --name volundr-tok") in entries
        assert (103, "claude daemon") in entries
        assert len(entries) == 2

    def test_ps_failure_returns_empty(self):
        with (
            patch("skuld.transports.remote_control.glob.glob", return_value=[]),
            patch(
                "skuld.transports.remote_control.subprocess.run",
                side_effect=OSError("no ps"),
            ),
        ):
            assert RemoteControlTransport._iter_processes() == []

    def test_sweep_without_token_is_noop(self):
        transport = RemoteControlTransport("/tmp")
        assert transport._sweep_kill(15) == 0


class TestStopAndResurface:
    @pytest.mark.asyncio
    async def test_stop_terminates_and_sweeps(self):
        import signal as _signal

        transport = RemoteControlTransport("/tmp", session_id="tok12345")

        class _Proc:
            pid = 4242
            returncode = None
            signals: list[str] = []

            def terminate(self):
                self.signals.append("terminate")
                self.returncode = 0

            async def wait(self):
                return 0

        proc = _Proc()
        transport._process = proc
        swept: list[int] = []
        with patch.object(transport, "_sweep_kill", side_effect=lambda sig: swept.append(sig) or 0):
            await transport.stop()

        assert "terminate" in proc.signals
        assert _signal.SIGTERM in swept

    @pytest.mark.asyncio
    async def test_stop_kills_and_awaits_process_that_ignores_terminate(self):
        """A process that never exits from SIGTERM must still be killed and
        awaited — regression test for the leaked subprocess transport where
        `stop()` gave up after a timed-out `wait()` and left the process (and
        its stdout pipe transport) to the raw-PID sweep, which never touches
        the asyncio Process object at all.
        """
        import signal as _signal

        transport = RemoteControlTransport("/tmp", session_id="tok12345")

        class _StubbornProc:
            pid = 4343
            returncode = None
            signals: list[str] = []
            waited = False

            def terminate(self):
                self.signals.append("terminate")

            def kill(self):
                self.signals.append("kill")
                self.returncode = -_signal.SIGKILL

            async def wait(self):
                if self.returncode is None:
                    # First wait(): the process ignores SIGTERM and this call
                    # would hang forever in production; stop() must not await
                    # it without a timeout.
                    await asyncio.sleep(3600)
                self.waited = True
                return self.returncode

        proc = _StubbornProc()
        transport._process = proc
        with (
            patch.object(transport, "_sweep_kill", return_value=0),
            patch("niuu.adapters.cli.runtime.asyncio.wait_for", _fast_timeout_wait_for),
        ):
            await transport.stop()

        assert proc.signals == ["terminate", "kill"]
        assert proc.waited

    @pytest.mark.asyncio
    async def test_send_message_resurfaces_pairing_url(self):
        transport = RemoteControlTransport("/tmp", session_id="tok12345")
        transport._url = "https://claude.ai/code?environment=env_abc"
        events: list[dict] = []

        async def collect(event: dict) -> None:
            events.append(event)

        transport.on_event(collect)
        await transport.send_message("hello?")

        resurfaced = [e for e in events if e.get("type") == "remote_control"]
        assert resurfaced and resurfaced[0]["url"].endswith("env_abc")


class TestStopDoesNotLeakSubprocessTransport:
    """Executable proof for the intermittent Forge-unit-lane failure:

        ExceptionGroup: multiple unraisable exception warnings
        ResourceWarning: unclosed transport <_UnixSubprocessTransport ...>

    The victim test was always unrelated to subprocesses — Python only warns
    when the abandoned transport is garbage-collected, which can happen
    during any later test, since asyncio only finishes closing a subprocess
    transport once BOTH its exit code is known AND its pipes are drained —
    and it only chases that down proactively for descendants a live event
    loop is still watching. The real bug was `RemoteControlTransport.stop()`
    giving up on the asyncio ``Process`` object after a timed-out ``wait()``
    and relying solely on a raw-PID sweep (``os.kill``) that never touches
    that ``Process``/its transport, so it was abandoned — still alive, still
    holding its stdout pipe transport open — whenever the client ignored
    SIGTERM. `gc.collect()` only reports the ``ResourceWarning`` once nothing
    references the abandoned ``Process`` any more, which can be long after
    the test that leaked it has finished; asserting on the reaped
    `returncode` right after `stop()` catches the defect immediately and
    deterministically instead of racing that GC timing.
    """

    def test_real_process_that_ignores_sigterm_is_killed_and_reaped(self):
        if sys.platform == "win32":
            pytest.skip("posix-only: relies on SIGTERM/SIGKILL reaping semantics")

        transport = RemoteControlTransport("/tmp", session_id="leaktest123")
        loop = asyncio.new_event_loop()
        pid: int | None = None
        try:

            async def _spawn_and_stop() -> asyncio.subprocess.Process:
                nonlocal pid
                # A real child, started through the exact same
                # `create_subprocess_exec(..., stdout=PIPE)` call `start()`
                # uses, that ignores SIGTERM so `stop()` must escalate.
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-c",
                    "import signal, time\n"
                    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                    "print('ready', flush=True)\n"
                    "time.sleep(20)\n",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                pid = process.pid
                transport._process = process
                # Block until the SIGTERM-ignoring handler is actually
                # installed — otherwise a SIGTERM sent during interpreter
                # start-up would kill the child via the *default* action and
                # the escalation path this test exists to exercise would
                # never run.
                await asyncio.wait_for(process.stdout.readline(), timeout=10)
                # The raw-PID sweep is a separate, independently-tested
                # safety net (see TestSweepKill above); disabling it here
                # isolates what `stop()` itself does with the `Process`
                # object it owns.
                with (
                    patch.object(transport, "_sweep_kill", return_value=0),
                    patch("asyncio.wait_for", _fast_timeout_wait_for),
                ):
                    await transport.stop()
                return process

            process = loop.run_until_complete(_spawn_and_stop())
        finally:
            # Mirrors pytest-asyncio's per-test loop teardown: close the loop
            # right after the run, with no grace period for stray callbacks.
            loop.close()
            if pid is not None:
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGKILL)

        # `stop()` must reap the process through the `Process` object itself
        # (terminate, then escalate to kill + wait on timeout) — not merely
        # rely on the separate PID sweep, which is disabled above. A
        # `returncode` of `None` here means the process (and its stdout pipe
        # transport) was abandoned mid-shutdown.
        assert process.returncode is not None
