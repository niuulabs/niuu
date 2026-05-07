"""Tests for Chronicle Timeline REST endpoints."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import (
    InMemoryChronicleRepository,
    InMemorySessionRepository,
    InMemoryTimelineRepository,
    MockPodManager,
)
from volundr.adapters.inbound.rest import create_router
from volundr.adapters.outbound.identity import AllowAllIdentityAdapter
from volundr.domain.models import GitSource, TimelineEvent, TimelineEventType
from volundr.domain.services import ChronicleService, SessionService


@pytest.fixture
def session_service(
    repository: InMemorySessionRepository, pod_manager: MockPodManager
) -> SessionService:
    return SessionService(repository, pod_manager)


@pytest.fixture
def chronicle_svc(
    chronicle_repository: InMemoryChronicleRepository,
    session_service: SessionService,
    timeline_repository: InMemoryTimelineRepository,
) -> ChronicleService:
    return ChronicleService(
        chronicle_repository,
        session_service,
        timeline_repository=timeline_repository,
    )


@pytest.fixture
def chronicle_svc_no_timeline(
    chronicle_repository: InMemoryChronicleRepository,
    session_service: SessionService,
) -> ChronicleService:
    return ChronicleService(chronicle_repository, session_service)


@pytest.fixture
def app(session_service: SessionService, chronicle_svc: ChronicleService) -> FastAPI:
    app = FastAPI()
    app.state.identity = AllowAllIdentityAdapter(user_repository=None)
    router = create_router(session_service, chronicle_service=chronicle_svc)
    app.include_router(router)
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture
def app_no_timeline(
    session_service: SessionService, chronicle_svc_no_timeline: ChronicleService
) -> FastAPI:
    app = FastAPI()
    app.state.identity = AllowAllIdentityAdapter(user_repository=None)
    router = create_router(session_service, chronicle_service=chronicle_svc_no_timeline)
    app.include_router(router)
    return app


@pytest.fixture
def client_no_timeline(app_no_timeline: FastAPI) -> TestClient:
    return TestClient(app_no_timeline)


@pytest.fixture
def app_no_chronicles(session_service: SessionService) -> FastAPI:
    app = FastAPI()
    router = create_router(session_service)
    app.include_router(router)
    return app


@pytest.fixture
def client_no_chronicles(app_no_chronicles: FastAPI) -> TestClient:
    return TestClient(app_no_chronicles)


class TestGetTimeline:
    """Tests for GET /api/v1/forge/chronicles/{session_id}/timeline."""

    async def test_get_timeline_success(
        self,
        client: TestClient,
        session_service: SessionService,
        chronicle_svc: ChronicleService,
        timeline_repository: InMemoryTimelineRepository,
    ):
        """Returns full timeline with events, files, commits, and token_burn."""
        session = await session_service.create_session(
            name="Test",
            model="sonnet",
            source=GitSource(
                repo="https://github.com/org/repo",
                branch="main",
            ),
        )
        chronicle = await chronicle_svc.create_chronicle(session.id)

        # Add various events
        await timeline_repository.add_event(
            TimelineEvent(
                id=uuid4(),
                chronicle_id=chronicle.id,
                session_id=session.id,
                t=0,
                type=TimelineEventType.SESSION,
                label="Session started",
                created_at=datetime.now(UTC),
            )
        )
        await timeline_repository.add_event(
            TimelineEvent(
                id=uuid4(),
                chronicle_id=chronicle.id,
                session_id=session.id,
                t=5,
                type=TimelineEventType.MESSAGE,
                label="Review code",
                tokens=2400,
                created_at=datetime.now(UTC),
            )
        )
        await timeline_repository.add_event(
            TimelineEvent(
                id=uuid4(),
                chronicle_id=chronicle.id,
                session_id=session.id,
                t=15,
                type=TimelineEventType.FILE,
                label="src/main.py",
                action="modified",
                ins=45,
                del_=23,
                created_at=datetime.now(UTC),
            )
        )
        await timeline_repository.add_event(
            TimelineEvent(
                id=uuid4(),
                chronicle_id=chronicle.id,
                session_id=session.id,
                t=30,
                type=TimelineEventType.GIT,
                label="fix(thermal): add derivative filter",
                hash="e4f7a21bb",
                created_at=datetime.now(UTC),
            )
        )

        response = client.get(f"/api/v1/forge/chronicles/{session.id}/timeline")

        assert response.status_code == 200
        data = response.json()

        # Events ordered by t
        assert len(data["events"]) == 4
        assert data["events"][0]["t"] == 0
        assert data["events"][0]["type"] == "session"
        assert data["events"][1]["type"] == "message"
        assert data["events"][1]["tokens"] == 2400
        assert data["events"][2]["type"] == "file"
        assert data["events"][2]["ins"] == 45
        assert data["events"][2]["del"] == 23
        assert data["events"][3]["type"] == "git"
        assert data["events"][3]["hash"] == "e4f7a21bb"

        # Files aggregated
        assert len(data["files"]) == 1
        assert data["files"][0]["path"] == "src/main.py"
        assert data["files"][0]["status"] == "mod"

        # Commits newest first
        assert len(data["commits"]) == 1
        assert data["commits"][0]["hash"] == "e4f7a21"

        # Token burn
        assert len(data["token_burn"]) >= 1
        assert data["token_burn"][0] == 2400

    async def test_get_timeline_empty(
        self,
        client: TestClient,
        session_service: SessionService,
        chronicle_svc: ChronicleService,
    ):
        """Returns empty timeline when chronicle has no events."""
        session = await session_service.create_session(
            name="Test",
            model="sonnet",
            source=GitSource(
                repo="https://github.com/org/repo",
                branch="main",
            ),
        )
        await chronicle_svc.create_chronicle(session.id)

        response = client.get(f"/api/v1/forge/chronicles/{session.id}/timeline")

        assert response.status_code == 200
        data = response.json()
        assert data["events"] == []
        assert data["files"] == []
        assert data["commits"] == []
        assert data["token_burn"] == []

    def test_get_timeline_no_chronicle(self, client: TestClient):
        """Returns 404 when no chronicle data exists for session."""
        fake_id = uuid4()
        response = client.get(f"/api/v1/forge/chronicles/{fake_id}/timeline")
        assert response.status_code == 404
        assert "no chronicle data" in response.json()["detail"].lower()

    def test_get_timeline_no_timeline_repo(self, client_no_timeline: TestClient):
        """Returns 404 when timeline repo not configured (no data)."""
        fake_id = uuid4()
        response = client_no_timeline.get(f"/api/v1/forge/chronicles/{fake_id}/timeline")
        assert response.status_code == 404

    def test_get_timeline_service_unavailable(self, client_no_chronicles: TestClient):
        """Returns 503 when chronicle service is not configured."""
        fake_id = uuid4()
        response = client_no_chronicles.get(f"/api/v1/forge/chronicles/{fake_id}/timeline")
        assert response.status_code == 503
        assert "not available" in response.json()["detail"].lower()


class TestAddTimelineEvent:
    """Tests for POST /api/v1/forge/chronicles/{session_id}/timeline."""

    async def test_add_event_success(
        self,
        client: TestClient,
        session_service: SessionService,
        chronicle_svc: ChronicleService,
    ):
        """Adding a timeline event returns 201 with the event."""
        session = await session_service.create_session(
            name="Test",
            model="sonnet",
            source=GitSource(
                repo="https://github.com/org/repo",
                branch="main",
            ),
        )
        await chronicle_svc.create_chronicle(session.id)

        response = client.post(
            f"/api/v1/forge/chronicles/{session.id}/timeline",
            json={
                "t": 10,
                "type": "message",
                "label": "Reviewing thermal code",
                "tokens": 1500,
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["t"] == 10
        assert data["type"] == "message"
        assert data["label"] == "Reviewing thermal code"
        assert data["tokens"] == 1500

    async def test_add_file_event(
        self,
        client: TestClient,
        session_service: SessionService,
        chronicle_svc: ChronicleService,
    ):
        """Adding a file event with ins/del/action."""
        session = await session_service.create_session(
            name="Test",
            model="sonnet",
            source=GitSource(
                repo="https://github.com/org/repo",
                branch="main",
            ),
        )
        await chronicle_svc.create_chronicle(session.id)

        response = client.post(
            f"/api/v1/forge/chronicles/{session.id}/timeline",
            json={
                "t": 20,
                "type": "file",
                "label": "src/thermal.py",
                "action": "modified",
                "ins": 45,
                "del": 23,
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["action"] == "modified"
        assert data["ins"] == 45
        assert data["del"] == 23

    async def test_add_git_event(
        self,
        client: TestClient,
        session_service: SessionService,
        chronicle_svc: ChronicleService,
    ):
        """Adding a git event with hash."""
        session = await session_service.create_session(
            name="Test",
            model="sonnet",
            source=GitSource(
                repo="https://github.com/org/repo",
                branch="main",
            ),
        )
        await chronicle_svc.create_chronicle(session.id)

        response = client.post(
            f"/api/v1/forge/chronicles/{session.id}/timeline",
            json={
                "t": 60,
                "type": "git",
                "label": "fix(thermal): null check",
                "hash": "a1d3e47",
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["hash"] == "a1d3e47"

    async def test_add_terminal_event(
        self,
        client: TestClient,
        session_service: SessionService,
        chronicle_svc: ChronicleService,
    ):
        """Adding a terminal event with exit code."""
        session = await session_service.create_session(
            name="Test",
            model="sonnet",
            source=GitSource(
                repo="https://github.com/org/repo",
                branch="main",
            ),
        )
        await chronicle_svc.create_chronicle(session.id)

        response = client.post(
            f"/api/v1/forge/chronicles/{session.id}/timeline",
            json={
                "t": 40,
                "type": "terminal",
                "label": "python -m pytest tests/ 6 passed",
                "exit": 0,
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["exit"] == 0

    def test_add_event_no_chronicle(self, client: TestClient):
        """Returns 404 when no chronicle exists for session."""
        fake_id = uuid4()
        response = client.post(
            f"/api/v1/forge/chronicles/{fake_id}/timeline",
            json={"t": 0, "type": "session", "label": "start"},
        )
        assert response.status_code == 404

    def test_add_event_invalid_type(self, client: TestClient):
        """Returns 422 for invalid event type."""
        fake_id = uuid4()
        response = client.post(
            f"/api/v1/forge/chronicles/{fake_id}/timeline",
            json={"t": 0, "type": "invalid_type", "label": "test"},
        )
        assert response.status_code == 422

    def test_add_event_service_unavailable(self, client_no_chronicles: TestClient):
        """Returns 503 when chronicle service is not configured."""
        fake_id = uuid4()
        response = client_no_chronicles.post(
            f"/api/v1/forge/chronicles/{fake_id}/timeline",
            json={"t": 0, "type": "session", "label": "start"},
        )
        assert response.status_code == 503


class TestTimelineRoundTrip:
    """Integration tests: add events then fetch timeline."""

    async def test_add_then_get(
        self,
        client: TestClient,
        session_service: SessionService,
        chronicle_svc: ChronicleService,
    ):
        """Events added via POST appear in GET timeline."""
        session = await session_service.create_session(
            name="Test",
            model="sonnet",
            source=GitSource(
                repo="https://github.com/org/repo",
                branch="main",
            ),
        )
        await chronicle_svc.create_chronicle(session.id)

        # Add events
        for ev in [
            {"t": 0, "type": "session", "label": "Session started"},
            {"t": 5, "type": "message", "label": "Review code", "tokens": 1000},
            {
                "t": 10,
                "type": "file",
                "label": "src/a.py",
                "action": "created",
                "ins": 50,
                "del": 0,
            },
            {"t": 30, "type": "git", "label": "feat: add file", "hash": "abc1234"},
        ]:
            resp = client.post(f"/api/v1/forge/chronicles/{session.id}/timeline", json=ev)
            assert resp.status_code == 201

        # Get timeline
        response = client.get(f"/api/v1/forge/chronicles/{session.id}/timeline")
        assert response.status_code == 200
        data = response.json()

        assert len(data["events"]) == 4
        assert len(data["files"]) == 1
        assert data["files"][0]["path"] == "src/a.py"
        assert data["files"][0]["status"] == "new"
        assert len(data["commits"]) == 1
        assert data["commits"][0]["hash"] == "abc1234"
        assert data["token_burn"][0] == 1000
