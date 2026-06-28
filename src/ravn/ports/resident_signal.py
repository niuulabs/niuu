"""Resident signal source port."""

from __future__ import annotations

from typing import Protocol

from ravn.resident_inbox.models import ResidentInboxSignal


class ResidentSignalSourcePort(Protocol):
    """Loads canonical resident signal envelopes from one concrete source."""

    async def load_signal(self, ref_or_id: str) -> ResidentInboxSignal: ...


class ResidentSignalCandidateSourcePort(Protocol):
    """Lists normalized resident signals for attention consideration."""

    async def list_candidates(
        self,
        *,
        limit: int,
        status: str = "",
        classification: str = "",
    ) -> list[tuple[str, ResidentInboxSignal]]: ...
