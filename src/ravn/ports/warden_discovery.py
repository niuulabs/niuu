"""Port for discovering long-lived Ravn wardens."""

from __future__ import annotations

from typing import Protocol

from ravn.warden.models import WardenSpec


class WardenDiscoveryPort(Protocol):
    """Read-only discovery surface for WardenSpec providers."""

    async def list_wardens(self) -> list[WardenSpec]:
        """Return known wardens normalized as WardenSpec objects."""
        ...
