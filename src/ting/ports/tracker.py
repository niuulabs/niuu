"""Tracker port — interface for issue/work-item tracking systems."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol
from uuid import UUID

from ting.domain.models import (
    ConfidenceEvent,
    Phase,
    PhaseStatus,
    Run,
    RunStatus,
    Saga,
    SessionMessage,
    TrackerIssue,
    TrackerMilestone,
    TrackerProject,
)


class TrackerFactory(Protocol):
    """Protocol for resolving per-owner TrackerPort adapters."""

    async def for_owner(self, owner_id: str) -> list[TrackerPort]:
        raise NotImplementedError


class TrackerPort(ABC):
    """Abstract interface for tracker integration (Linear, Jira, etc.)."""

    # -- CRUD: create entities in the external tracker --

    @abstractmethod
    async def create_saga(self, saga: Saga, *, description: str = "") -> str:
        raise NotImplementedError

    @abstractmethod
    async def create_phase(self, phase: Phase, *, project_id: str = "") -> str:
        raise NotImplementedError

    @abstractmethod
    async def create_run(
        self, run: Run, *, project_id: str = "", milestone_id: str = ""
    ) -> str:
        raise NotImplementedError

    async def attach_document(self, project_id: str, title: str, content: str) -> str:
        """Attach a document to a project. Returns document ID. Optional — noop by default."""
        return ""

    async def add_comment(self, issue_id: str, body: str) -> None:
        """Add a comment to an issue. Optional — noop by default."""
        return None

    async def attach_issue_document(self, issue_id: str, title: str, content: str) -> str:
        """Attach a document to an issue. Returns document ID. Optional — noop by default."""
        return ""

    # -- CRUD: update / close --

    @abstractmethod
    async def update_run_state(self, run_id: str, state: RunStatus) -> None:
        raise NotImplementedError

    @abstractmethod
    async def close_run(self, run_id: str) -> None:
        raise NotImplementedError

    # -- Read: fetch domain entities by tracker ID --

    @abstractmethod
    async def get_saga(self, saga_id: str) -> Saga:
        raise NotImplementedError

    @abstractmethod
    async def get_phase(self, tracker_id: str) -> Phase:
        raise NotImplementedError

    @abstractmethod
    async def get_run(self, tracker_id: str) -> Run:
        raise NotImplementedError

    @abstractmethod
    async def list_pending_runs(self, phase_id: str) -> list[Run]:
        raise NotImplementedError

    # -- Dependency tracking --

    async def get_blocked_identifiers(self, project_id: str) -> set[str]:
        """Return identifiers of issues blocked by incomplete dependencies.

        Default returns empty set (no dependency tracking).
        """
        return set()

    # -- Browsing: read-only access to external tracker hierarchy --

    @abstractmethod
    async def list_projects(self) -> list[TrackerProject]:
        raise NotImplementedError

    @abstractmethod
    async def get_project(self, project_id: str) -> TrackerProject:
        raise NotImplementedError

    @abstractmethod
    async def list_milestones(self, project_id: str) -> list[TrackerMilestone]:
        raise NotImplementedError

    @abstractmethod
    async def list_issues(
        self,
        project_id: str,
        milestone_id: str | None = None,
    ) -> list[TrackerIssue]:
        raise NotImplementedError

    # -- Run progress: operational state (status, session, confidence, PR) --

    @abstractmethod
    async def update_run_progress(
        self,
        tracker_id: str,
        *,
        status: RunStatus | None = None,
        session_id: str | None = None,
        confidence: float | None = None,
        pr_url: str | None = None,
        pr_id: str | None = None,
        retry_count: int | None = None,
        reason: str | None = None,
        owner_id: str | None = None,
        phase_tracker_id: str | None = None,
        saga_tracker_id: str | None = None,
        chronicle_summary: str | None = None,
        reviewer_session_id: str | None = None,
        review_round: int | None = None,
    ) -> Run:
        raise NotImplementedError

    @abstractmethod
    async def get_run_progress_for_saga(self, saga_tracker_id: str) -> list[Run]:
        raise NotImplementedError

    @abstractmethod
    async def get_run_by_session(self, session_id: str) -> Run | None:
        raise NotImplementedError

    @abstractmethod
    async def list_runs_by_status(self, status: RunStatus) -> list[Run]:
        raise NotImplementedError

    @abstractmethod
    async def get_run_by_id(self, run_id: UUID) -> Run | None:
        raise NotImplementedError

    # -- Confidence events --

    @abstractmethod
    async def add_confidence_event(self, tracker_id: str, event: ConfidenceEvent) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_confidence_events(self, tracker_id: str) -> list[ConfidenceEvent]:
        raise NotImplementedError

    # -- Phase gate management --

    @abstractmethod
    async def all_runs_merged(self, phase_tracker_id: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def list_phases_for_saga(self, saga_tracker_id: str) -> list[Phase]:
        raise NotImplementedError

    @abstractmethod
    async def update_phase_status(
        self, phase_tracker_id: str, status: PhaseStatus
    ) -> Phase | None:
        raise NotImplementedError

    # -- Cross-entity navigation --

    @abstractmethod
    async def get_saga_for_run(self, tracker_id: str) -> Saga | None:
        raise NotImplementedError

    @abstractmethod
    async def get_phase_for_run(self, tracker_id: str) -> Phase | None:
        raise NotImplementedError

    @abstractmethod
    async def get_owner_for_run(self, tracker_id: str) -> str | None:
        raise NotImplementedError

    # -- Session messages --

    @abstractmethod
    async def save_session_message(self, message: SessionMessage) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_session_messages(self, tracker_id: str) -> list[SessionMessage]:
        raise NotImplementedError
