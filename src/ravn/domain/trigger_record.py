"""Durable trigger record — the API-layer shape for a stored Ravn trigger.

Distinct from :class:`ravn.ports.trigger.TriggerPort`, which is the *runtime*
interface a resident's drive loop registers. A ``TriggerRecord`` is what the
Ravn control API persists when an operator (or the Simple-mode realm recipe)
calls ``POST /api/v1/ravn/triggers`` — a resident later loads and executes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class TriggerRecord:
    """One durably-stored trigger, scoped to the tenant/owner that created it."""

    id: str
    kind: str
    persona_name: str
    spec: str
    enabled: bool
    owner_id: str
    tenant_id: str
    created_at: datetime
    repo: str = ""
    """The repository an event-kind trigger reacts to (empty for cron-kind).
    ravn.adapters.triggers.api_source filters incoming Sleipnir events on
    this before enqueueing, so a trigger without one cannot be an event kind
    (validate_trigger_spec enforces that at creation time)."""
