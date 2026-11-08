"""In-memory secret request repository for tests (same contract as the Postgres adapter)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from uuid import UUID

from volundr.domain.secret_request_ports import SecretRequestRepository
from volundr.domain.secret_requests import (
    OPEN_STATUSES,
    SecretKeep,
    SecretRequest,
    SecretRequestError,
    SecretRequestStatus,
)


class InMemorySecretRequestRepository(SecretRequestRepository):
    def __init__(self) -> None:
        self.rows: dict[UUID, SecretRequest] = {}

    async def create_or_get_open(self, request: SecretRequest) -> tuple[SecretRequest, bool]:
        for row in self.rows.values():
            if row.session_id == request.session_id and row.name == request.name and row.is_open:
                return row, False
        self.rows[request.id] = request
        return request, True

    async def get(self, request_id: UUID) -> SecretRequest | None:
        return self.rows.get(request_id)

    async def list_for_session(
        self, session_id: UUID, status: SecretRequestStatus | None = None
    ) -> list[SecretRequest]:
        rows = [r for r in self.rows.values() if r.session_id == session_id]
        if status is not None:
            rows = [r for r in rows if r.status is status]
        return sorted(rows, key=lambda r: r.created_at, reverse=True)

    def _transition(
        self, request_id: UUID, allowed: tuple[SecretRequestStatus, ...], **changes
    ) -> SecretRequest | None:
        row = self.rows.get(request_id)
        if row is None or row.status not in allowed:
            return None
        updated = replace(row, **changes)
        self.rows[request_id] = updated
        return updated

    async def mark_fulfilled(
        self, request_id: UUID, *, keep: SecretKeep, credential_name: str, at: datetime
    ) -> SecretRequest | None:
        return self._transition(
            request_id,
            (SecretRequestStatus.PENDING,),
            status=SecretRequestStatus.FULFILLED,
            keep=keep,
            credential_name=credential_name,
            resolved_at=at,
        )

    async def mark_declined(self, request_id: UUID, *, at: datetime) -> SecretRequest | None:
        return self._transition(
            request_id,
            (SecretRequestStatus.PENDING,),
            status=SecretRequestStatus.DECLINED,
            resolved_at=at,
        )

    async def mark_delivered(self, request_id: UUID, *, at: datetime) -> SecretRequest | None:
        return self._transition(
            request_id,
            (SecretRequestStatus.FULFILLED,),
            status=SecretRequestStatus.DELIVERED,
            delivered_at=at,
        )

    async def mark_cancelled(self, request_id: UUID, *, at: datetime) -> SecretRequest | None:
        return self._transition(
            request_id, OPEN_STATUSES, status=SecretRequestStatus.CANCELLED, resolved_at=at
        )

    async def cancel_open_for_session(
        self, session_id: UUID, *, at: datetime
    ) -> list[SecretRequest]:
        cancelled = []
        for row in list(self.rows.values()):
            if row.session_id != session_id or not row.is_open:
                continue
            updated = self._transition(
                row.id, OPEN_STATUSES, status=SecretRequestStatus.CANCELLED, resolved_at=at
            )
            if updated is None:
                raise SecretRequestError("unreachable: row was open")
            cancelled.append(updated)
        return cancelled

    async def delete_for_session(self, session_id: UUID) -> int:
        doomed = [rid for rid, row in self.rows.items() if row.session_id == session_id]
        for rid in doomed:
            del self.rows[rid]
        return len(doomed)
