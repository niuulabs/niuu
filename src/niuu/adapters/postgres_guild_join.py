"""PostgreSQL adapter for the atomic node-join transaction."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import asyncpg

from niuu.domain.models import (
    InstanceHealthStatus,
    PairingCode,
    RegisteredInstance,
    RegisteredNode,
)
from niuu.ports.guild_join_repository import (
    GuildJoinRepository,
    InstanceWrite,
    NodeConflictError,
)


class PostgresGuildJoinRepository(GuildJoinRepository):
    """Runs pairing-code consumption, node creation, and instance registration
    as one database transaction — see the port docstring for why."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def consume_and_register(
        self,
        *,
        code_hash: str,
        node_name: str,
        public_key: str,
        instances: list[InstanceWrite],
    ) -> tuple[PairingCode, RegisteredNode, list[RegisteredInstance]] | None:
        async with self._pool.acquire() as conn, conn.transaction():
            code_row = await conn.fetchrow(
                """
                UPDATE niuu_pairing_codes
                SET consumed_at = NOW()
                WHERE code_hash = $1 AND consumed_at IS NULL AND expires_at > NOW()
                RETURNING id, code_hash, created_by, tenant_id, allow_plaintext,
                          allow_untrusted_node_auth, expires_at, created_at
                """,
                code_hash,
            )
            if code_row is None:
                return None

            node_id = str(uuid4())
            try:
                node_row = await conn.fetchrow(
                    """
                    INSERT INTO niuu_nodes
                        (id, name, public_key, tenant_id, created_by, allow_plaintext)
                    VALUES ($1::uuid, $2, $3, $4, $5, $6)
                    RETURNING id, name, public_key, tenant_id, created_by, allow_plaintext,
                              created_at, last_seen_at, last_request_at
                    """,
                    node_id,
                    node_name,
                    public_key,
                    code_row["tenant_id"],
                    code_row["created_by"],
                    code_row["allow_plaintext"],
                )
            except asyncpg.UniqueViolationError as exc:
                raise NodeConflictError(
                    f"Node name {node_name!r} or public key is already registered"
                ) from exc

            await conn.execute(
                "UPDATE niuu_pairing_codes SET consumed_by_node_id = $1::uuid WHERE id = $2::uuid",
                node_id,
                code_row["id"],
            )

            saved_instances: list[RegisteredInstance] = []
            now = datetime.now(UTC)
            for offered in instances:
                instance_id = str(uuid4())
                await conn.execute(
                    """
                    INSERT INTO niuu_instances (
                        id, kind, slug, name, base_url, visibility, owner_id, tenant_id,
                        enabled, is_default, config, created_at, updated_at, tags,
                        health, node_id
                    )
                    VALUES (
                        $1::uuid, $2, $3, $4, $5, $6, NULL, $7, true, false, $8::jsonb,
                        $9, $9, '[]'::jsonb, $10, $11::uuid
                    )
                    """,
                    instance_id,
                    str(offered.kind),
                    offered.slug,
                    offered.name,
                    offered.base_url,
                    str(offered.visibility),
                    offered.tenant_id,
                    json.dumps(offered.config),
                    now,
                    str(InstanceHealthStatus.UNKNOWN),
                    node_id,
                )
                saved_instances.append(
                    RegisteredInstance(
                        id=instance_id,
                        kind=offered.kind,
                        slug=offered.slug,
                        name=offered.name,
                        base_url=offered.base_url,
                        visibility=offered.visibility,
                        owner_id=None,
                        tenant_id=offered.tenant_id,
                        enabled=True,
                        is_default=False,
                        config=offered.config,
                        created_at=now,
                        updated_at=now,
                        node_id=node_id,
                    )
                )

            return (
                PairingCode(
                    id=str(code_row["id"]),
                    code_hash=code_row["code_hash"],
                    created_by=code_row["created_by"],
                    tenant_id=code_row["tenant_id"],
                    expires_at=code_row["expires_at"],
                    created_at=code_row["created_at"],
                    allow_plaintext=code_row["allow_plaintext"],
                    allow_untrusted_node_auth=code_row["allow_untrusted_node_auth"],
                    consumed_at=now,
                    consumed_by_node_id=node_id,
                ),
                RegisteredNode(
                    id=str(node_row["id"]),
                    name=node_row["name"],
                    public_key=node_row["public_key"],
                    tenant_id=node_row["tenant_id"],
                    created_by=node_row["created_by"],
                    created_at=node_row["created_at"],
                    allow_plaintext=node_row["allow_plaintext"],
                    last_seen_at=node_row["last_seen_at"],
                    last_request_at=node_row["last_request_at"],
                ),
                saved_instances,
            )
