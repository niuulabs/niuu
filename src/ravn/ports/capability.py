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
