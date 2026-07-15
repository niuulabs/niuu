"""Port for durable Valkyrie decision / action / signal history."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ValkyrieHistoryStore(ABC):
    """Persist what Valkyries decided and did so history survives restarts.

    Records are plain dicts shaped by :mod:`ravn.domain.valkyrie_history`.
    ``list_*`` methods return ``(rows, total)`` where ``rows`` is one page
    ordered newest-first and ``total`` is the full filtered count.
    """

    @abstractmethod
    async def record_decision(self, record: dict[str, Any]) -> None:
        """Upsert one judgment record keyed by ``decisionId``."""

    @abstractmethod
    async def record_action(self, record: dict[str, Any]) -> None:
        """Upsert one action record keyed by ``eventId``."""

    @abstractmethod
    async def record_signal(self, record: dict[str, Any]) -> None:
        """Upsert one signal record keyed by ``signalId``."""

    @abstractmethod
    async def list_decisions(
        self,
        *,
        environment_id: str = "",
        valkyrie_id: str = "",
        operational_state: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return one page of decisions, newest first, plus the total count."""

    @abstractmethod
    async def get_decision(self, decision_id: str) -> dict[str, Any] | None:
        """Return one decision record or None."""

    @abstractmethod
    async def list_signals(
        self,
        *,
        environment_id: str = "",
        severity: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return one page of signals, newest first, plus the total count."""

    @abstractmethod
    async def signals_by_ids(self, signal_ids: list[str]) -> list[dict[str, Any]]:
        """Return the stored signals matching the given ids (order preserved)."""

    @abstractmethod
    async def actions_for_correlation(self, correlation_id: str) -> list[dict[str, Any]]:
        """Return actions sharing a correlation id, newest first."""

    @abstractmethod
    async def record_decision_outcome(
        self,
        *,
        correlation_id: str,
        outcome: str,
        detail: str,
        outcome_at: str,
    ) -> int:
        """Stamp an action outcome onto decisions sharing the correlation id.

        Returns the number of decisions updated.
        """

    @abstractmethod
    async def link_review_item(self, decision_id: str, review_item_id: str) -> None:
        """Remember which review inbox item a decision was escalated to."""
