"""Realm capability-ledger writer — the realm Self-Model's capability sync.

The realm governance API keeps a per-realm ``capabilities`` ledger (the
realm's Self-Model: what the resident can actually do right now). This module
keeps that ledger truthful without touching the evolution flows themselves:
it subscribes to the lifecycle events the resident already publishes and
mirrors them over HTTP via :class:`RealmClient`.

- an installed learned skill/tool (adoption install or build_tool
  self-registration) is recorded as PRESENT
- a rolled-back/archived skill is recorded as GAP

Capability bookkeeping is advisory: every failure — malformed payload,
unreachable realm, non-2xx response — degrades to a WARNING and never raises
into the bus consumer, so a realm outage can never brick the resident.
"""

from __future__ import annotations

import logging

from ravn.adapters.realm.client import RealmClient

# Event names come FROM THE PUBLISHER so producer and consumer cannot drift.
from ravn.valkyrie_evolution.resident_learning import (
    EVOLUTION_ACTIVATED_EVENT,
    EVOLUTION_ROLLED_BACK_EVENT,
)
from sleipnir.domain import registry
from sleipnir.domain.events import SleipnirEvent
from sleipnir.ports.events import SleipnirSubscriber, Subscription

logger = logging.getLogger(__name__)

#: Ledger status for a capability the resident currently has installed.
CAPABILITY_PRESENT = "present"

#: Ledger status for a capability the resident lost (rolled back/archived).
CAPABILITY_GAP = "gap"

#: Ledger kind recorded for learned skills/tools.
CAPABILITY_KIND_TOOL = "tool"

#: ``review_outcome`` build_tool stamps on its self-registration proposal.
SELF_REGISTERED_REVIEW_OUTCOME = "self_registered"

_SUBSCRIBED_EVENT_TYPES = [
    EVOLUTION_ACTIVATED_EVENT,
    EVOLUTION_ROLLED_BACK_EVENT,
    registry.FLOCK_LEARNING_PROPOSED,
]


class RealmCapabilitySync:
    """Mirror resident evolution events into the realm capability ledger."""

    def __init__(
        self,
        *,
        client: RealmClient,
        realm_slug: str,
        subscriber: SleipnirSubscriber,
    ) -> None:
        if not realm_slug:
            msg = "RealmCapabilitySync requires a realm_slug"
            raise ValueError(msg)
        self._client = client
        self._realm_slug = realm_slug
        self._subscriber = subscriber
        self._subscription: Subscription | None = None

    @property
    def is_running(self) -> bool:
        return self._subscription is not None

    async def start(self) -> None:
        if self._subscription is not None:
            return
        self._subscription = await self._subscriber.subscribe(
            _SUBSCRIBED_EVENT_TYPES,
            self._handle_event,
        )

    async def stop(self) -> None:
        if self._subscription is None:
            return
        await self._subscription.unsubscribe()
        self._subscription = None

    async def _handle_event(self, event: SleipnirEvent) -> None:
        try:
            await self._sync_event(event)
        except Exception as exc:  # noqa: BLE001 — ledger sync must never break the signal path
            logger.warning(
                "realm %s capability sync failed for %s: %s",
                self._realm_slug,
                event.event_type,
                exc,
            )

    async def _sync_event(self, event: SleipnirEvent) -> None:
        if event.event_type == EVOLUTION_ACTIVATED_EVENT:
            await self._record_installed(event)
            return
        if event.event_type == EVOLUTION_ROLLED_BACK_EVENT:
            await self._record_retracted(event)
            return
        if event.event_type == registry.FLOCK_LEARNING_PROPOSED:
            await self._record_self_registered(event)
            return

    async def _record_installed(self, event: SleipnirEvent) -> None:
        """Adoption install: the skill is now PRESENT in the realm."""
        name = _payload_str(event.payload, "skill_name")
        if not name:
            logger.warning(
                "realm %s capability sync: %s event has no skill_name; skipping",
                self._realm_slug,
                event.event_type,
            )
            return
        learning_id = _payload_str(event.payload, "learning_id")
        await self._record(
            name=name,
            status=CAPABILITY_PRESENT,
            notes=_join_notes("installed learning", learning_id),
        )

    async def _record_retracted(self, event: SleipnirEvent) -> None:
        """Rollback/archive: the capability is a GAP again."""
        name = _payload_str(event.payload, "skill_name")
        if not name:
            logger.warning(
                "realm %s capability sync: %s event has no skill_name; skipping",
                self._realm_slug,
                event.event_type,
            )
            return
        rationale = _payload_str(event.payload, "rationale")
        learning_id = _payload_str(event.payload, "learning_id")
        await self._record(
            name=name,
            status=CAPABILITY_GAP,
            notes=_join_notes(rationale, _join_notes("rolled back learning", learning_id)),
        )

    async def _record_self_registered(self, event: SleipnirEvent) -> None:
        """build_tool self-registration: the authored tool is PRESENT."""
        payload = event.payload
        if _payload_str(payload, "review_outcome") != SELF_REGISTERED_REVIEW_OUTCOME:
            return
        name = _payload_str(payload, "title") or _manifest_name(payload)
        if not name:
            logger.warning(
                "realm %s capability sync: self-registered %s event has no "
                "title or manifest name; skipping",
                self._realm_slug,
                event.event_type,
            )
            return
        learning_id = _payload_str(payload, "learning_id")
        await self._record(
            name=name,
            status=CAPABILITY_PRESENT,
            notes=_join_notes("self-registered learning", learning_id),
        )

    async def _record(self, *, name: str, status: str, notes: str) -> None:
        recorded = await self._client.record_capability(
            self._realm_slug,
            name=name,
            kind=CAPABILITY_KIND_TOOL,
            status=status,
            notes=notes,
        )
        if recorded:
            logger.info(
                "realm %s capability %r recorded as %s",
                self._realm_slug,
                name,
                status,
            )
            return
        logger.warning(
            "realm %s capability %r could not be recorded as %s; "
            "ledger is stale until the next lifecycle event",
            self._realm_slug,
            name,
            status,
        )


def _payload_str(payload: dict, key: str) -> str:
    """A payload value as a stripped string; '' for missing/None values."""
    value = payload.get(key)
    if value is None:
        return ""
    return str(value).strip()


def _manifest_name(payload: dict) -> str:
    manifest = payload.get("learned_tool_manifest")
    if not isinstance(manifest, dict):
        return ""
    return _payload_str(manifest, "name")


def _join_notes(prefix: str, suffix: str) -> str:
    """Join note fragments, dropping empty parts."""
    return " ".join(part for part in (prefix, suffix) if part)
