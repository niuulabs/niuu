"""Automated review engine — quality scoring and feedback loop for completed runs.

When a run enters REVIEW (detected by the watcher), this engine evaluates:
  1. PR status (mergeable? conflicts?)
  2. CI status (passed? failed? pending?)
  3. Scope breach (declared_files vs actual diff)
  4. Confidence scoring based on signals
  5. (Optional) LLM-powered reviewer session for deeper code review

When reviewer sessions are enabled, the engine spawns a reviewer session via
Volundr and tracks it. The reviewer session is detected as complete by the
ActivitySubscriber (via SSE idle events), which calls back into the engine's
``handle_reviewer_completion`` method with the chronicle summary. The engine
parses the reviewer's structured output, blends its confidence score, sends
feedback to the working session, and proceeds with the auto-approve / retry /
escalate decision.

The engine then decides: auto-approve, auto-retry, or escalate to human review.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from niuu.ports.integrations import IntegrationRepository
from ting.adapters.ravn_dispatcher import RavnDispatcher
from ting.config import ReviewConfig
from ting.domain.models import (
    ConfidenceEvent,
    ConfidenceEventType,
    Phase,
    PhaseStatus,
    PRStatus,
    RavnOutcome,
    Run,
    RunStatus,
    Saga,
    SagaStatus,
    validate_transition,
)
from ting.domain.services.dispatch_service import DispatchService
from ting.domain.services.reviewer_session import (
    ReviewerSessionService,
    parse_reviewer_response,
)
from ting.domain.services.session_transcript import attach_session_transcript
from ting.ports.dispatcher_repository import DispatcherRepository
from ting.ports.event_bus import EventBusPort, TingEvent
from ting.ports.git import GitPort
from ting.ports.saga_repository import SagaRepository
from ting.ports.tracker import TrackerFactory, TrackerPort
from ting.ports.volundr import VolundrFactory

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReviewDecision:
    """Outcome of the automated review engine evaluation."""

    run: Run
    action: str  # "auto_approved", "retried", "escalated", "failed", "reviewer_pending"
    reason: str
    phase_gate_unlocked: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(
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


def detect_scope_breach(
    declared_files: list[str],
    changed_files: list[str],
    threshold: float,
) -> bool:
    """Return True if the fraction of undeclared changed files exceeds the threshold."""
    if not changed_files:
        return False

    declared_set = set(declared_files)
    undeclared = [f for f in changed_files if f not in declared_set]
    ratio = len(undeclared) / len(changed_files)
    return ratio > threshold


def _is_authoritative_workflow_approval(outcome: RavnOutcome) -> bool:
    """Return True when a deterministic workflow stop-node approved the work.

    This lets the mesh/runtime stay generic: worker nodes emit canonical
    outcomes, the deterministic stop node decides the join rule, and Ting can
    trust that final approval without re-applying the old confidence gate.
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
    """Autonomous review engine that scores and acts on runs in REVIEW state.

    Called by the watcher after a run transitions to REVIEW. Gathers signals,
    scores confidence, and decides whether to auto-approve, auto-retry, or
    escalate.

    When reviewer sessions are enabled, the engine spawns a reviewer session
    and tracks the mapping from reviewer_session_id → run_tracker_id. The
    ActivitySubscriber detects reviewer completion via SSE idle events and
    calls ``handle_reviewer_completion`` to close the loop.
    """

    def __init__(
        self,
        tracker_factory: TrackerFactory,
        volundr_factory: VolundrFactory,
        git: GitPort,
        review_config: ReviewConfig,
        event_bus: EventBusPort | None = None,
        reviewer_service: ReviewerSessionService | None = None,
        integration_repo: IntegrationRepository | None = None,
        dispatcher_repo: DispatcherRepository | None = None,
        dispatch_service: DispatchService | None = None,
        ravn_dispatcher: RavnDispatcher | None = None,
        saga_repo: SagaRepository | None = None,
    ) -> None:
        self._tracker_factory = tracker_factory
        self._volundr_factory = volundr_factory
        self._git = git
        self._cfg = review_config
        self._event_bus = event_bus
        self._reviewer = reviewer_service
        self._integration_repo = integration_repo
        self._dispatcher_repo = dispatcher_repo
        self._dispatch_service = dispatch_service
        self._ravn = ravn_dispatcher
        self._saga_repo = saga_repo
        self._task: asyncio.Task[None] | None = None
        self._processed: set[str] = set()
        # Maps reviewer_session_id → (run_tracker_id, owner_id)
        self._reviewer_sessions: dict[str, tuple[str, str]] = {}

    @property
    def running(self) -> bool:
        return self._task is not None

    def get_reviewer_run(self, session_id: str) -> tuple[str, str] | None:
        """Look up the run tracker_id and owner_id for a reviewer session.

        Returns (tracker_id, owner_id) if the session is a tracked reviewer,
        None otherwise.
        """
        return self._reviewer_sessions.get(session_id)

    async def handle_reviewer_failure(self, session_id: str, reason: str) -> None:
        """Handle a reviewer session lifecycle failure.

        Triggered when the reviewer session never produces output because
        provisioning failed (e.g. ``Max concurrent sessions reached``) or
        the session crashed early. Without this hook the run sits in
        REVIEW indefinitely with a phantom reviewer that will never
        complete. Marks the run as REVIEW-pending no longer (transitions
        it back to a state that the autonomous flow can act on) and
        records a confidence event so the failure is visible in the audit
        trail.
        """
        mapping = self._reviewer_sessions.pop(session_id, None)
        if mapping is None:
            logger.debug("Reviewer session %s not tracked — ignoring failure", session_id)
            return

        tracker_id, owner_id = mapping
        logger.warning(
            "Reviewer session %s failed for run %s (reason=%s) — marking run for retry",
            session_id,
            tracker_id,
            reason,
        )
        adapters = await self._tracker_factory.for_owner(owner_id)
        if not adapters:
            logger.warning(
                "No tracker adapter for owner %s — cannot recover reviewer failure",
                owner_id[:8],
            )
            return
        tracker = adapters[0]
        try:
            run = await tracker.get_run(tracker_id)
        except Exception:
            logger.warning(
                "Could not fetch run %s while handling reviewer failure",
                tracker_id,
                exc_info=True,
            )
            return
        # Drop the dangling reviewer_session_id so a future review attempt
        # gets a fresh slot, then push the run into FAILED so the user can
        # /retry it (or the autonomous path can pick it up). FAILED is the
        # safer default than auto-respawning here, which could thrash if
        # the cap is genuinely full.
        updated = await tracker.update_run_progress(
            tracker_id,
            status=RunStatus.FAILED,
            reviewer_session_id="",
            reason=f"Reviewer session failed: {reason}",
        )
        try:
            await tracker.update_run_state(tracker_id, RunStatus.FAILED)
        except Exception:
            logger.warning(
                "Failed to set tracker issue %s to FAILED after reviewer failure",
                tracker_id,
                exc_info=True,
            )
        await self._emit_state_changed(updated or run, owner_id=owner_id, action="reviewer_failed")

    async def handle_reviewer_completion(self, session_id: str, reviewer_output: str) -> None:
        """Handle a reviewer session idle event (called by ActivitySubscriber).

        The reviewer may go idle multiple times during the review loop
        (e.g. after sending feedback to the working session). Only act
        when the output contains a structured JSON assessment — otherwise
        it's an intermediate idle and we skip.
        """
        mapping = self._reviewer_sessions.get(session_id)
        if mapping is None:
            logger.warning("Reviewer session %s not tracked — ignoring completion", session_id)
            return

        tracker_id, owner_id = mapping

        # Only process if the reviewer produced a JSON assessment
        result = parse_reviewer_response(reviewer_output)
        if result is None:
            logger.info(
                "Reviewer session %s idle without JSON assessment — intermediate, skipping",
                session_id,
            )
            return

        trackers = await self._tracker_factory.for_owner(owner_id)
        if not trackers:
            raise RuntimeError(f"No tracker for owner {owner_id[:8]}")
        tracker = trackers[0]

        run = await tracker.get_run(tracker_id)
        if run.status != RunStatus.REVIEW:
            logger.info(
                "Run %s no longer in REVIEW (status=%s) — skipping reviewer result",
                tracker_id,
                run.status,
            )
            self._reviewer_sessions.pop(session_id, None)
            return

        # Confidence is whatever the reviewer says — no blending
        score = result.confidence
        delta = score - run.confidence
        event = _make_event(run.id, ConfidenceEventType.REVIEWER_SCORE, delta, run.confidence)
        await tracker.add_confidence_event(tracker_id, event)
        await tracker.update_run_progress(tracker_id, confidence=score)
        await self._emit_confidence_updated(run.id, event)

        logger.info(
            "Reviewer session %s result (round %d): confidence=%.2f approved=%s issues=%d",
            session_id,
            run.review_round,
            result.confidence,
            result.approved,
            len(result.findings),
        )

        # If approved with no issues → check reviewer's own confidence
        if result.approved and not result.findings:
            self._reviewer_sessions.pop(session_id, None)
            if result.confidence >= self._cfg.auto_approve_threshold:
                decision = await self._handle_auto_approve(
                    tracker, tracker_id, owner_id, run, score
                )
            elif run.review_round >= self._cfg.max_review_rounds:
                decision = await self._handle_escalation(tracker, tracker_id, owner_id, run, score)
            else:
                # Low confidence but approved — let the loop continue
                new_round = run.review_round + 1
                await tracker.update_run_progress(tracker_id, review_round=new_round)
                self._reviewer_sessions[session_id] = (tracker_id, owner_id)
                logger.info(
                    "Reviewer approved but low confidence (%.2f < %.2f) — round %d/%d, continuing",
                    result.confidence,
                    self._cfg.auto_approve_threshold,
                    new_round,
                    self._cfg.max_review_rounds,
                )
                return
            logger.info(
                "Post-reviewer decision for %s: %s (reason=%s)",
                tracker_id,
                decision.action,
                decision.reason,
            )
            return

        # Issues found — the reviewer is driving the loop directly with
        # the working session. Ting just updates the round counter and
        # waits for the next idle event with a JSON assessment.
        new_round = run.review_round + 1
        await tracker.update_run_progress(tracker_id, review_round=new_round)

        if new_round >= self._cfg.max_review_rounds:
            # Max rounds exhausted — escalate
            self._reviewer_sessions.pop(session_id, None)
            decision = await self._handle_escalation(tracker, tracker_id, owner_id, run, score)
            logger.info(
                "Max review rounds (%d) reached for %s — %s (reason=%s)",
                self._cfg.max_review_rounds,
                tracker_id,
                decision.action,
                decision.reason,
            )
            return

        logger.info(
            "Review round %d/%d for %s: %d issues, reviewer driving loop",
            new_round,
            self._cfg.max_review_rounds,
            tracker_id,
            len(result.findings),
        )

    async def start(self) -> None:
        """Subscribe to the event bus and react to runs entering REVIEW.

        Rebuilds the in-memory reviewer session mapping from the database
        so that reviewer completions are handled after a restart.
        """
        await self._rebuild_reviewer_sessions()
        if self._event_bus is None:
            return
        self._task = asyncio.create_task(self._listen())

    async def _rebuild_reviewer_sessions(self) -> None:
        """Rebuild _reviewer_sessions from DB.

        Queries all active dispatchers and their trackers to find runs
        in REVIEW state with a reviewer_session_id.
        """
        try:
            owner_ids = await self._dispatcher_repo.list_active_owner_ids()
        except Exception:
            logger.warning("Could not list active owners for reviewer rebuild", exc_info=True)
            return

        for owner_id in owner_ids:
            trackers = await self._tracker_factory.for_owner(owner_id)
            for tracker in trackers:
                runs = await tracker.list_runs_by_status(RunStatus.REVIEW)
                for run in runs:
                    if run.reviewer_session_id:
                        self._reviewer_sessions[run.reviewer_session_id] = (
                            run.tracker_id,
                            owner_id,
                        )
        if self._reviewer_sessions:
            logger.info(
                "Rebuilt %d reviewer session mapping(s) from database",
                len(self._reviewer_sessions),
            )

    async def stop(self) -> None:
        """Cancel the event listener task."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
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
        """Run the full review pipeline for a run in REVIEW state.

        When reviewer sessions are enabled, spawns an LLM reviewer and
        defers the final decision until the reviewer completes (via
        handle_reviewer_completion). A small spawn bonus is applied
        immediately. If the reviewer is not available, falls through
        to the signal-based decision.
        """
        trackers = await self._tracker_factory.for_owner(owner_id)
        if not trackers:
            raise ValueError(f"No tracker adapter found for owner {owner_id}")
        tracker = trackers[0]

        run = await tracker.get_run(tracker_id)
        if run.status != RunStatus.REVIEW:
            raise ValueError(f"Run {tracker_id} not in REVIEW state: {run.status}")

        # Gather signals
        pr_status = await self._fetch_pr_status(run)
        changed_files = await self._fetch_changed_files(run)
        score = run.confidence

        # Apply confidence signals
        score = await self._apply_ci_signal(tracker, tracker_id, run.id, score, pr_status)
        score = await self._apply_mergeable_signal(tracker, tracker_id, run.id, score, pr_status)
        score = await self._apply_scope_breach_signal(
            tracker, tracker_id, run.id, score, run.declared_files, changed_files
        )
        score = await self._apply_retry_penalty(tracker, tracker_id, run.id, score, run.retry_count)

        # Spawn reviewer session if enabled — defers final decision
        reviewer_spawned = await self._spawn_reviewer_session(
            tracker, tracker_id, run, owner_id, score, pr_status, changed_files
        )
        if reviewer_spawned:
            # Reviewer is running; final decision deferred to handle_reviewer_completion
            return ReviewDecision(
                run=run,
                action="reviewer_pending",
                reason="Reviewer session spawned, awaiting completion",
            )

        # Try review-arbiter ravn for nuanced judgment (falls back to imperative logic)
        if self._cfg.ravn_arbiter_enabled and self._ravn is not None:
            arbiter_decision = await self._dispatch_to_arbiter(
                tracker, tracker_id, owner_id, run, pr_status, changed_files, score
            )
            if arbiter_decision is not None:
                return arbiter_decision

        # Imperative fallback: decide based on confidence signals
        if pr_status and not pr_status.ci_passed:
            return await self._handle_ci_failure(
                tracker, tracker_id, owner_id, run, pr_status, score
            )

        if pr_status and not pr_status.mergeable:
            return await self._handle_conflict(tracker, tracker_id, owner_id, run, score)

        if score >= self._cfg.auto_approve_threshold and self._can_auto_approve(pr_status):
            return await self._handle_auto_approve(tracker, tracker_id, owner_id, run, score)

        return await self._handle_escalation(tracker, tracker_id, owner_id, run, score)

    async def handle_ravn_outcome(
        self,
        tracker_id: str,
        owner_id: str,
        outcome: RavnOutcome,
        *,
        scope_adherence_threshold: float,
    ) -> ReviewDecision:
        """Process a structured outcome from a ``ravn.task.completed`` event.

        Maps the ravn coordinator's outcome fields to :class:`ConfidenceEvent`
        entries and routes to the appropriate decision handler based on the
        explicit verdict.

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

        score = run.confidence

        if outcome.tests_passing is True:
            event = _make_event(
                run.id, ConfidenceEventType.CI_PASS, self._cfg.confidence_delta_ci_pass, score
            )
            await tracker.add_confidence_event(tracker_id, event)
            await self._emit_confidence_updated(run.id, event)
            score = event.score_after
        elif outcome.tests_passing is False:
            event = _make_event(
                run.id, ConfidenceEventType.CI_FAIL, self._cfg.confidence_delta_ci_fail, score
            )
            await tracker.add_confidence_event(tracker_id, event)
            await self._emit_confidence_updated(run.id, event)
            score = event.score_after

        if (
            outcome.scope_adherence is not None
            and outcome.scope_adherence < scope_adherence_threshold
        ):
            event = _make_event(
                run.id,
                ConfidenceEventType.SCOPE_BREACH,
                self._cfg.confidence_delta_scope_breach,
                score,
            )
            await tracker.add_confidence_event(tracker_id, event)
            await self._emit_confidence_updated(run.id, event)
            score = event.score_after

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
                    return await self._handle_auto_approve(
                        tracker, tracker_id, owner_id, run, score
                    )
                if score >= self._cfg.auto_approve_threshold:
                    return await self._handle_auto_approve(
                        tracker, tracker_id, owner_id, run, score
                    )
                return await self._handle_escalation(tracker, tracker_id, owner_id, run, score)
            case "escalate":
                return await self._handle_escalation(tracker, tracker_id, owner_id, run, score)
            case _:
                logger.warning(
                    "handle_ravn_outcome: unknown verdict %r for run %s — escalating",
                    outcome.verdict,
                    tracker_id,
                )
                return await self._handle_escalation(tracker, tracker_id, owner_id, run, score)

    # -- Signal fetchers --

    async def _fetch_pr_status(self, run: Run) -> PRStatus | None:
        if not run.pr_id:
            return None
        try:
            return await self._git.get_pr_status(run.pr_id)
        except Exception:
            logger.warning("Failed to fetch PR status for run %s (pr=%s)", run.id, run.pr_id)
            return None

    async def _fetch_changed_files(self, run: Run) -> list[str]:
        if not run.pr_id:
            return []
        try:
            return await self._git.get_pr_changed_files(run.pr_id)
        except Exception:
            logger.warning("Failed to fetch changed files for run %s", run.id)
            return []

    # -- Confidence signal application --

    async def _apply_ci_signal(
        self,
        tracker: TrackerPort,
        tracker_id: str,
        run_id: UUID,
        score: float,
        pr_status: PRStatus | None,
    ) -> float:
        if pr_status is None or pr_status.ci_passed is None:
            return score

        if pr_status.ci_passed:
            event = _make_event(
                run_id, ConfidenceEventType.CI_PASS, self._cfg.confidence_delta_ci_pass, score
            )
        else:
            event = _make_event(
                run_id, ConfidenceEventType.CI_FAIL, self._cfg.confidence_delta_ci_fail, score
            )

        await tracker.add_confidence_event(tracker_id, event)
        await self._emit_confidence_updated(run_id, event)
        return event.score_after

    async def _apply_mergeable_signal(
        self,
        tracker: TrackerPort,
        tracker_id: str,
        run_id: UUID,
        score: float,
        pr_status: PRStatus | None,
    ) -> float:
        if pr_status is None:
            return score

        if pr_status.mergeable:
            event = _make_event(
                run_id,
                ConfidenceEventType.PR_MERGEABLE,
                self._cfg.confidence_delta_mergeable,
                score,
            )
        else:
            event = _make_event(
                run_id, ConfidenceEventType.PR_CONFLICT, self._cfg.confidence_delta_conflict, score
            )

        await tracker.add_confidence_event(tracker_id, event)
        await self._emit_confidence_updated(run_id, event)
        return event.score_after

    async def _apply_scope_breach_signal(
        self,
        tracker: TrackerPort,
        tracker_id: str,
        run_id: UUID,
        score: float,
        declared_files: list[str],
        changed_files: list[str],
    ) -> float:
        if not detect_scope_breach(declared_files, changed_files, self._cfg.scope_breach_threshold):
            return score

        event = _make_event(
            run_id,
            ConfidenceEventType.SCOPE_BREACH,
            self._cfg.confidence_delta_scope_breach,
            score,
        )
        await tracker.add_confidence_event(tracker_id, event)
        await self._emit_confidence_updated(run_id, event)
        logger.info("Scope breach detected for run %s", tracker_id)
        return event.score_after

    async def _apply_retry_penalty(
        self,
        tracker: TrackerPort,
        tracker_id: str,
        run_id: UUID,
        score: float,
        retry_count: int,
    ) -> float:
        if retry_count == 0:
            return score

        delta = self._cfg.confidence_delta_retry_multiplier * retry_count
        event = _make_event(run_id, ConfidenceEventType.RETRY, delta, score)
        await tracker.add_confidence_event(tracker_id, event)
        await self._emit_confidence_updated(run_id, event)
        return event.score_after

    # -- Review-arbiter ravn dispatch --

    def _assemble_arbiter_context(
        self,
        run: Run,
        pr_status: PRStatus | None,
        changed_files: list[str],
        score: float,
    ) -> str:
        """Assemble the initiative context string for the review-arbiter persona."""
        lines: list[str] = [
            f"# Review arbiter context for run: {run.name}",
            f"Tracker ID: {run.tracker_id}",
            f"Confidence score (after signals): {score:.3f}",
            f"Auto-approve threshold: {self._cfg.auto_approve_threshold:.3f}",
            f"Retry count: {run.retry_count} / {self._cfg.max_retries}",
            "",
            "## Acceptance criteria",
        ]
        for criterion in run.acceptance_criteria:
            lines.append(f"- {criterion}")

        lines.append("")
        lines.append("## PR status")
        if pr_status is None:
            lines.append("No PR found.")
        else:
            lines.append(f"PR ID: {pr_status.pr_id}")
            lines.append(f"PR URL: {pr_status.url}")
            lines.append(f"State: {pr_status.state}")
            lines.append(f"Mergeable: {pr_status.mergeable}")
            ci_label = {True: "passed", False: "failed", None: "unknown"}[pr_status.ci_passed]
            lines.append(f"CI: {ci_label}")

        declared_set = set(run.declared_files)

        lines.append("")
        lines.append("## Declared files")
        for f in run.declared_files:
            lines.append(f"- {f}")

        lines.append("")
        lines.append("## Changed files in PR")
        if changed_files:
            for f in changed_files:
                declared = " [declared]" if f in declared_set else " [undeclared]"
                lines.append(f"- {f}{declared}")
        else:
            lines.append("No changed files available.")

        undeclared = [f for f in changed_files if f not in declared_set]
        if undeclared:
            ratio = len(undeclared) / len(changed_files) if changed_files else 0.0
            lines.append(f"\nScope breach ratio: {ratio:.1%} undeclared")

        lines.append("")
        lines.append(
            "Based on the above context, decide: approve, retry, or escalate. "
            "Produce your outcome block."
        )
        return "\n".join(lines)

    async def _dispatch_to_arbiter(
        self,
        tracker: TrackerPort,
        tracker_id: str,
        owner_id: str,
        run: Run,
        pr_status: PRStatus | None,
        changed_files: list[str],
        score: float,
    ) -> ReviewDecision | None:
        """Dispatch to the review-arbiter ravn persona.

        Returns a :class:`ReviewDecision` if the arbiter produced a valid
        outcome, or ``None`` to fall back to imperative logic.
        """
        if self._ravn is None:
            return None

        context = self._assemble_arbiter_context(run, pr_status, changed_files, score)

        try:
            outcome = await self._ravn.dispatch(
                "review-arbiter",
                context,
            )
        except Exception:
            logger.warning(
                "review-arbiter dispatch failed for run %s — using imperative fallback",
                tracker_id,
                exc_info=True,
            )
            return None

        if outcome is None:
            logger.info(
                "review-arbiter returned no outcome for run %s — using imperative fallback",
                tracker_id,
            )
            return None

        verdict = str(outcome.get("verdict", "")).lower()
        reason = str(outcome.get("reason", "review-arbiter decision"))

        logger.info(
            "review-arbiter verdict for run %s: %s (reason=%s)",
            tracker_id,
            verdict,
            reason,
        )

        match verdict:
            case "approve":
                return await self._handle_auto_approve(tracker, tracker_id, owner_id, run, score)
            case "retry":
                if run.retry_count < self._cfg.max_retries:
                    return await self._auto_retry(
                        tracker,
                        tracker_id,
                        owner_id,
                        run,
                        reason=f"review-arbiter: {reason}",
                    )
                return await self._handle_escalation(tracker, tracker_id, owner_id, run, score)
            case "escalate":
                return await self._handle_escalation(tracker, tracker_id, owner_id, run, score)
            case _:
                logger.warning(
                    "review-arbiter returned unknown verdict %r for run %s — imperative fallback",
                    verdict,
                    tracker_id,
                )
                return None

    # -- Reviewer session --

    async def _spawn_reviewer_session(
        self,
        tracker: TrackerPort,
        tracker_id: str,
        run: Run,
        owner_id: str,
        score: float,
        pr_status: PRStatus | None,
        changed_files: list[str],
    ) -> bool:
        """Spawn an LLM reviewer session and track it.

        Returns True if a reviewer was spawned (decision deferred),
        False if no reviewer was spawned (decide immediately).
        """
        if not self._cfg.reviewer_session_enabled:
            return False

        if self._reviewer is None:
            return False

        integration_ids = await self._resolve_integration_ids(owner_id)

        # Get repo/branch from the working session on Volundr
        working_session = None
        if run.session_id:
            adapters = await self._volundr_factory.for_owner(owner_id)
            for adapter in adapters:
                try:
                    working_session = await adapter.get_session(run.session_id)
                    if working_session is not None:
                        break
                except Exception:
                    continue
            if working_session is None:
                raise ValueError(
                    f"Working session {run.session_id} not found on any Volundr cluster"
                )

        session = await self._reviewer.spawn_reviewer(
            run=run,
            owner_id=owner_id,
            pr_status=pr_status,
            changed_files=changed_files,
            integration_ids=integration_ids,
            working_session=working_session,
        )
        if session is None:
            logger.warning("Reviewer session not spawned for run %s — skipping", tracker_id)
            return False

        # Persist reviewer session mapping to DB and in-memory cache
        await tracker.update_run_progress(
            tracker_id,
            reviewer_session_id=session.id,
        )
        self._reviewer_sessions[session.id] = (tracker_id, owner_id)

        logger.info(
            "Reviewer session %s spawned for run %s, awaiting completion via SSE",
            session.id,
            tracker_id,
        )

        # Record a small spawn bonus
        reviewer_delta = self._cfg.reviewer_spawn_bonus
        event = _make_event(
            run.id,
            ConfidenceEventType.REVIEWER_SCORE,
            reviewer_delta,
            score,
        )
        await tracker.add_confidence_event(tracker_id, event)
        await self._emit_confidence_updated(run.id, event)

        return True

    # -- Integration resolution --

    async def _resolve_integration_ids(self, owner_id: str) -> list[str]:
        """Resolve integration connection IDs from Volundr for the owner."""
        try:
            adapters = await self._volundr_factory.for_owner(owner_id)
            if not adapters:
                logger.error(
                    "No authenticated Volundr adapter for owner %s — "
                    "cannot resolve integrations; user must configure a CODE_FORGE integration",
                    owner_id[:8],
                )
                return []
            return await adapters[0].list_integration_ids()
        except Exception:
            logger.warning("Failed to fetch Volundr integrations for owner %s", owner_id[:8])
        return []

    # -- Decision handlers --

    def _can_auto_approve(self, pr_status: PRStatus | None) -> bool:
        if pr_status is None:
            return False
        return bool(pr_status.ci_passed and pr_status.mergeable)

    async def _handle_auto_approve(
        self,
        tracker: TrackerPort,
        tracker_id: str,
        owner_id: str,
        run: Run,
        score: float,
    ) -> ReviewDecision:
        """Auto-approve: transition REVIEW → MERGED.

        The reviewer session merges the PR itself via `gh pr merge`.
        Ting attaches the review transcript to the Linear issue and
        stops the reviewer session.
        """
        event = _make_event(
            run.id, ConfidenceEventType.AUTO_APPROVED, self._cfg.confidence_delta_approved, score
        )
        await tracker.add_confidence_event(tracker_id, event)

        validate_transition(run.status, RunStatus.MERGED)
        updated = await tracker.update_run_progress(tracker_id, status=RunStatus.MERGED)

        # Close the tracker issue (sets it to Done in Linear/Jira)
        try:
            await tracker.close_run(tracker_id)
            logger.info("Closed tracker issue %s (Done)", tracker_id)
        except Exception:
            logger.error("FAILED to close tracker issue %s after merge", tracker_id, exc_info=True)

        # Attach transcripts and stop sessions
        if run.session_id:
            await self._attach_working_transcript(tracker, tracker_id, owner_id, run)
            await self._stop_session(owner_id, run.session_id, "working session")
        if run.reviewer_session_id:
            await self._attach_review_transcript(tracker, tracker_id, owner_id, run)
            await self._stop_session(owner_id, run.reviewer_session_id, "reviewer session")

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
            reason=f"Confidence {event.score_after:.2f} >= {self._cfg.auto_approve_threshold:.2f}",
            phase_gate_unlocked=phase_gate_unlocked,
        )

    async def _attach_review_transcript(
        self,
        tracker: TrackerPort,
        tracker_id: str,
        owner_id: str,
        run: Run,
    ) -> None:
        """Fetch the reviewer's full conversation and attach as a document."""
        await attach_session_transcript(
            volundr_factory=self._volundr_factory,
            tracker=tracker,
            tracker_id=tracker_id,
            owner_id=owner_id,
            session_id=run.reviewer_session_id,
            title_prefix="Review Transcript",
            run_name=run.name,
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

    async def _handle_ci_failure(
        self,
        tracker: TrackerPort,
        tracker_id: str,
        owner_id: str,
        run: Run,
        pr_status: PRStatus,
        score: float,
    ) -> ReviewDecision:
        """CI failed: auto-retry if retries remain, otherwise FAILED + escalate."""
        if run.retry_count < self._cfg.max_retries:
            return await self._auto_retry(
                tracker,
                tracker_id,
                owner_id,
                run,
                reason=f"CI failed (attempt {run.retry_count + 1}/{self._cfg.max_retries})",
            )

        # Retries exhausted → FAILED
        validate_transition(run.status, RunStatus.FAILED)
        updated = await tracker.update_run_progress(
            tracker_id, status=RunStatus.FAILED, reason="CI failed, retries exhausted"
        )

        if run.session_id:
            await self._attach_working_transcript(tracker, tracker_id, owner_id, run)
            await self._stop_session(owner_id, run.session_id, "working session")

        await self._emit_state_changed(updated, owner_id=owner_id, action="failed")
        return ReviewDecision(
            run=updated,
            action="failed",
            reason=f"CI failed after {self._cfg.max_retries} retries",
        )

    async def _handle_conflict(
        self,
        tracker: TrackerPort,
        tracker_id: str,
        owner_id: str,
        run: Run,
        score: float,
    ) -> ReviewDecision:
        """PR has conflicts: auto-retry if retries remain, otherwise escalate."""
        if run.retry_count < self._cfg.max_retries:
            return await self._auto_retry(
                tracker,
                tracker_id,
                owner_id,
                run,
                reason=f"PR conflicts (attempt {run.retry_count + 1}/{self._cfg.max_retries})",
            )

        validate_transition(run.status, RunStatus.FAILED)
        updated = await tracker.update_run_progress(
            tracker_id, status=RunStatus.FAILED, reason="PR conflicts, retries exhausted"
        )

        if run.session_id:
            await self._attach_working_transcript(tracker, tracker_id, owner_id, run)
            await self._stop_session(owner_id, run.session_id, "working session")

        await self._emit_state_changed(updated, owner_id=owner_id, action="failed")
        return ReviewDecision(
            run=updated,
            action="failed",
            reason=f"PR conflicts after {self._cfg.max_retries} retries",
        )

    async def _handle_escalation(
        self,
        tracker: TrackerPort,
        tracker_id: str,
        owner_id: str,
        run: Run,
        score: float,
    ) -> ReviewDecision:
        """Confidence too low or conditions not met — escalate to human review."""
        validate_transition(run.status, RunStatus.ESCALATED)
        updated = await tracker.update_run_progress(tracker_id, status=RunStatus.ESCALATED)

        # Snapshot the working transcript but keep the session alive for human review
        if run.session_id:
            await self._attach_working_transcript(tracker, tracker_id, owner_id, run)

        await self._emit_state_changed(updated, owner_id=owner_id, action="escalated")
        return ReviewDecision(
            run=updated,
            action="escalated",
            reason=(
                f"Confidence {score:.2f} < {self._cfg.auto_approve_threshold:.2f},"
                " escalating to human review"
            ),
        )

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
        event = _make_event(
            run.id,
            ConfidenceEventType.RETRY,
            self._cfg.confidence_delta_retry,
            run.confidence,
        )
        await tracker.add_confidence_event(tracker_id, event)

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

    async def _emit_confidence_updated(self, run_id: UUID, event: ConfidenceEvent) -> None:
        if self._event_bus is None:
            return
        await self._event_bus.emit(
            TingEvent(
                event="confidence.updated",
                data={
                    "run_id": str(run_id),
                    "event_type": event.event_type.value,
                    "delta": event.delta,
                    "score_after": event.score_after,
                },
            )
        )
