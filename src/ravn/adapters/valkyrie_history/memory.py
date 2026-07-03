"""In-memory Valkyrie history store — dev, proofs, and unit tests."""

from __future__ import annotations

from typing import Any

from ravn.ports.valkyrie_history import ValkyrieHistoryStore

DEFAULT_MAX_RECORDS_PER_KIND = 5_000


class InMemoryValkyrieHistoryStore(ValkyrieHistoryStore):
    """Keep history in process memory, capped per record kind."""

    def __init__(self, *, max_records_per_kind: int = DEFAULT_MAX_RECORDS_PER_KIND) -> None:
        self._max_records = max(max_records_per_kind, 1)
        self._decisions: dict[str, dict[str, Any]] = {}
        self._actions: dict[str, dict[str, Any]] = {}
        self._signals: dict[str, dict[str, Any]] = {}

    async def record_decision(self, record: dict[str, Any]) -> None:
        existing = self._decisions.get(str(record["decisionId"])) or {}
        merged = {**existing, **record}
        # Replays must never erase what happened after the judgment: the
        # stamped outcome and the inbox link only move forward.
        for stamped in ("outcome", "outcomeDetail", "outcomeAt", "reviewItemId"):
            if not merged.get(stamped) and existing.get(stamped):
                merged[stamped] = existing[stamped]
        self._decisions[str(record["decisionId"])] = merged
        self._trim(self._decisions, "decidedAt")

    async def record_action(self, record: dict[str, Any]) -> None:
        self._actions[str(record["eventId"])] = dict(record)
        self._trim(self._actions, "observedAt")

    async def record_signal(self, record: dict[str, Any]) -> None:
        self._signals[str(record["signalId"])] = dict(record)
        self._trim(self._signals, "receivedAt")

    async def list_decisions(
        self,
        *,
        environment_id: str = "",
        valkyrie_id: str = "",
        operational_state: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = [
            dict(record)
            for record in self._decisions.values()
            if (not environment_id or record.get("environmentId") == environment_id)
            and (not valkyrie_id or record.get("valkyrieId") == valkyrie_id)
            and (not operational_state or record.get("operationalState") == operational_state)
        ]
        rows.sort(key=lambda record: str(record.get("decidedAt") or ""), reverse=True)
        return _page(rows, limit=limit, offset=offset)

    async def get_decision(self, decision_id: str) -> dict[str, Any] | None:
        record = self._decisions.get(decision_id)
        return dict(record) if record else None

    async def list_signals(
        self,
        *,
        environment_id: str = "",
        severity: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = [
            dict(record)
            for record in self._signals.values()
            if (not environment_id or record.get("environmentId") == environment_id)
            and (not severity or record.get("severity") == severity)
        ]
        rows.sort(key=lambda record: str(record.get("receivedAt") or ""), reverse=True)
        return _page(rows, limit=limit, offset=offset)

    async def signals_by_ids(self, signal_ids: list[str]) -> list[dict[str, Any]]:
        return [dict(self._signals[sid]) for sid in signal_ids if sid in self._signals]

    async def actions_for_correlation(self, correlation_id: str) -> list[dict[str, Any]]:
        if not correlation_id:
            return []
        rows = [
            dict(record)
            for record in self._actions.values()
            if record.get("correlationId") == correlation_id
        ]
        rows.sort(key=lambda record: str(record.get("observedAt") or ""), reverse=True)
        return rows

    async def record_decision_outcome(
        self,
        *,
        correlation_id: str,
        outcome: str,
        detail: str,
        outcome_at: str,
    ) -> int:
        if not correlation_id:
            return 0
        updated = 0
        for record in self._decisions.values():
            if record.get("correlationId") != correlation_id:
                continue
            record["outcome"] = outcome
            record["outcomeDetail"] = detail
            record["outcomeAt"] = outcome_at
            updated += 1
        return updated

    async def link_review_item(self, decision_id: str, review_item_id: str) -> None:
        record = self._decisions.get(decision_id)
        if record is not None:
            record["reviewItemId"] = review_item_id

    def _trim(self, records: dict[str, dict[str, Any]], timestamp_key: str) -> None:
        overflow = len(records) - self._max_records
        if overflow <= 0:
            return
        oldest = sorted(
            records.items(),
            key=lambda entry: str(entry[1].get(timestamp_key) or ""),
        )[:overflow]
        for key, _ in oldest:
            del records[key]


def _page(
    rows: list[dict[str, Any]],
    *,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    total = len(rows)
    start = max(offset, 0)
    return rows[start : start + max(limit, 0)], total
