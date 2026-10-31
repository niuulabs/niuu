"""Tests for the Yggdrasil background realm poller."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from unittest.mock import Mock

import pytest
from fastapi import Request

from yggdrasil.connection_manager import ConnectionManager, SSEClientLimitExceededError
from yggdrasil.main import create_app
from yggdrasil.models import RealmSummary
from yggdrasil.sse.poller import RealmPoller
from yggdrasil.stream import stream_realm_summary


@dataclass
class FakeRealmAdapter:
    realm_id: str
    summary: RealmSummary | None = None
    error: Exception | None = None
    calls: int = 0

    async def get_summary(self) -> RealmSummary:
        self.calls += 1
        if self.error is not None:
            raise self.error
        assert self.summary is not None
        return self.summary


@pytest.mark.asyncio
async def test_poller_broadcasts_only_changed_realm_summaries() -> None:
    summary = RealmSummary(realm_id="odin", health_status="healthy", metrics={"nodes": 3})
    adapter = FakeRealmAdapter("odin", summary)
    manager = ConnectionManager(max_clients=1)
    queue = manager.connect("odin")
    poller = RealmPoller([adapter], manager, poll_interval=15.0)

    await poller.poll_once()
    assert await queue.get() == summary

    await poller.poll_once()
    assert queue.empty()

    adapter.summary = RealmSummary(realm_id="odin", health_status="degraded", metrics={"nodes": 2})
    await poller.poll_once()
    assert await queue.get() == adapter.summary


@pytest.mark.asyncio
async def test_poller_logs_adapter_errors_and_continues(caplog: pytest.LogCaptureFixture) -> None:
    healthy_summary = RealmSummary(realm_id="thor", health_status="healthy")
    failing = FakeRealmAdapter("odin", error=RuntimeError("adapter unavailable"))
    healthy = FakeRealmAdapter("thor", summary=healthy_summary)
    manager = ConnectionManager(max_clients=1)
    broadcast = Mock(wraps=manager.broadcast)
    manager.broadcast = broadcast
    poller = RealmPoller([failing, healthy], manager, poll_interval=15.0)

    await poller.poll_once()

    assert poller.summary_for("odin") is None
    assert poller.summary_for("thor") == healthy_summary
    broadcast.assert_called_once_with(healthy_summary)
    assert "could not poll realm odin" in caplog.text


@pytest.mark.asyncio
async def test_poller_start_is_idempotent_and_stop_cancels_the_task() -> None:
    summary = RealmSummary(realm_id="odin", health_status="healthy")
    poller = RealmPoller(
        [FakeRealmAdapter("odin", summary=summary)],
        ConnectionManager(max_clients=1),
        poll_interval=15.0,
    )

    poller.start()
    task = poller._task
    poller.start()
    await asyncio.sleep(0)
    await poller.stop()
    await poller.stop()

    assert task is not None
    assert poller.summary_for("odin") is None
    assert poller._task is None


@pytest.mark.asyncio
async def test_poller_waits_an_interval_before_the_next_poll() -> None:
    summary = RealmSummary(realm_id="odin", health_status="healthy")
    adapter = FakeRealmAdapter("odin", summary=summary)
    poller = RealmPoller([adapter], ConnectionManager(max_clients=1), poll_interval=15.0)

    await poller.poll_once()
    poller.start()
    await asyncio.sleep(0)
    await poller.stop()

    assert adapter.calls == 1


def test_connection_manager_enforces_the_limit_and_removes_empty_realm_sets() -> None:
    manager = ConnectionManager(max_clients=2)
    queue = manager.connect("odin")
    second_queue = manager.connect("odin")

    assert manager.max_clients == 2
    with pytest.raises(SSEClientLimitExceededError):
        manager.connect("thor")
    manager.disconnect("odin", queue)
    assert manager.active_client_count == 1
    manager.disconnect("thor", queue)
    manager.disconnect("odin", second_queue)

    assert manager.active_client_count == 0


@pytest.mark.asyncio
async def test_application_lifespan_starts_and_stops_the_poller() -> None:
    summary = RealmSummary(realm_id="odin", health_status="healthy")
    app = create_app(adapters=[FakeRealmAdapter("odin", summary)])
    health = next(route for route in app.routes if getattr(route, "path", None) == "/health")
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/health",
            "headers": [],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )

    async with app.router.lifespan_context(app):
        assert app.state.realm_poller.summary_for("odin") == summary
        assert app.state.realm_poller._task is not None
        assert await health.endpoint(request) == {"status": "healthy"}

    assert app.state.realm_poller._task is None


@pytest.mark.asyncio
async def test_stream_checks_for_disconnect_after_an_empty_queue_timeout() -> None:
    class RequestThatDisconnects:
        def __init__(self) -> None:
            self._checks = 0

        async def is_disconnected(self) -> bool:
            self._checks += 1
            return self._checks > 1

    manager = ConnectionManager(max_clients=2)
    queue = manager.connect("odin")
    summary = RealmSummary(realm_id="odin", health_status="healthy")
    stream = stream_realm_summary(
        RequestThatDisconnects(),
        "odin",
        manager,
        summary,
        queue,
        disconnect_check_interval=0,
    )

    await anext(stream)
    with pytest.raises(StopAsyncIteration):
        await anext(stream)

    assert manager.active_client_count == 0
