"""Authenticated HTTP client for the domain-neutral durable execution lifecycle.

Fan-out and join, durable waits, retry, replies, and cancellation call the
generic ``/workflow-executions`` execution. A specialization such as code
delivery layers its own client over the same execution id (see
``ravn.adapters.delivery_http``) instead of teaching this module its
vocabulary.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from ravn.adapters.tool_build.http import AsyncJsonHttpClient


class HttpWorkflowExecutionClient:
    """Call the owner-bound Ting generic execution facade from a Ravn session."""

    def __init__(
        self,
        *,
        base_url: str,
        execution_id: str,
        client: AsyncJsonHttpClient,
    ) -> None:
        if not execution_id.strip():
            raise ValueError("workflow execution_id is required")
        root = base_url.rstrip("/")
        execution_id = execution_id.strip()
        self._url = f"{root}/api/v1/ting/workflow-executions/{execution_id}"
        self._client = client

    async def expand(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post(self._url, "/expansions", payload)

    async def reconcile(self, payload: dict[str, Any]) -> dict[str, Any]:
        del payload
        return await self._post(self._url, "/reconcile", {})

    async def retry(self, payload: dict[str, Any]) -> dict[str, Any]:
        child_key = str(payload.get("child_key") or "").strip()
        attempt_id = str(payload.get("attempt_id") or "").strip()
        if not child_key or not attempt_id:
            raise ValueError("retry requires the exact child_key and current attempt_id")
        return await self._post(
            self._url, f"/children/{quote(child_key, safe='')}/retry", {"attempt_id": attempt_id}
        )

    async def cancel(self) -> dict[str, Any]:
        return await self._post(self._url, "/cancel", {})

    async def message(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post(self._url, "/messages", payload)

    async def wait(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post(self._url, "/waits", payload)

    async def _post(self, base_url: str, suffix: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._client.post(base_url + suffix, payload)
        if not 200 <= response.status_code < 300:
            raise RuntimeError(_http_error(response.status_code, response.body))
        if not isinstance(response.body, dict):
            raise RuntimeError("Ting workflow execution returned a non-object response")
        return response.body


def _http_error(status_code: int, body: object) -> str:
    detail = body.get("detail") if isinstance(body, dict) else None
    return f"HTTP {status_code}: {detail or 'operation failed'}"
