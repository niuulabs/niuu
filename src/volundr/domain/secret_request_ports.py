"""Ports for secure secret requests: the durable records, and the session-end hook."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from volundr.domain.secret_requests import (
    SecretKeep,
    SecretRequest,
    SecretRequestStatus,
)


class SecretRequestRepository(ABC):
    """Durable request records. Every transition is one atomic, conditional write."""

    @abstractmethod
    async def create_or_get_open(self, request: SecretRequest) -> tuple[SecretRequest, bool]:
        """Record ``request`` unless the session already has an open (pending or
        fulfilled) request for the same name; then return that one.

        Returns ``(request, created)``. Two concurrent creates for one name yield one
        row: the loser gets the winner's with ``created`` false.
        """

    @abstractmethod
    async def get(self, request_id: UUID) -> SecretRequest | None:
        """One request by id."""

    @abstractmethod
    async def list_for_session(
        self, session_id: UUID, status: SecretRequestStatus | None = None
    ) -> list[SecretRequest]:
        """The session's requests, newest first, optionally of one status."""

    @abstractmethod
    async def mark_fulfilled(
        self,
        request_id: UUID,
        *,
        keep: SecretKeep,
        credential_name: str,
        at: datetime,
    ) -> SecretRequest | None:
        """pending -> fulfilled. ``None`` when the request is not pending."""

    @abstractmethod
    async def mark_declined(self, request_id: UUID, *, at: datetime) -> SecretRequest | None:
        """pending -> declined. ``None`` when the request is not pending."""

    @abstractmethod
    async def mark_delivered(self, request_id: UUID, *, at: datetime) -> SecretRequest | None:
        """fulfilled -> delivered. ``None`` when the request is not fulfilled."""

    @abstractmethod
    async def mark_cancelled(self, request_id: UUID, *, at: datetime) -> SecretRequest | None:
        """pending or fulfilled -> cancelled. ``None`` when the request is already closed."""

    @abstractmethod
    async def cancel_open_for_session(
        self, session_id: UUID, *, at: datetime
    ) -> list[SecretRequest]:
        """Cancel every open request of the session; returns the rows cancelled."""

    @abstractmethod
    async def delete_for_session(self, session_id: UUID) -> int:
        """Drop every record of the session; returns how many."""


class SessionSecretCleanup(ABC):
    """What a session's end does to its secret requests (the session service calls this)."""

    @abstractmethod
    async def cancel_for_session(self, session_id: UUID) -> int:
        """Cancel the session's open requests and delete their session-only values.

        Returns how many requests were cancelled. Raises when a stored value cannot be
        deleted, so a lingering secret is never silent.
        """

    @abstractmethod
    async def purge_session(self, session_id: UUID) -> None:
        """As ``cancel_for_session``, then drop the session's records (the session is gone)."""
