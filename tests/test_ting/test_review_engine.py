"""Tests for the review engine's outcome projection and escalation paths.

The old confidence-scoring pipeline (CI deltas, scope-breach ratios, reviewer
sessions, arbiter dispatch) is gone: workflows carry the machine verdict, and
anything without one goes to a human. These tests pin that contract.
"""

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
    Phase,
    PhaseStatus,
    PRStatus,
    RavnOutcome,
    Run,
    RunStatus,
    Saga,
    SagaStatus,
    SessionMessage,
    TrackerIssue,
    TrackerMilestone,
    TrackerProject,
)
from ting.domain.services.review_engine import ReviewEngine
from ting.ports.event_bus import TingEvent
from ting.ports.tracker import TrackerPort
from ting.ports.volundr import ActivityEvent, SpawnRequest, VolundrPort, VolundrSession

NOW = datetime.now(UTC)

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


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
        self.closed_runs: list[str] = []

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
        self.closed_runs.append(run_id)

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
            confidence=confidence if confidence is not None else run.confidence,
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
        return {"turns": [{"role": "assistant", "content": "done"}]}

    async def get_last_assistant_message(self, session_id: str) -> str:
        return "done"

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


def _make_engine(
    tracker: StubTracker | None = None,
    config: ReviewConfig | None = None,
    event_bus: InMemoryEventBus | None = None,
    volundr: StubVolundr | None = None,
    saga_repo: InMemorySagaRepository | None = None,
) -> tuple[ReviewEngine, StubTracker, InMemoryEventBus, StubVolundr]:
    r = tracker or StubTracker()
    e = event_bus or InMemoryEventBus()
    c = config or ReviewConfig()
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
        review_config=c,
        event_bus=e,
        saga_repo=repo,
    )
    return engine, r, e, v


def _authoritative_approval(**overrides: object) -> RavnOutcome:
    defaults: dict = {
        "verdict": "approve",
        "tests_passing": True,
        "scope_adherence": 1.0,
        "pr_url": "https://github.com/org/repo/pull/42",
        "files_changed": ["src/main.py"],
        "summary": "done",
        "authoritative": True,
        "checks": [{"node": "verify", "verdict": "pass"}],
    }
    defaults.update(overrides)
    return RavnOutcome(**defaults)


async def _drain(q) -> list[TingEvent]:
    events = []
    while not q.empty():
        events.append(await q.get())
    return events


# ---------------------------------------------------------------------------
# Tests: evaluate() — a REVIEW run without a workflow verdict goes to a human
# ---------------------------------------------------------------------------


class TestEvaluateEscalates:
    @pytest.mark.asyncio
    async def test_review_run_escalates_to_human(self) -> None:
        engine, repo, bus, _ = _make_engine()
        run = _make_run()
        repo.runs[run.tracker_id] = run

        q = bus.subscribe()
        decision = await engine.evaluate(run.tracker_id, OWNER_ID)

        assert decision.action == "escalated"
        assert "human review" in decision.reason
        assert repo.runs[run.tracker_id].status == RunStatus.ESCALATED
        events = await _drain(q)
        assert [e.event for e in events] == ["run.state_changed"]
        assert events[0].data["action"] == "escalated"

    @pytest.mark.asyncio
    async def test_escalation_snapshots_transcript_but_keeps_session(self) -> None:
        engine, repo, _, volundr = _make_engine()
        run = _make_run()
        repo.runs[run.tracker_id] = run

        await engine.evaluate(run.tracker_id, OWNER_ID)

        # Transcript attached for the human reviewer...
        assert len(repo.attached_documents) == 1
        # ...but the session stays alive for them to interact with.
        assert volundr.stopped_sessions == []

    @pytest.mark.asyncio
    async def test_run_not_found(self) -> None:
        engine, _, _, _ = _make_engine()
        with pytest.raises(ValueError, match="Run not found"):
            await engine.evaluate("NIU-NONEXISTENT", OWNER_ID)

    @pytest.mark.asyncio
    async def test_wrong_state(self) -> None:
        engine, repo, _, _ = _make_engine()
        run = _make_run(status=RunStatus.RUNNING)
        repo.runs[run.tracker_id] = run

        with pytest.raises(ValueError, match="not in REVIEW"):
            await engine.evaluate(run.tracker_id, OWNER_ID)


# ---------------------------------------------------------------------------
# Tests: handle_ravn_outcome — the workflow verdict is the machine verdict
# ---------------------------------------------------------------------------


class TestHandleRavnOutcome:
    @pytest.mark.asyncio
    async def test_authoritative_approval_merges(self) -> None:
        engine, repo, bus, volundr = _make_engine()
        run = _make_run()
        repo.runs[run.tracker_id] = run
        repo.saga = _make_saga()

        q = bus.subscribe()
        decision = await engine.handle_ravn_outcome(
            run.tracker_id, OWNER_ID, _authoritative_approval()
        )

        assert decision.action == "auto_approved"
        assert repo.runs[run.tracker_id].status == RunStatus.MERGED
        assert repo.closed_runs == [run.tracker_id]
        assert volundr.stopped_sessions == ["session-1"]
        events = await _drain(q)
        state_events = [e for e in events if e.event == "run.state_changed"]
        assert state_events[0].data["action"] == "auto_approved"

    @pytest.mark.asyncio
    async def test_running_run_transitions_through_review(self) -> None:
        engine, repo, _, _ = _make_engine()
        run = _make_run(status=RunStatus.RUNNING)
        repo.runs[run.tracker_id] = run
        repo.saga = _make_saga()

        decision = await engine.handle_ravn_outcome(
            run.tracker_id, OWNER_ID, _authoritative_approval()
        )

        assert decision.action == "auto_approved"
        assert repo.runs[run.tracker_id].status == RunStatus.MERGED

    @pytest.mark.asyncio
    async def test_non_authoritative_approval_escalates(self) -> None:
        """An approve without workflow authority is an opinion, not a verdict."""
        engine, repo, _, _ = _make_engine()
        run = _make_run()
        repo.runs[run.tracker_id] = run

        decision = await engine.handle_ravn_outcome(
            run.tracker_id,
            OWNER_ID,
            _authoritative_approval(authoritative=False),
        )

        assert decision.action == "escalated"
        assert repo.runs[run.tracker_id].status == RunStatus.ESCALATED

    @pytest.mark.asyncio
    async def test_approval_with_failing_check_escalates(self) -> None:
        engine, repo, _, _ = _make_engine()
        run = _make_run()
        repo.runs[run.tracker_id] = run

        decision = await engine.handle_ravn_outcome(
            run.tracker_id,
            OWNER_ID,
            _authoritative_approval(checks=[{"node": "verify", "verdict": "fail"}]),
        )

        assert decision.action == "escalated"

    @pytest.mark.asyncio
    async def test_retry_verdict_redispatches_with_feedback(self) -> None:
        engine, repo, _, volundr = _make_engine()
        run = _make_run()
        repo.runs[run.tracker_id] = run

        decision = await engine.handle_ravn_outcome(
            run.tracker_id,
            OWNER_ID,
            _authoritative_approval(verdict="retry", authoritative=False, checks=[]),
        )

        assert decision.action == "retried"
        updated = repo.runs[run.tracker_id]
        assert updated.status == RunStatus.PENDING
        assert updated.retry_count == 1
        # Failure context reaches the session before the reset.
        assert len(volundr.messages) == 1
        assert volundr.messages[0][0] == "session-1"

    @pytest.mark.asyncio
    async def test_retry_verdict_with_retries_exhausted_fails(self) -> None:
        engine, repo, _, volundr = _make_engine(config=ReviewConfig(max_retries=1))
        run = _make_run(retry_count=1)
        repo.runs[run.tracker_id] = run

        decision = await engine.handle_ravn_outcome(
            run.tracker_id,
            OWNER_ID,
            _authoritative_approval(verdict="retry", authoritative=False, checks=[]),
        )

        assert decision.action == "failed"
        assert repo.runs[run.tracker_id].status == RunStatus.FAILED
        assert volundr.stopped_sessions == ["session-1"]

    @pytest.mark.asyncio
    async def test_escalate_verdict_escalates(self) -> None:
        engine, repo, _, _ = _make_engine()
        run = _make_run()
        repo.runs[run.tracker_id] = run

        decision = await engine.handle_ravn_outcome(
            run.tracker_id,
            OWNER_ID,
            _authoritative_approval(verdict="escalate", authoritative=False, checks=[]),
        )

        assert decision.action == "escalated"

    @pytest.mark.asyncio
    async def test_unknown_verdict_escalates(self) -> None:
        engine, repo, _, _ = _make_engine()
        run = _make_run()
        repo.runs[run.tracker_id] = run

        decision = await engine.handle_ravn_outcome(
            run.tracker_id,
            OWNER_ID,
            _authoritative_approval(verdict="celebrate", authoritative=False, checks=[]),
        )

        assert decision.action == "escalated"
        assert "celebrate" in decision.reason

    @pytest.mark.asyncio
    async def test_terminal_run_is_skipped(self) -> None:
        engine, repo, _, _ = _make_engine()
        run = _make_run(status=RunStatus.MERGED)
        repo.runs[run.tracker_id] = run

        decision = await engine.handle_ravn_outcome(
            run.tracker_id, OWNER_ID, _authoritative_approval()
        )

        assert decision.action == "skipped"


# ---------------------------------------------------------------------------
# Tests: event-driven listening
# ---------------------------------------------------------------------------


class TestEventDrivenReview:
    @pytest.mark.asyncio
    async def test_review_event_triggers_escalation(self) -> None:
        import asyncio

        engine, repo, bus, _ = _make_engine()
        run = _make_run()
        repo.runs[run.tracker_id] = run

        await engine.start()
        assert engine.running is True
        await asyncio.sleep(0)  # let the listener task subscribe before emitting
        try:
            await bus.emit(
                TingEvent(
                    event="run.state_changed",
                    owner_id=OWNER_ID,
                    data={"status": RunStatus.REVIEW.value, "tracker_id": run.tracker_id},
                )
            )
            for _ in range(50):
                await asyncio.sleep(0.01)
                if repo.runs[run.tracker_id].status == RunStatus.ESCALATED:
                    break
        finally:
            await engine.stop()

        assert repo.runs[run.tracker_id].status == RunStatus.ESCALATED
        assert engine.running is False

    @pytest.mark.asyncio
    async def test_non_review_transition_resets_processed_guard(self) -> None:
        engine, _, _, _ = _make_engine()
        engine._processed.add("NIU-1")

        # Simulate the guard-reset branch directly: a MERGED transition for a
        # previously processed run clears it for the next cycle.
        # (The full loop path is covered by test_review_event_triggers_escalation.)
        import asyncio

        bus = engine._event_bus
        await engine.start()
        await asyncio.sleep(0)  # let the listener task subscribe before emitting
        try:
            await bus.emit(
                TingEvent(
                    event="run.state_changed",
                    owner_id=OWNER_ID,
                    data={"status": RunStatus.MERGED.value, "tracker_id": "NIU-1"},
                )
            )
            for _ in range(50):
                await asyncio.sleep(0.01)
                if "NIU-1" not in engine._processed:
                    break
        finally:
            await engine.stop()

        assert "NIU-1" not in engine._processed


# ---------------------------------------------------------------------------
# Tests: phase gate + projections (driven via authoritative approval)
# ---------------------------------------------------------------------------


class TestPhaseGate:
    @pytest.mark.asyncio
    async def test_phase_gate_unlocked(self) -> None:
        """When all runs in a phase are merged, the next phase is unlocked."""
        engine, repo, bus, _ = _make_engine()
        saga_repo = engine._saga_repo
        assert isinstance(saga_repo, InMemorySagaRepository)
        run = _make_run()
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

        q = bus.subscribe()
        result = await engine.handle_ravn_outcome(
            run.tracker_id, OWNER_ID, _authoritative_approval()
        )

        assert result.phase_gate_unlocked is True

        # Verify next phase was unlocked by tracker_id (string)
        assert len(repo.phase_status_updates) == 1
        assert repo.phase_status_updates[0] == (phase2.tracker_id, PhaseStatus.ACTIVE)

        # Verify phase.unlocked event emitted
        events = await _drain(q)
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
        engine, repo, _, _ = _make_engine()
        run = _make_run()
        repo.runs[run.tracker_id] = run
        repo.saga = _make_saga()
        repo.phase = _make_phase()
        repo._all_merged = False

        result = await engine.handle_ravn_outcome(
            run.tracker_id, OWNER_ID, _authoritative_approval()
        )

        assert result.phase_gate_unlocked is False
        assert len(repo.phase_status_updates) == 0

    @pytest.mark.asyncio
    async def test_no_next_phase(self) -> None:
        """Phase gate unlocked but no next phase — should return True without error."""
        engine, repo, _, _ = _make_engine()
        saga_repo = engine._saga_repo
        assert isinstance(saga_repo, InMemorySagaRepository)
        run = _make_run()
        repo.runs[run.tracker_id] = run
        repo.saga = _make_saga()
        repo._all_merged = True
        repo.phase = _make_phase()
        repo.phases = [_make_phase()]  # Only one phase
        await saga_repo.save_saga(repo.saga)
        await saga_repo.save_phase(repo.phase)

        result = await engine.handle_ravn_outcome(
            run.tracker_id, OWNER_ID, _authoritative_approval()
        )

        assert result.phase_gate_unlocked is True
        assert len(repo.phase_status_updates) == 0
        persisted_phase = await saga_repo.get_phase(repo.phase.id)
        persisted_saga = await saga_repo.get_saga(repo.saga.id)
        assert persisted_phase is not None and persisted_phase.status == PhaseStatus.COMPLETE
        assert persisted_saga is not None and persisted_saga.status == SagaStatus.COMPLETE


# ---------------------------------------------------------------------------
# Tests: workflow completion + run failure projections
# ---------------------------------------------------------------------------


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


@pytest.mark.asyncio
async def test_workflow_completion_projects_merge_and_auto_continue() -> None:
    engine, tracker, _, _ = _make_engine()
    engine._dispatch_service = type("DispatchServiceStub", (), {})()
    engine._dispatch_service.try_auto_continue = AsyncMock()
    tracker.saga = _make_saga()
    run = _make_run(status=RunStatus.RUNNING)
    tracker.runs[run.tracker_id] = run

    await engine.handle_workflow_completion(run.tracker_id, OWNER_ID)

    assert tracker.runs[run.tracker_id].status == RunStatus.MERGED
    engine._dispatch_service.try_auto_continue.assert_awaited_once_with(
        OWNER_ID,
        tracker.saga.tracker_id,
    )


@pytest.mark.asyncio
async def test_run_failure_triggers_auto_continue_for_saga() -> None:
    """Terminal run failures should refill any newly freed dispatch slot."""
    engine, tracker, _, _ = _make_engine()
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


# ---------------------------------------------------------------------------
# Tests: config survivors
# ---------------------------------------------------------------------------


class TestReviewConfig:
    def test_defaults(self) -> None:
        cfg = ReviewConfig()
        assert cfg.max_retries == 3
        assert cfg.initial_confidence == 0.5

    def test_custom_config(self) -> None:
        cfg = ReviewConfig(max_retries=5)
        assert cfg.max_retries == 5
