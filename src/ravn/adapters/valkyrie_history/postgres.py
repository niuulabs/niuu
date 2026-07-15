"""PostgreSQL-backed Valkyrie history store (raw SQL, asyncpg)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from ravn.ports.valkyrie_history import ValkyrieHistoryStore

_UPSERT_DECISION_SQL = """
INSERT INTO valkyrie_decisions (
    decision_id, environment_id, valkyrie_id, operational_state, tier,
    action_authority, confidence, correlation_id, outcome, review_item_id,
    payload, decided_at, updated_at
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, NOW())
ON CONFLICT (decision_id) DO UPDATE SET
    environment_id = EXCLUDED.environment_id,
    valkyrie_id = EXCLUDED.valkyrie_id,
    operational_state = EXCLUDED.operational_state,
    tier = EXCLUDED.tier,
    action_authority = EXCLUDED.action_authority,
    confidence = EXCLUDED.confidence,
    correlation_id = EXCLUDED.correlation_id,
    outcome = CASE
        WHEN EXCLUDED.outcome = '' THEN valkyrie_decisions.outcome
        ELSE EXCLUDED.outcome
    END,
    review_item_id = CASE
        WHEN EXCLUDED.review_item_id = '' THEN valkyrie_decisions.review_item_id
        ELSE EXCLUDED.review_item_id
    END,
    payload = valkyrie_decisions.payload || EXCLUDED.payload,
    decided_at = EXCLUDED.decided_at,
    updated_at = NOW()
"""

_UPSERT_ACTION_SQL = """
INSERT INTO valkyrie_actions (
    event_id, action_id, environment_id, valkyrie_id, status,
    correlation_id, payload, observed_at, updated_at
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
ON CONFLICT (event_id) DO UPDATE SET
    status = EXCLUDED.status,
    payload = EXCLUDED.payload,
    observed_at = EXCLUDED.observed_at,
    updated_at = NOW()
"""

_UPSERT_SIGNAL_SQL = """
INSERT INTO valkyrie_signals (
    signal_id, environment_id, severity, payload, received_at, updated_at
) VALUES ($1, $2, $3, $4, $5, NOW())
ON CONFLICT (signal_id) DO UPDATE SET
    severity = EXCLUDED.severity,
    payload = EXCLUDED.payload,
    received_at = EXCLUDED.received_at,
    updated_at = NOW()
"""


class PostgresValkyrieHistoryStore(ValkyrieHistoryStore):
    """Store history rows with the full record as JSONB plus filter columns."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def record_decision(self, record: dict[str, Any]) -> None:
        await self._pool.execute(
            _UPSERT_DECISION_SQL,
            str(record["decisionId"]),
            str(record.get("environmentId") or "unknown"),
            str(record.get("valkyrieId") or ""),
            str(record.get("operationalState") or ""),
            str(record.get("tier") or ""),
            str(record.get("actionAuthority") or ""),
            float(record.get("confidence") or 0.0),
            str(record.get("correlationId") or ""),
            str(record.get("outcome") or ""),
            str(record.get("reviewItemId") or ""),
            json.dumps(record),
            _as_datetime(record.get("decidedAt")),
        )

    async def record_action(self, record: dict[str, Any]) -> None:
        await self._pool.execute(
            _UPSERT_ACTION_SQL,
            str(record["eventId"]),
            str(record.get("actionId") or ""),
            str(record.get("environmentId") or "unknown"),
            str(record.get("valkyrieId") or ""),
            str(record.get("status") or ""),
            str(record.get("correlationId") or ""),
            json.dumps(record),
            _as_datetime(record.get("observedAt")),
        )

    async def record_signal(self, record: dict[str, Any]) -> None:
        await self._pool.execute(
            _UPSERT_SIGNAL_SQL,
            str(record["signalId"]),
            str(record.get("environmentId") or "unknown"),
            str(record.get("severity") or "info"),
            json.dumps(record),
            _as_datetime(record.get("receivedAt")),
        )

    async def list_decisions(
        self,
        *,
        environment_id: str = "",
        valkyrie_id: str = "",
        operational_state: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        clauses: list[str] = []
        params: list[Any] = []
        if environment_id:
            params.append(environment_id)
            clauses.append(f"environment_id = ${len(params)}")
        if valkyrie_id:
            params.append(valkyrie_id)
            clauses.append(f"valkyrie_id = ${len(params)}")
        if operational_state:
            params.append(operational_state)
            clauses.append(f"operational_state = ${len(params)}")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        total_row = await self._pool.fetchrow(
            f"SELECT COUNT(*) AS total FROM valkyrie_decisions {where}",
            *params,
        )
        params.append(max(limit, 0))
        limit_pos = len(params)
        params.append(max(offset, 0))
        rows = await self._pool.fetch(
            f"SELECT payload FROM valkyrie_decisions {where} "
            f"ORDER BY decided_at DESC LIMIT ${limit_pos} OFFSET ${limit_pos + 1}",
            *params,
        )
        return [_payload(row) for row in rows], int(total_row["total"])

    async def get_decision(self, decision_id: str) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(
            "SELECT payload FROM valkyrie_decisions WHERE decision_id = $1",
            decision_id,
        )
        if row is None:
            return None
        return _payload(row)

    async def list_signals(
        self,
        *,
        environment_id: str = "",
        severity: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        clauses: list[str] = []
        params: list[Any] = []
        if environment_id:
            params.append(environment_id)
            clauses.append(f"environment_id = ${len(params)}")
        if severity:
            params.append(severity)
            clauses.append(f"severity = ${len(params)}")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        total_row = await self._pool.fetchrow(
            f"SELECT COUNT(*) AS total FROM valkyrie_signals {where}",
            *params,
        )
        params.append(max(limit, 0))
        limit_pos = len(params)
        params.append(max(offset, 0))
        rows = await self._pool.fetch(
            f"SELECT payload FROM valkyrie_signals {where} "
            f"ORDER BY received_at DESC LIMIT ${limit_pos} OFFSET ${limit_pos + 1}",
            *params,
        )
        return [_payload(row) for row in rows], int(total_row["total"])

    async def signals_by_ids(self, signal_ids: list[str]) -> list[dict[str, Any]]:
        if not signal_ids:
            return []
        rows = await self._pool.fetch(
            "SELECT signal_id, payload FROM valkyrie_signals WHERE signal_id = ANY($1::text[])",
            signal_ids,
        )
        by_id = {str(row["signal_id"]): _payload(row) for row in rows}
        return [by_id[sid] for sid in signal_ids if sid in by_id]

    async def actions_for_correlation(self, correlation_id: str) -> list[dict[str, Any]]:
        if not correlation_id:
            return []
        rows = await self._pool.fetch(
            "SELECT payload FROM valkyrie_actions WHERE correlation_id = $1 "
            "ORDER BY observed_at DESC",
            correlation_id,
        )
        return [_payload(row) for row in rows]

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
        result = await self._pool.execute(
            "UPDATE valkyrie_decisions SET outcome = $2, "
            "payload = payload || $3::jsonb, updated_at = NOW() "
            "WHERE correlation_id = $1",
            correlation_id,
            outcome,
            json.dumps({"outcome": outcome, "outcomeDetail": detail, "outcomeAt": outcome_at}),
        )
        return _update_count(result)

    async def link_review_item(self, decision_id: str, review_item_id: str) -> None:
        await self._pool.execute(
            "UPDATE valkyrie_decisions SET review_item_id = $2, "
            "payload = payload || $3::jsonb, updated_at = NOW() "
            "WHERE decision_id = $1",
            decision_id,
            review_item_id,
            json.dumps({"reviewItemId": review_item_id}),
        )


def _payload(row: Any) -> dict[str, Any]:
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return dict(payload)


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return datetime.now(UTC)


def _update_count(result: Any) -> int:
    # asyncpg execute() returns a status tag like "UPDATE 3".
    try:
        return int(str(result).rsplit(" ", 1)[-1])
    except (TypeError, ValueError):
        return 0
