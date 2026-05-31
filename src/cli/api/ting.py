"""Ting REST API methods for the CLI client."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from cli.api.client import APIClient

logger = logging.getLogger(__name__)

V1 = "/api/v1/ting"


@dataclass(frozen=True)
class SagaInfo:
    """Lightweight saga representation for the CLI."""

    id: str
    name: str
    status: str
    description: str = ""


@dataclass(frozen=True)
class RunInfo:
    """Lightweight run representation for the CLI."""

    id: str
    saga_id: str
    status: str
    session_ids: list[str]


@dataclass(frozen=True)
class DispatchResult:
    """Result of dispatching a saga."""

    run_id: str
    session_ids: list[str]


class TingAPI:
    """Ting orchestration endpoint methods."""

    def __init__(self, client: APIClient) -> None:
        self._client = client

    async def list_sagas(self) -> list[SagaInfo]:
        resp = await self._client.get(f"{V1}/sagas")
        resp.raise_for_status()
        return [
            SagaInfo(
                id=s["id"],
                name=s["name"],
                status=s["status"],
                description=s.get("description", ""),
            )
            for s in resp.json()
        ]

    async def get_saga(self, saga_id: str) -> SagaInfo | None:
        resp = await self._client.get(f"{V1}/sagas/{saga_id}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        return SagaInfo(
            id=data["id"],
            name=data["name"],
            status=data["status"],
            description=data.get("description", ""),
        )

    async def create_saga(self, name: str, description: str = "") -> SagaInfo:
        resp = await self._client.post(
            f"{V1}/sagas",
            json={"name": name, "description": description},
        )
        resp.raise_for_status()
        data = resp.json()
        return SagaInfo(
            id=data["id"],
            name=data["name"],
            status=data["status"],
            description=data.get("description", ""),
        )

    async def list_runs(self, saga_id: str) -> list[RunInfo]:
        resp = await self._client.get(f"{V1}/sagas/{saga_id}/runs")
        resp.raise_for_status()
        return [
            RunInfo(
                id=r["id"],
                saga_id=r.get("saga_id", saga_id),
                status=r["status"],
                session_ids=r.get("session_ids", []),
            )
            for r in resp.json()
        ]

    async def dispatch(
        self,
        saga_id: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> DispatchResult:
        resp = await self._client.post(
            f"{V1}/sagas/{saga_id}/dispatch",
            json=params or {},
        )
        resp.raise_for_status()
        data = resp.json()
        return DispatchResult(
            run_id=data["run_id"],
            session_ids=data.get("session_ids", []),
        )
