"""Contract tests for the Yggdrasil realm summary SSE endpoint."""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest
from fastapi import HTTPException, Request

from yggdrasil.config import AdapterConfig, YggdrasilSettings
from yggdrasil.main import _build_adapter, create_app
from yggdrasil.models import RealmSummary
from yggdrasil.stream import REALM_SUMMARY_EVENT


@dataclass
class FakeRealmAdapter:
    """In-memory realm adapter used by the stream contract tests."""

    realm_id: str
    summary: RealmSummary

    async def get_summary(self) -> RealmSummary:
        return self.summary


def _request(app) -> Request:
    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(
        {
            "type": "http",
            "app": app,
            "headers": [],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
            "scheme": "http",
            "method": "GET",
            "path": "/api/v1/yggdrasil/realms/odin/stream",
            "query_string": b"",
        },
        receive=receive,
    )


def _route(app):
    return next(
        route
        for route in app.state.sse_router.routes
        if route.path == "/api/v1/yggdrasil/realms/{realm_id}/stream"
    )


def test_build_adapter_loads_configured_adapter() -> None:
    summary = RealmSummary(realm_id="odin", health_status="healthy")
    adapter = _build_adapter(
        AdapterConfig(
            adapter=f"{FakeRealmAdapter.__module__}.FakeRealmAdapter",
            kwargs={"realm_id": "odin", "summary": summary},
        )
    )

    assert adapter.realm_id == "odin"


@pytest.mark.asyncio
async def test_stream_sends_current_realm_summary_as_an_sse_frame() -> None:
    summary = RealmSummary(realm_id="odin", health_status="healthy", metrics={"nodes": 3})
    app = create_app(adapters=[FakeRealmAdapter("odin", summary)])
    await app.state.realm_poller.poll_once()

    response = await _route(app).endpoint(_request(app), "odin")
    frame = await anext(response.body_iterator)
    await response.body_iterator.aclose()

    assert response.media_type == "text/event-stream"
    assert response.headers["x-accel-buffering"] == "no"
    assert frame.startswith(f"event: {REALM_SUMMARY_EVENT}\n")
    payload = frame.removeprefix(f"event: {REALM_SUMMARY_EVENT}\ndata: ").removesuffix("\n\n")
    assert json.loads(payload) == summary.model_dump(mode="json")
    assert app.state.connection_manager.active_client_count == 0


@pytest.mark.asyncio
async def test_stream_returns_503_when_client_limit_is_reached() -> None:
    summary = RealmSummary(realm_id="odin", health_status="healthy")
    app = create_app(
        settings=YggdrasilSettings(max_sse_clients=1),
        adapters=[FakeRealmAdapter("odin", summary)],
    )
    await app.state.realm_poller.poll_once()
    route = _route(app)

    response = await route.endpoint(_request(app), "odin")
    await anext(response.body_iterator)

    with pytest.raises(HTTPException) as error:
        await route.endpoint(_request(app), "odin")

    await response.body_iterator.aclose()
    assert error.value.status_code == 503
    assert app.state.connection_manager.active_client_count == 0


@pytest.mark.asyncio
async def test_stream_returns_404_without_a_realm_summary() -> None:
    app = create_app()

    with pytest.raises(HTTPException) as error:
        await _route(app).endpoint(_request(app), "odin")

    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_stream_delivers_a_poll_between_connection_and_first_read_once() -> None:
    initial_summary = RealmSummary(realm_id="odin", health_status="healthy", metrics={"nodes": 3})
    updated_summary = RealmSummary(realm_id="odin", health_status="degraded", metrics={"nodes": 2})
    adapter = FakeRealmAdapter("odin", initial_summary)
    app = create_app(adapters=[adapter])
    await app.state.realm_poller.poll_once()

    response = await _route(app).endpoint(_request(app), "odin")
    adapter.summary = updated_summary
    await app.state.realm_poller.poll_once()

    initial_frame = await anext(response.body_iterator)
    update_frame = await anext(response.body_iterator)
    await response.body_iterator.aclose()

    initial_payload = json.loads(initial_frame.split("data: ", 1)[1])
    assert initial_payload == initial_summary.model_dump(mode="json")
    assert json.loads(update_frame.split("data: ", 1)[1]) == updated_summary.model_dump(mode="json")


def test_poll_interval_uses_yggdrasil_environment_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YGGDRASIL_POLL_INTERVAL", raising=False)
    assert YggdrasilSettings().poll_interval == 15.0
    assert YggdrasilSettings().stream_disconnect_check_interval == 1.0

    monkeypatch.setenv("YGGDRASIL_POLL_INTERVAL", "2.5")
    assert YggdrasilSettings().poll_interval == 2.5
