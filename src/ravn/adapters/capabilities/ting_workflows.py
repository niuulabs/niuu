"""Workflow capability adapter backed by Ting's existing workflow catalog.

The adapter talks to Ting over HTTP and authenticates by exchanging the
resident's projected workload token for the same short-lived Niuu workload JWT
that Envoy already accepts. Ravn does not import Ting internals.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx

from ravn.domain.capability_resolution import WorkflowCapability
from ravn.ports.capability import (
    WorkflowCapabilityPort,
    WorkflowLaunchRequest,
    WorkflowLaunchResult,
)


class TingWorkflowCapabilityAdapter(WorkflowCapabilityPort):
    """Discover and launch workflows through Ting's REST API."""

    def __init__(
        self,
        *,
        base_url: str,
        workload_token_file: str = "/var/run/secrets/kubernetes.io/serviceaccount/token",
        exchange_url: str = "",
        token_ttl_skew_seconds: int = 30,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._workload_token_file = Path(workload_token_file)
        self._exchange_url = exchange_url.rstrip("/") or (
            f"{self._base_url}/api/v1/tokens/workload/exchange"
        )
        self._token_ttl_skew_seconds = token_ttl_skew_seconds
        self._timeout_seconds = timeout_seconds
        self._cached_token = ""
        self._cached_expires_at = 0

    async def list_workflows(self) -> list[WorkflowCapability]:
        resp = await self._request("GET", f"{self._base_url}/api/v1/ting/workflows")
        if resp.status_code != 200:
            raise RuntimeError(f"Ting workflow discovery returned HTTP {resp.status_code}")
        body = resp.json()
        if not isinstance(body, list):
            raise RuntimeError("Ting workflow discovery returned a non-list body")
        return [_workflow_from_body(item) for item in body if isinstance(item, dict)]

    async def launch_workflow(self, request: WorkflowLaunchRequest) -> WorkflowLaunchResult:
        body: dict[str, Any] = {"prompt": request.prompt}
        if request.session_name:
            body["sessionName"] = request.session_name
        if request.repo:
            body["repo"] = request.repo
        if request.branch:
            body["branch"] = request.branch
        if request.connection_id:
            body["connectionId"] = request.connection_id
        if request.provenance:
            body["provenance"] = dict(request.provenance)

        resp = await self._request(
            "POST",
            f"{self._base_url}/api/v1/ting/workflows/{request.workflow_id}/launch",
            json=body,
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Ting workflow launch returned HTTP {resp.status_code}")
        raw = resp.json()
        if not isinstance(raw, dict):
            raise RuntimeError("Ting workflow launch returned a non-object body")
        return WorkflowLaunchResult(
            workflow_id=str(raw.get("workflowId") or raw.get("workflow_id") or request.workflow_id),
            workflow_name=str(raw.get("workflowName") or raw.get("workflow_name") or ""),
            session_id=str(raw.get("sessionId") or raw.get("session_id") or ""),
            session_name=str(raw.get("sessionName") or raw.get("session_name") or ""),
            status=str(raw.get("status") or ""),
            slug=str(raw.get("slug") or ""),
            cluster_name=str(raw.get("clusterName") or raw.get("cluster_name") or ""),
            raw=raw,
        )

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        token = await self._workload_bearer_token()
        headers = dict(kwargs.pop("headers", {}) or {})
        headers["Authorization"] = f"Bearer {token}"
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            return await client.request(method, url, headers=headers, **kwargs)

    async def _workload_bearer_token(self) -> str:
        now = int(time.time())
        if self._cached_token and self._cached_expires_at - self._token_ttl_skew_seconds > now:
            return self._cached_token

        proof = self._workload_token_file.read_text(encoding="utf-8").strip()
        if not proof:
            raise RuntimeError(f"workload token file is empty: {self._workload_token_file}")
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            resp = await client.post(self._exchange_url, json={"token": proof})
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"workload token exchange returned HTTP {resp.status_code}")
        body = resp.json()
        token = str(body.get("token") or "")
        if not token:
            raise RuntimeError("workload token exchange returned no token")
        self._cached_token = token
        self._cached_expires_at = int(body.get("expiresAt") or body.get("expires_at") or now)
        return token


def _workflow_from_body(body: dict[str, Any]) -> WorkflowCapability:
    tags = body.get("tags")
    if not isinstance(tags, list):
        tags = []
    return WorkflowCapability(
        workflow_id=str(body.get("id") or ""),
        name=str(body.get("name") or ""),
        description=str(body.get("description") or ""),
        version=str(body.get("version") or ""),
        tags=[str(tag) for tag in tags if str(tag).strip()],
        metadata={
            "scope": body.get("scope"),
            "owner_id": body.get("ownerId") or body.get("owner_id"),
        },
    )
