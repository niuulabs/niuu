"""Tests for run review REST API endpoints."""

from __future__ import annotations
from contextlib import suppress

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ting.api.runs import (
    create_runs_router,
    resolve_git,
    resolve_run_repo,
    resolve_tracker,
    resolve_volundr,
)
from ting.config import AuthConfig, ReviewConfig
from ting.domain.models import (
    ConfidenceEvent,
    ConfidenceEventType,
    Phase,
    PhaseStatus,
    PRStatus,
    Run,
    RunStatus,
    Saga,
    SagaStatus,
    SessionMessage,
)
from ting.ports.git import GitPort
from ting.ports.saga_repository import SagaRepository
from ting.ports.volundr import SpawnRequest, VolundrPort, VolundrSession

from .test_tracker_api import MockTracker

# ---------------------------------------------------------------------------
# Default config for tests
# ---------------------------------------------------------------------------

REVIEW_CFG = ReviewConfig()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PHASE_ID = uuid4()
SAGA_ID = uuid4()


# ---------------------------------------------------------------------------
# Stateful mock tracker for run API tests
# ---------------------------------------------------------------------------


class StatefulMockTracker(MockTracker):
    """MockTracker with stateful storage for run review tests."""

    def __init__(self) -> None:
        super().__init__()
        self.runs: dict[UUID, Run] = {}
        self.events: dict[str, list[ConfidenceEvent]] = {}  # keyed by tracker_id
        self.events_by_run_id: dict[UUID, list[ConfidenceEvent]] = {}
        self.saga: Saga | None = None
        self.phase: Phase | None = None
        self._all_merged: bool = False
        self.messages: dict[UUID, list[SessionMessage]] = {}

    async def get_run(self, tracker_id: str) -> Run:
        # Look up by UUID string (run_id) first, then by tracker_id
        with suppress(ValueError):
            uid = UUID(tracker_id)
            if uid in self.runs:
                return self.runs[uid]
        for run in self.runs.values():
            if run.tracker_id == tracker_id:
                return run
        from ting.domain.exceptions import RunNotFoundError

        raise RunNotFoundError(f"Run not found: {tracker_id}")

    async def get_run_by_id(self, run_id: UUID) -> Run | None:
        return self.runs.get(run_id)

    async def update_run_progress(self, tracker_id: str, **kwargs: object) -> Run:  # noqa: ANN003
        run = next((r for r in self.runs.values() if r.tracker_id == tracker_id), None)
        if run is None:
            raise ValueError(f"Run not found: {tracker_id}")
        now = datetime.now(UTC)
        status = kwargs.get("status", run.status)
        retry_count = kwargs.get("retry_count", run.retry_count)
        events = self.events.get(tracker_id, [])
        confidence = events[-1].score_after if events else run.confidence
        updated = Run(
            id=run.id,
            phase_id=run.phase_id,
            tracker_id=run.tracker_id,
            name=run.name,
            description=run.description,
            acceptance_criteria=run.acceptance_criteria,
            declared_files=run.declared_files,
            estimate_hours=run.estimate_hours,
            status=status,  # type: ignore[arg-type]
            confidence=confidence,
            session_id=run.session_id,
            branch=run.branch,
            chronicle_summary=run.chronicle_summary,
            pr_url=run.pr_url,
            pr_id=run.pr_id,
            retry_count=retry_count,  # type: ignore[arg-type]
            created_at=run.created_at,
            updated_at=now,
        )
        self.runs[run.id] = updated
        return updated

    async def add_confidence_event(self, tracker_id: str, event: object) -> None:  # noqa: ANN001
        self.events.setdefault(tracker_id, []).append(event)  # type: ignore[arg-type]
        ce = event  # type: ignore[assignment]
        self.events_by_run_id.setdefault(ce.run_id, []).append(ce)  # type: ignore[union-attr]

    async def get_confidence_events(self, tracker_id: str) -> list:
        return self.events.get(tracker_id, [])

    async def get_saga_for_run(self, tracker_id: str) -> Saga | None:
        return self.saga

    async def get_phase_for_run(self, tracker_id: str) -> Phase | None:
        return self.phase

    async def all_runs_merged(self, phase_tracker_id: str) -> bool:
        return self._all_merged

    async def save_session_message(self, message: SessionMessage) -> None:
        self.messages.setdefault(message.run_id, []).append(message)

    async def get_session_messages(self, tracker_id: str) -> list:
        # Find runs matching tracker_id
        for run in self.runs.values():
            if run.tracker_id == tracker_id:
                return self.messages.get(run.id, [])
        return []


# ---------------------------------------------------------------------------
# Mock implementations
# ---------------------------------------------------------------------------


class MockVolundr(VolundrPort):
    """In-memory mock for Volundr port."""

    def __init__(self) -> None:
        self.pr_status = PRStatus(
            pr_id="42",
            url="https://github.com/org/repo/pull/42",
            state="open",
            mergeable=True,
            ci_passed=True,
        )
        self.chronicle = "Everything looks good"
        self.fail_pr_status = False

    async def spawn_session(
        self,
        request: SpawnRequest,
        *,
        auth_token: str | None = None,
    ) -> VolundrSession:
        return VolundrSession(
            id="session-1",
            name=request.name,
            status="running",
            tracker_issue_id=request.tracker_issue_id,
        )

    async def get_session(
        self,
        session_id: str,
        *,
        auth_token: str | None = None,
    ) -> VolundrSession | None:
        return VolundrSession(id=session_id, name="test", status="completed", tracker_issue_id=None)

    async def list_sessions(
        self,
        *,
        auth_token: str | None = None,
    ) -> list[VolundrSession]:
        return []

    async def get_chronicle_summary(self, session_id: str) -> str:
        return self.chronicle

    async def get_pr_status(self, session_id: str) -> PRStatus:
        if self.fail_pr_status:
            raise ConnectionError("Volundr unreachable")
        return self.pr_status

    async def send_message(
        self,
        session_id: str,
        message: str,
        *,
        auth_token: str | None = None,
    ) -> None:
        pass

    async def stop_session(self, session_id, *, auth_token=None):
        pass

    async def list_integration_ids(
        self,
        *,
        auth_token: str | None = None,
    ) -> list[str]:
        return []

    async def list_repos(self, *, auth_token: str | None = None) -> list[dict]:
        return []

    async def get_conversation(self, session_id: str) -> dict:
        stub = '{"confidence": 0.9, "approved": true, "issues": []}'
        return {"turns": [{"role": "assistant", "content": stub}]}

    async def get_last_assistant_message(self, session_id: str) -> str:
        return '{"confidence": 0.9, "approved": true, "summary": "stub", "issues": []}'

    async def subscribe_activity(self):
        return
        yield  # type: ignore[misc]  # pragma: no cover


class MockGit(GitPort):
    """In-memory mock for Git port."""

    def __init__(self) -> None:
        self.merged: list[tuple[str, str, str]] = []
        self.deleted: list[tuple[str, str]] = []
        self.fail_merge = False

    async def create_branch(self, repo: str, branch: str, base: str) -> None:
        pass

    async def merge_branch(self, repo: str, source: str, target: str) -> None:
        if self.fail_merge:
            raise RuntimeError("Merge conflict")
        self.merged.append((repo, source, target))

    async def delete_branch(self, repo: str, branch: str) -> None:
        self.deleted.append((repo, branch))

    async def create_pr(self, repo: str, source: str, target: str, title: str) -> str:
        return "pr-1"

    async def get_pr_status(self, pr_id: str) -> PRStatus:
        return PRStatus(
            pr_id=pr_id,
            url=f"https://github.com/org/repo/pull/{pr_id}",
            state="open",
            mergeable=True,
            ci_passed=True,
        )

    async def get_pr_changed_files(self, pr_id: str) -> list[str]:
        return []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_run(
    run_id: UUID | None = None,
    status: RunStatus = RunStatus.REVIEW,
    confidence: float = 0.5,
    session_id: str | None = "session-1",
    branch: str | None = "run/test-branch",
) -> Run:
    now = datetime.now(UTC)
    return Run(
        id=run_id or uuid4(),
        phase_id=PHASE_ID,
        tracker_id="tracker-1",
        name="Test run",
        description="A test run",
        acceptance_criteria=["it works"],
        declared_files=["src/main.py"],
        estimate_hours=2.0,
        status=status,
        confidence=confidence,
        session_id=session_id,
        branch=branch,
        chronicle_summary="All tests pass, code looks clean",
        pr_url=None,
        pr_id=None,
        retry_count=0,
        created_at=now,
        updated_at=now,
    )


def _make_saga() -> Saga:
    return Saga(
        id=SAGA_ID,
        tracker_id="proj-1",
        tracker_type="mock",
        slug="alpha",
        name="Alpha",
        repos=["org/repo"],
        feature_branch="feat/alpha",
        status=SagaStatus.ACTIVE,
        confidence=0.0,
        created_at=datetime.now(UTC),
        base_branch="dev",
    )


def _make_phase() -> Phase:
    return Phase(
        id=PHASE_ID,
        saga_id=SAGA_ID,
        tracker_id="phase-1",
        number=1,
        name="Phase 1",
        status=PhaseStatus.ACTIVE,
        confidence=0.0,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tracker() -> StatefulMockTracker:
    t = StatefulMockTracker()
    t.saga = _make_saga()
    t.phase = _make_phase()
    return t


@pytest.fixture
def volundr() -> MockVolundr:
    return MockVolundr()


@pytest.fixture
def git() -> MockGit:
    return MockGit()


@pytest.fixture
def client(
    tracker: StatefulMockTracker,
    volundr: MockVolundr,
    git: MockGit,
) -> TestClient:
    app = FastAPI()
    app.include_router(create_runs_router())
    app.dependency_overrides[resolve_tracker] = lambda: tracker
    app.dependency_overrides[resolve_volundr] = lambda: volundr
    app.dependency_overrides[resolve_git] = lambda: git

    # Provide settings with ReviewConfig and auth on app.state
    app.state.settings = SimpleNamespace(
        review=REVIEW_CFG,
        auth=AuthConfig(allow_anonymous_dev=True),
    )

    return TestClient(app)


# ---------------------------------------------------------------------------
# GET /runs/{id}/review
# ---------------------------------------------------------------------------


class TestGetReview:
    def test_returns_review_state(
        self, client: TestClient, tracker: StatefulMockTracker, volundr: MockVolundr
    ):
        run = _make_run()
        tracker.runs[run.id] = run

        resp = client.get(f"/api/v1/ting/runs/{run.id}/review")
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == str(run.id)
        assert data["name"] == "Test run"
        assert data["status"] == "REVIEW"
        assert data["chronicle_summary"] == "All tests pass, code looks clean"
        assert data["pr_url"] == "https://github.com/org/repo/pull/42"
        assert data["ci_passed"] is True
        assert data["confidence"] == 0.5
        assert data["confidence_events"] == []

    def test_includes_confidence_events(self, client: TestClient, tracker: StatefulMockTracker):
        run = _make_run()
        tracker.runs[run.id] = run
        event = ConfidenceEvent(
            id=uuid4(),
            run_id=run.id,
            event_type=ConfidenceEventType.CI_PASS,
            delta=0.1,
            score_after=0.6,
            created_at=datetime.now(UTC),
        )
        tracker.events[run.tracker_id] = [event]

        resp = client.get(f"/api/v1/ting/runs/{run.id}/review")
        data = resp.json()
        assert len(data["confidence_events"]) == 1
        assert data["confidence_events"][0]["event_type"] == "ci_pass"
        assert data["confidence_events"][0]["delta"] == 0.1

    def test_not_found(self, client: TestClient):
        resp = client.get(f"/api/v1/ting/runs/{uuid4()}/review")
        assert resp.status_code == 404

    def test_no_session_id(self, client: TestClient, tracker: StatefulMockTracker):
        run = _make_run(session_id=None)
        tracker.runs[run.id] = run

        resp = client.get(f"/api/v1/ting/runs/{run.id}/review")
        assert resp.status_code == 200
        data = resp.json()
        assert data["pr_url"] is None
        assert data["ci_passed"] is None

    def test_volundr_unreachable(
        self,
        client: TestClient,
        tracker: StatefulMockTracker,
        volundr: MockVolundr,
    ):
        run = _make_run()
        tracker.runs[run.id] = run
        volundr.fail_pr_status = True

        resp = client.get(f"/api/v1/ting/runs/{run.id}/review")
        assert resp.status_code == 200
        data = resp.json()
        assert data["pr_url"] is None
        assert data["ci_passed"] is None


# ---------------------------------------------------------------------------
# POST /runs/{id}/approve
# ---------------------------------------------------------------------------


class TestApproveRun:
    def test_approve_success(
        self,
        client: TestClient,
        tracker: StatefulMockTracker,
        git: MockGit,
    ):
        run = _make_run()
        tracker.runs[run.id] = run

        resp = client.post(f"/api/v1/ting/runs/{run.id}/approve")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "MERGED"
        assert data["id"] == str(run.id)

        # Verify branch was merged and deleted
        assert len(git.merged) == 1
        assert git.merged[0] == ("org/repo", "run/test-branch", "feat/alpha")
        assert len(git.deleted) == 1
        assert git.deleted[0] == ("org/repo", "run/test-branch")

        # Verify confidence event was added
        events = tracker.events[run.tracker_id]
        assert len(events) == 1
        assert events[0].event_type == ConfidenceEventType.HUMAN_APPROVED
        assert events[0].delta == REVIEW_CFG.confidence_delta_approved

    def test_approve_not_found(self, client: TestClient):
        resp = client.post(f"/api/v1/ting/runs/{uuid4()}/approve")
        assert resp.status_code == 404

    def test_approve_wrong_state(self, client: TestClient, tracker: StatefulMockTracker):
        run = _make_run(status=RunStatus.PENDING)
        tracker.runs[run.id] = run

        resp = client.post(f"/api/v1/ting/runs/{run.id}/approve")
        assert resp.status_code == 409

    def test_approve_no_saga(self, client: TestClient, tracker: StatefulMockTracker):
        run = _make_run()
        tracker.runs[run.id] = run
        tracker.saga = None

        resp = client.post(f"/api/v1/ting/runs/{run.id}/approve")
        assert resp.status_code == 404
        assert "saga" in resp.json()["detail"].lower()

    def test_approve_merge_failure(
        self, client: TestClient, tracker: StatefulMockTracker, git: MockGit
    ):
        run = _make_run()
        tracker.runs[run.id] = run
        git.fail_merge = True

        resp = client.post(f"/api/v1/ting/runs/{run.id}/approve")
        assert resp.status_code == 502
        assert "merge" in resp.json()["detail"].lower()

    def test_approve_no_branch(
        self, client: TestClient, tracker: StatefulMockTracker, git: MockGit
    ):
        run = _make_run(branch=None)
        tracker.runs[run.id] = run

        resp = client.post(f"/api/v1/ting/runs/{run.id}/approve")
        assert resp.status_code == 200
        # No merge/delete attempted
        assert len(git.merged) == 0
        assert len(git.deleted) == 0

    def test_approve_phase_gate_check(
        self,
        client: TestClient,
        tracker: StatefulMockTracker,
    ):
        run = _make_run()
        tracker.runs[run.id] = run
        tracker._all_merged = True

        resp = client.post(f"/api/v1/ting/runs/{run.id}/approve")
        assert resp.status_code == 200

    def test_approve_ci_failing_still_succeeds(
        self,
        client: TestClient,
        tracker: StatefulMockTracker,
        volundr: MockVolundr,
    ):
        run = _make_run()
        tracker.runs[run.id] = run
        volundr.pr_status = PRStatus(
            pr_id="pr-1",
            url="https://github.com/org/repo/pull/1",
            state="open",
            mergeable=True,
            ci_passed=False,
        )

        resp = client.post(f"/api/v1/ting/runs/{run.id}/approve")
        assert resp.status_code == 200
        assert resp.json()["status"] == "MERGED"


# ---------------------------------------------------------------------------
# POST /runs/{id}/reject
# ---------------------------------------------------------------------------


class TestRejectRun:
    def test_reject_success(self, client: TestClient, tracker: StatefulMockTracker):
        run = _make_run()
        tracker.runs[run.id] = run

        resp = client.post(f"/api/v1/ting/runs/{run.id}/reject")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "FAILED"
        assert data["reason"] is None

        events = tracker.events[run.tracker_id]
        assert len(events) == 1
        assert events[0].event_type == ConfidenceEventType.HUMAN_REJECT
        assert events[0].delta == REVIEW_CFG.confidence_delta_rejected

    def test_reject_with_reason(self, client: TestClient, tracker: StatefulMockTracker):
        run = _make_run()
        tracker.runs[run.id] = run

        resp = client.post(
            f"/api/v1/ting/runs/{run.id}/reject",
            json={"reason": "Code quality too low"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "FAILED"
        assert data["reason"] == "Code quality too low"

    def test_reject_not_found(self, client: TestClient):
        resp = client.post(f"/api/v1/ting/runs/{uuid4()}/reject")
        assert resp.status_code == 404

    def test_reject_wrong_state(self, client: TestClient, tracker: StatefulMockTracker):
        run = _make_run(status=RunStatus.MERGED)
        tracker.runs[run.id] = run

        resp = client.post(f"/api/v1/ting/runs/{run.id}/reject")
        assert resp.status_code == 409

    def test_reject_confidence_clamped_at_zero(
        self, client: TestClient, tracker: StatefulMockTracker
    ):
        run = _make_run(confidence=0.05)
        tracker.runs[run.id] = run

        resp = client.post(f"/api/v1/ting/runs/{run.id}/reject")
        assert resp.status_code == 200
        events = tracker.events[run.tracker_id]
        assert events[0].score_after == 0.0


# ---------------------------------------------------------------------------
# POST /runs/{id}/retry
# ---------------------------------------------------------------------------


class TestRetryRun:
    def test_retry_success(self, client: TestClient, tracker: StatefulMockTracker):
        run = _make_run()
        tracker.runs[run.id] = run

        resp = client.post(f"/api/v1/ting/runs/{run.id}/retry")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "PENDING"
        assert data["retry_count"] == 1

        events = tracker.events[run.tracker_id]
        assert len(events) == 1
        assert events[0].event_type == ConfidenceEventType.RETRY
        assert events[0].delta == REVIEW_CFG.confidence_delta_retry

    def test_retry_not_found(self, client: TestClient):
        resp = client.post(f"/api/v1/ting/runs/{uuid4()}/retry")
        assert resp.status_code == 404

    def test_retry_wrong_state(self, client: TestClient, tracker: StatefulMockTracker):
        run = _make_run(status=RunStatus.MERGED)
        tracker.runs[run.id] = run

        resp = client.post(f"/api/v1/ting/runs/{run.id}/retry")
        assert resp.status_code == 409

    def test_retry_from_review_state(self, client: TestClient, tracker: StatefulMockTracker):
        """Retry from REVIEW state resets to PENDING."""
        run = _make_run(status=RunStatus.REVIEW)
        tracker.runs[run.id] = run

        resp = client.post(f"/api/v1/ting/runs/{run.id}/retry")
        assert resp.status_code == 200
        assert resp.json()["status"] == "PENDING"

    def test_retry_increments_count(self, client: TestClient, tracker: StatefulMockTracker):
        run = _make_run()
        tracker.runs[run.id] = run

        client.post(f"/api/v1/ting/runs/{run.id}/retry")
        # Reset to REVIEW with retry_count=1 to test second retry
        now = datetime.now(UTC)
        tracker.runs[run.id] = Run(
            id=run.id,
            phase_id=run.phase_id,
            tracker_id=run.tracker_id,
            name=run.name,
            description=run.description,
            acceptance_criteria=run.acceptance_criteria,
            declared_files=run.declared_files,
            estimate_hours=run.estimate_hours,
            status=RunStatus.REVIEW,
            confidence=run.confidence,
            session_id=run.session_id,
            branch=run.branch,
            chronicle_summary=run.chronicle_summary,
            pr_url=run.pr_url,
            pr_id=run.pr_id,
            retry_count=1,
            created_at=run.created_at,
            updated_at=now,
        )

        resp = client.post(f"/api/v1/ting/runs/{run.id}/retry")
        assert resp.status_code == 200
        assert resp.json()["retry_count"] == 2


# ---------------------------------------------------------------------------
# Mock SagaRepository for summary tests
# ---------------------------------------------------------------------------


class MockSagaRepository(SagaRepository):
    """In-memory mock for SagaRepository used in summary tests."""

    def __init__(self, counts: dict[str, int] | None = None) -> None:
        self._counts = counts or {s.value: 0 for s in RunStatus}

    async def count_by_status(self) -> dict[str, int]:
        return dict(self._counts)

    async def save_saga(self, saga, *, conn=None) -> None:
        pass

    async def save_phase(self, phase, *, conn=None) -> None:
        pass

    async def save_run(self, run, *, conn=None) -> None:
        pass

    async def list_sagas(self, *, owner_id=None):
        return []

    async def get_saga(self, saga_id, *, owner_id=None):
        return None

    async def get_saga_by_slug(self, slug):
        return None

    async def delete_saga(self, saga_id, *, owner_id=None) -> bool:
        return False

    async def update_saga_status(self, saga_id, status) -> None:
        pass


# ---------------------------------------------------------------------------
# GET /runs/summary
# ---------------------------------------------------------------------------


class TestRunsSummary:
    def _make_client(self, counts: dict[str, int] | None = None) -> TestClient:
        repo = MockSagaRepository(counts)
        app = FastAPI()
        app.include_router(create_runs_router())
        app.dependency_overrides[resolve_run_repo] = lambda: repo
        app.state.settings = SimpleNamespace(
            review=REVIEW_CFG,
            auth=AuthConfig(allow_anonymous_dev=True),
        )
        return TestClient(app)

    def test_returns_all_statuses(self):
        client = self._make_client()
        resp = client.get("/api/v1/ting/runs/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {s.value for s in RunStatus}

    def test_returns_correct_counts(self):
        counts = {
            "PENDING": 3,
            "QUEUED": 1,
            "RUNNING": 2,
            "REVIEW": 0,
            "MERGED": 5,
            "FAILED": 1,
        }
        client = self._make_client(counts)
        resp = client.get("/api/v1/ting/runs/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["PENDING"] == 3
        assert data["QUEUED"] == 1
        assert data["RUNNING"] == 2
        assert data["REVIEW"] == 0
        assert data["MERGED"] == 5
        assert data["FAILED"] == 1

    def test_zero_counts_when_no_runs(self):
        client = self._make_client()
        resp = client.get("/api/v1/ting/runs/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert all(v == 0 for v in data.values())

    def test_unconfigured_repo_returns_503(self):
        app = FastAPI()
        app.include_router(create_runs_router())
        app.state.settings = SimpleNamespace(
            review=REVIEW_CFG,
            auth=AuthConfig(allow_anonymous_dev=True),
        )
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/v1/ting/runs/summary")
        assert resp.status_code == 503
