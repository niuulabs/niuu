"""Tests for auto-continue logic in DispatchService and ReviewEngine.

Verifies that newly unblocked issues are dispatched after a run merges
or a phase gate is unlocked, respecting the auto_continue toggle and
max_concurrent_runs limit.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ting.domain.models import (
    DispatcherState,
    PhaseStatus,
    Run,
    RunStatus,
    Saga,
    SagaStatus,
    TrackerIssue,
    TrackerMilestone,
    TrackerProject,
)
from ting.domain.services.dispatch_service import (
    DispatchConfig,
    DispatchService,
)

from ..test_dispatch_api import (
    MockDispatcherRepo,
    MockTrackerFactory,
    MockVolundr,
    MockVolundrFactory,
)
from ..test_tracker_api import MockSagaRepo, MockTracker

NOW = datetime.now(UTC)
OWNER = "owner-1"
SAGA_TRACKER_ID = "proj-1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class ConfigurableDispatcherRepo(MockDispatcherRepo):
    """DispatcherRepo whose get_or_create returns configurable state."""

    def __init__(
        self,
        *,
        running: bool = True,
        auto_continue: bool = True,
        max_concurrent_runs: int = 3,
    ) -> None:
        self._running = running
        self._auto_continue = auto_continue
        self._max_concurrent = max_concurrent_runs

    async def get_or_create(self, owner_id: str) -> DispatcherState:
        return DispatcherState(
            id=uuid4(),
            owner_id=owner_id,
            running=self._running,
            threshold=0.5,
            max_concurrent_runs=self._max_concurrent,
            auto_continue=self._auto_continue,
            updated_at=NOW,
        )


class RunAwareTracker(MockTracker):
    """MockTracker that supports list_runs_by_status with configurable runs."""

    def __init__(self) -> None:
        super().__init__()
        self._runs: list[Run] = []

    async def list_runs_by_status(self, status: RunStatus) -> list[Run]:
        return [r for r in self._runs if r.status == status]


def _make_saga() -> Saga:
    return Saga(
        id=uuid4(),
        tracker_id=SAGA_TRACKER_ID,
        tracker_type="linear",
        slug="alpha",
        name="Alpha",
        repos=["org/repo-a"],
        feature_branch="feat/alpha",
        status=SagaStatus.ACTIVE,
        confidence=0.0,
        created_at=NOW,
        base_branch="dev",
    )


def _make_run(status: RunStatus = RunStatus.RUNNING) -> Run:
    return Run(
        id=uuid4(),
        phase_id=uuid4(),
        tracker_id=f"run-{uuid4().hex[:6]}",
        name="Test Run",
        description="",
        acceptance_criteria=[],
        declared_files=[],
        estimate_hours=1.0,
        status=status,
        confidence=0.5,
        session_id="ses-1",
        branch="run/test",
        chronicle_summary=None,
        pr_url=None,
        pr_id=None,
        retry_count=0,
        created_at=NOW,
        updated_at=NOW,
    )


def _make_tracker_with_issues() -> RunAwareTracker:
    tracker = RunAwareTracker()
    tracker.projects = [
        TrackerProject(
            id="proj-1",
            name="Alpha",
            description="",
            status="started",
            url="",
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
                description="",
                sort_order=1,
                progress=0.0,
            ),
        ],
    }
    tracker.issues = {
        "proj-1": [
            TrackerIssue(
                id="i-1",
                identifier="ALPHA-1",
                title="Task A",
                description="Do A",
                status="Todo",
                priority=1,
                priority_label="Urgent",
                url="",
                milestone_id="ms-1",
            ),
            TrackerIssue(
                id="i-2",
                identifier="ALPHA-2",
                title="Task B",
                description="Do B",
                status="Todo",
                priority=2,
                priority_label="High",
                url="",
                milestone_id="ms-1",
            ),
        ],
    }
    return tracker


def _build_service(
    tracker: MockTracker | None = None,
    volundr: MockVolundr | None = None,
    dispatcher_repo: MockDispatcherRepo | None = None,
    saga_repo: MockSagaRepo | None = None,
) -> tuple[DispatchService, MockVolundr, RunAwareTracker]:
    t = tracker or _make_tracker_with_issues()
    v = volundr or MockVolundr()
    repo = saga_repo or MockSagaRepo()
    if not repo.sagas:
        repo.sagas.append(_make_saga())
    d_repo = dispatcher_repo or ConfigurableDispatcherRepo()

    svc = DispatchService(
        tracker_factory=MockTrackerFactory([t]),
        volundr_factory=MockVolundrFactory(adapters=[v]),
        saga_repo=repo,
        dispatcher_repo=d_repo,
        config=DispatchConfig(default_system_prompt="Be helpful."),
    )
    return svc, v, t


# ---------------------------------------------------------------------------
# Tests: try_auto_continue on DispatchService
# ---------------------------------------------------------------------------


class TestTryAutoContinue:
    @pytest.mark.asyncio
    async def test_dispatches_when_enabled(self):
        """auto_continue=True and running=True should dispatch ready issues."""
        svc, volundr, _ = _build_service()
        results = await svc.try_auto_continue(OWNER, SAGA_TRACKER_ID)
        assert len(results) > 0
        assert all(r.status == "spawned" for r in results)
        assert len(volundr.spawned) > 0

    @pytest.mark.asyncio
    async def test_noop_when_auto_continue_disabled(self):
        """auto_continue=False should return empty list without dispatching."""
        repo = ConfigurableDispatcherRepo(auto_continue=False)
        svc, volundr, _ = _build_service(dispatcher_repo=repo)
        results = await svc.try_auto_continue(OWNER, SAGA_TRACKER_ID)
        assert results == []
        assert len(volundr.spawned) == 0

    @pytest.mark.asyncio
    async def test_noop_when_not_running(self):
        """running=False should return empty list."""
        repo = ConfigurableDispatcherRepo(running=False)
        svc, volundr, _ = _build_service(dispatcher_repo=repo)
        results = await svc.try_auto_continue(OWNER, SAGA_TRACKER_ID)
        assert results == []
        assert len(volundr.spawned) == 0

    @pytest.mark.asyncio
    async def test_respects_max_concurrent_runs(self):
        """Should not dispatch more issues than available slots."""
        tracker = _make_tracker_with_issues()
        # 2 already running, max=3 → only 1 slot available
        tracker._runs = [_make_run(RunStatus.RUNNING), _make_run(RunStatus.RUNNING)]
        repo = ConfigurableDispatcherRepo(max_concurrent_runs=3)

        svc, volundr, _ = _build_service(tracker=tracker, dispatcher_repo=repo)
        results = await svc.try_auto_continue(OWNER, SAGA_TRACKER_ID)
        assert len(results) == 1
        assert len(volundr.spawned) == 1

    @pytest.mark.asyncio
    async def test_no_slots_returns_empty(self):
        """When max_concurrent_runs already reached, return empty."""
        tracker = _make_tracker_with_issues()
        tracker._runs = [
            _make_run(RunStatus.RUNNING),
            _make_run(RunStatus.RUNNING),
            _make_run(RunStatus.RUNNING),
        ]
        repo = ConfigurableDispatcherRepo(max_concurrent_runs=3)

        svc, volundr, _ = _build_service(tracker=tracker, dispatcher_repo=repo)
        results = await svc.try_auto_continue(OWNER, SAGA_TRACKER_ID)
        assert results == []
        assert len(volundr.spawned) == 0

    @pytest.mark.asyncio
    async def test_no_ready_issues_returns_empty(self):
        """When no issues are ready, return empty."""
        tracker = RunAwareTracker()
        tracker.projects = [
            TrackerProject(
                id="proj-1",
                name="Alpha",
                description="",
                status="started",
                url="",
                milestone_count=0,
                issue_count=0,
            ),
        ]
        tracker.milestones = {"proj-1": []}
        tracker.issues = {"proj-1": []}

        svc, volundr, _ = _build_service(tracker=tracker)
        results = await svc.try_auto_continue(OWNER, SAGA_TRACKER_ID)
        assert results == []

    @pytest.mark.asyncio
    async def test_per_owner_lock_prevents_concurrent_dispatch(self):
        """Concurrent calls for the same owner should serialize via lock."""
        svc, volundr, _ = _build_service()

        call_count = 0
        original_find = svc.find_ready_issues

        async def counting_find(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            # Small delay to increase overlap window
            await asyncio.sleep(0.01)
            return await original_find(*args, **kwargs)

        svc.find_ready_issues = counting_find  # type: ignore[assignment]

        # Launch two concurrent auto-continue calls
        await asyncio.gather(
            svc.try_auto_continue(OWNER, SAGA_TRACKER_ID),
            svc.try_auto_continue(OWNER, SAGA_TRACKER_ID),
        )
        # Both calls should complete (serialized), not crash
        assert call_count == 2
        # The lock key should exist for the owner
        assert OWNER in svc._locks


# ---------------------------------------------------------------------------
# Tests: ReviewEngine integration with auto-continue
# ---------------------------------------------------------------------------


class TestReviewEngineAutoContinue:
    """Verify ReviewEngine calls try_auto_continue after merge and phase unlock."""

    @pytest.mark.asyncio
    async def test_auto_approve_triggers_auto_continue(self):
        """After _handle_auto_approve, try_auto_continue should be called."""
        from ting.adapters.memory_event_bus import InMemoryEventBus
        from ting.config import ReviewConfig
        from ting.domain.services.review_engine import ReviewEngine

        from ..test_review_engine import (
            StubTracker,
            StubTrackerFactory,
            StubVolundr,
            _authoritative_approval,
            _make_run,
            _make_saga,
        )

        tracker = StubTracker()
        bus = InMemoryEventBus()
        volundr = StubVolundr()
        saga = _make_saga()

        run = _make_run(confidence=0.9)
        tracker.runs[run.tracker_id] = run
        tracker.saga = saga

        # Track auto-continue calls
        auto_continue_calls: list[tuple[str, str]] = []

        class TrackingDispatchService:
            async def try_auto_continue(self, owner_id: str, saga_tracker_id: str):
                auto_continue_calls.append((owner_id, saga_tracker_id))
                return []

        class _StubVolundrFactory:
            async def for_owner(self, owner_id):
                return [volundr]

            async def primary_for_owner(self, owner_id):
                return volundr

        engine = ReviewEngine(
            tracker_factory=StubTrackerFactory(tracker),
            volundr_factory=_StubVolundrFactory(),
            review_config=ReviewConfig(),
            event_bus=bus,
            dispatch_service=TrackingDispatchService(),
        )

        decision = await engine.handle_ravn_outcome(
            run.tracker_id, "user-1", _authoritative_approval()
        )
        assert decision.action == "auto_approved"
        assert len(auto_continue_calls) == 1
        assert auto_continue_calls[0] == ("user-1", saga.tracker_id)

    @pytest.mark.asyncio
    async def test_phase_unlock_triggers_auto_continue(self):
        """When a phase gate is unlocked, try_auto_continue should be called."""
        from ting.adapters.memory_event_bus import InMemoryEventBus
        from ting.config import ReviewConfig
        from ting.domain.services.review_engine import ReviewEngine

        from ..test_review_engine import (
            PHASE_ID,
            StubTracker,
            StubTrackerFactory,
            StubVolundr,
            _authoritative_approval,
            _make_phase,
            _make_run,
            _make_saga,
        )

        tracker = StubTracker()
        bus = InMemoryEventBus()
        volundr = StubVolundr()
        saga = _make_saga()

        # Setup phase gate: phase 1 is current, phase 2 is gated
        phase1 = _make_phase(phase_id=PHASE_ID, number=1, status=PhaseStatus.ACTIVE)
        phase2 = _make_phase(phase_id=uuid4(), number=2, status=PhaseStatus.GATED)
        tracker.saga = saga
        tracker.phase = phase1
        tracker.phases = [phase1, phase2]
        tracker._all_merged = True  # All runs in phase merged

        run = _make_run(confidence=0.9)
        tracker.runs[run.tracker_id] = run

        auto_continue_calls: list[tuple[str, str]] = []

        class TrackingDispatchService:
            async def try_auto_continue(self, owner_id: str, saga_tracker_id: str):
                auto_continue_calls.append((owner_id, saga_tracker_id))
                return []

        class _StubVolundrFactory:
            async def for_owner(self, owner_id):
                return [volundr]

            async def primary_for_owner(self, owner_id):
                return volundr

        engine = ReviewEngine(
            tracker_factory=StubTrackerFactory(tracker),
            volundr_factory=_StubVolundrFactory(),
            review_config=ReviewConfig(),
            event_bus=bus,
            dispatch_service=TrackingDispatchService(),
        )

        decision = await engine.handle_ravn_outcome(
            run.tracker_id, "user-1", _authoritative_approval()
        )
        assert decision.action == "auto_approved"
        assert decision.phase_gate_unlocked is True
        # Called twice: once from phase unlock, once from merge in _handle_auto_approve
        assert len(auto_continue_calls) == 2

    @pytest.mark.asyncio
    async def test_no_dispatch_service_is_safe(self):
        """ReviewEngine without dispatch_service should work fine (no-op)."""
        from ting.config import ReviewConfig
        from ting.domain.services.review_engine import ReviewEngine

        from ..test_review_engine import (
            StubTracker,
            StubTrackerFactory,
            StubVolundr,
            _authoritative_approval,
            _make_run,
            _make_saga,
        )

        tracker = StubTracker()
        volundr = StubVolundr()
        saga = _make_saga()

        run = _make_run(confidence=0.9)
        tracker.runs[run.tracker_id] = run
        tracker.saga = saga

        class _StubVolundrFactory:
            async def for_owner(self, owner_id):
                return [volundr]

            async def primary_for_owner(self, owner_id):
                return volundr

        engine = ReviewEngine(
            tracker_factory=StubTrackerFactory(tracker),
            volundr_factory=_StubVolundrFactory(),
            review_config=ReviewConfig(),
            # No dispatch_service passed
        )

        decision = await engine.handle_ravn_outcome(
            run.tracker_id, "user-1", _authoritative_approval()
        )
        assert decision.action == "auto_approved"

    @pytest.mark.asyncio
    async def test_dispatch_service_error_does_not_break_merge(self):
        """If try_auto_continue raises, the merge should still succeed."""
        from ting.adapters.memory_event_bus import InMemoryEventBus
        from ting.config import ReviewConfig
        from ting.domain.services.review_engine import ReviewEngine

        from ..test_review_engine import (
            StubTracker,
            StubTrackerFactory,
            StubVolundr,
            _authoritative_approval,
            _make_run,
            _make_saga,
        )

        tracker = StubTracker()
        volundr = StubVolundr()
        saga = _make_saga()

        run = _make_run(confidence=0.9)
        tracker.runs[run.tracker_id] = run
        tracker.saga = saga

        class FailingDispatchService:
            async def try_auto_continue(self, owner_id: str, saga_tracker_id: str):
                raise RuntimeError("dispatch blew up")

        class _StubVolundrFactory:
            async def for_owner(self, owner_id):
                return [volundr]

            async def primary_for_owner(self, owner_id):
                return volundr

        engine = ReviewEngine(
            tracker_factory=StubTrackerFactory(tracker),
            volundr_factory=_StubVolundrFactory(),
            review_config=ReviewConfig(),
            event_bus=InMemoryEventBus(),
            dispatch_service=FailingDispatchService(),
        )

        # Should not raise
        decision = await engine.handle_ravn_outcome(
            run.tracker_id, "user-1", _authoritative_approval()
        )
        assert decision.action == "auto_approved"
