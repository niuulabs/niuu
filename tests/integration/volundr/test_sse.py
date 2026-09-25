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


async def test_sse_stream_connects(volundr_app: object, auth_headers: object) -> None:
    """GET /sessions/stream returns 200 with SSE content-type headers.

    Publishes a heartbeat so the stream has at least one event to yield,
    then verifies response headers and that data is received.
    """
    headers = auth_headers("sse-user", "sse@test.com", "default")  # type: ignore[operator]
    broadcaster = volundr_app.state.broadcaster  # type: ignore[union-attr]
    server, base_url = await start_server(volundr_app)

    try:

        async def _publish_heartbeat() -> None:
            await asyncio.sleep(0.3)
            await broadcaster.publish_heartbeat()

        publish_task = asyncio.create_task(_publish_heartbeat())

        events = await asyncio.wait_for(
            collect_sse(base_url, SSE_URL, n=1, headers=headers),
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
            collect_sse(base_url, SSE_URL, n=1, headers=headers),
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


async def test_sse_receives_stats_update(
    volundr_app: object, volundr_client: object, auth_headers: object, txn_pool: object
) -> None:
    """A stats tick reaches the subscriber as figures over its own sessions."""
    from datetime import UTC, datetime
    from uuid import UUID, uuid4

    headers = auth_headers("sse-user", "sse@test.com", "default")  # type: ignore[operator]
    created = await volundr_client.post(  # type: ignore[union-attr]
        f"{API}/sessions",
        json={
            "name": "sse-stats-session",
            "model": "claude-sonnet-4-6",
            "source": {"type": "git", "repo": "github.com/acme/demo", "branch": "main"},
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    await txn_pool.execute(  # type: ignore[union-attr]
        "INSERT INTO token_usage (id, session_id, tokens, provider, model, cost) "
        "VALUES ($1, $2, 1234, 'cloud', 'claude-sonnet-4-6', 0)",
        uuid4(),
        UUID(created.json()["id"]),
    )
    broadcaster = volundr_app.state.broadcaster  # type: ignore[union-attr]
    server, base_url = await start_server(volundr_app)

    try:
        # A publisher's own figures are never what a subscriber receives.
        stats_event = RealtimeEvent(
            type=EventType.STATS_UPDATED,
            data={"active_sessions": 3, "total_sessions": 10, "tokens_today": 5000},
            timestamp=datetime.now(UTC),
        )

        async def _publish_stats() -> None:
            await asyncio.sleep(0.3)
            await broadcaster.publish(stats_event)

        publish_task = asyncio.create_task(_publish_stats())

        events = await asyncio.wait_for(
            collect_sse(base_url, SSE_URL, n=1, headers=headers),
            timeout=SSE_TIMEOUT,
        )
        _ = await publish_task

        assert len(events) >= 1
        stats_events = [e for e in events if e["event"] == EventType.STATS_UPDATED.value]
        assert len(stats_events) >= 1

        data = json.loads(stats_events[0]["data"])
        assert data["tokens_today"] == 1234
        assert data["cloud_tokens"] == 1234
        assert data["total_sessions"] == 1
        assert set(data) == {
            "active_sessions",
            "total_sessions",
            "sessions_today",
            "tokens_today",
            "local_tokens",
            "cloud_tokens",
            "cost_today",
            "sparklines",
        }
    finally:
        server.should_exit = True
        await asyncio.sleep(0.1)


async def test_sse_rejects_an_unauthenticated_subscriber(volundr_app: object) -> None:
    """The stream is part of the session data plane: no identity, no events."""
    server, base_url = await start_server(volundr_app)
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{base_url}{SSE_URL}", timeout=SSE_TIMEOUT)
        assert response.status_code == 401
    finally:
        server.should_exit = True
        await asyncio.sleep(0.1)


async def test_sse_hides_another_users_session_from_a_non_owner(
    volundr_app: object,
    auth_headers: object,
) -> None:
    """A same-tenant developer never sees events for someone else's session."""
    owner = auth_headers("sse-owner", "owner@test.com", "default")  # type: ignore[operator]
    other = auth_headers("sse-other", "other@test.com", "default")  # type: ignore[operator]
    broadcaster = volundr_app.state.broadcaster  # type: ignore[union-attr]
    server, base_url = await start_server(volundr_app)

    try:

        async def _create_then_heartbeat() -> None:
            await asyncio.sleep(0.3)
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{base_url}{API}/sessions",
                    json={
                        "name": "owner-only-session",
                        "model": "claude-sonnet-4-6",
                        "source": {
                            "type": "git",
                            "repo": "github.com/acme/demo",
                            "branch": "main",
                        },
                    },
                    headers=owner,
                )
                assert resp.status_code == 201, resp.text
            # Published after the session events, so it is the first event a
            # subscriber who cannot see the session receives.
            await broadcaster.publish_heartbeat()

        task = asyncio.create_task(_create_then_heartbeat())

        events = await asyncio.wait_for(
            collect_sse(base_url, SSE_URL, n=1, headers=other),
            timeout=SSE_TIMEOUT,
        )
        _ = await task

        assert [e["event"] for e in events] == [EventType.HEARTBEAT.value]
    finally:
        server.should_exit = True
        await asyncio.sleep(0.1)
