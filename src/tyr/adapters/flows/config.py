"""YAML-file-backed flock flow provider.

Loads flows from a YAML file at startup and keeps in-memory runtime
additions. Mirrors the pattern of ConfigProfileProvider.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from tyr.domain.flock_flow import FlockFlowConfig
from tyr.ports.flock_flow import FlockFlowProvider

logger = logging.getLogger(__name__)


class ConfigFlockFlowProvider(FlockFlowProvider):
    """Loads flows from a local YAML file plus runtime additions."""

    def __init__(self, path: str = "") -> None:
        self._flows: dict[str, FlockFlowConfig] = {}
        if path:
            self._load_from_file(Path(path))

    def _load_from_file(self, path: Path) -> None:
        if not path.exists():
            logger.warning("Flock flow config file not found: %s", path)
            return
        with open(path) as f:
            data = yaml.safe_load(f)
        if not isinstance(data, list):
            logger.warning("Flock flow config must be a YAML list, got %s", type(data).__name__)
            return
        for entry in data:
            try:
                flow = FlockFlowConfig.from_dict(entry)
                self._flows[flow.name] = flow
            except (KeyError, TypeError):
                logger.warning("Skipping invalid flow entry: %s", entry)
        logger.info("Loaded %d flock flow(s) from %s", len(self._flows), path)

    def get(self, name: str) -> FlockFlowConfig | None:
        return self._flows.get(name)

    def list(self) -> list[FlockFlowConfig]:
        return list(self._flows.values())

    def save(self, flow: FlockFlowConfig) -> None:
        self._flows[flow.name] = flow

    def delete(self, name: str) -> bool:
        return self._flows.pop(name, None) is not None
