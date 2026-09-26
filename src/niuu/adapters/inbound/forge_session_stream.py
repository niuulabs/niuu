"""Independent, cancellable SSE readers for the Guild session view."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any

import httpx

logger = logging.getLogger(__name__)
Event = tuple[str, dict[str, Any]]
Source = Callable[[], AsyncIterator[Event]]


async def remote_events(
    client: httpx.AsyncClient,
    url: str,
    headers: Mapping[str, str],
) -> AsyncIterator[Event]:
    """Decode complete SSE records (including multi-line data) from one host.

    *client* is the caller's, built (and closed) by the caller — for a
    genuinely remote Guild instance that must be
    ``niuu.adapters.outbound.guild_transport.build_guild_httpx_client()``, so
    the same transport-policy enforcement, TLS pinning, and ``trust_env``
    handling applies to the session stream as to every other outbound Guild
    call; this function only ever reads from it.
    """
    async with client:
        async with client.stream("GET", url, headers=headers) as response:
            response.raise_for_status()
            name, data = "message", []
            async for line in response.aiter_lines():
                if not line:
                    if data:
                        try:
                            payload = json.loads("\n".join(data))
                        except ValueError:
                            logger.warning("Ignoring malformed Forge SSE record")
                        else:
                            if isinstance(payload, dict):
                                yield name, payload
                    name, data = "message", []
                elif line.startswith("event:"):
                    name = line[6:].lstrip(" ")
                elif line.startswith("data:"):
                    data.append(line[5:].lstrip(" "))


async def merge_events(
    sources: Mapping[str, Source],
    *,
    retry_seconds: float = 5,
    keepalive_seconds: float = 15,
    queue_maxsize: int = 256,
) -> AsyncIterator[bytes]:
    """A missing host cannot stall others; disconnect cancels every reader."""
    queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=queue_maxsize)

    async def read(host: str, source: Source) -> None:
        while True:
            try:
                async for event in source():
                    await queue.put(event)
            except Exception:
                logger.warning("Forge session stream disconnected: %s", host, exc_info=True)
            await queue.put(("forge_stream_status", {"instance_id": host, "state": "reconnecting"}))
            await asyncio.sleep(retry_seconds)

    tasks = [asyncio.create_task(read(host, source)) for host, source in sources.items()]
    try:
        while True:
            try:
                name, data = await asyncio.wait_for(queue.get(), timeout=keepalive_seconds)
                yield f"event: {name}\ndata: {json.dumps(data)}\n\n".encode()
            except TimeoutError:
                yield b": keepalive\n\n"
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
