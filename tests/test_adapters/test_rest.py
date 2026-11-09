"""Tests for the REST adapter."""

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from niuu.domain.guild_membership import GuildInstance, GuildMembership
from niuu.domain.models import Principal
from niuu.domain.peer_message import PeerAddress, PeerKind, PeerMessage, ReplyKind
from tests.conftest import (
    InMemoryPricingProvider,
    InMemorySessionRepository,
    InMemoryStatsRepository,
    MockGitProvider,
    MockGitRegistry,
    MockPodManager,
    make_session_participant_service,
)
from volundr.adapters.inbound.rest import (
    SessionCreate,
    SessionResponse,
    SessionUpdate,
    StatsResponse,
    _session_proxy_url,
    create_router,
)
from volundr.adapters.outbound.k8s_storage import InMemoryStorageAdapter
from volundr.config import LocalMountsConfig
from volundr.domain.execution_catalog import (
    ExecutionCatalogError,
    ExecutionCatalogIntegrityError,
    ExecutionCatalogNotFoundError,
    ExecutionPlanMismatchError,
    ExecutionSelectionError,
)
from volundr.domain.models import GitProviderType, GitSource, RepoInfo, Session, SessionStatus
from volundr.domain.ports import SessionCapacity
from volundr.domain.services import RepoService, SessionService, StatsService
from volundr.domain.services.session import SessionAccessDeniedError
from volundr.domain.session_startup import SessionStartup, StartupStage, StartupStageState

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
def stats_service(stats_repo: InMemoryStatsRepository, service: SessionService) -> StatsService:
    """Create a stats service with test repository."""
    return StatsService(stats_repo, service)


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
    router = create_router(
        service,
        stats_service,
        pricing_provider=pricing,
        session_participant_service=make_session_participant_service(service),
    )
    app.include_router(router)

    # Minimal settings stub for endpoints that read app.state.settings
    class _SettingsStub:
        local_mounts = LocalMountsConfig()

    app.state.settings = _SettingsStub()
    app.state.admin_settings = {}
    app.state.storage = InMemoryStorageAdapter()
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

    def test_from_session_carries_the_launch_stages(self):
        """The stages of the latest launch ride on the session, as JSON."""
        started = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
        startup = SessionStartup(
            started_at=started,
            handed_off_at=started,
            stages=(
                StartupStage(
                    key="schedule",
                    label="Scheduling",
                    state=StartupStageState.DONE,
                    detail="On node hnoss",
                    started_at=started,
                    finished_at=started,
                ),
                StartupStage(
                    key="containers",
                    label="Starting containers",
                    state=StartupStageState.PENDING,
                ),
            ),
        )
        session = Session(
            id=uuid4(),
            name="Test",
            model="claude-sonnet-4",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
            status=SessionStatus.STARTING,
            startup=startup,
        )

        response = SessionResponse.from_session(session)

        assert response.startup == startup
        body = response.model_dump(mode="json")["startup"]
        assert body["handed_off_at"] == "2026-11-06T12:00:00Z"
        assert [(s["key"], s["state"], s["detail"]) for s in body["stages"]] == [
            ("schedule", "done", "On node hnoss"),
            ("containers", "pending", None),
        ]

    def test_from_session_without_a_recorded_launch_has_null_startup(self):
        session = Session(
            id=uuid4(),
            name="Test",
            model="claude-sonnet-4",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )

        response = SessionResponse.from_session(session)

        assert response.startup is None
        assert response.model_dump(mode="json")["startup"] is None

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
        app.include_router(
            create_router(
                service,
                stats_service,
                prefix="/api/v1/forge",
                session_participant_service=make_session_participant_service(service),
            )
        )

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

    def test_create_session_refuses_the_removed_ravn_flock_workload(
        self, client: TestClient, pod_manager: MockPodManager
    ):
        """A removed workload type is a 422 naming the replacement, never a plain session."""
        response = client.post(
            "/api/v1/forge/sessions",
            json={
                "name": "flock",
                "model": "claude-sonnet-4",
                "source": {"type": "git", "repo": "https://github.com/org/repo", "branch": "main"},
                "workload_type": "ravn_flock",
            },
        )
        assert response.status_code == 422
        assert (
            "workload type ravn_flock was removed: workflow steps will run on "
            "harness sessions (NIU-1198)"
        ) in response.text
        assert pod_manager.start_calls == []
        listed = client.get("/api/v1/forge/sessions").json()
        items = listed if isinstance(listed, list) else listed.get("items", [])
        assert "flock" not in [s["name"] for s in items]

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

    async def test_start_refuses_a_stored_ravn_flock_session(
        self, client: TestClient, service: SessionService, pod_manager: MockPodManager
    ):
        session = await service.create_session(
            "Flock",
            "claude-sonnet-4",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        await service._repository.update(session.model_copy(update={"workload_type": "ravn_flock"}))

        response = client.post(f"/api/v1/forge/sessions/{session.id}/start")

        assert response.status_code == 422
        assert response.json()["detail"] == (
            "workload type ravn_flock was removed: workflow steps will run on "
            "harness sessions (NIU-1198)"
        )
        assert pod_manager.start_calls == []

    @pytest.mark.parametrize(
        ("error", "expected_status", "expected_detail"),
        [
            (
                ExecutionSelectionError("conflicting selectors"),
                422,
                "Invalid compute execution selection",
            ),
            (
                ExecutionCatalogNotFoundError("unknown execution"),
                422,
                "Invalid compute execution selection",
            ),
            (
                ExecutionPlanMismatchError("digest changed"),
                409,
                "Compute execution selection conflicts with its durable allocation",
            ),
            (
                ExecutionCatalogIntegrityError("artifact changed"),
                503,
                "Compute execution catalog is unavailable",
            ),
            (
                ExecutionCatalogError("cannot read catalog"),
                503,
                "Compute execution catalog is unavailable",
            ),
        ],
    )
    def test_create_session_maps_execution_catalog_errors(
        self,
        client: TestClient,
        service: SessionService,
        error: Exception,
        expected_status: int,
        expected_detail: str,
    ):
        with patch.object(service, "start_session", AsyncMock(side_effect=error)):
            response = client.post(
                "/api/v1/forge/sessions",
                json={"name": "catalog-error", "model": "test", "source": {"type": "git"}},
            )

        assert response.status_code == expected_status
        assert response.json()["detail"] == expected_detail

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

    async def test_delete_session_reports_undeleted_compute_as_unavailable(
        self, client: TestClient, service: SessionService, pod_manager: MockPodManager
    ):
        """Compute that cannot be removed answers 503 with the remedy and keeps the session."""
        session = await service.create_session("Test", "claude-sonnet-4")
        pod_manager.stop_success = False

        response = client.delete(f"/api/v1/forge/sessions/{session.id}")

        assert response.status_code == 503
        assert response.json()["detail"] == "Pod stop failed"
        assert client.get(f"/api/v1/forge/sessions/{session.id}").status_code == 200

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

    @pytest.mark.parametrize(
        ("error", "expected_status", "expected_detail"),
        [
            (
                ExecutionSelectionError("conflicting selectors"),
                422,
                "Invalid compute execution selection",
            ),
            (
                ExecutionCatalogNotFoundError("unknown execution"),
                422,
                "Invalid compute execution selection",
            ),
            (
                ExecutionPlanMismatchError("digest changed"),
                409,
                "Compute execution selection conflicts with its durable allocation",
            ),
            (
                ExecutionCatalogIntegrityError("artifact changed"),
                503,
                "Compute execution catalog is unavailable",
            ),
            (
                ExecutionCatalogError("cannot read catalog"),
                503,
                "Compute execution catalog is unavailable",
            ),
        ],
    )
    async def test_start_session_maps_execution_catalog_errors(
        self,
        client: TestClient,
        service: SessionService,
        error: Exception,
        expected_status: int,
        expected_detail: str,
    ):
        session = await service.create_session("Catalog error", "test")
        with patch.object(service, "start_session", AsyncMock(side_effect=error)):
            response = client.post(f"/api/v1/forge/sessions/{session.id}/start")

        assert response.status_code == expected_status
        assert response.json()["detail"] == expected_detail

    @pytest.mark.parametrize("error", [ValueError("unrelated"), RuntimeError("unrelated")])
    async def test_start_session_does_not_swallow_unrelated_errors(
        self,
        client: TestClient,
        service: SessionService,
        error: Exception,
    ):
        session = await service.create_session("Unrelated error", "test")
        with (
            patch.object(service, "start_session", AsyncMock(side_effect=error)),
            pytest.raises(type(error), match="unrelated"),
        ):
            client.post(f"/api/v1/forge/sessions/{session.id}/start")

    async def test_restart_forwards_selected_integrations(self, client, service):
        session = await service.create_session(
            "Test",
            "claude",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        with patch.object(service, "start_session", AsyncMock(return_value=session)) as start:
            response = client.post(
                f"/api/v1/forge/sessions/{session.id}/start",
                json={"integration_ids": ["existing-ai", "new-github"]},
            )
        assert response.status_code == 200
        assert start.call_args.kwargs["integration_ids"] == ["existing-ai", "new-github"]

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

    async def test_start_session_without_a_free_slot_is_409_with_the_remedy(
        self, client: TestClient, service: SessionService
    ):
        """A restart with no capacity is refused up front, and the answer says
        where the limit is raised; the session is not flipped to starting."""
        session = await service.create_session(
            "Test",
            "claude-sonnet-4",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        full = SessionCapacity(
            limit=4, active=4, remedy="raise it in Settings → Runtime (/settings/runtime/sessions)"
        )
        with patch.object(type(service._pod_manager), "capacity", AsyncMock(return_value=full)):
            response = client.post(f"/api/v1/forge/sessions/{session.id}/start")
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert "4 of 4 sessions are running" in detail
        assert "/settings/runtime/sessions" in detail
        assert (await service.get_session(session.id)).status == session.status

    def test_create_session_without_a_free_slot_is_409_and_creates_nothing(
        self, client: TestClient, service: SessionService
    ):
        full = SessionCapacity(limit=2, active=2, remedy="raise pod_manager.max_concurrent")
        with patch.object(type(service._pod_manager), "capacity", AsyncMock(return_value=full)):
            response = client.post(
                "/api/v1/forge/sessions",
                json={
                    "name": "no-room",
                    "model": "claude-sonnet-4",
                    "source": {
                        "type": "git",
                        "repo": "https://github.com/org/repo",
                        "branch": "main",
                    },
                },
            )
        assert response.status_code == 409
        assert "2 of 2 sessions are running" in response.json()["detail"]
        listed = client.get("/api/v1/forge/sessions").json()
        items = listed if isinstance(listed, list) else listed.get("items", [])
        assert "no-room" not in [s["name"] for s in items]


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
            authed = client.post(
                f"/api/v1/forge/sessions/{session.id}/messages",
                json={"content": "hello again"},
                headers={"Authorization": "Bearer caller-jwt"},
            )

        # The caller's bearer goes to the broker socket as a header, never in the URL:
        # Envoy reads no `access_token` parameter, and URLs reach access logs.
        assert authed.status_code == 202
        authed_url, authed_kwargs = fake_connect.calls.pop()
        assert "caller-jwt" not in authed_url
        assert authed_kwargs["additional_headers"] == {"Authorization": "Bearer caller-jwt"}
        fake_connect.ws.sent.pop()

        # INV-7: no delivery ACK arrives within the grace (recv times out), so the
        # broker has ACCEPTED the message but not yet confirmed it reached the agent.
        # The contract reports 202 "pending" (NOT a 200 "sent") — its retry drives it.
        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "pending"
        assert body["delivery"] == "pending"
        assert fake_connect.calls == [
            (
                "ws://localhost:8080/s/message-session/session"
                "?history=recent&history_protocol=2&history_delivery=none",
                {"open_timeout": 10},
            )
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
        request_id: str | None = None,
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
                json={"content": content, **({"request_id": request_id} if request_id else {})},
            )

    def _running_session(self, repository, name: str) -> Session:
        session = Session(
            id=uuid4(),
            name=name,
            model="claude-sonnet-4",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
            status=SessionStatus.RUNNING,
            chat_endpoint=f"ws://localhost:8080/s/{name}/session",
            owner_id="dev-user",
        )
        asyncio.run(repository.create(session))
        return session

    def _post_as_session(self, client, repository, body: dict, *, scopes_user="dev-user"):
        """POST a message to a target session with a verified session credential."""
        caller = self._running_session(repository, "sender-session")
        target = self._running_session(repository, "target-session")
        principal = Principal(
            user_id=scopes_user,
            email="",
            tenant_id="default",
            roles=[],
            token_use="forge_session",
            bound_session_id=str(caller.id),
        )
        fake = self._ack_connect({"type": "user_delivered", "status": "delivered"})
        with (
            patch("websockets.asyncio.client.connect", new=fake),
            patch(
                "volundr.adapters.inbound.rest.extract_principal",
                new=AsyncMock(return_value=principal),
            ),
        ):
            response = client.post(f"/api/v1/forge/sessions/{target.id}/messages", json=body)
        return response, fake, caller, target

    def test_a_session_sender_is_stamped_into_a_peer_message_envelope(self, client, repository):
        response, fake, caller, target = self._post_as_session(
            client, repository, {"content": "rebase please", "request_id": "req-1"}
        )

        assert response.status_code == 200
        frame = json.loads(fake.ws.sent[0])
        assert frame["type"] == "peer_message" and frame["request_id"] == "req-1"
        envelope = PeerMessage.from_wire(frame["envelope"])
        assert envelope.content == "rebase please"
        assert (envelope.sender.kind, envelope.sender.id, envelope.sender.name) == (
            PeerKind.SESSION,
            str(caller.id),
            "sender-session",
        )
        assert (envelope.recipient.id, envelope.recipient.name) == (
            str(target.id),
            "target-session",
        )
        assert envelope.reply is not None
        assert (envelope.reply.kind, envelope.reply.target) == (ReplyKind.SESSION, str(caller.id))
        assert envelope.relayed_by is None and envelope.room_id == ""
        assert envelope.urgent is False and envelope.in_reply_to == ""

    def test_a_retry_with_the_same_request_id_carries_the_same_message_id(self, client, repository):
        caller = self._running_session(repository, "sender-session")
        target = self._running_session(repository, "target-session")
        principal = Principal(
            user_id="dev-user",
            email="",
            tenant_id="default",
            roles=[],
            token_use="forge_session",
            bound_session_id=str(caller.id),
        )
        ids = []
        for _ in range(2):
            fake = self._ack_connect({"type": "user_delivered", "status": "delivered"})
            with (
                patch("websockets.asyncio.client.connect", new=fake),
                patch(
                    "volundr.adapters.inbound.rest.extract_principal",
                    new=AsyncMock(return_value=principal),
                ),
            ):
                client.post(
                    f"/api/v1/forge/sessions/{target.id}/messages",
                    json={"content": "same", "request_id": "retry-1"},
                )
            ids.append(json.loads(fake.ws.sent[0])["envelope"]["message_id"])

        assert ids[0] == ids[1]

    def test_the_body_adds_in_reply_to_and_urgent_only(self, client, repository):
        response, fake, _, _ = self._post_as_session(
            client, repository, {"content": "ok", "in_reply_to": "m-7", "urgent": True}
        )

        assert response.status_code == 200
        envelope = json.loads(fake.ws.sent[0])["envelope"]
        assert envelope["in_reply_to"] == "m-7" and envelope["urgent"] is True

    @pytest.mark.parametrize(
        "forged",
        [
            {"sender": {"kind": "person", "id": "boss", "name": "The Boss", "node": ""}},
            {"relayed_by": {"kind": "session", "id": "x", "name": "x", "node": ""}},
            {"recipient": {"kind": "session", "id": "x", "name": "x", "node": ""}},
            {"sent_at": "2020-01-01T00:00:00+00:00"},
            {"message_id": "chosen-by-sender"},
        ],
    )
    def test_a_session_cannot_forge_what_forge_stamps(self, client, repository, forged):
        response, fake, _, _ = self._post_as_session(
            client, repository, {"content": "I am the boss", **forged}
        )

        assert response.status_code == 400
        assert "stamped by Forge" in response.json()["detail"]
        assert fake.ws.sent == []

    @pytest.mark.parametrize(
        "bad", [{"in_reply_to": 5}, {"urgent": "yes"}, {"peer": {"speaker": 1}}, {"peer": "x"}]
    )
    def test_a_malformed_envelope_field_is_a_400(self, client, repository, bad):
        response, fake, _, _ = self._post_as_session(client, repository, {"content": "x", **bad})

        assert response.status_code == 400
        assert fake.ws.sent == []

    def test_a_room_host_relays_the_speaker_and_forge_stamps_the_host(self, client, repository):
        peer = {
            "speaker": {"kind": "person", "id": "human:jozef", "name": "Jozef", "node": ""},
            "room_id": "huddle-1",
            "reply": {"kind": "room", "target": "host-session", "node": "thor"},
        }
        response, fake, caller, _ = self._post_as_session(
            client, repository, {"content": "ship it", "peer": peer, "request_id": "room-1"}
        )

        assert response.status_code == 200
        envelope = PeerMessage.from_wire(json.loads(fake.ws.sent[0])["envelope"])
        assert (envelope.sender.kind, envelope.sender.id) == (PeerKind.PERSON, "human:jozef")
        assert envelope.room_id == "huddle-1"
        assert envelope.relayed_by is not None and envelope.relayed_by.id == str(caller.id)
        assert envelope.reply is not None and envelope.reply.kind is ReplyKind.ROOM

    def test_a_person_keeps_the_plain_user_turn_and_cannot_send_a_peer_block(
        self, client, repository
    ):
        session = self._running_session(repository, "plain")
        person = Principal(user_id="dev-user", email="", tenant_id="default", roles=[])
        fake = self._ack_connect({"type": "user_delivered", "status": "delivered"})
        with (
            patch("websockets.asyncio.client.connect", new=fake),
            patch(
                "volundr.adapters.inbound.rest.extract_principal",
                new=AsyncMock(return_value=person),
            ),
        ):
            plain = client.post(
                f"/api/v1/forge/sessions/{session.id}/messages",
                json={"content": "hi", "sender": {"id": "ignored"}},
            )
            peer = client.post(
                f"/api/v1/forge/sessions/{session.id}/messages",
                json={"content": "hi", "peer": {}},
            )

        assert plain.status_code == 200
        assert json.loads(fake.ws.sent[0])["type"] == "user"
        assert peer.status_code == 400
        assert len(fake.ws.sent) == 1

    def test_send_preserves_client_identity_for_duplicate_safe_retry(self, client, repository):
        fake_connect = self._ack_connect({"type": "user_delivered", "status": "delivered"})
        identity = str(uuid4())
        response = self._post_message(client, repository, fake_connect, request_id=identity)
        assert response.json()["request_id"] == identity
        assert json.loads(fake_connect.ws.sent[0])["request_id"] == identity

    @pytest.mark.parametrize(
        ("frame", "expected"),
        [
            ({"type": "user_delivery_pending", "status": "pending"}, 202),
            ({"type": "error", "code": "request_id_conflict", "content": "different prompt"}, 409),
            (
                {
                    "type": "error",
                    "code": "message_claim_unavailable",
                    "content": "database offline",
                },
                503,
            ),
        ],
    )
    def test_duplicate_and_claim_failures_are_not_delivered(
        self, client, repository, frame, expected
    ):
        response = self._post_message(client, repository, self._ack_connect(frame))
        assert response.status_code == expected
        assert response.json()["delivery"] != "delivered"

    def test_send_failure_after_dispatch_is_uncertain_not_safe_to_repeat(self, client, repository):
        fake_connect = self._ack_connect(None)
        fake_connect.ws.send = AsyncMock(side_effect=TimeoutError("write may have reached peer"))
        identity = str(uuid4())
        response = self._post_message(client, repository, fake_connect, request_id=identity)
        assert response.status_code == 202
        assert response.json()["request_id"] == identity
        assert response.json()["delivery"] == "pending"

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
        service_logs = workspace / ".services" / "logs"
        service_logs.mkdir(parents=True)
        (workspace / ".skuld.log").write_text(
            "2026-05-01 15:19:48,121 - skuld.broker - INFO - Starting Skuld broker\n",
            encoding="utf-8",
        )
        (service_logs / "coder.log").write_text(
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
            "service:coder",
        }
        assert [line["participant"] for line in data["lines"]] == ["skuld", "service:coder"]

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


class TestSessionRoomProxy:
    """Services reach a session's room through Forge, not the session's own origin."""

    PRINCIPAL = Principal(
        user_id="dev-user", email="dev@example.com", tenant_id="default", roles=[]
    )

    def _session(
        self, repository: InMemorySessionRepository, owner_id: str = "dev-user"
    ) -> Session:
        session = Session(
            id=uuid4(),
            name="room-session",
            model="claude-sonnet-4",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
            status=SessionStatus.RUNNING,
            chat_endpoint="ws://localhost:8080/s/room-session/session",
            owner_id=owner_id,
        )
        asyncio.run(repository.create(session))
        return session

    def test_the_owner_reaches_the_broker_with_the_owner_room_role(
        self, client: TestClient, repository: InMemorySessionRepository
    ) -> None:
        session = self._session(repository)
        upstream = MagicMock(spec=httpx.Response)
        upstream.content = b'{"status": "sent"}'
        upstream.status_code = 200
        upstream.headers = {"content-type": "application/json"}

        with (
            patch(
                "volundr.adapters.inbound.rest.extract_principal",
                new=AsyncMock(return_value=self.PRINCIPAL),
            ),
            patch("volundr.adapters.inbound.rest.httpx.AsyncClient") as mock_client_cls,
        ):
            mock_client = AsyncMock()
            mock_client.request.return_value = upstream
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            response = client.post(
                f"/api/v1/forge/sessions/{session.id}/room/direct",
                json={"target_peer_id": "flock-coordinator", "content": "start"},
            )

        assert response.status_code == 200
        assert response.json() == {"status": "sent"}
        args, kwargs = mock_client.request.await_args
        assert args[1].endswith("/s/room-session/api/room/direct")
        assert kwargs["headers"]["x-niuu-room-role"] == "owner"

    def _post_room(self, client, session, principal, operation: str = "session-message"):
        upstream = MagicMock(spec=httpx.Response)
        upstream.content = b'{"status": "sent"}'
        upstream.status_code = 200
        upstream.headers = {"content-type": "application/json"}
        with (
            patch(
                "volundr.adapters.inbound.rest.extract_principal",
                new=AsyncMock(return_value=principal),
            ),
            patch("volundr.adapters.inbound.rest.httpx.AsyncClient") as mock_client_cls,
        ):
            mock_client = AsyncMock()
            mock_client.request.return_value = upstream
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            response = client.post(
                f"/api/v1/forge/sessions/{session.id}/room/{operation}",
                json={"room_id": "huddle-1", "content": "hi"},
                headers={"x-niuu-room-caller-session": "forged-by-the-client"},
            )
        return response, mock_client.request.await_args

    def test_a_member_session_is_stamped_from_its_verified_credential(
        self, client: TestClient, repository: InMemorySessionRepository
    ) -> None:
        host = self._session(repository)
        member_id = str(uuid4())
        member = Principal(
            user_id="dev-user",
            email="",
            tenant_id="default",
            roles=[],
            token_use="forge_session",
            bound_session_id=member_id,
        )

        response, (_, kwargs) = self._post_room(client, host, member)

        assert response.status_code == 200
        # The stamp is the verified credential's session, never what the client sent.
        assert kwargs["headers"]["x-niuu-room-caller-session"] == member_id
        assert kwargs["headers"]["x-niuu-room-role"] == "owner"

    def test_a_person_is_never_stamped_as_a_calling_session(
        self, client: TestClient, repository: InMemorySessionRepository
    ) -> None:
        host = self._session(repository)

        response, (_, kwargs) = self._post_room(client, host, self.PRINCIPAL)

        assert response.status_code == 200
        assert "x-niuu-room-caller-session" not in kwargs["headers"]

    def test_someone_else_cannot_reach_the_room(
        self, client: TestClient, repository: InMemorySessionRepository
    ) -> None:
        session = self._session(repository, owner_id="someone-else")

        with patch(
            "volundr.adapters.inbound.rest.extract_principal",
            new=AsyncMock(return_value=self.PRINCIPAL),
        ):
            response = client.post(
                f"/api/v1/forge/sessions/{session.id}/room/direct", json={"content": "start"}
            )

        assert response.status_code == 403

    def test_an_unknown_room_operation_is_refused(
        self, client: TestClient, repository: InMemorySessionRepository
    ) -> None:
        session = self._session(repository)

        with patch(
            "volundr.adapters.inbound.rest.extract_principal",
            new=AsyncMock(return_value=self.PRINCIPAL),
        ):
            response = client.post(
                f"/api/v1/forge/sessions/{session.id}/room/huddle", json={"content": "start"}
            )

        assert response.status_code == 422

    def test_a_missing_session_has_no_room(self, client: TestClient) -> None:
        with patch(
            "volundr.adapters.inbound.rest.extract_principal",
            new=AsyncMock(return_value=self.PRINCIPAL),
        ):
            response = client.post(
                f"/api/v1/forge/sessions/{uuid4()}/room/direct", json={"content": "start"}
            )

        assert response.status_code == 404


class TestSessionBrokerProxy:
    """Tests for the URLs and conversation reads Forge relays to a session's broker."""

    def test_session_proxy_url_quotes_path_segments(self) -> None:
        """Dynamic path segments should be encoded before proxying to a session pod."""
        assert (
            _session_proxy_url(
                "http://localhost:8080/s/session-123",
                "api",
                "help",
                "requests",
                "request needs answer?step=1",
                "answer",
            )
            == "http://localhost:8080/s/session-123/api/help/requests/request%20needs%20answer%3Fstep%3D1/answer"
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


class TestConversationTurnPosition:
    """A turn's position comes from the full listing, even when the broker answers the
    item request with only that turn (as live brokers do)."""

    @pytest.mark.asyncio
    async def test_live_turn_reports_its_position_in_the_full_listing(
        self,
        client: TestClient,
        service: SessionService,
    ) -> None:
        session = await service.create_session(
            "live",
            "claude-sonnet-4",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        session = session.with_endpoints(
            f"ws://localhost:8080/s/{session.id}/session",
            "https://workspace.example.com/session",
        ).with_status(SessionStatus.RUNNING)
        await service._repository.update(session)
        turns = [
            {"id": "u1", "role": "user", "content": "hi", "parts": []},
            {"id": "a1", "role": "assistant", "content": "hello", "parts": []},
            {"id": "a2", "role": "assistant", "content": "done", "parts": []},
        ]

        def reply(url, headers=None, params=None):
            response = MagicMock(spec=httpx.Response)
            response.status_code = 200
            response.raise_for_status = MagicMock()
            only = (params or {}).get("turn_id")
            body = [t for t in turns if t["id"] == only] if only else turns
            response.json.return_value = {"turns": body, "is_active": False}
            return response

        with patch("volundr.adapters.inbound.rest.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.side_effect = reply
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            found = client.get(f"/api/v1/forge/sessions/{session.id}/conversation/turns/a2")

        assert found.status_code == 200
        body = found.json()
        assert body["turn"]["id"] == "a2"
        assert (body["index"], body["total_turns"]) == (2, 3)


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


class TestSessionRuntimes:
    """Tests for GET /api/v1/forge/session-runtimes."""

    def test_a_host_that_declares_no_runtime_offers_none(self, client: TestClient):
        response = client.get("/api/v1/forge/session-runtimes")
        assert response.status_code == 200
        assert response.json() == {"runtimes": []}

    def test_the_offer_is_the_hosts_runtime_with_grants_and_readiness(
        self, client: TestClient, pod_manager
    ):
        from volundr.domain.session_runtimes import (
            ReadinessState,
            RuntimeReadiness,
            SessionGrant,
            SessionRuntimeKind,
            SessionRuntimeOffer,
        )

        pod_manager.session_runtimes = AsyncMock(
            return_value=(
                SessionRuntimeOffer(
                    runtime=SessionRuntimeKind.OPENSHELL,
                    grants=(SessionGrant.SUDO,),
                    readiness=RuntimeReadiness(
                        state=ReadinessState.FULL, warm=0, remedy="raise the pool"
                    ),
                ),
            )
        )

        response = client.get("/api/v1/forge/session-runtimes")

        assert response.json() == {
            "runtimes": [
                {
                    "runtime": "openshell",
                    "grants": ["sudo"],
                    "readiness": {"state": "full", "warm": 0, "remedy": "raise the pool"},
                }
            ]
        }


class TestFeatureFlags:
    """Tests for GET /api/v1/forge/feature-flags."""

    def test_feature_flags_returns_local_mounts_flag(self, client: TestClient):
        """Returns feature flags including local_mounts_enabled."""
        response = client.get("/api/v1/forge/feature-flags")
        assert response.status_code == 200
        data = response.json()
        assert "local_mounts_enabled" in data
        assert isinstance(data["local_mounts_enabled"], bool)

    def test_feature_flags_report_home_volume_support(self, client: TestClient):
        """The in-memory storage adapter only simulates storage: no home volumes."""
        response = client.get("/api/v1/forge/feature-flags")
        assert response.status_code == 200
        assert response.json()["home_volumes_supported"] is False

    def test_feature_flags_lists_allowed_mount_prefixes(self, client: TestClient):
        """Exposes the configured mount prefix allowlist for UI/automation."""
        response = client.get("/api/v1/forge/feature-flags")
        assert response.status_code == 200
        data = response.json()
        assert "local_mounts_allowed_prefixes" in data
        assert isinstance(data["local_mounts_allowed_prefixes"], list)

    def test_feature_flags_report_notifications_only_when_served(self, client: TestClient):
        """The flag follows whether the notification feed is composed on this host."""
        data = client.get("/api/v1/forge/feature-flags").json()
        assert data["notifications_enabled"] is False
        assert data["capabilities"]["notifications"] is False
        client.app.state.notification_service = object()
        data = client.get("/api/v1/forge/feature-flags").json()
        assert data["notifications_enabled"] is True
        assert data["capabilities"]["notifications"] is True

    def test_feature_flags_report_forge_mcp_credentials(self, client: TestClient):
        """Session credentials and the HTTP MCP are reported, never silently absent."""
        capabilities = client.get("/api/v1/forge/feature-flags").json()["capabilities"]
        assert capabilities["forge_session_tokens"] is False
        assert capabilities["forge_session_token_key"] is None
        assert capabilities["forge_mcp_http"] is False
        client.app.state.forge_session_tokens = type("T", (), {"key_source": "key_file"})()
        client.app.state.forge_mcp_http = True
        capabilities = client.get("/api/v1/forge/feature-flags").json()["capabilities"]
        assert capabilities["forge_session_tokens"] is True
        assert capabilities["forge_session_token_key"] == "key_file"
        assert capabilities["forge_mcp_http"] is True


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
        router = create_router(
            service,
            stats_service=None,
            session_participant_service=make_session_participant_service(service),
        )
        app.include_router(router)
        with TestClient(app) as client:
            response = client.get("/api/v1/forge/stats")
        assert response.status_code == 503
        assert "not available" in response.json()["detail"].lower()

    def test_get_stats_with_zero_values(self, service: SessionService):
        """Returns stats with zero values."""
        stats_repo = InMemoryStatsRepository()
        stats_svc = StatsService(stats_repo, service)
        app = FastAPI()
        router = create_router(
            service,
            stats_svc,
            session_participant_service=make_session_participant_service(service),
        )
        app.include_router(router)
        with TestClient(app) as client:
            response = client.get("/api/v1/forge/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["active_sessions"] == 0
        assert data["total_sessions"] == 0
        assert data["tokens_today"] == 0
        assert data["cost_today"] == 0.0


NOATUN = GuildInstance(id="70b502d8", name="Noatun", slug="noatun")


def _node_client(
    service: SessionService,
    stats_service: StatsService,
    membership: GuildMembership | None,
) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_router(
            service,
            stats_service,
            session_participant_service=make_session_participant_service(service),
            guild_membership=membership,
        )
    )
    return TestClient(app)


class TestNodeInstanceFields:
    """A node joined to a Guild names itself on its sessions and stats, as the facade does."""

    async def test_sessions_and_stats_carry_the_nodes_instance(
        self, service: SessionService, stats_service: StatsService
    ):
        await service.create_session(
            "On Noatun",
            "claude-sonnet-4",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        membership = GuildMembership(guild_url="https://guild.example", instance=NOATUN)
        with _node_client(service, stats_service, membership) as client:
            sessions = client.get("/api/v1/forge/sessions").json()
            stats = client.get("/api/v1/forge/stats", params={"instance_id": NOATUN.id})

        assert [(s["instance_id"], s["instance_name"], s["instance_slug"]) for s in sessions] == [
            ("70b502d8", "Noatun", "noatun")
        ]
        assert stats.status_code == 200
        assert stats.json()["instance_id"] == "70b502d8"
        assert stats.json()["instance_name"] == "Noatun"
        assert stats.json()["tokens_today"] == 50000

    def test_stats_for_another_forge_is_404(
        self, service: SessionService, stats_service: StatsService
    ):
        membership = GuildMembership(guild_url="https://guild.example", instance=NOATUN)
        with _node_client(service, stats_service, membership) as client:
            response = client.get("/api/v1/forge/stats", params={"instance_id": "ymir-id"})

        assert response.status_code == 404
        assert "not this Forge node (Noatun, 70b502d8)" in response.json()["detail"]

    def test_stats_by_instance_on_an_unjoined_node_is_404(
        self, service: SessionService, stats_service: StatsService
    ):
        with _node_client(service, stats_service, None) as client:
            by_instance = client.get("/api/v1/forge/stats", params={"instance_id": NOATUN.id})
            plain = client.get("/api/v1/forge/stats")

        assert by_instance.status_code == 404
        assert plain.status_code == 200
        assert plain.json()["instance_id"] is None

    def test_stats_by_instance_before_the_instance_is_recorded_is_503(
        self, service: SessionService, stats_service: StatsService
    ):
        membership = GuildMembership(guild_url="https://guild.example")
        with _node_client(service, stats_service, membership) as client:
            response = client.get("/api/v1/forge/stats", params={"instance_id": NOATUN.id})

        assert response.status_code == 503
        assert response.json()["detail"] == (
            "This Forge node belongs to the Guild at https://guild.example, but its instance "
            "id there is not recorded. On a host joined with `niuu join`, run "
            "`niuu guild heartbeat --once`; on a cluster, set the Volundr chart's "
            "guild.instanceId, guild.instanceName and guild.instanceSlug from the Guild's "
            "registry (GET /api/v1/niuu/targets/volundr)."
        )


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

    @staticmethod
    def _app(repo_service: RepoService | None) -> FastAPI:
        """A niuu repos app with the caller resolved to a known person, the way
        the platform's auth would."""
        from niuu.adapters.inbound.auth import extract_principal
        from niuu.adapters.inbound.rest_repos import create_repos_router

        app = FastAPI()
        app.include_router(create_repos_router(repo_service))
        app.dependency_overrides[extract_principal] = lambda: Principal(
            user_id="dev-user", email="dev@example.com", tenant_id="default", roles=[]
        )
        return app

    @pytest.fixture
    def repos_client(self, repo_service: RepoService) -> TestClient:
        """Create a test client with niuu repos router."""
        client = TestClient(self._app(repo_service))
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
        with TestClient(self._app(None)) as client:
            response = client.get("/api/v1/niuu/repos")
        assert response.status_code == 503
        assert "not available" in response.json()["detail"].lower()

    def test_list_repos_empty_when_no_orgs(self):
        """Returns empty dict when no providers have orgs configured."""
        gh = MockGitProvider(name="GitHub")
        registry = MockGitRegistry([gh])
        repo_service = RepoService(registry)
        with TestClient(self._app(repo_service)) as client:
            response = client.get("/api/v1/niuu/repos")
        assert response.status_code == 200
        assert response.json() == {}


class TestRelayedSessionMessages:
    """A session on another Guild node may message any session its owner owns."""

    def _target(self, repository, *, owner="dev-user") -> Session:
        session = Session(
            id=uuid4(),
            name="ios-coordinator",
            model="claude-sonnet-4",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
            status=SessionStatus.RUNNING,
            chat_endpoint="ws://localhost:8080/s/ios-coordinator/session",
            owner_id=owner,
        )
        asyncio.run(repository.create(session))
        return session

    @staticmethod
    def _sender(**fields) -> dict:
        base = {"session_id": str(uuid4()), "session_name": "voice", "instance_name": "mini-m6"}
        return base | fields

    def _relay(self, client, target_id, sender, *, principal=None, fake_connect=None, **body):
        fake_connect = fake_connect or TestSessionMessages._ack_connect(
            {"type": "user_delivered", "status": "delivered"}
        )
        principal = principal or Principal(
            user_id="dev-user", email="dev@example.com", tenant_id="default", roles=[]
        )
        with (
            patch("websockets.asyncio.client.connect", new=fake_connect),
            patch(
                "volundr.adapters.inbound.rest.extract_principal",
                new=AsyncMock(return_value=principal),
            ),
        ):
            response = client.post(
                f"/api/v1/forge/sessions/{target_id}/messages/relayed",
                json={"content": "hotfix landed", "sender": sender, **body},
            )
        return response, fake_connect

    def test_any_session_of_the_owner_is_delivered_as_an_envelope_from_the_sender(
        self, client, repository
    ):
        target = self._target(repository)
        sender = self._sender()
        response, fake = self._relay(client, target.id, sender, request_id="r-1")

        assert response.status_code == 200
        assert response.json()["delivery"] == "delivered"
        frame = json.loads(fake.ws.sent[0])
        assert frame["type"] == "peer_message"
        envelope = PeerMessage.from_wire(frame["envelope"])
        # No text prefix any more: the sender is the envelope's own field.
        assert envelope.content == "hotfix landed"
        assert envelope.sender == PeerAddress(
            kind=PeerKind.SESSION, id=sender["session_id"], name="voice", node="mini-m6"
        )
        assert envelope.reply is not None
        assert (envelope.reply.target, envelope.reply.node) == (sender["session_id"], "mini-m6")

    def test_the_envelope_carries_in_reply_to_and_urgent(self, client, repository):
        target = self._target(repository)
        response, fake = self._relay(
            client, target.id, self._sender(), in_reply_to="m-3", urgent=True
        )

        assert response.status_code == 200
        envelope = json.loads(fake.ws.sent[0])["envelope"]
        assert (envelope["in_reply_to"], envelope["urgent"]) == ("m-3", True)

    def test_a_relayed_room_message_names_the_relaying_session(self, client, repository):
        target = self._target(repository)
        sender = self._sender()
        peer = {
            "speaker": {"kind": "person", "id": "p", "name": "P", "node": ""},
            "room_id": "room-1",
            "reply": {"kind": "room", "target": sender["session_id"], "node": "mini-m6"},
        }
        response, fake = self._relay(client, target.id, sender, peer=peer)

        assert response.status_code == 200
        envelope = PeerMessage.from_wire(json.loads(fake.ws.sent[0])["envelope"])
        assert envelope.sender.id == "p" and envelope.room_id == "room-1"
        assert envelope.relayed_by is not None
        assert (envelope.relayed_by.id, envelope.relayed_by.node) == (
            sender["session_id"],
            "mini-m6",
        )

    def test_a_malformed_peer_block_is_a_400(self, client, repository):
        target = self._target(repository)
        response, fake = self._relay(client, target.id, self._sender(), peer={"speaker": 3})

        assert response.status_code == 400
        assert fake.ws.sent == []

    def test_a_session_of_another_owner_is_refused_with_the_reason(self, client, repository):
        target = self._target(repository, owner="someone-else")
        response, fake = self._relay(client, target.id, self._sender())

        assert response.status_code == 403
        assert "belongs to another owner" in response.json()["detail"]
        assert fake.ws.sent == []

    def test_a_session_cannot_relay_to_itself(self, client, repository):
        target = self._target(repository)
        response, fake = self._relay(client, target.id, self._sender(session_id=str(target.id)))

        assert response.status_code == 403
        assert "cannot message itself" in response.json()["detail"]
        assert fake.ws.sent == []

    def test_a_session_credential_cannot_relay(self, client, repository):
        target = self._target(repository)
        session_principal = Principal(
            user_id="dev-user",
            email="dev@example.com",
            tenant_id="default",
            roles=[],
            token_use="forge_session",
            bound_session_id=str(uuid4()),
        )
        response, fake = self._relay(client, target.id, self._sender(), principal=session_principal)

        assert response.status_code == 403
        assert fake.ws.sent == []

    def test_an_unknown_session_is_not_found(self, client, repository):
        response, _ = self._relay(client, uuid4(), self._sender())

        assert response.status_code == 404
