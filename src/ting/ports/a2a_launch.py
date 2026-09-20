"""Persistence port for receiver-side A2A launch intents."""

from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from ting.domain.a2a_launch import A2ALaunchReservation


class A2ALaunchReservationRepository(ABC):
    @abstractmethod
    async def reserve(
        self, reservation: A2ALaunchReservation
    ) -> tuple[A2ALaunchReservation, bool]: ...

    @abstractmethod
    async def claim(
        self, reservation_id: UUID, *, lease_token: UUID, lease_until: datetime
    ) -> A2ALaunchReservation | None: ...

    @abstractmethod
    async def mark_launched(
        self, reservation_id: UUID, *, lease_token: UUID, session_id: str
    ) -> A2ALaunchReservation: ...
