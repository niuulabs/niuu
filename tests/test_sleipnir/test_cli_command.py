"""Tests for CLICommandTransport (NIU-630)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

from sleipnir.adapters._subscriber_support import DEFAULT_RING_BUFFER_DEPTH
from sleipnir.adapters.cli_command import (
    _DEFAULT_EVENT_ARG_NAME,
    _DEFAULT_TIMEOUT_S,
    _VALID_PASS_MODES,
    CLICommandTransport,
)
from sleipnir.domain.events import SleipnirEvent
from sleipnir.ports.events import Subscription
from tests.test_sleipnir.conftest import make_event

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOOP = AsyncMock()


def _make_transport(**kwargs) -> CLICommandTransport:
    defaults = dict(command="echo", subscribe=["ravn.*"])
    defaults.update(kwargs)
    return CLICommandTransport(**defaults)


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------


class TestCLICommandTransportInit:
    def test_defaults(self):
        t = CLICommandTransport(command="echo")
        assert t._command == "echo"
        assert t._args == []
        assert t._subscribe_patterns == []
        assert t._pass_event_as == "stdin"
        assert t._event_arg_name == _DEFAULT_EVENT_ARG_NAME
        assert t._timeout_s == _DEFAULT_TIMEOUT_S
        assert t._ring_buffer_depth == DEFAULT_RING_BUFFER_DEPTH

    def test_all_params_stored(self):
        t = CLICommandTransport(
            command="/usr/bin/curl",
            args=["-s", "-X", "POST"],
            subscribe=["ravn.*", "tyr.*"],
            pass_event_as="env",
            event_arg_name="--payload",
            timeout_s=60,
            ring_buffer_depth=512,
        )
        assert t._command == "/usr/bin/curl"
        assert t._args == ["-s", "-X", "POST"]
        assert t._subscribe_patterns == ["ravn.*", "tyr.*"]
        assert t._pass_event_as == "env"
        assert t._event_arg_name == "--payload"
        assert t._timeout_s == 60
        assert t._ring_buffer_depth == 512

    def test_invalid_pass_event_as_raises(self):
        with pytest.raises(ValueError, match="pass_event_as must be one of"):
            CLICommandTransport(command="echo", pass_event_as="invalid")

    def test_arg_mode_empty_event_arg_name_raises(self):
        with pytest.raises(ValueError, match="event_arg_name must not be empty"):
            CLICommandTransport(command="echo", pass_event_as="arg", event_arg_name="")

    def test_zero_ring_buffer_depth_raises(self):
        with pytest.raises(ValueError, match="ring_buffer_depth must be >= 1"):
            CLICommandTransport(command="echo", ring_buffer_depth=0)

    def test_valid_pass_modes_accepted(self):
        for mode in _VALID_PASS_MODES:
            t = CLICommandTransport(command="echo", pass_event_as=mode)
            assert t._pass_event_as == mode

    def test_args_default_to_empty_list(self):
        t = CLICommandTransport(command="echo", args=None)
        assert t._args == []

    def test_subscribe_default_to_empty_list(self):
        t = CLICommandTransport(command="echo", subscribe=None)
        assert t._subscribe_patterns == []


# ---------------------------------------------------------------------------
# subscribe_patterns property
# ---------------------------------------------------------------------------


class TestSubscribePatterns:
    def test_returns_configured_patterns(self):
        t = _make_transport(subscribe=["ravn.*", "tyr.*"])
        assert t.subscribe_patterns == ["ravn.*", "tyr.*"]

    def test_returns_copy(self):
        t = _make_transport(subscribe=["ravn.*"])
        patterns = t.subscribe_patterns
        patterns.append("extra")
        assert t.subscribe_patterns == ["ravn.*"]  # original unchanged


# ---------------------------------------------------------------------------
# subscribe() — port contract
# ---------------------------------------------------------------------------


class TestSubscribePort:
    async def test_subscribe_returns_subscription(self):
        t = _make_transport()
        sub = await t.subscribe(["ravn.*"], _NOOP)
        assert isinstance(sub, Subscription)
        await sub.unsubscribe()

    async def test_subscribe_uses_cli_command_not_handler(self):
        """The passed handler is not invoked; the CLI command is the consumer."""
        called = []
        handler = AsyncMock(side_effect=lambda e: called.append(e))

        t = _make_transport(command=sys.executable, args=["-c", "import sys; sys.exit(0)"])
        sub = await t.subscribe(["ravn.*"], handler)
        await t.handle(make_event())
        # Give the queue consumer a moment to run
        await asyncio.sleep(0.05)

        handler.assert_not_called()
        await sub.unsubscribe()

    async def test_unsubscribe_removes_subscription(self):
        t = _make_transport()
        sub = await t.subscribe(["ravn.*"], _NOOP)
        assert len(t._subscriptions) == 1
        await sub.unsubscribe()
        assert len(t._subscriptions) == 0

    async def test_multiple_subscriptions_tracked(self):
        t = _make_transport()
        s1 = await t.subscribe(["ravn.*"], _NOOP)
        s2 = await t.subscribe(["tyr.*"], _NOOP)
        assert len(t._subscriptions) == 2
        await s1.unsubscribe()
        await s2.unsubscribe()


# ---------------------------------------------------------------------------
# handle() — dispatch entry point
# ---------------------------------------------------------------------------


class TestHandleDispatch:
    async def test_handle_dispatches_to_matching_subscription(self):
        received: list[SleipnirEvent] = []

        t = CLICommandTransport(command=sys.executable, args=["-c", "import sys; sys.exit(0)"])

        # Patch _run_command to capture events instead of spawning a process
        async def _capture(event: SleipnirEvent) -> None:
            received.append(event)

        t._run_command = _capture  # type: ignore[method-assign]

        sub = await t.subscribe(["ravn.*"], _NOOP)
        await t.handle(make_event(event_type="ravn.tool.complete"))
        await asyncio.sleep(0.05)

        assert len(received) == 1
        assert received[0].event_type == "ravn.tool.complete"
        await sub.unsubscribe()

    async def test_handle_does_not_dispatch_non_matching(self):
        received: list[SleipnirEvent] = []

        t = CLICommandTransport(command="echo")

        async def _capture(event: SleipnirEvent) -> None:
            received.append(event)

        t._run_command = _capture  # type: ignore[method-assign]

        sub = await t.subscribe(["ravn.*"], _NOOP)
        await t.handle(make_event(event_type="tyr.saga.created"))
        await asyncio.sleep(0.05)

        assert received == []
        await sub.unsubscribe()

    async def test_handle_dispatches_to_multiple_subscriptions(self):
        calls: list[str] = []

        async def capture(event: SleipnirEvent) -> None:
            calls.append(event.event_type)

        t = CLICommandTransport(command="echo")
        t._run_command = capture  # type: ignore[method-assign]

        s1 = await t.subscribe(["ravn.*"], _NOOP)
        s2 = await t.subscribe(["ravn.*"], _NOOP)
        await t.handle(make_event(event_type="ravn.step.start"))
        await asyncio.sleep(0.05)

        assert len(calls) == 2
        await s1.unsubscribe()
        await s2.unsubscribe()

    async def test_handle_drops_expired_event(self):
        received: list[SleipnirEvent] = []

        async def capture(event: SleipnirEvent) -> None:
            received.append(event)

        t = CLICommandTransport(command="echo")
        t._run_command = capture  # type: ignore[method-assign]

        sub = await t.subscribe(["ravn.*"], _NOOP)
        expired = make_event(ttl=0)
        await t.handle(expired)
        await asyncio.sleep(0.05)

        assert received == []
        await sub.unsubscribe()


# ---------------------------------------------------------------------------
# _build_argv
# ---------------------------------------------------------------------------


class TestBuildArgv:
    def test_stdin_mode_no_extra_args(self):
        t = CLICommandTransport(command="myscript.sh", pass_event_as="stdin")
        argv = t._build_argv('{"key": "val"}')
        assert argv == ["myscript.sh"]

    def test_stdin_mode_with_static_args(self):
        t = CLICommandTransport(command="myscript.sh", args=["--verbose"], pass_event_as="stdin")
        argv = t._build_argv("{}")
        assert argv == ["myscript.sh", "--verbose"]

    def test_env_mode_no_extra_args(self):
        t = CLICommandTransport(command="myscript.sh", pass_event_as="env")
        argv = t._build_argv("{}")
        assert argv == ["myscript.sh"]

    def test_arg_mode_appends_name_and_json(self):
        t = CLICommandTransport(
            command="myscript.sh",
            args=["--dry-run"],
            pass_event_as="arg",
            event_arg_name="--event",
        )
        event_json = '{"event_type": "ravn.tool.complete"}'
        argv = t._build_argv(event_json)
        assert argv == ["myscript.sh", "--dry-run", "--event", event_json]

    def test_arg_mode_custom_arg_name(self):
        t = CLICommandTransport(
            command="notify.sh",
            pass_event_as="arg",
            event_arg_name="--payload",
        )
        argv = t._build_argv('{"x": 1}')
        assert argv == ["notify.sh", "--payload", '{"x": 1}']


# ---------------------------------------------------------------------------
# _build_env
# ---------------------------------------------------------------------------


class TestBuildEnv:
    def test_stdin_mode_returns_none(self):
        t = CLICommandTransport(command="echo", pass_event_as="stdin")
        assert t._build_env(make_event(), "{}") is None

    def test_arg_mode_returns_none(self):
        t = CLICommandTransport(command="echo", pass_event_as="arg")
        assert t._build_env(make_event(), "{}") is None

    def test_env_mode_includes_sleipnir_vars(self):
        event = make_event(
            event_type="ravn.tool.complete",
            source="ravn:agent-1",
            correlation_id="corr-xyz",
        )
        event_json = json.dumps(event.to_dict())
        t = CLICommandTransport(command="echo", pass_event_as="env")
        env = t._build_env(event, event_json)

        assert env is not None
        assert env["SLEIPNIR_EVENT"] == event_json
        assert env["SLEIPNIR_EVENT_TYPE"] == "ravn.tool.complete"
        assert env["SLEIPNIR_CORRELATION_ID"] == "corr-xyz"
        assert env["SLEIPNIR_SOURCE"] == "ravn:agent-1"

    def test_env_mode_none_correlation_id_maps_to_empty_string(self):
        event = make_event(correlation_id=None)
        t = CLICommandTransport(command="echo", pass_event_as="env")
        env = t._build_env(event, "{}")
        assert env["SLEIPNIR_CORRELATION_ID"] == ""

    def test_env_mode_inherits_os_environ(self):
        t = CLICommandTransport(command="echo", pass_event_as="env")
        with patch.dict(os.environ, {"MY_VAR": "hello"}):
            env = t._build_env(make_event(), "{}")
        assert env is not None
        assert env.get("MY_VAR") == "hello"


# ---------------------------------------------------------------------------
# _run_command — subprocess execution
# ---------------------------------------------------------------------------


class TestRunCommand:
    """Tests that use real subprocess execution via sys.executable."""

    async def test_stdin_mode_receives_json(self, tmp_path):
        """Command reads JSON from stdin and writes it to a file."""
        out = tmp_path / "out.json"
        script = (
            f"import sys, json; data=json.load(sys.stdin); "
            f"open({str(out)!r}, 'w').write(data['event_type'])"
        )
        t = CLICommandTransport(
            command=sys.executable,
            args=["-c", script],
            pass_event_as="stdin",
        )
        event = make_event(event_type="ravn.tool.complete")
        await t._run_command(event)
        assert out.read_text() == "ravn.tool.complete"

    async def test_env_mode_receives_env_vars(self, tmp_path):
        """Command reads SLEIPNIR_EVENT_TYPE from env and writes to file."""
        out = tmp_path / "out.txt"
        script = f"import os; open({str(out)!r}, 'w').write(os.environ['SLEIPNIR_EVENT_TYPE'])"
        t = CLICommandTransport(
            command=sys.executable,
            args=["-c", script],
            pass_event_as="env",
        )
        event = make_event(event_type="ravn.step.start")
        await t._run_command(event)
        assert out.read_text() == "ravn.step.start"

    async def test_arg_mode_receives_json_arg(self, tmp_path):
        """Command reads --event arg and writes event_type to file."""
        out = tmp_path / "out.txt"
        script = (
            f"import sys, json; "
            f"idx=sys.argv.index('--event'); "
            f"data=json.loads(sys.argv[idx+1]); "
            f"open({str(out)!r}, 'w').write(data['event_type'])"
        )
        t = CLICommandTransport(
            command=sys.executable,
            args=["-c", script],
            pass_event_as="arg",
            event_arg_name="--event",
        )
        event = make_event(event_type="tyr.saga.created")
        await t._run_command(event)
        assert out.read_text() == "tyr.saga.created"

    async def test_zero_exit_logged_at_debug(self, caplog):
        t = CLICommandTransport(
            command=sys.executable,
            args=["-c", "import sys; sys.exit(0)"],
        )
        with caplog.at_level(logging.DEBUG, logger="sleipnir.adapters.cli_command"):
            await t._run_command(make_event())
        assert any("exited 0" in r.message for r in caplog.records)

    async def test_nonzero_exit_logged_at_warning(self, caplog):
        t = CLICommandTransport(
            command=sys.executable,
            args=["-c", "import sys; sys.exit(42)"],
        )
        with caplog.at_level(logging.WARNING, logger="sleipnir.adapters.cli_command"):
            await t._run_command(make_event())
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("exited 42" in r.message for r in warnings)

    async def test_stdout_logged_at_debug(self, caplog):
        t = CLICommandTransport(
            command=sys.executable,
            args=["-c", "print('hello from stdout')"],
        )
        with caplog.at_level(logging.DEBUG, logger="sleipnir.adapters.cli_command"):
            await t._run_command(make_event())
        assert any("hello from stdout" in r.message for r in caplog.records)

    async def test_stderr_logged_at_debug_on_success(self, caplog):
        t = CLICommandTransport(
            command=sys.executable,
            args=["-c", "import sys; print('err output', file=sys.stderr)"],
        )
        with caplog.at_level(logging.DEBUG, logger="sleipnir.adapters.cli_command"):
            await t._run_command(make_event())
        assert any("err output" in r.message for r in caplog.records)

    async def test_timeout_kills_process(self, caplog):
        t = CLICommandTransport(
            command=sys.executable,
            args=["-c", "import time; time.sleep(10)"],
            timeout_s=1,
        )
        with caplog.at_level(logging.WARNING, logger="sleipnir.adapters.cli_command"):
            await t._run_command(make_event())
        assert any("timed out" in r.message for r in caplog.records)

    async def test_file_not_found_logged_at_error(self, caplog):
        t = CLICommandTransport(command="/no/such/command/xyz")
        with caplog.at_level(logging.ERROR, logger="sleipnir.adapters.cli_command"):
            await t._run_command(make_event())
        assert any("not found" in r.message for r in caplog.records)

    async def test_permission_error_logged_at_error(self, tmp_path, caplog):
        """Create a non-executable file and attempt to run it."""
        script = tmp_path / "noperm.sh"
        script.write_text("#!/bin/sh\necho hello")
        script.chmod(0o000)  # no permissions
        t = CLICommandTransport(command=str(script))
        with caplog.at_level(logging.ERROR, logger="sleipnir.adapters.cli_command"):
            await t._run_command(make_event())
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert errors, "Expected at least one ERROR log"

    async def test_os_error_logged_at_error(self, caplog):
        """Simulate an arbitrary OSError during subprocess creation."""
        t = CLICommandTransport(command="echo")
        with (
            patch(
                "asyncio.create_subprocess_exec",
                side_effect=OSError("mock OS error"),
            ),
            caplog.at_level(logging.ERROR, logger="sleipnir.adapters.cli_command"),
        ):
            await t._run_command(make_event())
        assert any("OS error" in r.message for r in caplog.records)

    async def test_event_json_is_valid(self, tmp_path):
        """Confirm JSON written to stdin is a valid SleipnirEvent dict."""
        out = tmp_path / "raw.json"
        script = f"import sys; open({str(out)!r}, 'w').write(sys.stdin.read())"
        t = CLICommandTransport(
            command=sys.executable,
            args=["-c", script],
            pass_event_as="stdin",
        )
        event = make_event(event_type="ravn.tool.complete", correlation_id="c-1")
        await t._run_command(event)
        data = json.loads(out.read_text())
        assert data["event_type"] == "ravn.tool.complete"
        assert data["correlation_id"] == "c-1"

    async def test_static_args_passed_before_event_arg(self, tmp_path):
        """Static args appear before the --event arg in argv."""
        out = tmp_path / "argv.json"
        script = f"import sys, json; open({str(out)!r}, 'w').write(json.dumps(sys.argv[1:]))"
        t = CLICommandTransport(
            command=sys.executable,
            args=["-c", script, "--flag", "value"],
            pass_event_as="arg",
            event_arg_name="--event",
        )
        await t._run_command(make_event())
        argv = json.loads(out.read_text())
        flag_idx = argv.index("--flag")
        event_idx = argv.index("--event")
        assert flag_idx < event_idx


# ---------------------------------------------------------------------------
# Integration: handle → subscribe → _run_command via queue
# ---------------------------------------------------------------------------


class TestHandleToCommandIntegration:
    async def test_end_to_end_stdin_delivery(self, tmp_path):
        out = tmp_path / "result.txt"
        script = (
            f"import sys, json; d=json.load(sys.stdin); "
            f"open({str(out)!r}, 'w').write(d['event_type'])"
        )
        t = CLICommandTransport(
            command=sys.executable,
            args=["-c", script],
            subscribe=["ravn.*"],
            pass_event_as="stdin",
        )
        sub = await t.subscribe(["ravn.*"], _NOOP)
        await t.handle(make_event(event_type="ravn.task.completed"))
        # Wait for queue consumer to finish
        await sub._queue.join()
        assert out.read_text() == "ravn.task.completed"
        await sub.unsubscribe()

    async def test_non_matching_events_not_dispatched(self, tmp_path):
        out = tmp_path / "flag.txt"
        script = f"open({str(out)!r}, 'w').write('ran')"
        t = CLICommandTransport(
            command=sys.executable,
            args=["-c", script],
            subscribe=["ravn.*"],
            pass_event_as="stdin",
        )
        sub = await t.subscribe(["ravn.*"], _NOOP)
        await t.handle(make_event(event_type="tyr.saga.created"))
        await asyncio.sleep(0.05)
        assert not out.exists()
        await sub.unsubscribe()

    async def test_wildcard_star_matches_all(self, tmp_path):
        out = tmp_path / "out.txt"
        script = (
            f"import sys, json; d=json.load(sys.stdin); "
            f"open({str(out)!r}, 'w').write(d['event_type'])"
        )
        t = CLICommandTransport(
            command=sys.executable,
            args=["-c", script],
            pass_event_as="stdin",
        )
        sub = await t.subscribe(["*"], _NOOP)
        await t.handle(make_event(event_type="tyr.saga.created"))
        await sub._queue.join()
        assert out.read_text() == "tyr.saga.created"
        await sub.unsubscribe()
