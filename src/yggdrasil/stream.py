"""SSE frame serialization and stream iteration."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator

from fastapi import Request

from yggdrasil.connection_manager import ConnectionManager
from yggdrasil.models import RealmSummary

REALM_SUMMARY_EVENT = "realm.summary"


def format_sse(summary: RealmSummary) -> str:
    """Serialize a realm summary as a named SSE frame."""
    payload = json.dumps(summary.model_dump(mode="json"), sort_keys=True)
    return f"event: {REALM_SUMMARY_EVENT}\ndata: {payload}\n\n"


async def stream_realm_summary(
    request: Request,
    realm_id: str,
    connection_manager: ConnectionManager,
    initial_summary: RealmSummary,
    queue: asyncio.Queue[RealmSummary],
    disconnect_check_interval: float,
) -> AsyncGenerator[str, None]:
    """Yield an initial summary followed by changed summaries until disconnect."""
    try:
        yield format_sse(initial_summary)

        while not await request.is_disconnected():
            try:
                summary = await asyncio.wait_for(queue.get(), disconnect_check_interval)
            except TimeoutError:
                continue
            yield format_sse(summary)
    finally:
        connection_manager.disconnect(realm_id, queue)
