"""JSON-file adapter for setup progress (single-host deployments)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from niuu.domain.setup import SetupState
from niuu.ports.setup_state import SetupStateStore


class FileSetupStateStore(SetupStateStore):
    """Keeps the wizard's progress in one JSON file under the data directory."""

    def __init__(self, *, path: str) -> None:
        self._path = Path(str(path)).expanduser()

    @property
    def path(self) -> Path:
        return self._path

    async def load(self) -> SetupState:
        if not self._path.exists():
            return SetupState()
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"Setup state file {self._path} must contain a JSON object")
        return SetupState.from_dict(raw)

    async def save(self, state: SetupState) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
        os.replace(tmp, self._path)
