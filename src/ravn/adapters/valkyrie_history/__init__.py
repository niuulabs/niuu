"""Valkyrie history store adapters."""

from __future__ import annotations

import os
from typing import Any

from ravn.adapters._pool_sizing import AUX_POOL_MAX_SIZE, AUX_POOL_MIN_SIZE
from ravn.adapters.valkyrie_history.memory import InMemoryValkyrieHistoryStore
from ravn.adapters.valkyrie_history.postgres import PostgresValkyrieHistoryStore
from ravn.ports.valkyrie_history import ValkyrieHistoryStore

HISTORY_DATABASE_URL_ENV = "RAVN_VALKYRIE_HISTORY_DATABASE_URL"
#: The review queue DSN doubles as the history DSN so one configured
#: database gives both durable reviews and durable decision history.
FALLBACK_DATABASE_URL_ENV = "RAVN_ODIN_REVIEW_DATABASE_URL"


class LazyPostgresValkyrieHistoryStore(ValkyrieHistoryStore):
    """Connect on first use so app startup never blocks on the database."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._store: ValkyrieHistoryStore | None = None

    async def _delegate(self) -> ValkyrieHistoryStore:
        if self._store is None:
            import asyncpg  # noqa: PLC0415

            pool = await asyncpg.create_pool(
                dsn=self._dsn,
                min_size=AUX_POOL_MIN_SIZE,
                max_size=AUX_POOL_MAX_SIZE,
            )
            self._store = PostgresValkyrieHistoryStore(pool)
        return self._store

    async def record_decision(self, record: dict[str, Any]) -> None:
        await (await self._delegate()).record_decision(record)

    async def record_action(self, record: dict[str, Any]) -> None:
        await (await self._delegate()).record_action(record)

    async def record_signal(self, record: dict[str, Any]) -> None:
        await (await self._delegate()).record_signal(record)

    async def list_decisions(self, **kwargs: Any) -> tuple[list[dict[str, Any]], int]:
        return await (await self._delegate()).list_decisions(**kwargs)

    async def get_decision(self, decision_id: str) -> dict[str, Any] | None:
        return await (await self._delegate()).get_decision(decision_id)

    async def list_signals(self, **kwargs: Any) -> tuple[list[dict[str, Any]], int]:
        return await (await self._delegate()).list_signals(**kwargs)

    async def signals_by_ids(self, signal_ids: list[str]) -> list[dict[str, Any]]:
        return await (await self._delegate()).signals_by_ids(signal_ids)

    async def actions_for_correlation(self, correlation_id: str) -> list[dict[str, Any]]:
        return await (await self._delegate()).actions_for_correlation(correlation_id)

    async def record_decision_outcome(self, **kwargs: Any) -> int:
        return await (await self._delegate()).record_decision_outcome(**kwargs)

    async def link_review_item(self, decision_id: str, review_item_id: str) -> None:
        await (await self._delegate()).link_review_item(decision_id, review_item_id)


def build_valkyrie_history_store_from_env() -> ValkyrieHistoryStore:
    """Postgres when a DSN is configured, otherwise in-memory."""
    dsn = (
        os.environ.get(HISTORY_DATABASE_URL_ENV, "").strip()
        or os.environ.get(FALLBACK_DATABASE_URL_ENV, "").strip()
    )
    if dsn:
        return LazyPostgresValkyrieHistoryStore(dsn)
    return InMemoryValkyrieHistoryStore()


__all__ = [
    "FALLBACK_DATABASE_URL_ENV",
    "HISTORY_DATABASE_URL_ENV",
    "InMemoryValkyrieHistoryStore",
    "LazyPostgresValkyrieHistoryStore",
    "PostgresValkyrieHistoryStore",
    "build_valkyrie_history_store_from_env",
]
