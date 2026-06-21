"""Ports for resident workflow capability discovery and invocation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ravn.domain.capability_catalog import WorkflowCapability


@dataclass(frozen=True)
class WorkflowLaunchRequest:
    """Request to launch a discovered workflow for a resident signal."""

    workflow_id: str
    prompt: str
    session_name: str = ""
    repo: str = ""
    branch: str = ""
    connection_id: str = ""
    model: str = ""
    definition: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkflowLaunchResult:
    """Minimal workflow launch result returned to a resident daemon."""

    workflow_id: str
    workflow_name: str
    session_id: str
    session_name: str
    status: str
    slug: str = ""
    cluster_name: str = ""
    owner_id: str = ""
    tenant_id: str = ""
    workload_subject: str = ""
    workload_name: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkflowRunReference:
    """Identifiers returned by workflow launch or known by a caller."""

    session_id: str = ""
    slug: str = ""
    workflow_id: str = ""


@dataclass(frozen=True)
class WorkflowRunStatus:
    """Workflow runtime state as reported by the workflow owner."""

    state: str
    session_id: str = ""
    slug: str = ""
    workflow_id: str = ""
    workflow_name: str = ""
    session_name: str = ""
    cluster_name: str = ""
    active_stage_id: str = ""
    stage_state: list[dict[str, Any]] = field(default_factory=list)
    terminal: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkflowRunEvent:
    """Raw/reported workflow event from the workflow ledger."""

    event_type: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""
    source: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkflowArtifact:
    """Artifact pointer reported by a workflow owner."""

    path: str
    title: str = ""
    kind: str = ""
    summary: str = ""
    publish_state: str = ""
    source_ids: list[str] = field(default_factory=list)
    canonical: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkflowArtifactContent:
    """Readable workflow artifact content when the owner can serve it."""

    artifact: WorkflowArtifact
    content: str
    raw: dict[str, Any] = field(default_factory=dict)


class WorkflowCapabilityPort(ABC):
    """Discover and launch workflow capabilities through an existing catalog."""

    @abstractmethod
    async def list_workflows(self) -> list[WorkflowCapability]:
        """Return workflows visible to the daemon identity."""
        raise NotImplementedError

    @abstractmethod
    async def launch_workflow(self, request: WorkflowLaunchRequest) -> WorkflowLaunchResult:
        """Launch a workflow through the catalog's existing execution API."""
        raise NotImplementedError

    async def get_workflow_status(self, reference: WorkflowRunReference) -> WorkflowRunStatus:
        """Return run/campaign status from the workflow owner's ledger."""
        raise NotImplementedError

    async def list_workflow_events(
        self,
        reference: WorkflowRunReference,
        *,
        limit: int = 100,
    ) -> list[WorkflowRunEvent]:
        """Return raw/reported workflow events when the owner exposes them."""
        raise NotImplementedError

    async def list_workflow_artifacts(
        self,
        reference: WorkflowRunReference,
    ) -> list[WorkflowArtifact]:
        """Return artifact pointers reported by the workflow owner."""
        raise NotImplementedError

    async def read_workflow_artifact(
        self,
        reference: WorkflowRunReference,
        *,
        path: str,
    ) -> WorkflowArtifactContent:
        """Read one artifact from the workflow owner."""
        raise NotImplementedError

    async def cancel_workflow(
        self,
        reference: WorkflowRunReference,
        *,
        reason: str,
    ) -> WorkflowRunStatus:
        """Cancel or stop a workflow run when the owner exposes a lifecycle API."""
        raise NotImplementedError
