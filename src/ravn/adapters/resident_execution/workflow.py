"""Configured resident execution adapter backed by workflow capabilities."""

from __future__ import annotations

from typing import Any

from niuu.utils import import_class, resolve_secret_kwargs

from ravn.domain.resident_portfolio import (
    ResidentExecutionPort,
    ResidentExecutionResult,
    ResidentExecutionSession,
    ResidentWorkerBrief,
)
from ravn.ports.capability import WorkflowCapabilityPort
from ravn.resident_portfolio import WorkflowResidentExecutionAdapter


class ConfiguredWorkflowResidentExecutionAdapter(ResidentExecutionPort):
    """Load a workflow source adapter and reuse the resident workflow executor."""

    def __init__(
        self,
        *,
        workflow_source_adapter: str,
        workflow_source_kwargs: dict[str, Any] | None = None,
        workflow_source_secret_kwargs_env: dict[str, str] | None = None,
        workflow_id: str = "",
        connection_id: str = "",
        repo: str = "",
        branch: str = "",
    ) -> None:
        if not workflow_source_adapter:
            raise ValueError("workflow_source_adapter is required")
        cls = import_class(workflow_source_adapter)
        kwargs = resolve_secret_kwargs(
            dict(workflow_source_kwargs or {}),
            dict(workflow_source_secret_kwargs_env or {}),
        )
        workflows: WorkflowCapabilityPort = cls(**kwargs)
        self._delegate = WorkflowResidentExecutionAdapter(
            workflows=workflows,
            workflow_id=workflow_id,
            connection_id=connection_id,
            repo=repo,
            branch=branch,
        )

    async def launch(self, brief: ResidentWorkerBrief) -> ResidentExecutionSession:
        return await self._delegate.launch(brief)

    async def read_status(self, session_id: str) -> ResidentExecutionSession:
        return await self._delegate.read_status(session_id)

    async def read_result(self, session_id: str) -> ResidentExecutionResult | None:
        return await self._delegate.read_result(session_id)

    async def cancel(self, session_id: str, reason: str) -> ResidentExecutionSession:
        return await self._delegate.cancel(session_id, reason)
