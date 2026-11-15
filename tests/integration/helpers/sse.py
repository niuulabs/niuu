"""Shared SSE test utilities for integration tests.

Provides helpers for spinning up a real uvicorn server, connecting to SSE
endpoints via ``httpx.AsyncClient.stream()``, and parsing the SSE wire format.

httpx's ``ASGITransport`` buffers the entire response body before returning,
which deadlocks with infinite SSE generators — hence the real HTTP server.
"""

from __future__ import annotations

import asyncio
import socket

import httpx
import uvicorn

# Timeout for SSE event collection (seconds)
SSE_TIMEOUT = 5

# How often ``wait_for_subscriber`` re-reads the broadcaster (seconds)
SUBSCRIBER_POLL_INTERVAL = 0.01


def parse_sse_events(raw: str) -> list[dict[str, str]]:
    """Parse raw SSE text into a list of field dicts.

    Each SSE message is separated by a blank line (``\\n\\n``).
    Within a message, lines are ``field: value``.
    SSE comment lines (starting with ``:``) are skipped.
    """
    events: list[dict[str, str]] = []
    for block in raw.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        # Skip SSE comment lines (e.g. keepalive pings)
        if block.startswith(":"):
            continue
        fields: dict[str, str] = {}
        for line in block.split("\n"):
            if ": " in line:
                key, value = line.split(": ", 1)
                fields[key] = value
        if fields:
            events.append(fields)
    return events


async def wait_for_subscriber(broadcaster: object, count: int = 1) -> None:
    """Wait until *broadcaster* has *count* live subscribers.

    The server sends the SSE response headers before the stream handler
    registers its subscription, so a client that has the headers cannot tell
    whether it is subscribed yet. The broadcaster does not replay, so an event
    published before the subscription is lost for good. Publishing must wait
    for this, not for a guessed delay.

    Raises ``TimeoutError`` after ``SSE_TIMEOUT`` seconds.
    """

    async def _poll() -> None:
        while broadcaster.subscriber_count < count:  # type: ignore[attr-defined]
            await asyncio.sleep(SUBSCRIBER_POLL_INTERVAL)

    try:
        await asyncio.wait_for(_poll(), timeout=SSE_TIMEOUT)
    except TimeoutError as exc:
        raise TimeoutError(
            f"no SSE subscriber registered within {SSE_TIMEOUT}s: the stream handler "
            "never subscribed to the broadcaster after the response started"
        ) from exc


def free_port() -> int:
    """Find an available TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def collect_sse(
    base_url: str,
    path: str,
    n: int = 1,
    headers: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    """Connect to a real HTTP SSE endpoint and collect *n* events."""
    collected: list[dict[str, str]] = []

    async with httpx.AsyncClient() as client:
        async with client.stream(
            "GET",
            f"{base_url}{path}",
            headers=headers or {},
            timeout=SSE_TIMEOUT,
        ) as response:
            buffer = ""
            async for chunk in response.aiter_text():
                buffer += chunk
                while "\n\n" in buffer:
                    raw, buffer = buffer.split("\n\n", 1)
                    parsed = parse_sse_events(raw + "\n\n")
                    collected.extend(parsed)
                    if len(collected) >= n:
                        return collected
    return collected


async def start_server(app: object) -> tuple[uvicorn.Server, str]:
    """Start a uvicorn server and return ``(server, base_url)``.

    Raises ``RuntimeError`` if the server fails to start within 5 seconds.
    """
    port = free_port()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        ws="none",  # This server exercises HTTP/SSE only; no legacy WS protocol imports.
    )
    server = uvicorn.Server(config)
    asyncio.create_task(server.serve())

    # Wait for server to start
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.05)

    if not server.started:
        raise RuntimeError("uvicorn server failed to start within 5s")

    return server, f"http://127.0.0.1:{port}"
