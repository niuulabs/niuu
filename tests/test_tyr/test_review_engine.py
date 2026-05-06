"""Tests for the automated review engine (NIU-239)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from tyr.adapters.memory_event_bus import InMemoryEventBus
from tyr.config import ReviewConfig
from tyr.domain.models import (
    ConfidenceEvent,
    ConfidenceEventType,
    Phase,
    PhaseStatus,
    PRStatus,
    Raid,
    RaidStatus,
    Saga,
    SagaStatus,
    SessionMessage,
    TrackerIssue,
    TrackerMilestone,
    TrackerProject,
)
from tyr.domain.services.review_engine import (
    ReviewEngine,
    detect_scope_breach,
)
from tyr.ports.git import GitPort
from tyr.ports.tracker import TrackerPort
from tyr.ports.volundr import ActivityEvent, SpawnRequest, VolundrPort, VolundrSession

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
        # raids keyed by tracker_id (str)
        self.raids: dict[str, Raid] = {}
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

    async def create_raid(self, raid: Raid, *, project_id: str = "", milestone_id: str = "") -> str:
        self.raids[raid.tracker_id] = raid
        return raid.tracker_id

    # -- CRUD: update / close --

    async def update_raid_state(self, raid_id: str, state: RaidStatus) -> None:
        pass

    async def close_raid(self, raid_id: str) -> None:
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

    async def get_raid(self, tracker_id: str) -> Raid:
        raid = self.raids.get(tracker_id)
        if raid is None:
            raise ValueError(f"Raid not found: {tracker_id}")
        return raid

    async def list_pending_raids(self, phase_id: str) -> list[Raid]:
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

    # -- Raid progress --

    async def update_raid_progress(
        self,
        tracker_id: str,
        *,
        status: RaidStatus | None = None,
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
    ) -> Raid:
        raid = self.raids.get(tracker_id)
        if raid is None:
            raise ValueError(f"Raid not found: {tracker_id}")
        events = self.events.get(tracker_id, [])
        new_confidence = events[-1].score_after if events else raid.confidence
        updated = Raid(
            id=raid.id,
            phase_id=raid.phase_id,
            tracker_id=raid.tracker_id,
            name=raid.name,
            description=raid.description,
            acceptance_criteria=raid.acceptance_criteria,
            declared_files=raid.declared_files,
            estimate_hours=raid.estimate_hours,
            status=status if status is not None else raid.status,
            confidence=confidence if confidence is not None else new_confidence,
            session_id=session_id if session_id is not None else raid.session_id,
            branch=raid.branch,
            chronicle_summary=raid.chronicle_summary,
            pr_url=pr_url if pr_url is not None else raid.pr_url,
            pr_id=pr_id if pr_id is not None else raid.pr_id,
            retry_count=retry_count if retry_count is not None else raid.retry_count,
            created_at=raid.created_at,
            updated_at=datetime.now(UTC),
            reviewer_session_id=(
                reviewer_session_id
                if reviewer_session_id is not None
                else raid.reviewer_session_id
            ),
        )
        self.raids[tracker_id] = updated
        return updated

    async def get_raid_progress_for_saga(self, saga_tracker_id: str) -> list[Raid]:
        return list(self.raids.values())

    async def get_raid_by_session(self, session_id: str) -> Raid | None:
        return next((r for r in self.raids.values() if r.session_id == session_id), None)

    async def list_raids_by_status(self, status: RaidStatus) -> list[Raid]:
        return [r for r in self.raids.values() if r.status == status]

    async def get_raid_by_id(self, raid_id: UUID) -> Raid | None:
        return next((r for r in self.raids.values() if r.id == raid_id), None)

    # -- Confidence events --

    async def add_confidence_event(self, tracker_id: str, event: ConfidenceEvent) -> None:
        self.events.setdefault(tracker_id, []).append(event)

    async def get_confidence_events(self, tracker_id: str) -> list[ConfidenceEvent]:
        return self.events.get(tracker_id, [])

    # -- Phase gate management --

    async def all_raids_merged(self, phase_tracker_id: str) -> bool:
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

    async def get_saga_for_raid(self, tracker_id: str) -> Saga | None:
        return self.saga

    async def get_phase_for_raid(self, tracker_id: str) -> Phase | None:
        return self.phase

    async def get_owner_for_raid(self, tracker_id: str) -> str | None:
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


def _make_raid(
    raid_id: UUID | None = None,
    tracker_id: str = TRACKER_ID,
    status: RaidStatus = RaidStatus.REVIEW,
    confidence: float = 0.5,
    pr_id: str | None = "https://api.github.com/repos/org/repo/pulls/42",
    branch: str | None = "raid/test-branch",
    declared_files: list[str] | None = None,
    retry_count: int = 0,
    reviewer_session_id: str | None = None,
) -> Raid:
    return Raid(
        id=raid_id or uuid4(),
        phase_id=PHASE_ID,
        tracker_id=tracker_id,
        name="Test raid",
        description="A test raid",
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
) -> tuple[ReviewEngine, StubTracker, StubGit, InMemoryEventBus, StubVolundr]:
    r = tracker or StubTracker()
    g = git or StubGit()
    e = event_bus or InMemoryEventBus()
    c = config or _default_config()
    v = volundr or StubVolundr()

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
        """Raid with high confidence, passing CI, and mergeable PR is auto-approved."""
        engine, repo, git, bus, _ = _make_engine()
        raid = _make_raid(confidence=0.5)
        repo.raids[raid.tracker_id] = raid
        repo.saga = _make_saga()
        repo.phase = _make_phase()

        pr_id = raid.pr_id
        _setup_passing_pr(git, pr_id)
        git.changed_files[pr_id] = ["src/main.py", "tests/test_main.py"]

        result = await engine.evaluate(raid.tracker_id, OWNER_ID)

        assert result.action == "auto_approved"
        assert repo.raids[raid.tracker_id].status == RaidStatus.MERGED

    @pytest.mark.asyncio
    async def test_auto_approve_does_not_merge_branch(self) -> None:
        """Auto-approve should NOT merge the branch — the reviewer session does it."""
        engine, repo, git, _, _ = _make_engine()
        raid = _make_raid(confidence=0.5)
        repo.raids[raid.tracker_id] = raid
        repo.saga = _make_saga()
        repo.phase = _make_phase()

        _setup_passing_pr(git, raid.pr_id)
        git.changed_files[raid.pr_id] = ["src/main.py"]

        await engine.evaluate(raid.tracker_id, OWNER_ID)

        assert len(git.merged) == 0
        assert len(git.deleted) == 0

    @pytest.mark.asyncio
    async def test_auto_approve_emits_events(self) -> None:
        """Auto-approve should emit raid.state_changed and confidence.updated events."""
        engine, repo, git, bus, _ = _make_engine()
        raid = _make_raid(confidence=0.5)
        repo.raids[raid.tracker_id] = raid
        repo.saga = _make_saga()
        repo.phase = _make_phase()

        _setup_passing_pr(git, raid.pr_id)
        git.changed_files[raid.pr_id] = ["src/main.py"]

        q = bus.subscribe()
        await engine.evaluate(raid.tracker_id, OWNER_ID)

        events = []
        while not q.empty():
            events.append(await q.get())

        event_types = [e.event for e in events]
        assert "confidence.updated" in event_types
        assert "raid.state_changed" in event_types

        state_event = next(e for e in events if e.event == "raid.state_changed")
        assert state_event.data["action"] == "auto_approved"
        assert state_event.data["saga_tracker_id"] == "proj-1"

    @pytest.mark.asyncio
    async def test_auto_approve_event_no_saga(self) -> None:
        """raid.state_changed omits saga_tracker_id when saga is not found."""
        engine, repo, git, bus, _ = _make_engine()
        raid = _make_raid(confidence=0.5)
        repo.raids[raid.tracker_id] = raid
        repo.saga = None
        repo.phase = _make_phase()
        repo._all_merged = False

        _setup_passing_pr(git, raid.pr_id)
        git.changed_files[raid.pr_id] = ["src/main.py"]

        q = bus.subscribe()
        await engine.evaluate(raid.tracker_id, OWNER_ID)

        events = []
        while not q.empty():
            events.append(await q.get())

        state_event = next(e for e in events if e.event == "raid.state_changed")
        assert "saga_tracker_id" not in state_event.data

    @pytest.mark.asyncio
    async def test_auto_approve_records_confidence_events(self) -> None:
        """Auto-approve should record CI_PASS and AUTO_APPROVED confidence events."""
        engine, repo, git, _, _ = _make_engine()
        raid = _make_raid(confidence=0.5)
        repo.raids[raid.tracker_id] = raid
        repo.saga = _make_saga()
        repo.phase = _make_phase()

        _setup_passing_pr(git, raid.pr_id)
        git.changed_files[raid.pr_id] = ["src/main.py"]

        await engine.evaluate(raid.tracker_id, OWNER_ID)

        events = repo.events[raid.tracker_id]
        event_types = [e.event_type for e in events]
        assert ConfidenceEventType.CI_PASS in event_types
        assert ConfidenceEventType.AUTO_APPROVED in event_types

    @pytest.mark.asyncio
    async def test_merge_failure_does_not_block_approval(self) -> None:
        """If branch merge fails, the raid should still transition to MERGED."""
        engine, repo, git, _, _ = _make_engine()
        raid = _make_raid(confidence=0.5)
        repo.raids[raid.tracker_id] = raid
        repo.saga = _make_saga()
        repo.phase = _make_phase()

        _setup_passing_pr(git, raid.pr_id)
        git.changed_files[raid.pr_id] = ["src/main.py"]
        git.fail_merge = True

        result = await engine.evaluate(raid.tracker_id, OWNER_ID)

        assert result.action == "auto_approved"
        assert repo.raids[raid.tracker_id].status == RaidStatus.MERGED


# ---------------------------------------------------------------------------
# Tests: CI failure
# ---------------------------------------------------------------------------


class TestCIFailure:
    @pytest.mark.asyncio
    async def test_ci_failure_auto_retry(self) -> None:
        """CI failure with retries remaining should auto-retry (REVIEW → PENDING)."""
        engine, repo, git, _, _ = _make_engine()
        raid = _make_raid(retry_count=0)
        repo.raids[raid.tracker_id] = raid

        _setup_failing_pr(git, raid.pr_id)

        result = await engine.evaluate(raid.tracker_id, OWNER_ID)

        assert result.action == "retried"
        assert repo.raids[raid.tracker_id].status == RaidStatus.PENDING
        assert repo.raids[raid.tracker_id].retry_count == 1

    @pytest.mark.asyncio
    async def test_ci_failure_retries_exhausted(self) -> None:
        """CI failure with no retries left should transition to FAILED."""
        config = _default_config(max_retries=3)
        engine, repo, git, _, _ = _make_engine(config=config)
        raid = _make_raid(retry_count=3)
        repo.raids[raid.tracker_id] = raid

        _setup_failing_pr(git, raid.pr_id)

        result = await engine.evaluate(raid.tracker_id, OWNER_ID)

        assert result.action == "failed"
        assert repo.raids[raid.tracker_id].status == RaidStatus.FAILED

    @pytest.mark.asyncio
    async def test_ci_failure_emits_ci_fail_event(self) -> None:
        """CI failure should record a CI_FAIL confidence event."""
        engine, repo, git, _, _ = _make_engine()
        raid = _make_raid()
        repo.raids[raid.tracker_id] = raid

        _setup_failing_pr(git, raid.pr_id)

        await engine.evaluate(raid.tracker_id, OWNER_ID)

        events = repo.events[raid.tracker_id]
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
        raid = _make_raid(retry_count=0)
        repo.raids[raid.tracker_id] = raid

        _setup_conflicted_pr(git, raid.pr_id)

        result = await engine.evaluate(raid.tracker_id, OWNER_ID)

        assert result.action == "retried"
        assert repo.raids[raid.tracker_id].status == RaidStatus.PENDING

    @pytest.mark.asyncio
    async def test_conflict_retries_exhausted(self) -> None:
        """PR with conflicts and no retries left should fail."""
        config = _default_config(max_retries=2)
        engine, repo, git, _, _ = _make_engine(config=config)
        raid = _make_raid(retry_count=2)
        repo.raids[raid.tracker_id] = raid

        _setup_conflicted_pr(git, raid.pr_id)

        result = await engine.evaluate(raid.tracker_id, OWNER_ID)

        assert result.action == "failed"
        assert repo.raids[raid.tracker_id].status == RaidStatus.FAILED

    @pytest.mark.asyncio
    async def test_conflict_records_pr_conflict_event(self) -> None:
        """PR conflict should record a PR_CONFLICT confidence event."""
        engine, repo, git, _, _ = _make_engine()
        raid = _make_raid()
        repo.raids[raid.tracker_id] = raid

        _setup_conflicted_pr(git, raid.pr_id)

        await engine.evaluate(raid.tracker_id, OWNER_ID)

        events = repo.events[raid.tracker_id]
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
        raid = _make_raid(
            declared_files=["src/main.py"],
        )
        repo.raids[raid.tracker_id] = raid
        repo.saga = _make_saga()
        repo.phase = _make_phase()

        _setup_passing_pr(git, raid.pr_id)
        # 3 out of 4 files are undeclared → 75% breach
        git.changed_files[raid.pr_id] = [
            "src/main.py",
            "src/extra1.py",
            "src/extra2.py",
            "src/extra3.py",
        ]

        await engine.evaluate(raid.tracker_id, OWNER_ID)

        events = repo.events[raid.tracker_id]
        event_types = [e.event_type for e in events]
        assert ConfidenceEventType.SCOPE_BREACH in event_types

    @pytest.mark.asyncio
    async def test_no_scope_breach_within_threshold(self) -> None:
        """No scope breach when undeclared files are within threshold."""
        engine, repo, git, _, _ = _make_engine()
        raid = _make_raid(
            declared_files=["src/main.py", "src/b.py", "src/c.py"],
        )
        repo.raids[raid.tracker_id] = raid
        repo.saga = _make_saga()
        repo.phase = _make_phase()

        _setup_passing_pr(git, raid.pr_id)
        # 1 out of 4 files undeclared → 25% < 30%
        git.changed_files[raid.pr_id] = ["src/main.py", "src/b.py", "src/c.py", "src/d.py"]

        await engine.evaluate(raid.tracker_id, OWNER_ID)

        events = repo.events[raid.tracker_id]
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
        raid = _make_raid(confidence=0.5)
        repo.raids[raid.tracker_id] = raid

        _setup_passing_pr(git, raid.pr_id)
        git.changed_files[raid.pr_id] = ["src/main.py"]

        result = await engine.evaluate(raid.tracker_id, OWNER_ID)

        assert result.action == "escalated"
        # Raid transitions to ESCALATED (no longer REVIEW, preventing re-trigger)
        assert repo.raids[raid.tracker_id].status == RaidStatus.ESCALATED

    @pytest.mark.asyncio
    async def test_no_pr_escalates(self) -> None:
        """Raid without a PR ID should escalate to human review."""
        engine, repo, git, _, _ = _make_engine()
        raid = _make_raid(pr_id=None, confidence=0.9)
        repo.raids[raid.tracker_id] = raid

        result = await engine.evaluate(raid.tracker_id, OWNER_ID)

        assert result.action == "escalated"

    @pytest.mark.asyncio
    async def test_pr_status_fetch_failure_escalates(self) -> None:
        """If PR status cannot be fetched, escalate to human review."""
        engine, repo, git, _, _ = _make_engine()
        raid = _make_raid(confidence=0.9)
        repo.raids[raid.tracker_id] = raid
        git.fail_pr_status = True

        result = await engine.evaluate(raid.tracker_id, OWNER_ID)

        assert result.action == "escalated"


# ---------------------------------------------------------------------------
# Tests: Retry penalty
# ---------------------------------------------------------------------------


class TestRetryPenalty:
    @pytest.mark.asyncio
    async def test_retry_count_applies_penalty(self) -> None:
        """Previous retries should apply a cumulative confidence penalty."""
        engine, repo, git, _, _ = _make_engine()
        raid = _make_raid(confidence=0.5, retry_count=2)
        repo.raids[raid.tracker_id] = raid
        repo.saga = _make_saga()
        repo.phase = _make_phase()

        _setup_passing_pr(git, raid.pr_id)
        git.changed_files[raid.pr_id] = ["src/main.py"]

        await engine.evaluate(raid.tracker_id, OWNER_ID)

        events = repo.events[raid.tracker_id]
        retry_events = [e for e in events if e.event_type == ConfidenceEventType.RETRY]
        assert len(retry_events) == 1
        # -0.05 * 2 = -0.10
        assert retry_events[0].delta == pytest.approx(-0.10)

    @pytest.mark.asyncio
    async def test_zero_retries_no_penalty(self) -> None:
        """First attempt should not apply a retry penalty."""
        engine, repo, git, _, _ = _make_engine()
        raid = _make_raid(confidence=0.5, retry_count=0)
        repo.raids[raid.tracker_id] = raid
        repo.saga = _make_saga()
        repo.phase = _make_phase()

        _setup_passing_pr(git, raid.pr_id)
        git.changed_files[raid.pr_id] = ["src/main.py"]

        await engine.evaluate(raid.tracker_id, OWNER_ID)

        events = repo.events[raid.tracker_id]
        retry_events = [e for e in events if e.event_type == ConfidenceEventType.RETRY]
        assert len(retry_events) == 0


# ---------------------------------------------------------------------------
# Tests: Phase gate
# ---------------------------------------------------------------------------


class TestPhaseGate:
    @pytest.mark.asyncio
    async def test_phase_gate_unlocked(self) -> None:
        """When all raids in a phase are merged, the next phase is unlocked."""
        engine, repo, git, bus, _ = _make_engine()
        raid = _make_raid(confidence=0.5)
        repo.raids[raid.tracker_id] = raid
        repo.saga = _make_saga()
        repo._all_merged = True

        phase1 = _make_phase(phase_id=PHASE_ID, number=1, status=PhaseStatus.ACTIVE)
        next_phase_id = uuid4()
        phase2 = _make_phase(phase_id=next_phase_id, number=2, status=PhaseStatus.GATED)
        repo.phase = phase1
        repo.phases = [phase1, phase2]

        _setup_passing_pr(git, raid.pr_id)
        git.changed_files[raid.pr_id] = ["src/main.py"]

        q = bus.subscribe()
        result = await engine.evaluate(raid.tracker_id, OWNER_ID)

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

        # Also verify raid.state_changed includes saga_tracker_id
        state_events = [e for e in events if e.event == "raid.state_changed"]
        assert len(state_events) == 1
        assert state_events[0].data["saga_tracker_id"] == repo.saga.tracker_id

    @pytest.mark.asyncio
    async def test_no_phase_gate_when_raids_remain(self) -> None:
        """Phase gate should not unlock when not all raids are merged."""
        engine, repo, git, _, _ = _make_engine()
        raid = _make_raid(confidence=0.5)
        repo.raids[raid.tracker_id] = raid
        repo.saga = _make_saga()
        repo.phase = _make_phase()
        repo._all_merged = False

        _setup_passing_pr(git, raid.pr_id)
        git.changed_files[raid.pr_id] = ["src/main.py"]

        result = await engine.evaluate(raid.tracker_id, OWNER_ID)

        assert result.phase_gate_unlocked is False
        assert len(repo.phase_status_updates) == 0

    @pytest.mark.asyncio
    async def test_no_next_phase(self) -> None:
        """Phase gate unlocked but no next phase — should return True without error."""
        engine, repo, git, _, _ = _make_engine()
        raid = _make_raid(confidence=0.5)
        repo.raids[raid.tracker_id] = raid
        repo.saga = _make_saga()
        repo._all_merged = True
        repo.phase = _make_phase()
        repo.phases = [_make_phase()]  # Only one phase

        _setup_passing_pr(git, raid.pr_id)
        git.changed_files[raid.pr_id] = ["src/main.py"]

        result = await engine.evaluate(raid.tracker_id, OWNER_ID)

        assert result.phase_gate_unlocked is True
        assert len(repo.phase_status_updates) == 0


# ---------------------------------------------------------------------------
# Tests: Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_raid_not_found(self) -> None:
        """Evaluating a non-existent raid should raise ValueError."""
        engine, _, _, _, _ = _make_engine()
        with pytest.raises(ValueError, match="Raid not found"):
            await engine.evaluate("NIU-NONEXISTENT", OWNER_ID)

    @pytest.mark.asyncio
    async def test_wrong_state(self) -> None:
        """Evaluating a raid not in REVIEW should raise ValueError."""
        engine, repo, _, _, _ = _make_engine()
        raid = _make_raid(status=RaidStatus.RUNNING)
        repo.raids[raid.tracker_id] = raid

        with pytest.raises(ValueError, match="not in REVIEW"):
            await engine.evaluate(raid.tracker_id, OWNER_ID)

    @pytest.mark.asyncio
    async def test_changed_files_failure_does_not_block(self) -> None:
        """If fetching changed files fails, review should still proceed."""
        engine, repo, git, _, _ = _make_engine()
        raid = _make_raid(confidence=0.5)
        repo.raids[raid.tracker_id] = raid
        repo.saga = _make_saga()
        repo.phase = _make_phase()

        _setup_passing_pr(git, raid.pr_id)
        git.fail_changed_files = True

        result = await engine.evaluate(raid.tracker_id, OWNER_ID)

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
        """ReviewEngine should evaluate a raid when it sees a REVIEW state_changed event."""
        import asyncio

        from tyr.ports.event_bus import TyrEvent

        engine, repo, git, bus, _ = _make_engine()
        raid = _make_raid(confidence=0.5)
        repo.raids[raid.tracker_id] = raid
        repo.saga = _make_saga()
        repo.phase = _make_phase()

        _setup_passing_pr(git, raid.pr_id)
        git.changed_files[raid.pr_id] = ["src/main.py"]

        await engine.start()
        await asyncio.sleep(0)  # yield so listener task subscribes

        # Emit a raid.state_changed event with status=REVIEW
        await bus.emit(
            TyrEvent(
                event="raid.state_changed",
                owner_id=OWNER_ID,
                data={
                    "raid_id": str(raid.id),
                    "status": RaidStatus.REVIEW.value,
                    "confidence": raid.confidence,
                    "action": "completed",
                    "tracker_id": raid.tracker_id,
                },
            )
        )

        # Give the listener task time to process
        await asyncio.sleep(0.1)
        await engine.stop()

        # The engine should have auto-approved the raid
        assert repo.raids[raid.tracker_id].status == RaidStatus.MERGED

    @pytest.mark.asyncio
    async def test_review_engine_ignores_non_review_events(self) -> None:
        """ReviewEngine should ignore state_changed events for non-REVIEW states."""
        import asyncio

        from tyr.ports.event_bus import TyrEvent

        engine, repo, git, bus, _ = _make_engine()
        raid = _make_raid(confidence=0.5, status=RaidStatus.RUNNING)
        repo.raids[raid.tracker_id] = raid

        await engine.start()
        await asyncio.sleep(0)

        await bus.emit(
            TyrEvent(
                event="raid.state_changed",
                owner_id=OWNER_ID,
                data={
                    "raid_id": str(raid.id),
                    "status": RaidStatus.RUNNING.value,
                    "confidence": raid.confidence,
                    "action": "started",
                    "tracker_id": raid.tracker_id,
                },
            )
        )

        await asyncio.sleep(0.1)
        await engine.stop()

        # Raid should still be RUNNING (not evaluated)
        assert repo.raids[raid.tracker_id].status == RaidStatus.RUNNING

    @pytest.mark.asyncio
    async def test_review_engine_handles_evaluation_error(self) -> None:
        """ReviewEngine should log and continue if evaluation fails."""
        import asyncio

        from tyr.ports.event_bus import TyrEvent

        engine, repo, git, bus, _ = _make_engine()
        # No raid in repo — will cause ValueError("Raid not found")

        await engine.start()
        await asyncio.sleep(0)

        await bus.emit(
            TyrEvent(
                event="raid.state_changed",
                owner_id=OWNER_ID,
                data={
                    "raid_id": str(uuid4()),
                    "status": RaidStatus.REVIEW.value,
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
        raid = _make_raid(confidence=0.5)
        repo.raids[raid.tracker_id] = raid
        repo.saga = _make_saga()
        repo.phase = _make_phase()

        _setup_passing_pr(git, raid.pr_id)
        git.changed_files[raid.pr_id] = ["src/main.py"]

        await engine.evaluate(raid.tracker_id, OWNER_ID)

        events = repo.events[raid.tracker_id]
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
        raid = _make_raid(retry_count=0)
        repo.raids[raid.tracker_id] = raid

        _setup_failing_pr(git, raid.pr_id)

        result = await engine.evaluate(raid.tracker_id, OWNER_ID)

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
        raid = _make_raid(retry_count=0)
        repo.raids[raid.tracker_id] = raid

        _setup_conflicted_pr(git, raid.pr_id)

        result = await engine.evaluate(raid.tracker_id, OWNER_ID)

        assert result.action == "retried"
        assert len(volundr.messages) == 1
        assert "PR conflicts" in volundr.messages[0][1]

    @pytest.mark.asyncio
    async def test_retry_without_volundr_does_not_fail(self) -> None:
        """Auto-retry without VolundrPort should still work."""
        engine, repo, git, _, _ = _make_engine()  # no volundr
        raid = _make_raid(retry_count=0)
        repo.raids[raid.tracker_id] = raid

        _setup_failing_pr(git, raid.pr_id)

        result = await engine.evaluate(raid.tracker_id, OWNER_ID)

        assert result.action == "retried"

    @pytest.mark.asyncio
    async def test_retry_send_failure_does_not_block(self) -> None:
        """If sending the message fails, retry should still proceed."""
        volundr = StubVolundr()
        volundr.fail_send = True
        engine, repo, git, _, _ = _make_engine(volundr=volundr)
        raid = _make_raid(retry_count=0)
        repo.raids[raid.tracker_id] = raid

        _setup_failing_pr(git, raid.pr_id)

        result = await engine.evaluate(raid.tracker_id, OWNER_ID)

        assert result.action == "retried"
        assert repo.raids[raid.tracker_id].status == RaidStatus.PENDING

    @pytest.mark.asyncio
    async def test_retry_no_session_id_skips_message(self) -> None:
        """If the raid has no session_id, skip sending the message."""
        volundr = StubVolundr()
        engine, repo, git, _, _ = _make_engine(volundr=volundr)
        raid = _make_raid(retry_count=0)
        # Clear session_id
        raid = Raid(
            id=raid.id,
            phase_id=raid.phase_id,
            tracker_id=raid.tracker_id,
            name=raid.name,
            description=raid.description,
            acceptance_criteria=raid.acceptance_criteria,
            declared_files=raid.declared_files,
            estimate_hours=raid.estimate_hours,
            status=raid.status,
            confidence=raid.confidence,
            session_id=None,
            branch=raid.branch,
            chronicle_summary=raid.chronicle_summary,
            pr_url=raid.pr_url,
            pr_id=raid.pr_id,
            retry_count=raid.retry_count,
            created_at=raid.created_at,
            updated_at=raid.updated_at,
        )
        repo.raids[raid.tracker_id] = raid

        _setup_failing_pr(git, raid.pr_id)

        result = await engine.evaluate(raid.tracker_id, OWNER_ID)

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
        raid = _make_raid(confidence=0.5)
        repo.raids[raid.tracker_id] = raid
        repo.saga = _make_saga()
        repo.phase = _make_phase()

        _setup_passing_pr(git, raid.pr_id)
        git.changed_files[raid.pr_id] = ["src/main.py", "tests/test_main.py"]

        await engine.evaluate(raid.tracker_id, OWNER_ID)

        assert raid.session_id in volundr.stopped_sessions

    @pytest.mark.asyncio
    async def test_ci_failure_exhausted_stops_working_session(self) -> None:
        """FAILED (CI retries exhausted) should stop the working session."""
        volundr = StubVolundr()
        config = _default_config(max_retries=3)
        engine, repo, git, _, _ = _make_engine(config=config, volundr=volundr)
        raid = _make_raid(retry_count=3)
        repo.raids[raid.tracker_id] = raid

        _setup_failing_pr(git, raid.pr_id)

        await engine.evaluate(raid.tracker_id, OWNER_ID)

        assert raid.session_id in volundr.stopped_sessions

    @pytest.mark.asyncio
    async def test_conflict_exhausted_stops_working_session(self) -> None:
        """FAILED (conflict retries exhausted) should stop the working session."""
        volundr = StubVolundr()
        config = _default_config(max_retries=2)
        engine, repo, git, _, _ = _make_engine(config=config, volundr=volundr)
        raid = _make_raid(retry_count=2)
        repo.raids[raid.tracker_id] = raid

        _setup_conflicted_pr(git, raid.pr_id)

        await engine.evaluate(raid.tracker_id, OWNER_ID)

        assert raid.session_id in volundr.stopped_sessions

    @pytest.mark.asyncio
    async def test_escalated_does_not_stop_working_session(self) -> None:
        """ESCALATED should NOT stop the working session."""
        volundr = StubVolundr()
        config = _default_config(auto_approve_threshold=0.95)
        engine, repo, git, _, _ = _make_engine(config=config, volundr=volundr)
        raid = _make_raid(confidence=0.5)
        repo.raids[raid.tracker_id] = raid

        _setup_passing_pr(git, raid.pr_id)
        git.changed_files[raid.pr_id] = ["src/main.py"]

        await engine.evaluate(raid.tracker_id, OWNER_ID)

        assert raid.session_id not in volundr.stopped_sessions

    @pytest.mark.asyncio
    async def test_merged_attaches_working_transcript(self) -> None:
        """MERGED should attach the working session transcript to the tracker."""
        volundr = StubVolundr()
        engine, repo, git, _, _ = _make_engine(volundr=volundr)
        raid = _make_raid(confidence=0.5)
        repo.raids[raid.tracker_id] = raid
        repo.saga = _make_saga()
        repo.phase = _make_phase()

        _setup_passing_pr(git, raid.pr_id)
        git.changed_files[raid.pr_id] = ["src/main.py", "tests/test_main.py"]

        await engine.evaluate(raid.tracker_id, OWNER_ID)

        assert any("Working Session Transcript" in title for _, title, _ in repo.attached_documents)

    @pytest.mark.asyncio
    async def test_failed_attaches_working_transcript(self) -> None:
        """FAILED should attach the working session transcript to the tracker."""
        volundr = StubVolundr()
        config = _default_config(max_retries=3)
        engine, repo, git, _, _ = _make_engine(config=config, volundr=volundr)
        raid = _make_raid(retry_count=3)
        repo.raids[raid.tracker_id] = raid

        _setup_failing_pr(git, raid.pr_id)

        await engine.evaluate(raid.tracker_id, OWNER_ID)

        assert any("Working Session Transcript" in title for _, title, _ in repo.attached_documents)

    @pytest.mark.asyncio
    async def test_escalated_attaches_working_transcript(self) -> None:
        """ESCALATED should attach the working session transcript as a snapshot."""
        volundr = StubVolundr()
        config = _default_config(auto_approve_threshold=0.95)
        engine, repo, git, _, _ = _make_engine(config=config, volundr=volundr)
        raid = _make_raid(confidence=0.5)
        repo.raids[raid.tracker_id] = raid

        _setup_passing_pr(git, raid.pr_id)
        git.changed_files[raid.pr_id] = ["src/main.py"]

        result = await engine.evaluate(raid.tracker_id, OWNER_ID)

        assert result.action == "escalated"
        assert any("Working Session Transcript" in title for _, title, _ in repo.attached_documents)
        # Session should NOT be stopped for escalation
        assert raid.session_id not in volundr.stopped_sessions


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

        from tyr.adapters.ravn_dispatcher import RavnDispatcher

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

                raid = _make_raid(confidence=0.9)
                tracker.raids[raid.tracker_id] = raid
                tracker.saga = _make_saga()
                tracker.phase = _make_phase()
                git.pr_statuses[raid.pr_id] = PRStatus(
                    pr_id=raid.pr_id,
                    url="https://github.com/org/repo/pull/42",
                    state="open",
                    mergeable=True,
                    ci_passed=True,
                )
                git.changed_files[raid.pr_id] = list(raid.declared_files)

                await engine.evaluate(raid.tracker_id, "owner-llm-test")
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

        from tyr.adapters.ravn_dispatcher import RavnDispatcher

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
                    raid = _make_raid(confidence=0.5)
                    tracker.raids[raid.tracker_id] = raid
                    git.pr_statuses[raid.pr_id] = PRStatus(
                        pr_id=raid.pr_id,
                        url="https://github.com/org/repo/pull/42",
                        state="open",
                        mergeable=True,
                        ci_passed=True,
                    )
                    await engine.evaluate(raid.tracker_id, "owner-diff-model")
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
    async def test_reviewer_failure_marks_raid_failed(self) -> None:
        """When a tracked reviewer dies, the raid transitions to FAILED."""
        engine, tracker, _git, _ev, _vol = _make_engine()
        reviewer_id = "reviewer-session-xyz"
        raid = _make_raid(
            tracker_id="NIU-777",
            status=RaidStatus.REVIEW,
            reviewer_session_id=reviewer_id,
        )
        tracker.raids[raid.tracker_id] = raid
        engine._reviewer_sessions[reviewer_id] = (raid.tracker_id, "dev-user")

        await engine.handle_reviewer_failure(reviewer_id, reason="Max concurrent")

        updated = tracker.raids[raid.tracker_id]
        assert updated.status == RaidStatus.FAILED
        assert reviewer_id not in engine._reviewer_sessions

    @pytest.mark.asyncio
    async def test_reviewer_failure_for_untracked_session_is_noop(self) -> None:
        """Failures for unknown sessions are silently ignored."""
        engine, _t, _g, _e, _v = _make_engine()
        # Should not raise even though no mapping exists.
        await engine.handle_reviewer_failure("nope", reason="something")
