"""Port for reading the logs of residents the platform does not manage.

A managed resident's logs come from its control plane. A discovered or
catalog resident runs as a plain Kubernetes workload, possibly in a cluster
with no Niuu instance at all, so its logs are read by its placement from
wherever the cluster's logs are collected.
"""

from __future__ import annotations

from typing import Any, Protocol

from ravn.ports.resident_discovery import ResidentPlacement


class ResidentLogSourceError(RuntimeError):
    """The configured log source could not answer; the message names it and why."""


class ResidentLogPort(Protocol):
    """Read recent log entries of the workload at a placement."""

    async def read_logs(
        self,
        placement: ResidentPlacement,
        *,
        lines: int,
        sources: tuple[str, ...],
        min_level: str,
    ) -> dict[str, Any]:
        """Return ``{"entries": [...], "bufferTotal": n}``, oldest entry first.

        Each entry has the managed-resident log shape: ``timestampMs``,
        ``level``, ``source`` (the container), ``target``, ``message`` and
        ``fields``. A source names a container; none means all of them.
        """
        raise NotImplementedError

    async def aclose(self) -> None:
        """Release the adapter's connections."""
        raise NotImplementedError
