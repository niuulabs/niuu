"""Resident memory directory read from the Valkyrie API (``/api/v1/ravn/valkyrie/memory``)."""

from __future__ import annotations

import asyncio
import importlib
from typing import Any

import httpx

from mimir.ports.resident_memory import ResidentMemoryDirectoryPort, ServedResidentMemory


def _import_class(dotted_path: str) -> type:
    module_path, class_name = dotted_path.rsplit(".", 1)
    return getattr(importlib.import_module(module_path), class_name)


class ValkyrieMemoryDirectory(ResidentMemoryDirectoryPort):
    """Ask the Valkyrie API which resident stores are served, and where.

    Constructor kwargs (dynamic adapter pattern):
        base_url: the API host serving ``/api/v1/ravn/valkyrie``.
        auth_adapter: fully-qualified ``niuu.ports.http_auth.HttpAuthPort`` class.
        auth_kwargs: its constructor kwargs.
        timeout_seconds: HTTP timeout for the directory call.
    """

    def __init__(
        self,
        *,
        base_url: str,
        auth_adapter: str,
        auth_kwargs: dict[str, Any] | None = None,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not base_url.strip():
            raise ValueError("resident_memory.directory.base_url is required")
        if not auth_adapter.strip():
            raise ValueError(
                "resident_memory.directory.auth_adapter is required: the Valkyrie API "
                "admits only the platform org"
            )
        self._base_url = base_url.rstrip("/")
        self._auth = _import_class(auth_adapter)(**(auth_kwargs or {}))
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def served(self) -> list[ServedResidentMemory]:
        # HttpAuthPort is synchronous; a token refresh must not block the loop.
        headers = await asyncio.to_thread(self._auth.headers)
        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=self._timeout_seconds, transport=self._transport
        ) as client:
            response = await client.get("/api/v1/ravn/valkyrie/memory", headers=headers)
        response.raise_for_status()
        return [
            ServedResidentMemory(
                valkyrie_id=str(entry["valkyrieId"]),
                name=str(entry["name"]),
                role=str(entry["role"]),
                url=str(entry["url"]),
            )
            for entry in response.json()
        ]
