"""Discover wardens from persisted WardenSpec files."""

from __future__ import annotations

from pathlib import Path

from ravn.warden.models import WardenSpec
from ravn.warden.store import WardenStore


class WardenSpecDiscoveryAdapter:
    """Read WardenSpec files from a local WardenStore."""

    def __init__(
        self,
        root: str = "",
        store: WardenStore | None = None,
    ) -> None:
        self._store = store or WardenStore(Path(root).expanduser() if root else None)

    async def list_wardens(self) -> list[WardenSpec]:
        """Return persisted wardens."""
        return self._store.list()
