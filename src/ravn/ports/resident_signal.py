"""Resident signal source port."""

from __future__ import annotations

from typing import Protocol

from ravn.resident_inbox.models import ResidentInboxSignal


class ResidentSignalSourcePort(Protocol):
    """Loads canonical resident signal envelopes from one concrete source."""

    async def load_signal(self, ref_or_id: str) -> ResidentInboxSignal: ...
