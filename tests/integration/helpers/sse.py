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
