"""BrokerHarness — wire a REAL skuld Broker to a REAL tmux transport + fakeagent.

This is the broker-tier seam of the forge tmux harness. It stands a live
``skuld.broker.Broker`` up against a real ``TmuxInteractiveTransport`` driving
``fakeagent`` inside tmux, with in-process fake WebSocket clients and a
durable-log spy — no Volundr backend, no aiohttp, no live IDP.

Wiring approach (the MINIMAL manual path, NOT ``broker.startup()``):

  * ``broker.startup()`` pulls in the chronicle watcher, peer watchdog, the
    httpx client, the event-log flush loop, and timeline reporting — none of
    which a harness wants. Instead we construct the Broker, build the transport
    ourselves, hand it to the broker via ``broker._transport`` + register
    ``broker._handle_cli_event`` through ``transport.on_event``, start the
    transport, and stand a ``HookServer`` whose handler awaits
    ``broker.handle_claude_hook``.
  * The durable event log: ``_enqueue_event_log`` only buffers when BOTH
    ``event_log_enabled`` (default True) AND ``volundr_api_url`` is truthy. We
    set a dummy ``volundr_api_url`` so frames buffer, and we NEVER start the
    flush loop (``startup`` is the only thing that starts it), so the buffer
    accumulates in ``broker._event_log_buffer`` and ``h.event_log`` can read it.

A ``FakeWebSocket`` mirrors exactly the Starlette/FastAPI WebSocket surface that
``Broker.handle_websocket`` and ``WebSocketChannel`` touch: ``accept()``,
``receive_json()``, ``send_json()``, ``send_text()``, ``close()``. Inbound test
frames are pushed onto an ``asyncio.Queue``; outbound frames are recorded.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import signal
import subprocess
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import WebSocketDisconnect

from skuld.broker import Broker
from skuld.config import SkuldSettings
from skuld.transports.tmux_interactive import TmuxInteractiveTransport
from tests.support.forge.fakeclaude_shim import install_fake_claude
from tests.support.forge.hook_server import HookServer

_TMUX_TRANSPORT = "skuld.transports.tmux_interactive.TmuxInteractiveTransport"


class FakeWebSocket:
    """In-process stand-in for a FastAPI/Starlette WebSocket.

    Only implements the surface ``Broker.handle_websocket`` + ``WebSocketChannel``
    use. Inbound frames arrive via ``feed`` (queued); the handler's
    ``receive_json`` awaits them. Outbound frames are recorded in ``frames``.
    """

    def __init__(self) -> None:
        self._inbound: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.frames: list[dict[str, Any]] = []
        self.accepted = False
        self.closed = False
        # Set by ``kill`` to make the NEXT receive raise WebSocketDisconnect,
        # unwinding handle_websocket exactly like a dropped browser socket.
        self._disconnect = False
        # Headers are read pre-accept by _update_jwt_from_websocket.
        self.headers: dict[str, str] = {}

    async def accept(self) -> None:
        self.accepted = True

    def feed(self, message: dict[str, Any]) -> None:
        self._inbound.put_nowait(message)

    def kill(self) -> None:
        self._disconnect = True
        # Wake a blocked receive_json so it observes the disconnect promptly.
        self._inbound.put_nowait({"__kill__": True})

    async def receive_json(self) -> dict[str, Any]:
        if self._disconnect:
            raise WebSocketDisconnect(code=1006)
        message = await self._inbound.get()
        if message.get("__kill__"):
            raise WebSocketDisconnect(code=1006)
        return message

    async def receive_text(self) -> str:
        return json.dumps(await self.receive_json())

    async def send_json(self, payload: dict[str, Any]) -> None:
        if self.closed:
            raise WebSocketDisconnect(code=1006)
        self.frames.append(payload)

    async def send_text(self, text: str) -> None:
        await self.send_json(json.loads(text))

    async def close(self, code: int = 1000) -> None:
        self.closed = True


class FakeWsClient:
    """Test-side handle for one fake browser WebSocket connection."""

    def __init__(self, ws: FakeWebSocket, task: asyncio.Task[None]) -> None:
        self._ws = ws
        self._task = task

    @property
    def ws(self) -> FakeWebSocket:
        return self._ws

    @property
    def frames(self) -> list[dict[str, Any]]:
        return self._ws.frames

    @property
    def task(self) -> asyncio.Task[None]:
        return self._task

    def send(self, message: dict[str, Any]) -> None:
        """Push an inbound frame as if the browser sent it."""
        self._ws.feed(message)

    def frames_of_type(self, frame_type: str) -> list[dict[str, Any]]:
        return [f for f in self._ws.frames if f.get("type") == frame_type]

    async def wait_for(
        self,
        predicate: Callable[[list[dict[str, Any]]], bool],
        timeout: float = 3.0,
    ) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if predicate(self._ws.frames):
                return
            await asyncio.sleep(0.02)
        raise AssertionError(
            f"frames predicate not satisfied within {timeout}s. Frames so far:\n"
            + "\n".join(repr(f) for f in self._ws.frames)
        )

    async def wait_for_type(self, frame_type: str, timeout: float = 3.0) -> dict[str, Any]:
        await self.wait_for(
            lambda frames: any(f.get("type") == frame_type for f in frames),
            timeout=timeout,
        )
        return self.frames_of_type(frame_type)[-1]


class BrokerHarness:
    """Async context manager wiring a real Broker → real tmux transport → fakeagent."""

    def __init__(
        self,
        *,
        boot: str | None = None,
        hooks: bool = True,
        skip_permissions: bool = False,
        idle_timeout_s: float = 0.3,
        no_output_timeout_s: float = 3.0,
        pane_poll_interval_s: float = 0.2,
        ask_user_question_enabled: bool = False,
        start_transport: bool = False,
        bin_dir: Path | None = None,
        workspace_dir: Path | None = None,
    ) -> None:
        self._boot = boot
        self._hooks = hooks
        self._skip_permissions = skip_permissions
        self._idle_timeout_s = idle_timeout_s
        self._no_output_timeout_s = no_output_timeout_s
        self._pane_poll_interval_s = pane_poll_interval_s
        self._ask_user_question_enabled = ask_user_question_enabled
        # When False, the transport is NOT started in __aenter__ — the broker
        # lazy-starts it on the first connect(), so a connected client observes
        # the one-shot system/init + terminal_pane_opened start frames. Tests
        # that drive the transport directly (no client) pass True.
        self._start_transport = start_transport
        self._bin_dir = bin_dir
        self._workspace_dir = workspace_dir

        # A token unique to this harness — used as both the tmux session id and
        # the fakeagent FORGE_FAKEAGENT_SESSION, so crash_agent() can pkill the
        # exact agent process without touching unrelated sessions.
        self._token = f"bh-{uuid.uuid4().hex[:8]}"

        self.broker: Broker | None = None
        self.transport: TmuxInteractiveTransport | None = None
        self.hook_server: HookServer | None = None
        self._clients: list[FakeWsClient] = []
        self._old_env: dict[str, str | None] = {}
        self._started = False

    # ---------------------------------------------------------------- lifecycle

    async def __aenter__(self) -> BrokerHarness:
        if shutil.which("tmux") is None:  # pragma: no cover - guarded by skip in tests
            raise RuntimeError("tmux is not installed")
        await self._start()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self._stop()

    async def _start(self) -> None:
        bin_dir = self._bin_dir or Path(self._make_tmp("bin"))
        workspace_dir = self._workspace_dir or Path(self._make_tmp("workspace"))

        env_mutations = install_fake_claude(bin_dir, boot=self._boot)
        # The fakeagent reads this to name its Stop-hook session id, and we
        # reuse the token to find + kill its process for crash_agent().
        env_mutations["FORGE_FAKEAGENT_SESSION"] = self._token
        # Remote Control would try to register with claude.ai — force it off.
        env_mutations["SKULD__TMUX_REMOTE_CONTROL"] = "0"
        self._apply_env(env_mutations)

        # Stand up the hook server FIRST so we know the port before the transport
        # writes its hook settings file (sdk_port -> hook URL).
        self.hook_server = await HookServer(self._hook_handler).start()
        sdk_port = self.hook_server.port if self._hooks else None

        self.transport = TmuxInteractiveTransport(
            workspace_dir=str(workspace_dir),
            session_id=self._token,
            skip_permissions=self._skip_permissions,
            sdk_port=sdk_port,
            turn_idle_timeout_s=self._idle_timeout_s,
            turn_no_output_timeout_s=self._no_output_timeout_s,
            pane_poll_interval_s=self._pane_poll_interval_s,
        )

        settings = SkuldSettings(
            transport_adapter=_TMUX_TRANSPORT,
            # Truthy so _enqueue_event_log buffers; we never start the flush loop
            # (only broker.startup() does), so nothing drains the buffer and it
            # is fully readable via h.event_log.
            volundr_api_url="http://harness.invalid",
            event_log_enabled=True,
            skip_permissions=self._skip_permissions,
            ask_user_question_enabled=self._ask_user_question_enabled,
        )
        settings.session.id = self._token
        settings.session.workspace_dir = str(workspace_dir)

        self.broker = Broker(settings=settings)
        # Minimal manual wiring — no startup(): hand the transport to the broker
        # and route its events into the broker's CLI pipeline.
        self.broker._transport = self.transport
        self.transport.on_event(self.broker._handle_cli_event)

        if self._start_transport:
            await self.transport.start()
        self._started = True

    async def ensure_transport(self) -> None:
        """Start the transport directly (for transport-driven tests with no client)."""
        if self.transport is None:
            raise RuntimeError("BrokerHarness not started")
        if not self.transport.is_alive:
            await self.transport.start()

    async def _stop(self) -> None:
        for client in self._clients:
            await self._cancel_client(client)
        self._clients.clear()

        if self.transport is not None:
            with contextlib.suppress(Exception):
                await self.transport.stop()
            self.transport = None

        if self.hook_server is not None:
            with contextlib.suppress(Exception):
                await self.hook_server.stop()
            self.hook_server = None

        self._restore_env()
        self.broker = None
        self._started = False

    # ------------------------------------------------------------------- hooks

    async def _hook_handler(self, payload: dict[str, Any]) -> Any:
        if self.broker is None:
            return None
        await self.broker.handle_claude_hook(payload)
        return None

    # ----------------------------------------------------------------- clients

    async def connect(self, *, timeout: float = 3.0) -> FakeWsClient:
        """Open a fake browser WebSocket and wait for the initial handshake."""
        if self.broker is None:
            raise RuntimeError("BrokerHarness not started")
        ws = FakeWebSocket()
        task = asyncio.create_task(self.broker.handle_websocket(ws))
        client = FakeWsClient(ws, task)
        self._clients.append(client)
        # The handshake sends system(connected) then a capabilities frame; wait
        # for capabilities so the client is fully attached before returning.
        await client.wait_for(
            lambda frames: any(f.get("type") == "capabilities" for f in frames),
            timeout=timeout,
        )
        return client

    async def drop(self, client: FakeWsClient) -> None:
        """Cleanly close a client — close() returns normally; the handler unwinds."""
        client.ws.closed = True
        client.ws.kill()
        await self._await_client(client)
        if client in self._clients:
            self._clients.remove(client)

    async def kill(self, client: FakeWsClient) -> None:
        """Abruptly crash a client — the next receive raises WebSocketDisconnect."""
        client.ws.kill()
        await self._await_client(client)
        if client in self._clients:
            self._clients.remove(client)

    async def _await_client(self, client: FakeWsClient, timeout: float = 3.0) -> None:
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(client.task), timeout=timeout)

    async def _cancel_client(self, client: FakeWsClient) -> None:
        if client.task.done():
            return
        client.ws.kill()
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(client.task), timeout=2.0)
        if not client.task.done():
            client.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await client.task

    # -------------------------------------------------------------- durable log

    @property
    def event_log(self) -> list[dict[str, Any]]:
        """The durable event-log buffer the broker accumulated (newest last)."""
        if self.broker is None:
            return []
        return list(self.broker._event_log_buffer)

    def event_log_entries(self) -> list[Any]:
        """The buffer mapped to ``SessionLogEntry`` for transcript_rebuild."""
        from volundr.domain.models import SessionLogEntry

        session_uuid = self.broker._trace_id if self.broker is not None else uuid.uuid4()
        entries: list[Any] = []
        for raw in self.event_log:
            ts = raw.get("ts")
            entries.append(
                SessionLogEntry(
                    session_id=session_uuid,
                    seq=int(raw.get("seq", 0)),
                    kind=str(raw.get("kind", "unknown")),
                    payload=raw.get("payload", {}),
                    ts=_parse_ts(ts),
                    role=raw.get("role"),
                    request_id=raw.get("request_id"),
                )
            )
        return entries

    # ----------------------------------------------------------------- agent ops

    def crash_agent(self) -> None:
        """SIGKILL the fakeagent process running inside tmux for THIS harness.

        fakeagent is launched with FORGE_FAKEAGENT_SESSION=<token>; we match on
        that env value via the process command line so only our agent dies.
        """
        target = self._token
        # The shim execs `python fakeagent.py`; match the fakeagent script path
        # AND our unique token in the environment to avoid collateral kills.
        result = subprocess.run(
            ["pgrep", "-f", "fakeagent.py"],
            capture_output=True,
            text=True,
            check=False,
        )
        killed = False
        for pid_str in result.stdout.split():
            pid = int(pid_str)
            if not self._proc_has_token(pid, target):
                continue
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(pid, signal.SIGKILL)
                killed = True
        if not killed:
            raise AssertionError(f"no fakeagent process found for token {target}")

    @staticmethod
    def _proc_has_token(pid: int, token: str) -> bool:
        try:
            environ = Path(f"/proc/{pid}/environ").read_bytes()
        except (OSError, ValueError):
            return False
        return f"FORGE_FAKEAGENT_SESSION={token}".encode() in environ

    # ------------------------------------------------------------------- env mgmt

    def _apply_env(self, mutations: dict[str, str]) -> None:
        for key, value in mutations.items():
            self._old_env.setdefault(key, os.environ.get(key))
            os.environ[key] = value

    def _restore_env(self) -> None:
        for key, value in self._old_env.items():
            if value is None:
                os.environ.pop(key, None)
                continue
            os.environ[key] = value
        self._old_env.clear()

    def _make_tmp(self, suffix: str) -> str:
        import tempfile

        return tempfile.mkdtemp(prefix=f"{self._token}-{suffix}-")


def _parse_ts(value: Any):
    from datetime import UTC, datetime

    if isinstance(value, str) and value:
        with contextlib.suppress(ValueError):
            return datetime.fromisoformat(value)
    return datetime.now(UTC)
