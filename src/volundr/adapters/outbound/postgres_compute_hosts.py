"""PostgreSQL inventory of registered hosts and their single-use pairing codes.

A host is a ``compute_hosts`` row whose JSONB ``data`` is the ``RegisteredHost``; the
columns repeat the fields queries filter on, and table checks keep them equal to ``data``.
Only ``claim`` and ``release`` move ``allocation_id``; ``save`` never does.
"""

from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID

import asyncpg
from pydantic import JsonValue

from volundr.domain.compute import HostSelector
from volundr.domain.compute_hosts import (
    HostConflictError,
    HostHealth,
    HostPairingCode,
    RegisteredHost,
    RegisteredHostRepository,
)

_USABLE_CODE = "consumed_at IS NULL AND revoked_at IS NULL AND expires_at > NOW()"
#: Migration 000109: a reverse-SSH tunnel port belongs to one host of the Forge.
_TUNNEL_PORT_INDEX = "idx_compute_hosts_tunnel_port"


class PostgresRegisteredHostRepository(RegisteredHostRepository):
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create_pairing_code(self, code: HostPairingCode) -> None:
        await self._pool.execute(
            """INSERT INTO compute_host_pairing_codes (id, code_hash, pool_id, expires_at, data)
               VALUES ($1, $2, $3, $4, $5::jsonb)""",
            code.id,
            code.code_hash,
            code.pool_id,
            code.expires_at,
            code.model_dump_json(),
        )

    async def pairing_code(self, code_hash: str) -> HostPairingCode | None:
        data = await self._pool.fetchval(
            f"SELECT data FROM compute_host_pairing_codes WHERE code_hash = $1 AND {_USABLE_CODE}",
            code_hash,
        )
        return HostPairingCode.model_validate_json(data) if data else None

    async def pairing_codes(self, pool_id: str) -> list[HostPairingCode]:
        rows = await self._pool.fetch(
            f"SELECT data FROM compute_host_pairing_codes WHERE pool_id = $1 AND {_USABLE_CODE} "
            "ORDER BY created_at DESC",
            pool_id,
        )
        return [HostPairingCode.model_validate_json(row["data"]) for row in rows]

    async def revoke_pairing_code(self, pool_id: str, code_id: UUID) -> bool:
        result = await self._pool.execute(
            "UPDATE compute_host_pairing_codes SET revoked_at = NOW(), "
            "data = jsonb_set(data, '{revoked_at}', to_jsonb(NOW())) "
            f"WHERE id = $1 AND pool_id = $2 AND {_USABLE_CODE}",
            code_id,
            pool_id,
        )
        return result == "UPDATE 1"

    async def consume_and_register(
        self, code_hash: str, host: RegisteredHost
    ) -> RegisteredHost | None:
        async with self._pool.acquire() as conn, conn.transaction():
            consumed = await conn.fetchval(
                # $2 is the host's uuid in both places: a second type for it (text) is a
                # statement Postgres refuses to prepare.
                "UPDATE compute_host_pairing_codes SET consumed_at = NOW(), "
                "consumed_by_host_id = $2::uuid, data = data "
                "|| jsonb_build_object('consumed_at', to_jsonb(NOW()), "
                "'consumed_by_host_id', to_jsonb($2::uuid)) "
                f"WHERE code_hash = $1 AND {_USABLE_CODE} RETURNING id",
                code_hash,
                host.id,
            )
            if consumed is None:
                return None
            try:
                await conn.execute(
                    """INSERT INTO compute_hosts
                       (id, pool_id, name, host_public_key, allocation_id, health, service, data)
                       VALUES ($1, $2, $3, $4, NULL, $5, $6, $7::jsonb)""",
                    host.id,
                    host.pool_id,
                    host.name,
                    host.host_public_key,
                    host.health.value,
                    host.service.value,
                    host.model_dump_json(),
                )
            except asyncpg.UniqueViolationError as exc:
                # Raising rolls the transaction back, so the code stays usable.
                if exc.constraint_name == _TUNNEL_PORT_INDEX:
                    raise HostConflictError(
                        "Another host joining at the same moment was given the same tunnel "
                        "port; run the join command again"
                    ) from exc
                raise HostConflictError(
                    f"A host named {host.name!r} or with this host key is already registered"
                ) from exc
            return host

    async def get(self, host_id: UUID) -> RegisteredHost | None:
        data = await self._pool.fetchval("SELECT data FROM compute_hosts WHERE id = $1", host_id)
        return RegisteredHost.model_validate_json(data) if data else None

    async def with_public_key(self, public_key: str) -> RegisteredHost | None:
        data = await self._pool.fetchval(
            "SELECT data FROM compute_hosts WHERE host_public_key = $1", public_key
        )
        return RegisteredHost.model_validate_json(data) if data else None

    async def connection_values(self, field: str) -> list[JsonValue]:
        rows = await self._pool.fetch(
            "SELECT data->'connection'->$1 AS value FROM compute_hosts "
            "WHERE data->'connection' ? $1",
            field,
        )
        return [json.loads(row["value"]) for row in rows]

    async def held_by(self, allocation_id: UUID) -> RegisteredHost | None:
        data = await self._pool.fetchval(
            "SELECT data FROM compute_hosts WHERE allocation_id = $1", allocation_id
        )
        return RegisteredHost.model_validate_json(data) if data else None

    async def list(self, pool_id: str) -> list[RegisteredHost]:
        rows = await self._pool.fetch(
            "SELECT data FROM compute_hosts WHERE pool_id = $1 ORDER BY name", pool_id
        )
        return [RegisteredHost.model_validate_json(row["data"]) for row in rows]

    async def save(self, host: RegisteredHost) -> None:
        operator = host.model_dump(mode="json", include={"name", "tags", "slots", "service"})
        try:
            result = await self._pool.execute(
                """UPDATE compute_hosts
                   SET name = $2, service = $3, data = data || $4::jsonb, updated_at = NOW()
                   WHERE id = $1""",
                host.id,
                host.name,
                host.service.value,
                json.dumps(operator),
            )
        except asyncpg.UniqueViolationError as exc:
            raise HostConflictError(f"A host named {host.name!r} is already registered") from exc
        if result != "UPDATE 1":
            raise LookupError(f"Registered host {host.id} does not exist")

    async def record_check(
        self,
        host_id: UUID,
        *,
        facts: dict[str, JsonValue],
        findings: tuple[str, ...],
        health: HostHealth,
        checked_at: datetime,
    ) -> RegisteredHost:
        result = {
            "facts": facts,
            "findings": list(findings),
            "health": health.value,
            "last_checked_at": checked_at.isoformat(),
        }
        data = await self._pool.fetchval(
            """UPDATE compute_hosts SET health = $2, data = data || $3::jsonb, updated_at = NOW()
               WHERE id = $1 RETURNING data""",
            host_id,
            health.value,
            json.dumps(result),
        )
        if data is None:
            raise LookupError(f"Registered host {host_id} does not exist")
        return RegisteredHost.model_validate_json(data)

    async def claim(
        self, pool_id: str, allocation_id: UUID, profile: str, selector: HostSelector
    ) -> RegisteredHost | None:
        async with self._pool.acquire() as conn, conn.transaction():
            held = await conn.fetchval(
                "SELECT data FROM compute_hosts WHERE allocation_id = $1", allocation_id
            )
            if held is not None:
                return RegisteredHost.model_validate_json(held)
            tags = list(selector.tags)
            data = await conn.fetchval(
                """UPDATE compute_hosts
                   SET allocation_id = $2::uuid,
                       data = jsonb_set(data, '{allocation_id}', to_jsonb($2::uuid)),
                       updated_at = NOW()
                   WHERE id = (
                       SELECT id FROM compute_hosts
                       WHERE pool_id = $1 AND allocation_id IS NULL
                         AND health = 'healthy' AND service = 'active'
                         AND data->>'profile' = $3
                         AND jsonb_typeof(data->'slots') = 'number'
                         AND (cardinality($4::text[]) = 0
                              OR ($5 = 'all' AND data->'tags' @> to_jsonb($4::text[]))
                              OR ($5 = 'any' AND data->'tags' ?| $4::text[]))
                       ORDER BY (data->>'slots')::integer DESC, name
                       LIMIT 1
                       FOR UPDATE SKIP LOCKED)
                   RETURNING data""",
                pool_id,
                allocation_id,
                profile,
                tags,
                selector.match,
            )
            return RegisteredHost.model_validate_json(data) if data else None

    async def release(self, allocation_id: UUID, update: RegisteredHost) -> None:
        released = update.model_copy(update={"allocation_id": None})
        await self._pool.execute(
            """UPDATE compute_hosts
               SET allocation_id = NULL, health = $2, service = $3, data = $4::jsonb,
                   updated_at = NOW()
               WHERE allocation_id = $1""",
            allocation_id,
            released.health.value,
            released.service.value,
            released.model_dump_json(),
        )

    async def delete(self, host_id: UUID) -> bool:
        result = await self._pool.execute(
            "DELETE FROM compute_hosts WHERE id = $1 AND allocation_id IS NULL", host_id
        )
        return result == "DELETE 1"
