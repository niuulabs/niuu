"""PostgreSQL adapter for durable resident runtime records."""

from __future__ import annotations

import json
from uuid import UUID

import asyncpg

from volundr.domain.models import (
    ResidentBackend,
    ResidentCapability,
    ResidentCondition,
    ResidentDesiredState,
    ResidentEndpoint,
    ResidentEngine,
    ResidentObservedState,
    ResidentRuntime,
)
from volundr.domain.ports import ResidentRuntimeRepository


class PostgresResidentRuntimeRepository(ResidentRuntimeRepository):
    """Raw-SQL resident runtime persistence."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create(self, runtime: ResidentRuntime) -> ResidentRuntime:
        await self._pool.execute(
            """
            INSERT INTO resident_runtimes
                (id, owner_id, tenant_id, name, persona_name, model, backend,
                 engine, profile_id, desired_state, observed_state, backend_ref,
                 endpoints, capabilities, conditions, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                    $13, $14, $15, $16, $17)
            """,
            runtime.id,
            runtime.owner_id,
            runtime.tenant_id,
            runtime.name,
            runtime.persona_name,
            runtime.model,
            runtime.backend.value,
            runtime.engine.value,
            runtime.profile_id,
            runtime.desired_state.value,
            runtime.observed_state.value,
            json.dumps(runtime.backend_ref),
            json.dumps([endpoint.model_dump(mode="json") for endpoint in runtime.endpoints]),
            [capability.value for capability in runtime.capabilities],
            json.dumps([condition.model_dump(mode="json") for condition in runtime.conditions]),
            runtime.created_at,
            runtime.updated_at,
        )
        return runtime

    async def get(self, runtime_id: UUID) -> ResidentRuntime | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM resident_runtimes WHERE id = $1",
            runtime_id,
        )
        return self._row_to_runtime(row) if row is not None else None

    async def get_by_owner_name(self, owner_id: str, name: str) -> ResidentRuntime | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM resident_runtimes WHERE owner_id = $1 AND name = $2",
            owner_id,
            name,
        )
        return self._row_to_runtime(row) if row is not None else None

    async def list(
        self,
        *,
        tenant_id: str,
        owner_id: str | None = None,
    ) -> list[ResidentRuntime]:
        if owner_id is not None:
            rows = await self._pool.fetch(
                """
                SELECT * FROM resident_runtimes
                WHERE tenant_id = $1 AND owner_id = $2
                ORDER BY updated_at DESC
                """,
                tenant_id,
                owner_id,
            )
            return [self._row_to_runtime(row) for row in rows]

        rows = await self._pool.fetch(
            """
            SELECT * FROM resident_runtimes
            WHERE tenant_id = $1
            ORDER BY updated_at DESC
            """,
            tenant_id,
        )
        return [self._row_to_runtime(row) for row in rows]

    async def update(self, runtime: ResidentRuntime) -> ResidentRuntime:
        await self._pool.execute(
            """
            UPDATE resident_runtimes
            SET name = $2, persona_name = $3, model = $4, backend = $5,
                engine = $6, profile_id = $7, desired_state = $8,
                observed_state = $9, backend_ref = $10, endpoints = $11,
                capabilities = $12, conditions = $13, updated_at = $14
            WHERE id = $1
            """,
            runtime.id,
            runtime.name,
            runtime.persona_name,
            runtime.model,
            runtime.backend.value,
            runtime.engine.value,
            runtime.profile_id,
            runtime.desired_state.value,
            runtime.observed_state.value,
            json.dumps(runtime.backend_ref),
            json.dumps([endpoint.model_dump(mode="json") for endpoint in runtime.endpoints]),
            [capability.value for capability in runtime.capabilities],
            json.dumps([condition.model_dump(mode="json") for condition in runtime.conditions]),
            runtime.updated_at,
        )
        return runtime

    async def list_for_reconciliation(self) -> list[ResidentRuntime]:
        rows = await self._pool.fetch(
            """
            SELECT * FROM resident_runtimes
            WHERE desired_state <> 'deleted'
            ORDER BY updated_at ASC
            """
        )
        return [self._row_to_runtime(row) for row in rows]

    async def delete(self, runtime_id: UUID) -> bool:
        result = await self._pool.execute(
            "DELETE FROM resident_runtimes WHERE id = $1",
            runtime_id,
        )
        return result == "DELETE 1"

    @staticmethod
    def _json(value: object, fallback: object) -> object:
        if isinstance(value, str):
            return json.loads(value)
        return value if value is not None else fallback

    @classmethod
    def _row_to_runtime(cls, row: asyncpg.Record) -> ResidentRuntime:
        endpoints = cls._json(row["endpoints"], [])
        conditions = cls._json(row["conditions"], [])
        capabilities = row["capabilities"] or []
        return ResidentRuntime(
            id=row["id"],
            owner_id=row["owner_id"],
            tenant_id=row["tenant_id"],
            name=row["name"],
            persona_name=row["persona_name"],
            model=row["model"],
            backend=ResidentBackend(row["backend"]),
            engine=ResidentEngine(row["engine"]),
            profile_id=row["profile_id"],
            desired_state=ResidentDesiredState(row["desired_state"]),
            observed_state=ResidentObservedState(row["observed_state"]),
            backend_ref=cls._json(row["backend_ref"], {}),
            endpoints=[ResidentEndpoint.model_validate(endpoint) for endpoint in endpoints],
            capabilities=[ResidentCapability(capability) for capability in capabilities],
            conditions=[ResidentCondition.model_validate(condition) for condition in conditions],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
