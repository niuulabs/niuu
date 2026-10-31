"""GET /sessions/stream delivers session_notification only to its owner or an admin."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

from identity.adapters.identity import EnvoyHeaderAuthenticationAdapter
from tests.conftest import make_session_participant_service
from volundr.adapters.inbound.rest import create_router
from volundr.adapters.outbound.broadcaster import InMemoryEventBroadcaster
from volundr.domain.models import EventType, RealtimeEvent
from volundr.domain.services import SessionService, StatsService


class FiniteBroadcaster(InMemoryEventBroadcaster):
    def __init__(self, events: list[RealtimeEvent]):
        super().__init__()
        self._preset = events

    async def subscribe(self) -> AsyncGenerator[RealtimeEvent, None]:
        for event in self._preset:
            yield event


class StubIdentity(EnvoyHeaderAuthenticationAdapter):
    """Trusted Envoy headers with no user store to provision into."""

    async def get_or_provision_user(self, principal):
        return None


def _event(event_type: EventType, **data) -> RealtimeEvent:
    return RealtimeEvent(type=event_type, data=data, timestamp=datetime.now(UTC))


EVENTS = [
    _event(EventType.SESSION_NOTIFICATION, id="mine", owner_id="owner-a", tenant_id="t"),
    _event(EventType.SESSION_NOTIFICATION, id="theirs", owner_id="owner-b", tenant_id="t"),
    _event(EventType.SESSION_NOTIFICATION, id="elsewhere", owner_id="owner-c", tenant_id="z"),
    _event(EventType.HEARTBEAT),
]


async def _stream(
    repository, pod_manager, stats_repository, pricing_provider, *, identity, headers
) -> Response:
    broadcaster = FiniteBroadcaster(EVENTS)
    app = FastAPI()
    if identity is not None:
        app.state.identity = identity
    session_service = SessionService(repository, pod_manager, broadcaster=broadcaster)
    app.include_router(
        create_router(
            session_service=session_service,
            stats_service=StatsService(stats_repository, session_service),
            pricing_provider=pricing_provider,
            broadcaster=broadcaster,
            session_participant_service=make_session_participant_service(session_service),
        )
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get("/api/v1/forge/sessions/stream", headers=headers)


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"x-auth-user-id": "owner-a", "x-auth-tenant": "t"}, {"mine"}),
        (
            {"x-auth-user-id": "root", "x-auth-tenant": "t", "x-auth-roles": "volundr:admin"},
            {"mine", "theirs"},
        ),
    ],
)
async def test_notifications_are_owner_scoped(
    repository, pod_manager, stats_repository, pricing_provider, headers, expected
):
    response = await _stream(
        repository,
        pod_manager,
        stats_repository,
        pricing_provider,
        identity=StubIdentity(),
        headers=headers,
    )
    assert response.status_code == 200
    text = response.text
    delivered = {name for name in ("mine", "theirs", "elsewhere") if f'"{name}"' in text}
    assert delivered == expected
    assert "event: heartbeat" in text  # other event types are unchanged


async def test_bare_dev_app_without_identity_is_unscoped(
    repository, pod_manager, stats_repository, pricing_provider
):
    response = await _stream(
        repository, pod_manager, stats_repository, pricing_provider, identity=None, headers={}
    )
    text = response.text
    assert text.count("event: session_notification") == 3


async def test_anonymous_caller_is_refused_when_identity_is_configured(
    repository, pod_manager, stats_repository, pricing_provider
):
    response = await _stream(
        repository,
        pod_manager,
        stats_repository,
        pricing_provider,
        identity=StubIdentity(),
        headers={},
    )
    assert response.status_code == 401
    assert "session_notification" not in response.text
