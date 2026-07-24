"""Tests for the REST adapter."""

import asyncio
import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from niuu.domain.models import Principal
from tests.conftest import (
    InMemoryPricingProvider,
    InMemorySessionRepository,
    InMemoryStatsRepository,
    MockGitProvider,
    MockGitRegistry,
    MockPodManager,
)
from volundr.adapters.inbound.rest import (
    SessionCreate,
    SessionResponse,
    SessionUpdate,
    StatsResponse,
    _session_proxy_url,
    create_router,
)
from volundr.config import LocalMountsConfig
from volundr.domain.models import GitProviderType, GitSource, RepoInfo, Session, SessionStatus
from volundr.domain.services import RepoService, SessionService, StatsService
from volundr.domain.services.session import SessionAccessDeniedError

_SIGNING_KEY = "test-only-signing-key-32-bytes-long!"


def _build_token(scopes: list[str]) -> str:
    """Mint a Valkyrie build token JWT (signature ignored downstream)."""
    return jwt.encode(
        {"sub": "user-1", "token_use": "valkyrie_build", "scopes": scopes},
        _SIGNING_KEY,
        algorithm="HS256",
    )


@pytest.fixture
def service(repository: InMemorySessionRepository, pod_manager: MockPodManager) -> SessionService:
    """Create a session service with test doubles."""
    return SessionService(repository, pod_manager)


@pytest.fixture
def stats_repo() -> InMemoryStatsRepository:
    """Create a stats repository with sample data."""
    return InMemoryStatsRepository(
        active_sessions=3,
        total_sessions=10,
        tokens_today=50000,
        local_tokens=20000,
        cloud_tokens=30000,
        cost_today=Decimal("1.50"),
    )


@pytest.fixture
def stats_service(stats_repo: InMemoryStatsRepository) -> StatsService:
    """Create a stats service with test repository."""
    return StatsService(stats_repo)


@pytest.fixture
def pricing() -> InMemoryPricingProvider:
    """Create a pricing provider."""
    return InMemoryPricingProvider()


@pytest.fixture
def app(
    service: SessionService, stats_service: StatsService, pricing: InMemoryPricingProvider
) -> FastAPI:
    """Create a test FastAPI app."""
    app = FastAPI()
    router = create_router(service, stats_service, pricing_provider=pricing)
    app.include_router(router)

    # Minimal settings stub for endpoints that read app.state.settings
    class _SettingsStub:
        local_mounts = LocalMountsConfig()

    app.state.settings = _SettingsStub()
    app.state.admin_settings = {}
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """Create a test client."""
    client = TestClient(app)
    yield client
    client.close()


class TestSessionCreate:
    """Tests for SessionCreate model."""

    def test_valid_session_create(self):
        """SessionCreate accepts valid data."""
        data = SessionCreate(
            name="test-session",
            model="claude-sonnet-4",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        assert data.name == "test-session"
        assert data.model == "claude-sonnet-4"
        assert data.source.repo == "https://github.com/org/repo"
        assert data.source.branch == "main"

    def test_session_create_empty_name_rejected(self):
        """SessionCreate rejects empty name."""
        with pytest.raises(ValueError):
            SessionCreate(
                name="",
                model="claude-sonnet-4",
                source=GitSource(
                    repo="https://github.com/org/repo",
                    branch="main",
                ),
            )

    def test_session_create_name_too_long_rejected(self):
        """SessionCreate rejects name over 255 chars."""
        with pytest.raises(ValueError):
            SessionCreate(
                name="x" * 256,
                model="claude-sonnet-4",
                source=GitSource(repo="https://github.com/org/repo", branch="main"),
            )


class TestSessionUpdate:
    """Tests for SessionUpdate model."""

    def test_session_update_partial(self):
        """SessionUpdate allows partial updates."""
        data = SessionUpdate(name="new-name")
        assert data.name == "new-name"
        assert data.model is None
        assert data.branch is None

    def test_session_update_all_fields(self):
        """SessionUpdate allows all fields."""
        data = SessionUpdate(name="new-name", model="claude-opus-4", branch="feature/new")
        assert data.name == "new-name"
        assert data.model == "claude-opus-4"
        assert data.branch == "feature/new"


class TestSessionResponse:
    """Tests for SessionResponse model."""

    def test_from_session(self):
        """SessionResponse.from_session converts domain model."""
        session = Session(
            id=uuid4(),
            name="Test",
            model="claude-sonnet-4",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
            status=SessionStatus.RUNNING,
            chat_endpoint="wss://chat.example.com",
            code_endpoint="https://code.example.com",
            message_count=5,
            tokens_used=1000,
            pod_name="volundr-abc123",
        )
        response = SessionResponse.from_session(session)

        assert response.id == session.id
        assert response.name == session.name
        assert response.model == session.model
        assert response.source.type == "git"
        assert response.source.repo == "https://github.com/org/repo"
        assert response.status == SessionStatus.RUNNING
        assert response.chat_endpoint == "wss://chat.example.com"
        assert response.code_endpoint == "https://code.example.com"
        assert response.message_count == 5
        assert response.tokens_used == 1000
        assert response.pod_name == "volundr-abc123"
        assert session.created_at.isoformat() in response.created_at

    def test_from_session_normalizes_loopback_chat_endpoint(self):
        """Loopback chat endpoints should prefer localhost for browser clients."""
        session = Session(
            id=uuid4(),
            name="Test",
            model="claude-sonnet-4",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
            status=SessionStatus.RUNNING,
            chat_endpoint="ws://127.0.0.1:8080/s/example/session",
        )

        response = SessionResponse.from_session(session)

        assert response.chat_endpoint == "ws://localhost:8080/s/example/session"

    def test_from_session_routes_openshell_chat_through_niuu_proxy(self):
        session = Session(
            id=uuid4(),
            name="OpenShell",
            model="gpt-5.6-sol",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
            status=SessionStatus.RUNNING,
            chat_endpoint=("ws://forge-example--skuld.openshell.localhost:8080/session"),
        )

        response = SessionResponse.from_session(session)

        assert response.chat_endpoint == f"/s/{session.id}/session"


class TestListSessions:
    """Tests for GET /api/v1/forge/sessions."""

    def test_list_sessions_empty(self, client: TestClient):
        """Returns empty list when no sessions exist."""
        response = client.get("/api/v1/forge/sessions")
        assert response.status_code == 200
        assert response.json() == []

    async def test_list_sessions_with_data(self, client: TestClient, service: SessionService):
        """Returns list of sessions."""
        await service.create_session(
            "Session 1",
            "claude-sonnet-4",
            source=GitSource(
                repo="https://github.com/org/repo",
                branch="main",
            ),
        )
        await service.create_session(
            "Session 2",
            "claude-opus-4",
            source=GitSource(
                repo="https://github.com/org/repo",
                branch="dev",
            ),
        )

        response = client.get("/api/v1/forge/sessions")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

    def test_forge_alias_prefix_renders_same_sessions_endpoint(
        self, service: SessionService, stats_service: StatsService
    ):
        """The canonical /api/v1/forge alias should expose the same routes."""
        app = FastAPI()
        app.include_router(create_router(service, stats_service, prefix="/api/v1/forge"))

        client = TestClient(app)
        try:
            response = client.get("/api/v1/forge/sessions")
        finally:
            client.close()

        assert response.status_code == 200
        assert response.json() == []


class TestCreateSession:
    """Tests for POST /api/v1/forge/sessions.

    POST creates AND starts the session in one call.
    """

    def test_create_session_success(self, client: TestClient):
        """Creates and starts session, returns 201 with running status."""
        response = client.post(
            "/api/v1/forge/sessions",
            json={
                "name": "my-session",
                "model": "claude-sonnet-4",
                "source": {"type": "git", "repo": "https://github.com/org/repo", "branch": "main"},
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "my-session"
        assert data["model"] == "claude-sonnet-4"
        assert data["source"]["repo"] == "https://github.com/org/repo"
        assert data["source"]["branch"] == "main"
        assert data["status"] == "starting"
        assert "id" in data

    def test_create_session_validation_error(self, client: TestClient):
        """Returns 422 for invalid data."""
        response = client.post(
            "/api/v1/forge/sessions",
            json={
                "name": "",
                "model": "claude-sonnet-4",
                "source": {"type": "git", "repo": "https://github.com/org/repo", "branch": "main"},
            },
        )
        assert response.status_code == 422

    def test_create_session_missing_field(self, client: TestClient):
        """Returns 422 for missing required field (name)."""
        response = client.post(
            "/api/v1/forge/sessions",
            json={
                "model": "claude-sonnet-4",
                "source": {"type": "git", "repo": "https://github.com/org/repo"},
            },
        )
        assert response.status_code == 422

    def test_create_session_scoped_build_token_missing_scope_is_403(self, client: TestClient):
        """A Valkyrie build token lacking forge:session:create is 403'd."""
        token = _build_token(["ting:workflow:launch"])
        response = client.post(
            "/api/v1/forge/sessions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "my-session",
                "model": "claude-sonnet-4",
                "source": {"type": "git", "repo": "https://github.com/org/repo", "branch": "main"},
            },
        )
        assert response.status_code == 403
        assert "forge:session:create" in response.json()["detail"]

    def test_create_session_scoped_build_token_with_scope_admitted(self, client: TestClient):
        """A build token carrying forge:session:create proceeds normally."""
        token = _build_token(["forge:session:create"])
        response = client.post(
            "/api/v1/forge/sessions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "scoped-session",
                "model": "claude-sonnet-4",
                "source": {"type": "git", "repo": "https://github.com/org/repo", "branch": "main"},
            },
        )
        assert response.status_code == 201

    def test_create_session_normal_token_unaffected(self, client: TestClient):
        """A plain (non-build) bearer token has no scope constraint."""
        token = jwt.encode({"type": "pat", "sub": "user-1"}, _SIGNING_KEY, algorithm="HS256")
        response = client.post(
            "/api/v1/forge/sessions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "human-session",
                "model": "claude-sonnet-4",
                "source": {"type": "git", "repo": "https://github.com/org/repo", "branch": "main"},
            },
        )
        assert response.status_code == 201


class TestGetSession:
    """Tests for GET /api/v1/forge/sessions/{id}."""

    async def test_get_session_success(self, client: TestClient, service: SessionService):
        """Returns session by ID."""
        session = await service.create_session(
            "Test",
            "claude-sonnet-4",
            source=GitSource(
                repo="https://github.com/org/repo",
                branch="main",
            ),
        )

        response = client.get(f"/api/v1/forge/sessions/{session.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(session.id)
        assert data["name"] == "Test"
        assert data["source"]["repo"] == "https://github.com/org/repo"
        assert data["source"]["branch"] == "main"

    def test_get_session_not_found(self, client: TestClient):
        """Returns 404 for non-existent session."""
        fake_id = uuid4()
        response = client.get(f"/api/v1/forge/sessions/{fake_id}")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_get_session_invalid_uuid(self, client: TestClient):
        """Returns 422 for invalid UUID."""
        response = client.get("/api/v1/forge/sessions/not-a-uuid")
        assert response.status_code == 422


class TestUpdateSession:
    """Tests for PUT /api/v1/forge/sessions/{id}."""

    async def test_update_session_name(self, client: TestClient, service: SessionService):
        """Updates session name."""
        session = await service.create_session(
            "Old Name",
            "claude-sonnet-4",
            source=GitSource(
                repo="https://github.com/org/repo",
                branch="main",
            ),
        )

        response = client.put(
            f"/api/v1/forge/sessions/{session.id}",
            json={"name": "new-name"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "new-name"
        assert data["model"] == "claude-sonnet-4"

    async def test_update_session_model(self, client: TestClient, service: SessionService):
        """Updates session model."""
        session = await service.create_session(
            "Test",
            "claude-sonnet-4",
            source=GitSource(
                repo="https://github.com/org/repo",
                branch="main",
            ),
        )

        response = client.put(
            f"/api/v1/forge/sessions/{session.id}",
            json={"model": "claude-opus-4"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["model"] == "claude-opus-4"

    async def test_update_session_branch(self, client: TestClient, service: SessionService):
        """Updates session branch."""
        session = await service.create_session(
            "Test",
            "claude-sonnet-4",
            source=GitSource(
                repo="https://github.com/org/repo",
                branch="main",
            ),
        )

        response = client.put(
            f"/api/v1/forge/sessions/{session.id}",
            json={"branch": "feature/new"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["source"]["branch"] == "feature/new"

    async def test_update_session_all_fields(self, client: TestClient, service: SessionService):
        """Updates name, model, and branch."""
        session = await service.create_session(
            "old",
            "claude-sonnet-4",
            source=GitSource(
                repo="https://github.com/org/repo",
                branch="main",
            ),
        )

        response = client.put(
            f"/api/v1/forge/sessions/{session.id}",
            json={"name": "new", "model": "claude-opus-4", "branch": "dev"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "new"
        assert data["model"] == "claude-opus-4"
        assert data["source"]["branch"] == "dev"

    def test_update_session_not_found(self, client: TestClient):
        """Returns 404 for non-existent session."""
        fake_id = uuid4()
        response = client.put(
            f"/api/v1/forge/sessions/{fake_id}",
            json={"name": "new-name"},
        )
        assert response.status_code == 404


class TestDeleteSession:
    """Tests for DELETE /api/v1/forge/sessions/{id}."""

    async def test_delete_session_success(self, client: TestClient, service: SessionService):
        """Deletes session and returns 204."""
        session = await service.create_session(
            "Test",
            "claude-sonnet-4",
            source=GitSource(
                repo="https://github.com/org/repo",
                branch="main",
            ),
        )

        response = client.delete(f"/api/v1/forge/sessions/{session.id}")
        assert response.status_code == 204

        # Verify deleted
        get_response = client.get(f"/api/v1/forge/sessions/{session.id}")
        assert get_response.status_code == 404

    def test_delete_session_not_found(self, client: TestClient):
        """Returns 404 for non-existent session."""
        fake_id = uuid4()
        response = client.delete(f"/api/v1/forge/sessions/{fake_id}")
        assert response.status_code == 404

    async def test_delete_session_with_cleanup_targets(
        self, client: TestClient, service: SessionService
    ):
        """Deletes session with cleanup targets in request body."""
        session = await service.create_session(
            "Test",
            "claude-sonnet-4",
            source=GitSource(
                repo="https://github.com/org/repo",
                branch="main",
            ),
        )

        response = client.request(
            "DELETE",
            f"/api/v1/forge/sessions/{session.id}",
            json={"cleanup": ["workspace_storage", "chronicles"]},
        )
        assert response.status_code == 204

    async def test_delete_session_empty_body_backwards_compatible(
        self, client: TestClient, service: SessionService
    ):
        """Deletes session with empty body (backwards compatible)."""
        session = await service.create_session(
            "Test",
            "claude-sonnet-4",
            source=GitSource(
                repo="https://github.com/org/repo",
                branch="main",
            ),
        )

        response = client.delete(f"/api/v1/forge/sessions/{session.id}")
        assert response.status_code == 204


class TestStartSession:
    """Tests for POST /api/v1/forge/sessions/{id}/start."""

    async def test_start_session_success(self, client: TestClient, service: SessionService):
        """Starts session and returns endpoints."""
        session = await service.create_session(
            "Test",
            "claude-sonnet-4",
            source=GitSource(
                repo="https://github.com/org/repo",
                branch="main",
            ),
        )

        response = client.post(f"/api/v1/forge/sessions/{session.id}/start")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "starting"
        assert data["chat_endpoint"] is not None
        # code_endpoint set in background task
        # pod_name set in background task

    def test_start_session_not_found(self, client: TestClient):
        """Returns 404 for non-existent session."""
        fake_id = uuid4()
        response = client.post(f"/api/v1/forge/sessions/{fake_id}/start")
        assert response.status_code == 404

    async def test_start_session_invalid_state(self, client: TestClient, service: SessionService):
        """Returns 409 when session cannot be started."""
        session = await service.create_session(
            "Test",
            "claude-sonnet-4",
            source=GitSource(
                repo="https://github.com/org/repo",
                branch="main",
            ),
        )
        await service.start_session(session.id)

        # Try to start again (already running)
        response = client.post(f"/api/v1/forge/sessions/{session.id}/start")
        assert response.status_code == 409
        assert "cannot start" in response.json()["detail"].lower()


class TestStopSession:
    """Tests for POST /api/v1/forge/sessions/{id}/stop."""

    async def test_stop_session_success(self, client: TestClient, service: SessionService):
        """Stops session and clears endpoints."""
        session = await service.create_session(
            "Test",
            "claude-sonnet-4",
            source=GitSource(
                repo="https://github.com/org/repo",
                branch="main",
            ),
        )
        await service.start_session(session.id)

        response = client.post(f"/api/v1/forge/sessions/{session.id}/stop")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "stopped"
        assert data["chat_endpoint"] is None
        assert data["code_endpoint"] is None

    def test_stop_session_not_found(self, client: TestClient):
        """Returns 404 for non-existent session."""
        fake_id = uuid4()
        response = client.post(f"/api/v1/forge/sessions/{fake_id}/stop")
        assert response.status_code == 404

    async def test_stop_session_invalid_state(self, client: TestClient, service: SessionService):
        """Returns 409 when session cannot be stopped."""
        session = await service.create_session(
            "Test",
            "claude-sonnet-4",
            source=GitSource(
                repo="https://github.com/org/repo",
                branch="main",
            ),
        )

        # Try to stop a created (not running) session
        response = client.post(f"/api/v1/forge/sessions/{session.id}/stop")
        assert response.status_code == 409
        assert "cannot stop" in response.json()["detail"].lower()


class TestSessionMessages:
    """Tests for POST /api/v1/forge/sessions/{id}/messages."""

    def test_send_message_uses_plain_ws_without_ssl(
        self,
        client: TestClient,
        repository: InMemorySessionRepository,
    ) -> None:
        """ws:// chat endpoints should not receive an SSL context."""

        class _FakeWebSocket:
            def __init__(self) -> None:
                self.sent: list[str] = []

            async def recv(self) -> str:
                raise TimeoutError

            async def send(self, payload: str) -> None:
                self.sent.append(payload)

        class _FakeConnect:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, object]]] = []
                self.ws = _FakeWebSocket()

            def __call__(self, url: str, **kwargs: object):
                self.calls.append((url, kwargs))
                ws = self.ws

                class _Ctx:
                    async def __aenter__(self) -> _FakeWebSocket:
                        return ws

                    async def __aexit__(self, exc_type, exc, tb) -> bool:
                        return False

                return _Ctx()

        session = Session(
            id=uuid4(),
            name="message-session",
            model="claude-sonnet-4",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
            status=SessionStatus.RUNNING,
            chat_endpoint="ws://localhost:8080/s/message-session/session",
        )
        asyncio.run(repository.create(session))

        fake_connect = _FakeConnect()
        with (
            patch("websockets.asyncio.client.connect", new=fake_connect),
            patch(
                "volundr.adapters.inbound.rest.extract_principal",
                new=AsyncMock(
                    return_value=Principal(
                        user_id="dev-user",
                        email="dev@example.com",
                        tenant_id="default",
                        roles=[],
                    )
                ),
            ),
        ):
            response = client.post(
                f"/api/v1/forge/sessions/{session.id}/messages",
                json={"content": "hello from rest"},
            )

        # INV-7: no delivery ACK arrives within the grace (recv times out), so the
        # broker has ACCEPTED the message but not yet confirmed it reached the agent.
        # The contract reports 202 "pending" (NOT a 200 "sent") — its retry drives it.
        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "pending"
        assert body["delivery"] == "pending"
        assert fake_connect.calls == [
            ("ws://localhost:8080/s/message-session/session", {"open_timeout": 10})
        ]
        # BUG-3: the outbound user frame now carries a request_id correlating with the
        # broker's delivery ACK. The endpoint echoes the same request_id in its body,
        # so assert the frame is exactly {type, content, request_id} with that exact id.
        req_id = body["request_id"]
        assert len(fake_connect.ws.sent) == 1
        assert json.loads(fake_connect.ws.sent[0]) == {
            "type": "user",
            "content": "hello from rest",
            "request_id": req_id,
        }

    def test_send_message_dead_pod_self_heals_on_read_and_404s(
        self,
        client: TestClient,
        repository: InMemorySessionRepository,
        pod_manager: MockPodManager,
    ) -> None:
        """INV-9: a dead pod (pod_manager reports STOPPED) is reconciled on the
        read that resolves the proxy target, so the send returns a deterministic
        404 (no active endpoint) and never a false 'sent'."""
        session = Session(
            id=uuid4(),
            name="dead-message-session",
            model="claude-sonnet-4",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
            status=SessionStatus.RUNNING,
            chat_endpoint="ws://localhost:8080/s/dead-message-session/session",
        )
        asyncio.run(repository.create(session))

        async def _stopped(_session):
            return SessionStatus.STOPPED

        pod_manager.status = _stopped  # type: ignore[method-assign]

        with patch(
            "volundr.adapters.inbound.rest.extract_principal",
            new=AsyncMock(
                return_value=Principal(
                    user_id="dev-user",
                    email="dev@example.com",
                    tenant_id="default",
                    roles=[],
                )
            ),
        ):
            response = client.post(
                f"/api/v1/forge/sessions/{session.id}/messages",
                json={"content": "anybody home?"},
            )

        assert response.status_code == 404
        assert "sent" not in str(response.json()).lower()
        # The row self-healed off the dead endpoint.
        reconciled = asyncio.run(repository.get(session.id))
        assert reconciled.status == SessionStatus.STOPPED
        assert reconciled.chat_endpoint is None
        assert (reconciled.error or "").startswith("liveness:")

    def test_send_message_unreachable_broker_reconciles_and_returns_409(
        self,
        client: TestClient,
        repository: InMemorySessionRepository,
        pod_manager: MockPodManager,
    ) -> None:
        """INV-9: when the row still looks live (status lags) but the broker WS
        connect fails, the send reconciles and returns a deterministic 409 —
        never a false 'sent'."""

        def _refused(url: str, **kwargs: object):
            raise OSError("connection refused")

        session = Session(
            id=uuid4(),
            name="lagging-message-session",
            model="claude-sonnet-4",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
            status=SessionStatus.RUNNING,
            chat_endpoint="ws://localhost:8080/s/lagging-message-session/session",
        )
        asyncio.run(repository.create(session))

        # Pod manager still reports RUNNING (status lags reality), so the proxy
        # target resolves and we actually attempt — and fail — the WS connect.
        with (
            patch("websockets.asyncio.client.connect", new=_refused),
            patch(
                "volundr.adapters.inbound.rest.extract_principal",
                new=AsyncMock(
                    return_value=Principal(
                        user_id="dev-user",
                        email="dev@example.com",
                        tenant_id="default",
                        roles=[],
                    )
                ),
            ),
        ):
            response = client.post(
                f"/api/v1/forge/sessions/{session.id}/messages",
                json={"content": "anybody home?"},
            )

        assert response.status_code == 409
        assert "not delivered" in str(response.json()).lower()

    @staticmethod
    def _ack_connect(ack_frame: dict | None):
        """Build a fake websockets.connect whose socket replays one broker ACK frame.

        Used by the INV-7 delivery-contract tests: the broker emits a correlated
        user_delivered / user_delivery_failed frame; the REST bridge must map it to the
        right status. The ack_frame's request_id is filled in from the sent user frame so
        it correlates exactly the way the live broker would.
        """

        class _FakeWebSocket:
            def __init__(self) -> None:
                self.sent: list[str] = []
                self._delivered = False

            async def send(self, payload: str) -> None:
                self.sent.append(payload)

            async def recv(self) -> str:
                if ack_frame is None or self._delivered:
                    raise TimeoutError
                self._delivered = True
                req_id = json.loads(self.sent[0])["request_id"]
                return json.dumps({**ack_frame, "request_id": req_id})

        class _FakeConnect:
            def __init__(self) -> None:
                self.ws = _FakeWebSocket()

            def __call__(self, url: str, **kwargs: object):
                ws = self.ws

                class _Ctx:
                    async def __aenter__(self) -> _FakeWebSocket:
                        return ws

                    async def __aexit__(self, exc_type, exc, tb) -> bool:
                        return False

                return _Ctx()

        return _FakeConnect()

    def _post_message(
        self,
        client: TestClient,
        repository: InMemorySessionRepository,
        fake_connect,
        content: str = "hello",
    ):
        session = Session(
            id=uuid4(),
            name="ack-session",
            model="claude-sonnet-4",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
            status=SessionStatus.RUNNING,
            chat_endpoint="ws://localhost:8080/s/ack-session/session",
        )
        asyncio.run(repository.create(session))
        with (
            patch("websockets.asyncio.client.connect", new=fake_connect),
            patch(
                "volundr.adapters.inbound.rest.extract_principal",
                new=AsyncMock(
                    return_value=Principal(
                        user_id="dev-user",
                        email="dev@example.com",
                        tenant_id="default",
                        roles=[],
                    )
                ),
            ),
        ):
            return client.post(
                f"/api/v1/forge/sessions/{session.id}/messages",
                json={"content": content},
            )

    def test_send_message_delivered_ack_returns_200_delivered(
        self,
        client: TestClient,
        repository: InMemorySessionRepository,
    ) -> None:
        """INV-7: a correlated user_delivered ACK is the ONLY success contract — 200
        status=delivered, delivery=delivered."""
        fake_connect = self._ack_connect(
            {"type": "user_delivered", "status": "delivered", "id": "m1"}
        )
        response = self._post_message(client, repository, fake_connect)

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "delivered"
        assert body["delivery"] == "delivered"

    def test_send_message_failed_ack_does_not_report_success(
        self,
        client: TestClient,
        repository: InMemorySessionRepository,
    ) -> None:
        """INV-7: a terminal user_delivery_failed ACK surfaces as a 502 error — the REST
        bridge MUST NOT return a plain success for an undelivered message."""
        fake_connect = self._ack_connect(
            {"type": "user_delivery_failed", "status": "failed", "id": "m1", "error": "wedged"}
        )
        response = self._post_message(client, repository, fake_connect)

        assert response.status_code == 502
        assert "sent" not in str(response.json()).lower()
        assert "wedged" in str(response.json()).lower()

    def test_send_message_no_ack_reports_pending_not_sent(
        self,
        client: TestClient,
        repository: InMemorySessionRepository,
    ) -> None:
        """INV-7: no ACK within the grace -> 202 pending (the broker's retry drives it),
        never a 200 'sent' for an undelivered message."""
        fake_connect = self._ack_connect(None)
        response = self._post_message(client, repository, fake_connect)

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "pending"
        assert body["delivery"] == "pending"
        assert "sent" not in str(body).lower()
        # INV-7 (unconfirmed != delivered): an unACKed message is NEVER reported delivered.
        assert body["status"] != "delivered"
        assert body["delivery"] != "delivered"

    # SRD §11.4 — "message not received" before/after regression. The historical bug was
    # that POST /messages reported success ("sent"/200/delivered) for a message the agent
    # never received. This pins the inverse contract as a guard: the ONLY way to read a
    # delivered/200 response is a real correlated ``user_delivered`` ACK. Every non-delivery
    # outcome — no ACK within the grace, OR a terminal ``user_delivery_failed`` — MUST report
    # a status in {pending, failed}, NEVER 200 and NEVER delivery=="delivered"/"sent".
    @pytest.mark.parametrize(
        ("ack_frame", "expected_status_code", "expected_status"),
        [
            # No ACK arrives within the grace window: still in flight, reported pending.
            (None, 202, "pending"),
            # Terminal transport rejection after bounded retry: reported as a failure (502).
            (
                {"type": "user_delivery_failed", "status": "failed", "id": "m1", "error": "wedged"},
                502,
                "failed",
            ),
        ],
        ids=["no_ack_within_grace", "failed_ack"],
    )
    def test_non_delivered_message_never_reported_as_success(
        self,
        client: TestClient,
        repository: InMemorySessionRepository,
        ack_frame: dict | None,
        expected_status_code: int,
        expected_status: str,
    ) -> None:
        """SRD §11.4: a non-delivered message MUST NOT be reported as a successful send.

        Parametrized over the two "message not received" shapes — (no ack within grace) and
        (failed ack). For both, the response must be in {pending, failed}, must NOT be 200, and
        must NEVER carry delivery=="delivered" or the word "sent". The success contract (200 /
        delivery=="delivered") is reserved exclusively for a real ``user_delivered`` ACK, which
        these cases do not produce."""
        fake_connect = self._ack_connect(ack_frame)
        response = self._post_message(client, repository, fake_connect)

        assert response.status_code == expected_status_code
        assert response.status_code != 200, (
            "§11.4: a non-delivered message must never return a 200 success"
        )
        body = response.json()
        body_text = str(body).lower()
        assert "sent" not in body_text, "§11.4: must never claim the message was 'sent'"

        if expected_status == "pending":
            assert body["status"] == "pending"
            assert body["status"] in {"pending", "failed"}
            assert body.get("delivery") != "delivered"
            return

        # Failure path: 502 error body must signal failure, never a delivered/sent success.
        assert "delivered" not in body_text or "not" in body_text
        assert "wedged" in body_text


class TestSessionLogAggregationProxy:
    """Tests for aggregated session log proxy endpoints."""

    @pytest.mark.asyncio
    async def test_get_session_logs_success_forwards_auth(
        self,
        client: TestClient,
        service: SessionService,
    ) -> None:
        session = await service.create_session(
            "test",
            "claude-sonnet-4",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        await service.start_session(session.id)

        response_payload = {"session_id": str(session.id), "lines": [{"message": "ready"}]}
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.json.return_value = response_payload
        mock_response.raise_for_status.return_value = None

        with patch("volundr.adapters.inbound.rest.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            response = client.get(
                f"/api/v1/forge/sessions/{session.id}/logs?lines=20&level=INFO",
                headers={"Authorization": "Bearer caller-token"},
            )

        assert response.status_code == 200
        assert response.json() == response_payload
        mock_client.get.assert_awaited_once_with(
            f"http://localhost:8080/s/{session.id}/api/logs",
            params={"lines": 20, "level": "INFO"},
            headers={"Authorization": "Bearer caller-token"},
        )

    @pytest.mark.asyncio
    async def test_get_session_logs_aggregate_success(
        self,
        client: TestClient,
        service: SessionService,
    ) -> None:
        session = await service.create_session(
            "test",
            "claude-sonnet-4",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        await service.start_session(session.id)

        response_payload = {
            "session_id": str(session.id),
            "available_participants": [
                {"id": "skuld", "label": "Skuld", "kind": "broker"},
                {"id": "coder", "label": "Coder", "kind": "ravn"},
            ],
            "lines": [
                {
                    "id": "agg-1",
                    "timestamp": "2026-05-01T15:19:51.232000+00:00",
                    "level": "INFO",
                    "participant": "coder",
                    "participant_label": "Coder",
                    "participant_kind": "ravn",
                    "source": "ravn.cli.commands",
                    "message": "mesh: received outcome event_type=code.requested",
                    "sequence": 28,
                    "stream": "logs/coder.log",
                }
            ],
        }
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.json.return_value = response_payload
        mock_response.raise_for_status.return_value = None

        with patch("volundr.adapters.inbound.rest.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            response = client.get(
                f"/api/v1/forge/sessions/{session.id}/logs/aggregate?lines=50&level=WARNING&participants=coder&query=mesh",
                headers={"Authorization": "Bearer caller-token"},
            )

        assert response.status_code == 200
        assert response.json() == response_payload
        mock_client.get.assert_awaited_once_with(
            f"http://localhost:8080/s/{session.id}/api/logs/aggregate",
            params={
                "lines": 50,
                "level": "WARNING",
                "participants": "coder",
                "query": "mesh",
            },
            headers={"Authorization": "Bearer caller-token"},
        )

    @pytest.mark.asyncio
    async def test_get_session_logs_aggregate_falls_back_to_local_workspace_on_404(
        self,
        client: TestClient,
        service: SessionService,
        tmp_path,
    ) -> None:
        workspace = tmp_path / "workspace"
        flock_logs = workspace / ".flock" / "logs"
        flock_logs.mkdir(parents=True)
        (workspace / ".skuld.log").write_text(
            "2026-05-01 15:19:48,121 - skuld.broker - INFO - Starting Skuld broker\n",
            encoding="utf-8",
        )
        (flock_logs / "coder.log").write_text(
            "2026-05-01 15:19:58,326 ravn.drive_loop ERROR "
            "drive_loop: task failed after 3 retries\n",
            encoding="utf-8",
        )

        session = await service.create_session(
            "test",
            "claude-sonnet-4",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        session = session.with_endpoints(
            f"ws://localhost:8080/s/{session.id}/session",
            f"file://{workspace}",
        ).with_status(SessionStatus.RUNNING)
        await service._repository.update(session)

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 404
        request = httpx.Request("GET", f"http://localhost:8080/s/{session.id}/api/logs/aggregate")
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "not found",
            request=request,
            response=mock_response,
        )

        with patch("volundr.adapters.inbound.rest.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            response = client.get(
                f"/api/v1/forge/sessions/{session.id}/logs/aggregate?lines=10&level=DEBUG"
            )

        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == str(session.id)
        assert {participant["id"] for participant in data["available_participants"]} == {
            "skuld",
            "coder",
        }
        assert [line["participant"] for line in data["lines"]] == ["skuld", "coder"]

    @pytest.mark.asyncio
    async def test_get_conversation_falls_back_to_local_workspace_without_archive_service(
        self,
        client: TestClient,
        service: SessionService,
        tmp_path,
    ) -> None:
        workspace = tmp_path / "workspace"
        transcript_dir = workspace / ".skuld"
        transcript_dir.mkdir(parents=True)

        session = await service.create_session(
            "test",
            "claude-sonnet-4",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        (transcript_dir / f"conversation_{session.id}.json").write_text(
            '{"turns":[{"id":"1","role":"assistant","content":"from workspace"}]}',
            encoding="utf-8",
        )
        session = session.with_endpoints(
            f"ws://localhost:8080/s/{session.id}/session",
            f"file://{workspace}",
        ).with_status(SessionStatus.RUNNING)
        await service._repository.update(session)

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 404
        request = httpx.Request("GET", f"http://localhost:8080/s/{session.id}/api/conversation")
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "not found",
            request=request,
            response=mock_response,
        )

        with patch("volundr.adapters.inbound.rest.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            response = client.get(f"/api/v1/forge/sessions/{session.id}/conversation")

        assert response.status_code == 200
        assert response.json()["turns"][0]["content"] == "from workspace"

    @pytest.mark.asyncio
    async def test_get_session_logs_aggregate_returns_503_without_archive_or_file_workspace(
        self,
        client: TestClient,
        service: SessionService,
    ) -> None:
        session = await service.create_session(
            "test",
            "claude-sonnet-4",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        session = session.with_endpoints(
            f"ws://localhost:8080/s/{session.id}/session",
            "https://workspace.example.com/session",
        ).with_status(SessionStatus.RUNNING)
        await service._repository.update(session)

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 404
        request = httpx.Request("GET", f"http://localhost:8080/s/{session.id}/api/logs/aggregate")
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "not found",
            request=request,
            response=mock_response,
        )

        with patch("volundr.adapters.inbound.rest.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            response = client.get(f"/api/v1/forge/sessions/{session.id}/logs/aggregate")

        assert response.status_code == 503
        assert response.json()["detail"] == "Session archive service not available"


class TestWorkflowGateProxy:
    """Tests for native workflow gate proxy endpoints."""

    def test_session_proxy_url_quotes_path_segments(self) -> None:
        """Dynamic path segments should be encoded before proxying to a session pod."""
        assert (
            _session_proxy_url(
                "http://localhost:8080/s/session-123",
                "api",
                "workflow",
                "gates",
                "gate needs review?step=1",
                "resolve",
            )
            == "http://localhost:8080/s/session-123/api/workflow/gates/gate%20needs%20review%3Fstep%3D1/resolve"
        )

    @pytest.mark.asyncio
    async def test_get_workflow_gates_unreachable_pod_reconciles_and_409s(
        self,
        client: TestClient,
        service: SessionService,
    ) -> None:
        """INV-9: an unreachable pod on the gate read path reconciles the row and
        returns a deterministic 409 instead of a misleading 502."""
        session = await service.create_session(
            "test",
            "claude-sonnet-4",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        session = session.with_endpoints(
            f"ws://localhost:8080/s/{session.id}/session",
            f"file:///tmp/{session.id}",
        ).with_status(SessionStatus.RUNNING)
        await service._repository.update(session)

        request = httpx.Request("GET", f"http://localhost:8080/s/{session.id}/api/workflow/gates")

        with patch("volundr.adapters.inbound.rest.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.ConnectError("refused", request=request)
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            response = client.get(f"/api/v1/forge/sessions/{session.id}/workflow/gates")

        assert response.status_code == 409
        assert "no longer reachable" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_resolve_workflow_gate_unreachable_pod_reconciles_and_409s(
        self,
        client: TestClient,
        service: SessionService,
    ) -> None:
        """INV-9: an unreachable pod on the gate resolve path reconciles the row
        and returns a deterministic 409 — never a misleading bad-gateway."""
        session = await service.create_session(
            "test",
            "claude-sonnet-4",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        session = session.with_endpoints(
            f"ws://localhost:8080/s/{session.id}/session",
            f"file:///tmp/{session.id}",
        ).with_status(SessionStatus.RUNNING)
        await service._repository.update(session)

        request = httpx.Request(
            "POST", f"http://localhost:8080/s/{session.id}/api/workflow/gates/g1/resolve"
        )

        with (
            patch(
                "volundr.adapters.inbound.rest.extract_principal",
                new=AsyncMock(
                    return_value=Principal(
                        user_id="dev-user",
                        email="dev@example.com",
                        tenant_id="default",
                        roles=[],
                    )
                ),
            ),
            patch("volundr.adapters.inbound.rest.httpx.AsyncClient") as mock_client_cls,
        ):
            mock_client = AsyncMock()
            mock_client.post.side_effect = httpx.ConnectError("refused", request=request)
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            response = client.post(
                f"/api/v1/forge/sessions/{session.id}/workflow/gates/g1/resolve",
                json={"decision": "approved", "notes": "", "source": "human"},
            )

        assert response.status_code == 409
        assert "not resolved" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_resolve_workflow_gate_quotes_gate_id(
        self,
        client: TestClient,
        service: SessionService,
    ) -> None:
        """Route-level gate resolution should proxy using an encoded gate path segment."""
        session = await service.create_session(
            "test",
            "claude-sonnet-4",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        await service.start_session(session.id)

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.json.return_value = {"status": "resolved"}
        mock_response.raise_for_status.return_value = None

        with (
            patch(
                "volundr.adapters.inbound.rest.extract_principal",
                new=AsyncMock(
                    return_value=Principal(
                        user_id="dev-user",
                        email="dev@example.com",
                        tenant_id="default",
                        roles=[],
                    )
                ),
            ),
            patch("volundr.adapters.inbound.rest.httpx.AsyncClient") as mock_client_cls,
        ):
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            response = client.post(
                f"/api/v1/forge/sessions/{session.id}/workflow/gates/prd%20review%3Fstep%3D1/resolve",
                json={"decision": "approved", "notes": "looks good", "source": "human"},
                headers={"x-niuu-workflow-gate-intent": "resolve"},
            )

        assert response.status_code == 200
        assert response.json() == {"status": "resolved"}
        mock_client.post.assert_awaited_once_with(
            f"http://localhost:8080/s/{session.id}/api/workflow/gates/prd%20review%3Fstep%3D1/resolve",
            headers={"x-niuu-workflow-gate-intent": "resolve"},
            json={"decision": "approved", "notes": "looks good", "source": "human"},
        )

    @pytest.mark.asyncio
    async def test_get_conversation_returns_503_without_archive_or_file_workspace(
        self,
        client: TestClient,
        service: SessionService,
    ) -> None:
        session = await service.create_session(
            "test",
            "claude-sonnet-4",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        session = session.with_endpoints(
            f"ws://localhost:8080/s/{session.id}/session",
            "https://workspace.example.com/session",
        ).with_status(SessionStatus.RUNNING)
        await service._repository.update(session)

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 404
        request = httpx.Request(
            "GET",
            f"http://localhost:8080/s/{session.id}/api/conversation/history",
        )
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "not found",
            request=request,
            response=mock_response,
        )

        with patch("volundr.adapters.inbound.rest.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            response = client.get(f"/api/v1/forge/sessions/{session.id}/conversation")

        assert response.status_code == 503
        assert response.json()["detail"] == "Session archive service not available"


class TestHelpRequestProxy:
    """Peer help requests (agent questions) proxy to the session pod."""

    @pytest.mark.asyncio
    async def test_get_help_requests_proxies_to_session_pod(
        self,
        client: TestClient,
        service: SessionService,
    ) -> None:
        session = await service.create_session(
            "test",
            "claude-sonnet-4",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        session = session.with_endpoints(
            f"ws://localhost:8080/s/{session.id}/session",
            f"file:///tmp/{session.id}",
        ).with_status(SessionStatus.RUNNING)
        await service._repository.update(session)

        request = httpx.Request("GET", f"http://localhost:8080/s/{session.id}/api/help/requests")
        payload = {
            "requests": [
                {
                    "id": "help-1",
                    "status": "pending",
                    "persona": "specification-framer",
                    "summary": "Which namespaces are in scope?",
                }
            ]
        }

        with patch("volundr.adapters.inbound.rest.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = httpx.Response(200, json=payload, request=request)
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            response = client.get(f"/api/v1/forge/sessions/{session.id}/help/requests")

        assert response.status_code == 200
        assert response.json() == payload
        proxied_url = str(mock_client.get.call_args.args[0])
        assert proxied_url.endswith("/api/help/requests")

    @pytest.mark.asyncio
    async def test_get_help_requests_unknown_session_404s(self, client: TestClient) -> None:
        response = client.get(
            "/api/v1/forge/sessions/00000000-0000-0000-0000-000000000000/help/requests"
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_get_help_requests_enforces_session_access(
        self,
        client: TestClient,
        service: SessionService,
    ) -> None:
        session = await service.create_session(
            "private",
            "claude-sonnet-4",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        with patch.object(
            service,
            "_check_access",
            new=AsyncMock(side_effect=SessionAccessDeniedError(session.id, "other-user")),
        ):
            response = client.get(f"/api/v1/forge/sessions/{session.id}/help/requests")

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_answer_help_request_proxies_post(
        self,
        client: TestClient,
        service: SessionService,
    ) -> None:
        session = await service.create_session(
            "test",
            "claude-sonnet-4",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        session = session.with_endpoints(
            f"ws://localhost:8080/s/{session.id}/session",
            f"file:///tmp/{session.id}",
        ).with_status(SessionStatus.RUNNING)
        await service._repository.update(session)

        request = httpx.Request(
            "POST", f"http://localhost:8080/s/{session.id}/api/help/requests/help-1/answer"
        )

        with (
            patch(
                "volundr.adapters.inbound.rest.extract_principal",
                new=AsyncMock(
                    return_value=Principal(
                        user_id="dev-user",
                        email="dev@example.com",
                        tenant_id="default",
                        roles=[],
                    )
                ),
            ),
            patch("volundr.adapters.inbound.rest.httpx.AsyncClient") as mock_client_cls,
        ):
            mock_client = AsyncMock()
            mock_client.post.return_value = httpx.Response(
                200,
                json={"status": "answered", "message_id": "msg-1"},
                request=request,
            )
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            response = client.post(
                f"/api/v1/forge/sessions/{session.id}/help/requests/help-1/answer",
                json={"answer": "All namespaces, read-only.", "source": "a2a"},
            )

        assert response.status_code == 200
        assert response.json() == {"status": "answered", "message_id": "msg-1"}
        assert mock_client.post.call_args.kwargs["json"] == {
            "answer": "All namespaces, read-only.",
            "source": "a2a",
        }

    @pytest.mark.asyncio
    async def test_answer_help_request_unreachable_pod_409s(
        self,
        client: TestClient,
        service: SessionService,
    ) -> None:
        session = await service.create_session(
            "test",
            "claude-sonnet-4",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        session = session.with_endpoints(
            f"ws://localhost:8080/s/{session.id}/session",
            f"file:///tmp/{session.id}",
        ).with_status(SessionStatus.RUNNING)
        await service._repository.update(session)

        request = httpx.Request(
            "POST", f"http://localhost:8080/s/{session.id}/api/help/requests/help-1/answer"
        )

        with (
            patch(
                "volundr.adapters.inbound.rest.extract_principal",
                new=AsyncMock(
                    return_value=Principal(
                        user_id="dev-user",
                        email="dev@example.com",
                        tenant_id="default",
                        roles=[],
                    )
                ),
            ),
            patch("volundr.adapters.inbound.rest.httpx.AsyncClient") as mock_client_cls,
        ):
            mock_client = AsyncMock()
            mock_client.post.side_effect = httpx.ConnectError("refused", request=request)
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            response = client.post(
                f"/api/v1/forge/sessions/{session.id}/help/requests/help-1/answer",
                json={"answer": "answer", "source": "a2a"},
            )

        assert response.status_code == 409
        assert "not answered" in response.json()["detail"].lower()


class TestFeatureFlags:
    """Tests for GET /api/v1/forge/feature-flags."""

    def test_feature_flags_returns_local_mounts_flag(self, client: TestClient):
        """Returns feature flags including local_mounts_enabled."""
        response = client.get("/api/v1/forge/feature-flags")
        assert response.status_code == 200
        data = response.json()
        assert "local_mounts_enabled" in data
        assert isinstance(data["local_mounts_enabled"], bool)

    def test_feature_flags_lists_allowed_mount_prefixes(self, client: TestClient):
        """Exposes the configured mount prefix allowlist for UI/automation."""
        response = client.get("/api/v1/forge/feature-flags")
        assert response.status_code == 200
        data = response.json()
        assert "local_mounts_allowed_prefixes" in data
        assert isinstance(data["local_mounts_allowed_prefixes"], list)


class TestStatsResponse:
    """Tests for StatsResponse model."""

    def test_stats_response_fields(self):
        """StatsResponse has all required fields."""
        stats = StatsResponse(
            active_sessions=3,
            total_sessions=10,
            sessions_today=2,
            tokens_today=50000,
            local_tokens=20000,
            cloud_tokens=30000,
            cost_today=1.50,
            sparklines={"sessionsToday": [1.0, 2.0]},
        )
        assert stats.active_sessions == 3
        assert stats.total_sessions == 10
        assert stats.sessions_today == 2
        assert stats.tokens_today == 50000
        assert stats.local_tokens == 20000
        assert stats.cloud_tokens == 30000
        assert stats.cost_today == 1.50
        assert stats.sparklines == {"sessionsToday": [1.0, 2.0]}

    def test_stats_response_defaults_to_zero_values(self):
        """StatsResponse can represent an empty stats payload."""
        stats = StatsResponse()
        assert stats.active_sessions == 0
        assert stats.total_sessions == 0
        assert stats.sessions_today == 0
        assert stats.tokens_today == 0
        assert stats.local_tokens == 0
        assert stats.cloud_tokens == 0
        assert stats.cost_today == 0.0
        assert stats.sparklines == {}


class TestGetStats:
    """Tests for GET /api/v1/forge/stats."""

    def test_get_stats_success(self, client: TestClient):
        """Returns aggregate statistics."""
        response = client.get("/api/v1/forge/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["active_sessions"] == 3
        assert data["total_sessions"] == 10
        assert data["sessions_today"] == 0
        assert data["tokens_today"] == 50000
        assert data["local_tokens"] == 20000
        assert data["cloud_tokens"] == 30000
        assert data["cost_today"] == 1.50
        assert data["sparklines"] == {}

    def test_get_stats_has_all_fields(self, client: TestClient):
        """Stats response contains all expected fields."""
        response = client.get("/api/v1/forge/stats")
        assert response.status_code == 200
        data = response.json()
        assert "active_sessions" in data
        assert "total_sessions" in data
        assert "sessions_today" in data
        assert "tokens_today" in data
        assert "local_tokens" in data
        assert "cloud_tokens" in data
        assert "cost_today" in data
        assert "sparklines" in data

    def test_get_stats_without_service(self, service: SessionService):
        """Returns 503 when stats service is not available."""
        app = FastAPI()
        router = create_router(service, stats_service=None)
        app.include_router(router)
        with TestClient(app) as client:
            response = client.get("/api/v1/forge/stats")
        assert response.status_code == 503
        assert "not available" in response.json()["detail"].lower()

    def test_get_stats_with_zero_values(self, service: SessionService):
        """Returns stats with zero values."""
        stats_repo = InMemoryStatsRepository()
        stats_svc = StatsService(stats_repo)
        app = FastAPI()
        router = create_router(service, stats_svc)
        app.include_router(router)
        with TestClient(app) as client:
            response = client.get("/api/v1/forge/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["active_sessions"] == 0
        assert data["total_sessions"] == 0
        assert data["tokens_today"] == 0
        assert data["cost_today"] == 0.0


class TestListRepos:
    """Tests for GET /api/v1/niuu/repos."""

    @pytest.fixture
    def repos(self) -> list[RepoInfo]:
        """Sample repos."""
        return [
            RepoInfo(
                provider=GitProviderType.GITHUB,
                org="my-org",
                name="repo1",
                clone_url="https://github.com/my-org/repo1.git",
                url="https://github.com/my-org/repo1",
            ),
            RepoInfo(
                provider=GitProviderType.GITHUB,
                org="my-org",
                name="repo2",
                clone_url="https://github.com/my-org/repo2.git",
                url="https://github.com/my-org/repo2",
            ),
        ]

    @pytest.fixture
    def repo_service(self, repos: list[RepoInfo]) -> RepoService:
        """Create a RepoService with mock repos."""
        gh = MockGitProvider(
            name="GitHub",
            provider_type=GitProviderType.GITHUB,
            orgs=("my-org",),
            repos=repos,
        )
        registry = MockGitRegistry([gh])
        return RepoService(registry)

    @pytest.fixture
    def repos_client(self, repo_service: RepoService) -> TestClient:
        """Create a test client with niuu repos router."""
        from niuu.adapters.inbound.rest_repos import create_repos_router

        app = FastAPI()
        app.include_router(create_repos_router(repo_service))
        client = TestClient(app)
        yield client
        client.close()

    def test_list_repos_success(self, repos_client: TestClient):
        """Returns repos grouped by provider name."""
        response = repos_client.get("/api/v1/niuu/repos")
        assert response.status_code == 200
        data = response.json()
        assert "GitHub" in data
        assert len(data["GitHub"]) == 2
        assert data["GitHub"][0]["name"] == "repo1"
        assert data["GitHub"][0]["provider"] == "github"
        assert data["GitHub"][0]["org"] == "my-org"
        assert data["GitHub"][1]["name"] == "repo2"

    def test_list_repos_without_service(self):
        """Returns 503 when repo service is not available."""
        from niuu.adapters.inbound.rest_repos import create_repos_router

        app = FastAPI()
        app.include_router(create_repos_router(None))
        with TestClient(app) as client:
            response = client.get("/api/v1/niuu/repos")
        assert response.status_code == 503
        assert "not available" in response.json()["detail"].lower()

    def test_list_repos_empty_when_no_orgs(self):
        """Returns empty dict when no providers have orgs configured."""
        from niuu.adapters.inbound.rest_repos import create_repos_router

        gh = MockGitProvider(name="GitHub")
        registry = MockGitRegistry([gh])
        repo_service = RepoService(registry)
        app = FastAPI()
        app.include_router(create_repos_router(repo_service))
        with TestClient(app) as client:
            response = client.get("/api/v1/niuu/repos")
        assert response.status_code == 200
        assert response.json() == {}
