"""Tests for authorization enforcement on Skuld-facing API endpoints.

Exercises the auth check paths added to:
- POST /sessions/{id}/usage (report_usage)
- POST /sessions/{id}/chronicle (report_chronicle)
- POST /chronicles/{id}/timeline (report_timeline)
- POST /events (emit_event)
- POST /events/batch (emit_event)
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import (
    InMemoryChronicleRepository,
    InMemorySessionRepository,
    InMemoryTimelineRepository,
    InMemoryTokenTracker,
    MockPodManager,
)
from volundr.adapters.inbound.rest import create_router
from volundr.adapters.inbound.rest_events import create_events_router
from volundr.adapters.outbound.authorization import AllowAllAuthorizationAdapter
from volundr.adapters.outbound.identity import AllowAllIdentityAdapter
from volundr.domain.models import (
    GitSource,
    Principal,
    Session,
    SessionEvent,
    SessionStatus,
)
from volundr.domain.ports import AuthorizationPort, Resource, UserRepository
from volundr.domain.services.chronicle import ChronicleService
from volundr.domain.services.event_ingestion import EventIngestionService
from volundr.domain.services.session import SessionService
from volundr.domain.services.token import TokenService

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class StubUserRepository(UserRepository):
    """Minimal user repo stub for identity adapters."""

    async def get(self, user_id):
        return None

    async def create(self, user):
        return user

    async def update(self, user):
        return user

    async def list(self, **_kw):
        return []

    async def delete(self, user_id):
        return True

    async def get_by_email(self, email):
        return None

    async def add_membership(self, *args, **kwargs):
        pass

    async def get_members(self, *args, **kwargs):
        return []

    async def get_memberships(self, *args, **kwargs):
        return []

    async def remove_membership(self, *args, **kwargs):
        return True


class StubIdentityAdapter(AllowAllIdentityAdapter):
    """Identity adapter that returns a fixed principal for testing.

    Subclasses AllowAllIdentityAdapter so extract_principal() recognizes it
    and calls validate_token() without requiring an Authorization header.
    """

    def __init__(self, principal: Principal):
        super().__init__(user_repository=StubUserRepository())
        self._fixed_principal = principal

    async def validate_token(self, raw_token: str) -> Principal:
        return self._fixed_principal

    async def get_or_provision_user(self, principal: Principal):
        return None


class DenyAllAuthorizationAdapter(AuthorizationPort):
    """Authorization adapter that denies everything."""

    async def is_allowed(self, principal: Principal, action: str, resource: Resource) -> bool:
        return False

    async def filter_allowed(
        self, principal: Principal, action: str, resources: list[Resource]
    ) -> list[Resource]:
        return []


class InMemoryEventSink:
    """Minimal event sink for testing."""

    def __init__(self):
        self._events: list[SessionEvent] = []

    async def emit(self, event: SessionEvent) -> None:
        self._events.append(event)

    async def emit_batch(self, events: list[SessionEvent]) -> None:
        self._events.extend(events)

    async def flush(self) -> None:
        pass

    async def close(self) -> None:
        pass

    @property
    def sink_name(self) -> str:
        return "test"

    @property
    def healthy(self) -> bool:
        return True

    async def get_events(self, session_id, **_kw) -> list[SessionEvent]:
        return [e for e in self._events if e.session_id == session_id]

    async def get_event_counts(self, session_id) -> dict[str, int]:
        return {}

    async def get_token_timeline(self, session_id, **_kw) -> list[dict]:
        return []

    async def delete_by_session(self, session_id) -> int:
        return 0


OWNER_PRINCIPAL = Principal(
    user_id="owner-user",
    email="owner@test.com",
    tenant_id="test-tenant",
    roles=["volundr:developer"],
)

OTHER_PRINCIPAL = Principal(
    user_id="other-user",
    email="other@test.com",
    tenant_id="test-tenant",
    roles=["volundr:developer"],
)


def _make_session(owner_id: str = "owner-user", tenant_id: str = "test-tenant") -> Session:
    return Session(
        id=uuid4(),
        name="test-session",
        model="claude-sonnet-4-20250514",
        status=SessionStatus.RUNNING,
        owner_id=owner_id,
        tenant_id=tenant_id,
        source=GitSource(repo="https://github.com/test/repo", branch="main"),
    )


def _seed_session(repo: InMemorySessionRepository, session: Session) -> None:
    """Directly seed a session into the in-memory repo (no async needed)."""
    repo._sessions[session.id] = session


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def session_repo():
    return InMemorySessionRepository()


@pytest.fixture
def deny_authz():
    return DenyAllAuthorizationAdapter()


@pytest.fixture
def allow_authz():
    return AllowAllAuthorizationAdapter()


@pytest.fixture
def owner_identity():
    return StubIdentityAdapter(OWNER_PRINCIPAL)


@pytest.fixture
def other_identity():
    return StubIdentityAdapter(OTHER_PRINCIPAL)


def _build_rest_app(session_repo, identity, authz):
    """Build a FastAPI app with identity + authorization configured."""
    pod_manager = MockPodManager()
    service = SessionService(session_repo, pod_manager, authorization=authz)

    token_tracker = InMemoryTokenTracker()
    token_service = TokenService(token_tracker, session_repo)

    chronicle_repo = InMemoryChronicleRepository()
    timeline_repo = InMemoryTimelineRepository()
    chronicle_service = ChronicleService(
        chronicle_repository=chronicle_repo,
        session_service=service,
        timeline_repository=timeline_repo,
    )

    router = create_router(
        service,
        token_service=token_service,
        chronicle_service=chronicle_service,
    )

    app = FastAPI()
    app.include_router(router)
    app.state.identity = identity
    app.state.authorization = authz

    from volundr.config import LocalMountsConfig

    class _SettingsStub:
        local_mounts = LocalMountsConfig()

    app.state.settings = _SettingsStub()
    app.state.admin_settings = {}
    return app


def _build_events_app(identity, authz, session_repo):
    """Build a FastAPI app for the events router with auth."""
    sink = InMemoryEventSink()
    ingestion = EventIngestionService(sinks=[sink])
    pod_manager = MockPodManager()
    service = SessionService(session_repo, pod_manager, authorization=authz)

    router = create_events_router(ingestion, sink, session_service=service)
    app = FastAPI()
    app.include_router(router)
    app.state.identity = identity
    app.state.authorization = authz
    return app, sink


# ---------------------------------------------------------------------------
# Tests: Usage endpoint auth
# ---------------------------------------------------------------------------


class TestUsageEndpointAuth:
    """POST /sessions/{id}/usage authorization checks."""

    def test_owner_can_report_usage(self, session_repo, owner_identity, allow_authz):
        session = _make_session()
        _seed_session(session_repo, session)

        app = _build_rest_app(session_repo, owner_identity, allow_authz)
        with TestClient(app) as client:
            resp = client.post(
                f"/api/v1/volundr/sessions/{session.id}/usage",
                json={"tokens": 100, "provider": "cloud", "model": "sonnet", "message_count": 1},
            )
        assert resp.status_code == 201

    def test_denied_user_gets_403_on_usage(self, session_repo, other_identity, deny_authz):
        session = _make_session()
        _seed_session(session_repo, session)

        app = _build_rest_app(session_repo, other_identity, deny_authz)
        with TestClient(app) as client:
            resp = client.post(
                f"/api/v1/volundr/sessions/{session.id}/usage",
                json={"tokens": 100, "provider": "cloud", "model": "sonnet", "message_count": 1},
            )
        assert resp.status_code == 403
        assert "Not authorized" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Tests: Chronicle endpoint auth
# ---------------------------------------------------------------------------


class TestChronicleEndpointAuth:
    """POST /sessions/{id}/chronicle authorization checks."""

    def test_owner_can_report_chronicle(self, session_repo, owner_identity, allow_authz):
        session = _make_session()
        _seed_session(session_repo, session)

        app = _build_rest_app(session_repo, owner_identity, allow_authz)
        with TestClient(app) as client:
            resp = client.post(
                f"/api/v1/volundr/sessions/{session.id}/chronicle",
                json={"duration_seconds": 120, "key_changes": ["file.py: added tests"]},
            )
        assert resp.status_code == 201

    def test_denied_user_gets_403_on_chronicle(self, session_repo, other_identity, deny_authz):
        session = _make_session()
        _seed_session(session_repo, session)

        app = _build_rest_app(session_repo, other_identity, deny_authz)
        with TestClient(app) as client:
            resp = client.post(
                f"/api/v1/volundr/sessions/{session.id}/chronicle",
                json={"duration_seconds": 120, "key_changes": ["file.py: added tests"]},
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Tests: Timeline endpoint auth
# ---------------------------------------------------------------------------


class TestTimelineEndpointAuth:
    """POST /chronicles/{id}/timeline authorization checks."""

    def test_owner_can_add_timeline_event(self, session_repo, owner_identity, allow_authz):
        session = _make_session()
        _seed_session(session_repo, session)

        app = _build_rest_app(session_repo, owner_identity, allow_authz)
        with TestClient(app) as client:
            resp = client.post(
                f"/api/v1/volundr/chronicles/{session.id}/timeline",
                json={"t": 10, "type": "file", "label": "main.py", "action": "modified"},
            )
        assert resp.status_code == 201

    def test_denied_user_gets_403_on_timeline(self, session_repo, other_identity, deny_authz):
        session = _make_session()
        _seed_session(session_repo, session)

        app = _build_rest_app(session_repo, other_identity, deny_authz)
        with TestClient(app) as client:
            resp = client.post(
                f"/api/v1/volundr/chronicles/{session.id}/timeline",
                json={"t": 10, "type": "file", "label": "main.py", "action": "modified"},
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Tests: Events endpoint auth
# ---------------------------------------------------------------------------


class TestEventsEndpointAuth:
    """POST /events and /events/batch authorization checks."""

    def test_owner_can_emit_event(self, session_repo, owner_identity, allow_authz):
        session = _make_session()
        _seed_session(session_repo, session)

        app, _ = _build_events_app(owner_identity, allow_authz, session_repo)
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/volundr/events",
                json={
                    "session_id": str(session.id),
                    "event_type": "session_start",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "data": {},
                    "sequence": 0,
                },
            )
        assert resp.status_code == 201

    def test_denied_user_gets_403_on_event(self, session_repo, other_identity, deny_authz):
        session = _make_session()
        _seed_session(session_repo, session)

        app, _ = _build_events_app(other_identity, deny_authz, session_repo)
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/volundr/events",
                json={
                    "session_id": str(session.id),
                    "event_type": "session_start",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "data": {},
                    "sequence": 0,
                },
            )
        assert resp.status_code == 403

    def test_denied_user_gets_403_on_event_batch(self, session_repo, other_identity, deny_authz):
        session = _make_session()
        _seed_session(session_repo, session)

        app, _ = _build_events_app(other_identity, deny_authz, session_repo)
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/volundr/events/batch",
                json={
                    "events": [
                        {
                            "session_id": str(session.id),
                            "event_type": "session_start",
                            "timestamp": datetime.now(UTC).isoformat(),
                            "data": {},
                            "sequence": 0,
                        }
                    ]
                },
            )
        assert resp.status_code == 403

    def test_event_for_nonexistent_session_passes(self, session_repo, owner_identity, allow_authz):
        """When session doesn't exist, auth check is skipped (session lookup returns None)."""
        app, _ = _build_events_app(owner_identity, allow_authz, session_repo)
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/volundr/events",
                json={
                    "session_id": str(uuid4()),
                    "event_type": "session_start",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "data": {},
                    "sequence": 0,
                },
            )
        assert resp.status_code == 201


# ---------------------------------------------------------------------------
# Tests: No identity configured (dev mode) — auth checks are no-op
# ---------------------------------------------------------------------------


class TestNoIdentityDevMode:
    """When no identity adapter is on app.state, auth checks pass through."""

    def test_usage_no_identity(self, session_repo):
        session = _make_session()
        _seed_session(session_repo, session)

        app = _build_rest_app(
            session_repo,
            identity=AllowAllAuthorizationAdapter(),  # Placeholder, will be removed
            authz=AllowAllAuthorizationAdapter(),
        )
        # Remove identity from state to simulate dev mode
        del app.state.identity

        with TestClient(app) as client:
            resp = client.post(
                f"/api/v1/volundr/sessions/{session.id}/usage",
                json={"tokens": 50, "provider": "cloud", "model": "test", "message_count": 1},
            )
        assert resp.status_code == 201

    def test_events_no_session_service(self):
        """When session_service is None, auth check is skipped."""
        sink = InMemoryEventSink()
        ingestion = EventIngestionService(sinks=[sink])
        router = create_events_router(ingestion, sink, session_service=None)

        app = FastAPI()
        app.include_router(router)
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/volundr/events",
                json={
                    "session_id": str(uuid4()),
                    "event_type": "session_start",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "data": {},
                    "sequence": 0,
                },
            )
        assert resp.status_code == 201
