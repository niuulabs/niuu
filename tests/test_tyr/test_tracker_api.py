"""Tests for tracker REST API endpoints.

Tests the tracker browsing endpoints by overriding the
_resolve_tracker_adapters FastAPI dependency with a mock TrackerPort.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tyr.api.tracker import create_canonical_tracker_router, create_tracker_router, resolve_trackers
from tyr.config import AuthConfig
from tyr.domain.models import (
    Phase,
    PhaseStatus,
    Raid,
    RaidStatus,
    Saga,
    SagaStatus,
    TrackerIssue,
    TrackerMilestone,
    TrackerProject,
)
from tyr.ports.saga_repository import SagaRepository
from tyr.ports.tracker import TrackerPort

# ---------------------------------------------------------------------------
# Mock TrackerPort implementation
# ---------------------------------------------------------------------------


class MockTracker(TrackerPort):
    """In-memory mock tracker for API tests."""

    def __init__(self) -> None:
        self.projects: list[TrackerProject] = []
        self.milestones: dict[str, list[TrackerMilestone]] = {}
        self.issues: dict[str, list[TrackerIssue]] = {}

    async def create_saga(self, saga: Saga, *, description: str = "") -> str:
        return "saga-created"

    async def create_phase(self, phase: Phase, *, project_id: str = "") -> str:
        return "phase-created"

    async def create_raid(self, raid: Raid, *, project_id: str = "", milestone_id: str = "") -> str:
        return "raid-created"

    async def update_raid_state(self, raid_id: str, state: RaidStatus) -> None:
        pass

    async def close_raid(self, raid_id: str) -> None:
        pass

    async def get_saga(self, saga_id: str) -> Saga:
        now = datetime.now(UTC)
        return Saga(
            id=uuid4(),
            tracker_id=saga_id,
            tracker_type="mock",
            slug="test",
            name="Test",
            repos=[],
            feature_branch="feat/test",
            status=SagaStatus.ACTIVE,
            confidence=0.0,
            created_at=now,
            base_branch="dev",
        )

    async def get_phase(self, tracker_id: str) -> Phase:
        return Phase(
            id=uuid4(),
            saga_id=UUID(int=0),
            tracker_id=tracker_id,
            number=1,
            name="P1",
            status=PhaseStatus.PENDING,
            confidence=0.0,
        )

    async def get_raid(self, tracker_id: str) -> Raid:
        now = datetime.now(UTC)
        return Raid(
            id=uuid4(),
            phase_id=UUID(int=0),
            tracker_id=tracker_id,
            name="R1",
            description="",
            acceptance_criteria=[],
            declared_files=[],
            estimate_hours=None,
            status=RaidStatus.PENDING,
            confidence=0.0,
            session_id=None,
            branch=None,
            chronicle_summary=None,
            pr_url=None,
            pr_id=None,
            retry_count=0,
            created_at=now,
            updated_at=now,
        )

    async def list_pending_raids(self, phase_id: str) -> list[Raid]:
        return []

    async def list_projects(self) -> list[TrackerProject]:
        return self.projects

    async def get_project(self, project_id: str) -> TrackerProject:
        for p in self.projects:
            if p.id == project_id:
                return p
        raise ValueError(f"Project not found: {project_id}")

    async def list_milestones(self, project_id: str) -> list[TrackerMilestone]:
        return self.milestones.get(project_id, [])

    async def list_issues(
        self,
        project_id: str,
        milestone_id: str | None = None,
    ) -> list[TrackerIssue]:
        issues = self.issues.get(project_id, [])
        if milestone_id:
            issues = [i for i in issues if i.milestone_id == milestone_id]
        return issues

    async def get_project_full(
        self, project_id: str
    ) -> tuple[TrackerProject, list[TrackerMilestone], list[TrackerIssue]]:
        project = await self.get_project(project_id)
        milestones = await self.list_milestones(project_id)
        issues = await self.list_issues(project_id)
        return project, milestones, issues

    async def get_blocked_identifiers(self, project_id: str) -> set[str]:
        return getattr(self, "_blocked", set())

    async def get_raid_progress_for_saga(self, saga_tracker_id: str) -> list[Raid]:
        return []

    async def update_raid_progress(self, tracker_id: str, **kwargs: object) -> Raid:  # noqa: ANN003
        now = datetime.now(UTC)
        return Raid(
            id=uuid4(),
            phase_id=UUID(int=0),
            tracker_id=tracker_id,
            name="R1",
            description="",
            acceptance_criteria=[],
            declared_files=[],
            estimate_hours=None,
            status=RaidStatus.PENDING,
            confidence=0.0,
            session_id=None,
            branch=None,
            chronicle_summary=None,
            pr_url=None,
            pr_id=None,
            retry_count=0,
            created_at=now,
            updated_at=now,
        )

    async def get_raid_by_session(self, session_id: str) -> Raid | None:
        return None

    async def list_raids_by_status(self, status: RaidStatus) -> list[Raid]:
        return []

    async def get_raid_by_id(self, raid_id: UUID) -> Raid | None:
        return None

    async def add_confidence_event(self, tracker_id: str, event: object) -> None:  # noqa: ANN001
        pass

    async def get_confidence_events(self, tracker_id: str) -> list:
        return []

    async def all_raids_merged(self, phase_tracker_id: str) -> bool:
        return False

    async def list_phases_for_saga(self, saga_tracker_id: str) -> list[Phase]:
        return []

    async def update_phase_status(self, phase_tracker_id: str, status: PhaseStatus) -> Phase | None:
        return None

    async def get_saga_for_raid(self, tracker_id: str) -> Saga | None:
        return None

    async def get_phase_for_raid(self, tracker_id: str) -> Phase | None:
        return None

    async def get_owner_for_raid(self, tracker_id: str) -> str | None:
        return None

    async def save_session_message(self, message: object) -> None:  # noqa: ANN001
        pass

    async def get_session_messages(self, tracker_id: str) -> list:
        return []


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_tracker() -> MockTracker:
    tracker = MockTracker()
    tracker.projects = [
        TrackerProject(
            id="proj-1",
            name="Alpha",
            description="First project",
            status="started",
            url="https://linear.app/proj-1",
            milestone_count=2,
            issue_count=5,
        ),
        TrackerProject(
            id="proj-2",
            name="Beta",
            description="Second project",
            status="planned",
            url="https://linear.app/proj-2",
            milestone_count=0,
            issue_count=0,
        ),
        TrackerProject(
            id="proj-3",
            name="Gamma",
            description="Completed project",
            status="completed",
            url="https://linear.app/proj-3",
            milestone_count=1,
            issue_count=2,
        ),
    ]
    tracker.milestones = {
        "proj-1": [
            TrackerMilestone(
                id="ms-1",
                project_id="proj-1",
                name="Phase 1",
                description="First phase",
                sort_order=1,
                progress=0.5,
            ),
            TrackerMilestone(
                id="ms-2",
                project_id="proj-1",
                name="Phase 2",
                description="Second phase",
                sort_order=2,
                progress=0.0,
            ),
        ],
    }
    tracker.issues = {
        "proj-1": [
            TrackerIssue(
                id="i-1",
                identifier="ALPHA-1",
                title="Setup",
                description="Initial setup",
                status="Todo",
                assignee="Dev",
                labels=["setup"],
                priority=1,
                url="https://linear.app/i-1",
                milestone_id="ms-1",
            ),
            TrackerIssue(
                id="i-2",
                identifier="ALPHA-2",
                title="Build",
                description="Build things",
                status="In Progress",
                assignee=None,
                labels=[],
                priority=2,
                url="https://linear.app/i-2",
                milestone_id="ms-2",
            ),
        ],
    }
    return tracker


class MockSagaRepo(SagaRepository):
    """In-memory saga repository for tests."""

    def __init__(self) -> None:
        self.sagas: list[Saga] = []
        self.phases: list[Phase] = []
        self.raids: list[Raid] = []

    @contextlib.asynccontextmanager
    async def begin(self):  # noqa: ANN201
        yield None

    async def save_saga(self, saga: Saga, *, conn=None) -> None:  # noqa: ANN001
        self.sagas.append(saga)

    async def save_phase(self, phase: Phase, *, conn=None) -> None:  # noqa: ANN001
        self.phases.append(phase)

    async def save_raid(self, raid: Raid, *, conn=None) -> None:  # noqa: ANN001
        self.raids.append(raid)

    async def get_saga_by_slug(self, slug: str) -> Saga | None:
        return next((s for s in self.sagas if s.slug == slug), None)

    async def list_sagas(self, *, owner_id: str | None = None) -> list[Saga]:
        return list(self.sagas)

    async def get_saga(self, saga_id, *, owner_id: str | None = None) -> Saga | None:
        return next((s for s in self.sagas if s.id == saga_id), None)

    async def count_by_status(self) -> dict[str, int]:
        from tyr.domain.models import RaidStatus

        counts = {s.value: 0 for s in RaidStatus}
        for raid in self.raids:
            counts[raid.status.value] += 1
        return counts

    async def delete_saga(self, saga_id, *, owner_id=None) -> bool:
        before = len(self.sagas)
        self.sagas = [
            s
            for s in self.sagas
            if not (s.id == saga_id and (owner_id is None or s.owner_id == owner_id))
        ]
        return len(self.sagas) < before

    async def update_saga_status(self, saga_id: UUID, status: SagaStatus) -> None:
        pass

    async def update_saga_workflow(
        self,
        saga_id: UUID,
        *,
        workflow_id: UUID | None,
        workflow_version: str | None,
        workflow_snapshot: dict | None,
        owner_id: str | None = None,
    ) -> None:
        updated: list[Saga] = []
        for saga in self.sagas:
            if saga.id != saga_id:
                updated.append(saga)
                continue
            if owner_id is not None and saga.owner_id != owner_id:
                updated.append(saga)
                continue
            updated.append(
                Saga(
                    id=saga.id,
                    tracker_id=saga.tracker_id,
                    tracker_type=saga.tracker_type,
                    slug=saga.slug,
                    name=saga.name,
                    repos=saga.repos,
                    feature_branch=saga.feature_branch,
                    status=saga.status,
                    confidence=saga.confidence,
                    created_at=saga.created_at,
                    base_branch=saga.base_branch,
                    owner_id=saga.owner_id,
                    workflow_id=workflow_id,
                    workflow_version=workflow_version,
                    workflow_snapshot=workflow_snapshot,
                )
            )
        self.sagas = updated

    async def get_phases_by_saga(self, saga_id: UUID) -> list[Phase]:
        return [phase for phase in self.phases if phase.saga_id == saga_id]

    async def get_raids_by_phase(self, phase_id: UUID) -> list[Raid]:
        return [raid for raid in self.raids if raid.phase_id == phase_id]


def _build_test_client(mock_tracker: MockTracker) -> TestClient:
    app = FastAPI()
    app.state.legacy_route_hits = {}
    app.include_router(create_canonical_tracker_router())
    app.include_router(create_tracker_router())
    app.dependency_overrides[resolve_trackers] = lambda: [mock_tracker]
    app.state.saga_repo = MockSagaRepo()
    mock_settings = MagicMock()
    mock_settings.auth = AuthConfig(allow_anonymous_dev=True)
    app.state.settings = mock_settings
    return TestClient(app)


@pytest.fixture
def client(mock_tracker: MockTracker) -> TestClient:
    return _build_test_client(mock_tracker)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestListProjects:
    def test_filters_terminal_projects(self, client: TestClient):
        resp = client.get("/api/v1/tyr/tracker/projects")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["id"] == "proj-1"
        assert data[1]["id"] == "proj-2"
        assert {project["id"] for project in data} == {"proj-1", "proj-2"}

    def test_canonical_projects_match_legacy(self, client: TestClient):
        legacy = client.get("/api/v1/tyr/tracker/projects")
        canonical = client.get("/api/v1/tracker/projects")
        assert legacy.status_code == 200
        assert canonical.status_code == 200
        assert canonical.json() == legacy.json()
        assert legacy.headers["X-Niuu-Canonical-Route"] == "/api/v1/tracker/projects"


class TestGetProject:
    def test_found(self, client: TestClient):
        resp = client.get("/api/v1/tyr/tracker/projects/proj-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "proj-1"
        assert data["name"] == "Alpha"

    def test_not_found(self, client: TestClient):
        resp = client.get("/api/v1/tyr/tracker/projects/nonexistent")
        assert resp.status_code == 404


class TestListMilestones:
    def test_returns_milestones(self, client: TestClient):
        resp = client.get("/api/v1/tyr/tracker/projects/proj-1/milestones")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["name"] == "Phase 1"

    def test_empty_milestones(self, client: TestClient):
        resp = client.get("/api/v1/tyr/tracker/projects/proj-2/milestones")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_canonical_milestones_match_legacy(self, client: TestClient):
        legacy = client.get("/api/v1/tyr/tracker/projects/proj-1/milestones")
        canonical = client.get("/api/v1/tracker/projects/proj-1/milestones")
        assert legacy.status_code == 200
        assert canonical.status_code == 200
        assert canonical.json() == legacy.json()


class TestListIssues:
    def test_all_issues(self, client: TestClient):
        resp = client.get("/api/v1/tyr/tracker/projects/proj-1/issues")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    def test_filtered_by_milestone(self, client: TestClient):
        resp = client.get("/api/v1/tyr/tracker/projects/proj-1/issues?milestone_id=ms-1")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["identifier"] == "ALPHA-1"

    def test_no_matching_milestone(self, client: TestClient):
        resp = client.get("/api/v1/tyr/tracker/projects/proj-1/issues?milestone_id=nonexistent")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_canonical_project_issues_match_legacy(self, client: TestClient):
        legacy = client.get("/api/v1/tyr/tracker/projects/proj-1/issues?milestone_id=ms-1")
        canonical = client.get("/api/v1/tracker/projects/proj-1/issues?milestone_id=ms-1")
        assert legacy.status_code == 200
        assert canonical.status_code == 200
        assert canonical.json() == legacy.json()


class TestImportProject:
    def test_success(self, client: TestClient):
        resp = client.post(
            "/api/v1/tyr/tracker/import",
            json={
                "project_id": "proj-1",
                "repos": ["org/repo"],
                "base_branch": "dev",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["tracker_id"] == "proj-1"
        assert data["name"] == "Alpha"
        assert data["repos"] == ["org/repo"]
        assert data["feature_branch"] == "feat/alpha"
        assert data["status"] == "ACTIVE"
        assert data["phase_count"] == 2
        assert data["raid_count"] == 5

    def test_project_not_found(self, client: TestClient):
        resp = client.post(
            "/api/v1/tyr/tracker/import",
            json={
                "project_id": "nonexistent",
                "repos": ["org/repo"],
                "base_branch": "dev",
            },
        )
        assert resp.status_code == 404

    def test_duplicate_slug_returns_409(self, client: TestClient):
        client.app.state.saga_repo.sagas.append(
            Saga(
                id=uuid4(),
                tracker_id="existing",
                tracker_type="mock",
                slug="alpha",
                name="Existing",
                repos=["org/repo"],
                feature_branch="feat/alpha",
                status=SagaStatus.ACTIVE,
                confidence=0.0,
                created_at=datetime.now(UTC),
                base_branch="dev",
                owner_id="dev-user",
            )
        )

        resp = client.post(
            "/api/v1/tyr/tracker/import",
            json={
                "project_id": "proj-1",
                "repos": ["org/repo"],
                "base_branch": "dev",
            },
        )
        assert resp.status_code == 409
        assert "already exists" in resp.json()["detail"]

    def test_canonical_import_matches_legacy_shape(self, mock_tracker: MockTracker):
        legacy_client = _build_test_client(mock_tracker)
        canonical_client = _build_test_client(mock_tracker)

        legacy = legacy_client.post(
            "/api/v1/tyr/tracker/import",
            json={
                "project_id": "proj-1",
                "repos": ["org/repo"],
                "base_branch": "dev",
            },
        )
        canonical = canonical_client.post(
            "/api/v1/tracker/import",
            json={
                "project_id": "proj-1",
                "repos": ["org/repo"],
                "base_branch": "dev",
            },
        )
        assert legacy.status_code == 200
        assert canonical.status_code == 200
        assert canonical.json()["tracker_id"] == legacy.json()["tracker_id"]
        assert canonical.json()["name"] == legacy.json()["name"]
