"""Event-driven raid completion — replaces polling-based RaidWatcher.

Subscribes to Volundr's SSE stream for session_activity events, evaluates
completion signals, and transitions raids accordingly.

Uses VolundrAdapterFactory to resolve per-owner authenticated adapters —
each user's PAT (from their IntegrationConnection) authenticates the SSE
subscription to their Volundr instance.

When a ReviewEngine is provided, the subscriber also detects reviewer session
completion. If an idle session is not associated with a RUNNING raid, the
subscriber checks whether it is a tracked reviewer session and, if so, fetches
the chronicle summary and delegates to ReviewEngine.handle_reviewer_completion.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

try:
    from sleipnir.domain.catalog import tyr_raid_needs_approval as _catalog_raid_needs_approval
except ImportError:
    _catalog_raid_needs_approval = None  # type: ignore[assignment]

from tyr.config import WatcherConfig
from tyr.domain.models import Raid, RaidStatus, RavnOutcome, SessionMessage
from tyr.ports.dispatcher_repository import DispatcherRepository
from tyr.ports.event_bus import EventBusPort, TyrEvent
from tyr.ports.tracker import TrackerFactory, TrackerPort  # noqa: F401 — re-exported for consumers
from tyr.ports.volundr import ActivityEvent, VolundrFactory, VolundrPort

if TYPE_CHECKING:
    from tyr.domain.services.review_engine import ReviewEngine

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CompletionEvaluation:
    """Result of evaluating whether a raid's work is complete."""

    is_complete: bool
    signals: dict[str, bool]
    confidence: float
    pr_id: str | None = None
    pr_url: str | None = None


class SessionActivitySubscriber:
    """Subscribes to Volundr SSE and evaluates raid completion on activity events.

    Uses the VolundrAdapterFactory to resolve per-owner authenticated adapters.
    Each active owner (with RUNNING raids) gets their own SSE subscription using
    their PAT from their IntegrationConnection.
    """

    def __init__(
        self,
        volundr_factory: VolundrFactory,
        tracker_factory: TrackerFactory,
        dispatcher_repo: DispatcherRepository,
        event_bus: EventBusPort,
        config: WatcherConfig,
        review_engine: ReviewEngine | None = None,
        sleipnir_publisher: object | None = None,
        ravn_scope_adherence_threshold: float = 0.7,
    ) -> None:
        self._factory = volundr_factory
        self._tracker_factory = tracker_factory
        self._dispatcher_repo = dispatcher_repo
        self._event_bus = event_bus
        self._config = config
        self._review_engine = review_engine
        self._sleipnir_publisher = sleipnir_publisher
        self._ravn_scope_adherence_threshold = ravn_scope_adherence_threshold
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._owner_tasks: dict[str, list[asyncio.Task[None]]] = {}
        self._pending_evaluations: dict[str, asyncio.Task[None]] = {}
        # Cache per-owner adapters so we don't re-resolve on every cycle
        self._owner_adapters: dict[str, list[VolundrPort]] = {}

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        """Start the SSE subscriber background loop."""
        if not self._config.enabled:
            logger.info("Session activity subscriber disabled by configuration")
            return

        self._running = True
        self._task = asyncio.create_task(self._run(), name="activity-subscriber")
        logger.info(
            "Session activity subscriber started (idle_threshold=%.1fs)",
            self._config.idle_threshold,
        )

    async def stop(self) -> None:
        """Gracefully stop the subscriber."""
        self._running = False
        for task in self._pending_evaluations.values():
            task.cancel()
        self._pending_evaluations.clear()
        for tasks in self._owner_tasks.values():
            for task in tasks:
                task.cancel()
        self._owner_tasks.clear()
        self._owner_adapters.clear()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass  # Expected during graceful shutdown
            self._task = None
        logger.info("Session activity subscriber stopped")

    async def _run(self) -> None:
        """Main loop — discover active owners and manage per-owner SSE subscriptions."""
        while self._running:
            try:
                await self._sync_owner_subscriptions()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Failed to sync owner subscriptions")
            if self._running:
                await asyncio.sleep(self._config.reconnect_delay)

    async def _sync_owner_subscriptions(self) -> None:
        """Discover owners with active dispatchers, ensure each has SSE subs."""
        active_owners = set(await self._dispatcher_repo.list_active_owner_ids())
        logger.info(
            "Sync: active_owners=%s, existing_tasks=%s",
            active_owners,
            {
                k: [("running" if not t.done() else "done") for t in v]
                for k, v in self._owner_tasks.items()
            },
        )

        if not active_owners:
            for owner_id, tasks in list(self._owner_tasks.items()):
                for task in tasks:
                    task.cancel()
            self._owner_tasks.clear()
            self._owner_adapters.clear()
            await asyncio.sleep(self._config.reconnect_delay)
            return

        # Start subscriptions for new owners (one task per cluster)
        for owner_id in active_owners:
            existing = self._owner_tasks.get(owner_id, [])
            all_done = not existing or all(t.done() for t in existing)
            if all_done:
                adapters = await self._resolve_owner_adapters(owner_id)
                tasks = []
                for idx, adapter in enumerate(adapters):
                    task = asyncio.create_task(
                        self._adapter_subscription_loop(owner_id, adapter),
                        name=f"sse-{owner_id[:8]}-{idx}",
                    )
                    tasks.append(task)
                self._owner_tasks[owner_id] = tasks

        # Cancel subscriptions for owners with no more active dispatchers
        for owner_id in list(self._owner_tasks):
            if owner_id not in active_owners:
                for task in self._owner_tasks.pop(owner_id):
                    task.cancel()
                self._owner_adapters.pop(owner_id, None)

        # Wait before re-syncing
        await asyncio.sleep(self._config.reconnect_delay)

    async def _adapter_subscription_loop(self, owner_id: str, volundr: VolundrPort) -> None:
        """Maintain an SSE subscription for a single owner-cluster pair."""
        while self._running:
            try:
                await self._reconcile_running_raids(owner_id, volundr)
                logger.info("SSE subscription started for owner %s", owner_id[:8])
                async for event in volundr.subscribe_activity():
                    if not self._running:
                        break
                    await self._on_activity_event(event, volundr, owner_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "SSE subscription failed for owner %s, reconnecting",
                    owner_id[:8],
                )
                # One cluster failed — cancel ALL tasks for this owner so the
                # sync cycle recreates them with fresh adapters.
                self._cancel_owner_tasks(owner_id)
                return

            if self._running:
                await asyncio.sleep(self._config.reconnect_delay)

    async def _reconcile_running_raids(self, owner_id: str, volundr: VolundrPort) -> None:
        """Fail tracker-side RUNNING raids whose backing Forge sessions are already terminal.

        This closes the restart gap where Volundr has reconciled dead local
        sessions to ``stopped`` before Tyr's SSE subscription comes online.
        Without this pass, ``try_auto_continue()`` can believe all owner slots
        are still occupied by phantom RUNNING raids.
        """
        trackers = await self._tracker_factory.for_owner(owner_id)
        for tracker in trackers:
            running_raids = await tracker.list_raids_by_status(RaidStatus.RUNNING)
            for raid in running_raids:
                if not raid.session_id:
                    continue
                session = await volundr.get_session(raid.session_id)
                if session is None:
                    await self._handle_failure(
                        raid,
                        tracker,
                        owner_id,
                        reason="Session not found during subscriber startup",
                    )
                    continue
                if session.status in self._FAILED_STATUSES:
                    await self._handle_failure(
                        raid,
                        tracker,
                        owner_id,
                        reason=f"Session {session.status}",
                    )

    def _cancel_owner_tasks(self, owner_id: str) -> None:
        """Cancel all SSE tasks for *owner_id* and clear the adapter cache."""
        self._owner_adapters.pop(owner_id, None)
        for task in self._owner_tasks.pop(owner_id, []):
            if not task.done():
                task.cancel()

    async def _resolve_owner_adapters(self, owner_id: str) -> list[VolundrPort]:
        """Resolve and cache per-owner Volundr adapters (one per cluster)."""
        if owner_id in self._owner_adapters:
            return self._owner_adapters[owner_id]

        adapters = await self._factory.for_owner(owner_id)
        if not adapters:
            logger.error(
                "No authenticated Volundr adapter for owner %s — "
                "user must configure a CODE_FORGE integration with a valid PAT",
                owner_id[:8],
            )
            return []
        self._owner_adapters[owner_id] = adapters
        return adapters

    _FAILED_STATUSES: frozenset[str] = frozenset({"stopped", "failed"})

    async def _on_activity_event(
        self, event: ActivityEvent, volundr: VolundrPort, owner_id: str
    ) -> None:
        """Handle a single activity or session lifecycle event from the SSE stream."""
        logger.info(
            "Activity event: session=%s state=%s status=%s meta=%s",
            event.session_id[:8] if event.session_id else "?",
            event.state,
            event.session_status or "-",
            event.metadata,
        )
        if event.session_status in self._FAILED_STATUSES:
            await self._on_session_failed(event, volundr, owner_id)
            return

        if await self._maybe_handle_help_needed(event, owner_id):
            return

        if event.state != "idle":
            pending = self._pending_evaluations.pop(event.session_id, None)
            if pending is not None:
                pending.cancel()
            return

        if event.session_id in self._pending_evaluations:
            return

        task = asyncio.create_task(
            self._debounced_evaluation(event, volundr, owner_id),
            name=f"eval-{event.session_id}",
        )
        task.add_done_callback(self._on_eval_done)
        self._pending_evaluations[event.session_id] = task

    @staticmethod
    def _on_eval_done(task: asyncio.Task) -> None:  # type: ignore[type-arg]
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("Debounced evaluation failed: %s", exc, exc_info=exc)

    async def _debounced_evaluation(
        self, event: ActivityEvent, volundr: VolundrPort, owner_id: str
    ) -> None:
        """Wait for the debounce delay, then evaluate completion."""
        try:
            await asyncio.sleep(self._config.completion_check_delay)
        except asyncio.CancelledError:
            return
        finally:
            self._pending_evaluations.pop(event.session_id, None)

        raid, tracker = await self._find_raid_for_session(event.session_id, owner_id)
        if raid is None or tracker is None:
            # Check if this is a reviewer session completing
            await self._try_handle_reviewer_completion(event.session_id, volundr)
            return

        # Working session idle during an active review loop (round >= 1) —
        # the reviewer drives the loop directly. Tyr does not re-trigger.
        # Round 0 means the working session just completed initial work and
        # should proceed to normal completion → REVIEW transition.
        if raid.status == RaidStatus.REVIEW and raid.review_round >= 1:
            logger.info(
                "Working session %s idle during review loop (round %d) — reviewer drives",
                event.session_id[:8],
                raid.review_round,
            )
            return

        session = await volundr.get_session(event.session_id)
        if session is None:
            await self._handle_failure(raid, tracker, owner_id, reason="Session not found")
            return
        if session.status in ("stopped", "failed"):
            await self._handle_failure(raid, tracker, owner_id, reason=f"Session {session.status}")
            return

        if session.workload_type == "ravn_flock":
            if await self._try_handle_flock_completion(event, raid, owner_id):
                return
            logger.info(
                "Skipping idle completion evaluation for flock session %s; awaiting ravn outcome",
                event.session_id[:8],
            )
            return

        if not await self._is_owner_active(owner_id):
            return

        completion = await self._evaluate_completion(raid, volundr, event.metadata)
        if not completion.is_complete:
            return

        await self._handle_completion(raid, tracker, volundr, owner_id, completion)

    async def _find_raid_for_session(
        self, session_id: str, owner_id: str
    ) -> tuple[Raid | None, TrackerPort | None]:
        """Find the raid and tracker for a given session_id.

        Accepts any non-terminal raid state — a session may still be
        active even if Tyr moved the raid to QUEUED (retry) or REVIEW.
        """
        terminal = {RaidStatus.MERGED, RaidStatus.FAILED, RaidStatus.ESCALATED}
        trackers = await self._tracker_factory.for_owner(owner_id)
        for tracker in trackers:
            raid = await tracker.get_raid_by_session(session_id)
            if raid and raid.status not in terminal:
                return raid, tracker
        return None, None

    async def _is_owner_active(self, owner_id: str) -> bool:
        """Check if the owner's dispatcher is running."""
        state = await self._dispatcher_repo.get_or_create(owner_id)
        return state.running

    async def _evaluate_completion(
        self, raid: Raid, volundr: VolundrPort, metadata: dict
    ) -> CompletionEvaluation:
        """Evaluate whether a session's work is complete based on signals."""
        signals: dict[str, bool] = {}

        signals["session_idle"] = True
        signals["has_turns"] = metadata.get("turn_count", 0) >= 1

        signals["pr_exists"] = False
        signals["ci_passed"] = False
        pr_id: str | None = None
        pr_url: str | None = None
        try:
            pr = await volundr.get_pr_status(raid.session_id)
            signals["pr_exists"] = bool(pr.pr_id)
            signals["ci_passed"] = bool(pr.ci_passed)
            if pr.pr_id:
                pr_id = pr.pr_id
                pr_url = pr.url
        except Exception:
            logger.debug("PR status check failed for session %s", raid.session_id, exc_info=True)

        # Signal 3: Extended idle (metadata.duration_seconds as proxy)
        idle_seconds = metadata.get("duration_seconds", 0)
        signals["extended_idle"] = idle_seconds > self._config.idle_threshold

        # Minimum requirement: session idle + has processed turns
        is_complete = signals["session_idle"] and signals["has_turns"]

        # Apply require_pr / require_ci constraints
        if self._config.require_pr and not signals["pr_exists"]:
            is_complete = False
        if self._config.require_ci and not signals["ci_passed"]:
            is_complete = False

        # Calculate confidence based on configurable signal strength
        cfg = self._config
        confidence = cfg.confidence_base if is_complete else 0.0
        if signals["pr_exists"]:
            confidence += cfg.confidence_pr_bonus
        if signals["ci_passed"]:
            confidence += cfg.confidence_ci_bonus
        if signals["extended_idle"]:
            confidence += cfg.confidence_idle_bonus

        logger.info(
            "Completion evaluation: session=%s is_complete=%s confidence=%.2f signals=%s",
            raid.session_id,
            is_complete,
            min(confidence, 1.0),
            signals,
        )

        return CompletionEvaluation(
            is_complete=is_complete,
            signals=signals,
            confidence=min(confidence, 1.0),
            pr_id=pr_id,
            pr_url=pr_url,
        )

    async def _maybe_handle_help_needed(self, event: ActivityEvent, owner_id: str) -> bool:
        payload = _help_needed_payload(event.metadata)
        if payload is None:
            return False

        raid, tracker = await self._find_raid_for_session(event.session_id, owner_id)
        if raid is None or tracker is None:
            logger.warning(
                "Help-needed activity received for unknown session %s",
                event.session_id[:8] if event.session_id else "?",
            )
            return False

        if raid.session_id:
            payload["session_id"] = raid.session_id

        serialized = json.dumps(payload, default=str, sort_keys=True)
        messages = await tracker.get_session_messages(raid.tracker_id)
        if _is_duplicate_help_request(messages, serialized):
            return True

        now = datetime.now(UTC)
        await tracker.save_session_message(
            SessionMessage(
                id=uuid4(),
                raid_id=raid.id,
                session_id=raid.session_id or str(payload.get("session_id") or ""),
                content=serialized,
                sender="help_needed",
                created_at=now,
            )
        )

        saga = await tracker.get_saga_for_raid(raid.tracker_id)
        await self._event_bus.emit(
            TyrEvent(
                event="raid.feedback_requested",
                owner_id=owner_id,
                data={
                    "owner_id": owner_id,
                    "raid_id": str(raid.id),
                    "raid_name": raid.name,
                    "tracker_id": raid.tracker_id,
                    "session_id": raid.session_id or str(payload.get("session_id") or ""),
                    "saga_id": str(saga.id) if saga is not None else "",
                    "saga_name": saga.name if saga is not None else "",
                    "summary": payload.get("summary", ""),
                    "reason": payload.get("reason", ""),
                    "recommendation": payload.get("recommendation", ""),
                    "ui_path": f"/tyr/sagas/{saga.id}" if saga is not None else "",
                },
            )
        )
        logger.info(
            "Recorded help-needed request for raid=%s session=%s",
            raid.tracker_id,
            event.session_id[:8] if event.session_id else "?",
        )
        return True

    async def _try_handle_flock_completion(
        self,
        event: ActivityEvent,
        raid: Raid,
        owner_id: str,
    ) -> bool:
        """Route authoritative local flock completion through ReviewEngine.

        In co-hosted development runs the canonical local path is:
        Skuld room outcome -> Volundr session activity SSE -> Tyr.
        """
        if self._review_engine is None:
            return False

        metadata = event.metadata
        if metadata.get("completion_source") != "ravn_flock":
            return False

        structured = metadata.get("structured_outcome")
        if not isinstance(structured, dict) or not structured:
            logger.warning(
                "Flock completion metadata missing structured_outcome for session %s",
                event.session_id[:8],
            )
            return False
        structured_payload = (
            structured.get("outcome") if isinstance(structured.get("outcome"), dict) else structured
        )
        if not isinstance(structured_payload, dict) or not structured_payload:
            logger.warning(
                "Flock completion metadata missing nested outcome payload for session %s",
                event.session_id[:8],
            )
            return False

        outcome = RavnOutcome(
            verdict=str(structured_payload.get("verdict") or "escalate"),
            tests_passing=_coerce_bool(structured_payload.get("tests_passing")),
            scope_adherence=_coerce_float(structured_payload.get("scope_adherence")),
            pr_url=_coerce_str(structured_payload.get("pr_url")),
            files_changed=_coerce_str_list(
                structured_payload.get("files_changed") or metadata.get("files_changed")
            ),
            summary=_coerce_str(structured_payload.get("summary")) or "",
            authoritative=str(
                metadata.get("completion_peer_id") or ""
            ).startswith("workflow-stop:"),
            checks=[
                dict(item)
                for item in structured_payload.get("checks", [])
                if isinstance(item, dict)
            ],
        )

        logger.info(
            "Handling local flock completion for session %s raid=%s verdict=%s",
            event.session_id[:8],
            raid.tracker_id,
            outcome.verdict,
        )
        await self._review_engine.handle_ravn_outcome(
            raid.tracker_id,
            owner_id,
            outcome,
            scope_adherence_threshold=self._ravn_scope_adherence_threshold,
        )
        return True

    async def _handle_completion(
        self,
        raid: Raid,
        tracker: TrackerPort,
        volundr: VolundrPort,
        owner_id: str,
        evaluation: CompletionEvaluation | None = None,
    ) -> None:
        """Mark a raid as complete (REVIEW state).

        Fetches a chronicle summary from Volundr when chronicle_on_complete is
        enabled in config — this captures the session narrative alongside the
        PR metadata for human reviewers.
        """
        pr_id = evaluation.pr_id if evaluation else None
        pr_url = evaluation.pr_url if evaluation else None

        chronicle_summary: str | None = None
        if self._config.chronicle_on_complete and raid.session_id:
            try:
                chronicle_summary = await volundr.get_chronicle_summary(raid.session_id)
            except Exception:
                logger.warning(
                    "Failed to fetch chronicle for session %s", raid.session_id, exc_info=True
                )

        await tracker.update_raid_progress(
            raid.tracker_id,
            status=RaidStatus.REVIEW,
            pr_url=pr_url,
            pr_id=pr_id,
            chronicle_summary=chronicle_summary,
        )

        # Set tracker issue to In Review
        try:
            await tracker.update_raid_state(raid.tracker_id, RaidStatus.REVIEW)
            logger.info("Set tracker issue %s to In Review", raid.tracker_id)
        except Exception:
            logger.error(
                "FAILED to set tracker issue %s to In Review", raid.tracker_id, exc_info=True
            )

        await self._emit_state_changed(raid, owner_id, "REVIEW", pr_id=pr_id, pr_url=pr_url)

        # NIU-582: emit tyr.raid.needs_approval to Sleipnir catalog (best-effort)
        if self._sleipnir_publisher is not None and _catalog_raid_needs_approval is not None:
            try:
                description = f"PR {pr_url or pr_id or 'ready'} — {raid.tracker_id}"
                _event = _catalog_raid_needs_approval(
                    raid_id=raid.tracker_id,
                    saga_id=str(raid.phase_id),
                    description=description,
                    source="tyr:activity_subscriber",
                    correlation_id=raid.session_id or raid.tracker_id,
                )
                await self._sleipnir_publisher.publish(_event)
            except Exception:
                logger.warning("Failed to emit tyr.raid.needs_approval; continuing.", exc_info=True)

        logger.info(
            "Session %s completed (tracker=%s, pr=%s, chronicle=%s)",
            raid.session_id,
            raid.tracker_id,
            pr_id or "none",
            "yes" if chronicle_summary else "no",
        )
    async def _on_session_failed(
        self, event: ActivityEvent, volundr: VolundrPort, owner_id: str
    ) -> None:
        """Handle a session stopped/failed lifecycle event."""
        pending = self._pending_evaluations.pop(event.session_id, None)
        if pending is not None:
            pending.cancel()

        raid, tracker = await self._find_raid_for_session(event.session_id, owner_id)
        if raid is None or tracker is None:
            # Not a working-session failure. The session may still be a
            # tracked reviewer that died early (e.g. provisioning hit the
            # max-concurrent cap before the reviewer could start). Without
            # this hop the raid would sit in REVIEW indefinitely with a
            # phantom reviewer_session_id.
            await self._try_handle_reviewer_failure(
                event.session_id, event.session_status or "failed"
            )
            return

        await self._handle_failure(
            raid, tracker, owner_id, reason=f"Session {event.session_status}"
        )

    async def _try_handle_reviewer_failure(self, session_id: str, reason: str) -> None:
        """If the failed session is a tracked reviewer, hand off to review_engine."""
        if self._review_engine is None:
            return
        try:
            await self._review_engine.handle_reviewer_failure(session_id, reason)
        except Exception:
            logger.warning(
                "Failed to handle reviewer failure for session %s",
                session_id,
                exc_info=True,
            )

    async def _handle_failure(
        self,
        raid: Raid,
        tracker: TrackerPort,
        owner_id: str,
        *,
        reason: str,
    ) -> None:
        """Mark a raid as failed."""
        await tracker.update_raid_progress(
            raid.tracker_id,
            status=RaidStatus.FAILED,
            reason=reason,
        )

        await self._emit_state_changed(raid, owner_id, "FAILED")
        logger.info(
            "Session %s failed (tracker=%s, reason=%s)",
            raid.session_id,
            raid.tracker_id,
            reason,
        )

    async def _try_handle_reviewer_completion(self, session_id: str, volundr: VolundrPort) -> None:
        """If the session is a tracked reviewer, fetch its output and delegate."""
        if self._review_engine is None:
            return

        mapping = self._review_engine.get_reviewer_raid(session_id)
        if mapping is None:
            return

        try:
            reviewer_output = await volundr.get_last_assistant_message(session_id)
        except Exception:
            logger.error(
                "Failed to fetch reviewer output for session %s",
                session_id,
                exc_info=True,
            )
            raise

        try:
            await self._review_engine.handle_reviewer_completion(session_id, reviewer_output)
        except Exception:
            logger.warning(
                "Failed to handle reviewer completion for session %s",
                session_id,
                exc_info=True,
            )

    async def _emit_state_changed(
        self,
        raid: Raid,
        owner_id: str,
        status: str,
        *,
        pr_id: str | None = None,
        pr_url: str | None = None,
    ) -> None:
        """Emit a raid.state_changed event via the event bus."""
        await self._event_bus.emit(
            TyrEvent(
                event="raid.state_changed",
                owner_id=owner_id,
                data={
                    "session_id": raid.session_id,
                    "owner_id": owner_id,
                    "tracker_id": raid.tracker_id,
                    "url": raid.url,
                    "status": status,
                    "pr_id": pr_id,
                    "pr_url": pr_url,
                },
            )
        )


def _coerce_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    return None


def _coerce_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError:
            return None
    return None


def _coerce_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_str_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _help_needed_payload(metadata: dict) -> dict[str, object] | None:
    raw = metadata.get("help_needed")
    if not isinstance(raw, dict):
        return None
    attempted = raw.get("attempted")
    context = raw.get("context")
    return {
        "summary": str(raw.get("summary") or "Agent requested human feedback."),
        "reason": str(raw.get("reason") or "needs_context"),
        "attempted": (
            [str(item).strip() for item in attempted if str(item).strip()]
            if isinstance(attempted, list)
            else []
        ),
        "recommendation": str(raw.get("recommendation") or ""),
        "context": context if isinstance(context, dict) else {},
        "persona": str(raw.get("persona") or ""),
        "target_peer_id": str(raw.get("target_peer_id") or ""),
        "session_id": str(raw.get("session_id") or ""),
    }


def _is_duplicate_help_request(messages: list[SessionMessage], serialized_payload: str) -> bool:
    latest_help = next(
        (message for message in reversed(messages) if message.sender == "help_needed"),
        None,
    )
    if latest_help is None or latest_help.content != serialized_payload:
        return False
    latest_user = next(
        (message for message in reversed(messages) if message.sender == "user"),
        None,
    )
    if latest_user is None:
        return True
    return latest_user.created_at <= latest_help.created_at
