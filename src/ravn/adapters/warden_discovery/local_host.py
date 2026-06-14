"""Discover locally installed wardens from host-side WardenSpec state."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

from ravn.warden.models import (
    WardenObservation,
    WardenObservedField,
    WardenRuntime,
    WardenSpec,
)
from ravn.warden.store import WardenStore


class LocalHostWardenDiscoveryAdapter:
    """Read local WardenSpec state and annotate it with host context."""

    def __init__(
        self,
        root: str = "",
        store: WardenStore | None = None,
        host_name: str = "",
    ) -> None:
        self._store = store or WardenStore(Path(root).expanduser() if root else None)
        self._host_name = host_name or "local"

    async def list_wardens(self) -> list[WardenSpec]:
        """Return locally persisted wardens with a local-host observation."""
        return [self._with_local_observation(warden) for warden in self._store.list()]

    def _with_local_observation(self, warden: WardenSpec) -> WardenSpec:
        status = "running" if warden.runtime.state == "active" else "idle"
        if not warden.supervisor.installed:
            status = "missing"

        return warden.model_copy(
            update={
                "deployment": warden.deployment or "local-host",
                "deployment_kwargs": {
                    **warden.deployment_kwargs,
                    "discovery_source": "local-host",
                    "host": self._host_name,
                },
                "runtime": warden.runtime.model_copy(
                    update={
                        "state": warden.runtime.state
                        if warden.supervisor.installed
                        else "offline",
                    }
                )
                if isinstance(warden.runtime, WardenRuntime)
                else warden.runtime,
                "supervisor": warden.supervisor.model_copy(
                    update={
                        "observation": WardenObservation(
                            status=status,
                            detail=f"Discovered from local WardenSpec store on {self._host_name}",
                            source="local-host",
                            checked_at=datetime.now(UTC),
                            fields=[
                                WardenObservedField(label="host", value=self._host_name),
                                WardenObservedField(label="platform", value=sys.platform),
                                WardenObservedField(
                                    label="installed",
                                    value=str(warden.supervisor.installed).lower(),
                                ),
                            ],
                        )
                    }
                ),
            }
        )
