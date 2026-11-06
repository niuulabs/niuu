"""Secure secret requests: create, supply, claim and clean up.

The value is held by exactly one place, the credential store (OpenBao on a cluster),
under the session owner. It enters Forge only through :meth:`SecretRequestService.fulfill`
and leaves only through :meth:`SecretRequestService.claim`. It is never logged, never in
an exception message, and never part of a request record or a response other than the
claim's.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

from niuu.domain.models import SecretType
from niuu.ports.credentials import CredentialStorePort
from volundr.domain.models import Session
from volundr.domain.secret_request_ports import SecretRequestRepository, SessionSecretCleanup
from volundr.domain.secret_requests import (
    CREDENTIAL_SOURCE,
    CREDENTIAL_VALUE_KEY,
    SecretKeep,
    SecretRequest,
    SecretRequestNotFoundError,
    SecretRequestStateError,
    SecretRequestStatus,
    SecretRequestValidationError,
    SecretValueMissingError,
    saved_credential_name,
    session_credential_name,
    validate_secret_name,
)

logger = logging.getLogger(__name__)

#: Credentials a secret request stores belong to the session's owner.
CREDENTIAL_OWNER_TYPE = "user"


class SecretRequestService(SessionSecretCleanup):
    """Owns the life of a session's secret requests and of the values they hold."""

    def __init__(self, repository: SecretRequestRepository, credentials: CredentialStorePort):
        self._repository = repository
        self._credentials = credentials

    async def create(
        self, session: Session, *, name: str, purpose: str, target: str | None
    ) -> tuple[SecretRequest, bool]:
        """Open a request, or return the session's open one for ``name``.

        Returns ``(request, created)``; ``created`` is false when an open request for
        this name already stood (a repeat ask keeps waiting on the same request).
        """
        validate_secret_name(name)
        if not purpose.strip():
            raise SecretRequestValidationError("A secret request needs a purpose")
        request = SecretRequest(
            id=uuid4(),
            session_id=session.id,
            owner_id=session.owner_id,
            name=name,
            purpose=purpose,
            target=target or None,
            status=SecretRequestStatus.PENDING,
            created_at=datetime.now(UTC),
        )
        stored, created = await self._repository.create_or_get_open(request)
        logger.info(
            "Secret request %s for session %s %s (%s)",
            stored.id,
            session.id,
            "created" if created else "already open",
            stored.name,
        )
        return stored, created

    async def get(self, session_id: UUID, request_id: UUID) -> SecretRequest:
        request = await self._repository.get(request_id)
        if request is None or request.session_id != session_id:
            raise SecretRequestNotFoundError(request_id)
        return request

    async def list(
        self, session_id: UUID, status: SecretRequestStatus | None = None
    ) -> list[SecretRequest]:
        return await self._repository.list_for_session(session_id, status)

    async def fulfill(
        self, session_id: UUID, request_id: UUID, *, value: str, keep: SecretKeep
    ) -> SecretRequest:
        """Store the owner's value and mark the request fulfilled."""
        if not value:
            raise SecretRequestValidationError("A secret value must not be empty")
        request = await self.get(session_id, request_id)
        if request.status is not SecretRequestStatus.PENDING:
            raise SecretRequestStateError(request.id, "fulfill", request.status)

        credential_name = (
            saved_credential_name(request.name)
            if keep is SecretKeep.SAVED
            else session_credential_name(request.id)
        )
        await self._credentials.store(
            CREDENTIAL_OWNER_TYPE,
            request.owner_id,
            credential_name,
            SecretType.GENERIC,
            {CREDENTIAL_VALUE_KEY: value},
            {
                "source": CREDENTIAL_SOURCE,
                "session_id": str(request.session_id),
                "request_id": str(request.id),
            },
        )
        fulfilled = await self._repository.mark_fulfilled(
            request.id, keep=keep, credential_name=credential_name, at=datetime.now(UTC)
        )
        if fulfilled is not None:
            logger.info("Secret request %s fulfilled (keep=%s)", request.id, keep.value)
            return fulfilled

        # The request closed while the value was being stored (cancelled, or another
        # fulfil won). A value stored for a request that no longer wants it must not stay.
        current = await self.get(session_id, request.id)
        if current.status is not SecretRequestStatus.FULFILLED:
            await self._delete_session_value(current)
        raise SecretRequestStateError(request.id, "fulfill", current.status)

    async def decline(self, session_id: UUID, request_id: UUID) -> SecretRequest:
        request = await self.get(session_id, request_id)
        declined = await self._repository.mark_declined(request.id, at=datetime.now(UTC))
        if declined is not None:
            logger.info("Secret request %s declined", request.id)
            return declined
        current = await self.get(session_id, request.id)
        raise SecretRequestStateError(request.id, "decline", current.status)

    async def claim(self, session_id: UUID, request_id: UUID) -> tuple[SecretRequest, str]:
        """Hand the value over once: returns the delivered request and the value.

        A session-only value is deleted from the credential store as it is read. Only
        one of several concurrent claims succeeds; the others get a state error.
        """
        request = await self.get(session_id, request_id)
        if request.status is not SecretRequestStatus.FULFILLED or request.credential_name is None:
            raise SecretRequestStateError(request.id, "claim", request.status)

        stored = await self._credentials.get_value(
            CREDENTIAL_OWNER_TYPE, request.owner_id, request.credential_name
        )
        value = None if stored is None else stored.get(CREDENTIAL_VALUE_KEY)
        if not value:
            raise SecretValueMissingError(request.id)

        # Delete before marking delivered: a value that could not be removed is an
        # error the caller sees, with the request still fulfilled, not a lingering secret.
        await self._delete_session_value(request)
        delivered = await self._repository.mark_delivered(request.id, at=datetime.now(UTC))
        if delivered is None:
            current = await self.get(session_id, request.id)
            raise SecretRequestStateError(request.id, "claim", current.status)
        logger.info("Secret request %s delivered", request.id)
        return delivered, value

    async def cancel(self, session_id: UUID, request_id: UUID) -> SecretRequest:
        """Cancel an open request and delete its session-only value."""
        request = await self.get(session_id, request_id)
        cancelled = await self._repository.mark_cancelled(request.id, at=datetime.now(UTC))
        if cancelled is None:
            current = await self.get(session_id, request.id)
            raise SecretRequestStateError(request.id, "cancel", current.status)
        await self._delete_session_value(cancelled)
        logger.info("Secret request %s cancelled", request.id)
        return cancelled

    async def cancel_for_session(self, session_id: UUID) -> int:
        cancelled = await self._repository.cancel_open_for_session(session_id, at=datetime.now(UTC))
        for request in cancelled:
            await self._delete_session_value(request)
        if cancelled:
            logger.info(
                "Cancelled %d open secret requests of session %s",
                len(cancelled),
                cancelled[0].session_id,
            )
        return len(cancelled)

    async def purge_session(self, session_id: UUID) -> None:
        await self.cancel_for_session(session_id)
        await self._repository.delete_for_session(session_id)

    async def _delete_session_value(self, request: SecretRequest) -> None:
        """Delete the request's session-only value (a no-op when none was stored).

        The name is derived from the request id, so this is safe for a request whose
        ``keep`` was never recorded (a value stored just before the request closed). A
        saved value belongs to the owner and is never touched here.
        """
        if request.keep is SecretKeep.SAVED:
            return
        await self._credentials.delete(
            CREDENTIAL_OWNER_TYPE, request.owner_id, session_credential_name(request.id)
        )
