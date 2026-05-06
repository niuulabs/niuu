"""CLI command transport adapter for Sleipnir.

Executes a shell command when an event matches a subscription pattern.
No long-running service is required — useful for lightweight integrations
that invoke scripts, curl, or other CLI tools in response to events.

Pass-event modes
----------------
stdin (default):
    The event JSON is written to the subprocess stdin.  Recommended for
    large payloads — no shell-escaping concerns.

env:
    Event metadata is exposed as environment variables:
    - ``SLEIPNIR_EVENT``           — full JSON payload
    - ``SLEIPNIR_EVENT_TYPE``      — event type string
    - ``SLEIPNIR_CORRELATION_ID``  — correlation id (empty string if None)
    - ``SLEIPNIR_SOURCE``          — source identifier

arg:
    The JSON payload is appended as a named argument:
    ``<command> [<args>...] <event_arg_name> <json>``.  The argument name
    defaults to ``--event`` and can be overridden via ``event_arg_name``.

Security note
-------------
The command is executed via :func:`asyncio.create_subprocess_exec` (not a
shell string), so there is no shell-injection risk from the event payload.

Configuration example
---------------------
::

    sleipnir:
      transports:
        - adapter: sleipnir.adapters.cli_command.CLICommandTransport
          command: "scripts/on-event.sh"
          args: ["--verbose"]
          subscribe: ["ravn.task.completed", "review.*"]
          pass_event_as: stdin   # stdin | env | arg
          event_arg_name: --event
          timeout_s: 30
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from sleipnir.adapters._subscriber_support import (
    DEFAULT_RING_BUFFER_DEPTH,
    _BaseSubscription,
    consume_queue,
    dispatch_to_subscriptions,
)
from sleipnir.domain.events import SleipnirEvent
from sleipnir.ports.events import EventHandler, SleipnirSubscriber, Subscription

logger = logging.getLogger(__name__)


#: Supported values for ``pass_event_as``.
_VALID_PASS_MODES: frozenset[str] = frozenset({"stdin", "env", "arg"})

#: Default timeout for CLI command execution (seconds).
_DEFAULT_TIMEOUT_S: int = 30

#: Default argument name when ``pass_event_as: arg`` is used.
_DEFAULT_EVENT_ARG_NAME: str = "--event"


class CLICommandTransport(SleipnirSubscriber):
    """Sleipnir subscriber that executes a shell command per matching event.

    Implements :class:`~sleipnir.ports.events.SleipnirSubscriber` so it can
    be registered on any event bus or composed with other transports.

    Typical wiring::

        cli = CLICommandTransport(command="scripts/on-event.sh",
                                  subscribe=["ravn.task.completed"])
        # Register on an existing bus — cli.handle is the dispatch entry point
        await bus.subscribe(cli.subscribe_patterns, cli.handle)

    :param command:         Executable path (no shell expansion).
    :param args:            Static argument list prepended before the event
                            argument (when applicable).
    :param subscribe:       Event-type patterns this transport cares about.
                            Used by the wiring layer; not enforced by
                            :meth:`subscribe` itself.
    :param pass_event_as:   How to deliver the event payload to the subprocess.
                            One of ``"stdin"``, ``"env"``, or ``"arg"``.
    :param event_arg_name:  Named argument used when ``pass_event_as="arg"``.
                            Defaults to ``"--event"``.
    :param timeout_s:       Seconds before a running command is killed.
    :param ring_buffer_depth: Per-subscription queue depth (ring buffer).
    """

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        subscribe: list[str] | None = None,
        pass_event_as: str = "stdin",
        event_arg_name: str = _DEFAULT_EVENT_ARG_NAME,
        timeout_s: int = _DEFAULT_TIMEOUT_S,
        ring_buffer_depth: int = DEFAULT_RING_BUFFER_DEPTH,
    ) -> None:
        if pass_event_as not in _VALID_PASS_MODES:
            raise ValueError(
                f"pass_event_as must be one of {sorted(_VALID_PASS_MODES)}, got {pass_event_as!r}"
            )
        if pass_event_as == "arg" and not event_arg_name:
            raise ValueError("event_arg_name must not be empty when pass_event_as='arg'")
        if ring_buffer_depth < 1:
            raise ValueError(f"ring_buffer_depth must be >= 1, got {ring_buffer_depth}")

        self._command = command
        self._args: list[str] = list(args or [])
        self._subscribe_patterns: list[str] = list(subscribe or [])
        self._pass_event_as = pass_event_as
        self._event_arg_name = event_arg_name
        self._timeout_s = timeout_s
        self._ring_buffer_depth = ring_buffer_depth
        self._subscriptions: list[_BaseSubscription] = []

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def subscribe_patterns(self) -> list[str]:
        """Event-type patterns configured for this transport."""
        return list(self._subscribe_patterns)

    # ------------------------------------------------------------------
    # SleipnirSubscriber port
    # ------------------------------------------------------------------

    async def subscribe(
        self,
        event_types: list[str],
        handler: EventHandler,
    ) -> Subscription:
        """Register a subscription that executes the CLI command per event.

        The *handler* argument satisfies the port contract but is not invoked —
        the configured CLI command is the event consumer.  To chain a downstream
        handler, wrap it with :meth:`handle` before passing to an external bus.

        :param event_types: Patterns to match (fnmatch shell-style wildcards).
        :param handler:     Accepted for port compatibility; not used.
        :returns:           A :class:`~sleipnir.ports.events.Subscription`
                            handle; call ``unsubscribe()`` to cancel.
        """
        queue: asyncio.Queue[SleipnirEvent] = asyncio.Queue(maxsize=self._ring_buffer_depth)
        task = asyncio.create_task(consume_queue(queue, self._run_command))
        sub = _BaseSubscription(
            list(event_types),
            queue,
            task,
            lambda: self._remove_subscription(sub),
        )
        self._subscriptions.append(sub)
        return sub

    # ------------------------------------------------------------------
    # Dispatch entry point (for external buses)
    # ------------------------------------------------------------------

    async def handle(self, event: SleipnirEvent) -> None:
        """Dispatch *event* to all active subscriptions whose patterns match.

        Pass this method as the handler when registering with an external bus::

            await bus.subscribe(cli.subscribe_patterns, cli.handle)
        """
        await dispatch_to_subscriptions(event, self._subscriptions, self._ring_buffer_depth, logger)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _remove_subscription(self, sub: _BaseSubscription) -> None:
        try:
            self._subscriptions.remove(sub)
        except ValueError:
            pass

    def _build_argv(self, event_json: str) -> list[str]:
        """Return the argv list for the subprocess."""
        argv = [self._command, *self._args]
        if self._pass_event_as == "arg":
            argv = [*argv, self._event_arg_name, event_json]
        return argv

    def _build_env(self, event: SleipnirEvent, event_json: str) -> dict[str, str] | None:
        """Return the environment for the subprocess, or None to inherit."""
        if self._pass_event_as != "env":
            return None
        return {
            **os.environ,
            "SLEIPNIR_EVENT": event_json,
            "SLEIPNIR_EVENT_TYPE": event.event_type,
            "SLEIPNIR_CORRELATION_ID": event.correlation_id or "",
            "SLEIPNIR_SOURCE": event.source,
        }

    async def _run_command(self, event: SleipnirEvent) -> None:
        """Execute the configured command for *event*.

        - Serialises the event as JSON.
        - Builds argv and environment according to ``pass_event_as``.
        - Kills the subprocess if it exceeds ``timeout_s``.
        - Logs stdout at DEBUG, stderr at DEBUG for success / WARNING on failure.
        - Logs a WARNING for non-zero exit codes.
        - Logs ERROR for ``FileNotFoundError`` / ``PermissionError`` / ``OSError``.
        """
        event_json = json.dumps(event.to_dict())
        argv = self._build_argv(event_json)
        env = self._build_env(event, event_json)
        stdin_data: bytes | None = event_json.encode() if self._pass_event_as == "stdin" else None

        try:
            _pipe = asyncio.subprocess.PIPE
            _devnull = asyncio.subprocess.DEVNULL
            stdin_mode = _pipe if stdin_data is not None else _devnull
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=stdin_mode,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError:
            logger.error(
                "CLI command not found: %r (event=%s type=%s)",
                self._command,
                event.event_id,
                event.event_type,
            )
            return
        except PermissionError:
            logger.error(
                "CLI command permission denied: %r (event=%s type=%s)",
                self._command,
                event.event_id,
                event.event_type,
            )
            return
        except OSError as exc:
            logger.error(
                "CLI command OS error launching %r: %s (event=%s type=%s)",
                self._command,
                exc,
                event.event_id,
                event.event_type,
            )
            return

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(stdin_data),
                timeout=self._timeout_s,
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            logger.warning(
                "CLI command timed out after %ds: %r (event=%s type=%s)",
                self._timeout_s,
                self._command,
                event.event_id,
                event.event_type,
            )
            return

        stdout_text = stdout_bytes.decode(errors="replace").rstrip() if stdout_bytes else ""
        stderr_text = stderr_bytes.decode(errors="replace").rstrip() if stderr_bytes else ""

        if stdout_text:
            logger.debug(
                "CLI command stdout (event=%s): %s",
                event.event_id,
                stdout_text,
            )
        if stderr_text:
            logger.debug(
                "CLI command stderr (event=%s): %s",
                event.event_id,
                stderr_text,
            )

        if proc.returncode != 0:
            logger.warning(
                "CLI command exited %d: %r (event=%s type=%s)%s",
                proc.returncode,
                self._command,
                event.event_id,
                event.event_type,
                f"\nstderr: {stderr_text}" if stderr_text else "",
            )
        else:
            logger.debug(
                "CLI command exited 0: %r (event=%s type=%s)",
                self._command,
                event.event_id,
                event.event_type,
            )
