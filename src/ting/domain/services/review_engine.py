"""Review engine — projects terminal run outcomes and phase progression.

Ting's execution primitive is the workflow: a deterministic stop node emits an
authoritative outcome (``ravn.task.completed`` with checks), and this engine
projects it — MERGED on an authoritative approval, retry/escalate/failure
otherwise — then walks the phase gate and auto-continue.

A run that lands in REVIEW *without* an authoritative workflow outcome has no
machine verdict, so it is escalated to a human. The old confidence-scoring
pipeline that used to guess here (CI deltas, scope-breach ratios, LLM reviewer
sessions, an arbiter persona) is gone: gating lives in workflow gate nodes,
and human review lives in :class:`~ting.domain.services.run_review.RunReviewService`.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass

from ting.config import ReviewConfig
from ting.domain.models import (
    Phase,
    PhaseStatus,
    RavnOutcome,
    Run,
    RunStatus,
    Saga,
    SagaStatus,
    validate_transition,
)
from ting.domain.services.dispatch_service import DispatchService
from ting.domain.services.session_transcript import attach_session_transcript
from ting.ports.event_bus import EventBusPort, TingEvent
from ting.ports.saga_repository import SagaRepository
from ting.ports.tracker import TrackerFactory, TrackerPort
from ting.ports.volundr import VolundrFactory

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReviewDecision:
    """Outcome of the review engine's projection of a run."""

    run: Run
    action: str  # "auto_approved", "retried", "escalated", "failed", "skipped"
    reason: str
    phase_gate_unlocked: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_authoritative_workflow_approval(outcome: RavnOutcome) -> bool:
    """Return True when a deterministic workflow stop-node approved the work.

    This lets the mesh/runtime stay generic: worker nodes emit canonical
    outcomes, the deterministic stop node decides the join rule, and Ting can
    trust that final approval directly.
    """
    if not outcome.authoritative or outcome.verdict != "approve":
        return False
    if not outcome.checks:
        return False

    successful_verdicts = {
        "pass",
        "approve",
        "approved",
        "ok",
        "success",
        "complete",
        "completed",
        "done",
    }

    for check in outcome.checks:
        verdict = str(check.get("verdict") or "").strip().lower()
        if verdict not in successful_verdicts:
            return False
    return True


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class ReviewEngine:
    """Projects runs entering REVIEW and authoritative workflow outcomes.

    Called by the watcher after a run transitions to REVIEW, and by the ravn
    outcome path when a ``ravn.task.completed`` event arrives. An
    authoritative workflow approval merges; everything else without a clear
    machine verdict goes to a human.
    """

    def __init__(
        self,
        tracker_factory: TrackerFactory,
        volundr_factory: VolundrFactory,
        review_config: ReviewConfig,
        event_bus: EventBusPort | None = None,
        dispatch_service: DispatchService | None = None,
        saga_repo: SagaRepository | None = None,
    ) -> None:
        self._tracker_factory = tracker_factory
        self._volundr_factory = volundr_factory
        self._cfg = review_config
        self._event_bus = event_bus
        self._dispatch_service = dispatch_service
        self._saga_repo = saga_repo
        self._task: asyncio.Task[None] | None = None
        self._processed: set[str] = set()

    @property
    def running(self) -> bool:
        return self._task is not None

    async def start(self) -> None:
        """Subscribe to the event bus and react to runs entering REVIEW."""
        if self._event_bus is None:
            return
        self._task = asyncio.create_task(self._listen())

    async def stop(self) -> None:
        """Cancel the event listener task."""
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _listen(self) -> None:
        """Listen for run.state_changed events where status == REVIEW."""
        if self._event_bus is None:
            logger.warning("Review engine has no event bus — cannot listen")
            return
        q = self._event_bus.subscribe()
        logger.info("Review engine listening for run.state_changed events")
        try:
            while True:
                event = await q.get()
                logger.debug(
                    "Review engine received event: %s (data=%s)",
                    event.event,
                    event.data,
                )
                if event.event != "run.state_changed":
                    continue
                status = event.data.get("status")
                tracker_id = event.data.get("tracker_id")
                # Any non-REVIEW transition means a previous review cycle
                # is over (run was MERGED, FAILED, or re-dispatched into
                # RUNNING). Forget the tracker_id so a future REVIEW
                # transition for the same run (e.g. after auto-continue
                # re-dispatches it) is treated as a fresh cycle, not
                # silently skipped as "already processed".
                if status != RunStatus.REVIEW.value:
                    if isinstance(tracker_id, str) and tracker_id:
                        self._processed.discard(tracker_id)
                    logger.debug(
                        "Skipping — status=%s (not REVIEW)",
                        status,
                    )
                    continue
                owner_id = event.owner_id
                if not tracker_id or not owner_id:
                    logger.warning(
                        "Skipping — missing tracker_id=%s or owner_id=%s",
                        tracker_id,
                        owner_id,
                    )
                    continue
                if tracker_id in self._processed:
                    logger.debug(
                        "Skipping — tracker_id=%s already processed",
                        tracker_id,
                    )
                    continue
                logger.info(
                    "Review engine evaluating run %s for owner %s",
                    tracker_id,
                    owner_id[:8],
                )
                try:
                    self._processed.add(tracker_id)
                    decision = await self.evaluate(tracker_id, owner_id)
                    logger.info(
                        "Review engine decision for %s: %s (reason=%s)",
                        tracker_id,
                        decision.action,
                        decision.reason,
                    )
                except Exception:
                    logger.warning(
                        "Review engine failed for run %s",
                        tracker_id,
                        exc_info=True,
                    )
        except asyncio.CancelledError:
            return
        finally:
            if self._event_bus is not None:
                self._event_bus.unsubscribe(q)

    async def evaluate(self, tracker_id: str, owner_id: str) -> ReviewDecision:
        """Escalate a run in REVIEW to a human decision.

        A run reaches this path only when it completed without an
        authoritative workflow outcome (solo sessions, or a flock whose stop
        node never fired). There is no machine verdict to trust, so the run
        is handed to a human instead of being scored by heuristics.
        """
        trackers = await self._tracker_factory.for_owner(owner_id)
        if not trackers:
            raise ValueError(f"No tracker adapter found for owner {owner_id}")
        tracker = trackers[0]

        run = await tracker.get_run(tracker_id)
        if run.status != RunStatus.REVIEW:
            raise ValueError(f"Run {tracker_id} not in REVIEW state: {run.status}")

        return await self._handle_escalation(
            tracker,
            tracker_id,
            owner_id,
            run,
            reason="no authoritative workflow outcome — escalating to human review",
        )

    async def handle_ravn_outcome(
        self,
        tracker_id: str,
        owner_id: str,
        outcome: RavnOutcome,
    ) -> ReviewDecision:
        """Process a structured outcome from a ``ravn.task.completed`` event.

        Routes on the explicit verdict: an authoritative workflow approval
        merges; a plain "approve" without workflow authority escalates to a
        human; "retry" re-dispatches while retries remain.

        Accepts runs in RUNNING or REVIEW state:

        - RUNNING → transitions to REVIEW first (ravn outcome is authoritative).
        - REVIEW → processes directly (ActivitySubscriber may have beaten us).
        - Any other status → skipped (already handled or terminal).
        """
        trackers = await self._tracker_factory.for_owner(owner_id)
        if not trackers:
            raise ValueError(f"No tracker adapter found for owner {owner_id}")
        tracker = trackers[0]

        run = await tracker.get_run(tracker_id)

        if run.status not in (RunStatus.RUNNING, RunStatus.REVIEW):
            logger.info(
                "handle_ravn_outcome: run %s is %s — already handled, skipping",
                tracker_id,
                run.status,
            )
            return ReviewDecision(
                run=run,
                action="skipped",
                reason=f"Run already in {run.status} state",
            )

        if run.status == RunStatus.RUNNING:
            validate_transition(run.status, RunStatus.REVIEW)
            run = await tracker.update_run_progress(tracker_id, status=RunStatus.REVIEW)

        match outcome.verdict:
            case "retry":
                if run.retry_count < self._cfg.max_retries:
                    attempt = f"attempt {run.retry_count + 1}/{self._cfg.max_retries}"
                    return await self._auto_retry(
                        tracker,
                        tracker_id,
                        owner_id,
                        run,
                        reason=f"ravn coordinator requested retry ({attempt})",
                    )

                # Retries exhausted → FAILED
                validate_transition(run.status, RunStatus.FAILED)
                updated = await tracker.update_run_progress(
                    tracker_id,
                    status=RunStatus.FAILED,
                    reason="ravn coordinator requested retry but retries exhausted",
                )
                if run.session_id:
                    await self._attach_working_transcript(tracker, tracker_id, owner_id, run)
                    await self._stop_session(owner_id, run.session_id, "working session")
                await self._emit_state_changed(updated, owner_id=owner_id, action="failed")
                return ReviewDecision(
                    run=updated,
                    action="failed",
                    reason=(
                        f"ravn coordinator requested retry after"
                        f" {self._cfg.max_retries} retries exhausted"
                    ),
                )
            case "approve":
                if _is_authoritative_workflow_approval(outcome):
                    logger.info(
                        "handle_ravn_outcome: authoritative workflow approval for run %s",
                        tracker_id,
                    )
                    return await self._handle_auto_approve(tracker, tracker_id, owner_id, run)
                return await self._handle_escalation(
                    tracker,
                    tracker_id,
                    owner_id,
                    run,
                    reason=(
                        "approve verdict without authoritative workflow checks"
                        " — escalating to human review"
                    ),
                )
            case "escalate":
                return await self._handle_escalation(
                    tracker,
                    tracker_id,
                    owner_id,
                    run,
                    reason="ravn coordinator requested escalation",
                )
            case _:
                logger.warning(
                    "handle_ravn_outcome: unknown verdict %r for run %s — escalating",
                    outcome.verdict,
                    tracker_id,
                )
                return await self._handle_escalation(
                    tracker,
                    tracker_id,
                    owner_id,
                    run,
                    reason=f"unknown ravn verdict {outcome.verdict!r}",
                )
        raise AssertionError("Unreachable handle_ravn_outcome fallthrough")

    # -- Decision handlers --

    async def _handle_auto_approve(
        self,
        tracker: TrackerPort,
        tracker_id: str,
        owner_id: str,
        run: Run,
    ) -> ReviewDecision:
        """Authoritative approval: transition REVIEW → MERGED.

        The workflow merges the PR itself. Ting attaches the working
        transcript to the tracker issue, stops the session, and walks the
        phase gate.
        """
        validate_transition(run.status, RunStatus.MERGED)
        updated = await tracker.update_run_progress(tracker_id, status=RunStatus.MERGED)

        # Close the tracker issue (sets it to Done in Linear/Jira)
        try:
            await tracker.close_run(tracker_id)
            logger.info("Closed tracker issue %s (Done)", tracker_id)
        except Exception:
            logger.error("FAILED to close tracker issue %s after merge", tracker_id, exc_info=True)

        # Attach transcript and stop the working session
        if run.session_id:
            await self._attach_working_transcript(tracker, tracker_id, owner_id, run)
            await self._stop_session(owner_id, run.session_id, "working session")

        # Look up saga once — used by both phase gate and event emission
        saga = await tracker.get_saga_for_run(tracker_id)

        # Phase gate check
        phase_gate_unlocked = await self._check_phase_gate(tracker, tracker_id, owner_id, saga=saga)

        saga_tid = saga.tracker_id if saga else None
        await self._emit_state_changed(
            updated, owner_id=owner_id, action="auto_approved", saga_tracker_id=saga_tid
        )

        # Trigger auto-continue after merge (and after phase unlock)
        if saga_tid:
            await self._try_auto_continue(owner_id, saga_tid)

        return ReviewDecision(
            run=updated,
            action="auto_approved",
            reason="authoritative workflow approval",
            phase_gate_unlocked=phase_gate_unlocked,
        )

    async def _stop_session(self, owner_id: str, session_id: str, label: str = "session") -> None:
        """Stop a Volundr session after terminal state."""
        try:
            adapters = await self._volundr_factory.for_owner(owner_id)
            if not adapters:
                return
            await adapters[0].stop_session(session_id)
            logger.info("Stopped %s %s", label, session_id)
        except Exception:
            logger.warning("Failed to stop %s %s", label, session_id, exc_info=True)

    async def _attach_working_transcript(
        self,
        tracker: TrackerPort,
        tracker_id: str,
        owner_id: str,
        run: Run,
    ) -> None:
        """Fetch the working session conversation and attach as a document."""
        await attach_session_transcript(
            volundr_factory=self._volundr_factory,
            tracker=tracker,
            tracker_id=tracker_id,
            owner_id=owner_id,
            session_id=run.session_id,
            title_prefix="Working Session Transcript",
            run_name=run.name,
        )

    async def _handle_escalation(
        self,
        tracker: TrackerPort,
        tracker_id: str,
        owner_id: str,
        run: Run,
        *,
        reason: str,
    ) -> ReviewDecision:
        """No machine verdict to trust — escalate to human review."""
        validate_transition(run.status, RunStatus.ESCALATED)
        updated = await tracker.update_run_progress(tracker_id, status=RunStatus.ESCALATED)

        # Snapshot the working transcript but keep the session alive for human review
        if run.session_id:
            await self._attach_working_transcript(tracker, tracker_id, owner_id, run)

        await self._emit_state_changed(updated, owner_id=owner_id, action="escalated")
        return ReviewDecision(run=updated, action="escalated", reason=reason)

    async def _auto_retry(
        self,
        tracker: TrackerPort,
        tracker_id: str,
        owner_id: str,
        run: Run,
        *,
        reason: str,
    ) -> ReviewDecision:
        """Transition run back to PENDING for re-dispatch."""
        # Send failure context to the running session before resetting
        await self._send_retry_feedback(run, owner_id, reason)

        validate_transition(run.status, RunStatus.PENDING)
        updated = await tracker.update_run_progress(
            tracker_id, status=RunStatus.PENDING, retry_count=run.retry_count + 1
        )

        await self._emit_state_changed(updated, owner_id=owner_id, action="retried")
        return ReviewDecision(run=updated, action="retried", reason=reason)

    # -- Session feedback --

    async def _send_retry_feedback(self, run: Run, owner_id: str, reason: str) -> None:
        """Send failure context to the session before retrying."""
        if not run.session_id:
            return
        adapters = await self._volundr_factory.for_owner(owner_id)
        if not adapters:
            logger.warning(
                "No authenticated Volundr adapter for owner %s — cannot send feedback",
                owner_id[:8],
            )
            return
        try:
            await adapters[0].send_message(
                run.session_id,
                f"Review failed: {reason}. Please fix and push again.",
            )
            logger.info("Sent retry feedback to session %s", run.session_id)
        except Exception:
            logger.warning(
                "Failed to send retry feedback to session %s for run %s",
                run.session_id,
                run.id,
            )

    # -- Phase gate --

    async def _check_phase_gate(
        self,
        tracker: TrackerPort,
        tracker_id: str,
        owner_id: str,
        *,
        saga: Saga | None = None,
    ) -> bool:
        """Check if all runs in the phase are merged, and unlock next phase if so."""
        phase = await tracker.get_phase_for_run(tracker_id)
        if phase is None:
            return False

        if not await tracker.all_runs_merged(phase.tracker_id):
            return False

        logger.info("Phase gate unlocked — all runs merged in phase %s", phase.tracker_id)

        # Unlock the next phase
        if saga is None:
            saga = await tracker.get_saga_for_run(tracker_id)
        if saga is None:
            return True

        phases = await tracker.list_phases_for_saga(saga.tracker_id)
        current_idx = next(
            (i for i, p in enumerate(phases) if p.tracker_id == phase.tracker_id), -1
        )
        if current_idx < 0:
            return True

        await self._sync_saga_phase_projection(
            saga=saga,
            current_phase=phase,
            next_phase=phases[current_idx + 1] if current_idx + 1 < len(phases) else None,
        )

        if current_idx + 1 >= len(phases):
            return True

        next_phase = phases[current_idx + 1]
        if next_phase.status == PhaseStatus.GATED:
            await tracker.update_phase_status(next_phase.tracker_id, PhaseStatus.ACTIVE)
            logger.info("Next phase %s unlocked (GATED → ACTIVE)", next_phase.tracker_id)

            if self._event_bus:
                await self._event_bus.emit(
                    TingEvent(
                        event="phase.unlocked",
                        owner_id=owner_id,
                        data={
                            "phase_id": next_phase.tracker_id,
                            "saga_id": saga.tracker_id,
                            "phase_number": next_phase.number,
                            "phase_name": next_phase.name,
                            "owner_id": owner_id,
                        },
                    )
                )

            # Phase unlock may unblock new issues — trigger auto-continue
            await self._try_auto_continue(owner_id, saga.tracker_id)

        return True

    async def handle_workflow_completion(
        self,
        tracker_id: str,
        owner_id: str,
    ) -> bool:
        """Finalize a workflow-managed run and trigger phase progression.

        Some workflow-backed saga runs handle implementation, review, merge, and
        tracker updates internally inside a ravn_flock session. When that
        workflow reaches an authoritative terminal node, Ting still needs to
        project the run as merged and unlock the next phase.
        """
        adapters = await self._tracker_factory.for_owner(owner_id)
        if not adapters:
            logger.info(
                "Workflow completion skipped for run %s: no tracker adapters for owner %s",
                tracker_id,
                owner_id[:8],
            )
            return False

        tracker = adapters[0]
        saga = await tracker.get_saga_for_run(tracker_id)

        await tracker.update_run_progress(tracker_id, status=RunStatus.MERGED)

        phase_gate_unlocked = await self._check_phase_gate(
            tracker,
            tracker_id,
            owner_id,
            saga=saga,
        )

        if saga is not None:
            await self._try_auto_continue(owner_id, saga.tracker_id)

        logger.info(
            "Workflow completion finalized for run %s (phase_gate_unlocked=%s)",
            tracker_id,
            phase_gate_unlocked,
        )
        return phase_gate_unlocked

    async def handle_run_failure(
        self,
        tracker_id: str,
        owner_id: str,
        *,
        reason: str,
    ) -> bool:
        """Project a terminal run failure and refill any newly freed slot.

        When Ting reconciles a stale or failed backing session, the run becomes
        terminal without going through the normal merge/review transitions. The
        dispatcher still needs to notice that a slot has opened and advance the
        next ready saga run automatically.
        """
        adapters = await self._tracker_factory.for_owner(owner_id)
        if not adapters:
            logger.info(
                "Run failure handling skipped for run %s: no tracker adapters for owner %s",
                tracker_id,
                owner_id[:8],
            )
            return False

        tracker = adapters[0]
        await tracker.update_run_progress(
            tracker_id,
            status=RunStatus.FAILED,
            reason=reason,
        )

        try:
            await tracker.update_run_state(tracker_id, RunStatus.FAILED)
        except Exception:
            logger.warning(
                "Failed to set tracker issue %s to FAILED after session failure",
                tracker_id,
                exc_info=True,
            )

        saga = await tracker.get_saga_for_run(tracker_id)
        if saga is not None:
            await self._try_auto_continue(owner_id, saga.tracker_id)

        logger.info(
            "Run failure finalized for %s (saga=%s)",
            tracker_id,
            saga.tracker_id if saga is not None else "unknown",
        )
        return saga is not None

    async def _sync_saga_phase_projection(
        self,
        *,
        saga: Saga,
        current_phase: object,
        next_phase: object | None,
    ) -> None:
        """Keep Ting's persisted phase/saga projection aligned with tracker progress."""
        if self._saga_repo is None:
            return

        persisted_phases = await self._saga_repo.get_phases_by_saga(saga.id)
        current_tracker_id = str(getattr(current_phase, "tracker_id", "") or "").strip()
        next_tracker_id = ""
        if next_phase is not None:
            next_tracker_id = str(getattr(next_phase, "tracker_id", "") or "").strip()

        if not persisted_phases:
            saga_status = SagaStatus.ACTIVE if next_tracker_id else SagaStatus.COMPLETE
            if saga.status != saga_status:
                await self._saga_repo.update_saga_status(saga.id, saga_status)
            return

        for persisted in persisted_phases:
            if (
                persisted.tracker_id == current_tracker_id
                and persisted.status != PhaseStatus.COMPLETE
            ):
                await self._saga_repo.save_phase(
                    Phase(
                        id=persisted.id,
                        saga_id=persisted.saga_id,
                        tracker_id=persisted.tracker_id,
                        number=persisted.number,
                        name=persisted.name,
                        status=PhaseStatus.COMPLETE,
                        confidence=persisted.confidence,
                    )
                )
            elif (
                next_tracker_id
                and persisted.tracker_id == next_tracker_id
                and persisted.status != PhaseStatus.ACTIVE
            ):
                await self._saga_repo.save_phase(
                    Phase(
                        id=persisted.id,
                        saga_id=persisted.saga_id,
                        tracker_id=persisted.tracker_id,
                        number=persisted.number,
                        name=persisted.name,
                        status=PhaseStatus.ACTIVE,
                        confidence=persisted.confidence,
                    )
                )

        saga_status = SagaStatus.ACTIVE if next_tracker_id else SagaStatus.COMPLETE
        if saga.status != saga_status:
            await self._saga_repo.update_saga_status(saga.id, saga_status)

    # -- Auto-continue --

    async def _try_auto_continue(self, owner_id: str, saga_tracker_id: str) -> None:
        """Delegate auto-continue to DispatchService if available."""
        if self._dispatch_service is None:
            return
        try:
            await self._dispatch_service.try_auto_continue(owner_id, saga_tracker_id)
        except Exception:
            logger.warning(
                "Auto-continue failed for owner %s (saga=%s)",
                owner_id[:8],
                saga_tracker_id,
                exc_info=True,
            )

    # -- Event emission --

    async def _emit_state_changed(
        self,
        run: Run,
        *,
        owner_id: str,
        action: str,
        saga_tracker_id: str | None = None,
    ) -> None:
        if self._event_bus is None:
            return
        data: dict[str, object] = {
            "run_id": str(run.id),
            "status": run.status.value,
            "confidence": run.confidence,
            "action": action,
            "tracker_id": run.tracker_id,
        }
        if saga_tracker_id is not None:
            data["saga_tracker_id"] = saga_tracker_id
        await self._event_bus.emit(
            TingEvent(
                event="run.state_changed",
                owner_id=owner_id,
                data=data,
            )
        )
