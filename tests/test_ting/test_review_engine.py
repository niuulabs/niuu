"""Tests for the automated review engine (NIU-239)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from tests.test_ting.stubs import InMemorySagaRepository
from ting.adapters.memory_event_bus import InMemoryEventBus
from ting.config import ReviewConfig
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
    TrackerIssue,
    TrackerMilestone,
    TrackerProject,
)
from ting.domain.services.review_engine import (
    ReviewEngine,
    detect_scope_breach,
)
from ting.ports.git import GitPort
from ting.ports.tracker import TrackerPort
from ting.ports.volundr import ActivityEvent, SpawnRequest, VolundrPort, VolundrSession

NOW = datetime.now(UTC)

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class StubGit(GitPort):
    """In-memory Git stub for review engine tests."""

    def __init__(self) -> None:
        self.pr_statuses: dict[str, PRStatus] = {}
        self.changed_files: dict[str, list[str]] = {}
        self.merged: list[tuple[str, str, str]] = []
        self.deleted: list[tuple[str, str]] = []
        self.fail_pr_status: bool = False
        self.fail_changed_files: bool = False
        self.fail_merge: bool = False

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
        if self.fail_pr_status:
            raise RuntimeError("PR status unavailable")
        pr = self.pr_statuses.get(pr_id)
        if pr is None:
            raise RuntimeError(f"No PR: {pr_id}")
        return pr

    async def get_pr_changed_files(self, pr_id: str) -> list[str]:
        if self.fail_changed_files:
            raise RuntimeError("Changed files unavailable")
        return self.changed_files.get(pr_id, [])


class StubTracker(TrackerPort):
    """In-memory tracker stub for review engine tests."""

    def __init__(self) -> None:
        # runs keyed by tracker_id (str)
        self.runs: dict[str, Run] = {}
        # confidence events keyed by tracker_id (str)
        self.events: dict[str, list[ConfidenceEvent]] = {}
        self.saga: Saga | None = None
        self.phase: Phase | None = None
        self.phases: list[Phase] = []
        self._all_merged: bool = False
        self.phase_status_updates: list[tuple[str, PhaseStatus]] = []
        self.attached_documents: list[tuple[str, str, str]] = []

    # -- CRUD: create entities --

    async def create_saga(self, saga: Saga, *, description: str = "") -> str:
        return saga.tracker_id

    async def create_phase(self, phase: Phase, *, project_id: str = "") -> str:
        return phase.tracker_id

    async def create_run(self, run: Run, *, project_id: str = "", milestone_id: str = "") -> str:
        self.runs[run.tracker_id] = run
        return run.tracker_id

    # -- CRUD: update / close --

    async def update_run_state(self, run_id: str, state: RunStatus) -> None:
        pass

    async def close_run(self, run_id: str) -> None:
        pass

    async def attach_issue_document(self, issue_id: str, title: str, content: str) -> str:
        self.attached_documents.append((issue_id, title, content))
        return "doc-stub"

    # -- Read: fetch domain entities by tracker ID --

    async def get_saga(self, saga_id: str) -> Saga:
        if self.saga is None:
            raise ValueError(f"Saga not found: {saga_id}")
        return self.saga

    async def get_phase(self, tracker_id: str) -> Phase:
        if self.phase is None:
            raise ValueError(f"Phase not found: {tracker_id}")
        return self.phase

    async def get_run(self, tracker_id: str) -> Run:
        run = self.runs.get(tracker_id)
        if run is None:
            raise ValueError(f"Run not found: {tracker_id}")
        return run

    async def list_pending_runs(self, phase_id: str) -> list[Run]:
        return []

    # -- Browsing --

    async def list_projects(self) -> list[TrackerProject]:
        return []

    async def get_project(self, project_id: str) -> TrackerProject:
        raise NotImplementedError

    async def list_milestones(self, project_id: str) -> list[TrackerMilestone]:
        return []

    async def list_issues(
        self,
        project_id: str,
        milestone_id: str | None = None,
    ) -> list[TrackerIssue]:
        return []

    # -- Run progress --

    async def update_run_progress(
        self,
        tracker_id: str,
        *,
        status: RunStatus | None = None,
        session_id: str | None = None,
        confidence: float | None = None,
        pr_url: str | None = None,
        pr_id: str | None = None,
        retry_count: int | None = None,
        reason: str | None = None,
        owner_id: str | None = None,
        phase_tracker_id: str | None = None,
        saga_tracker_id: str | None = None,
        chronicle_summary: str | None = None,
        reviewer_session_id: str | None = None,
    ) -> Run:
        run = self.runs.get(tracker_id)
        if run is None:
            raise ValueError(f"Run not found: {tracker_id}")
        events = self.events.get(tracker_id, [])
        new_confidence = events[-1].score_after if events else run.confidence
        updated = Run(
            id=run.id,
            phase_id=run.phase_id,
            tracker_id=run.tracker_id,
            name=run.name,
            description=run.description,
            acceptance_criteria=run.acceptance_criteria,
            declared_files=run.declared_files,
            estimate_hours=run.estimate_hours,
            status=status if status is not None else run.status,
            confidence=confidence if confidence is not None else new_confidence,
            session_id=session_id if session_id is not None else run.session_id,
            branch=run.branch,
            chronicle_summary=run.chronicle_summary,
            pr_url=pr_url if pr_url is not None else run.pr_url,
            pr_id=pr_id if pr_id is not None else run.pr_id,
            retry_count=retry_count if retry_count is not None else run.retry_count,
            created_at=run.created_at,
            updated_at=datetime.now(UTC),
            reviewer_session_id=(
                reviewer_session_id if reviewer_session_id is not None else run.reviewer_session_id
            ),
        )
        self.runs[tracker_id] = updated
        return updated

    async def get_run_progress_for_saga(self, saga_tracker_id: str) -> list[Run]:
        return list(self.runs.values())

    async def get_run_by_session(self, session_id: str) -> Run | None:
        return next((r for r in self.runs.values() if r.session_id == session_id), None)

    async def list_runs_by_status(self, status: RunStatus) -> list[Run]:
        return [r for r in self.runs.values() if r.status == status]

    async def get_run_by_id(self, run_id: UUID) -> Run | None:
        return next((r for r in self.runs.values() if r.id == run_id), None)

    # -- Confidence events --

    async def add_confidence_event(self, tracker_id: str, event: ConfidenceEvent) -> None:
        self.events.setdefault(tracker_id, []).append(event)

    async def get_confidence_events(self, tracker_id: str) -> list[ConfidenceEvent]:
        return self.events.get(tracker_id, [])

    # -- Phase gate management --

    async def all_runs_merged(self, phase_tracker_id: str) -> bool:
        return self._all_merged

    async def list_phases_for_saga(self, saga_tracker_id: str) -> list[Phase]:
        return self.phases

    async def update_phase_status(self, phase_tracker_id: str, status: PhaseStatus) -> Phase | None:
        self.phase_status_updates.append((phase_tracker_id, status))
        for i, p in enumerate(self.phases):
            if p.tracker_id == phase_tracker_id:
                updated = Phase(
                    id=p.id,
                    saga_id=p.saga_id,
                    tracker_id=p.tracker_id,
                    number=p.number,
                    name=p.name,
                    status=status,
                    confidence=p.confidence,
                )
                self.phases[i] = updated
                return updated
        return None

    # -- Cross-entity navigation --

    async def get_saga_for_run(self, tracker_id: str) -> Saga | None:
        return self.saga

    async def get_phase_for_run(self, tracker_id: str) -> Phase | None:
        return self.phase

    async def get_owner_for_run(self, tracker_id: str) -> str | None:
        if self.saga:
            return self.saga.owner_id
        return None

    # -- Session messages --

    async def save_session_message(self, message: SessionMessage) -> None:
        pass

    async def get_session_messages(self, tracker_id: str) -> list[SessionMessage]:
        return []


class StubTrackerFactory:
    """In-memory tracker factory stub for review engine tests."""

    def __init__(self, tracker: StubTracker) -> None:
        self._tracker = tracker

    async def for_owner(self, owner_id: str) -> list[StubTracker]:
        return [self._tracker]


class StubVolundr(VolundrPort):
    """In-memory Volundr stub for review engine tests."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []
        self.stopped_sessions: list[str] = []
        self.fail_send: bool = False

    async def spawn_session(
        self, request: SpawnRequest, *, auth_token: str | None = None
    ) -> VolundrSession:
        raise NotImplementedError

    async def get_session(
        self, session_id: str, *, auth_token: str | None = None
    ) -> VolundrSession | None:
        return VolundrSession(id=session_id, name="s", status="running", tracker_issue_id=None)

    async def list_sessions(self, *, auth_token: str | None = None) -> list[VolundrSession]:
        return []

    async def get_pr_status(self, session_id: str) -> PRStatus:
        raise NotImplementedError

    async def get_chronicle_summary(self, session_id: str) -> str:
        return ""

    async def send_message(
        self, session_id: str, message: str, *, auth_token: str | None = None
    ) -> None:
        if self.fail_send:
            raise RuntimeError("Send failed")
        self.messages.append((session_id, message))

    async def stop_session(self, session_id, *, auth_token=None):
        self.stopped_sessions.append(session_id)

    async def list_integration_ids(self, *, auth_token=None) -> list[str]:
        return []

    async def list_repos(self, *, auth_token: str | None = None) -> list[dict]:
        return []

    async def get_conversation(self, session_id: str) -> dict:
        stub = '{"confidence": 0.9, "approved": true, "issues": []}'
        return {"turns": [{"role": "assistant", "content": stub}]}

    async def get_last_assistant_message(self, session_id: str) -> str:
        return '{"confidence": 0.9, "approved": true, "summary": "stub", "issues": []}'

    async def subscribe_activity(self) -> AsyncGenerator[ActivityEvent, None]:
        return
        yield  # type: ignore[misc]  # pragma: no cover


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PHASE_ID = uuid4()
SAGA_ID = uuid4()

OWNER_ID = "user-1"
TRACKER_ID = "NIU-100"


def _make_run(
    run_id: UUID | None = None,
    tracker_id: str = TRACKER_ID,
    status: RunStatus = RunStatus.REVIEW,
    confidence: float = 0.5,
    pr_id: str | None = "https://api.github.com/repos/org/repo/pulls/42",
    branch: str | None = "run/test-branch",
    declared_files: list[str] | None = None,
    retry_count: int = 0,
    reviewer_session_id: str | None = None,
) -> Run:
    return Run(
        id=run_id or uuid4(),
        phase_id=PHASE_ID,
        tracker_id=tracker_id,
        name="Test run",
        description="A test run",
        acceptance_criteria=["tests pass"],
        declared_files=declared_files or ["src/main.py", "tests/test_main.py"],
        estimate_hours=2.0,
        status=status,
        confidence=confidence,
        session_id="session-1",
        branch=branch,
        chronicle_summary="All tests pass",
        pr_url="https://github.com/org/repo/pull/42",
        pr_id=pr_id,
        retry_count=retry_count,
        created_at=NOW,
        updated_at=NOW,
        reviewer_session_id=reviewer_session_id,
    )


def _make_saga() -> Saga:
    return Saga(
        id=SAGA_ID,
        tracker_id="proj-1",
        tracker_type="linear",
        slug="alpha",
        name="Alpha",
        repos=["org/repo"],
        feature_branch="feat/alpha",
        status=SagaStatus.ACTIVE,
        confidence=0.5,
        created_at=NOW,
        base_branch="dev",
        owner_id=OWNER_ID,
    )


def _make_phase(
    phase_id: UUID | None = None,
    number: int = 1,
    status: PhaseStatus = PhaseStatus.ACTIVE,
) -> Phase:
    return Phase(
        id=phase_id or PHASE_ID,
        saga_id=SAGA_ID,
        tracker_id=f"phase-{number}",
        number=number,
        name=f"Phase {number}",
        status=status,
        confidence=0.5,
    )


def _default_config(**overrides: object) -> ReviewConfig:
    defaults: dict = {
        "auto_approve_threshold": 0.80,
        "max_retries": 3,
        "scope_breach_threshold": 0.30,
    }
    defaults.update(overrides)
    return ReviewConfig(**defaults)


def _make_engine(
    tracker: StubTracker | None = None,
    git: StubGit | None = None,
    config: ReviewConfig | None = None,
    event_bus: InMemoryEventBus | None = None,
    volundr: StubVolundr | None = None,
    saga_repo: InMemorySagaRepository | None = None,
) -> tuple[ReviewEngine, StubTracker, StubGit, InMemoryEventBus, StubVolundr]:
    r = tracker or StubTracker()
    g = git or StubGit()
    e = event_bus or InMemoryEventBus()
    c = config or _default_config()
    v = volundr or StubVolundr()
    repo = saga_repo or InMemorySagaRepository()

    class _StubVolundrFactory:
        async def for_owner(self, owner_id: str) -> list[StubVolundr]:
            return [v]

        async def primary_for_owner(self, owner_id: str) -> StubVolundr | None:
            return v

    engine = ReviewEngine(
        tracker_factory=StubTrackerFactory(r),
        volundr_factory=_StubVolundrFactory(),
        git=g,
        review_config=c,
        event_bus=e,
        saga_repo=repo,
    )
    return engine, r, g, e, v


def _setup_passing_pr(git: StubGit, pr_id: str) -> None:
    """Set up a PR that has passed CI and is mergeable."""
    git.pr_statuses[pr_id] = PRStatus(
        pr_id=pr_id,
        url="https://github.com/org/repo/pull/42",
        state="open",
        mergeable=True,
        ci_passed=True,
    )


def _setup_failing_pr(git: StubGit, pr_id: str) -> None:
    """Set up a PR with failing CI."""
    git.pr_statuses[pr_id] = PRStatus(
        pr_id=pr_id,
        url="https://github.com/org/repo/pull/42",
        state="open",
        mergeable=True,
        ci_passed=False,
    )


def _setup_conflicted_pr(git: StubGit, pr_id: str) -> None:
    """Set up a PR with merge conflicts."""
    git.pr_statuses[pr_id] = PRStatus(
        pr_id=pr_id,
        url="https://github.com/org/repo/pull/42",
        state="open",
        mergeable=False,
        ci_passed=True,
    )


# ---------------------------------------------------------------------------
# Tests: detect_scope_breach
# ---------------------------------------------------------------------------


class TestScopeBreachDetection:
    def test_no_changed_files(self) -> None:
        assert detect_scope_breach(["src/main.py"], [], 0.30) is False

    def test_all_files_declared(self) -> None:
        declared = ["src/main.py", "tests/test_main.py"]
        changed = ["src/main.py", "tests/test_main.py"]
        assert detect_scope_breach(declared, changed, 0.30) is False


@pytest.mark.asyncio
async def test_sync_phase_projection_marks_imported_saga_complete_without_persisted_phases() -> (
    None
):
    repo = InMemorySagaRepository()
    saga = Saga(
        id=uuid4(),
        tracker_id="proj-1",
        tracker_type="linear",
        slug="imported-proof",
        name="Imported Proof",
        repos=["niuulabs/volundr"],
        feature_branch="feat/imported-proof",
        base_branch="dev",
        status=SagaStatus.ACTIVE,
        confidence=0.0,
        created_at=NOW,
        owner_id="dev-user",
    )
    await repo.save_saga(saga)
    engine, *_ = _make_engine(saga_repo=repo)

    await engine._sync_saga_phase_projection(
        saga=saga,
        current_phase=SimpleNamespace(tracker_id="__unassigned__"),
        next_phase=None,
    )

    updated = await repo.get_saga(saga.id, owner_id="dev-user")
    assert updated is not None
    assert updated.status == SagaStatus.COMPLETE

    def test_below_threshold(self) -> None:
        declared = ["src/a.py", "src/b.py", "src/c.py"]
        changed = ["src/a.py", "src/b.py", "src/c.py", "src/d.py"]
        # 1/4 = 0.25 which is < 0.30
        assert detect_scope_breach(declared, changed, 0.30) is False

    def test_above_threshold(self) -> None:
        declared = ["src/a.py"]
        changed = ["src/a.py", "src/b.py", "src/c.py", "src/d.py"]
        # 3/4 = 0.75 which is > 0.30
        assert detect_scope_breach(declared, changed, 0.30) is True

    def test_exactly_at_threshold(self) -> None:
        declared = ["src/a.py", "src/b.py"]
        changed = ["src/a.py", "src/b.py", "src/c.py"]
        # 1/3 = 0.333... which is > 0.30
        assert detect_scope_breach(declared, changed, 0.30) is True

    def test_empty_declared_files(self) -> None:
        changed = ["src/a.py"]
        # 1/1 = 1.0 which is > 0.30
        assert detect_scope_breach([], changed, 0.30) is True


# ---------------------------------------------------------------------------
# Tests: Auto-approve
# ---------------------------------------------------------------------------


class TestAutoApprove:
    @pytest.mark.asyncio
    async def test_auto_approve_high_confidence(self) -> None:
        """Run with high confidence, passing CI, and mergeable PR is auto-approved."""
        engine, repo, git, bus, _ = _make_engine()
        run = _make_run(confidence=0.5)
        repo.runs[run.tracker_id] = run
        repo.saga = _make_saga()
        repo.phase = _make_phase()

        pr_id = run.pr_id
        _setup_passing_pr(git, pr_id)
        git.changed_files[pr_id] = ["src/main.py", "tests/test_main.py"]

        result = await engine.evaluate(run.tracker_id, OWNER_ID)

        assert result.action == "auto_approved"
        assert repo.runs[run.tracker_id].status == RunStatus.MERGED

    @pytest.mark.asyncio
    async def test_auto_approve_does_not_merge_branch(self) -> None:
        """Auto-approve should NOT merge the branch — the reviewer session does it."""
        engine, repo, git, _, _ = _make_engine()
        run = _make_run(confidence=0.5)
        repo.runs[run.tracker_id] = run
        repo.saga = _make_saga()
        repo.phase = _make_phase()

        _setup_passing_pr(git, run.pr_id)
        git.changed_files[run.pr_id] = ["src/main.py"]

        await engine.evaluate(run.tracker_id, OWNER_ID)

        assert len(git.merged) == 0
        assert len(git.deleted) == 0

    @pytest.mark.asyncio
    async def test_auto_approve_emits_events(self) -> None:
        """Auto-approve should emit run.state_changed and confidence.updated events."""
        engine, repo, git, bus, _ = _make_engine()
        run = _make_run(confidence=0.5)
        repo.runs[run.tracker_id] = run
        repo.saga = _make_saga()
        repo.phase = _make_phase()

        _setup_passing_pr(git, run.pr_id)
        git.changed_files[run.pr_id] = ["src/main.py"]

        q = bus.subscribe()
        await engine.evaluate(run.tracker_id, OWNER_ID)

        events = []
        while not q.empty():
            events.append(await q.get())

        event_types = [e.event for e in events]
        assert "confidence.updated" in event_types
        assert "run.state_changed" in event_types

        state_event = next(e for e in events if e.event == "run.state_changed")
        assert state_event.data["action"] == "auto_approved"
        assert state_event.data["saga_tracker_id"] == "proj-1"

    @pytest.mark.asyncio
    async def test_auto_approve_event_no_saga(self) -> None:
        """run.state_changed omits saga_tracker_id when saga is not found."""
        engine, repo, git, bus, _ = _make_engine()
        run = _make_run(confidence=0.5)
        repo.runs[run.tracker_id] = run
        repo.saga = None
        repo.phase = _make_phase()
        repo._all_merged = False

        _setup_passing_pr(git, run.pr_id)
        git.changed_files[run.pr_id] = ["src/main.py"]

        q = bus.subscribe()
        await engine.evaluate(run.tracker_id, OWNER_ID)

        events = []
        while not q.empty():
            events.append(await q.get())

        state_event = next(e for e in events if e.event == "run.state_changed")
        assert "saga_tracker_id" not in state_event.data

    @pytest.mark.asyncio
    async def test_auto_approve_records_confidence_events(self) -> None:
        """Auto-approve should record CI_PASS and AUTO_APPROVED confidence events."""
        engine, repo, git, _, _ = _make_engine()
        run = _make_run(confidence=0.5)
        repo.runs[run.tracker_id] = run
        repo.saga = _make_saga()
        repo.phase = _make_phase()

        _setup_passing_pr(git, run.pr_id)
        git.changed_files[run.pr_id] = ["src/main.py"]

        await engine.evaluate(run.tracker_id, OWNER_ID)

        events = repo.events[run.tracker_id]
        event_types = [e.event_type for e in events]
        assert ConfidenceEventType.CI_PASS in event_types
        assert ConfidenceEventType.AUTO_APPROVED in event_types

    @pytest.mark.asyncio
    async def test_merge_failure_does_not_block_approval(self) -> None:
        """If branch merge fails, the run should still transition to MERGED."""
        engine, repo, git, _, _ = _make_engine()
        run = _make_run(confidence=0.5)
        repo.runs[run.tracker_id] = run
        repo.saga = _make_saga()
        repo.phase = _make_phase()

        _setup_passing_pr(git, run.pr_id)
        git.changed_files[run.pr_id] = ["src/main.py"]
        git.fail_merge = True

        result = await engine.evaluate(run.tracker_id, OWNER_ID)

        assert result.action == "auto_approved"
        assert repo.runs[run.tracker_id].status == RunStatus.MERGED


# ---------------------------------------------------------------------------
# Tests: CI failure
# ---------------------------------------------------------------------------


class TestCIFailure:
    @pytest.mark.asyncio
    async def test_ci_failure_auto_retry(self) -> None:
        """CI failure with retries remaining should auto-retry (REVIEW → PENDING)."""
        engine, repo, git, _, _ = _make_engine()
        run = _make_run(retry_count=0)
        repo.runs[run.tracker_id] = run

        _setup_failing_pr(git, run.pr_id)

        result = await engine.evaluate(run.tracker_id, OWNER_ID)

        assert result.action == "retried"
        assert repo.runs[run.tracker_id].status == RunStatus.PENDING
        assert repo.runs[run.tracker_id].retry_count == 1

    @pytest.mark.asyncio
    async def test_ci_failure_retries_exhausted(self) -> None:
        """CI failure with no retries left should transition to FAILED."""
        config = _default_config(max_retries=3)
        engine, repo, git, _, _ = _make_engine(config=config)
        run = _make_run(retry_count=3)
        repo.runs[run.tracker_id] = run

        _setup_failing_pr(git, run.pr_id)

        result = await engine.evaluate(run.tracker_id, OWNER_ID)

        assert result.action == "failed"
        assert repo.runs[run.tracker_id].status == RunStatus.FAILED

    @pytest.mark.asyncio
    async def test_ci_failure_emits_ci_fail_event(self) -> None:
        """CI failure should record a CI_FAIL confidence event."""
        engine, repo, git, _, _ = _make_engine()
        run = _make_run()
        repo.runs[run.tracker_id] = run

        _setup_failing_pr(git, run.pr_id)

        await engine.evaluate(run.tracker_id, OWNER_ID)

        events = repo.events[run.tracker_id]
        event_types = [e.event_type for e in events]
        assert ConfidenceEventType.CI_FAIL in event_types


# ---------------------------------------------------------------------------
# Tests: PR conflicts
# ---------------------------------------------------------------------------


class TestPRConflicts:
    @pytest.mark.asyncio
    async def test_conflict_auto_retry(self) -> None:
        """PR with conflicts and retries remaining should auto-retry."""
        engine, repo, git, _, _ = _make_engine()
        run = _make_run(retry_count=0)
        repo.runs[run.tracker_id] = run

        _setup_conflicted_pr(git, run.pr_id)

        result = await engine.evaluate(run.tracker_id, OWNER_ID)

        assert result.action == "retried"
        assert repo.runs[run.tracker_id].status == RunStatus.PENDING

    @pytest.mark.asyncio
    async def test_conflict_retries_exhausted(self) -> None:
        """PR with conflicts and no retries left should fail."""
        config = _default_config(max_retries=2)
        engine, repo, git, _, _ = _make_engine(config=config)
        run = _make_run(retry_count=2)
        repo.runs[run.tracker_id] = run

        _setup_conflicted_pr(git, run.pr_id)

        result = await engine.evaluate(run.tracker_id, OWNER_ID)

        assert result.action == "failed"
        assert repo.runs[run.tracker_id].status == RunStatus.FAILED

    @pytest.mark.asyncio
    async def test_conflict_records_pr_conflict_event(self) -> None:
        """PR conflict should record a PR_CONFLICT confidence event."""
        engine, repo, git, _, _ = _make_engine()
        run = _make_run()
        repo.runs[run.tracker_id] = run

        _setup_conflicted_pr(git, run.pr_id)

        await engine.evaluate(run.tracker_id, OWNER_ID)

        events = repo.events[run.tracker_id]
        event_types = [e.event_type for e in events]
        assert ConfidenceEventType.PR_CONFLICT in event_types


# ---------------------------------------------------------------------------
# Tests: Scope breach
# ---------------------------------------------------------------------------


class TestScopeBreach:
    @pytest.mark.asyncio
    async def test_scope_breach_lowers_confidence(self) -> None:
        """Scope breach should apply a negative confidence delta."""
        engine, repo, git, _, _ = _make_engine()
        run = _make_run(
            declared_files=["src/main.py"],
        )
        repo.runs[run.tracker_id] = run
        repo.saga = _make_saga()
        repo.phase = _make_phase()

        _setup_passing_pr(git, run.pr_id)
        # 3 out of 4 files are undeclared → 75% breach
        git.changed_files[run.pr_id] = [
            "src/main.py",
            "src/extra1.py",
            "src/extra2.py",
            "src/extra3.py",
        ]

        await engine.evaluate(run.tracker_id, OWNER_ID)

        events = repo.events[run.tracker_id]
        event_types = [e.event_type for e in events]
        assert ConfidenceEventType.SCOPE_BREACH in event_types

    @pytest.mark.asyncio
    async def test_no_scope_breach_within_threshold(self) -> None:
        """No scope breach when undeclared files are within threshold."""
        engine, repo, git, _, _ = _make_engine()
        run = _make_run(
            declared_files=["src/main.py", "src/b.py", "src/c.py"],
        )
        repo.runs[run.tracker_id] = run
        repo.saga = _make_saga()
        repo.phase = _make_phase()

        _setup_passing_pr(git, run.pr_id)
        # 1 out of 4 files undeclared → 25% < 30%
        git.changed_files[run.pr_id] = ["src/main.py", "src/b.py", "src/c.py", "src/d.py"]

        await engine.evaluate(run.tracker_id, OWNER_ID)

        events = repo.events[run.tracker_id]
        event_types = [e.event_type for e in events]
        assert ConfidenceEventType.SCOPE_BREACH not in event_types


# ---------------------------------------------------------------------------
# Tests: Escalation
# ---------------------------------------------------------------------------


class TestEscalation:
    @pytest.mark.asyncio
    async def test_low_confidence_escalates(self) -> None:
        """Confidence below threshold should escalate to human review."""
        config = _default_config(auto_approve_threshold=0.95)
        engine, repo, git, _, _ = _make_engine(config=config)
        run = _make_run(confidence=0.5)
        repo.runs[run.tracker_id] = run

        _setup_passing_pr(git, run.pr_id)
        git.changed_files[run.pr_id] = ["src/main.py"]

        result = await engine.evaluate(run.tracker_id, OWNER_ID)

        assert result.action == "escalated"
        # Run transitions to ESCALATED (no longer REVIEW, preventing re-trigger)
        assert repo.runs[run.tracker_id].status == RunStatus.ESCALATED

    @pytest.mark.asyncio
    async def test_no_pr_escalates(self) -> None:
        """Run without a PR ID should escalate to human review."""
        engine, repo, git, _, _ = _make_engine()
        run = _make_run(pr_id=None, confidence=0.9)
        repo.runs[run.tracker_id] = run

        result = await engine.evaluate(run.tracker_id, OWNER_ID)

        assert result.action == "escalated"

    @pytest.mark.asyncio
    async def test_pr_status_fetch_failure_escalates(self) -> None:
        """If PR status cannot be fetched, escalate to human review."""
        engine, repo, git, _, _ = _make_engine()
        run = _make_run(confidence=0.9)
        repo.runs[run.tracker_id] = run
        git.fail_pr_status = True

        result = await engine.evaluate(run.tracker_id, OWNER_ID)

        assert result.action == "escalated"


# ---------------------------------------------------------------------------
# Tests: Retry penalty
# ---------------------------------------------------------------------------


class TestRetryPenalty:
    @pytest.mark.asyncio
    async def test_retry_count_applies_penalty(self) -> None:
        """Previous retries should apply a cumulative confidence penalty."""
        engine, repo, git, _, _ = _make_engine()
        run = _make_run(confidence=0.5, retry_count=2)
        repo.runs[run.tracker_id] = run
        repo.saga = _make_saga()
        repo.phase = _make_phase()

        _setup_passing_pr(git, run.pr_id)
        git.changed_files[run.pr_id] = ["src/main.py"]

        await engine.evaluate(run.tracker_id, OWNER_ID)

        events = repo.events[run.tracker_id]
        retry_events = [e for e in events if e.event_type == ConfidenceEventType.RETRY]
        assert len(retry_events) == 1
        # -0.05 * 2 = -0.10
        assert retry_events[0].delta == pytest.approx(-0.10)

    @pytest.mark.asyncio
    async def test_zero_retries_no_penalty(self) -> None:
        """First attempt should not apply a retry penalty."""
        engine, repo, git, _, _ = _make_engine()
        run = _make_run(confidence=0.5, retry_count=0)
        repo.runs[run.tracker_id] = run
        repo.saga = _make_saga()
        repo.phase = _make_phase()

        _setup_passing_pr(git, run.pr_id)
        git.changed_files[run.pr_id] = ["src/main.py"]

        await engine.evaluate(run.tracker_id, OWNER_ID)

        events = repo.events[run.tracker_id]
        retry_events = [e for e in events if e.event_type == ConfidenceEventType.RETRY]
        assert len(retry_events) == 0


# ---------------------------------------------------------------------------
# Tests: Phase gate
# ---------------------------------------------------------------------------


class TestPhaseGate:
    @pytest.mark.asyncio
    async def test_phase_gate_unlocked(self) -> None:
        """When all runs in a phase are merged, the next phase is unlocked."""
        engine, repo, git, bus, _ = _make_engine()
        saga_repo = engine._saga_repo
        assert isinstance(saga_repo, InMemorySagaRepository)
        run = _make_run(confidence=0.5)
        repo.runs[run.tracker_id] = run
        repo.saga = _make_saga()
        repo._all_merged = True

        phase1 = _make_phase(phase_id=PHASE_ID, number=1, status=PhaseStatus.ACTIVE)
        next_phase_id = uuid4()
        phase2 = _make_phase(phase_id=next_phase_id, number=2, status=PhaseStatus.GATED)
        repo.phase = phase1
        repo.phases = [phase1, phase2]
        await saga_repo.save_saga(repo.saga)
        await saga_repo.save_phase(phase1)
        await saga_repo.save_phase(phase2)

        _setup_passing_pr(git, run.pr_id)
        git.changed_files[run.pr_id] = ["src/main.py"]

        q = bus.subscribe()
        result = await engine.evaluate(run.tracker_id, OWNER_ID)

        assert result.phase_gate_unlocked is True

        # Verify next phase was unlocked by tracker_id (string)
        assert len(repo.phase_status_updates) == 1
        assert repo.phase_status_updates[0] == (phase2.tracker_id, PhaseStatus.ACTIVE)

        # Verify phase.unlocked event emitted
        events = []
        while not q.empty():
            events.append(await q.get())
        phase_events = [e for e in events if e.event == "phase.unlocked"]
        assert len(phase_events) == 1
        assert phase_events[0].data["phase_id"] == phase2.tracker_id
        assert phase_events[0].data["owner_id"] == OWNER_ID
        assert phase_events[0].data["saga_id"] == repo.saga.tracker_id

        # Also verify run.state_changed includes saga_tracker_id
        state_events = [e for e in events if e.event == "run.state_changed"]
        assert len(state_events) == 1
        assert state_events[0].data["saga_tracker_id"] == repo.saga.tracker_id

        persisted_phase1 = await saga_repo.get_phase(phase1.id)
        persisted_phase2 = await saga_repo.get_phase(phase2.id)
        persisted_saga = await saga_repo.get_saga(repo.saga.id)
        assert persisted_phase1 is not None and persisted_phase1.status == PhaseStatus.COMPLETE
        assert persisted_phase2 is not None and persisted_phase2.status == PhaseStatus.ACTIVE
        assert persisted_saga is not None and persisted_saga.status == SagaStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_no_phase_gate_when_runs_remain(self) -> None:
        """Phase gate should not unlock when not all runs are merged."""
        engine, repo, git, _, _ = _make_engine()
        run = _make_run(confidence=0.5)
        repo.runs[run.tracker_id] = run
        repo.saga = _make_saga()
        repo.phase = _make_phase()
        repo._all_merged = False

        _setup_passing_pr(git, run.pr_id)
        git.changed_files[run.pr_id] = ["src/main.py"]

        result = await engine.evaluate(run.tracker_id, OWNER_ID)

        assert result.phase_gate_unlocked is False
        assert len(repo.phase_status_updates) == 0

    @pytest.mark.asyncio
    async def test_no_next_phase(self) -> None:
        """Phase gate unlocked but no next phase — should return True without error."""
        engine, repo, git, _, _ = _make_engine()
        saga_repo = engine._saga_repo
        assert isinstance(saga_repo, InMemorySagaRepository)
        run = _make_run(confidence=0.5)
        repo.runs[run.tracker_id] = run
        repo.saga = _make_saga()
        repo._all_merged = True
        repo.phase = _make_phase()
        repo.phases = [_make_phase()]  # Only one phase
        await saga_repo.save_saga(repo.saga)
        await saga_repo.save_phase(repo.phase)

        _setup_passing_pr(git, run.pr_id)
        git.changed_files[run.pr_id] = ["src/main.py"]

        result = await engine.evaluate(run.tracker_id, OWNER_ID)

        assert result.phase_gate_unlocked is True
        assert len(repo.phase_status_updates) == 0
        persisted_phase = await saga_repo.get_phase(repo.phase.id)
        persisted_saga = await saga_repo.get_saga(repo.saga.id)
        assert persisted_phase is not None and persisted_phase.status == PhaseStatus.COMPLETE
        assert persisted_saga is not None and persisted_saga.status == SagaStatus.COMPLETE


# ---------------------------------------------------------------------------
# Tests: Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_run_not_found(self) -> None:
        """Evaluating a non-existent run should raise ValueError."""
        engine, _, _, _, _ = _make_engine()
        with pytest.raises(ValueError, match="Run not found"):
            await engine.evaluate("NIU-NONEXISTENT", OWNER_ID)

    @pytest.mark.asyncio
    async def test_wrong_state(self) -> None:
        """Evaluating a run not in REVIEW should raise ValueError."""
        engine, repo, _, _, _ = _make_engine()
        run = _make_run(status=RunStatus.RUNNING)
        repo.runs[run.tracker_id] = run

        with pytest.raises(ValueError, match="not in REVIEW"):
            await engine.evaluate(run.tracker_id, OWNER_ID)

    @pytest.mark.asyncio
    async def test_changed_files_failure_does_not_block(self) -> None:
        """If fetching changed files fails, review should still proceed."""
        engine, repo, git, _, _ = _make_engine()
        run = _make_run(confidence=0.5)
        repo.runs[run.tracker_id] = run
        repo.saga = _make_saga()
        repo.phase = _make_phase()

        _setup_passing_pr(git, run.pr_id)
        git.fail_changed_files = True

        result = await engine.evaluate(run.tracker_id, OWNER_ID)

        # Should still auto-approve (no scope breach without file data)
        assert result.action == "auto_approved"


# ---------------------------------------------------------------------------
# Tests: Config
# ---------------------------------------------------------------------------


class TestReviewConfig:
    def test_new_config_defaults(self) -> None:
        cfg = ReviewConfig()
        assert cfg.auto_approve_threshold == 0.80
        assert cfg.max_retries == 3
        assert cfg.scope_breach_threshold == 0.30
        assert cfg.confidence_delta_ci_pass == 0.30
        assert cfg.confidence_delta_ci_fail == -0.30
        assert cfg.confidence_delta_mergeable == 0.10
        assert cfg.confidence_delta_conflict == -0.20
        assert cfg.confidence_delta_scope_breach == -0.25
        assert cfg.confidence_delta_retry_multiplier == -0.05

    def test_custom_config(self) -> None:
        cfg = ReviewConfig(auto_approve_threshold=0.90, max_retries=5)
        assert cfg.auto_approve_threshold == 0.90
        assert cfg.max_retries == 5


# ---------------------------------------------------------------------------
# Tests: Event-driven review integration
# ---------------------------------------------------------------------------


class TestEventDrivenReview:
    @pytest.mark.asyncio
    async def test_review_engine_reacts_to_state_changed_event(self) -> None:
        """ReviewEngine should evaluate a run when it sees a REVIEW state_changed event."""
        import asyncio

        from ting.ports.event_bus import TingEvent

        engine, repo, git, bus, _ = _make_engine()
        run = _make_run(confidence=0.5)
        repo.runs[run.tracker_id] = run
        repo.saga = _make_saga()
        repo.phase = _make_phase()

        _setup_passing_pr(git, run.pr_id)
        git.changed_files[run.pr_id] = ["src/main.py"]

        await engine.start()
        await asyncio.sleep(0)  # yield so listener task subscribes

        # Emit a run.state_changed event with status=REVIEW
        await bus.emit(
            TingEvent(
                event="run.state_changed",
                owner_id=OWNER_ID,
                data={
                    "run_id": str(run.id),
                    "status": RunStatus.REVIEW.value,
                    "confidence": run.confidence,
                    "action": "completed",
                    "tracker_id": run.tracker_id,
                },
            )
        )

        # Give the listener task time to process
        await asyncio.sleep(0.1)
        await engine.stop()

        # The engine should have auto-approved the run
        assert repo.runs[run.tracker_id].status == RunStatus.MERGED

    @pytest.mark.asyncio
    async def test_review_engine_ignores_non_review_events(self) -> None:
        """ReviewEngine should ignore state_changed events for non-REVIEW states."""
        import asyncio

        from ting.ports.event_bus import TingEvent

        engine, repo, git, bus, _ = _make_engine()
        run = _make_run(confidence=0.5, status=RunStatus.RUNNING)
        repo.runs[run.tracker_id] = run

        await engine.start()
        await asyncio.sleep(0)

        await bus.emit(
            TingEvent(
                event="run.state_changed",
                owner_id=OWNER_ID,
                data={
                    "run_id": str(run.id),
                    "status": RunStatus.RUNNING.value,
                    "confidence": run.confidence,
                    "action": "started",
                    "tracker_id": run.tracker_id,
                },
            )
        )

        await asyncio.sleep(0.1)
        await engine.stop()

        # Run should still be RUNNING (not evaluated)
        assert repo.runs[run.tracker_id].status == RunStatus.RUNNING

    @pytest.mark.asyncio
    async def test_review_engine_handles_evaluation_error(self) -> None:
        """ReviewEngine should log and continue if evaluation fails."""
        import asyncio

        from ting.ports.event_bus import TingEvent

        engine, repo, git, bus, _ = _make_engine()
        # No run in repo — will cause ValueError("Run not found")

        await engine.start()
        await asyncio.sleep(0)

        await bus.emit(
            TingEvent(
                event="run.state_changed",
                owner_id=OWNER_ID,
                data={
                    "run_id": str(uuid4()),
                    "status": RunStatus.REVIEW.value,
                    "confidence": 0.5,
                    "action": "completed",
                    "tracker_id": "NIU-999",
                },
            )
        )

        await asyncio.sleep(0.1)
        await engine.stop()

        # Engine should have survived the error (no crash)


# ---------------------------------------------------------------------------
# Tests: ConfidenceEventType new values
# ---------------------------------------------------------------------------


class TestNewConfidenceEventTypes:
    def test_auto_approved_exists(self) -> None:
        assert ConfidenceEventType.AUTO_APPROVED == "auto_approved"

    def test_pr_conflict_exists(self) -> None:
        assert ConfidenceEventType.PR_CONFLICT == "pr_conflict"

    def test_pr_mergeable_exists(self) -> None:
        assert ConfidenceEventType.PR_MERGEABLE == "pr_mergeable"

    def test_all_values_serializable(self) -> None:
        for evt in ConfidenceEventType:
            assert isinstance(evt.value, str)


# ---------------------------------------------------------------------------
# Tests: Mergeable signal uses PR_MERGEABLE (not CI_PASS)
# ---------------------------------------------------------------------------


class TestMergeableSignal:
    @pytest.mark.asyncio
    async def test_mergeable_records_pr_mergeable_event(self) -> None:
        """Mergeable PR should record PR_MERGEABLE, not CI_PASS."""
        engine, repo, git, _, _ = _make_engine()
        run = _make_run(confidence=0.5)
        repo.runs[run.tracker_id] = run
        repo.saga = _make_saga()
        repo.phase = _make_phase()

        _setup_passing_pr(git, run.pr_id)
        git.changed_files[run.pr_id] = ["src/main.py"]

        await engine.evaluate(run.tracker_id, OWNER_ID)

        events = repo.events[run.tracker_id]
        event_types = [e.event_type for e in events]
        assert ConfidenceEventType.PR_MERGEABLE in event_types
        # CI_PASS should only appear once (from CI signal, not mergeable)
        ci_pass_count = sum(1 for t in event_types if t == ConfidenceEventType.CI_PASS)
        assert ci_pass_count == 1


# ---------------------------------------------------------------------------
# Tests: Session feedback on retry
# ---------------------------------------------------------------------------


class TestSessionFeedback:
    @pytest.mark.asyncio
    async def test_retry_sends_message_to_session(self) -> None:
        """Auto-retry should send failure context to the session."""
        volundr = StubVolundr()
        engine, repo, git, _, _ = _make_engine(volundr=volundr)
        run = _make_run(retry_count=0)
        repo.runs[run.tracker_id] = run

        _setup_failing_pr(git, run.pr_id)

        result = await engine.evaluate(run.tracker_id, OWNER_ID)

        assert result.action == "retried"
        assert len(volundr.messages) == 1
        session_id, message = volundr.messages[0]
        assert session_id == "session-1"
        assert "CI failed" in message

    @pytest.mark.asyncio
    async def test_retry_sends_conflict_message(self) -> None:
        """Auto-retry for PR conflicts should send conflict context."""
        volundr = StubVolundr()
        engine, repo, git, _, _ = _make_engine(volundr=volundr)
        run = _make_run(retry_count=0)
        repo.runs[run.tracker_id] = run

        _setup_conflicted_pr(git, run.pr_id)

        result = await engine.evaluate(run.tracker_id, OWNER_ID)

        assert result.action == "retried"
        assert len(volundr.messages) == 1
        assert "PR conflicts" in volundr.messages[0][1]

    @pytest.mark.asyncio
    async def test_retry_without_volundr_does_not_fail(self) -> None:
        """Auto-retry without VolundrPort should still work."""
        engine, repo, git, _, _ = _make_engine()  # no volundr
        run = _make_run(retry_count=0)
        repo.runs[run.tracker_id] = run

        _setup_failing_pr(git, run.pr_id)

        result = await engine.evaluate(run.tracker_id, OWNER_ID)

        assert result.action == "retried"

    @pytest.mark.asyncio
    async def test_retry_send_failure_does_not_block(self) -> None:
        """If sending the message fails, retry should still proceed."""
        volundr = StubVolundr()
        volundr.fail_send = True
        engine, repo, git, _, _ = _make_engine(volundr=volundr)
        run = _make_run(retry_count=0)
        repo.runs[run.tracker_id] = run

        _setup_failing_pr(git, run.pr_id)

        result = await engine.evaluate(run.tracker_id, OWNER_ID)

        assert result.action == "retried"
        assert repo.runs[run.tracker_id].status == RunStatus.PENDING

    @pytest.mark.asyncio
    async def test_retry_no_session_id_skips_message(self) -> None:
        """If the run has no session_id, skip sending the message."""
        volundr = StubVolundr()
        engine, repo, git, _, _ = _make_engine(volundr=volundr)
        run = _make_run(retry_count=0)
        # Clear session_id
        run = Run(
            id=run.id,
            phase_id=run.phase_id,
            tracker_id=run.tracker_id,
            name=run.name,
            description=run.description,
            acceptance_criteria=run.acceptance_criteria,
            declared_files=run.declared_files,
            estimate_hours=run.estimate_hours,
            status=run.status,
            confidence=run.confidence,
            session_id=None,
            branch=run.branch,
            chronicle_summary=run.chronicle_summary,
            pr_url=run.pr_url,
            pr_id=run.pr_id,
            retry_count=run.retry_count,
            created_at=run.created_at,
            updated_at=run.updated_at,
        )
        repo.runs[run.tracker_id] = run

        _setup_failing_pr(git, run.pr_id)

        result = await engine.evaluate(run.tracker_id, OWNER_ID)

        assert result.action == "retried"
        assert len(volundr.messages) == 0


# ---------------------------------------------------------------------------
# Tests: Working session cleanup on terminal states (NIU-471)
# ---------------------------------------------------------------------------


class TestWorkingSessionCleanup:
    """Verify working session transcript attachment and stop on terminal states."""

    @pytest.mark.asyncio
    async def test_merged_stops_working_session(self) -> None:
        """MERGED should stop the working session."""
        volundr = StubVolundr()
        engine, repo, git, _, _ = _make_engine(volundr=volundr)
        run = _make_run(confidence=0.5)
        repo.runs[run.tracker_id] = run
        repo.saga = _make_saga()
        repo.phase = _make_phase()

        _setup_passing_pr(git, run.pr_id)
        git.changed_files[run.pr_id] = ["src/main.py", "tests/test_main.py"]

        await engine.evaluate(run.tracker_id, OWNER_ID)

        assert run.session_id in volundr.stopped_sessions

    @pytest.mark.asyncio
    async def test_ci_failure_exhausted_stops_working_session(self) -> None:
        """FAILED (CI retries exhausted) should stop the working session."""
        volundr = StubVolundr()
        config = _default_config(max_retries=3)
        engine, repo, git, _, _ = _make_engine(config=config, volundr=volundr)
        run = _make_run(retry_count=3)
        repo.runs[run.tracker_id] = run

        _setup_failing_pr(git, run.pr_id)

        await engine.evaluate(run.tracker_id, OWNER_ID)

        assert run.session_id in volundr.stopped_sessions

    @pytest.mark.asyncio
    async def test_conflict_exhausted_stops_working_session(self) -> None:
        """FAILED (conflict retries exhausted) should stop the working session."""
        volundr = StubVolundr()
        config = _default_config(max_retries=2)
        engine, repo, git, _, _ = _make_engine(config=config, volundr=volundr)
        run = _make_run(retry_count=2)
        repo.runs[run.tracker_id] = run

        _setup_conflicted_pr(git, run.pr_id)

        await engine.evaluate(run.tracker_id, OWNER_ID)

        assert run.session_id in volundr.stopped_sessions

    @pytest.mark.asyncio
    async def test_escalated_does_not_stop_working_session(self) -> None:
        """ESCALATED should NOT stop the working session."""
        volundr = StubVolundr()
        config = _default_config(auto_approve_threshold=0.95)
        engine, repo, git, _, _ = _make_engine(config=config, volundr=volundr)
        run = _make_run(confidence=0.5)
        repo.runs[run.tracker_id] = run

        _setup_passing_pr(git, run.pr_id)
        git.changed_files[run.pr_id] = ["src/main.py"]

        await engine.evaluate(run.tracker_id, OWNER_ID)

        assert run.session_id not in volundr.stopped_sessions

    @pytest.mark.asyncio
    async def test_merged_attaches_working_transcript(self) -> None:
        """MERGED should attach the working session transcript to the tracker."""
        volundr = StubVolundr()
        engine, repo, git, _, _ = _make_engine(volundr=volundr)
        run = _make_run(confidence=0.5)
        repo.runs[run.tracker_id] = run
        repo.saga = _make_saga()
        repo.phase = _make_phase()

        _setup_passing_pr(git, run.pr_id)
        git.changed_files[run.pr_id] = ["src/main.py", "tests/test_main.py"]

        await engine.evaluate(run.tracker_id, OWNER_ID)

        assert any("Working Session Transcript" in title for _, title, _ in repo.attached_documents)

    @pytest.mark.asyncio
    async def test_failed_attaches_working_transcript(self) -> None:
        """FAILED should attach the working session transcript to the tracker."""
        volundr = StubVolundr()
        config = _default_config(max_retries=3)
        engine, repo, git, _, _ = _make_engine(config=config, volundr=volundr)
        run = _make_run(retry_count=3)
        repo.runs[run.tracker_id] = run

        _setup_failing_pr(git, run.pr_id)

        await engine.evaluate(run.tracker_id, OWNER_ID)

        assert any("Working Session Transcript" in title for _, title, _ in repo.attached_documents)

    @pytest.mark.asyncio
    async def test_escalated_attaches_working_transcript(self) -> None:
        """ESCALATED should attach the working session transcript as a snapshot."""
        volundr = StubVolundr()
        config = _default_config(auto_approve_threshold=0.95)
        engine, repo, git, _, _ = _make_engine(config=config, volundr=volundr)
        run = _make_run(confidence=0.5)
        repo.runs[run.tracker_id] = run

        _setup_passing_pr(git, run.pr_id)
        git.changed_files[run.pr_id] = ["src/main.py"]

        result = await engine.evaluate(run.tracker_id, OWNER_ID)

        assert result.action == "escalated"
        assert any("Working Session Transcript" in title for _, title, _ in repo.attached_documents)
        # Session should NOT be stopped for escalation
        assert run.session_id not in volundr.stopped_sessions


# ---------------------------------------------------------------------------
# Regression: ReviewEngine respects persona LLM via default_llm_config
# (NIU-645 — RavnDispatcher honors persona.llm + flock LLM config)
# ---------------------------------------------------------------------------


class TestReviewEngineLLMConfigRegression:
    """Changing default_llm_config on RavnDispatcher changes the model used."""

    @pytest.mark.asyncio
    async def test_changing_llm_config_changes_model_dispatched(self) -> None:
        """When reviewer persona has llm.primary_alias='powerful' but a cheap
        global override is set, the cheap model is used.

        This is the NIU-645 regression: previously RavnDispatcher always used
        the hardcoded constructor arg regardless of persona or config.
        """
        import json

        import httpx
        import respx

        from ting.adapters.ravn_dispatcher import RavnDispatcher

        captured_models: list[str] = []

        def mock_llm(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            captured_models.append(body["model"])
            outcome = "---outcome---\nverdict: approve\nreason: clean\n---end---"
            return httpx.Response(
                200,
                json={
                    "content": [{"type": "text", "text": outcome}],
                    "usage": {"input_tokens": 10, "output_tokens": 20},
                },
            )

        with respx.mock:
            respx.post("http://ravn.test/v1/messages").mock(side_effect=mock_llm)

            # Dispatcher configured with explicit global model override
            ravn = RavnDispatcher(
                base_url="http://ravn.test",
                api_key="key",
                model="claude-sonnet-4-6",  # constructor default
                default_llm_config={"model": "claude-opus-4-6"},  # global override
            )
            try:
                tracker = StubTracker()
                git = StubGit()
                cfg = ReviewConfig(
                    reviewer_session_enabled=False,
                    ravn_arbiter_enabled=True,
                    auto_approve_threshold=0.80,
                    max_retries=3,
                )

                class _FakeFactory:
                    async def for_owner(self, owner_id: str) -> list[StubTracker]:
                        return [tracker]

                    async def primary_for_owner(self, owner_id: str) -> StubTracker | None:
                        return None

                engine = ReviewEngine(
                    tracker_factory=StubTrackerFactory(tracker),
                    volundr_factory=_FakeFactory(),
                    git=git,
                    review_config=cfg,
                    event_bus=InMemoryEventBus(),
                    ravn_dispatcher=ravn,
                )

                run = _make_run(confidence=0.9)
                tracker.runs[run.tracker_id] = run
                tracker.saga = _make_saga()
                tracker.phase = _make_phase()
                git.pr_statuses[run.pr_id] = PRStatus(
                    pr_id=run.pr_id,
                    url="https://github.com/org/repo/pull/42",
                    state="open",
                    mergeable=True,
                    ci_passed=True,
                )
                git.changed_files[run.pr_id] = list(run.declared_files)

                await engine.evaluate(run.tracker_id, "owner-llm-test")
            finally:
                await ravn.close()

        # The model used must be the one from default_llm_config, not the constructor default
        assert len(captured_models) >= 1
        assert captured_models[0] == "claude-opus-4-6", (
            f"Expected claude-opus-4-6 (from default_llm_config), "
            f"got {captured_models[0]!r} — NIU-645 regression"
        )

    @pytest.mark.asyncio
    async def test_different_llm_config_uses_different_model(self) -> None:
        """Two dispatchers with different default_llm_config use different models.

        Verifies that swapping the config changes behavior without restarting —
        the key acceptance criterion for NIU-645.
        """
        import json

        import httpx
        import respx

        from ting.adapters.ravn_dispatcher import RavnDispatcher

        async def _run_with_config(model_override: str) -> str:
            """Run one dispatch and return the model that was sent to the API."""
            sent_models: list[str] = []

            def handler(request: httpx.Request) -> httpx.Response:
                body = json.loads(request.content)
                sent_models.append(body["model"])
                outcome = "---outcome---\nverdict: escalate\nreason: test\n---end---"
                return httpx.Response(
                    200,
                    json={
                        "content": [{"type": "text", "text": outcome}],
                        "usage": {"input_tokens": 5, "output_tokens": 10},
                    },
                )

            with respx.mock:
                respx.post("http://ravn2.test/v1/messages").mock(side_effect=handler)

                ravn = RavnDispatcher(
                    base_url="http://ravn2.test",
                    api_key="key",
                    model="claude-sonnet-4-6",
                    default_llm_config={"model": model_override},
                )
                try:
                    tracker = StubTracker()
                    git = StubGit()
                    cfg = ReviewConfig(
                        reviewer_session_enabled=False,
                        ravn_arbiter_enabled=True,
                    )

                    class _FF:
                        async def for_owner(self, owner_id: str) -> list[StubTracker]:
                            return [tracker]

                        async def primary_for_owner(self, owner_id: str) -> StubTracker | None:
                            return None

                    engine = ReviewEngine(
                        tracker_factory=StubTrackerFactory(tracker),
                        volundr_factory=_FF(),
                        git=git,
                        review_config=cfg,
                        event_bus=InMemoryEventBus(),
                        ravn_dispatcher=ravn,
                    )
                    run = _make_run(confidence=0.5)
                    tracker.runs[run.tracker_id] = run
                    git.pr_statuses[run.pr_id] = PRStatus(
                        pr_id=run.pr_id,
                        url="https://github.com/org/repo/pull/42",
                        state="open",
                        mergeable=True,
                        ci_passed=True,
                    )
                    await engine.evaluate(run.tracker_id, "owner-diff-model")
                finally:
                    await ravn.close()

            return sent_models[0] if sent_models else ""

        model_a = await _run_with_config("claude-opus-4-6")
        model_b = await _run_with_config("claude-haiku-4-5-20251001")

        assert model_a == "claude-opus-4-6"
        assert model_b == "claude-haiku-4-5-20251001"
        assert model_a != model_b


# ---------------------------------------------------------------------------
# Reviewer-failure handler — recovery from spawn failures (e.g. cap reached)
# ---------------------------------------------------------------------------


class TestReviewerFailure:
    @pytest.mark.asyncio
    async def test_reviewer_failure_marks_run_failed(self) -> None:
        """When a tracked reviewer dies, the run transitions to FAILED."""
        engine, tracker, _git, _ev, _vol = _make_engine()
        reviewer_id = "reviewer-session-xyz"
        run = _make_run(
            tracker_id="NIU-777",
            status=RunStatus.REVIEW,
            reviewer_session_id=reviewer_id,
        )
        tracker.runs[run.tracker_id] = run
        engine._reviewer_sessions[reviewer_id] = (run.tracker_id, "dev-user")

        await engine.handle_reviewer_failure(reviewer_id, reason="Max concurrent")

        updated = tracker.runs[run.tracker_id]
        assert updated.status == RunStatus.FAILED
        assert reviewer_id not in engine._reviewer_sessions

    @pytest.mark.asyncio
    async def test_reviewer_failure_for_untracked_session_is_noop(self) -> None:
        """Failures for unknown sessions are silently ignored."""
        engine, _t, _g, _e, _v = _make_engine()
        # Should not raise even though no mapping exists.
        await engine.handle_reviewer_failure("nope", reason="something")

    @pytest.mark.asyncio
    async def test_run_failure_triggers_auto_continue_for_saga(self) -> None:
        """Terminal run failures should refill any newly freed dispatch slot."""
        engine, tracker, _git, _ev, _vol = _make_engine()
        engine._dispatch_service = type("DispatchServiceStub", (), {})()
        engine._dispatch_service.try_auto_continue = AsyncMock()
        tracker.saga = _make_saga()
        run = _make_run(tracker_id="NIU-778", status=RunStatus.RUNNING)
        tracker.runs[run.tracker_id] = run

        handled = await engine.handle_run_failure(
            run.tracker_id,
            "dev-user",
            reason="Session stopped",
        )

        assert handled is True
        updated = tracker.runs[run.tracker_id]
        assert updated.status == RunStatus.FAILED
        engine._dispatch_service.try_auto_continue.assert_awaited_once_with(
            "dev-user",
            tracker.saga.tracker_id,
        )
