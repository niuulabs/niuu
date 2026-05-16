"""Tests for the REST adapter."""

import asyncio

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

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
    create_router,
)
from volundr.config import LocalMountsConfig
from volundr.domain.models import GitProviderType, GitSource, RepoInfo, Session, SessionStatus
from volundr.domain.services import RepoService, SessionService, StatsService
from niuu.domain.models import Principal


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
    """Tests for POST /api/v1/volundr/sessions/{id}/messages."""

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
                    async def __aenter__(self_inner) -> _FakeWebSocket:
                        return ws

                    async def __aexit__(self_inner, exc_type, exc, tb) -> bool:
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
                f"/api/v1/volundr/sessions/{session.id}/messages",
                json={"content": "hello from rest"},
            )

        assert response.status_code == 200
        assert fake_connect.calls == [
            ("ws://localhost:8080/s/message-session/session", {"open_timeout": 10})
        ]
        assert fake_connect.ws.sent == ['{"type": "user", "content": "hello from rest"}']


class TestSessionLogAggregationProxy:
    """Tests for aggregated session log proxy endpoints."""

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
                f"/api/v1/forge/sessions/{session.id}/logs/aggregate?lines=50&level=WARNING&participants=coder&query=mesh"
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

            response = client.get(f"/api/v1/volundr/sessions/{session.id}/conversation")

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

            response = client.get(f"/api/v1/volundr/sessions/{session.id}/logs/aggregate")

        assert response.status_code == 503
        assert response.json()["detail"] == "Session archive service not available"

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

            response = client.get(f"/api/v1/volundr/sessions/{session.id}/conversation")

        assert response.status_code == 503
        assert response.json()["detail"] == "Session archive service not available"


class TestFeatureFlags:
    """Tests for GET /api/v1/forge/feature-flags."""

    def test_feature_flags_returns_local_mounts_flag(self, client: TestClient):
        """Returns feature flags including local_mounts_enabled."""
        response = client.get("/api/v1/forge/feature-flags")
        assert response.status_code == 200
        data = response.json()
        assert "local_mounts_enabled" in data
        assert isinstance(data["local_mounts_enabled"], bool)


class TestListModels:
    """Tests for GET /api/v1/forge/models."""

    def test_list_models_success(self, client: TestClient, pricing: InMemoryPricingProvider):
        """Returns list of available models."""
        response = client.get("/api/v1/forge/models")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == len(pricing.list_models())
        assert all("id" in m for m in data)
        assert all("name" in m for m in data)
        assert all("description" in m for m in data)

    def test_list_models_contains_expected(self, client: TestClient):
        """Models list contains expected models."""
        response = client.get("/api/v1/forge/models")
        data = response.json()
        model_ids = [m["id"] for m in data]
        assert "claude-sonnet-4-20250514" in model_ids
        assert "claude-opus-4-20250514" in model_ids

    def test_list_models_has_extended_fields(self, client: TestClient):
        """Models include provider, tier, color, and pricing."""
        response = client.get("/api/v1/forge/models")
        assert response.status_code == 200
        data = response.json()

        for model in data:
            assert "provider" in model
            assert "tier" in model
            assert "color" in model
            assert "cost_per_million_tokens" in model
            assert "vram_required" in model

    def test_list_models_cloud_has_pricing(self, client: TestClient):
        """Cloud models have pricing information."""
        response = client.get("/api/v1/forge/models")
        data = response.json()

        cloud_models = [m for m in data if m["provider"] == "cloud"]
        assert len(cloud_models) > 0

        for model in cloud_models:
            assert model["cost_per_million_tokens"] is not None
            assert model["cost_per_million_tokens"] > 0

    def test_list_models_local_has_vram(self, client: TestClient):
        """Local models have VRAM requirements."""
        response = client.get("/api/v1/forge/models")
        data = response.json()

        local_models = [m for m in data if m["provider"] == "local"]
        assert len(local_models) > 0

        for model in local_models:
            assert model["vram_required"] is not None
            assert model["cost_per_million_tokens"] is None

    def test_list_models_without_provider(
        self, service: SessionService, stats_service: StatsService
    ):
        """Returns 503 when pricing provider is not available."""
        app = FastAPI()
        router = create_router(service, stats_service, pricing_provider=None)
        app.include_router(router)
        with TestClient(app) as client:
            response = client.get("/api/v1/forge/models")
        assert response.status_code == 503
        assert "not available" in response.json()["detail"].lower()


class TestStatsResponse:
    """Tests for StatsResponse model."""

    def test_stats_response_fields(self):
        """StatsResponse has all required fields."""
        stats = StatsResponse(
            active_sessions=3,
            total_sessions=10,
            tokens_today=50000,
            local_tokens=20000,
            cloud_tokens=30000,
            cost_today=1.50,
        )
        assert stats.active_sessions == 3
        assert stats.total_sessions == 10
        assert stats.tokens_today == 50000
        assert stats.local_tokens == 20000
        assert stats.cloud_tokens == 30000
        assert stats.cost_today == 1.50


class TestGetStats:
    """Tests for GET /api/v1/forge/stats."""

    def test_get_stats_success(self, client: TestClient):
        """Returns aggregate statistics."""
        response = client.get("/api/v1/forge/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["active_sessions"] == 3
        assert data["total_sessions"] == 10
        assert data["tokens_today"] == 50000
        assert data["local_tokens"] == 20000
        assert data["cloud_tokens"] == 30000
        assert data["cost_today"] == 1.50

    def test_get_stats_has_all_fields(self, client: TestClient):
        """Stats response contains all expected fields."""
        response = client.get("/api/v1/forge/stats")
        assert response.status_code == 200
        data = response.json()
        assert "active_sessions" in data
        assert "total_sessions" in data
        assert "tokens_today" in data
        assert "local_tokens" in data
        assert "cloud_tokens" in data
        assert "cost_today" in data

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
