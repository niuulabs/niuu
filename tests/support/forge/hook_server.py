"""HookServer — a minimal asyncio HTTP/1.1 server for Claude hook POSTs.

This is the seam that lets ``fakeagent``'s hook POSTs reach a handler in the
test: either ``transport.handle_claude_hook`` (component tier) or
``broker.handle_claude_hook`` (broker tier). It only understands what Claude's
HTTP hook transport needs: ``POST /api/claude/hooks`` with a JSON body and a
``{"continue": true}`` reply.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

_HOOK_PATH = "/api/claude/hooks"
_MAX_HEADER_BYTES = 64 * 1024

Handler = Callable[[dict[str, Any]], Awaitable[Any]]


class HookServer:
    """One-request-per-connection HTTP server bound to 127.0.0.1."""

    def __init__(self, handler: Handler, port: int = 0, host: str = "127.0.0.1") -> None:
        self._handler = handler
        self._host = host
        self._requested_port = port
        self._server: asyncio.AbstractServer | None = None
        self._port = port

    @property
    def port(self) -> int:
        return self._port

    async def start(self) -> HookServer:
        self._server = await asyncio.start_server(
            self._handle_connection, self._host, self._requested_port
        )
        sock = self._server.sockets[0]
        self._port = sock.getsockname()[1]
        return self

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    async def __aenter__(self) -> HookServer:
        return await self.start()

    async def __aexit__(self, *_exc: object) -> None:
        await self.stop()

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            await self._serve_one(reader, writer)
        except (ConnectionError, asyncio.IncompleteReadError):
            return
        finally:
            with suppress(Exception):
                writer.close()

    async def _serve_one(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request_line = await reader.readline()
        if not request_line:
            return
        parts = request_line.decode("latin-1").split()
        method = parts[0] if parts else ""
        path = parts[1] if len(parts) > 1 else ""

        content_length = 0
        header_bytes = 0
        while True:
            line = await reader.readline()
            header_bytes += len(line)
            if header_bytes > _MAX_HEADER_BYTES:
                await self._write_response(writer, 400, {"error": "headers too large"})
                return
            if line in (b"\r\n", b"\n", b""):
                break
            name, _, value = line.decode("latin-1").partition(":")
            if name.strip().lower() == "content-length":
                content_length = int(value.strip() or "0")

        body = await reader.readexactly(content_length) if content_length else b""

        if method != "POST" or path != _HOOK_PATH:
            await self._write_response(writer, 404, {"error": "not found"})
            return

        payload = self._parse_json(body)
        await self._handler(payload)
        await self._write_response(writer, 200, {"continue": True})

    @staticmethod
    def _parse_json(body: bytes) -> dict[str, Any]:
        if not body:
            return {}
        try:
            data = json.loads(body.decode("utf-8"))
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    async def _write_response(
        self, writer: asyncio.StreamWriter, status: int, payload: dict[str, Any]
    ) -> None:
        body = json.dumps(payload).encode("utf-8")
        reason = {200: "OK", 400: "Bad Request", 404: "Not Found"}.get(status, "OK")
        head = (
            f"HTTP/1.1 {status} {reason}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("latin-1")
        writer.write(head + body)
        with suppress(Exception):
            await writer.drain()
