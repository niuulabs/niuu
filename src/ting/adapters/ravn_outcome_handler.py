"""RavnOutcomeHandler — Sleipnir subscriber for Ravn completion events.

Consumes canonical ``ravn.session.ended`` events from Volundr-managed flock
sessions plus compatibility ``ravn.task.completed`` events from direct Ravn
flows, then routes normalized outcomes through the
:class:`~ting.domain.services.review_engine.ReviewEngine` decision pipeline.

Lifecycle::

    handler = RavnOutcomeHandler(...)
    await handler.start()   # subscribe to ravn.session.ended + ravn.task.completed
    # ... application runs ...
    await handler.stop()    # unsubscribe; clean up

**Coexistence with ActivitySubscriber**

Both paths remain active.  ``ActivitySubscriber`` (SSE-based) is the fallback
for standard Claude Code sessions.  ``RavnOutcomeHandler`` is the primary path
for flock sessions.  When both fire for the same run the explicit ravn outcome
takes precedence — ``ReviewEngine.handle_ravn_outcome`` is idempotent and skips
runs that are no longer in RUNNING or REVIEW state.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from sleipnir.domain.events import SleipnirEvent
from sleipnir.ports.events import SleipnirSubscriber, Subscription
from ting.domain.models import RavnOutcome
from ting.domain.services.review_engine import ReviewEngine
from ting.ports.tracker import TrackerFactory, TrackerPort

logger = logging.getLogger(__name__)

RAVN_TASK_COMPLETED = "ravn.task.completed"
RAVN_SESSION_ENDED = "ravn.session.ended"
_SUBSCRIBED_EVENT_TYPES = [RAVN_SESSION_ENDED, RAVN_TASK_COMPLETED]


class RavnOutcomeHandler:
    """Subscribes to Ravn completion events and routes outcomes through ReviewEngine.

    Parameters
    ----------
    subscriber:
        Sleipnir subscriber used to receive events.
    tracker_factory:
        Factory for resolving per-owner tracker adapters.
    review_engine:
        The ReviewEngine instance to delegate decisions to.
    owner_id:
        Owner ID used when looking up tracker adapters.
    """

    def __init__(
        self,
        *,
        subscriber: SleipnirSubscriber,
        tracker_factory: TrackerFactory,
        review_engine: ReviewEngine,
        owner_id: str,
    ) -> None:
        self._subscriber = subscriber
        self._tracker_factory = tracker_factory
        self._review_engine = review_engine
        self._owner_id = owner_id

        self._subscription: Subscription | None = None
        self._pending_tasks: set[asyncio.Task[None]] = set()

    @property
    def is_running(self) -> bool:
        return self._subscription is not None

    async def start(self) -> None:
        """Subscribe to Ravn completion events on Sleipnir."""
        if self._subscription is not None:
            return
        self._subscription = await self._subscriber.subscribe(
            _SUBSCRIBED_EVENT_TYPES, self._handle_event
        )
        logger.info(
            "RavnOutcomeHandler started: subscribed to %s",
            ", ".join(_SUBSCRIBED_EVENT_TYPES),
        )

    async def stop(self) -> None:
        """Unsubscribe and cancel in-flight tasks."""
        if self._subscription is not None:
            await self._subscription.unsubscribe()
            self._subscription = None

        for task in list(self._pending_tasks):
            task.cancel()
        if self._pending_tasks:
            await asyncio.gather(*self._pending_tasks, return_exceptions=True)
        self._pending_tasks.clear()

        logger.info("RavnOutcomeHandler stopped")

    # ------------------------------------------------------------------
    # Internal event handling
    # ------------------------------------------------------------------

    async def _handle_event(self, event: SleipnirEvent) -> None:
        task = asyncio.create_task(
            self._process_event(event),
            name=f"ravn-outcome:{event.event_id}",
        )
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    async def _process_event(self, event: SleipnirEvent) -> None:
        run = await self._resolve_run(event)
        if run is None:
            payload_session_id = str(event.payload.get("session_id") or "").strip()
            payload_run_id = str(event.payload.get("run_id") or "").strip()
            payload_tracker_id = str(
                event.payload.get("tracker_issue_id") or event.payload.get("tracker_id") or ""
            ).strip()
            if not (
                payload_run_id or payload_tracker_id or payload_session_id or event.correlation_id
            ):
                logger.warning(
                    "RavnOutcomeHandler: event %s (%s) has no usable "
                    "run/session identifiers — dropping",
                    event.event_id,
                    event.event_type,
                )
            else:
                logger.warning(
                    "RavnOutcomeHandler: no run for event=%s type=%s "
                    "(run_id=%s tracker_issue_id=%s session_id=%s correlation_id=%s) — dropping",
                    event.event_id,
                    event.event_type,
                    payload_run_id or "-",
                    payload_tracker_id or "-",
                    payload_session_id or "-",
                    event.correlation_id or "-",
                )
            return

        outcome = _extract_outcome(event.payload)

        logger.info(
            "RavnOutcomeHandler: processing %s for run %s "
            "(verdict=%s, tests_passing=%s, session=%s)",
            event.event_type,
            run.tracker_id,
            outcome.verdict,
            outcome.tests_passing,
            run.session_id or event.correlation_id or event.payload.get("session_id") or "-",
        )

        try:
            decision = await self._review_engine.handle_ravn_outcome(
                run.tracker_id,
                self._owner_id,
                outcome,
            )
            logger.info(
                "RavnOutcomeHandler: run %s → %s (reason=%s)",
                run.tracker_id,
                decision.action,
                decision.reason,
            )
        except Exception:
            logger.exception(
                "RavnOutcomeHandler: failed to process outcome for run %s",
                run.tracker_id,
            )

    async def _resolve_run(self, event: SleipnirEvent):
        payload = event.payload
        trackers = await self._tracker_factory.for_owner(self._owner_id)
        if not trackers:
            return None

        run_id = str(payload.get("run_id") or "").strip()
        tracker_issue_id = str(
            payload.get("tracker_issue_id") or payload.get("tracker_id") or ""
        ).strip()
        session_candidates = [
            str(payload.get("session_id") or "").strip(),
            str(event.correlation_id or "").strip(),
        ]

        for tracker in trackers:
            run = await self._resolve_run_by_explicit_id(tracker, run_id)
            if run is not None:
                return run

            if tracker_issue_id:
                try:
                    return await tracker.get_run(tracker_issue_id)
                except Exception:
                    logger.debug(
                        "RavnOutcomeHandler: tracker_issue_id lookup failed for %s",
                        tracker_issue_id,
                        exc_info=True,
                    )

            for session_id in session_candidates:
                if not session_id:
                    continue
                run = await tracker.get_run_by_session(session_id)
                if run is not None:
                    return run

        return None

    async def _resolve_run_by_explicit_id(self, tracker: TrackerPort, run_id: str):
        if not run_id:
            return None

        try:
            parsed = UUID(run_id)
        except ValueError:
            parsed = None

        if parsed is not None:
            run = await tracker.get_run_by_id(parsed)
            if run is not None:
                return run

        try:
            return await tracker.get_run(run_id)
        except Exception:
            logger.debug(
                "RavnOutcomeHandler: tracker run_id lookup failed for %s",
                run_id,
                exc_info=True,
            )
            return None


def _extract_outcome(payload: dict) -> RavnOutcome:
    """Parse a Ravn completion payload into a typed :class:`RavnOutcome`."""
    structured = payload.get("structured_outcome")
    structured_payload = structured if isinstance(structured, dict) else {}

    verdict = structured_payload.get("verdict")
    if verdict is None:
        verdict = payload.get("verdict")

    tests_passing = structured_payload.get("tests_passing")
    if tests_passing is None:
        tests_passing = payload.get("tests_passing")

    scope_adherence = structured_payload.get("scope_adherence")
    if scope_adherence is None:
        scope_adherence = payload.get("scope_adherence")

    pr_url = structured_payload.get("pr_url")
    if pr_url is None:
        pr_url = payload.get("pr_url")

    files_changed = structured_payload.get("files_changed")
    if files_changed is None:
        files_changed = payload.get("files_changed")

    summary = structured_payload.get("summary")
    if summary is None:
        summary = payload.get("summary")

    checks = structured_payload.get("checks")
    if checks is None:
        checks = payload.get("checks")

    authoritative = structured_payload.get("authoritative")
    if authoritative is None:
        authoritative = payload.get("authoritative")

    return RavnOutcome(
        verdict=str(verdict or "escalate"),
        tests_passing=tests_passing,
        scope_adherence=scope_adherence,
        pr_url=pr_url,
        files_changed=list(files_changed or []),
        summary=str(summary or ""),
        authoritative=bool(authoritative),
        checks=[dict(item) for item in (checks or []) if isinstance(item, dict)],
    )
