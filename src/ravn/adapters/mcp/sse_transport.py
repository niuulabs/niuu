"""SSE and HTTP MCP transports.

SSETransport — connects to a server-sent event endpoint.  The server sends
an ``endpoint`` event containing the URL to POST messages to; responses
arrive as ``message`` events on the SSE stream.

HTTPTransport — simpler request/response: POST JSON-RPC, receive JSON reply.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from typing import Any

from ravn.adapters.mcp.transport import MCPTransport, MCPTransportError

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT_SECONDS = 10.0
_REQUEST_TIMEOUT_SECONDS = 30.0
_RECEIVE_QUEUE_SIZE = 256


class HTTPTransport(MCPTransport):
    """Simple HTTP transport: each JSON-RPC call is a POST → response pair.

    Uses httpx for async HTTP; httpx is available in the project via the
    web_fetch tool dependency.  Falls back gracefully if unavailable.
    """

    def __init__(self, url: str, *, timeout: float = _REQUEST_TIMEOUT_SECONDS) -> None:
        self._url = url
        self._timeout = timeout
        self._client: Any = None
        self._started = False
        self._auth_headers: dict[str, str] = {}

    def set_auth_headers(self, headers: dict[str, str]) -> None:
        self._auth_headers = dict(headers)

    async def start(self) -> None:
        try:
            import httpx

            self._client = httpx.AsyncClient(timeout=self._timeout)
            self._started = True
        except ImportError as exc:
            raise MCPTransportError("httpx is required for HTTP transport") from exc

    async def send(self, message: dict[str, Any]) -> None:
        if not self._started:
            raise MCPTransportError("Transport not started")
        self._pending_message = message

    async def receive(self) -> dict[str, Any]:
        if not self._started:
            raise MCPTransportError("Transport not started")
        message = getattr(self, "_pending_message", None)
        if message is None:
            raise MCPTransportError("No pending message to send")
        self._pending_message = None

        try:
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                **self._auth_headers,
            }
            response = await self._client.post(
                self._url,
                json=message,
                headers=headers,
            )
            response.raise_for_status()

            content_type = response.headers.get("content-type", "")

            # Streamable HTTP servers may return SSE even on POST.
            if "text/event-stream" in content_type:
                return self._parse_sse_response(response.text)

            return response.json()  # type: ignore[return-value]
        except Exception as exc:
            raise MCPTransportError(f"HTTP request failed: {exc}") from exc

    @staticmethod
    def _parse_sse_response(text: str) -> dict[str, Any]:
        """Extract the first JSON-RPC result from an SSE response body."""
        for line in text.splitlines():
            if line.startswith("data:"):
                data = line[len("data:") :].strip()
                if data:
                    return json.loads(data)
        raise MCPTransportError("No data event found in SSE response")

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception as exc:
                logger.debug("Error closing HTTP transport: %s", exc)
            self._client = None
        self._started = False

    @property
    def is_alive(self) -> bool:
        return self._started and self._client is not None


class SSETransport(MCPTransport):
    """Server-Sent Events MCP transport.

    Protocol:
    1. Connect to ``url`` as an SSE stream.
    2. Wait for the server to emit an ``endpoint`` event with a POST URL.
    3. POST JSON-RPC messages to that endpoint.
    4. Receive responses as ``message`` events on the SSE stream.
    """

    def __init__(
        self,
        url: str,
        *,
        timeout: float = _REQUEST_TIMEOUT_SECONDS,
        connect_timeout: float = _CONNECT_TIMEOUT_SECONDS,
    ) -> None:
        self._url = url
        self._timeout = timeout
        self._connect_timeout = connect_timeout
        self._post_url: str | None = None
        self._response_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=_RECEIVE_QUEUE_SIZE
        )
        self._client: Any = None
        self._sse_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._started = False
        self._auth_headers: dict[str, str] = {}

    def set_auth_headers(self, headers: dict[str, str]) -> None:
        self._auth_headers = dict(headers)

    async def start(self) -> None:
        try:
            import httpx

            self._client = httpx.AsyncClient(timeout=None)
        except ImportError as exc:
            raise MCPTransportError("httpx is required for SSE transport") from exc

        # Start the SSE reader in the background and wait for the endpoint event.
        endpoint_ready: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._sse_task = asyncio.create_task(
            self._read_sse(endpoint_ready),
            name="mcp-sse-reader",
        )

        try:
            self._post_url = await asyncio.wait_for(
                endpoint_ready,
                timeout=self._connect_timeout,
            )
        except TimeoutError:
            self._sse_task.cancel()
            raise MCPTransportError("Timed out waiting for SSE endpoint event")

        self._started = True

    async def _read_sse(self, endpoint_ready: asyncio.Future[str]) -> None:
        """Background task: read SSE stream, route events to queues."""
        event_type = "message"
        buffer: list[str] = []

        try:
            async with self._client.stream(
                "GET", self._url, headers=self._auth_headers
            ) as response:
                async for line in response.aiter_lines():
                    if line.startswith("event:"):
                        event_type = line[len("event:") :].strip()
                        continue
                    if line.startswith("data:"):
                        buffer.append(line[len("data:") :].strip())
                        continue
                    if line == "":
                        # End of event block — dispatch.
                        data = "\n".join(buffer)
                        buffer.clear()

                        if event_type == "endpoint":
                            if not endpoint_ready.done():
                                endpoint_ready.set_result(data.strip())
                        elif event_type == "message" and data:
                            try:
                                parsed = json.loads(data)
                                await self._response_queue.put(parsed)
                            except (ValueError, asyncio.QueueFull):
                                logger.warning("Dropped SSE message (parse error or queue full)")

                        event_type = "message"
        except asyncio.CancelledError:
            return
        except Exception as exc:
            if not endpoint_ready.done():
                endpoint_ready.set_exception(MCPTransportError(f"SSE stream error: {exc}"))
            logger.debug("SSE stream ended: %s", exc)

    async def send(self, message: dict[str, Any]) -> None:
        if not self._started or self._post_url is None:
            raise MCPTransportError("SSE transport not started")
        try:
            headers = {"Content-Type": "application/json", **self._auth_headers}
            response = await self._client.post(
                self._post_url,
                json=message,
                headers=headers,
                timeout=self._timeout,
            )
            response.raise_for_status()
        except Exception as exc:
            raise MCPTransportError(f"SSE POST failed: {exc}") from exc

    async def receive(self) -> dict[str, Any]:
        if not self._started:
            raise MCPTransportError("SSE transport not started")
        try:
            return await asyncio.wait_for(
                self._response_queue.get(),
                timeout=self._timeout,
            )
        except TimeoutError:
            raise MCPTransportError("Timed out waiting for SSE response")

    async def close(self) -> None:
        if self._sse_task is not None:
            self._sse_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self._sse_task
            self._sse_task = None

        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception as exc:
                logger.debug("Error closing SSE client: %s", exc)
            self._client = None

        self._started = False

    @property
    def is_alive(self) -> bool:
        return self._started and self._sse_task is not None and not self._sse_task.done()
