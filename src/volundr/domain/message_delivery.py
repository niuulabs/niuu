"""Durable at-most-once claims for user-message dispatch.

A pending claim is deliberately never stolen after a restart: the native process
may have received the message before its acknowledgement was persisted. Keeping
that state uncertain is safer than executing the same user instruction twice.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

DeliveryStatus = Literal["pending", "delivered", "failed"]


class MessageDeliveryConflictError(ValueError):
    """An identity was reused for different content or by a different claimant."""


@dataclass(frozen=True)
class MessageDelivery:
    request_id: str
    status: DeliveryStatus
    claimed: bool = False
    error: str | None = None


class MessageDeliveryRepository(ABC):
    @abstractmethod
    async def claim(
        self, session_id: UUID, request_id: str, payload_hash: str, claim_token: UUID
    ) -> MessageDelivery: ...

    @abstractmethod
    async def settle(
        self,
        session_id: UUID,
        request_id: str,
        claim_token: UUID,
        status: DeliveryStatus,
        error: str | None = None,
    ) -> MessageDelivery: ...

    @abstractmethod
    async def get(self, session_id: UUID, request_id: str) -> MessageDelivery | None: ...
