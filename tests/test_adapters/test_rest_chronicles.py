"""Tests for Chronicle REST endpoints."""

from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import (
    InMemoryChronicleRepository,
    InMemorySessionRepository,
    MockPodManager,
)
from volundr.adapters.inbound.rest import create_router
from volundr.domain.models import GitSource
from volundr.domain.services import ChronicleService, SessionService


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
        chronicle = await chronicle_svc.create_chronicle(session.id)

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
        await chronicle_svc.create_chronicle(s1.id)
        await chronicle_svc.create_chronicle(s2.id)

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
        chronicle = await chronicle_svc.create_chronicle(session.id)

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
        chronicle = await chronicle_svc.create_chronicle(session.id)

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
        chronicle = await chronicle_svc.create_chronicle(session.id)

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
        chronicle = await chronicle_svc.create_chronicle(session.id)

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
        chronicle = await chronicle_svc.create_chronicle(session.id)

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
        assert data["status"] == "draft"

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
        existing = await chronicle_svc.create_chronicle(session.id)

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
