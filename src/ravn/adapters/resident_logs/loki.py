"""Read resident logs from Loki, where every cluster's pod logs are collected.

A resident's placement names its cluster, namespace and Deployment; the shared
reader (:mod:`niuu.adapters.loki_pod_logs`) turns that into a query against the
cluster's Loki tenant.
"""

from __future__ import annotations

from typing import Any

import httpx

from niuu.adapters.loki_pod_logs import LokiPodLogError, LokiPodLogReader
from ravn.ports.resident_discovery import ResidentPlacement
from ravn.ports.resident_logs import ResidentLogSourceError


class LokiResidentLogAdapter:
    """Implements :class:`~ravn.ports.resident_logs.ResidentLogPort` over Loki."""

    def __init__(
        self,
        base_url: str,
        query_path: str = "/{cluster}/loki/api/v1/query_range",
        lookback_seconds: int = 86400,
        timeout_seconds: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._reader = LokiPodLogReader(
            base_url=base_url,
            query_path=query_path,
            lookback_seconds=lookback_seconds,
            timeout_seconds=timeout_seconds,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._reader.aclose()

    async def read_logs(
        self,
        placement: ResidentPlacement,
        *,
        lines: int,
        sources: tuple[str, ...],
        min_level: str,
    ) -> dict[str, Any]:
        try:
            entries = await self._reader.read(
                cluster=placement.cluster,
                namespace=placement.namespace,
                workload=placement.workload,
                lines=lines,
                sources=sources,
                min_level=min_level,
            )
        except LokiPodLogError as exc:
            raise ResidentLogSourceError(str(exc)) from exc
        return {"entries": entries, "bufferTotal": len(entries)}
