"""Reach the session broker's ``present_file`` RPC from a CLI agent's tool server.

A flock member that runs on a CLI transport (Codex, Claude Code) gets its Ravn tools
from a separate ``ravn tool-mcp`` process. That process cannot join the flock mesh:
the member's mesh sockets are bound by its daemon, and the broker trusts only the
peers its static peer list names. So the daemon keeps the one mesh connection and
serves the broker's ``present_file`` RPC to its own children over a Unix socket in a
private directory (mode 0700, so only the daemon's user can connect).

:class:`PresentFileRelayServer` runs in the daemon and forwards a ``present_file``
request to the configured broker, nothing else. :class:`PresentFileRelayClient` is
the ``send`` side that :class:`~ravn.adapters.tools.present_file.PresentFileTool`
uses in the tool server, so the tool validates and writes the document exactly as it
does in the daemon. An unreachable relay raises :class:`PresentFileRelayError`; it is
never a missing tool or a quiet empty answer.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from niuu.domain.presented_files import PRESENT_FILE_RPC_TYPE
from ravn.cap_telemetry import record_cap_hit

if TYPE_CHECKING:
    from niuu.ports.mesh import MeshPort

logger = logging.getLogger(__name__)

_SOCKET_NAME = "present-file.sock"


class PresentFileRelayError(RuntimeError):
    """The daemon's present-file relay could not be reached or answered malformed."""


class PresentFileRelayServer:
    """Forward ``present_file`` requests from the daemon's CLI children to the broker."""

    def __init__(self, mesh: MeshPort, *, broker_peer_id: str, timeout_s: float) -> None:
        self._mesh = mesh
        self._broker_peer_id = broker_peer_id
        self._timeout_s = timeout_s
        self._directory: Path | None = None
        self._server: asyncio.AbstractServer | None = None

    @property
    def socket_path(self) -> str:
        """The socket a tool server connects to; raises before :meth:`start`."""
        if self._directory is None or self._server is None:
            raise PresentFileRelayError(
                "the present-file relay is not running; the daemon starts it with the "
                "flock mesh before any CLI agent is built"
            )
        return str(self._directory / _SOCKET_NAME)

    async def start(self) -> None:
        # mkdtemp creates the directory 0700: the socket is the daemon user's alone.
        # Under /tmp where it exists, as niuu.mesh.ipc does: a macOS TMPDIR can push a
        # socket path past the kernel's limit for Unix socket addresses.
        short_tmp = Path("/tmp")
        self._directory = Path(
            tempfile.mkdtemp(
                prefix="ravn-present-", dir=str(short_tmp) if short_tmp.is_dir() else None
            )
        )
        self._server = await asyncio.start_unix_server(
            self._serve, path=str(self._directory / _SOCKET_NAME)
        )
        logger.info("present_file relay: serving on %s", self.socket_path)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._directory is not None:
            shutil.rmtree(self._directory, ignore_errors=True)
            self._directory = None

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await reader.readline()
            reply = await self._answer(line)
            writer.write(json.dumps(reply).encode("utf-8") + b"\n")
            await writer.drain()
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()

    async def _answer(self, line: bytes) -> dict[str, Any]:
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            return _refused("the relay request is not JSON")
        if not isinstance(request, dict):
            return _refused("the relay request is not a JSON object")
        message = request.get("message")
        if not isinstance(message, dict) or message.get("type") != PRESENT_FILE_RPC_TYPE:
            return _refused(f"the relay forwards only {PRESENT_FILE_RPC_TYPE!r} requests")
        if request.get("target") != self._broker_peer_id:
            return _refused(
                f"the relay forwards only to the session broker {self._broker_peer_id!r}, "
                f"not {request.get('target')!r}"
            )
        try:
            return await self._mesh.send(self._broker_peer_id, message, timeout_s=self._timeout_s)
        except TimeoutError:
            # Counted here, in the daemon, whose health snapshot carries cap hits.
            problem = (
                f"The session broker did not answer within present_file.timeout_s="
                f"{self._timeout_s:g}s; the file may still appear. Raise "
                "present_file.timeout_s if presenting regularly takes longer."
            )
            record_cap_hit("present_file.timeout_s", problem)
            return {"status": "error", "error": problem}
        except Exception as exc:  # noqa: BLE001 - answered to the agent and logged at error
            logger.error("present_file relay: the broker request failed", exc_info=True)
            return {"status": "error", "error": f"the broker request failed: {exc}"}


class PresentFileRelayClient:
    """The mesh ``send`` a tool server's ``PresentFileTool`` uses, through the relay."""

    def __init__(self, socket_path: str) -> None:
        self._socket_path = socket_path

    def require_reachable(self) -> None:
        """Raise unless the relay socket exists, so a broken relay fails at startup."""
        if not Path(self._socket_path).is_socket():
            raise PresentFileRelayError(
                f"present_file is offered through the daemon's relay at {self._socket_path}, "
                "but no socket is there; the Ravn daemon serves it while it runs"
            )

    async def send(
        self,
        target_peer_id: str,
        message: dict,
        *,
        timeout_s: float | None = None,
    ) -> dict:
        # The daemon bounds the broker call by present_file.timeout_s and answers
        # with the outcome; waiting here for that answer adds no second limit.
        del timeout_s
        try:
            reader, writer = await asyncio.open_unix_connection(self._socket_path)
        except OSError as exc:
            raise PresentFileRelayError(
                f"cannot reach the daemon's present-file relay at {self._socket_path}: {exc}"
            ) from exc
        try:
            request = {"target": target_peer_id, "message": message}
            writer.write(json.dumps(request).encode("utf-8") + b"\n")
            await writer.drain()
            line = await reader.readline()
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()
        if not line:
            raise PresentFileRelayError(
                "the daemon's present-file relay closed without answering; check the "
                "Ravn daemon's log"
            )
        try:
            reply = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PresentFileRelayError("the present-file relay answered malformed JSON") from exc
        if not isinstance(reply, dict):
            raise PresentFileRelayError("the present-file relay answered a non-object")
        return reply


def _refused(problem: str) -> dict[str, Any]:
    return {"status": "error", "error": problem}
