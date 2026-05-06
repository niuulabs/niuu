"""RavnOutcomeHandler — Sleipnir subscriber for ``ravn.task.completed`` events.

Consumes structured task outcomes published by the ravn flock coordinator and
routes them through the :class:`~tyr.domain.services.review_engine.ReviewEngine`
decision pipeline.

Lifecycle::

    handler = RavnOutcomeHandler(...)
    await handler.start()   # subscribe to ravn.task.completed
    # ... application runs ...
    await handler.stop()    # unsubscribe; clean up

**Correlation**

The ``SleipnirEvent.correlation_id`` carries the Volundr session ID.  The
handler uses this to look up the corresponding raid via
``TrackerPort.get_raid_by_session``.

**Coexistence with ActivitySubscriber**

Both paths remain active.  ``ActivitySubscriber`` (SSE-based) is the fallback
for standard Claude Code sessions.  ``RavnOutcomeHandler`` is the primary path
for flock sessions.  When both fire for the same raid the explicit ravn outcome
takes precedence — ``ReviewEngine.handle_ravn_outcome`` is idempotent and skips
raids that are no longer in RUNNING or REVIEW state.
"""

from __future__ import annotations

import asyncio
import logging

from sleipnir.domain.events import SleipnirEvent
from sleipnir.ports.events import SleipnirSubscriber, Subscription
from tyr.domain.models import RavnOutcome
from tyr.domain.services.review_engine import ReviewEngine
from tyr.ports.tracker import TrackerFactory

logger = logging.getLogger(__name__)

RAVN_TASK_COMPLETED = "ravn.task.completed"


class RavnOutcomeHandler:
    """Subscribes to ``ravn.task.completed`` events and routes outcomes through ReviewEngine.

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
    scope_adherence_threshold:
        ``scope_adherence`` values below this threshold trigger a
        :attr:`~tyr.domain.models.ConfidenceEventType.SCOPE_BREACH` signal.
    """

    def __init__(
        self,
        *,
        subscriber: SleipnirSubscriber,
        tracker_factory: TrackerFactory,
        review_engine: ReviewEngine,
        owner_id: str,
        scope_adherence_threshold: float = 0.7,
    ) -> None:
        self._subscriber = subscriber
        self._tracker_factory = tracker_factory
        self._review_engine = review_engine
        self._owner_id = owner_id
        self._scope_adherence_threshold = scope_adherence_threshold

        self._subscription: Subscription | None = None
        self._pending_tasks: set[asyncio.Task[None]] = set()

    @property
    def is_running(self) -> bool:
        return self._subscription is not None

    async def start(self) -> None:
        """Subscribe to ``ravn.task.completed`` on Sleipnir."""
        if self._subscription is not None:
            return
        self._subscription = await self._subscriber.subscribe(
            [RAVN_TASK_COMPLETED], self._handle_event
        )
        logger.info("RavnOutcomeHandler started: subscribed to %s", RAVN_TASK_COMPLETED)

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
        session_id = event.correlation_id
        if not session_id:
            logger.warning(
                "RavnOutcomeHandler: event %s has no correlation_id — dropping",
                event.event_id,
            )
            return

        outcome = _extract_outcome(event.payload)

        raid = None
        trackers = await self._tracker_factory.for_owner(self._owner_id)
        for tracker in trackers:
            raid = await tracker.get_raid_by_session(session_id)
            if raid is not None:
                break

        if raid is None:
            logger.warning(
                "RavnOutcomeHandler: no raid for session_id=%s (event=%s) — dropping",
                session_id,
                event.event_id,
            )
            return

        logger.info(
            "RavnOutcomeHandler: processing outcome for raid %s "
            "(verdict=%s, tests_passing=%s, session=%s)",
            raid.tracker_id,
            outcome.verdict,
            outcome.tests_passing,
            session_id,
        )

        try:
            decision = await self._review_engine.handle_ravn_outcome(
                raid.tracker_id,
                self._owner_id,
                outcome,
                scope_adherence_threshold=self._scope_adherence_threshold,
            )
            logger.info(
                "RavnOutcomeHandler: raid %s → %s (reason=%s)",
                raid.tracker_id,
                decision.action,
                decision.reason,
            )
        except Exception:
            logger.exception(
                "RavnOutcomeHandler: failed to process outcome for raid %s",
                raid.tracker_id,
            )


def _extract_outcome(payload: dict) -> RavnOutcome:
    """Parse a ``ravn.task.completed`` payload into a typed :class:`RavnOutcome`."""
    return RavnOutcome(
        verdict=payload.get("verdict", "escalate"),
        tests_passing=payload.get("tests_passing"),
        scope_adherence=payload.get("scope_adherence"),
        pr_url=payload.get("pr_url"),
        files_changed=list(payload.get("files_changed") or []),
        summary=str(payload.get("summary") or ""),
    )
