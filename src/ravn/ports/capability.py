"""Ports for resident capability discovery and invocation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ravn.domain.capability_resolution import WorkflowCapability


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


@dataclass(frozen=True)
class WorkflowSubmissionRecord:
    """Durable record of a resident workflow submission."""

    submission_id: str
    status: str
    signal_id: str
    workflow_id: str
    workflow_name: str
    decision: str
    environment_id: str
    valkyrie_id: str
    owner_id: str = ""
    tenant_id: str = ""
    workload_subject: str = ""
    workload_name: str = ""
    source_id: str = ""
    source_event_id: str = ""
    correlation_id: str = ""
    session_id: str = ""
    session_name: str = ""
    slug: str = ""
    cluster_name: str = ""
    error: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


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


class WorkflowSubmissionStore(ABC):
    """Durable state for resident workflow submissions."""

    @abstractmethod
    async def upsert(self, record: WorkflowSubmissionRecord) -> WorkflowSubmissionRecord:
        """Insert or replace one workflow submission record."""
        raise NotImplementedError

    @abstractmethod
    async def get(self, submission_id: str) -> WorkflowSubmissionRecord | None:
        """Return one submission record, or None when unknown."""
        raise NotImplementedError

    @abstractmethod
    async def list_submissions(
        self,
        *,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[WorkflowSubmissionRecord]:
        """Return newest submissions, optionally filtered by status."""
        raise NotImplementedError
