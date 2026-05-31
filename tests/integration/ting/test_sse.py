"""Integration tests for Ting SSE event streaming.

Verifies that ``GET /api/v1/ting/events`` delivers real-time Server-Sent
Events when domain actions occur.  Uses a real ``InMemoryEventBus``
instead of the no-op ``StubEventBus`` so that emitted events actually
fan out to SSE subscribers.

Uses a real HTTP server (uvicorn) because httpx's ``ASGITransport``
buffers the entire response body before returning, which deadlocks with
infinite SSE generators.
"""

from __future__ import annotations

import asyncio
import json

import pytest
import pytest_asyncio

from tests.integration.helpers.sse import SSE_TIMEOUT, collect_sse, start_server
from tests.integration.ting.conftest import create_ting_test_app
from ting.adapters.memory_event_bus import InMemoryEventBus
from ting.ports.event_bus import TingEvent

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
]

SSE_URL = "/api/v1/ting/events"


# ------------------------------------------------------------------
# Fixtures — SSE-specific app with real InMemoryEventBus
# ------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def sse_event_bus() -> InMemoryEventBus:
    """Real in-memory event bus that fans out to subscribers."""
    return InMemoryEventBus()


@pytest_asyncio.fixture(loop_scope="session")
async def sse_ting_app(ting_settings, txn_pool, sse_event_bus):  # noqa: ANN001
    """Ting app wired with a real InMemoryEventBus for SSE testing."""
    return create_ting_test_app(ting_settings, txn_pool, sse_event_bus)


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


async def test_ting_sse_connects(
    sse_ting_app: object,
    sse_event_bus: InMemoryEventBus,
) -> None:
    """GET /api/v1/ting/events starts streaming with a snapshot event.

    Emits a dispatcher.state event (snapshot type) *before* connecting so
    the SSE endpoint replays it immediately on connect.
    """
    snapshot_event = TingEvent(
        event="dispatcher.state",
        data={"status": "idle", "active_runs": 0},
    )
    await sse_event_bus.emit(snapshot_event)

    server, base_url = await start_server(sse_ting_app)

    try:
        events = await asyncio.wait_for(
            collect_sse(base_url, SSE_URL, n=1),
            timeout=SSE_TIMEOUT,
        )

        assert len(events) >= 1
        assert events[0]["event"] == "dispatcher.state"
        data = json.loads(events[0]["data"])
        assert data["status"] == "idle"
        assert data["active_runs"] == 0
    finally:
        server.should_exit = True
        await asyncio.sleep(0.1)


async def test_ting_sse_receives_saga_event(
    sse_ting_app: object,
    sse_event_bus: InMemoryEventBus,
) -> None:
    """Connect SSE, emit a saga event, verify it arrives via the stream."""
    server, base_url = await start_server(sse_ting_app)

    try:
        saga_event = TingEvent(
            event="saga.created",
            data={"id": "saga-123", "name": "Auth Overhaul", "status": "ACTIVE"},
        )

        async def _emit_event() -> None:
            await asyncio.sleep(0.3)
            await sse_event_bus.emit(saga_event)

        emit_task = asyncio.create_task(_emit_event())

        events = await asyncio.wait_for(
            collect_sse(base_url, SSE_URL, n=1),
            timeout=SSE_TIMEOUT,
        )
        await emit_task

        assert len(events) >= 1
        saga_events = [e for e in events if e["event"] == "saga.created"]
        assert len(saga_events) >= 1

        data = json.loads(saga_events[0]["data"])
        assert data["id"] == "saga-123"
        assert data["name"] == "Auth Overhaul"
        assert data["status"] == "ACTIVE"
    finally:
        server.should_exit = True
        await asyncio.sleep(0.1)
