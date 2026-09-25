"""Event-driven run completion — replaces polling-based RunWatcher.

Subscribes to Volundr's SSE stream for session_activity events, evaluates
completion signals, and transitions runs accordingly.

Uses VolundrAdapterFactory to resolve per-owner authenticated adapters —
each user's PAT (from their IntegrationConnection) authenticates the SSE
subscription to their Volundr instance.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

try:
    from sleipnir.domain.catalog import ting_run_needs_approval as _catalog_run_needs_approval
except ImportError:
    _catalog_run_needs_approval = None  # type: ignore[assignment]

from ting.config import WatcherConfig
from ting.domain.models import RavnOutcome, Run, RunStatus, SessionMessage
from ting.ports.activity_projection import ActivityProjector
from ting.ports.dispatcher_repository import DispatcherRepository
from ting.ports.event_bus import EventBusPort, TingEvent
from ting.ports.tracker import TrackerFactory, TrackerPort  # noqa: F401 — re-exported for consumers
from ting.ports.volundr import ActivityEvent, VolundrFactory, VolundrPort

if TYPE_CHECKING:
    from ting.domain.services.review_engine import ReviewEngine
    from ting.domain.services.workflow_campaign_projector import WorkflowCampaignProjector

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CompletionEvaluation:
    """Result of evaluating whether a run's work is complete."""

    is_complete: bool
    signals: dict[str, bool]
    pr_id: str | None = None
    pr_url: str | None = None


@dataclass
class _ClusterBackoffState:
    """Per-(owner, cluster) SSE reconnect backoff state.

    Cleared entirely once a cluster reconnects successfully or its
    subscription is torn down (cluster removed, owner removed) — see
    ``SessionActivitySubscriber._on_cluster_connected`` and
    ``_cancel_owner_tasks``.
    """

    consecutive_failures: int = 0


class SessionActivitySubscriber:
    """Subscribes to Volundr SSE and evaluates run completion on activity events.

    Uses the VolundrAdapterFactory to resolve per-owner authenticated adapters.
    Each active owner (with RUNNING runs) gets their own SSE subscription using
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
        workflow_campaign_projector: WorkflowCampaignProjector | None = None,
        attested_review_projector: ActivityProjector | None = None,
    ) -> None:
        self._factory = volundr_factory
        self._tracker_factory = tracker_factory
        self._dispatcher_repo = dispatcher_repo
        self._event_bus = event_bus
        self._config = config
        self._review_engine = review_engine
        self._sleipnir_publisher = sleipnir_publisher
        self._workflow_campaign_projector = workflow_campaign_projector
        self._attested_review_projector = attested_review_projector
        self._running = False
        self._task: asyncio.Task[None] | None = None
        # owner_id -> {cluster_key: task} — one isolated task per (owner, cluster).
        self._owner_tasks: dict[str, dict[str, asyncio.Task[None]]] = {}
        self._pending_evaluations: dict[str, asyncio.Task[None]] = {}
        self._completed_workflow_sessions: set[str] = set()
        # Most recently resolved adapters per owner (informational; re-resolved
        # every sync cycle so a cluster added/removed from the owner's Guild
        # registration is picked up without disturbing other clusters).
        self._owner_adapters: dict[str, list[VolundrPort]] = {}
        # (owner_id, cluster_key) -> backoff state, isolated per cluster so one
        # cluster's outage never affects another's reconnect cadence.
        self._cluster_backoff: dict[tuple[str, str], _ClusterBackoffState] = {}

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
        self._completed_workflow_sessions.clear()
        for tasks in self._owner_tasks.values():
            for task in tasks.values():
                task.cancel()
        self._owner_tasks.clear()
        self._owner_adapters.clear()
        self._cluster_backoff.clear()
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
        """Discover owners with active work, ensure each has SSE subscriptions."""
        active_owners = set(await self._dispatcher_repo.list_active_owner_ids())
        if self._workflow_campaign_projector is not None:
            active_owners.update(await self._workflow_campaign_projector.list_active_owner_ids())
        logger.info(
            "Sync: active_owners=%s, existing_clusters=%s",
            active_owners,
            {owner: list(clusters) for owner, clusters in self._owner_tasks.items()},
        )

        if not active_owners:
            for owner_id in list(self._owner_tasks):
                self._cancel_owner_tasks(owner_id)
            await asyncio.sleep(self._config.reconnect_delay)
            return

        # Ensure a subscription set for every active owner. A brand-new owner
        # (no cluster tasks yet) gets a workflow-campaign reconcile pass, same
        # as before; an owner already being tracked does not get a repeat
        # reconcile just because this cycle ran again.
        for owner_id in active_owners:
            is_new_owner = owner_id not in self._owner_tasks
            if is_new_owner and self._workflow_campaign_projector is not None:
                await self._workflow_campaign_projector.reconcile_owner(owner_id)
            await self._sync_owner_clusters(owner_id)

        # Cancel subscriptions for owners with no more active dispatchers
        for owner_id in list(self._owner_tasks):
            if owner_id not in active_owners:
                self._cancel_owner_tasks(owner_id)

        # Wait before re-syncing
        await asyncio.sleep(self._config.reconnect_delay)

    async def _sync_owner_clusters(self, owner_id: str) -> None:
        """Ensure *owner_id* has exactly one SSE task per currently registered cluster.

        Resolved fresh every cycle so a cluster added to or removed from the
        owner's Guild registration is picked up. Diffed against the running
        tasks so an unaffected cluster's in-flight subscription (and its
        backoff state) is never disturbed — only a genuinely new or removed
        cluster gets a task change.
        """
        adapters = await self._resolve_owner_adapters(owner_id)
        desired = {self._cluster_key(adapter): adapter for adapter in adapters}
        clusters = self._owner_tasks.setdefault(owner_id, {})

        for key, adapter in desired.items():
            existing_task = clusters.get(key)
            if existing_task is not None and not existing_task.done():
                continue
            clusters[key] = asyncio.create_task(
                self._adapter_subscription_loop(owner_id, adapter),
                name=f"sse-{owner_id[:8]}-{key}",
            )

        for key in list(clusters):
            if key in desired:
                continue
            task = clusters.pop(key)
            if not task.done():
                task.cancel()
            self._cluster_backoff.pop((owner_id, key), None)

    async def _adapter_subscription_loop(self, owner_id: str, volundr: VolundrPort) -> None:
        """Maintain an SSE subscription for a single owner-cluster pair.

        Failures are isolated to this cluster: they retry here with their own
        exponential backoff (``WatcherConfig.reconnect_*``) and never cancel
        another cluster's task for the same owner. The failure itself is
        never swallowed (see .claude/rules/no-fallbacks.md) — it is logged
        loudly on the first occurrence and on every recovery, and with one
        line (no repeated traceback) on each continued attempt.
        """
        key = self._cluster_key(volundr)
        label = self._cluster_label(volundr)
        while self._running:
            try:
                await self._reconcile_running_runs(owner_id, volundr)
                logger.info("SSE subscription started for owner %s cluster=%s", owner_id[:8], label)
                async for event in volundr.subscribe_activity():
                    if not self._running:
                        break
                    await self._on_activity_event(event, volundr, owner_id)
                self._on_cluster_connected(owner_id, key, label)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                delay = self._on_cluster_failure(owner_id, key, label, exc)
                if not self._running:
                    return
                await asyncio.sleep(delay)
                continue

            if self._running:
                await asyncio.sleep(self._config.reconnect_delay)

    async def _reconcile_running_runs(self, owner_id: str, volundr: VolundrPort) -> None:
        """Fail tracker-side RUNNING runs whose backing Forge sessions are already terminal.

        This closes the restart gap where Volundr has reconciled dead local
        sessions to ``stopped`` before Ting's SSE subscription comes online.
        Without this pass, ``try_auto_continue()`` can believe all owner slots
        are still occupied by phantom RUNNING runs.
        """
        trackers = await self._tracker_factory.for_owner(owner_id)
        for tracker in trackers:
            running_runs = await tracker.list_runs_by_status(RunStatus.RUNNING)
            for run in running_runs:
                if not run.session_id:
                    continue
                session = await volundr.get_session(run.session_id)
                if session is None:
                    await self._handle_failure(
                        run,
                        tracker,
                        owner_id,
                        reason="Session not found during subscriber startup",
                    )
                    continue
                if session.status in self._FAILED_STATUSES:
                    await self._handle_failure(
                        run,
                        tracker,
                        owner_id,
                        reason=f"Session {session.status}",
                    )

    def _cancel_owner_tasks(self, owner_id: str) -> None:
        """Cancel all SSE tasks for *owner_id* and clear its adapter/backoff state."""
        self._owner_adapters.pop(owner_id, None)
        for key, task in self._owner_tasks.pop(owner_id, {}).items():
            if not task.done():
                task.cancel()
            self._cluster_backoff.pop((owner_id, key), None)

    async def _resolve_owner_adapters(self, owner_id: str) -> list[VolundrPort]:
        """Resolve the owner's current Volundr adapters (one per registered cluster).

        Called every sync cycle (no caching) so ``_sync_owner_clusters`` can
        diff against the live set and pick up a cluster the owner added or
        removed from their Guild registration.
        """
        adapters = await self._factory.for_owner(owner_id)
        self._owner_adapters[owner_id] = adapters
        if not adapters:
            logger.error(
                "No authenticated Volundr adapter for owner %s — "
                "user must configure a CODE_FORGE integration with a valid PAT",
                owner_id[:8],
            )
        return adapters

    @staticmethod
    def _cluster_key(volundr: VolundrPort) -> str:
        """Stable per-cluster key used to track tasks and backoff state."""
        return volundr.target_id or volundr.name or "unknown"

    @staticmethod
    def _cluster_label(volundr: VolundrPort) -> str:
        """Human-readable cluster identity for log lines (name/slug + base URL)."""
        name = volundr.name or volundr.target_id or "unknown"
        base_url = volundr.base_url
        if base_url:
            return f"{name} ({base_url})"
        return name

    def _compute_backoff_delay(self, attempt: int) -> float:
        """Exponential backoff with a cap and downward jitter, all config-driven.

        ``attempt`` is the 1-based consecutive-failure count. The delay grows
        as ``reconnect_initial_delay * reconnect_backoff_multiplier **
        (attempt - 1)``, capped at ``reconnect_max_delay``, then reduced by up
        to ``reconnect_jitter`` fraction so many clusters failing together
        don't retry in lockstep.
        """
        cfg = self._config
        exponent = max(attempt - 1, 0)
        base = min(
            cfg.reconnect_initial_delay * (cfg.reconnect_backoff_multiplier**exponent),
            cfg.reconnect_max_delay,
        )
        jitter_fraction = min(max(cfg.reconnect_jitter, 0.0), 1.0)
        if jitter_fraction == 0.0:
            return base
        return base * (1 - random.uniform(0.0, jitter_fraction))

    def _on_cluster_connected(self, owner_id: str, key: str, label: str) -> None:
        """Reset a cluster's backoff state after it reconnects successfully.

        A no-op unless the cluster had previously failed — a healthy cluster
        must not get a recovery log line on every ordinary reconnect cycle.
        """
        state = self._cluster_backoff.pop((owner_id, key), None)
        if state is None or state.consecutive_failures == 0:
            return
        logger.info(
            "SSE subscription for owner %s cluster=%s recovered after %d failed attempt(s)",
            owner_id[:8],
            label,
            state.consecutive_failures,
        )

    def _on_cluster_failure(self, owner_id: str, key: str, label: str, exc: Exception) -> float:
        """Record a cluster's SSE failure, log it, and return the next retry delay.

        The traceback is logged once, on the first failure of a run of
        failures. Every subsequent attempt logs one WARNING line — exception
        class, message, attempt number, next delay — with no repeated
        traceback, so a down cluster stays loud without flooding logs (see
        .claude/rules/no-fallbacks.md).
        """
        state = self._cluster_backoff.setdefault((owner_id, key), _ClusterBackoffState())
        state.consecutive_failures += 1
        delay = self._compute_backoff_delay(state.consecutive_failures)
        if state.consecutive_failures == 1:
            logger.error(
                "SSE subscription failed for owner %s cluster=%s — retrying with "
                "backoff (attempt 1, next in %.1fs)",
                owner_id[:8],
                label,
                delay,
                exc_info=True,
            )
        else:
            logger.warning(
                "SSE subscription still failing for owner %s cluster=%s: %s: %s "
                "(attempt %d, next in %.1fs)",
                owner_id[:8],
                label,
                type(exc).__name__,
                exc,
                state.consecutive_failures,
                delay,
            )
        return delay

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
        terminal_event = event.state == "error" or bool(event.session_status)
        if self._attested_review_projector is not None:
            await self._attested_review_projector.handle_activity(event, owner_id)
        if (
            terminal_event
            and self._workflow_campaign_projector is not None
            and await self._workflow_campaign_projector.handle_activity(
                event, owner_id, connection_id=volundr.target_id or None
            )
        ):
            return
        if await self._maybe_handle_help_needed(
            event, owner_id, connection_id=volundr.target_id or None
        ):
            return
        if (
            not terminal_event
            and self._workflow_campaign_projector is not None
            and await self._workflow_campaign_projector.handle_activity(
                event, owner_id, connection_id=volundr.target_id or None
            )
        ):
            return
        if await self._try_handle_authoritative_completion(event, volundr, owner_id):
            return

        if event.session_status in self._FAILED_STATUSES:
            if event.session_id in self._completed_workflow_sessions:
                logger.info(
                    "Ignoring terminal session status %s for already-completed workflow session %s",
                    event.session_status,
                    event.session_id[:8],
                )
                self._completed_workflow_sessions.discard(event.session_id)
                return
            await self._on_session_failed(event, volundr, owner_id)
            return

        if event.state == "error":
            await self._on_session_failed(event, volundr, owner_id)
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

    async def _try_handle_authoritative_completion(
        self,
        event: ActivityEvent,
        volundr: VolundrPort,
        owner_id: str,
    ) -> bool:
        """Process authoritative workflow terminal outcomes immediately.

        Local flock sessions emit a deterministic terminal outcome right before
        Volundr stops the session. If Ting waits on the normal idle debounce,
        the subsequent ``stopped`` lifecycle event can arrive first and
        incorrectly downgrade a successful workflow run to FAILED/Canceled.
        """
        if event.state != "idle" or self._review_engine is None:
            return False
        if not _is_authoritative_completion_metadata(event.metadata):
            return False

        pending = self._pending_evaluations.pop(event.session_id, None)
        if pending is not None:
            pending.cancel()

        run, _tracker = await self._find_run_for_session(event.session_id, owner_id)
        if run is None:
            return False

        session = await volundr.get_session(event.session_id)
        if session is None or session.workload_type != "ravn_flock":
            return False

        handled = await self._try_handle_flock_completion(event, run, owner_id)
        if handled:
            self._completed_workflow_sessions.add(event.session_id)
        return handled

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

        run, tracker = await self._find_run_for_session(event.session_id, owner_id)
        if run is None or tracker is None:
            return

        session = await volundr.get_session(event.session_id)
        if session is None:
            await self._handle_failure(run, tracker, owner_id, reason="Session not found")
            return
        if session.status in ("stopped", "failed"):
            await self._handle_failure(run, tracker, owner_id, reason=f"Session {session.status}")
            return

        if session.workload_type == "ravn_flock":
            if await self._try_handle_flock_completion(event, run, owner_id):
                return
            logger.info(
                "Skipping idle completion evaluation for flock session %s; awaiting ravn outcome",
                event.session_id[:8],
            )
            return

        if not await self._is_owner_active(owner_id):
            return

        completion = await self._evaluate_completion(run, volundr, event.metadata)
        if not completion.is_complete:
            return

        await self._handle_completion(run, tracker, volundr, owner_id, completion)

    async def _find_run_for_session(
        self, session_id: str, owner_id: str
    ) -> tuple[Run | None, TrackerPort | None]:
        """Find the run and tracker for a given session_id.

        Accepts any non-terminal run state — a session may still be
        active even if Ting moved the run to QUEUED (retry) or REVIEW.
        """
        terminal = {RunStatus.MERGED, RunStatus.FAILED, RunStatus.ESCALATED}
        trackers = await self._tracker_factory.for_owner(owner_id)
        for tracker in trackers:
            run = await tracker.get_run_by_session(session_id)
            if run and run.status not in terminal:
                return run, tracker
        return None, None

    async def _is_owner_active(self, owner_id: str) -> bool:
        """Check if the owner's dispatcher is running."""
        state = await self._dispatcher_repo.get_or_create(owner_id)
        return state.running

    async def _evaluate_completion(
        self, run: Run, volundr: VolundrPort, metadata: dict
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
            pr = await volundr.get_pr_status(run.session_id)
            signals["pr_exists"] = bool(pr.pr_id)
            signals["ci_passed"] = bool(pr.ci_passed)
            if pr.pr_id:
                pr_id = pr.pr_id
                pr_url = pr.url
        except Exception:
            logger.debug("PR status check failed for session %s", run.session_id, exc_info=True)

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

        logger.info(
            "Completion evaluation: session=%s is_complete=%s signals=%s",
            run.session_id,
            is_complete,
            signals,
        )

        return CompletionEvaluation(
            is_complete=is_complete,
            signals=signals,
            pr_id=pr_id,
            pr_url=pr_url,
        )

    async def _maybe_handle_help_needed(
        self,
        event: ActivityEvent,
        owner_id: str,
        *,
        connection_id: str | None = None,
    ) -> bool:
        payload = _help_needed_payload(event.metadata)
        if payload is None:
            return False

        run, tracker = await self._find_run_for_session(event.session_id, owner_id)
        if run is None or tracker is None:
            if await self._maybe_record_workflow_campaign_help_needed(
                event, payload, owner_id, connection_id=connection_id
            ):
                return True
            logger.warning(
                "Help-needed activity received for unknown session %s",
                event.session_id[:8] if event.session_id else "?",
            )
            return False

        if run.session_id:
            payload["session_id"] = run.session_id

        serialized = json.dumps(payload, default=str, sort_keys=True)
        messages = await tracker.get_session_messages(run.tracker_id)
        if _is_duplicate_help_request(messages, serialized):
            return True

        now = datetime.now(UTC)
        await tracker.save_session_message(
            SessionMessage(
                id=uuid4(),
                run_id=run.id,
                session_id=run.session_id or str(payload.get("session_id") or ""),
                content=serialized,
                sender="help_needed",
                created_at=now,
            )
        )

        saga = await tracker.get_saga_for_run(run.tracker_id)
        await self._event_bus.emit(
            TingEvent(
                event="run.feedback_requested",
                owner_id=owner_id,
                data={
                    "owner_id": owner_id,
                    "run_id": str(run.id),
                    "run_name": run.name,
                    "tracker_id": run.tracker_id,
                    "session_id": run.session_id or str(payload.get("session_id") or ""),
                    "saga_id": str(saga.id) if saga is not None else "",
                    "saga_name": saga.name if saga is not None else "",
                    "summary": payload.get("summary", ""),
                    "reason": payload.get("reason", ""),
                    "recommendation": payload.get("recommendation", ""),
                    "ui_path": f"/ting/sagas/{saga.id}" if saga is not None else "",
                },
            )
        )
        logger.info(
            "Recorded help-needed request for run=%s session=%s",
            run.tracker_id,
            event.session_id[:8] if event.session_id else "?",
        )
        return True

    async def _maybe_record_workflow_campaign_help_needed(
        self,
        event: ActivityEvent,
        payload: dict[str, object],
        owner_id: str,
        *,
        connection_id: str | None = None,
    ) -> bool:
        if self._workflow_campaign_projector is None:
            return False

        session_id = _help_needed_session_id(payload) or event.session_id
        if not session_id:
            return False

        gate = _workflow_gate_from_help_needed(payload)
        return await self._workflow_campaign_projector.record_help_needed(
            payload,
            owner_id,
            session_id=session_id,
            gate=gate,
            connection_id=connection_id,
        )

    async def _try_handle_flock_completion(
        self,
        event: ActivityEvent,
        run: Run,
        owner_id: str,
    ) -> bool:
        """Route authoritative local flock completion through ReviewEngine.

        In co-hosted development runs the canonical local path is:
        Skuld room outcome -> Volundr session activity SSE -> Ting.
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
            authoritative=str(metadata.get("completion_peer_id") or "").startswith(
                "workflow-stop:"
            ),
            checks=[
                dict(item)
                for item in structured_payload.get("checks", [])
                if isinstance(item, dict)
            ],
        )
        if outcome.authoritative:
            logger.info(
                "Handling workflow-terminal flock completion for session %s run=%s verdict=%s",
                event.session_id[:8],
                run.tracker_id,
                outcome.verdict,
            )
            await self._review_engine.handle_workflow_completion(
                run.tracker_id,
                owner_id,
            )
            return True

        logger.info(
            "Handling local flock completion for session %s run=%s verdict=%s",
            event.session_id[:8],
            run.tracker_id,
            outcome.verdict,
        )
        await self._review_engine.handle_ravn_outcome(
            run.tracker_id,
            owner_id,
            outcome,
        )
        return True

    async def _handle_completion(
        self,
        run: Run,
        tracker: TrackerPort,
        volundr: VolundrPort,
        owner_id: str,
        evaluation: CompletionEvaluation | None = None,
    ) -> None:
        """Mark a run as complete (REVIEW state).

        Fetches a chronicle summary from Volundr when chronicle_on_complete is
        enabled in config — this captures the session narrative alongside the
        PR metadata for human reviewers.
        """
        pr_id = evaluation.pr_id if evaluation else None
        pr_url = evaluation.pr_url if evaluation else None

        chronicle_summary: str | None = None
        if self._config.chronicle_on_complete and run.session_id:
            try:
                chronicle_summary = await volundr.get_chronicle_summary(run.session_id)
            except Exception:
                logger.warning(
                    "Failed to fetch chronicle for session %s", run.session_id, exc_info=True
                )

        await tracker.update_run_progress(
            run.tracker_id,
            status=RunStatus.REVIEW,
            pr_url=pr_url,
            pr_id=pr_id,
            chronicle_summary=chronicle_summary,
        )

        # Set tracker issue to In Review
        try:
            await tracker.update_run_state(run.tracker_id, RunStatus.REVIEW)
            logger.info("Set tracker issue %s to In Review", run.tracker_id)
        except Exception:
            logger.error(
                "FAILED to set tracker issue %s to In Review", run.tracker_id, exc_info=True
            )

        await self._emit_state_changed(run, owner_id, "REVIEW", pr_id=pr_id, pr_url=pr_url)

        # NIU-582: emit ting.run.needs_approval to Sleipnir catalog (best-effort)
        if self._sleipnir_publisher is not None and _catalog_run_needs_approval is not None:
            try:
                description = f"PR {pr_url or pr_id or 'ready'} — {run.tracker_id}"
                _event = _catalog_run_needs_approval(
                    run_id=run.tracker_id,
                    saga_id=str(run.phase_id),
                    description=description,
                    source="ting:activity_subscriber",
                    correlation_id=run.session_id or run.tracker_id,
                )
                await self._sleipnir_publisher.publish(_event)
            except Exception:
                logger.warning("Failed to emit ting.run.needs_approval; continuing.", exc_info=True)

        logger.info(
            "Session %s completed (tracker=%s, pr=%s, chronicle=%s)",
            run.session_id,
            run.tracker_id,
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

        run, tracker = await self._find_run_for_session(event.session_id, owner_id)
        if run is None or tracker is None:
            return

        reason = str(event.metadata.get("error") or event.metadata.get("message") or "").strip()
        if not reason:
            reason = f"Session {event.session_status or event.state or 'failed'}"
        await self._handle_failure(run, tracker, owner_id, reason=reason)

    async def _handle_failure(
        self,
        run: Run,
        tracker: TrackerPort,
        owner_id: str,
        *,
        reason: str,
    ) -> None:
        """Mark a run as failed."""
        if self._review_engine is not None:
            handled = await self._review_engine.handle_run_failure(
                run.tracker_id,
                owner_id,
                reason=reason,
            )
            if not handled:
                await tracker.update_run_progress(
                    run.tracker_id,
                    status=RunStatus.FAILED,
                    reason=reason,
                )
        else:
            await tracker.update_run_progress(
                run.tracker_id,
                status=RunStatus.FAILED,
                reason=reason,
            )

        await self._emit_state_changed(run, owner_id, "FAILED")
        logger.info(
            "Session %s failed (tracker=%s, reason=%s)",
            run.session_id,
            run.tracker_id,
            reason,
        )

    async def _emit_state_changed(
        self,
        run: Run,
        owner_id: str,
        status: str,
        *,
        pr_id: str | None = None,
        pr_url: str | None = None,
    ) -> None:
        """Emit a run.state_changed event via the event bus."""
        await self._event_bus.emit(
            TingEvent(
                event="run.state_changed",
                owner_id=owner_id,
                data={
                    "session_id": run.session_id,
                    "owner_id": owner_id,
                    "tracker_id": run.tracker_id,
                    "url": run.url,
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


def _is_authoritative_completion_metadata(metadata: dict) -> bool:
    if metadata.get("completion_source") != "ravn_flock":
        return False
    if not str(metadata.get("completion_peer_id") or "").startswith("workflow-stop:"):
        return False

    structured = metadata.get("structured_outcome")
    if not isinstance(structured, dict) or not structured:
        return False
    if isinstance(structured.get("outcome"), dict):
        structured_payload = structured["outcome"]
    else:
        structured_payload = structured
    if not isinstance(structured_payload, dict) or not structured_payload:
        return False

    return bool(structured_payload.get("authoritative") or metadata.get("outcome_valid"))


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


def _help_needed_session_id(payload: dict[str, object]) -> str:
    session_id = str(payload.get("session_id") or "").strip()
    if session_id:
        return session_id
    context = payload.get("context")
    if isinstance(context, dict):
        return str(context.get("session_id") or context.get("ravn_session_id") or "").strip()
    return ""


def _workflow_gate_from_help_needed(payload: dict[str, object]) -> dict[str, object]:
    context = payload.get("context")
    context_dict = context if isinstance(context, dict) else {}
    gate_id = str(context_dict.get("gate_id") or "").strip()
    node_id = str(context_dict.get("gate_node_id") or context_dict.get("node_id") or "").strip()
    return {
        "id": gate_id,
        "node_id": node_id,
        "status": str(context_dict.get("gate_status") or "pending"),
        "summary": str(payload.get("summary") or ""),
        "instructions": str(
            context_dict.get("instructions") or payload.get("recommendation") or ""
        ),
        "reason": str(payload.get("reason") or ""),
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
