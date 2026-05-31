"""Integration tests for Volundr SSE event streaming.

Verifies that ``GET /api/v1/forge/sessions/stream`` delivers real-time
Server-Sent Events when sessions are created or stats are broadcast.

Uses a real HTTP server (uvicorn) because httpx's ``ASGITransport``
buffers the entire response body before returning, which deadlocks with
infinite SSE generators.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from tests.integration.helpers.sse import SSE_TIMEOUT, collect_sse, start_server
from volundr.domain.models import EventType, RealtimeEvent

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
]

API = "/api/v1/forge"
SSE_URL = f"{API}/sessions/stream"


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


async def test_sse_stream_connects(volundr_app: object) -> None:
    """GET /sessions/stream returns 200 with SSE content-type headers.

    Publishes a heartbeat so the stream has at least one event to yield,
    then verifies response headers and that data is received.
    """
    broadcaster = volundr_app.state.broadcaster  # type: ignore[union-attr]
    server, base_url = await start_server(volundr_app)

    try:

        async def _publish_heartbeat() -> None:
            await asyncio.sleep(0.3)
            await broadcaster.publish_heartbeat()

        publish_task = asyncio.create_task(_publish_heartbeat())

        events = await asyncio.wait_for(
            collect_sse(base_url, SSE_URL, n=1),
            timeout=SSE_TIMEOUT,
        )
        _ = await publish_task

        assert len(events) >= 1
        assert events[0]["event"] == EventType.HEARTBEAT.value
    finally:
        server.should_exit = True
        await asyncio.sleep(0.1)


async def test_sse_receives_session_created_event(
    volundr_app: object,
    auth_headers: object,
) -> None:
    """Connect SSE, create a session via POST, verify SESSION_CREATED event."""
    headers = auth_headers("sse-user", "sse@test.com", "default", ["volundr:admin"])  # type: ignore[operator]
    server, base_url = await start_server(volundr_app)

    try:

        async def _create_session() -> None:
            await asyncio.sleep(0.3)
            payload = {
                "name": "sse-test-session",
                "model": "claude-sonnet-4-6",
                "source": {
                    "type": "git",
                    "repo": "github.com/acme/demo",
                    "branch": "main",
                },
            }
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{base_url}{API}/sessions",
                    json=payload,
                    headers=headers,
                )
                assert resp.status_code == 201, resp.text

        create_task = asyncio.create_task(_create_session())

        events = await asyncio.wait_for(
            collect_sse(base_url, SSE_URL, n=1),
            timeout=SSE_TIMEOUT,
        )
        _ = await create_task

        assert len(events) >= 1
        session_events = [e for e in events if e["event"] == EventType.SESSION_CREATED.value]
        assert len(session_events) >= 1

        data = json.loads(session_events[0]["data"])
        assert data["name"] == "sse-test-session"
        assert "id" in data
    finally:
        server.should_exit = True
        await asyncio.sleep(0.1)


async def test_sse_receives_stats_update(volundr_app: object) -> None:
    """Publish a stats event through the broadcaster, verify SSE delivers it."""
    from datetime import UTC, datetime

    broadcaster = volundr_app.state.broadcaster  # type: ignore[union-attr]
    server, base_url = await start_server(volundr_app)

    try:
        stats_event = RealtimeEvent(
            type=EventType.STATS_UPDATED,
            data={
                "active_sessions": 3,
                "total_sessions": 10,
                "tokens_today": 5000,
                "local_tokens": 1000,
                "cloud_tokens": 4000,
                "cost_today": 1.25,
            },
            timestamp=datetime.now(UTC),
        )

        async def _publish_stats() -> None:
            await asyncio.sleep(0.3)
            await broadcaster.publish(stats_event)

        publish_task = asyncio.create_task(_publish_stats())

        events = await asyncio.wait_for(
            collect_sse(base_url, SSE_URL, n=1),
            timeout=SSE_TIMEOUT,
        )
        _ = await publish_task

        assert len(events) >= 1
        stats_events = [e for e in events if e["event"] == EventType.STATS_UPDATED.value]
        assert len(stats_events) >= 1

        data = json.loads(stats_events[0]["data"])
        assert data["tokens_today"] == 5000
        assert data["active_sessions"] == 3
        assert data["cost_today"] == 1.25
    finally:
        server.should_exit = True
        await asyncio.sleep(0.1)
