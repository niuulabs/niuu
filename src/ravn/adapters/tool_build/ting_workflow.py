"""Commission a learned-tool build via a Ting workflow.

A Ting workflow campaign itself spawns Forge sessions to do the work; ravn
launches the campaign and polls it for the built artifact over the
workload-authenticated HTTP boundary (ravn never imports ting).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from ravn.adapters.tool_build._contract import (
    build_prompts,
    parse_tool_build_response,
    poll_until,
)
from ravn.adapters.tool_build.http import AsyncJsonHttpClient, client_from_workload_identity
from ravn.domain.capability_catalog import (
    WorkflowCapability,
    WorkflowSelector,
    select_workflow,
)
from ravn.ports.tool_build_backend import (
    ToolBuildBackend,
    ToolBuildError,
    ToolBuildRequest,
    ToolBuildResult,
)

_DONE_STATUSES = frozenset({"completed", "complete", "failed", "error", "cancelled"})
_FAILED_STATUSES = frozenset({"failed", "error", "cancelled"})


class TingWorkflowToolBuildBackend(ToolBuildBackend):
    """Launch a Ting workflow campaign to build the tool, retrieve the artifact."""

    def __init__(
        self,
        *,
        base_url: str,
        workflow_id: str = "",
        workflow_selector: dict[str, Any] | None = None,
        client: AsyncJsonHttpClient | None = None,
        external_token_env: str = "",
        workload_token_file: str = "",
        workload_exchange_url: str = "",
        workload_audiences: list[str] | None = None,
        repo: str = "",
        branch: str = "",
        max_poll_attempts: int = 120,
        poll_interval_seconds: float = 5.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = (
            client
            if client is not None
            else client_from_workload_identity(
                base_url=base_url,
                external_token_env=external_token_env,
                workload_token_file=workload_token_file,
                workload_exchange_url=workload_exchange_url,
                workload_audiences=workload_audiences,
            )
        )
        self._base_url = base_url.rstrip("/")
        self._workflow_id = workflow_id
        self._workflow_selector = _selector_from_dict(workflow_selector)
        self._repo = repo
        self._branch = branch
        self._max_poll_attempts = max_poll_attempts
        self._poll_interval = poll_interval_seconds
        self._sleep = sleep

    @property
    def name(self) -> str:
        return "ting_workflow"

    @property
    def base_url(self) -> str:
        """Normalized base URL the backend talks to (read-only, for diagnostics)."""
        return self._base_url

    @property
    def client(self) -> AsyncJsonHttpClient:
        """The authenticated HTTP client (read-only, for diagnostics)."""
        return self._client

    @property
    def workflow_id(self) -> str:
        """Configured workflow id, or empty when discovery via selector is used."""
        return self._workflow_id

    @property
    def workflow_selector(self) -> WorkflowSelector:
        """Configured workflow selector (names/tags) used to discover the builder."""
        return self._workflow_selector

    async def build(self, request: ToolBuildRequest) -> ToolBuildResult:
        workflow_id = await self._resolve_workflow_id()
        if not workflow_id:
            raise ToolBuildError("ting_workflow backend requires workflow_id or workflow_selector")
        _system, initial_prompt = build_prompts(request)
        launch = await self._launch(request, initial_prompt, workflow_id=workflow_id)
        campaign_id = str(launch.get("campaign_id") or launch.get("id") or "")
        if not campaign_id:
            raise ToolBuildError("Ting workflow launch returned no campaign id")

        final = await poll_until(
            lambda: self._get_campaign(campaign_id),
            lambda c: _campaign_status(c) in _DONE_STATUSES,
            max_attempts=self._max_poll_attempts,
            interval_seconds=self._poll_interval,
            sleep=self._sleep,
        )
        status = _campaign_status(final)
        if status in _FAILED_STATUSES:
            raise ToolBuildError(f"Ting build campaign {campaign_id} ended in status {status!r}")
        if status not in _DONE_STATUSES:
            raise ToolBuildError(
                f"Ting build campaign {campaign_id} did not finish "
                f"within {self._max_poll_attempts} polls (last status {status!r})"
            )

        content = await self._artifact_content(campaign_id, final)
        result = parse_tool_build_response(content, tool_name=request.name)
        return ToolBuildResult(
            manifest=result.manifest,
            tool_code=result.tool_code,
            provenance={
                "backend": self.name,
                "ting_campaign_id": campaign_id,
                "ting_workflow_id": workflow_id,
                "build_request": request.build_request,
            },
        )

    async def _resolve_workflow_id(self) -> str:
        if self._workflow_id:
            return self._workflow_id
        if not self._workflow_selector.configured:
            return ""
        resp = await self._client.get(f"{self._base_url}/api/v1/ting/workflows")
        if resp.status_code != 200 or not isinstance(resp.body, list):
            raise ToolBuildError(f"Ting workflow discovery returned HTTP {resp.status_code}")
        workflows = [_workflow_from_body(item) for item in resp.body if isinstance(item, dict)]
        workflow = select_workflow(self._workflow_selector, workflows)
        if workflow is None:
            raise ToolBuildError("Ting workflow discovery found no matching tool-builder workflow")
        self._workflow_id = workflow.workflow_id
        return self._workflow_id

    async def _launch(
        self,
        request: ToolBuildRequest,
        initial_prompt: str,
        *,
        workflow_id: str,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "prompt": initial_prompt,
            "sessionName": f"tool-build-{request.name}",
        }
        if self._repo:
            body["repo"] = self._repo
        if self._branch:
            body["branch"] = self._branch
        url = f"{self._base_url}/api/v1/ting/workflows/{workflow_id}/launch"
        resp = await self._client.post(url, body)
        if resp.status_code not in (200, 201):
            raise ToolBuildError(f"Ting workflow launch returned HTTP {resp.status_code}")
        if not isinstance(resp.body, dict):
            raise ToolBuildError("Ting workflow launch returned a non-object body")
        return resp.body

    async def _get_campaign(self, campaign_id: str) -> dict[str, Any]:
        resp = await self._client.get(
            f"{self._base_url}/api/v1/ting/research/campaigns/{campaign_id}"
        )
        if resp.status_code != 200 or not isinstance(resp.body, dict):
            return {"status": "running"}
        return resp.body

    async def _artifact_content(self, campaign_id: str, campaign: dict[str, Any]) -> str:
        # Prefer an inline result/summary on the campaign; fall back to the
        # first code artifact's content endpoint.
        for key in ("result", "summary", "output"):
            value = campaign.get(key)
            if isinstance(value, str) and value.strip():
                return value
        artifacts = campaign.get("artifacts")
        if isinstance(artifacts, list):
            for artifact in artifacts:
                if not isinstance(artifact, dict):
                    continue
                content = artifact.get("content")
                if isinstance(content, str) and content.strip():
                    return content
        raise ToolBuildError(f"Ting build campaign {campaign_id} produced no retrievable artifact")


def _campaign_status(campaign: Any) -> str:
    if not isinstance(campaign, dict):
        return "running"
    return str(campaign.get("status") or "running").lower()


def _selector_from_dict(value: dict[str, Any] | None) -> WorkflowSelector:
    if not isinstance(value, dict):
        value = {}
    names = value.get("names")
    tags = value.get("tags")
    return WorkflowSelector(
        names=[str(item) for item in names if str(item).strip()] if isinstance(names, list) else [],
        tags=[str(item) for item in tags if str(item).strip()] if isinstance(tags, list) else [],
        require_all_tags=bool(value.get("require_all_tags")),
    )


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
