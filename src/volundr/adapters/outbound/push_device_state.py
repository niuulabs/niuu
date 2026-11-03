"""The push gateway's per-token state (``device_tokens``): never a token, only what is
queried and locked.

:class:`~volundr.adapters.outbound.integration_device_tokens.IntegrationDeviceRegistry`
keeps devices as integration connections and tokens in the credential store; this
store holds one row per token naming its credential and connection, with the kind,
environment, activity, throttle columns and the token's SHA-256.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import asyncpg


@dataclass(frozen=True)
class DeviceStateRow:
    """One registered token's gateway state."""

    id: UUID
    owner_id: str
    platform: str
    app_bundle_id: str | None
    kind: str
    environment: str
    activity_id: str | None
    connection_id: str
    credential_name: str
    token_sha256: str
    created_at: datetime
    updated_at: datetime
    last_push_at: datetime | None = None


@dataclass(frozen=True)
class StoredToken:
    """A row written before tokens left Postgres: it still holds its token."""

    id: UUID
    owner_id: str
    platform: str
    app_bundle_id: str | None
    kind: str
    environment: str
    token: str
    created_at: datetime


class DeviceStateStore(ABC):
    """Rows of ``device_tokens``. A removal returns the rows it removed, so the
    registry can delete their credentials."""

    @abstractmethod
    async def get(self, credential_name: str) -> DeviceStateRow | None:
        """The row of a credential."""

    @abstractmethod
    async def get_by_id(self, row_id: UUID) -> DeviceStateRow | None:
        """A row by id."""

    @abstractmethod
    async def owner_rows(self, owner_id: str) -> list[DeviceStateRow]:
        """Every row of an owner."""

    @abstractmethod
    async def owner_rows_with_token(self, owner_id: str, token_sha256: str) -> list[DeviceStateRow]:
        """An owner's rows of one token (any kind)."""

    @abstractmethod
    async def holder(
        self, token_sha256: str, kind: str, credential_name: str
    ) -> DeviceStateRow | None:
        """A row of another credential that holds this token and kind."""

    @abstractmethod
    async def register(self, row: DeviceStateRow) -> tuple[DeviceStateRow, list[DeviceStateRow]]:
        """Write the row (keyed by its credential name) in one transaction.

        Any other row holding the same token and kind is removed (the token moved
        to this owner or install), and for a Live Activity token any earlier row of
        the same connection and activity. Returns the stored row and the removed ones.
        """

    @abstractmethod
    async def set_token_sha(self, row_id: UUID, token_sha256: str) -> None:
        """The credential now holds another token (re-registered on another node)."""

    @abstractmethod
    async def delete(self, row_id: UUID) -> list[DeviceStateRow]:
        """Remove one row."""

    @abstractmethod
    async def delete_connection(self, connection_id: str) -> list[DeviceStateRow]:
        """Remove every row of a device."""

    @abstractmethod
    async def claim_push(self, row_id: UUID, *, min_interval_seconds: float) -> bool:
        """Claim a throttled push, or mark it owed (see ``PushTokenRegistry``)."""

    @abstractmethod
    async def release_push(self, row_id: UUID) -> None:
        """Undo a claim whose push was not delivered."""

    @abstractmethod
    async def claim_owed_pushes(
        self, kind: str, *, min_interval_seconds: float
    ) -> list[DeviceStateRow]:
        """Claim every owed push of ``kind`` whose interval elapsed."""

    @abstractmethod
    async def count_by_environment(self) -> dict[str, dict[str, int]]:
        """Rows per environment and kind."""

    @abstractmethod
    async def stored_tokens(self) -> list[StoredToken]:
        """Rows that still hold their token, oldest first."""


_INTERVAL_ELAPSED = (
    "(last_push_at IS NULL OR last_push_at <= now() - make_interval(secs => $2::double precision))"
)
_MOVED = "credential_name IS NOT NULL"

SELECT_BY_CREDENTIAL_SQL = "SELECT * FROM device_tokens WHERE credential_name = $1"
SELECT_BY_ID_SQL = f"SELECT * FROM device_tokens WHERE id = $1 AND {_MOVED}"
SELECT_OWNER_ROWS_SQL = f"SELECT * FROM device_tokens WHERE owner_id = $1 AND {_MOVED}"
SELECT_OWNER_TOKEN_SQL = f"""SELECT * FROM device_tokens
WHERE owner_id = $1 AND token_sha256 = $2 AND {_MOVED}"""
HOLDER_SQL = """SELECT * FROM device_tokens
WHERE token_sha256 = $1 AND kind = $2 AND credential_name IS DISTINCT FROM $3
LIMIT 1"""

# A token belongs to whoever is signed in on that install now: another row holding
# it (another owner's, or one written before tokens left Postgres) is taken over.
TAKE_OVER_SQL = """DELETE FROM device_tokens
WHERE token_sha256 = $1 AND kind = $2 AND credential_name IS DISTINCT FROM $3
RETURNING *"""

# A running Live Activity has one update token: a new one replaces the earlier.
REPLACE_ACTIVITY_TOKEN_SQL = """DELETE FROM device_tokens
WHERE connection_id = $1 AND kind = $2 AND activity_id = $3 AND credential_name <> $4
RETURNING *"""

UPSERT_SQL = """INSERT INTO device_tokens
    (id, owner_id, platform, app_bundle_id, kind, environment, activity_id,
     connection_id, credential_name, token_sha256, created_at, updated_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
ON CONFLICT (credential_name) WHERE credential_name IS NOT NULL DO UPDATE
    SET owner_id = EXCLUDED.owner_id,
        platform = EXCLUDED.platform,
        app_bundle_id = EXCLUDED.app_bundle_id,
        environment = EXCLUDED.environment,
        activity_id = EXCLUDED.activity_id,
        connection_id = EXCLUDED.connection_id,
        token_sha256 = EXCLUDED.token_sha256,
        updated_at = EXCLUDED.updated_at
RETURNING *"""

UPDATE_SHA_SQL = "UPDATE device_tokens SET token_sha256 = $2 WHERE id = $1"
DELETE_SQL = f"DELETE FROM device_tokens WHERE id = $1 AND {_MOVED} RETURNING *"
DELETE_CONNECTION_SQL = "DELETE FROM device_tokens WHERE connection_id = $1 RETURNING *"

CLAIM_PUSH_SQL = f"""UPDATE device_tokens
SET last_push_at = now(), push_due = FALSE
WHERE id = $1 AND {_INTERVAL_ELAPSED}
RETURNING id"""
MARK_PUSH_DUE_SQL = "UPDATE device_tokens SET push_due = TRUE WHERE id = $1"
RELEASE_PUSH_SQL = "UPDATE device_tokens SET last_push_at = NULL, push_due = TRUE WHERE id = $1"
CLAIM_OWED_PUSHES_SQL = f"""UPDATE device_tokens
SET last_push_at = now(), push_due = FALSE
WHERE kind = $1 AND push_due AND {_MOVED} AND {_INTERVAL_ELAPSED}
RETURNING *"""

COUNT_SQL = f"""SELECT environment, kind, count(*) AS n FROM device_tokens
WHERE {_MOVED} GROUP BY 1, 2"""
STORED_TOKENS_SQL = "SELECT * FROM device_tokens WHERE token IS NOT NULL ORDER BY updated_at"


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _row(record: asyncpg.Record) -> DeviceStateRow:
    return DeviceStateRow(
        id=record["id"],
        owner_id=record["owner_id"],
        platform=record["platform"],
        app_bundle_id=record["app_bundle_id"],
        kind=record["kind"],
        environment=record["environment"],
        activity_id=record["activity_id"],
        connection_id=record["connection_id"],
        credential_name=record["credential_name"],
        token_sha256=record["token_sha256"],
        created_at=_aware(record["created_at"]),
        updated_at=_aware(record["updated_at"]),
        last_push_at=record["last_push_at"],
    )


def _moved(records: list[asyncpg.Record]) -> list[DeviceStateRow]:
    """Removed rows that name a credential (a row still holding its token names none)."""
    return [_row(record) for record in records if record["credential_name"] is not None]


class PostgresDeviceStateStore(DeviceStateStore):
    """``device_tokens`` over asyncpg."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get(self, credential_name: str) -> DeviceStateRow | None:
        record = await self._pool.fetchrow(SELECT_BY_CREDENTIAL_SQL, credential_name)
        return _row(record) if record is not None else None

    async def get_by_id(self, row_id: UUID) -> DeviceStateRow | None:
        record = await self._pool.fetchrow(SELECT_BY_ID_SQL, row_id)
        return _row(record) if record is not None else None

    async def owner_rows(self, owner_id: str) -> list[DeviceStateRow]:
        return [_row(r) for r in await self._pool.fetch(SELECT_OWNER_ROWS_SQL, owner_id)]

    async def owner_rows_with_token(self, owner_id: str, token_sha256: str) -> list[DeviceStateRow]:
        records = await self._pool.fetch(SELECT_OWNER_TOKEN_SQL, owner_id, token_sha256)
        return [_row(record) for record in records]

    async def holder(
        self, token_sha256: str, kind: str, credential_name: str
    ) -> DeviceStateRow | None:
        record = await self._pool.fetchrow(HOLDER_SQL, token_sha256, kind, credential_name)
        if record is None or record["credential_name"] is None:
            return None
        return _row(record)

    async def register(self, row: DeviceStateRow) -> tuple[DeviceStateRow, list[DeviceStateRow]]:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                removed = _moved(
                    await conn.fetch(TAKE_OVER_SQL, row.token_sha256, row.kind, row.credential_name)
                )
                if row.kind == "live_activity" and row.activity_id:
                    removed += _moved(
                        await conn.fetch(
                            REPLACE_ACTIVITY_TOKEN_SQL,
                            row.connection_id,
                            row.kind,
                            row.activity_id,
                            row.credential_name,
                        )
                    )
                stored = await conn.fetchrow(
                    UPSERT_SQL,
                    row.id,
                    row.owner_id,
                    row.platform,
                    row.app_bundle_id,
                    row.kind,
                    row.environment,
                    row.activity_id,
                    row.connection_id,
                    row.credential_name,
                    row.token_sha256,
                    row.created_at,
                    row.updated_at,
                )
        return _row(stored), removed

    async def set_token_sha(self, row_id: UUID, token_sha256: str) -> None:
        await self._pool.execute(UPDATE_SHA_SQL, row_id, token_sha256)

    async def delete(self, row_id: UUID) -> list[DeviceStateRow]:
        return _moved(await self._pool.fetch(DELETE_SQL, row_id))

    async def delete_connection(self, connection_id: str) -> list[DeviceStateRow]:
        return _moved(await self._pool.fetch(DELETE_CONNECTION_SQL, connection_id))

    async def claim_push(self, row_id: UUID, *, min_interval_seconds: float) -> bool:
        claimed = await self._pool.fetchval(CLAIM_PUSH_SQL, row_id, float(min_interval_seconds))
        if claimed is not None:
            return True
        await self._pool.execute(MARK_PUSH_DUE_SQL, row_id)
        return False

    async def release_push(self, row_id: UUID) -> None:
        await self._pool.execute(RELEASE_PUSH_SQL, row_id)

    async def claim_owed_pushes(
        self, kind: str, *, min_interval_seconds: float
    ) -> list[DeviceStateRow]:
        records = await self._pool.fetch(CLAIM_OWED_PUSHES_SQL, kind, float(min_interval_seconds))
        return [_row(record) for record in records]

    async def count_by_environment(self) -> dict[str, dict[str, int]]:
        counts: dict[str, dict[str, int]] = {}
        for record in await self._pool.fetch(COUNT_SQL):
            counts.setdefault(record["environment"], {})[record["kind"]] = record["n"]
        return counts

    async def stored_tokens(self) -> list[StoredToken]:
        return [
            StoredToken(
                id=record["id"],
                owner_id=record["owner_id"],
                platform=record["platform"],
                app_bundle_id=record["app_bundle_id"],
                kind=record["kind"],
                environment=record["environment"],
                token=record["token"],
                created_at=_aware(record["created_at"]),
            )
            for record in await self._pool.fetch(STORED_TOKENS_SQL)
        ]
