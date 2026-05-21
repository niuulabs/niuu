"""Tests for Ting domain models."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ting.domain.exceptions import InvalidStateTransitionError
from ting.domain.models import (
    RUN_TRANSITIONS,
    ConfidenceEvent,
    ConfidenceEventType,
    DispatcherState,
    Phase,
    PhaseSpec,
    PhaseStatus,
    PRStatus,
    Run,
    RunSpec,
    RunStatus,
    Saga,
    SagaStatus,
    SagaStructure,
    SessionInfo,
    validate_transition,
)

# ---------------------------------------------------------------------------
# Enum values
# ---------------------------------------------------------------------------


class TestSagaStatus:
    def test_values(self) -> None:
        assert SagaStatus.ACTIVE == "ACTIVE"
        assert SagaStatus.COMPLETE == "COMPLETE"
        assert SagaStatus.FAILED == "FAILED"

    def test_member_count(self) -> None:
        assert len(SagaStatus) == 3


class TestPhaseStatus:
    def test_values(self) -> None:
        assert PhaseStatus.PENDING == "PENDING"
        assert PhaseStatus.ACTIVE == "ACTIVE"
        assert PhaseStatus.GATED == "GATED"
        assert PhaseStatus.COMPLETE == "COMPLETE"

    def test_member_count(self) -> None:
        assert len(PhaseStatus) == 4


class TestRunStatus:
    def test_values(self) -> None:
        assert RunStatus.PENDING == "PENDING"
        assert RunStatus.QUEUED == "QUEUED"
        assert RunStatus.RUNNING == "RUNNING"
        assert RunStatus.REVIEW == "REVIEW"
        assert RunStatus.ESCALATED == "ESCALATED"
        assert RunStatus.MERGED == "MERGED"
        assert RunStatus.FAILED == "FAILED"

    def test_member_count(self) -> None:
        assert len(RunStatus) == 7


class TestConfidenceEventType:
    def test_values(self) -> None:
        assert ConfidenceEventType.CI_PASS == "ci_pass"
        assert ConfidenceEventType.CI_FAIL == "ci_fail"
        assert ConfidenceEventType.SCOPE_BREACH == "scope_breach"
        assert ConfidenceEventType.RETRY == "retry"
        assert ConfidenceEventType.HUMAN_REJECT == "human_reject"
        assert ConfidenceEventType.HUMAN_APPROVED == "human_approved"
        assert ConfidenceEventType.AUTO_APPROVED == "auto_approved"
        assert ConfidenceEventType.PR_CONFLICT == "pr_conflict"
        assert ConfidenceEventType.PR_MERGEABLE == "pr_mergeable"
        assert ConfidenceEventType.MESSAGE_SENT == "message_sent"
        assert ConfidenceEventType.REVIEWER_SCORE == "reviewer_score"

    def test_member_count(self) -> None:
        assert len(ConfidenceEventType) == 11


# ---------------------------------------------------------------------------
# State machine transitions
# ---------------------------------------------------------------------------


class TestRunTransitions:
    def test_pending_to_queued(self) -> None:
        validate_transition(RunStatus.PENDING, RunStatus.QUEUED)

    def test_queued_to_running(self) -> None:
        validate_transition(RunStatus.QUEUED, RunStatus.RUNNING)

    def test_queued_to_failed(self) -> None:
        validate_transition(RunStatus.QUEUED, RunStatus.FAILED)

    def test_running_to_review(self) -> None:
        validate_transition(RunStatus.RUNNING, RunStatus.REVIEW)

    def test_running_to_merged(self) -> None:
        validate_transition(RunStatus.RUNNING, RunStatus.MERGED)

    def test_running_to_failed(self) -> None:
        validate_transition(RunStatus.RUNNING, RunStatus.FAILED)

    def test_review_to_queued(self) -> None:
        validate_transition(RunStatus.REVIEW, RunStatus.QUEUED)

    def test_review_to_merged(self) -> None:
        validate_transition(RunStatus.REVIEW, RunStatus.MERGED)

    def test_review_to_failed(self) -> None:
        validate_transition(RunStatus.REVIEW, RunStatus.FAILED)

    def test_review_to_escalated(self) -> None:
        validate_transition(RunStatus.REVIEW, RunStatus.ESCALATED)

    def test_escalated_to_queued(self) -> None:
        validate_transition(RunStatus.ESCALATED, RunStatus.QUEUED)

    def test_escalated_to_merged(self) -> None:
        validate_transition(RunStatus.ESCALATED, RunStatus.MERGED)

    def test_escalated_to_failed(self) -> None:
        validate_transition(RunStatus.ESCALATED, RunStatus.FAILED)

    def test_failed_to_queued(self) -> None:
        validate_transition(RunStatus.FAILED, RunStatus.QUEUED)

    def test_merged_is_terminal(self) -> None:
        for target in RunStatus:
            if target == RunStatus.MERGED:
                continue
            with pytest.raises(InvalidStateTransitionError):
                validate_transition(RunStatus.MERGED, target)

    def test_invalid_pending_to_running(self) -> None:
        with pytest.raises(InvalidStateTransitionError) as exc_info:
            validate_transition(RunStatus.PENDING, RunStatus.RUNNING)
        assert exc_info.value.current == RunStatus.PENDING
        assert exc_info.value.target == RunStatus.RUNNING

    def test_invalid_pending_to_merged(self) -> None:
        with pytest.raises(InvalidStateTransitionError):
            validate_transition(RunStatus.PENDING, RunStatus.MERGED)

    def test_transition_map_covers_all_statuses(self) -> None:
        for status in RunStatus:
            assert status in RUN_TRANSITIONS


# ---------------------------------------------------------------------------
# Dataclass creation
# ---------------------------------------------------------------------------

NOW = datetime.now(UTC)


class TestSaga:
    def test_create(self) -> None:
        saga = Saga(
            id=uuid4(),
            tracker_id="LIN-100",
            tracker_type="linear",
            slug="my-saga",
            name="My Saga",
            repos=["niuulabs/volundr"],
            feature_branch="feat/my-saga",
            status=SagaStatus.ACTIVE,
            confidence=0.9,
            created_at=NOW,
            base_branch="dev",
        )
        assert saga.tracker_id == "LIN-100"
        assert saga.name == "My Saga"
        assert saga.status == SagaStatus.ACTIVE
        assert saga.confidence == 0.9
        assert saga.repos == ["niuulabs/volundr"]
        assert saga.feature_branch == "feat/my-saga"

    def test_frozen(self) -> None:
        saga = Saga(
            id=uuid4(),
            tracker_id="LIN-100",
            tracker_type="linear",
            slug="s",
            name="S",
            repos=["r"],
            feature_branch="feat/test",
            status=SagaStatus.ACTIVE,
            confidence=0.5,
            created_at=NOW,
            base_branch="dev",
        )
        with pytest.raises(AttributeError):
            saga.slug = "changed"  # type: ignore[misc]


class TestPhase:
    def test_create(self) -> None:
        phase = Phase(
            id=uuid4(),
            saga_id=uuid4(),
            tracker_id="LIN-101",
            number=1,
            name="Phase 1",
            status=PhaseStatus.PENDING,
            confidence=0.8,
        )
        assert phase.number == 1
        assert phase.status == PhaseStatus.PENDING


class TestRun:
    def test_create(self) -> None:
        run = Run(
            id=uuid4(),
            phase_id=uuid4(),
            tracker_id="LIN-102",
            name="Run 1",
            description="Implement the feature",
            acceptance_criteria=["Tests pass", "Coverage > 85%"],
            declared_files=["src/foo.py"],
            estimate_hours=2.0,
            status=RunStatus.PENDING,
            confidence=0.75,
            session_id=None,
            branch=None,
            chronicle_summary=None,
            pr_url=None,
            pr_id=None,
            retry_count=0,
            created_at=NOW,
            updated_at=NOW,
        )
        assert run.description == "Implement the feature"
        assert len(run.acceptance_criteria) == 2
        assert run.declared_files == ["src/foo.py"]
        assert run.estimate_hours == 2.0
        assert run.session_id is None


class TestConfidenceEvent:
    def test_create(self) -> None:
        event = ConfidenceEvent(
            id=uuid4(),
            run_id=uuid4(),
            event_type=ConfidenceEventType.CI_PASS,
            delta=0.1,
            score_after=0.85,
            created_at=NOW,
        )
        assert event.event_type == ConfidenceEventType.CI_PASS
        assert event.delta == 0.1


class TestDispatcherState:
    def test_create(self) -> None:
        state = DispatcherState(
            id=uuid4(),
            owner_id="user-1",
            running=True,
            threshold=0.7,
            max_concurrent_runs=3,
            auto_continue=False,
            updated_at=NOW,
        )
        assert state.running is True
        assert state.threshold == 0.7
        assert state.owner_id == "user-1"
        assert state.max_concurrent_runs == 3
        assert state.auto_continue is False


class TestSessionInfo:
    def test_create(self) -> None:
        info = SessionInfo(session_id="sess-1", status="running")
        assert info.session_id == "sess-1"


class TestPRStatus:
    def test_create(self) -> None:
        pr = PRStatus(
            pr_id="PR-1",
            url="https://github.com/org/repo/pull/1",
            state="open",
            mergeable=True,
            ci_passed=None,
        )
        assert pr.url == "https://github.com/org/repo/pull/1"
        assert pr.mergeable is True
        assert pr.ci_passed is None


class TestSpecStructures:
    def test_run_spec(self) -> None:
        spec = RunSpec(
            name="Add models",
            description="Create domain models",
            acceptance_criteria=["Tests pass"],
            declared_files=["models.py"],
            estimate_hours=1.5,
            confidence=0.9,
        )
        assert spec.estimate_hours == 1.5
        assert spec.confidence == 0.9

    def test_phase_spec(self) -> None:
        run = RunSpec(
            name="r1",
            description="d",
            acceptance_criteria=[],
            declared_files=[],
            estimate_hours=1.0,
            confidence=0.5,
        )
        phase = PhaseSpec(name="Phase 1", runs=[run])
        assert len(phase.runs) == 1

    def test_saga_structure(self) -> None:
        structure = SagaStructure(name="Test Saga", phases=[])
        assert structure.phases == []
        assert structure.name == "Test Saga"
