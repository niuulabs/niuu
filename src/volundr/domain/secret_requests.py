"""Secure secret requests: an agent asks its session's owner for one secret.

The agent never receives the value in chat. It opens a request naming the secret
(``GITLAB_TOKEN``) and why it needs it; the owner types the value into a masked card
that posts straight to Forge; the agent then claims the value once, through its own
session credential, and keeps only a file path.

A request moves through five states::

    pending -> fulfilled -> delivered
       |           |
       +-> declined, cancelled  (the agent, the owner, or the session ending)

The value itself is never part of this record. It lives in the credential store
(OpenBao on a cluster) under the session owner, and is read back only by the claim.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from uuid import UUID

#: A secret's name becomes a file name in the session (``secrets/<NAME>``), so it is
#: bounded by the longest file name a filesystem allows (``NAME_MAX``).
SECRET_NAME_MAX_CHARS = 255
SECRET_NAME_PATTERN = r"^[A-Z_][A-Z0-9_]*$"
_SECRET_NAME = re.compile(SECRET_NAME_PATTERN)

#: Credential name of a value kept only for this request (``keep == session``).
SESSION_CREDENTIAL_PREFIX = "session-secret-"
#: What a stored value is tagged with, so an operator can tell where it came from.
CREDENTIAL_SOURCE = "secret_request"
#: The one key the value is stored under, in the credential's data.
CREDENTIAL_VALUE_KEY = "value"


class SecretRequestStatus(StrEnum):
    PENDING = "pending"
    FULFILLED = "fulfilled"
    DELIVERED = "delivered"
    DECLINED = "declined"
    CANCELLED = "cancelled"


#: States in which the request still stands: it may yet be supplied, claimed or cancelled.
OPEN_STATUSES: tuple[SecretRequestStatus, ...] = (
    SecretRequestStatus.PENDING,
    SecretRequestStatus.FULFILLED,
)


class SecretKeep(StrEnum):
    """How long the owner lets Forge keep the value."""

    #: Only until the agent claims it (or the request is cancelled).
    SESSION = "session"
    #: Saved to the owner's credentials under the secret's own name, overwriting.
    SAVED = "saved"


class SecretRequestError(Exception):
    """Base of the errors a secret request raises. Messages never carry a value."""


class SecretRequestValidationError(SecretRequestError):
    """The request is malformed (for example a name that is not a legal secret name)."""


class SecretRequestNotFoundError(SecretRequestError):
    def __init__(self, request_id: UUID) -> None:
        super().__init__(f"Secret request not found: {request_id}")
        self.request_id = request_id


class SecretRequestStateError(SecretRequestError):
    """The request is not in a state this action applies to."""

    def __init__(self, request_id: UUID, action: str, status: SecretRequestStatus) -> None:
        super().__init__(f"Cannot {action} secret request {request_id}: it is {status.value}")
        self.request_id = request_id
        self.action = action
        self.status = status


class SecretValueMissingError(SecretRequestError):
    """A fulfilled request's value is not in the credential store."""

    def __init__(self, request_id: UUID) -> None:
        super().__init__(
            f"The value of secret request {request_id} is no longer in the credential "
            "store; cancel the request and ask again"
        )
        self.request_id = request_id


def validate_secret_name(name: str) -> str:
    """The name, or ``SecretRequestValidationError`` when it is not a legal secret name."""
    if len(name) > SECRET_NAME_MAX_CHARS or not _SECRET_NAME.fullmatch(name):
        raise SecretRequestValidationError(
            f"Secret name must match {SECRET_NAME_PATTERN} and be at most "
            f"{SECRET_NAME_MAX_CHARS} characters"
        )
    return name


def saved_credential_name(name: str) -> str:
    """The owner credential a saved secret is stored as: ``GITLAB_TOKEN`` -> ``gitlab-token``."""
    return validate_secret_name(name).lower().replace("_", "-")


def session_credential_name(request_id: UUID) -> str:
    return f"{SESSION_CREDENTIAL_PREFIX}{request_id}"


@dataclass(frozen=True)
class SecretRequest:
    """One request for one secret. Holds no value."""

    id: UUID
    session_id: UUID
    owner_id: str
    name: str
    purpose: str
    target: str | None
    status: SecretRequestStatus
    created_at: datetime
    keep: SecretKeep | None = None
    #: The credential the value was stored under (``keep`` decides which naming).
    credential_name: str | None = None
    resolved_at: datetime | None = None
    delivered_at: datetime | None = None

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    @property
    def stores_session_only(self) -> bool:
        """The value, if stored, is meant to vanish with the request."""
        return self.keep is SecretKeep.SESSION

    def with_status(self, status: SecretRequestStatus) -> SecretRequest:
        return replace(self, status=status)
