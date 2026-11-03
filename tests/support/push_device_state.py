"""An in-memory ``device_tokens``: the push gateway's per-token state, without a token."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from volundr.adapters.outbound.push_device_state import (
    DeviceStateRow,
    DeviceStateStore,
    StoredToken,
)


class InMemoryDeviceStateStore(DeviceStateStore):
    """Rows keyed by credential name, with the same take-over and replace rules."""

    def __init__(self) -> None:
        self.rows: dict[str, DeviceStateRow] = {}
        self.owed: set[UUID] = set()
        #: Rows written before tokens left Postgres.
        self.stored: list[StoredToken] = []

    def _by_id(self, row_id: UUID) -> DeviceStateRow | None:
        return next((row for row in self.rows.values() if row.id == row_id), None)

    async def get(self, credential_name: str) -> DeviceStateRow | None:
        return self.rows.get(credential_name)

    async def get_by_id(self, row_id: UUID) -> DeviceStateRow | None:
        return self._by_id(row_id)

    async def owner_rows(self, owner_id: str) -> list[DeviceStateRow]:
        return [row for row in self.rows.values() if row.owner_id == owner_id]

    async def owner_rows_with_token(self, owner_id: str, token_sha256: str) -> list[DeviceStateRow]:
        return [
            row
            for row in self.rows.values()
            if row.owner_id == owner_id and row.token_sha256 == token_sha256
        ]

    async def holder(
        self, token_sha256: str, kind: str, credential_name: str
    ) -> DeviceStateRow | None:
        return next(
            (
                row
                for row in self.rows.values()
                if row.token_sha256 == token_sha256
                and row.kind == kind
                and row.credential_name != credential_name
            ),
            None,
        )

    async def register(self, row: DeviceStateRow) -> tuple[DeviceStateRow, list[DeviceStateRow]]:
        removed = [
            other
            for other in self.rows.values()
            if other.token_sha256 == row.token_sha256
            and other.kind == row.kind
            and other.credential_name != row.credential_name
        ]
        if row.kind == "live_activity" and row.activity_id:
            removed += [
                other
                for other in self.rows.values()
                if other.connection_id == row.connection_id
                and other.kind == row.kind
                and other.activity_id == row.activity_id
                and other.credential_name != row.credential_name
                and other not in removed
            ]
        for other in removed:
            del self.rows[other.credential_name]
        # Unmoved rows of the same token are taken over too.
        self.stored = [
            s for s in self.stored if not (s.kind == row.kind and _sha(s.token) == row.token_sha256)
        ]
        existing = self.rows.get(row.credential_name)
        stored = (
            replace(
                row,
                id=existing.id,
                created_at=existing.created_at,
                last_push_at=existing.last_push_at,
            )
            if existing is not None
            else row
        )
        self.rows[row.credential_name] = stored
        return stored, removed

    async def set_token_sha(self, row_id: UUID, token_sha256: str) -> None:
        row = self._by_id(row_id)
        if row is not None:
            self.rows[row.credential_name] = replace(row, token_sha256=token_sha256)

    async def delete(self, row_id: UUID) -> list[DeviceStateRow]:
        row = self._by_id(row_id)
        if row is None:
            return []
        del self.rows[row.credential_name]
        return [row]

    async def delete_connection(self, connection_id: str) -> list[DeviceStateRow]:
        removed = [row for row in self.rows.values() if row.connection_id == connection_id]
        for row in removed:
            del self.rows[row.credential_name]
        return removed

    async def claim_push(self, row_id: UUID, *, min_interval_seconds: float) -> bool:
        row = self._by_id(row_id)
        if row is None:
            return False
        now = datetime.now(UTC)
        if row.last_push_at is None or row.last_push_at <= now - timedelta(
            seconds=min_interval_seconds
        ):
            self.rows[row.credential_name] = replace(row, last_push_at=now)
            self.owed.discard(row_id)
            return True
        self.owed.add(row_id)
        return False

    async def release_push(self, row_id: UUID) -> None:
        row = self._by_id(row_id)
        if row is not None:
            self.rows[row.credential_name] = replace(row, last_push_at=None)
            self.owed.add(row_id)

    async def claim_owed_pushes(
        self, kind: str, *, min_interval_seconds: float
    ) -> list[DeviceStateRow]:
        claimed = [row for row in self.rows.values() if row.kind == kind and row.id in self.owed]
        for row in claimed:
            self.owed.discard(row.id)
        return claimed

    async def count_by_environment(self) -> dict[str, dict[str, int]]:
        counts: dict[str, dict[str, int]] = {}
        for row in self.rows.values():
            kinds = counts.setdefault(row.environment, {})
            kinds[row.kind] = kinds.get(row.kind, 0) + 1
        return counts

    async def stored_tokens(self) -> list[StoredToken]:
        return list(self.stored)


def _sha(token: str) -> str:
    from volundr.adapters.outbound.integration_device_tokens import token_sha256

    return token_sha256(token)
