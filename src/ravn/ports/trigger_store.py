"""Trigger store port — durable persistence for API-created triggers.

Distinct from :class:`ravn.ports.trigger.TriggerPort` (the runtime source a
drive loop polls to fire tasks). This port is the control-plane store behind
``/api/v1/ravn/triggers``: it holds records, scoped by tenant/owner, that a
resident later reads and executes via a ``TriggerPort`` adapter
(``ravn.adapters.triggers.api_source``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ravn.domain.trigger_record import TriggerRecord


class TriggerStorePort(ABC):
    """Durable CRUD for trigger records, scoped by tenant."""

    @abstractmethod
    async def list_triggers(self, *, tenant_id: str) -> list[TriggerRecord]:
        """Return every trigger belonging to *tenant_id*, newest first."""
        raise NotImplementedError

    @abstractmethod
    async def get_trigger(self, trigger_id: str) -> TriggerRecord | None:
        """Return one trigger by id regardless of tenant, or ``None``.

        Callers must apply their own tenant/owner authorization after reading
        — this exists so the API layer can distinguish "not found" (404) from
        "found but not yours" (403) without a second round trip.
        """
        raise NotImplementedError

    @abstractmethod
    async def create_trigger(
        self,
        *,
        kind: str,
        persona_name: str,
        spec: str,
        enabled: bool,
        owner_id: str,
        tenant_id: str,
        repo: str = "",
    ) -> TriggerRecord:
        """Persist one new trigger and return the stored record."""
        raise NotImplementedError

    @abstractmethod
    async def delete_trigger(self, trigger_id: str) -> bool:
        """Delete one trigger by id. Returns ``True`` if a row was removed."""
        raise NotImplementedError
