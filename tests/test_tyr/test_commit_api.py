"""Tests for POST /sagas/commit endpoint."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tyr.api.sagas import (
    create_sagas_router,
    resolve_git,
    resolve_saga_repo,
)
from tyr.api.tracker import resolve_trackers
from tyr.config import AuthConfig, ReviewConfig
from tyr.domain.models import (
    PhaseStatus,
    RaidStatus,
    Saga,
    SagaStatus,
)
from tyr.ports.git import GitPort

from .test_tracker_api import MockSagaRepo, MockTracker

# ---------------------------------------------------------------------------
# Mock implementations
# ---------------------------------------------------------------------------


class MockGit(GitPort):
    """In-memory git adapter for tests."""

    def __init__(self) -> None:
        self.branches_created: list[tuple[str, str, str]] = []

    async def create_branch(self, repo: str, branch: str, base: str) -> None:
        self.branches_created.append((repo, branch, base))

    async def merge_branch(self, repo: str, source: str, target: str) -> None:
        pass

    async def delete_branch(self, repo: str, branch: str) -> None:
        pass

    async def create_pr(self, repo: str, source: str, target: str, title: str) -> str:
        return "pr-1"

    async def get_pr_status(self, pr_id: str):  # noqa: ANN201
        return None

    async def get_pr_changed_files(self, pr_id: str) -> list[str]:
        return []


def _dev_settings() -> MagicMock:
    s = MagicMock()
    s.auth = AuthConfig(allow_anonymous_dev=True)
    s.review = ReviewConfig()
    return s


VALID_COMMIT_BODY = {
    "name": "My Saga",
    "slug": "my-saga",
    "repos": ["org/repo"],
    "base_branch": "main",
    "phases": [
        {
            "name": "Phase 1",
            "raids": [
                {
                    "name": "Setup database",
                    "description": "Create tables",
                    "acceptance_criteria": ["Tables exist"],
                    "declared_files": ["migrations/001.sql"],
                    "estimate_hours": 2.0,
                },
                {
                    "name": "Add API endpoint",
                    "description": "REST endpoint",
                    "acceptance_criteria": ["Endpoint works"],
                    "declared_files": ["src/api.py"],
                    "estimate_hours": 3.0,
                },
            ],
        },
        {
            "name": "Phase 2",
            "raids": [
                {
                    "name": "Write tests",
                    "description": "Unit tests",
                    "acceptance_criteria": ["Coverage > 85%"],
                    "declared_files": ["tests/test_api.py"],
                    "estimate_hours": 1.5,
                },
            ],
        },
    ],
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_tracker() -> MockTracker:
    return MockTracker()


@pytest.fixture
def saga_repo() -> MockSagaRepo:
    return MockSagaRepo()


@pytest.fixture
def mock_git() -> MockGit:
    return MockGit()


@pytest.fixture
def client(
    mock_tracker: MockTracker,
    saga_repo: MockSagaRepo,
    mock_git: MockGit,
) -> TestClient:
    app = FastAPI()
    app.include_router(create_sagas_router())
    app.dependency_overrides[resolve_trackers] = lambda: [mock_tracker]
    app.dependency_overrides[resolve_saga_repo] = lambda: saga_repo
    app.dependency_overrides[resolve_git] = lambda: mock_git
    app.state.settings = _dev_settings()
    return TestClient(app)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCommitSaga:
    def test_success_returns_201(self, client: TestClient) -> None:
        resp = client.post("/api/v1/tyr/sagas/commit", json=VALID_COMMIT_BODY)
        assert resp.status_code == 201

    def test_returns_saga_with_ids(self, client: TestClient) -> None:
        resp = client.post("/api/v1/tyr/sagas/commit", json=VALID_COMMIT_BODY)
        data = resp.json()
        assert data["slug"] == "my-saga"
        assert data["name"] == "My Saga"
        assert data["repos"] == ["org/repo"]
        assert data["feature_branch"] == "feat/my-saga"
        assert data["base_branch"] == "main"
        assert data["status"] == "ACTIVE"
        assert data["created_at"]
        assert data["phase_summary"] == {"total": 2, "completed": 0}
        # Has a valid UUID id
        UUID(data["id"])
        # Has tracker_id from mock
        assert data["tracker_id"] == "saga-created"
        assert data["tracker_type"] == "MockTracker"

    def test_phases_have_correct_status(self, client: TestClient) -> None:
        resp = client.post("/api/v1/tyr/sagas/commit", json=VALID_COMMIT_BODY)
        data = resp.json()
        phases = data["phases"]
        assert len(phases) == 2
        assert phases[0]["status"] == "ACTIVE"
        assert phases[0]["number"] == 1
        assert phases[1]["status"] == "GATED"
        assert phases[1]["number"] == 2

    def test_phases_have_tracker_ids(self, client: TestClient) -> None:
        resp = client.post("/api/v1/tyr/sagas/commit", json=VALID_COMMIT_BODY)
        data = resp.json()
        for phase in data["phases"]:
            assert phase["tracker_id"] == "phase-created"

    def test_raids_have_correct_data(self, client: TestClient) -> None:
        resp = client.post("/api/v1/tyr/sagas/commit", json=VALID_COMMIT_BODY)
        data = resp.json()
        phase1_raids = data["phases"][0]["raids"]
        assert len(phase1_raids) == 2
        assert phase1_raids[0]["name"] == "Setup database"
        assert phase1_raids[0]["tracker_id"] == "raid-created"
        assert phase1_raids[0]["status"] == "PENDING"

        phase2_raids = data["phases"][1]["raids"]
        assert len(phase2_raids) == 1
        assert phase2_raids[0]["name"] == "Write tests"

    def test_persists_saga(self, client: TestClient, saga_repo: MockSagaRepo) -> None:
        client.post("/api/v1/tyr/sagas/commit", json=VALID_COMMIT_BODY)
        assert len(saga_repo.sagas) == 1
        saga = saga_repo.sagas[0]
        assert saga.slug == "my-saga"
        assert saga.tracker_id == "saga-created"
        assert saga.status == SagaStatus.ACTIVE

    def test_persists_phases(self, client: TestClient, saga_repo: MockSagaRepo) -> None:
        client.post("/api/v1/tyr/sagas/commit", json=VALID_COMMIT_BODY)
        assert len(saga_repo.phases) == 2
        assert saga_repo.phases[0].status == PhaseStatus.ACTIVE
        assert saga_repo.phases[1].status == PhaseStatus.GATED

    def test_persists_raids(self, client: TestClient, saga_repo: MockSagaRepo) -> None:
        client.post("/api/v1/tyr/sagas/commit", json=VALID_COMMIT_BODY)
        assert len(saga_repo.raids) == 3
        for raid in saga_repo.raids:
            assert raid.status == RaidStatus.PENDING
            assert raid.tracker_id == "raid-created"

    def test_creates_feature_branch(self, client: TestClient, mock_git: MockGit) -> None:
        client.post("/api/v1/tyr/sagas/commit", json=VALID_COMMIT_BODY)
        assert len(mock_git.branches_created) == 1
        repo, branch, base = mock_git.branches_created[0]
        assert repo == "org/repo"
        assert branch == "feat/my-saga"
        assert base == "main"

    def test_creates_branches_for_multiple_repos(
        self, client: TestClient, mock_git: MockGit
    ) -> None:
        body = {**VALID_COMMIT_BODY, "repos": ["org/repo-a", "org/repo-b"]}
        client.post("/api/v1/tyr/sagas/commit", json=body)
        assert len(mock_git.branches_created) == 2
        assert mock_git.branches_created[0][0] == "org/repo-a"
        assert mock_git.branches_created[1][0] == "org/repo-b"


class TestCommitSagaIdempotency:
    def test_duplicate_slug_returns_409(self, client: TestClient, saga_repo: MockSagaRepo) -> None:
        # Pre-populate with existing saga
        saga_repo.sagas.append(
            Saga(
                id=uuid4(),
                tracker_id="existing",
                tracker_type="mock",
                slug="my-saga",
                name="Existing",
                repos=["org/repo"],
                feature_branch="feat/my-saga",
                status=SagaStatus.ACTIVE,
                confidence=0.5,
                created_at=datetime.now(UTC),
                base_branch="dev",
                owner_id="dev-user",
            )
        )
        resp = client.post("/api/v1/tyr/sagas/commit", json=VALID_COMMIT_BODY)
        assert resp.status_code == 409
        assert "already exists" in resp.json()["detail"]


class TestCommitSagaValidation:
    def test_empty_phases_returns_422(self, client: TestClient) -> None:
        body = {**VALID_COMMIT_BODY, "phases": []}
        resp = client.post("/api/v1/tyr/sagas/commit", json=body)
        assert resp.status_code == 422

    def test_no_tracker_returns_503(
        self,
        saga_repo: MockSagaRepo,
        mock_git: MockGit,
    ) -> None:
        app = FastAPI()
        app.include_router(create_sagas_router())
        app.dependency_overrides[resolve_trackers] = lambda: []
        app.dependency_overrides[resolve_saga_repo] = lambda: saga_repo
        app.dependency_overrides[resolve_git] = lambda: mock_git
        app.state.settings = _dev_settings()
        client = TestClient(app)
        resp = client.post("/api/v1/tyr/sagas/commit", json=VALID_COMMIT_BODY)
        assert resp.status_code == 503

    def test_missing_name_returns_422(self, client: TestClient) -> None:
        body = {k: v for k, v in VALID_COMMIT_BODY.items() if k != "name"}
        resp = client.post("/api/v1/tyr/sagas/commit", json=body)
        assert resp.status_code == 422

    def test_missing_slug_returns_422(self, client: TestClient) -> None:
        body = {k: v for k, v in VALID_COMMIT_BODY.items() if k != "slug"}
        resp = client.post("/api/v1/tyr/sagas/commit", json=body)
        assert resp.status_code == 422


class TestCommitSagaConfidence:
    def test_saga_has_initial_confidence_from_config(self, client: TestClient) -> None:
        resp = client.post("/api/v1/tyr/sagas/commit", json=VALID_COMMIT_BODY)
        data = resp.json()
        assert data["confidence"] == ReviewConfig().initial_confidence

    def test_custom_initial_confidence(
        self,
        mock_tracker: MockTracker,
        saga_repo: MockSagaRepo,
        mock_git: MockGit,
    ) -> None:
        app = FastAPI()
        app.include_router(create_sagas_router())
        app.dependency_overrides[resolve_trackers] = lambda: [mock_tracker]
        app.dependency_overrides[resolve_saga_repo] = lambda: saga_repo
        app.dependency_overrides[resolve_git] = lambda: mock_git
        settings = _dev_settings()
        settings.review = ReviewConfig(initial_confidence=0.8)
        app.state.settings = settings
        client = TestClient(app)
        resp = client.post("/api/v1/tyr/sagas/commit", json=VALID_COMMIT_BODY)
        assert resp.json()["confidence"] == 0.8


class TestCommitSagaCustomBaseBranch:
    def test_custom_base_branch(self, client: TestClient, mock_git: MockGit) -> None:
        body = {**VALID_COMMIT_BODY, "slug": "custom-base", "base_branch": "develop"}
        resp = client.post("/api/v1/tyr/sagas/commit", json=body)
        assert resp.status_code == 201
        data = resp.json()
        assert data["base_branch"] == "develop"
        _, _, base = mock_git.branches_created[0]
        assert base == "develop"

    def test_missing_base_branch_returns_422(self, client: TestClient) -> None:
        body = {k: v for k, v in VALID_COMMIT_BODY.items() if k != "base_branch"}
        resp = client.post("/api/v1/tyr/sagas/commit", json=body)
        assert resp.status_code == 422


class TestCommitSagaOwnership:
    def test_saga_owner_set_from_principal(
        self, client: TestClient, saga_repo: MockSagaRepo
    ) -> None:
        client.post("/api/v1/tyr/sagas/commit", json=VALID_COMMIT_BODY)
        assert saga_repo.sagas[0].owner_id == "dev-user"


class TestCommitSagaTrackerFailure:
    """Tracker calls are best-effort — failures are logged, not raised."""

    def test_tracker_create_saga_failure_returns_502(
        self,
        saga_repo: MockSagaRepo,
        mock_git: MockGit,
    ) -> None:
        class FailingSagaTracker(MockTracker):
            async def create_saga(self, saga, *, description=""):  # noqa: ANN001
                raise ConnectionError("Tracker down")

        app = FastAPI()
        app.include_router(create_sagas_router())
        app.dependency_overrides[resolve_trackers] = lambda: [FailingSagaTracker()]
        app.dependency_overrides[resolve_saga_repo] = lambda: saga_repo
        app.dependency_overrides[resolve_git] = lambda: mock_git
        app.state.settings = _dev_settings()
        client = TestClient(app)

        resp = client.post("/api/v1/tyr/sagas/commit", json=VALID_COMMIT_BODY)
        assert resp.status_code == 502

    def test_tracker_create_phase_failure_returns_502(
        self,
        saga_repo: MockSagaRepo,
        mock_git: MockGit,
    ) -> None:
        class FailingPhaseTracker(MockTracker):
            async def create_phase(self, phase, *, project_id=""):  # noqa: ANN001
                raise ConnectionError("Tracker down")

        app = FastAPI()
        app.include_router(create_sagas_router())
        app.dependency_overrides[resolve_trackers] = lambda: [FailingPhaseTracker()]
        app.dependency_overrides[resolve_saga_repo] = lambda: saga_repo
        app.dependency_overrides[resolve_git] = lambda: mock_git
        app.state.settings = _dev_settings()
        client = TestClient(app)

        resp = client.post("/api/v1/tyr/sagas/commit", json=VALID_COMMIT_BODY)
        assert resp.status_code == 502

    def test_tracker_create_raid_failure_returns_502(
        self,
        saga_repo: MockSagaRepo,
        mock_git: MockGit,
    ) -> None:
        class FailingRaidTracker(MockTracker):
            async def create_raid(self, raid, *, project_id="", milestone_id=""):  # noqa: ANN001
                raise ConnectionError("Tracker down")

        app = FastAPI()
        app.include_router(create_sagas_router())
        app.dependency_overrides[resolve_trackers] = lambda: [FailingRaidTracker()]
        app.dependency_overrides[resolve_saga_repo] = lambda: saga_repo
        app.dependency_overrides[resolve_git] = lambda: mock_git
        app.state.settings = _dev_settings()
        client = TestClient(app)

        resp = client.post("/api/v1/tyr/sagas/commit", json=VALID_COMMIT_BODY)
        assert resp.status_code == 502


class TestCommitSagaGitFailure:
    """Git branch creation is best-effort — failures are logged and surfaced as warnings."""

    def test_git_failure_still_returns_201(
        self,
        saga_repo: MockSagaRepo,
    ) -> None:
        class FailingGit(MockGit):
            async def create_branch(self, repo: str, branch: str, base: str) -> None:
                raise ConnectionError("GitHub API down")

        app = FastAPI()
        app.include_router(create_sagas_router())
        app.dependency_overrides[resolve_trackers] = lambda: [MockTracker()]
        app.dependency_overrides[resolve_saga_repo] = lambda: saga_repo
        app.dependency_overrides[resolve_git] = lambda: FailingGit()
        app.state.settings = _dev_settings()
        client = TestClient(app)

        resp = client.post("/api/v1/tyr/sagas/commit", json=VALID_COMMIT_BODY)
        assert resp.status_code == 201
        data = resp.json()
        # Saga is persisted despite git failure
        assert len(saga_repo.sagas) == 1
        assert len(saga_repo.phases) == 2
        assert len(saga_repo.raids) == 3
        # Response includes warning about the failure
        assert len(data["warnings"]) == 1
        assert "feat/my-saga" in data["warnings"][0]
        assert "org/repo" in data["warnings"][0]

    def test_partial_git_failure_reports_failed_repos(
        self,
        saga_repo: MockSagaRepo,
    ) -> None:
        class PartialFailGit(MockGit):
            async def create_branch(self, repo: str, branch: str, base: str) -> None:
                if repo == "org/repo-b":
                    raise ConnectionError("GitHub API down")
                self.branches_created.append((repo, branch, base))

        git = PartialFailGit()
        app = FastAPI()
        app.include_router(create_sagas_router())
        app.dependency_overrides[resolve_trackers] = lambda: [MockTracker()]
        app.dependency_overrides[resolve_saga_repo] = lambda: saga_repo
        app.dependency_overrides[resolve_git] = lambda: git
        app.state.settings = _dev_settings()
        client = TestClient(app)

        body = {**VALID_COMMIT_BODY, "repos": ["org/repo-a", "org/repo-b"]}
        resp = client.post("/api/v1/tyr/sagas/commit", json=body)
        assert resp.status_code == 201
        data = resp.json()
        # repo-a succeeded, repo-b failed
        assert len(git.branches_created) == 1
        assert len(data["warnings"]) == 1
        assert "org/repo-b" in data["warnings"][0]

    def test_no_warnings_on_success(self, client: TestClient) -> None:
        resp = client.post("/api/v1/tyr/sagas/commit", json=VALID_COMMIT_BODY)
        assert resp.json()["warnings"] == []
