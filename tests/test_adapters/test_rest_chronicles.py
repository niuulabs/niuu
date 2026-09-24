"""Tests for Chronicle REST endpoints."""

from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.adapters.cedar import CedarAuthorizationAdapter
from tests.conftest import (
    InMemoryChronicleRepository,
    InMemorySessionRepository,
    InMemoryTimelineRepository,
    MockPodManager,
)
from volundr.adapters.inbound.rest import create_router
from volundr.adapters.outbound.identity import AllowAllIdentityAdapter
from volundr.domain.models import Chronicle, GitSource, Session
from volundr.domain.ports import SessionCapacity
from volundr.domain.services import (
    ChronicleService,
    RepoValidationError,
    SessionCapacityError,
    SessionService,
)


@pytest.fixture
def session_service(
    repository: InMemorySessionRepository, pod_manager: MockPodManager
) -> SessionService:
    """Create a session service with test doubles."""
    return SessionService(repository, pod_manager)


@pytest.fixture
def chronicle_svc(
    chronicle_repository: InMemoryChronicleRepository,
    session_service: SessionService,
) -> ChronicleService:
    """Create a chronicle service with test doubles."""
    return ChronicleService(chronicle_repository, session_service)


@pytest.fixture
def app(session_service: SessionService, chronicle_svc: ChronicleService) -> FastAPI:
    """Create a test FastAPI app with chronicle service."""
    app = FastAPI()
    router = create_router(session_service, chronicle_service=chronicle_svc)
    app.include_router(router)
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """Create a test client."""
    return TestClient(app)


@pytest.fixture
def app_no_chronicles(session_service: SessionService) -> FastAPI:
    """Create a test FastAPI app without chronicle service."""
    app = FastAPI()
    router = create_router(session_service)
    app.include_router(router)
    return app


@pytest.fixture
def client_no_chronicles(app_no_chronicles: FastAPI) -> TestClient:
    """Create a test client without chronicle service."""
    return TestClient(app_no_chronicles)


class TestChronicleEndpointCreate:
    """Tests for POST /api/v1/forge/chronicles."""

    async def test_create_chronicle_success(
        self, client: TestClient, session_service: SessionService
    ):
        """Creates chronicle from session and returns 201."""
        session = await session_service.create_session(
            name="Test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )

        response = client.post(
            "/api/v1/forge/chronicles",
            json={"session_id": str(session.id)},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["session_id"] == str(session.id)
        assert data["project"] == "repo"
        assert data["status"] == "draft"
        assert "id" in data

    def test_create_chronicle_session_not_found(self, client: TestClient):
        """Returns 404 for nonexistent session."""
        fake_id = uuid4()
        response = client.post(
            "/api/v1/forge/chronicles",
            json={"session_id": str(fake_id)},
        )
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestChronicleEndpointGet:
    """Tests for GET /api/v1/forge/chronicles/{id}."""

    async def test_get_chronicle_success(
        self,
        client: TestClient,
        session_service: SessionService,
        chronicle_svc: ChronicleService,
    ):
        """Returns chronicle by ID with 200."""
        session = await session_service.create_session(
            name="Test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        chronicle = await chronicle_svc.create_chronicle(session.id, principal=None)

        response = client.get(f"/api/v1/forge/chronicles/{chronicle.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(chronicle.id)
        assert data["project"] == "repo"

    def test_get_chronicle_not_found(self, client: TestClient):
        """Returns 404 for nonexistent chronicle."""
        fake_id = uuid4()
        response = client.get(f"/api/v1/forge/chronicles/{fake_id}")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestChronicleEndpointList:
    """Tests for GET /api/v1/forge/chronicles."""

    def test_list_chronicles_empty(self, client: TestClient):
        """Returns empty list when no chronicles exist."""
        response = client.get("/api/v1/forge/chronicles")
        assert response.status_code == 200
        assert response.json() == []

    async def test_list_chronicles_with_results(
        self,
        client: TestClient,
        session_service: SessionService,
        chronicle_svc: ChronicleService,
    ):
        """Returns list of chronicles."""
        s1 = await session_service.create_session(
            name="Session 1",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/org/repo1", branch="main"),
        )
        s2 = await session_service.create_session(
            name="Session 2",
            model="claude-opus-4-20250514",
            source=GitSource(repo="https://github.com/org/repo2", branch="dev"),
        )
        await chronicle_svc.create_chronicle(s1.id, principal=None)
        await chronicle_svc.create_chronicle(s2.id, principal=None)

        response = client.get("/api/v1/forge/chronicles")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2


class TestChronicleEndpointUpdate:
    """Tests for PATCH /api/v1/forge/chronicles/{id}."""

    async def test_update_chronicle_success(
        self,
        client: TestClient,
        session_service: SessionService,
        chronicle_svc: ChronicleService,
    ):
        """Updates chronicle fields and returns 200."""
        session = await session_service.create_session(
            name="Test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        chronicle = await chronicle_svc.create_chronicle(session.id, principal=None)

        response = client.patch(
            f"/api/v1/forge/chronicles/{chronicle.id}",
            json={
                "summary": "Updated summary",
                "tags": ["python", "fix"],
                "status": "complete",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["summary"] == "Updated summary"
        assert data["tags"] == ["python", "fix"]
        assert data["status"] == "complete"

    def test_update_chronicle_not_found(self, client: TestClient):
        """Returns 404 for nonexistent chronicle."""
        fake_id = uuid4()
        response = client.patch(
            f"/api/v1/forge/chronicles/{fake_id}",
            json={"summary": "new"},
        )
        assert response.status_code == 404


class TestChronicleEndpointDelete:
    """Tests for DELETE /api/v1/forge/chronicles/{id}."""

    async def test_delete_chronicle_success(
        self,
        client: TestClient,
        session_service: SessionService,
        chronicle_svc: ChronicleService,
    ):
        """Deletes chronicle and returns 204."""
        session = await session_service.create_session(
            name="Test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        chronicle = await chronicle_svc.create_chronicle(session.id, principal=None)

        response = client.delete(f"/api/v1/forge/chronicles/{chronicle.id}")

        assert response.status_code == 204

        # Verify deleted
        get_response = client.get(f"/api/v1/forge/chronicles/{chronicle.id}")
        assert get_response.status_code == 404

    def test_delete_chronicle_not_found(self, client: TestClient):
        """Returns 404 for nonexistent chronicle."""
        fake_id = uuid4()
        response = client.delete(f"/api/v1/forge/chronicles/{fake_id}")
        assert response.status_code == 404


class TestChronicleEndpointReforge:
    """Tests for POST /api/v1/forge/chronicles/{id}/reforge."""

    async def test_reforge_success(
        self,
        client: TestClient,
        session_service: SessionService,
        chronicle_svc: ChronicleService,
    ):
        """Reforging returns a new session with 200."""
        session = await session_service.create_session(
            name="Original",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        chronicle = await chronicle_svc.create_chronicle(session.id, principal=None)

        response = client.post(f"/api/v1/forge/chronicles/{chronicle.id}/reforge")

        assert response.status_code == 200
        data = response.json()
        assert "(reforged)" in data["name"]
        assert data["source"]["repo"] == "https://github.com/org/repo"
        assert data["model"] == "claude-sonnet-4-20250514"
        assert data["status"] == "created"
        assert data["id"] != str(session.id)

    def test_reforge_not_found(self, client: TestClient):
        """Returns 404 for nonexistent chronicle."""
        fake_id = uuid4()
        response = client.post(f"/api/v1/forge/chronicles/{fake_id}/reforge")
        assert response.status_code == 404

    @pytest.mark.parametrize("failure", ["repo", "capacity"])
    async def test_reforge_reports_why_the_session_was_refused(
        self,
        client: TestClient,
        session_service: SessionService,
        chronicle_svc: ChronicleService,
        failure: str,
    ):
        """A relaunch the session service refuses maps like POST /sessions does."""
        session = await session_service.create_session(
            name="Original",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        chronicle = await chronicle_svc.create_chronicle(session.id, principal=None)
        errors = {
            "repo": (RepoValidationError("https://github.com/org/repo", "gone"), 422),
            "capacity": (SessionCapacityError(SessionCapacity(1, 1, "raise the cap")), 409),
        }
        error, expected = errors[failure]
        session_service.create_session = AsyncMock(side_effect=error)

        response = client.post(f"/api/v1/forge/chronicles/{chronicle.id}/reforge")

        assert response.status_code == expected


class TestChronicleEndpointChain:
    """Tests for GET /api/v1/forge/chronicles/{id}/chain."""

    async def test_get_chain_success(
        self,
        client: TestClient,
        session_service: SessionService,
        chronicle_svc: ChronicleService,
    ):
        """Returns chain for a chronicle."""
        session = await session_service.create_session(
            name="Test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        chronicle = await chronicle_svc.create_chronicle(session.id, principal=None)

        response = client.get(f"/api/v1/forge/chronicles/{chronicle.id}/chain")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == str(chronicle.id)

    def test_get_chain_empty_for_nonexistent(self, client: TestClient):
        """Returns empty list for nonexistent chronicle."""
        fake_id = uuid4()
        response = client.get(f"/api/v1/forge/chronicles/{fake_id}/chain")
        assert response.status_code == 200
        assert response.json() == []


class TestChronicleEndpointGetBySession:
    """Tests for GET /api/v1/forge/sessions/{id}/chronicle."""

    async def test_get_session_chronicle_success(
        self,
        client: TestClient,
        session_service: SessionService,
        chronicle_svc: ChronicleService,
    ):
        """Returns the most recent chronicle for a session."""
        session = await session_service.create_session(
            name="Test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        chronicle = await chronicle_svc.create_chronicle(session.id, principal=None)

        response = client.get(f"/api/v1/forge/sessions/{session.id}/chronicle")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(chronicle.id)
        assert data["session_id"] == str(session.id)

    def test_get_session_chronicle_not_found(self, client: TestClient):
        """Returns 404 when no chronicle exists for session."""
        fake_id = uuid4()
        response = client.get(f"/api/v1/forge/sessions/{fake_id}/chronicle")
        assert response.status_code == 404
        assert "no chronicle found" in response.json()["detail"].lower()


class TestChronicleEndpointBrokerReport:
    """Tests for POST /api/v1/forge/sessions/{id}/chronicle."""

    async def test_broker_report_creates_chronicle(
        self,
        client: TestClient,
        session_service: SessionService,
    ):
        """Broker report creates a new chronicle with summary data."""
        session = await session_service.create_session(
            name="Test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )

        response = client.post(
            f"/api/v1/forge/sessions/{session.id}/chronicle",
            json={
                "summary": "Implemented feature X",
                "key_changes": ["main.py: added handler"],
                "duration_seconds": 120,
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["session_id"] == str(session.id)
        assert data["summary"] == "Implemented feature X"
        assert data["key_changes"] == ["main.py: added handler"]
        assert data["duration_seconds"] == 120
        assert data["status"] == "complete"

    async def test_broker_report_enriches_existing_draft(
        self,
        client: TestClient,
        session_service: SessionService,
        chronicle_svc: ChronicleService,
    ):
        """Broker report enriches an existing DRAFT chronicle."""
        session = await session_service.create_session(
            name="Test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        existing = await chronicle_svc.create_chronicle(session.id, principal=None)

        response = client.post(
            f"/api/v1/forge/sessions/{session.id}/chronicle",
            json={
                "summary": "Broker-generated summary",
                "unfinished_work": "Need more tests",
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["id"] == str(existing.id)
        assert data["summary"] == "Broker-generated summary"
        assert data["unfinished_work"] == "Need more tests"

    def test_broker_report_session_not_found(self, client: TestClient):
        """Returns 404 for nonexistent session."""
        fake_id = uuid4()
        response = client.post(
            f"/api/v1/forge/sessions/{fake_id}/chronicle",
            json={"summary": "test"},
        )
        assert response.status_code == 404

    def test_broker_report_empty_payload(
        self,
        client: TestClient,
    ):
        """Returns 404 for a nonexistent session even with empty payload."""
        fake_id = uuid4()
        response = client.post(
            f"/api/v1/forge/sessions/{fake_id}/chronicle",
            json={},
        )
        assert response.status_code == 404

    def test_broker_report_service_unavailable(self, client_no_chronicles: TestClient):
        """Returns 503 when chronicle service is not configured."""
        fake_id = uuid4()
        response = client_no_chronicles.post(
            f"/api/v1/forge/sessions/{fake_id}/chronicle",
            json={"summary": "test"},
        )
        assert response.status_code == 503
        assert "not available" in response.json()["detail"].lower()


class TestChronicleServiceUnavailable:
    """Tests for chronicle endpoints when service is not configured."""

    def test_list_chronicles_unavailable(self, client_no_chronicles: TestClient):
        """GET /chronicles returns 503 when service is None."""
        response = client_no_chronicles.get("/api/v1/forge/chronicles")
        assert response.status_code == 503
        assert "not available" in response.json()["detail"].lower()

    def test_create_chronicle_unavailable(self, client_no_chronicles: TestClient):
        """POST /chronicles returns 503 when service is None."""
        response = client_no_chronicles.post(
            "/api/v1/forge/chronicles",
            json={"session_id": str(uuid4())},
        )
        assert response.status_code == 503
        assert "not available" in response.json()["detail"].lower()

    def test_get_chronicle_unavailable(self, client_no_chronicles: TestClient):
        """GET /chronicles/{id} returns 503 when service is None."""
        response = client_no_chronicles.get(f"/api/v1/forge/chronicles/{uuid4()}")
        assert response.status_code == 503
        assert "not available" in response.json()["detail"].lower()

    def test_update_chronicle_unavailable(self, client_no_chronicles: TestClient):
        """PATCH /chronicles/{id} returns 503 when service is None."""
        response = client_no_chronicles.patch(
            f"/api/v1/forge/chronicles/{uuid4()}",
            json={"summary": "test"},
        )
        assert response.status_code == 503
        assert "not available" in response.json()["detail"].lower()

    def test_delete_chronicle_unavailable(self, client_no_chronicles: TestClient):
        """DELETE /chronicles/{id} returns 503 when service is None."""
        response = client_no_chronicles.delete(f"/api/v1/forge/chronicles/{uuid4()}")
        assert response.status_code == 503
        assert "not available" in response.json()["detail"].lower()

    def test_reforge_chronicle_unavailable(self, client_no_chronicles: TestClient):
        """POST /chronicles/{id}/reforge returns 503 when service is None."""
        response = client_no_chronicles.post(f"/api/v1/forge/chronicles/{uuid4()}/reforge")
        assert response.status_code == 503
        assert "not available" in response.json()["detail"].lower()

    def test_chain_chronicle_unavailable(self, client_no_chronicles: TestClient):
        """GET /chronicles/{id}/chain returns 503 when service is None."""
        response = client_no_chronicles.get(f"/api/v1/forge/chronicles/{uuid4()}/chain")
        assert response.status_code == 503
        assert "not available" in response.json()["detail"].lower()

    def test_get_session_chronicle_unavailable(self, client_no_chronicles: TestClient):
        """GET /sessions/{id}/chronicle returns 503 when service is None."""
        response = client_no_chronicles.get(f"/api/v1/forge/sessions/{uuid4()}/chronicle")
        assert response.status_code == 503
        assert "not available" in response.json()["detail"].lower()


# --- Principal scoping --------------------------------------------------------

_ALICE = {"x-auth-user-id": "alice", "x-auth-tenant": "t1", "x-auth-roles": "volundr:developer"}
_BOB = {"x-auth-user-id": "bob", "x-auth-tenant": "t1", "x-auth-roles": "volundr:developer"}
_VERA = {"x-auth-user-id": "vera", "x-auth-tenant": "t1", "x-auth-roles": "volundr:viewer"}
_T2_ADMIN = {"x-auth-user-id": "root", "x-auth-tenant": "t2", "x-auth-roles": "volundr:admin"}


class _ScopedForge:
    """A Forge with the bundled Cedar policy and header-selected principals."""

    def __init__(self, *, identity: bool = True) -> None:
        self.sessions = InMemorySessionRepository()
        self.chronicles = InMemoryChronicleRepository()
        self.timeline = InMemoryTimelineRepository()
        session_service = SessionService(
            self.sessions, MockPodManager(), authorization=CedarAuthorizationAdapter()
        )
        chronicle_service = ChronicleService(
            self.chronicles, session_service, timeline_repository=self.timeline
        )
        app = FastAPI()
        if identity:
            app.state.identity = AllowAllIdentityAdapter(user_repository=AsyncMock())
        app.include_router(create_router(session_service, chronicle_service=chronicle_service))
        self.client = TestClient(app)

    def session(self, owner_id: str, tenant_id: str = "t1"):
        session = Session(
            name=f"{owner_id}-session",
            model="sonnet",
            owner_id=owner_id,
            tenant_id=tenant_id,
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        self.sessions._sessions[session.id] = session
        return session

    def chronicle(self, owner_id: str, tenant_id: str = "t1"):
        session = self.session(owner_id, tenant_id)
        chronicle = Chronicle(
            session_id=session.id,
            project=f"{owner_id}-project",
            repo="https://github.com/org/repo",
            branch="main",
            model="sonnet",
            config_snapshot={"name": session.name, "model": "sonnet"},
            owner_id=owner_id,
            tenant_id=tenant_id,
        )
        self.chronicles._chronicles[chronicle.id] = chronicle
        return chronicle


_BASE = "/api/v1/forge"


class TestChronicleEndpointsAreScoped:
    def test_list_returns_only_the_callers_history(self):
        forge = _ScopedForge()
        alices = forge.chronicle("alice")
        forge.chronicle("bob")
        forge.chronicle("carol", "t2")

        response = forge.client.get(f"{_BASE}/chronicles", headers=_ALICE)

        assert response.status_code == 200
        assert [c["id"] for c in response.json()] == [str(alices.id)]

    def test_another_tenant_sees_nothing(self):
        forge = _ScopedForge()
        forge.chronicle("alice")

        response = forge.client.get(f"{_BASE}/chronicles", headers=_T2_ADMIN)

        assert response.json() == []

    @pytest.mark.parametrize("headers", [_BOB, _T2_ADMIN])
    def test_reads_outside_the_scope_are_not_found(self, headers):
        forge = _ScopedForge()
        alices = forge.chronicle("alice")
        sid = alices.session_id

        for path in (
            f"/chronicles/{alices.id}",
            f"/sessions/{sid}/chronicle",
            f"/chronicles/{sid}/timeline",
            f"/chronicles/{sid}/diff?file=a.py",
        ):
            assert forge.client.get(_BASE + path, headers=headers).status_code == 404, path
        chain = forge.client.get(f"{_BASE}/chronicles/{alices.id}/chain", headers=headers)
        assert chain.json() == []

    @pytest.mark.parametrize("headers", [_BOB, _T2_ADMIN])
    def test_writes_outside_the_scope_are_not_found_and_change_nothing(self, headers):
        forge = _ScopedForge()
        alices = forge.chronicle("alice")
        fresh = forge.session("alice")
        client = forge.client

        responses = [
            client.patch(f"{_BASE}/chronicles/{alices.id}", json={"summary": "x"}, headers=headers),
            client.delete(f"{_BASE}/chronicles/{alices.id}", headers=headers),
            client.post(f"{_BASE}/chronicles/{alices.id}/reforge", headers=headers),
            client.post(f"{_BASE}/chronicles", json={"session_id": str(fresh.id)}, headers=headers),
            client.post(
                f"{_BASE}/chronicles/{fresh.id}/timeline",
                json={"t": 1, "type": "message", "label": "x"},
                headers=headers,
            ),
            client.post(
                f"{_BASE}/sessions/{alices.session_id}/chronicle",
                json={"summary": "hijacked"},
                headers=headers,
            ),
        ]

        assert [r.status_code for r in responses] == [404] * len(responses)
        assert forge.chronicles._chronicles == {alices.id: alices}
        assert set(forge.sessions._sessions) == {alices.session_id, fresh.id}

    def test_a_viewer_is_forbidden_to_change_its_history(self):
        forge = _ScopedForge()
        veras = forge.chronicle("vera")
        client = forge.client

        assert client.get(f"{_BASE}/chronicles/{veras.id}", headers=_VERA).status_code == 200
        responses = [
            client.patch(f"{_BASE}/chronicles/{veras.id}", json={"summary": "x"}, headers=_VERA),
            client.delete(f"{_BASE}/chronicles/{veras.id}", headers=_VERA),
            client.post(f"{_BASE}/chronicles/{veras.id}/reforge", headers=_VERA),
            client.post(
                f"{_BASE}/chronicles/{veras.session_id}/timeline",
                json={"t": 1, "type": "message", "label": "x"},
                headers=_VERA,
            ),
        ]

        assert [r.status_code for r in responses] == [403] * len(responses)
        assert forge.chronicles._chronicles == {veras.id: veras}
        assert forge.timeline._events == []

    def test_the_owner_can_change_and_reforge_its_history(self):
        forge = _ScopedForge()
        alices = forge.chronicle("alice")
        client = forge.client

        patched = client.patch(
            f"{_BASE}/chronicles/{alices.id}", json={"summary": "done"}, headers=_ALICE
        )
        reforged = client.post(f"{_BASE}/chronicles/{alices.id}/reforge", headers=_ALICE)
        appended = client.post(
            f"{_BASE}/chronicles/{alices.session_id}/timeline",
            json={"t": 1, "type": "message", "label": "hi"},
            headers=_ALICE,
        )
        deleted = client.delete(f"{_BASE}/chronicles/{alices.id}", headers=_ALICE)

        assert patched.json()["summary"] == "done"
        assert reforged.status_code == 200
        new_session = forge.sessions._sessions[UUID(reforged.json()["id"])]
        assert (new_session.owner_id, new_session.tenant_id) == ("alice", "t1")
        assert appended.status_code == 201
        assert deleted.status_code == 204
        assert alices.id not in forge.chronicles._chronicles

    def test_the_diff_is_proxied_only_for_a_readable_session(self):
        forge = _ScopedForge()
        alices = forge.session("alice")

        response = forge.client.get(
            f"{_BASE}/chronicles/{alices.id}/diff?file=a.py", headers=_ALICE
        )

        # Authorized, so the lookup proceeds to the (absent) session endpoint.
        assert response.status_code == 404
        assert "no active endpoint" in response.json()["detail"]


_ROUTES = [
    ("GET", "/chronicles", None),
    ("POST", "/chronicles", {"session_id": "{sid}"}),
    ("GET", "/chronicles/{cid}", None),
    ("PATCH", "/chronicles/{cid}", {"summary": "x"}),
    ("DELETE", "/chronicles/{cid}", None),
    ("POST", "/chronicles/{cid}/reforge", None),
    ("GET", "/chronicles/{cid}/chain", None),
    ("GET", "/sessions/{sid}/chronicle", None),
    ("POST", "/sessions/{sid}/chronicle", {"summary": "x"}),
    ("GET", "/chronicles/{sid}/timeline", None),
    ("POST", "/chronicles/{sid}/timeline", {"t": 1, "type": "message", "label": "x"}),
    ("GET", "/chronicles/{sid}/diff?file=a.py", None),
]


class TestChronicleEndpointsRequireAPrincipal:
    @pytest.mark.parametrize(("method", "path", "body"), _ROUTES)
    @pytest.mark.parametrize("existing", [True, False])
    def test_authorization_without_a_principal_is_401(self, method, path, body, existing):
        """No principal with authorization configured is 401, whether or not the
        target exists, and nothing is written."""
        forge = _ScopedForge(identity=False)
        alices = forge.chronicle("alice")
        cid, sid = (alices.id, alices.session_id) if existing else (uuid4(), uuid4())
        url = _BASE + path.format(cid=cid, sid=sid)
        json_body = None
        if body is not None:
            json_body = {k: v.format(sid=sid) if isinstance(v, str) else v for k, v in body.items()}

        response = forge.client.request(method, url, json=json_body)

        assert response.status_code == 401
        assert forge.chronicles._chronicles == {alices.id: alices}
        assert forge.timeline._events == []
