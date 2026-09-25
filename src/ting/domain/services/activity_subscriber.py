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
import math
import random
import time
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
from ting.ports.volundr import (
    ActivityEvent,
    ActivityStreamConnected,
    VolundrFactory,
    VolundrPort,
    VolundrSession,
)

if TYPE_CHECKING:
    from ting.domain.services.review_engine import ReviewEngine
    from ting.domain.services.workflow_campaign_projector import WorkflowCampaignProjector

logger = logging.getLogger(__name__)

# Sentinel returned by _lookup_run_session() to distinguish "this cluster
# errored while being asked" from a clean "not found" (None) — a plain
# module-level object rather than an exception or a magic string, since it
# is compared by identity and never meant to be raised or serialized.
_SESSION_LOOKUP_FAILED = object()


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
    ``_cancel_owner_tasks``. ``next_retry_at`` (``time.monotonic()`` clock)
    is what makes ``_sync_owner_clusters`` wait out the backoff delay
    instead of rebuilding a failed cluster's task on the very next sync
    cycle.
    """

    consecutive_failures: int = 0
    next_retry_at: float = 0.0


class DuplicateVolundrClusterError(RuntimeError):
    """Raised when two of an owner's adapters resolve to the same cluster key.

    Silently keeping only one would mean Ting stops watching a real,
    distinctly-registered Forge cluster with no signal that it happened
    (see .claude/rules/no-fallbacks.md) — almost certainly a Guild
    registration bug (two instances sharing a target_id/name).
    """


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
        cluster_tasks = [task for tasks in self._owner_tasks.values() for task in tasks.values()]
        for task in cluster_tasks:
            task.cancel()
        self._owner_tasks.clear()
        self._cluster_backoff.clear()
        if cluster_tasks:
            # Wait for cancellation to actually land instead of returning
            # with tasks still mid-teardown — return_exceptions so one
            # task's CancelledError (or any other exception) never stops us
            # from waiting for the rest.
            await asyncio.gather(*cluster_tasks, return_exceptions=True)
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
        # reconcile just because this cycle ran again. Each owner is isolated
        # in its own try/except: one owner's broken Guild registration (e.g.
        # GuildRegistryUnavailableError, CredentialBindingError) must not
        # abort the sync cycle for every other owner — but it stays loud,
        # logged at ERROR with a traceback every time it recurs, never
        # swallowed (see .claude/rules/no-fallbacks.md).
        for owner_id in active_owners:
            try:
                is_new_owner = owner_id not in self._owner_tasks
                if is_new_owner and self._workflow_campaign_projector is not None:
                    await self._workflow_campaign_projector.reconcile_owner(owner_id)
                await self._sync_owner_clusters(owner_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error(
                    "Failed to sync Volundr subscriptions for owner %s",
                    owner_id[:8],
                    exc_info=True,
                )

        # Cancel subscriptions for owners with no more active dispatchers
        for owner_id in list(self._owner_tasks):
            if owner_id not in active_owners:
                self._cancel_owner_tasks(owner_id)

        # Wait before re-syncing
        await asyncio.sleep(self._config.reconnect_delay)

    async def _sync_owner_clusters(self, owner_id: str) -> None:
        """Ensure *owner_id* has exactly one live SSE task per registered cluster.

        Adapters are resolved fresh every cycle — no caching — so a cluster's
        rotated PAT or changed Guild ``base_url`` is visible the moment its
        task needs rebuilding, and a cluster added or removed from the
        owner's Guild registration is picked up. A cluster whose task is
        still running (or still waiting out its own backoff delay — see
        ``_ClusterBackoffState.next_retry_at``) is left completely alone;
        only a cluster whose task has finished and is due gets a new task
        with the freshly resolved adapter.
        """
        adapters = await self._resolve_owner_adapters(owner_id)
        if not adapters:
            # Nothing known for this owner right now. Tear down anything
            # stale, but leave no entry behind: an empty entry would read as
            # "already reconciled" forever, and _sync_owner_subscriptions
            # would then never call reconcile_owner again even once a
            # cluster reappears for this owner.
            if owner_id in self._owner_tasks:
                self._cancel_owner_tasks(owner_id)
            return

        desired: dict[str, VolundrPort] = {}
        for adapter in adapters:
            key = self._cluster_key(adapter)
            if key in desired:
                raise DuplicateVolundrClusterError(
                    f"Owner {owner_id[:8]} has two Volundr adapters resolving to the "
                    f"same cluster key {key!r}: {self._cluster_label(desired[key])!r} "
                    f"and {self._cluster_label(adapter)!r}. Fix the Guild registration "
                    "so each instance has a unique target_id/name."
                )
            desired[key] = adapter

        clusters = self._owner_tasks.setdefault(owner_id, {})
        now = time.monotonic()

        for key, adapter in desired.items():
            existing_task = clusters.get(key)
            if existing_task is not None and not existing_task.done():
                continue
            backoff = self._cluster_backoff.get((owner_id, key))
            if backoff is not None and now < backoff.next_retry_at:
                continue
            if backoff is not None and self._workflow_campaign_projector is not None:
                # This cluster just failed and is now due for its
                # backed-off resubscribe — catch up on any campaign
                # terminal events its outage could have missed, the same
                # pass a brand-new owner gets (see the is_new_owner branch
                # in _sync_owner_subscriptions). Without this, a workflow
                # gate resolved or a campaign finished while this one
                # cluster was down would never be picked up.
                await self._workflow_campaign_projector.reconcile_owner(owner_id)
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
        """Run one connect-and-stream attempt for a single owner-cluster pair.

        This is a single attempt, not a retry loop: on failure it records
        backoff state and returns; ``_sync_owner_clusters`` rebuilds the
        cluster's task — once the recorded backoff delay has elapsed — with
        a freshly resolved adapter. That is what picks up a rotated PAT or a
        changed Guild ``base_url`` instead of retrying forever against a
        stale one, and it never touches another cluster's task (see
        .claude/rules/no-fallbacks.md — a stale credential must not keep
        being silently retried against the wrong target). A clean stream end
        (no error) also just returns; the next sync cycle reconnects it at
        the ordinary ``reconnect_delay`` cadence.

        Guards against double-handling events from an orphaned task: before
        starting, and before handling each event, this checks that it is
        still the task ``_owner_tasks`` has recorded for its ``(owner,
        key)`` — if sync has since rebuilt this cluster under a different
        task, this one stops silently instead of also processing events.

        A cluster that legitimately has no sessions right now never gets
        the "first event" backoff reset below — if it then drops mid-stream
        (raises instead of closing cleanly) before ever sending an event,
        that reset never fires either. So the connection's own age is also
        a health signal: ``volundr.subscribe_activity()`` yields
        ``ActivityStreamConnected`` exactly once, as soon as the connection
        is genuinely open (not merely "we started trying"), and one that
        has stayed open at least ``reconnect_stable_after_seconds`` since
        then still counts as a recovery even though it ends in an
        exception. The clock starts there, never at task start — a connect
        that hangs to its own timeout (which can be the same order of
        magnitude as the stability window) must never be credited as
        "connected", or the backoff would never grow for a cluster that
        never actually answers.
        """
        key = self._cluster_key(volundr)
        label = self._cluster_label(volundr)
        this_task = asyncio.current_task()

        def _is_current() -> bool:
            return self._running and self._owner_tasks.get(owner_id, {}).get(key) is this_task

        if not _is_current():
            return

        connected = False
        stream_opened_at: float | None = None
        try:
            async for item in volundr.subscribe_activity():
                if not _is_current():
                    return
                if isinstance(item, ActivityStreamConnected):
                    stream_opened_at = time.monotonic()
                    # Reconcile only once the connection is genuinely open —
                    # not before, which left a window where a session status
                    # change (or the outage this cluster just had) went
                    # unnoticed until the *next* resubscribe, and skipped
                    # this cluster's own runs as "still backing off" since
                    # its backoff state isn't cleared until later in this
                    # same attempt.
                    await self._reconcile_running_runs(owner_id, exempt_cluster_key=key)
                    logger.info(
                        "SSE subscription started for owner %s cluster=%s", owner_id[:8], label
                    )
                    continue
                if not connected:
                    self._on_cluster_connected(owner_id, key, label)
                    connected = True
                await self._on_activity_event(item, volundr, owner_id)
            if not connected and stream_opened_at is not None:
                self._on_cluster_connected(owner_id, key, label)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if this_task is not None and this_task.cancelling():
                # A cancellation is already in flight for this task (e.g.
                # sync just removed this cluster) — let that win instead of
                # recording a spurious failure for a cluster we're no longer
                # tracking.
                raise
            if (
                not connected
                and stream_opened_at is not None
                and self._attempt_was_stable(stream_opened_at)
            ):
                self._on_cluster_connected(owner_id, key, label)
            self._on_cluster_failure(owner_id, key, label, exc)

    def _attempt_was_stable(self, stream_opened_at: float) -> bool:
        """Whether the connection stayed open long enough to count as healthy.

        *stream_opened_at* must be the moment ``ActivityStreamConnected``
        was observed — never task start, which would count time spent
        waiting on a connect (including one that eventually times out) as
        time spent connected.
        """
        return time.monotonic() - stream_opened_at >= self._config.reconnect_stable_after_seconds

    async def _reconcile_running_runs(
        self, owner_id: str, *, exempt_cluster_key: str | None = None
    ) -> None:
        """Fail tracker-side RUNNING runs whose backing Forge session is confirmed terminal.

        Called right after *this* cluster's SSE connection actually opens
        (``ActivityStreamConnected``) — including the resubscribe after an
        outage — so a session-status change missed while it was down is
        caught up the moment it is back, and there is no gap between this
        check and the stream actually starting. This closes the restart gap
        where Volundr has reconciled dead local sessions to ``stopped``
        before Ting's SSE subscription comes online; without this pass,
        ``try_auto_continue()`` can believe all owner slots are still
        occupied by phantom RUNNING runs.

        A ``Run`` does not record which Forge cluster its session lives on,
        so every one of the owner's currently registered adapters is asked
        — see ``_reconcile_run_session`` for how a definitive "missing"
        verdict is kept scoped to clusters that were actually, successfully
        queried, so this pass run for one cluster's resubscribe can never
        raise (or fail a run) because a *different* cluster is down.

        *exempt_cluster_key* is the cluster whose own resubscribe triggered
        this call: it is always queried regardless of its recorded backoff
        state, which — at this point — has not been cleared yet (that
        happens once an event arrives or the stream ends). Without the
        exemption, a cluster's own reconcile pass would skip itself as
        "currently backing off" and its phantom RUNNING runs from the
        outage that just ended would never get checked by anyone.
        """
        adapters, unresolved = await self._resolve_owner_adapters_for_reconcile(owner_id)
        if not adapters:
            return

        trackers = await self._tracker_factory.for_owner(owner_id)
        for tracker in trackers:
            running_runs = await tracker.list_runs_by_status(RunStatus.RUNNING)
            for run in running_runs:
                if not run.session_id:
                    continue
                await self._reconcile_run_session(
                    run,
                    tracker,
                    owner_id,
                    adapters,
                    unresolved,
                    exempt_cluster_key=exempt_cluster_key,
                )

    async def _resolve_owner_adapters_for_reconcile(
        self, owner_id: str
    ) -> tuple[list[VolundrPort], int]:
        """Resolve adapters for a reconcile pass, plus how many registered
        clusters could not be resolved into a usable adapter this cycle —
        see ``VolundrFactory.for_owner_with_unresolved``.
        """
        return await self._factory.for_owner_with_unresolved(owner_id)

    def _cluster_is_backing_off(
        self, owner_id: str, adapter: VolundrPort, *, exempt_cluster_key: str | None
    ) -> bool:
        """Whether *adapter*'s cluster currently has recorded backoff state.

        A cluster with an active failure streak has an unknown status until
        it reconnects — querying it here would both delay this reconcile
        pass by however long its own connect/read timeout takes, and risk
        the exact 404 misattribution ``_reconcile_run_session`` otherwise
        guards against. Its own ``_adapter_subscription_loop`` is already
        responsible for finding out when it recovers.

        *exempt_cluster_key*, when it matches this adapter's key, means
        this is that cluster's own resubscribe calling — it is never
        skipped for itself, only for other clusters still in backoff (see
        ``_reconcile_running_runs``).
        """
        key = self._cluster_key(adapter)
        if key == exempt_cluster_key:
            return False
        return (owner_id, key) in self._cluster_backoff

    async def _reconcile_run_session(
        self,
        run: Run,
        tracker: TrackerPort,
        owner_id: str,
        adapters: list[VolundrPort],
        unresolved: int,
        *,
        exempt_cluster_key: str | None = None,
    ) -> None:
        """Resolve *run*'s session across *adapters* and fail it only on real evidence.

        A run is marked FAILED for a missing session only when EVERY
        registered cluster gave a clean, error-free answer and none of them
        recognised the session. Three things stop that verdict, each
        leaving the run's status simply unknown this pass rather than
        failing it or letting anything raise out of this call (see
        .claude/rules/no-fallbacks.md):

        - A cluster currently in backoff — other than *exempt_cluster_key*
          — is skipped without being queried at all (see
          ``_cluster_is_backing_off``).
        - A cluster that errored while being asked (down, timing out) — the
          remaining clusters are still queried, concurrently, so one down
          cluster never delays or blocks the others.
        - Any cluster the factory could not even build an adapter for
          (*unresolved* > 0) — it was never queried at all, so "not found
          on every adapter we got" is not "not found on every registered
          cluster".

        A cluster that DID answer — found the session, healthy or terminal
        — is always trusted for that verdict regardless of what any other
        cluster did.
        """
        queryable = [
            adapter
            for adapter in adapters
            if not self._cluster_is_backing_off(
                owner_id, adapter, exempt_cluster_key=exempt_cluster_key
            )
        ]
        skipped_for_backoff = len(adapters) - len(queryable)

        results = await asyncio.gather(
            *(self._lookup_run_session(adapter, run, owner_id) for adapter in queryable)
        )

        session = next((result for result in results if isinstance(result, VolundrSession)), None)
        if session is not None:
            if session.status in self._FAILED_STATUSES:
                await self._handle_failure(
                    run,
                    tracker,
                    owner_id,
                    reason=f"Session {session.status}",
                )
            return

        any_lookup_failed = any(result is _SESSION_LOOKUP_FAILED for result in results)
        if any_lookup_failed or skipped_for_backoff > 0 or unresolved > 0:
            return

        await self._handle_failure(
            run,
            tracker,
            owner_id,
            reason="Session not found on any registered Volundr cluster",
        )

    async def _lookup_run_session(
        self,
        adapter: VolundrPort,
        run: Run,
        owner_id: str,
    ) -> VolundrSession | None | object:
        """Look up *run*'s session on *adapter*, catching that adapter's own error.

        Returns the session (or ``None`` for a clean "not found") on
        success, or the ``_SESSION_LOOKUP_FAILED`` sentinel on any
        non-cancellation exception — so one cluster's error, surfaced here
        per cluster, never propagates to cancel its siblings' concurrent
        lookups (``asyncio.gather`` would otherwise do exactly that).
        """
        try:
            return await adapter.get_session(run.session_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "Could not check session %s on cluster %s for owner %s while "
                "reconciling run %s; leaving its status unknown this pass",
                run.session_id,
                self._cluster_label(adapter),
                owner_id[:8],
                run.tracker_id,
                exc_info=True,
            )
            return _SESSION_LOOKUP_FAILED

    def _cancel_owner_tasks(self, owner_id: str) -> None:
        """Cancel all SSE tasks for *owner_id* and clear its backoff state."""
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
        if not adapters:
            logger.error(
                "No authenticated Volundr adapter for owner %s — "
                "user must configure a CODE_FORGE integration with a valid PAT",
                owner_id[:8],
            )
        return adapters

    @staticmethod
    def _cluster_key(volundr: VolundrPort) -> str:
        """Stable per-cluster key used to track tasks and backoff state.

        Raises rather than falling back to a shared placeholder — silently
        collapsing every unidentified adapter into one key would merge
        distinct clusters into a single tracked subscription (see
        .claude/rules/no-fallbacks.md).
        """
        key = volundr.target_id or volundr.name
        if not key:
            raise ValueError(
                "Volundr adapter has neither target_id nor name set; cannot derive a "
                "stable per-cluster subscription key for it — set target_id or name "
                "on the registered Guild instance."
            )
        return key

    @staticmethod
    def _cluster_label(volundr: VolundrPort) -> str:
        """Human-readable cluster identity for log lines (name/slug + base URL)."""
        name = volundr.name or volundr.target_id
        base_url = volundr.base_url
        if base_url:
            return f"{name} ({base_url})"
        return name

    def _max_backoff_exponent(self) -> int:
        """Largest exponent worth computing before the delay is already capped.

        Prevents ``multiplier ** attempt`` from ever approaching float
        overflow when a cluster fails for a very long time (an unbounded
        number of consecutive failures): once ``initial *
        multiplier ** exponent`` reaches ``reconnect_max_delay`` the result
        is clamped anyway, so no larger exponent is ever needed. Derived
        entirely from config, not a hardcoded cap.
        """
        cfg = self._config
        if cfg.reconnect_backoff_multiplier <= 1.0:
            return 0
        if cfg.reconnect_max_delay <= cfg.reconnect_initial_delay:
            return 0
        ratio = cfg.reconnect_max_delay / cfg.reconnect_initial_delay
        return max(math.ceil(math.log(ratio, cfg.reconnect_backoff_multiplier)), 0)

    def _compute_backoff_delay(self, attempt: int) -> float:
        """Exponential backoff with a cap and downward jitter, all config-driven.

        ``attempt`` is the 1-based consecutive-failure count. The delay grows
        as ``reconnect_initial_delay * reconnect_backoff_multiplier **
        (attempt - 1)``, capped at ``reconnect_max_delay``, then reduced by up
        to ``reconnect_jitter`` fraction so many clusters failing together
        don't retry in lockstep. ``reconnect_jitter`` is validated to
        ``[0, 1]`` by ``WatcherConfig``, so it is used directly here — no
        silent runtime clamp.
        """
        cfg = self._config
        if cfg.reconnect_initial_delay <= 0:
            return 0.0
        exponent = min(max(attempt - 1, 0), self._max_backoff_exponent())
        base = min(
            cfg.reconnect_initial_delay * (cfg.reconnect_backoff_multiplier**exponent),
            cfg.reconnect_max_delay,
        )
        if cfg.reconnect_jitter == 0.0:
            return base
        return base * (1 - random.uniform(0.0, cfg.reconnect_jitter))

    def _on_cluster_connected(self, owner_id: str, key: str, label: str) -> None:
        """Reset a cluster's backoff state as soon as it proves it's live.

        Called on the first event received (proof of a live connection) or,
        for a stream that ends cleanly having never sent one, once it ends —
        not only on a clean close, so a cluster that reconnects and stays up
        for a long time has its backoff reset promptly rather than only
        after that (possibly very long) stream eventually ends. A no-op
        unless the cluster had previously failed — a healthy cluster must
        not get a recovery log line on every ordinary reconnect cycle.
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
        state.next_retry_at = time.monotonic() + delay
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
