"""Shared run review service — approve, reject, retry with full domain logic.

Both the REST API and the Telegram command handler delegate to this service
so that confidence events, state transitions, and phase gate checks are
always applied consistently.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ting.config import ReviewConfig
from ting.domain.exceptions import InvalidStateTransitionError, RunNotFoundError
from ting.domain.models import (
    ConfidenceEvent,
    ConfidenceEventType,
    Run,
    RunStatus,
    validate_transition,
)
from ting.ports.event_bus import EventBusPort, TingEvent
from ting.ports.tracker import TrackerPort

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReviewResult:
    """Outcome of a review action (approve / reject / retry)."""

    run: Run
    reason: str | None = None
    phase_gate_unlocked: bool = False


class InvalidRunStateError(Exception):
    def __init__(self, run_id: UUID | str, current: str, action: str) -> None:
        self.run_id = run_id
        self.current = current
        self.action = action
        super().__init__(f"Cannot {action} run {run_id} in {current} state")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_confidence_event(
    run_id: UUID,
    event_type: ConfidenceEventType,
    delta: float,
    current_score: float,
) -> ConfidenceEvent:
    new_score = max(0.0, min(1.0, current_score + delta))
    return ConfidenceEvent(
        id=uuid4(),
        run_id=run_id,
        event_type=event_type,
        delta=delta,
        score_after=new_score,
        created_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class RunReviewService:
    """Encapsulates the domain logic for run review actions.

    Callers (REST, Telegram, autonomous dispatcher) use this service
    so that confidence history, state transitions, and phase gate checks
    are never skipped.
    """

    def __init__(
        self,
        tracker: TrackerPort,
        owner_id: str,
        review_config: ReviewConfig,
        event_bus: EventBusPort | None = None,
    ) -> None:
        self._tracker = tracker
        self._owner_id = owner_id
        self._cfg = review_config
        self._event_bus = event_bus

    async def _emit_state_changed(self, run: Run, *, action: str) -> None:
        """Emit a run.state_changed event if an EventBus is wired."""
        if self._event_bus is None:
            return
        await self._event_bus.emit(
            TingEvent(
                event="run.state_changed",
                owner_id=self._owner_id,
                data={
                    "run_id": str(run.id),
                    "status": run.status.value,
                    "confidence": run.confidence,
                    "action": action,
                    "tracker_id": run.tracker_id,
                    "url": run.url,
                    "pr_id": run.pr_id,
                    "pr_url": run.pr_url,
                },
            )
        )

    async def approve(self, run_id: UUID) -> ReviewResult:
        """Approve a run: HUMAN_APPROVED event → MERGED → phase gate check."""
        run = await self._tracker.get_run_by_id(run_id)
        if run is None:
            raise RunNotFoundError(run_id)

        try:
            validate_transition(run.status, RunStatus.MERGED)
        except InvalidStateTransitionError:
            raise InvalidRunStateError(run_id, run.status.value, "approve")

        # Record confidence event
        event = _make_confidence_event(
            run.id,
            ConfidenceEventType.HUMAN_APPROVED,
            self._cfg.confidence_delta_approved,
            run.confidence,
        )
        await self._tracker.add_confidence_event(run.tracker_id, event)

        # Transition state
        updated = await self._tracker.update_run_progress(run.tracker_id, status=RunStatus.MERGED)

        # Close the tracker issue (sets it to Done in Linear/Jira)
        try:
            await self._tracker.close_run(run.tracker_id)
            logger.info("Closed tracker issue %s (Done)", run.tracker_id)
        except Exception:
            logger.error(
                "FAILED to close tracker issue %s after approval", run.tracker_id, exc_info=True
            )

        # Phase gate check
        phase_gate_unlocked = False
        phase = await self._tracker.get_phase_for_run(run.tracker_id)
        if phase and await self._tracker.all_runs_merged(phase.tracker_id):
            logger.info("Phase gate unlocked — all runs merged in phase %s", phase.tracker_id)
            phase_gate_unlocked = True

        await self._emit_state_changed(updated, action="approved")

        return ReviewResult(run=updated, phase_gate_unlocked=phase_gate_unlocked)

    async def reject(
        self,
        run_id: UUID,
        *,
        reason: str | None = None,
    ) -> ReviewResult:
        """Reject a run: HUMAN_REJECT event → FAILED."""
        run = await self._tracker.get_run_by_id(run_id)
        if run is None:
            raise RunNotFoundError(run_id)

        try:
            validate_transition(run.status, RunStatus.FAILED)
        except InvalidStateTransitionError:
            raise InvalidRunStateError(run_id, run.status.value, "reject")

        event = _make_confidence_event(
            run.id,
            ConfidenceEventType.HUMAN_REJECT,
            self._cfg.confidence_delta_rejected,
            run.confidence,
        )
        await self._tracker.add_confidence_event(run.tracker_id, event)

        updated = await self._tracker.update_run_progress(
            run.tracker_id, status=RunStatus.FAILED, reason=reason
        )

        await self._emit_state_changed(updated, action="rejected")

        return ReviewResult(run=updated, reason=reason)

    async def retry(self, run_id: UUID) -> ReviewResult:
        """Retry a run: RETRY event → re-queued with incremented retry_count.

        REVIEW → PENDING, FAILED → QUEUED (per RUN_TRANSITIONS).
        """
        run = await self._tracker.get_run_by_id(run_id)
        if run is None:
            raise RunNotFoundError(run_id)

        # Determine target status based on current state
        if run.status in {RunStatus.FAILED, RunStatus.ESCALATED}:
            target = RunStatus.QUEUED
        else:
            target = RunStatus.PENDING

        try:
            validate_transition(run.status, target)
        except InvalidStateTransitionError:
            raise InvalidRunStateError(run_id, run.status.value, "retry")

        event = _make_confidence_event(
            run.id,
            ConfidenceEventType.RETRY,
            self._cfg.confidence_delta_retry,
            run.confidence,
        )
        await self._tracker.add_confidence_event(run.tracker_id, event)

        updated = await self._tracker.update_run_progress(
            run.tracker_id, status=target, retry_count=run.retry_count + 1
        )

        await self._emit_state_changed(updated, action="retried")

        return ReviewResult(run=updated)
