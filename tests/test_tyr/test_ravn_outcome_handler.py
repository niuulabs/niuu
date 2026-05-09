"""Tests for NIU-614 / NIU-803 — Tyr Ravn completion outcome ingestion.

Covers:
  - _extract_outcome: task/session payload → RavnOutcome mapping
  - ReviewEngine.handle_ravn_outcome: confidence signals + decision logic
  - RavnOutcomeHandler._process_event: raid resolution, missing raid, happy path
  - Integration: InProcessBus round-trip (publish → state transition)
  - Coexistence: raid already terminal when outcome arrives → skipped
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain.events import SleipnirEvent
from tests.test_tyr.stubs import (
    StubTracker,
    StubTrackerFactory,
    StubVolundrFactory,
    make_raid,
)
from tyr.adapters.memory_event_bus import InMemoryEventBus
from tyr.adapters.ravn_outcome_handler import RavnOutcomeHandler, _extract_outcome
from tyr.config import ReviewConfig
from tyr.domain.models import (
    ConfidenceEventType,
    PRStatus,
    RaidStatus,
    RavnOutcome,
    Saga,
    SagaStatus,
)
from tyr.domain.services.review_engine import ReviewEngine
from tyr.ports.git import GitPort

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NOW = datetime.now(UTC)
_OWNER = "test-owner"
_SESSION = "sess-ravn-001"
_TRACKER_ID = "raid-tracker-001"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sleipnir_event(
    payload: dict | None = None,
    *,
    event_type: str = "ravn.task.completed",
    correlation_id: str | None = _SESSION,
) -> SleipnirEvent:
    return SleipnirEvent(
        event_type=event_type,
        source="ravn:coordinator",
        payload=payload or {},
        summary="task completed",
        urgency=0.8,
        domain="code",
        timestamp=NOW,
        correlation_id=correlation_id,
    )


def _make_saga(*, tracker_id: str = "saga-tracker-001") -> Saga:
    return Saga(
        id=uuid4(),
        tracker_id=tracker_id,
        tracker_type="linear",
        slug="test-saga",
        name="Test Saga",
        repos=["niuulabs/volundr"],
        feature_branch="feature/test-saga",
        status=SagaStatus.ACTIVE,
        confidence=0.8,
        created_at=NOW,
        base_branch="main",
        owner_id=_OWNER,
    )


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class StubGit(GitPort):
    async def create_branch(self, repo: str, branch: str, base: str) -> None:
        pass

    async def merge_branch(self, repo: str, source: str, target: str) -> None:
        pass

    async def delete_branch(self, repo: str, branch: str) -> None:
        pass

    async def create_pr(self, repo: str, source: str, target: str, title: str) -> str:
        return "pr-1"

    async def get_pr_status(self, pr_id: str) -> PRStatus:
        raise RuntimeError("no PR in stub")

    async def get_pr_changed_files(self, pr_id: str) -> list[str]:
        return []


def _make_engine(tracker: StubTracker, event_bus: InMemoryEventBus | None = None) -> ReviewEngine:
    factory = StubTrackerFactory(tracker)
    return ReviewEngine(
        tracker_factory=factory,
        volundr_factory=StubVolundrFactory(),
        git=StubGit(),
        review_config=ReviewConfig(
            reviewer_session_enabled=False,
            auto_approve_threshold=0.80,
        ),
        event_bus=event_bus,
    )


# ---------------------------------------------------------------------------
# Unit: _extract_outcome
# ---------------------------------------------------------------------------


class TestExtractOutcome:
    def test_full_payload(self):
        payload = {
            "verdict": "approve",
            "tests_passing": True,
            "scope_adherence": 0.95,
            "pr_url": "https://github.com/pr/1",
            "files_changed": ["a.py", "b.py"],
            "summary": "All done",
        }
        outcome = _extract_outcome(payload)
        assert outcome.verdict == "approve"
        assert outcome.tests_passing is True
        assert outcome.scope_adherence == 0.95
        assert outcome.pr_url == "https://github.com/pr/1"
        assert outcome.files_changed == ["a.py", "b.py"]
        assert outcome.summary == "All done"

    def test_minimal_payload_defaults(self):
        outcome = _extract_outcome({})
        assert outcome.verdict == "escalate"
        assert outcome.tests_passing is None
        assert outcome.scope_adherence is None
        assert outcome.pr_url is None
        assert outcome.files_changed == []
        assert outcome.summary == ""

    def test_tests_passing_false(self):
        outcome = _extract_outcome({"tests_passing": False, "verdict": "retry"})
        assert outcome.tests_passing is False
        assert outcome.verdict == "retry"

    def test_null_files_changed_coerced_to_empty(self):
        outcome = _extract_outcome({"files_changed": None})
        assert outcome.files_changed == []

    def test_structured_outcome_payload_is_supported(self):
        outcome = _extract_outcome(
            {
                "structured_outcome": {
                    "verdict": "approve",
                    "tests_passing": True,
                    "scope_adherence": 0.91,
                    "pr_url": "https://github.com/pr/2",
                    "summary": "Canonical session-ended payload",
                },
                "files_changed": ["src/app.py"],
            }
        )
        assert outcome.verdict == "approve"
        assert outcome.tests_passing is True
        assert outcome.scope_adherence == 0.91
        assert outcome.pr_url == "https://github.com/pr/2"
        assert outcome.files_changed == ["src/app.py"]
        assert outcome.summary == "Canonical session-ended payload"


# ---------------------------------------------------------------------------
# Unit: ReviewEngine.handle_ravn_outcome — confidence signals
# ---------------------------------------------------------------------------


class TestHandleRavnOutcomeSignals:
    @pytest.mark.asyncio
    async def test_tests_passing_true_emits_ci_pass(self):
        raid = make_raid(status=RaidStatus.REVIEW, confidence=0.5)
        tracker = StubTracker(raid)
        engine = _make_engine(tracker)

        outcome = RavnOutcome(
            verdict="escalate",
            tests_passing=True,
            scope_adherence=1.0,
            pr_url=None,
            files_changed=[],
            summary="",
        )
        await engine.handle_ravn_outcome(
            _TRACKER_ID, _OWNER, outcome, scope_adherence_threshold=0.7
        )

        event_types = [e.event_type for e in tracker.confidence_events.get(_TRACKER_ID, [])]
        assert ConfidenceEventType.CI_PASS in event_types

    @pytest.mark.asyncio
    async def test_tests_passing_false_emits_ci_fail(self):
        raid = make_raid(status=RaidStatus.REVIEW, confidence=0.5)
        tracker = StubTracker(raid)
        engine = _make_engine(tracker)

        outcome = RavnOutcome(
            verdict="escalate",
            tests_passing=False,
            scope_adherence=1.0,
            pr_url=None,
            files_changed=[],
            summary="",
        )
        await engine.handle_ravn_outcome(
            _TRACKER_ID, _OWNER, outcome, scope_adherence_threshold=0.7
        )

        event_types = [e.event_type for e in tracker.confidence_events.get(_TRACKER_ID, [])]
        assert ConfidenceEventType.CI_FAIL in event_types

    @pytest.mark.asyncio
    async def test_tests_passing_none_no_ci_event(self):
        raid = make_raid(status=RaidStatus.REVIEW)
        tracker = StubTracker(raid)
        engine = _make_engine(tracker)

        outcome = RavnOutcome(
            verdict="escalate",
            tests_passing=None,
            scope_adherence=1.0,
            pr_url=None,
            files_changed=[],
            summary="",
        )
        await engine.handle_ravn_outcome(
            _TRACKER_ID, _OWNER, outcome, scope_adherence_threshold=0.7
        )

        event_types = [e.event_type for e in tracker.confidence_events.get(_TRACKER_ID, [])]
        assert ConfidenceEventType.CI_PASS not in event_types
        assert ConfidenceEventType.CI_FAIL not in event_types

    @pytest.mark.asyncio
    async def test_low_scope_adherence_emits_scope_breach(self):
        raid = make_raid(status=RaidStatus.REVIEW, confidence=0.8)
        tracker = StubTracker(raid)
        engine = _make_engine(tracker)

        outcome = RavnOutcome(
            verdict="escalate",
            tests_passing=None,
            scope_adherence=0.5,  # below threshold of 0.7
            pr_url=None,
            files_changed=[],
            summary="",
        )
        await engine.handle_ravn_outcome(
            _TRACKER_ID, _OWNER, outcome, scope_adherence_threshold=0.7
        )

        event_types = [e.event_type for e in tracker.confidence_events.get(_TRACKER_ID, [])]
        assert ConfidenceEventType.SCOPE_BREACH in event_types

    @pytest.mark.asyncio
    async def test_high_scope_adherence_no_breach(self):
        raid = make_raid(status=RaidStatus.REVIEW, confidence=0.8)
        tracker = StubTracker(raid)
        engine = _make_engine(tracker)

        outcome = RavnOutcome(
            verdict="approve",
            tests_passing=True,
            scope_adherence=0.9,  # above threshold
            pr_url=None,
            files_changed=[],
            summary="",
        )
        await engine.handle_ravn_outcome(
            _TRACKER_ID, _OWNER, outcome, scope_adherence_threshold=0.7
        )

        event_types = [e.event_type for e in tracker.confidence_events.get(_TRACKER_ID, [])]
        assert ConfidenceEventType.SCOPE_BREACH not in event_types

    @pytest.mark.asyncio
    async def test_scope_adherence_none_no_breach(self):
        raid = make_raid(status=RaidStatus.REVIEW)
        tracker = StubTracker(raid)
        engine = _make_engine(tracker)

        outcome = RavnOutcome(
            verdict="escalate",
            tests_passing=None,
            scope_adherence=None,
            pr_url=None,
            files_changed=[],
            summary="",
        )
        await engine.handle_ravn_outcome(
            _TRACKER_ID, _OWNER, outcome, scope_adherence_threshold=0.7
        )

        event_types = [e.event_type for e in tracker.confidence_events.get(_TRACKER_ID, [])]
        assert ConfidenceEventType.SCOPE_BREACH not in event_types


# ---------------------------------------------------------------------------
# Unit: ReviewEngine.handle_ravn_outcome — decision logic
# ---------------------------------------------------------------------------


class TestHandleRavnOutcomeDecisions:
    @pytest.mark.asyncio
    async def test_verdict_approve_high_confidence_auto_approved(self):
        # ci_pass delta=0.30, starting at 0.6 → 0.9 >= 0.8 threshold → auto_approved
        raid = make_raid(status=RaidStatus.REVIEW, confidence=0.6)
        tracker = StubTracker(raid)
        engine = _make_engine(tracker)

        outcome = RavnOutcome(
            verdict="approve",
            tests_passing=True,  # ci_pass +0.30 → score=0.9
            scope_adherence=1.0,
            pr_url=None,
            files_changed=[],
            summary="",
        )
        decision = await engine.handle_ravn_outcome(
            _TRACKER_ID, _OWNER, outcome, scope_adherence_threshold=0.7
        )

        assert decision.action == "auto_approved"
        updated_raid = tracker._raids_by_id[_TRACKER_ID]
        assert updated_raid.status == RaidStatus.MERGED

    @pytest.mark.asyncio
    async def test_verdict_approve_low_confidence_escalated(self):
        # Starting at 0.4, ci_fail -0.30 → 0.1 < 0.8 threshold → escalated even with "approve"
        raid = make_raid(status=RaidStatus.REVIEW, confidence=0.4)
        tracker = StubTracker(raid)
        engine = _make_engine(tracker)

        outcome = RavnOutcome(
            verdict="approve",
            tests_passing=False,  # ci_fail -0.30 → score=0.1
            scope_adherence=1.0,
            pr_url=None,
            files_changed=[],
            summary="",
        )
        decision = await engine.handle_ravn_outcome(
            _TRACKER_ID, _OWNER, outcome, scope_adherence_threshold=0.7
        )

        assert decision.action == "escalated"
        updated_raid = tracker._raids_by_id[_TRACKER_ID]
        assert updated_raid.status == RaidStatus.ESCALATED

    @pytest.mark.asyncio
    async def test_verdict_retry_transitions_to_pending(self):
        raid = make_raid(status=RaidStatus.REVIEW, confidence=0.5, retry_count=0)
        tracker = StubTracker(raid)
        engine = _make_engine(tracker)

        outcome = RavnOutcome(
            verdict="retry",
            tests_passing=False,
            scope_adherence=1.0,
            pr_url=None,
            files_changed=[],
            summary="",
        )
        decision = await engine.handle_ravn_outcome(
            _TRACKER_ID, _OWNER, outcome, scope_adherence_threshold=0.7
        )

        assert decision.action == "retried"
        updated_raid = tracker._raids_by_id[_TRACKER_ID]
        assert updated_raid.status == RaidStatus.PENDING
        assert updated_raid.retry_count == 1

    @pytest.mark.asyncio
    async def test_verdict_retry_exhausted_transitions_to_failed(self):
        raid = make_raid(status=RaidStatus.REVIEW, confidence=0.5, retry_count=3)
        tracker = StubTracker(raid)
        # max_retries default is 3, so retry_count=3 means retries exhausted
        engine = _make_engine(tracker)

        outcome = RavnOutcome(
            verdict="retry",
            tests_passing=False,
            scope_adherence=1.0,
            pr_url=None,
            files_changed=[],
            summary="",
        )
        decision = await engine.handle_ravn_outcome(
            _TRACKER_ID, _OWNER, outcome, scope_adherence_threshold=0.7
        )

        assert decision.action == "failed"
        updated_raid = tracker._raids_by_id[_TRACKER_ID]
        assert updated_raid.status == RaidStatus.FAILED

    @pytest.mark.asyncio
    async def test_verdict_escalate_direct_escalation(self):
        raid = make_raid(status=RaidStatus.REVIEW, confidence=0.9)
        tracker = StubTracker(raid)
        engine = _make_engine(tracker)

        outcome = RavnOutcome(
            verdict="escalate",
            tests_passing=True,
            scope_adherence=1.0,
            pr_url=None,
            files_changed=[],
            summary="",
        )
        decision = await engine.handle_ravn_outcome(
            _TRACKER_ID, _OWNER, outcome, scope_adherence_threshold=0.7
        )

        assert decision.action == "escalated"
        updated_raid = tracker._raids_by_id[_TRACKER_ID]
        assert updated_raid.status == RaidStatus.ESCALATED

    @pytest.mark.asyncio
    async def test_unknown_verdict_escalates(self):
        raid = make_raid(status=RaidStatus.REVIEW, confidence=0.5)
        tracker = StubTracker(raid)
        engine = _make_engine(tracker)

        outcome = RavnOutcome(
            verdict="something_unknown",
            tests_passing=None,
            scope_adherence=None,
            pr_url=None,
            files_changed=[],
            summary="",
        )
        decision = await engine.handle_ravn_outcome(
            _TRACKER_ID, _OWNER, outcome, scope_adherence_threshold=0.7
        )

        assert decision.action == "escalated"

    @pytest.mark.asyncio
    async def test_running_raid_transitions_to_review_first(self):
        raid = make_raid(status=RaidStatus.RUNNING, confidence=0.6)
        tracker = StubTracker(raid)
        engine = _make_engine(tracker)

        outcome = RavnOutcome(
            verdict="approve",
            tests_passing=True,
            scope_adherence=1.0,
            pr_url=None,
            files_changed=[],
            summary="",
        )
        decision = await engine.handle_ravn_outcome(
            _TRACKER_ID, _OWNER, outcome, scope_adherence_threshold=0.7
        )

        # After ci_pass (+0.30) starting at 0.6 → 0.9 ≥ 0.8 → auto_approved
        assert decision.action == "auto_approved"
        updated_raid = tracker._raids_by_id[_TRACKER_ID]
        assert updated_raid.status == RaidStatus.MERGED

    @pytest.mark.asyncio
    async def test_terminal_raid_skipped(self):
        raid = make_raid(status=RaidStatus.MERGED, confidence=1.0)
        tracker = StubTracker(raid)
        engine = _make_engine(tracker)

        outcome = RavnOutcome(
            verdict="approve",
            tests_passing=True,
            scope_adherence=1.0,
            pr_url=None,
            files_changed=[],
            summary="",
        )
        decision = await engine.handle_ravn_outcome(
            _TRACKER_ID, _OWNER, outcome, scope_adherence_threshold=0.7
        )

        assert decision.action == "skipped"
        updated_raid = tracker._raids_by_id[_TRACKER_ID]
        assert updated_raid.status == RaidStatus.MERGED  # unchanged


# ---------------------------------------------------------------------------
# Unit: RavnOutcomeHandler — correlation lookup
# ---------------------------------------------------------------------------


class TestRavnOutcomeHandlerCorrelation:
    def _make_handler(self, tracker: StubTracker) -> tuple[RavnOutcomeHandler, InMemoryEventBus]:
        bus = InMemoryEventBus()
        factory = StubTrackerFactory(tracker)
        engine = ReviewEngine(
            tracker_factory=factory,
            volundr_factory=StubVolundrFactory(),
            git=StubGit(),
            review_config=ReviewConfig(reviewer_session_enabled=False),
            event_bus=bus,
        )
        handler = RavnOutcomeHandler(
            subscriber=InProcessBus(),
            tracker_factory=factory,
            review_engine=engine,
            owner_id=_OWNER,
        )
        return handler, bus

    @pytest.mark.asyncio
    async def test_missing_correlation_id_drops_silently(self, caplog):
        tracker = StubTracker()
        handler, _ = self._make_handler(tracker)

        event = _make_sleipnir_event(correlation_id=None)
        await handler._process_event(event)

        assert "no usable raid/session identifiers" in caplog.text

    @pytest.mark.asyncio
    async def test_no_matching_raid_drops_silently(self, caplog):
        tracker = StubTracker()  # no raids
        handler, _ = self._make_handler(tracker)

        event = _make_sleipnir_event({"verdict": "approve"}, correlation_id="unknown-session")
        await handler._process_event(event)

        assert "no raid for event" in caplog.text

    @pytest.mark.asyncio
    async def test_valid_correlation_processes_raid(self):
        raid = make_raid(status=RaidStatus.REVIEW, confidence=0.6)
        tracker = StubTracker(raid)
        handler, _ = self._make_handler(tracker)

        payload = {"verdict": "approve", "tests_passing": True, "scope_adherence": 1.0}
        event = _make_sleipnir_event(payload, correlation_id=_SESSION)
        await handler._process_event(event)

        updated_raid = tracker._raids_by_id[_TRACKER_ID]
        assert updated_raid.status == RaidStatus.MERGED

    @pytest.mark.asyncio
    async def test_session_ended_resolves_raid_by_explicit_raid_id(self):
        raid = make_raid(status=RaidStatus.RUNNING, confidence=0.6)
        tracker = StubTracker(raid)
        handler, _ = self._make_handler(tracker)

        event = _make_sleipnir_event(
            {
                "raid_id": str(raid.id),
                "session_id": "wrong-session",
                "structured_outcome": {
                    "verdict": "approve",
                    "tests_passing": True,
                    "scope_adherence": 1.0,
                },
            },
            event_type="ravn.session.ended",
            correlation_id="wrong-correlation",
        )
        await handler._process_event(event)

        updated_raid = tracker._raids_by_id[_TRACKER_ID]
        assert updated_raid.status == RaidStatus.MERGED

    @pytest.mark.asyncio
    async def test_session_ended_can_fall_back_to_payload_session_id(self):
        raid = make_raid(status=RaidStatus.REVIEW, confidence=0.6)
        tracker = StubTracker(raid)
        handler, _ = self._make_handler(tracker)

        event = _make_sleipnir_event(
            {
                "session_id": _SESSION,
                "structured_outcome": {
                    "verdict": "approve",
                    "tests_passing": True,
                    "scope_adherence": 1.0,
                },
            },
            event_type="ravn.session.ended",
            correlation_id="daemon_task_123",
        )
        await handler._process_event(event)

        updated_raid = tracker._raids_by_id[_TRACKER_ID]
        assert updated_raid.status == RaidStatus.MERGED

    @pytest.mark.asyncio
    async def test_tracker_issue_lookup_falls_back_to_session_lookup(self):
        raid = make_raid(status=RaidStatus.REVIEW, confidence=0.6)
        tracker = StubTracker(raid)
        tracker.get_raid = AsyncMock(side_effect=[RuntimeError("missing tracker raid"), raid])
        handler, _ = self._make_handler(tracker)

        event = _make_sleipnir_event(
            {"tracker_issue_id": _TRACKER_ID, "verdict": "approve", "tests_passing": True},
            correlation_id=_SESSION,
        )
        await handler._process_event(event)

        updated_raid = tracker._raids_by_id[_TRACKER_ID]
        assert updated_raid.status == RaidStatus.MERGED

    @pytest.mark.asyncio
    async def test_invalid_uuid_raid_id_falls_back_to_tracker_lookup(self):
        raid = make_raid(status=RaidStatus.REVIEW, confidence=0.6)
        tracker = StubTracker(raid)
        tracker.get_raid = AsyncMock(side_effect=[raid, raid])
        handler, _ = self._make_handler(tracker)

        event = _make_sleipnir_event(
            {"raid_id": "not-a-uuid", "verdict": "approve", "tests_passing": True},
            correlation_id="unrelated",
        )
        await handler._process_event(event)

        assert tracker.get_raid.await_args_list[0].args == ("not-a-uuid",)
        updated_raid = tracker._raids_by_id[_TRACKER_ID]
        assert updated_raid.status == RaidStatus.MERGED

    @pytest.mark.asyncio
    async def test_no_trackers_returns_without_processing(self):
        bus = InMemoryEventBus()
        empty_factory = type("EmptyTrackerFactory", (), {"for_owner": AsyncMock(return_value=[])})()
        engine = ReviewEngine(
            tracker_factory=empty_factory,
            volundr_factory=StubVolundrFactory(),
            git=StubGit(),
            review_config=ReviewConfig(reviewer_session_enabled=False),
            event_bus=bus,
        )
        handler = RavnOutcomeHandler(
            subscriber=InProcessBus(),
            tracker_factory=empty_factory,
            review_engine=engine,
            owner_id=_OWNER,
        )

        await handler._process_event(_make_sleipnir_event({"verdict": "approve"}))
        assert True


# ---------------------------------------------------------------------------
# Integration: InProcessBus round-trip
# ---------------------------------------------------------------------------


class TestRavnOutcomeHandlerIntegration:
    @pytest.mark.asyncio
    async def test_publish_ravn_task_completed_transitions_raid(self):
        """Publish event on InProcessBus → raid transitions to MERGED."""
        raid = make_raid(status=RaidStatus.REVIEW, confidence=0.6)
        tracker = StubTracker(raid)
        bus = InProcessBus()
        factory = StubTrackerFactory(tracker)
        engine = ReviewEngine(
            tracker_factory=factory,
            volundr_factory=StubVolundrFactory(),
            git=StubGit(),
            review_config=ReviewConfig(reviewer_session_enabled=False),
        )
        handler = RavnOutcomeHandler(
            subscriber=bus,
            tracker_factory=factory,
            review_engine=engine,
            owner_id=_OWNER,
        )

        await handler.start()
        try:
            event = SleipnirEvent(
                event_type="ravn.task.completed",
                source="ravn:coordinator",
                payload={
                    "verdict": "approve",
                    "tests_passing": True,
                    "scope_adherence": 0.95,
                    "summary": "All tests pass",
                },
                summary="task done",
                urgency=0.9,
                domain="code",
                timestamp=NOW,
                correlation_id=_SESSION,
            )
            await bus.publish(event)

            # Give the event loop a chance to process the task
            for _ in range(10):
                await asyncio.sleep(0)

            updated = tracker._raids_by_id[_TRACKER_ID]
            assert updated.status == RaidStatus.MERGED
        finally:
            await handler.stop()

    @pytest.mark.asyncio
    async def test_retry_verdict_transitions_to_pending(self):
        raid = make_raid(status=RaidStatus.REVIEW, confidence=0.5, retry_count=0)
        tracker = StubTracker(raid)
        bus = InProcessBus()
        factory = StubTrackerFactory(tracker)
        engine = ReviewEngine(
            tracker_factory=factory,
            volundr_factory=StubVolundrFactory(),
            git=StubGit(),
            review_config=ReviewConfig(reviewer_session_enabled=False),
        )
        handler = RavnOutcomeHandler(
            subscriber=bus,
            tracker_factory=factory,
            review_engine=engine,
            owner_id=_OWNER,
        )

        await handler.start()
        try:
            event = SleipnirEvent(
                event_type="ravn.task.completed",
                source="ravn:coordinator",
                payload={"verdict": "retry", "tests_passing": False},
                summary="retry needed",
                urgency=0.8,
                domain="code",
                timestamp=NOW,
                correlation_id=_SESSION,
            )
            await bus.publish(event)

            for _ in range(10):
                await asyncio.sleep(0)

            updated = tracker._raids_by_id[_TRACKER_ID]
            assert updated.status == RaidStatus.PENDING
            assert updated.retry_count == 1
        finally:
            await handler.stop()

    @pytest.mark.asyncio
    async def test_session_ended_triggers_auto_continue_for_approved_flock_work(self):
        raid = make_raid(status=RaidStatus.RUNNING, confidence=0.9)
        tracker = StubTracker(raid)
        tracker.saga = _make_saga(tracker_id="saga-continue-001")
        bus = InProcessBus()
        factory = StubTrackerFactory(tracker)
        dispatch_service = AsyncMock()
        engine = ReviewEngine(
            tracker_factory=factory,
            volundr_factory=StubVolundrFactory(),
            git=StubGit(),
            review_config=ReviewConfig(reviewer_session_enabled=False),
            dispatch_service=dispatch_service,
        )
        handler = RavnOutcomeHandler(
            subscriber=bus,
            tracker_factory=factory,
            review_engine=engine,
            owner_id=_OWNER,
        )

        await handler.start()
        try:
            event = SleipnirEvent(
                event_type="ravn.session.ended",
                source="skuld:test",
                payload={
                    "session_id": _SESSION,
                    "raid_id": str(raid.id),
                    "structured_outcome": {
                        "verdict": "approve",
                        "tests_passing": True,
                        "scope_adherence": 0.95,
                        "summary": "Ready for merge",
                    },
                    "outcome_valid": True,
                    "files_changed": ["src/tyr/domain/services/dispatch_service.py"],
                },
                summary="session ended",
                urgency=0.7,
                domain="code",
                timestamp=NOW,
                correlation_id=_SESSION,
            )
            await bus.publish(event)

            for _ in range(10):
                await asyncio.sleep(0)

            updated = tracker._raids_by_id[_TRACKER_ID]
            assert updated.status == RaidStatus.MERGED
            dispatch_service.try_auto_continue.assert_awaited_once_with(
                _OWNER, "saga-continue-001"
            )
        finally:
            await handler.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_in_flight_tasks(self):
        """stop() should cancel pending tasks without raising."""
        bus = InProcessBus()
        handler = RavnOutcomeHandler(
            subscriber=bus,
            tracker_factory=StubTrackerFactory(StubTracker()),
            review_engine=ReviewEngine(
                tracker_factory=StubTrackerFactory(StubTracker()),
                volundr_factory=StubVolundrFactory(),
                git=StubGit(),
                review_config=ReviewConfig(reviewer_session_enabled=False),
            ),
            owner_id=_OWNER,
        )
        await handler.start()
        assert handler.is_running
        await handler.stop()
        assert not handler.is_running

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self):
        subscriber = AsyncMock()
        subscription = AsyncMock()
        subscriber.subscribe.return_value = subscription
        tracker = StubTracker()
        factory = StubTrackerFactory(tracker)
        engine = ReviewEngine(
            tracker_factory=factory,
            volundr_factory=StubVolundrFactory(),
            git=StubGit(),
            review_config=ReviewConfig(reviewer_session_enabled=False),
        )
        handler = RavnOutcomeHandler(
            subscriber=subscriber,
            tracker_factory=factory,
            review_engine=engine,
            owner_id=_OWNER,
        )

        await handler.start()
        await handler.start()

        subscriber.subscribe.assert_awaited_once()
        await handler.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_manual_pending_tasks(self):
        subscriber = AsyncMock()
        subscription = AsyncMock()
        subscriber.subscribe.return_value = subscription
        tracker = StubTracker()
        factory = StubTrackerFactory(tracker)
        engine = ReviewEngine(
            tracker_factory=factory,
            volundr_factory=StubVolundrFactory(),
            git=StubGit(),
            review_config=ReviewConfig(reviewer_session_enabled=False),
        )
        handler = RavnOutcomeHandler(
            subscriber=subscriber,
            tracker_factory=factory,
            review_engine=engine,
            owner_id=_OWNER,
        )
        await handler.start()

        blocker = asyncio.Event()

        async def _wait_forever() -> None:
            await blocker.wait()

        task = asyncio.create_task(_wait_forever())
        handler._pending_tasks.add(task)

        await handler.stop()

        assert task.cancelled()

    @pytest.mark.asyncio
    async def test_process_event_swallows_review_engine_failures(self):
        raid = make_raid(status=RaidStatus.REVIEW, confidence=0.6)
        tracker = StubTracker(raid)
        factory = StubTrackerFactory(tracker)
        engine = ReviewEngine(
            tracker_factory=factory,
            volundr_factory=StubVolundrFactory(),
            git=StubGit(),
            review_config=ReviewConfig(reviewer_session_enabled=False),
        )
        engine.handle_ravn_outcome = AsyncMock(side_effect=RuntimeError("boom"))
        handler = RavnOutcomeHandler(
            subscriber=InProcessBus(),
            tracker_factory=factory,
            review_engine=engine,
            owner_id=_OWNER,
        )

        await handler._process_event(
            _make_sleipnir_event({"verdict": "approve", "tests_passing": True})
        )
        engine.handle_ravn_outcome.assert_awaited_once()


# ---------------------------------------------------------------------------
# Integration: coexistence — ActivitySubscriber and RavnOutcomeHandler
# ---------------------------------------------------------------------------


class TestCoexistence:
    @pytest.mark.asyncio
    async def test_explicit_outcome_takes_precedence_over_terminal_state(self):
        """When ActivitySubscriber has already merged the raid, ravn outcome is skipped."""
        raid = make_raid(status=RaidStatus.MERGED, confidence=1.0)
        tracker = StubTracker(raid)
        factory = StubTrackerFactory(tracker)
        engine = ReviewEngine(
            tracker_factory=factory,
            volundr_factory=StubVolundrFactory(),
            git=StubGit(),
            review_config=ReviewConfig(reviewer_session_enabled=False),
        )
        handler = RavnOutcomeHandler(
            subscriber=InProcessBus(),
            tracker_factory=factory,
            review_engine=engine,
            owner_id=_OWNER,
        )

        event = _make_sleipnir_event(
            {"verdict": "approve", "tests_passing": True}, correlation_id=_SESSION
        )
        await handler._process_event(event)

        # Raid should still be MERGED — handler skips terminal raids
        assert tracker._raids_by_id[_TRACKER_ID].status == RaidStatus.MERGED

    @pytest.mark.asyncio
    async def test_escalated_raid_skipped(self):
        """If ActivitySubscriber already escalated, ravn outcome is skipped."""
        raid = make_raid(status=RaidStatus.ESCALATED, confidence=0.4)
        tracker = StubTracker(raid)
        factory = StubTrackerFactory(tracker)
        engine = ReviewEngine(
            tracker_factory=factory,
            volundr_factory=StubVolundrFactory(),
            git=StubGit(),
            review_config=ReviewConfig(reviewer_session_enabled=False),
        )
        handler = RavnOutcomeHandler(
            subscriber=InProcessBus(),
            tracker_factory=factory,
            review_engine=engine,
            owner_id=_OWNER,
        )

        event = _make_sleipnir_event(
            {"verdict": "approve", "tests_passing": True}, correlation_id=_SESSION
        )
        await handler._process_event(event)

        assert tracker._raids_by_id[_TRACKER_ID].status == RaidStatus.ESCALATED
