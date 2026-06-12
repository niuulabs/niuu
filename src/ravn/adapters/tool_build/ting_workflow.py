"""Commission a learned-tool build via a Ting workflow.

A Ting workflow campaign itself spawns Forge sessions to do the work; ravn
launches the campaign and polls it for the built artifact, over the same
PAT-authenticated HTTP boundary (ravn never imports ting).
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
from ravn.adapters.tool_build.http import AsyncJsonHttpClient, client_from_pat_env
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
        workflow_id: str,
        client: AsyncJsonHttpClient | None = None,
        pat_env: str = "",
        repo: str = "",
        branch: str = "",
        max_poll_attempts: int = 120,
        poll_interval_seconds: float = 5.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = client if client is not None else client_from_pat_env(pat_env)
        self._base_url = base_url.rstrip("/")
        self._workflow_id = workflow_id
        self._repo = repo
        self._branch = branch
        self._max_poll_attempts = max_poll_attempts
        self._poll_interval = poll_interval_seconds
        self._sleep = sleep

    @property
    def name(self) -> str:
        return "ting_workflow"

    async def build(self, request: ToolBuildRequest) -> ToolBuildResult:
        if not self._workflow_id:
            raise ToolBuildError("ting_workflow backend requires a configured workflow_id")
        _system, initial_prompt = build_prompts(request)
        launch = await self._launch(request, initial_prompt)
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
                "ting_workflow_id": self._workflow_id,
                "build_request": request.build_request,
            },
        )

    async def _launch(self, request: ToolBuildRequest, initial_prompt: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "prompt": initial_prompt,
            "sessionName": f"tool-build-{request.name}",
        }
        if self._repo:
            body["repo"] = self._repo
        if self._branch:
            body["branch"] = self._branch
        url = f"{self._base_url}/api/v1/ting/workflows/{self._workflow_id}/launch"
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
