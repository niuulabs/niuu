"""The guest-local HTTP endpoint: ``/niuu/v1/credentials`` and ``/niuu/v1/health``."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging

from volundr.domain.guest_credentials import METADATA_HEADER
from volundr.guest_agent.agent import CredentialState
from volundr.guest_agent.config import Listen

logger = logging.getLogger("volundr.guest_agent")

CREDENTIALS_ROUTE = "/niuu/v1/credentials"
HEALTH_ROUTE = "/niuu/v1/health"

_REASONS = {
    200: "OK",
    400: "Bad Request",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    431: "Request Header Fields Too Large",
    503: "Service Unavailable",
}


class MetadataServer:
    """Serves the last successful credentials response to the guest's own processes.

    The ``Niuu-Metadata: true`` header is required so that a request forwarded
    by a confused deputy (SSRF) cannot read credentials: a browser or proxy
    will not add a custom header on its own.
    """

    def __init__(
        self, state: CredentialState, listen: Listen, *, request_timeout_seconds: float
    ) -> None:
        self._state = state
        self._listen = listen
        self._timeout = request_timeout_seconds
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle, host=self._listen.address, port=self._listen.port
        )

    @property
    def port(self) -> int:
        if self._server is None:
            raise RuntimeError("the metadata server is not started")
        return self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            status, body = await asyncio.wait_for(self._respond(reader), self._timeout)
            await self._send(writer, status, body)
        except (TimeoutError, ConnectionError):
            logger.warning("metadata request dropped (timeout or connection reset)")
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()

    async def _respond(self, reader: asyncio.StreamReader) -> tuple[int, bytes]:
        try:
            request_line = await reader.readuntil(b"\r\n")
            headers = await _read_headers(reader)
        except (asyncio.LimitOverrunError, asyncio.IncompleteReadError):
            return 431, _json({"error": "request too large or incomplete"})
        parts = request_line.decode("latin-1").split()
        if len(parts) != 3:
            return 400, _json({"error": "malformed request line"})
        method, target, _version = parts
        path = target.split("?", 1)[0]
        if path not in (CREDENTIALS_ROUTE, HEALTH_ROUTE):
            return 404, _json({"error": "not found"})
        if method != "GET":
            return 405, _json({"error": "method not allowed"})
        if path == HEALTH_ROUTE:
            return self._health()
        if headers.get(METADATA_HEADER.lower(), "").strip().lower() != "true":
            return 403, _json({"error": f"the {METADATA_HEADER}: true header is required"})
        raw = self._state.raw
        if raw is None:
            return 503, _json({"error": "no credentials fetched yet"})
        return 200, raw

    def _health(self) -> tuple[int, bytes]:
        healthy, reason = self._state.health()
        if healthy:
            return 200, _json({"status": "ok"})
        return 503, _json({"status": "unavailable", "reason": reason})

    async def _send(self, writer: asyncio.StreamWriter, status: int, body: bytes) -> None:
        head = (
            f"HTTP/1.1 {status} {_REASONS[status]}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Cache-Control: no-store\r\n"
            "Connection: close\r\n\r\n"
        )
        writer.write(head.encode("latin-1") + body)
        await writer.drain()


async def _read_headers(reader: asyncio.StreamReader) -> dict[str, str]:
    headers: dict[str, str] = {}
    while True:
        line = await reader.readuntil(b"\r\n")
        if line == b"\r\n":
            return headers
        name, separator, value = line.decode("latin-1").partition(":")
        if separator:
            headers[name.strip().lower()] = value.strip()


def _json(document: dict) -> bytes:
    return json.dumps(document).encode()
