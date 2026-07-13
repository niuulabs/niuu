"""ToolPort proxies for tools owned by the running resident daemon."""

from __future__ import annotations

from typing import Any

import httpx

from ravn.domain.models import ToolResult
from ravn.ports.tool import ToolPort


class ResidentToolProxy(ToolPort):
    """Forward one tool invocation to the co-located resident daemon."""

    def __init__(
        self,
        *,
        base_url: str,
        definition: dict[str, Any],
        connect_timeout_s: float,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._definition = definition
        self._timeout = httpx.Timeout(None, connect=connect_timeout_s)

    @property
    def name(self) -> str:
        return str(self._definition["name"])

    @property
    def description(self) -> str:
        return str(self._definition.get("description", ""))

    @property
    def input_schema(self) -> dict:
        schema = self._definition.get("input_schema", {})
        return schema if isinstance(schema, dict) else {}

    @property
    def required_permission(self) -> str:
        return str(self._definition.get("required_permission", ""))

    @property
    def parallelisable(self) -> bool:
        return bool(self._definition.get("parallelisable", True))

    async def execute(self, input: dict) -> ToolResult:  # noqa: A002
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._base_url}/internal/tools/{self.name}",
                    json={"input": input},
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            return ToolResult(
                tool_call_id="",
                content=f"Resident tool {self.name!r} failed: {exc}",
                is_error=True,
            )

        payload = response.json()
        return ToolResult(
            tool_call_id=str(payload.get("tool_call_id", "")),
            content=str(payload.get("content", "")),
            is_error=bool(payload.get("is_error", False)),
        )


async def load_resident_tools(
    *,
    base_url: str,
    connect_timeout_s: float,
) -> list[ToolPort]:
    """Load resident-owned tool definitions and return executable proxies."""
    normalized_url = base_url.rstrip("/")
    timeout = httpx.Timeout(None, connect=connect_timeout_s)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(f"{normalized_url}/internal/tools")
        response.raise_for_status()
    definitions = response.json().get("tools", [])
    return [
        ResidentToolProxy(
            base_url=normalized_url,
            definition=definition,
            connect_timeout_s=connect_timeout_s,
        )
        for definition in definitions
        if isinstance(definition, dict) and definition.get("name")
    ]
