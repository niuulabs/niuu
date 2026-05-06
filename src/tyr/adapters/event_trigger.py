"""EventTriggerAdapter — create sagas from Sleipnir events.

Subscribes to the Sleipnir event bus and fires saga creation when configured
event patterns match.  Supports:

- Pattern matching via fnmatch (``github.pr.*``, ``ravn.session.ended``, etc.)
- Payload filter matching (key=value pairs that all must match)
- Saga templates loaded from YAML files
- ``auto_start: true``  — spawn Volundr sessions immediately for Phase 1
- ``auto_start: false`` — create PENDING raids, emit ``tyr.raid.needs_approval``
- Deduplication via correlation_id to avoid creating duplicate sagas
- Multi-phase sequential execution: Phase 1 dispatched on creation;
  subsequent phases advance via :meth:`advance_phase`
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import re
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sleipnir.domain.events import SleipnirEvent
from sleipnir.ports.events import SleipnirSubscriber, Subscription
from tyr.domain.models import (
    Phase,
    PhaseStatus,
    Raid,
    RaidStatus,
    Saga,
    SagaStatus,
)
from tyr.domain.templates import (
    BUNDLED_TEMPLATES_DIR,
    SagaTemplate,
    TemplatePhase,
    TemplateRaid,
    load_template,
)
from tyr.domain.utils import _slugify
from tyr.ports.event_bus import EventBusPort, TyrEvent
from tyr.ports.saga_repository import SagaRepository
from tyr.ports.volundr import SpawnRequest, VolundrFactory, VolundrPort

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Filter matching
# ---------------------------------------------------------------------------


def matches_filter(payload: dict, filter_: dict[str, str]) -> bool:
    """Return True when every key in *filter_* is present and equal in *payload*."""
    for key, expected in filter_.items():
        actual = payload.get(key)
        if actual is None:
            return False
        if str(actual) != expected:
            return False
    return True


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


@dataclass
class _TriggerRule:
    """Internal representation of a single trigger rule."""

    event_pattern: str
    saga_template: str
    auto_start: bool
    filter: dict[str, str]


class EventTriggerAdapter:
    """Subscribes to Sleipnir events and creates sagas from YAML templates.

    Lifecycle::

        adapter = EventTriggerAdapter(...)
        await adapter.start()   # subscribe; begin reacting to events
        # ... application runs ...
        await adapter.stop()    # unsubscribe; clean up

    **Multi-phase execution**

    When a template has more than one phase, only Phase 1 is dispatched at saga
    creation.  Subsequent phases are created in PENDING state.  Call
    :meth:`advance_phase` once all raids in the active phase complete to
    dispatch the next phase.  If a phase has ``needs_approval: true`` the
    adapter emits ``phase.needs_approval`` and gates (GATED status) the phase
    instead of dispatching its raids automatically.

    **Deduplication**

    Based on the event's ``correlation_id`` (falling back to ``event_id``).
    The same correlation-id+rule combination will not create a second saga
    within the lifetime of the adapter.

    The dedup cache is a bounded ``deque`` of fixed maximum size
    (``dedup_cache_size``).  The oldest entries are evicted when the cache is
    full.
    """

    def __init__(
        self,
        *,
        subscriber: SleipnirSubscriber,
        saga_repo: SagaRepository,
        volundr_factory: VolundrFactory,
        event_bus: EventBusPort,
        rules: list[_TriggerRule],
        templates_dir: Path,
        owner_id: str,
        default_model: str = "claude-sonnet-4-6",
        dedup_cache_size: int = 10_000,
        initial_confidence: float = 0.5,
    ) -> None:
        self._subscriber = subscriber
        self._saga_repo = saga_repo
        self._volundr_factory = volundr_factory
        self._event_bus = event_bus
        self._rules = rules
        self._templates_dir = templates_dir
        self._owner_id = owner_id
        self._default_model = default_model
        self._dedup_cache_size = dedup_cache_size
        self._initial_confidence = initial_confidence

        self._subscription: Subscription | None = None
        self._seen: deque[str] = deque(maxlen=dedup_cache_size)
        self._seen_set: set[str] = set()
        self._pending_tasks: set[asyncio.Task[None]] = set()

        # Maps saga_id → ordered list of (Phase, [Raid], TemplatePhase).
        # Used by advance_phase to know what to dispatch next without an
        # extra round-trip to the database.
        self._saga_phases: dict[str, list[tuple[Phase, list[Raid], TemplatePhase]]] = {}

    @property
    def is_running(self) -> bool:
        return self._subscription is not None

    async def start(self) -> None:
        """Subscribe to all configured event patterns on Sleipnir."""
        if self._subscription is not None:
            return

        patterns = list({r.event_pattern for r in self._rules})
        if not patterns:
            logger.info("EventTriggerAdapter: no rules configured, skipping subscription")
            return

        self._subscription = await self._subscriber.subscribe(patterns, self._handle_event)
        logger.info(
            "EventTriggerAdapter started: %d rule(s), patterns=%s",
            len(self._rules),
            patterns,
        )

    async def stop(self) -> None:
        """Unsubscribe and release resources."""
        if self._subscription is not None:
            await self._subscription.unsubscribe()
            self._subscription = None

        for task in list(self._pending_tasks):
            task.cancel()
        if self._pending_tasks:
            await asyncio.gather(*self._pending_tasks, return_exceptions=True)
        self._pending_tasks.clear()

        logger.info("EventTriggerAdapter stopped")

    # ------------------------------------------------------------------
    # Internal event handling
    # ------------------------------------------------------------------

    async def _handle_event(self, event: SleipnirEvent) -> None:
        """Dispatch *event* to any matching rules."""
        for rule in self._rules:
            if not fnmatch.fnmatch(event.event_type, rule.event_pattern):
                continue
            if not matches_filter(event.payload, rule.filter):
                continue

            dedup_key = self._dedup_key(event, rule.saga_template)
            if dedup_key in self._seen_set:
                logger.debug(
                    "EventTriggerAdapter: duplicate correlation_id %s for template %s, skipping",
                    dedup_key,
                    rule.saga_template,
                )
                continue

            self._add_dedup(dedup_key)
            task = asyncio.create_task(
                self._trigger_saga(event, rule),
                name=f"event-trigger:{rule.saga_template}:{event.event_id}",
            )
            self._pending_tasks.add(task)
            task.add_done_callback(self._pending_tasks.discard)

    def _dedup_key(self, event: SleipnirEvent, template: str) -> str:
        correlation = event.correlation_id or event.event_id
        return f"{template}:{correlation}"

    def _add_dedup(self, key: str) -> None:
        if len(self._seen) >= self._dedup_cache_size and self._seen:
            evicted = self._seen[0]
            self._seen_set.discard(evicted)
        self._seen.append(key)
        self._seen_set.add(key)

    async def _trigger_saga(self, event: SleipnirEvent, rule: _TriggerRule) -> None:
        """Create saga from template and dispatch Phase 1 (or gate it)."""
        try:
            template = load_template(rule.saga_template, self._templates_dir, event.payload)
        except FileNotFoundError:
            logger.error(
                "EventTriggerAdapter: template %r not found, ignoring event %s",
                rule.saga_template,
                event.event_type,
            )
            return
        except Exception:
            logger.exception("EventTriggerAdapter: failed to load template %r", rule.saga_template)
            return

        now = datetime.now(UTC)
        saga_id = uuid.uuid4()
        slug = _slugify(template.name)[:60]

        saga = Saga(
            id=saga_id,
            tracker_id=str(saga_id),
            tracker_type="native",
            slug=slug,
            name=template.name,
            repos=template.repos,
            feature_branch=template.feature_branch,
            base_branch=template.base_branch,
            status=SagaStatus.ACTIVE,
            confidence=self._initial_confidence,
            created_at=now,
            owner_id=self._owner_id,
        )
        await self._saga_repo.save_saga(saga)

        phases = await self._create_phases(saga, template, now, rule.auto_start)

        # Track phases in memory for advance_phase calls.
        self._saga_phases[str(saga_id)] = phases

        await self._event_bus.emit(
            TyrEvent(
                event="saga.created",
                data={
                    "saga_id": str(saga_id),
                    "saga_name": saga.name,
                    "slug": slug,
                    "trigger_event": event.event_type,
                    "template": rule.saga_template,
                    "auto_start": rule.auto_start,
                    "owner_id": self._owner_id,
                },
                owner_id=self._owner_id,
            )
        )

        logger.info(
            "EventTriggerAdapter: created saga %s (template=%s, auto_start=%s, phases=%d)",
            slug,
            rule.saga_template,
            rule.auto_start,
            len(template.phases),
        )

        if not template.phases:
            return

        first_phase, first_raids, first_tpl_phase = phases[0]
        if rule.auto_start:
            await self._activate_phase(saga, first_phase, first_raids, first_tpl_phase)
        else:
            await self._emit_needs_approval(saga, phases)

    async def _create_phases(
        self,
        saga: Saga,
        template: SagaTemplate,
        now: datetime,
        auto_start: bool,
    ) -> list[tuple[Phase, list[Raid], TemplatePhase]]:
        """Persist all phases and raids; return them for in-memory tracking.

        Only Phase 1 gets ACTIVE status; subsequent phases are PENDING.
        All raids are PENDING regardless — they are transitioned to QUEUED
        or RUNNING in :meth:`_activate_phase`.
        """
        phases: list[tuple[Phase, list[Raid], TemplatePhase]] = []

        for phase_num, tpl_phase in enumerate(template.phases, start=1):
            phase_status = PhaseStatus.ACTIVE if phase_num == 1 else PhaseStatus.PENDING
            phase_id = uuid.uuid4()
            phase = Phase(
                id=phase_id,
                saga_id=saga.id,
                tracker_id=str(phase_id),
                number=phase_num,
                name=tpl_phase.name,
                status=phase_status,
                confidence=self._initial_confidence,
            )
            await self._saga_repo.save_phase(phase)

            raids: list[Raid] = []
            for tpl_raid in tpl_phase.raids:
                raid_id = uuid.uuid4()
                raid = Raid(
                    id=raid_id,
                    phase_id=phase_id,
                    tracker_id=str(raid_id),
                    name=tpl_raid.name,
                    description=tpl_raid.description,
                    acceptance_criteria=tpl_raid.acceptance_criteria,
                    declared_files=tpl_raid.declared_files,
                    estimate_hours=tpl_raid.estimate_hours,
                    status=RaidStatus.PENDING,
                    confidence=self._initial_confidence,
                    session_id=None,
                    branch=None,
                    chronicle_summary=None,
                    pr_url=None,
                    pr_id=None,
                    retry_count=0,
                    created_at=now,
                    updated_at=now,
                )
                await self._saga_repo.save_raid(raid)
                raids.append(raid)

            phases.append((phase, raids, tpl_phase))

        return phases

    async def _activate_phase(
        self,
        saga: Saga,
        phase: Phase,
        raids: list[Raid],
        tpl_phase: TemplatePhase,
    ) -> None:
        """Dispatch a phase's raids or gate it for human approval."""
        if tpl_phase.needs_approval:
            # Gate the phase — emit event and wait for human approval.
            gated_phase = Phase(
                id=phase.id,
                saga_id=phase.saga_id,
                tracker_id=phase.tracker_id,
                number=phase.number,
                name=phase.name,
                status=PhaseStatus.GATED,
                confidence=phase.confidence,
            )
            await self._saga_repo.save_phase(gated_phase)
            await self._event_bus.emit(
                TyrEvent(
                    event="phase.needs_approval",
                    data={
                        "phase_id": str(phase.id),
                        "phase_name": phase.name,
                        "phase_number": phase.number,
                        "saga_id": str(saga.id),
                        "saga_name": saga.name,
                        "owner_id": self._owner_id,
                    },
                    owner_id=self._owner_id,
                )
            )
            logger.info(
                "EventTriggerAdapter: phase '%s' (saga=%s) gated — awaiting human approval",
                phase.name,
                saga.slug,
            )
            return

        volundr = await self._volundr_factory.primary_for_owner(self._owner_id)
        if volundr is None:
            logger.error(
                "EventTriggerAdapter: no Volundr adapter for owner %s, cannot dispatch phase '%s'",
                self._owner_id,
                phase.name,
            )
            return

        for raid, tpl_raid in zip(raids, tpl_phase.raids):
            await self._spawn_raid(volundr, saga, phase, raid, tpl_raid)

    async def advance_phase(self, saga_id: str) -> None:
        """Advance to the next PENDING phase for *saga_id*.

        Called after all raids in the current active phase have completed.
        Retrieves the next PENDING phase from the in-memory tracking dict and
        either dispatches its raids or gates it for human approval.

        If the saga is not tracked (e.g. after an adapter restart) this is a
        no-op.
        """
        phases = self._saga_phases.get(saga_id)
        if not phases:
            logger.warning(
                "EventTriggerAdapter.advance_phase: saga %s not in in-memory tracking "
                "(adapter may have restarted); cannot advance phase without DB query",
                saga_id,
            )
            return

        saga = await self._saga_repo.get_saga(
            next(
                (phase.saga_id for phase, _, _ in phases),
                None,  # type: ignore[arg-type]
            )
        )
        if saga is None:
            return

        # Find the first PENDING phase.
        next_phase_info = next(
            (
                (phase, raids, tpl)
                for phase, raids, tpl in phases
                if phase.status == PhaseStatus.PENDING
            ),
            None,
        )
        if next_phase_info is None:
            logger.debug(
                "EventTriggerAdapter.advance_phase: no pending phases for saga %s",
                saga_id,
            )
            return

        next_phase, next_raids, next_tpl = next_phase_info

        # Mark as ACTIVE in memory (the activate_phase call will set GATED if needed).
        idx = next(i for i, (p, _, _) in enumerate(phases) if p.id == next_phase.id)
        activated = Phase(
            id=next_phase.id,
            saga_id=next_phase.saga_id,
            tracker_id=next_phase.tracker_id,
            number=next_phase.number,
            name=next_phase.name,
            status=PhaseStatus.ACTIVE,
            confidence=next_phase.confidence,
        )
        phases[idx] = (activated, next_raids, next_tpl)

        logger.info(
            "EventTriggerAdapter: advancing saga %s to phase '%s' (needs_approval=%s)",
            saga_id,
            next_phase.name,
            next_tpl.needs_approval,
        )
        await self._activate_phase(saga, activated, next_raids, next_tpl)

        # Keep in-memory status in sync with DB: if the phase was gated,
        # update the entry from ACTIVE → GATED so subsequent advance_phase
        # calls reflect the real state.
        if next_tpl.needs_approval:
            gated = Phase(
                id=activated.id,
                saga_id=activated.saga_id,
                tracker_id=activated.tracker_id,
                number=activated.number,
                name=activated.name,
                status=PhaseStatus.GATED,
                confidence=activated.confidence,
            )
            phases[idx] = (gated, next_raids, next_tpl)

    # ------------------------------------------------------------------
    # Dispatch helpers
    # ------------------------------------------------------------------

    async def _spawn_raid(
        self,
        volundr: VolundrPort,
        saga: Saga,
        phase: Phase,
        raid: Raid,
        tpl_raid: TemplateRaid,
    ) -> None:
        """Spawn a single session for *raid*."""
        repo = saga.repos[0] if saga.repos else ""
        session_name = re.sub(r"[^a-z0-9]+", "-", raid.name.lower()).strip("-")[:48]
        integration_ids = await self._resolve_integration_ids(volundr)

        try:
            session = await volundr.spawn_session(
                request=SpawnRequest(
                    name=session_name,
                    repo=repo,
                    branch=saga.feature_branch,
                    base_branch=saga.base_branch,
                    model=self._default_model,
                    tracker_issue_id=raid.tracker_id,
                    tracker_issue_url="",
                    system_prompt="",
                    initial_prompt=tpl_raid.prompt,
                    profile=tpl_raid.persona or None,
                    integration_ids=integration_ids,
                ),
            )

            updated = Raid(
                id=raid.id,
                phase_id=raid.phase_id,
                tracker_id=raid.tracker_id,
                name=raid.name,
                description=raid.description,
                acceptance_criteria=raid.acceptance_criteria,
                declared_files=raid.declared_files,
                estimate_hours=raid.estimate_hours,
                status=RaidStatus.RUNNING,
                confidence=raid.confidence,
                session_id=session.id,
                branch=raid.branch,
                chronicle_summary=raid.chronicle_summary,
                pr_url=raid.pr_url,
                pr_id=raid.pr_id,
                retry_count=raid.retry_count,
                created_at=raid.created_at,
                updated_at=datetime.now(UTC),
            )
            await self._saga_repo.save_raid(updated)

            await self._event_bus.emit(
                TyrEvent(
                    event="raid.state_changed",
                    data={
                        "raid_id": str(raid.id),
                        "new_status": RaidStatus.RUNNING.value,
                        "session_id": session.id,
                        "saga_id": str(saga.id),
                        "owner_id": self._owner_id,
                    },
                    owner_id=self._owner_id,
                )
            )
            logger.info(
                "EventTriggerAdapter: dispatched raid %s → session %s (persona=%s)",
                raid.name,
                session.id,
                tpl_raid.persona or "(none)",
            )
        except Exception:
            logger.exception("EventTriggerAdapter: failed to spawn session for raid %s", raid.name)

    async def _resolve_integration_ids(self, volundr: VolundrPort) -> list[str]:
        """Resolve enabled integration IDs for the trigger owner."""
        try:
            ids = await volundr.list_integration_ids()
            logger.info(
                "EventTriggerAdapter: resolved %d integration IDs for owner %s",
                len(ids),
                self._owner_id[:8],
            )
            return ids
        except Exception:
            logger.warning(
                "EventTriggerAdapter: failed to resolve integrations for owner %s",
                self._owner_id[:8],
                exc_info=True,
            )
            return []

    async def _emit_needs_approval(
        self,
        saga: Saga,
        phases: list[tuple[Phase, list[Raid], TemplatePhase]],
    ) -> None:
        """Emit tyr.raid.needs_approval events for all PENDING raids."""
        for _phase, raids, _tpl in phases:
            for raid in raids:
                await self._event_bus.emit(
                    TyrEvent(
                        event="raid.needs_approval",
                        data={
                            "raid_id": str(raid.id),
                            "raid_name": raid.name,
                            "saga_id": str(saga.id),
                            "saga_name": saga.name,
                            "owner_id": self._owner_id,
                        },
                        owner_id=self._owner_id,
                    )
                )
                logger.info(
                    "EventTriggerAdapter: raid %s awaiting approval (saga=%s)",
                    raid.name,
                    saga.slug,
                )


# ---------------------------------------------------------------------------
# Factory helper
# ---------------------------------------------------------------------------


def build_event_trigger_adapter(
    *,
    subscriber: SleipnirSubscriber,
    saga_repo: SagaRepository,
    volundr_factory: VolundrFactory,
    event_bus: EventBusPort,
    config: object,  # tyr.config.EventTriggerConfig
    initial_confidence: float,
) -> EventTriggerAdapter:
    """Construct an EventTriggerAdapter from application config.

    Accepts an :class:`~tyr.config.EventTriggerConfig` instance and returns a
    fully wired adapter ready to be ``start()``-ed.
    """
    cfg = config  # type: ignore[assignment]

    rules = [
        _TriggerRule(
            event_pattern=r.event,
            saga_template=r.saga_template,
            auto_start=r.auto_start,
            filter=r.filter,
        )
        for r in cfg.rules
    ]

    templates_dir = Path(cfg.templates_dir) if cfg.templates_dir else BUNDLED_TEMPLATES_DIR

    return EventTriggerAdapter(
        subscriber=subscriber,
        saga_repo=saga_repo,
        volundr_factory=volundr_factory,
        event_bus=event_bus,
        rules=rules,
        templates_dir=templates_dir,
        owner_id=cfg.owner_id,
        default_model=cfg.default_model,
        dedup_cache_size=cfg.dedup_cache_size,
        initial_confidence=initial_confidence,
    )
