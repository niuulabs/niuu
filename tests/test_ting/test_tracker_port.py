"""Tests for TrackerPort abstract interface."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from ting.domain.models import (
    Phase,
    PhaseStatus,
    Run,
    RunStatus,
    Saga,
    SagaStatus,
    TrackerIssue,
    TrackerMilestone,
    TrackerProject,
)
from ting.ports.tracker import TrackerPort


class TestTrackerPortAbstract:
    def test_cannot_instantiate(self):
        """TrackerPort is abstract and cannot be instantiated directly."""
        with pytest.raises(TypeError):
            TrackerPort()

    def test_has_all_abstract_methods(self):
        expected_methods = {
            "create_saga",
            "create_phase",
            "create_run",
            "update_run_state",
            "close_run",
            "get_saga",
            "get_phase",
            "get_run",
            "list_pending_runs",
            "list_projects",
            "get_project",
            "list_milestones",
            "list_issues",
            "update_run_progress",
            "get_run_progress_for_saga",
            "get_run_by_session",
            "list_runs_by_status",
            "get_run_by_id",
            "add_confidence_event",
            "get_confidence_events",
            "all_runs_merged",
            "list_phases_for_saga",
            "update_phase_status",
            "get_saga_for_run",
            "get_phase_for_run",
            "get_owner_for_run",
            "save_session_message",
            "get_session_messages",
        }
        actual = set(TrackerPort.__abstractmethods__)
        assert actual == expected_methods


class ConcreteTracker(TrackerPort):
    """Minimal concrete implementation to verify the interface contract."""

    async def create_saga(self, saga: Saga, *, description: str = "") -> str:
        return "saga-1"

    async def create_phase(self, phase: Phase, *, project_id: str = "") -> str:
        return "phase-1"

    async def create_run(self, run: Run, *, project_id: str = "", milestone_id: str = "") -> str:
        return "run-1"

    async def update_run_state(self, run_id: str, state: RunStatus) -> None:
        pass

    async def close_run(self, run_id: str) -> None:
        pass

    async def get_saga(self, saga_id: str) -> Saga:
        now = datetime.now(UTC)
        return Saga(
            id=uuid4(),
            tracker_id=saga_id,
            tracker_type="test",
            slug="test",
            name="Test",
            repos=[],
            feature_branch="feat/test",
            status=SagaStatus.ACTIVE,
            confidence=0.0,
            created_at=now,
            base_branch="dev",
        )

    async def get_phase(self, tracker_id: str) -> Phase:
        return Phase(
            id=uuid4(),
            saga_id=UUID(int=0),
            tracker_id=tracker_id,
            number=1,
            name="Phase 1",
            status=PhaseStatus.PENDING,
            confidence=0.0,
        )

    async def get_run(self, tracker_id: str) -> Run:
        now = datetime.now(UTC)
        return Run(
            id=uuid4(),
            phase_id=UUID(int=0),
            tracker_id=tracker_id,
            name="Run",
            description="",
            acceptance_criteria=[],
            declared_files=[],
            estimate_hours=None,
            status=RunStatus.PENDING,
            confidence=0.0,
            session_id=None,
            branch=None,
            chronicle_summary=None,
            pr_url=None,
            pr_id=None,
            retry_count=0,
            created_at=now,
            updated_at=now,
        )

    async def list_pending_runs(self, phase_id: str) -> list[Run]:
        return []

    async def list_projects(self) -> list[TrackerProject]:
        return []

    async def get_project(self, project_id: str) -> TrackerProject:
        return TrackerProject(
            id=project_id,
            name="Proj",
            description="",
            status="active",
            url="",
            milestone_count=0,
            issue_count=0,
        )

    async def list_milestones(self, project_id: str) -> list[TrackerMilestone]:
        return []

    async def list_issues(
        self,
        project_id: str,
        milestone_id: str | None = None,
    ) -> list[TrackerIssue]:
        return []

    async def update_run_progress(self, tracker_id: str, **kwargs: object) -> Run:
        return await self.get_run(tracker_id)

    async def get_run_progress_for_saga(self, saga_tracker_id: str) -> list[Run]:
        return []

    async def get_run_by_session(self, session_id: str) -> Run | None:
        return None

    async def list_runs_by_status(self, status: RunStatus) -> list[Run]:
        return []

    async def get_run_by_id(self, run_id: UUID) -> Run | None:
        return None

    async def add_confidence_event(self, tracker_id: str, event: object) -> None:
        pass

    async def get_confidence_events(self, tracker_id: str) -> list:
        return []

    async def all_runs_merged(self, phase_tracker_id: str) -> bool:
        return False

    async def list_phases_for_saga(self, saga_tracker_id: str) -> list[Phase]:
        return []

    async def update_phase_status(self, phase_tracker_id: str, status: PhaseStatus) -> Phase | None:
        return None

    async def get_saga_for_run(self, tracker_id: str) -> Saga | None:
        return None

    async def get_phase_for_run(self, tracker_id: str) -> Phase | None:
        return None

    async def get_owner_for_run(self, tracker_id: str) -> str | None:
        return None

    async def save_session_message(self, message: object) -> None:
        pass

    async def get_session_messages(self, tracker_id: str) -> list:
        return []


class TestConcreteTracker:
    def test_can_instantiate(self):
        tracker = ConcreteTracker()
        assert isinstance(tracker, TrackerPort)

    async def test_create_saga(self):
        tracker = ConcreteTracker()
        now = datetime.now(UTC)
        saga = Saga(
            id=uuid4(),
            tracker_id="t-1",
            tracker_type="test",
            slug="s",
            name="S",
            repos=["r"],
            feature_branch="feat/test",
            status=SagaStatus.ACTIVE,
            confidence=0.0,
            created_at=now,
            base_branch="dev",
        )
        result = await tracker.create_saga(saga)
        assert result == "saga-1"

    async def test_list_projects(self):
        tracker = ConcreteTracker()
        projects = await tracker.list_projects()
        assert projects == []

    async def test_get_project(self):
        tracker = ConcreteTracker()
        project = await tracker.get_project("p-1")
        assert project.id == "p-1"

    async def test_list_issues_with_milestone(self):
        tracker = ConcreteTracker()
        issues = await tracker.list_issues("p-1", milestone_id="m-1")
        assert issues == []

    async def test_get_blocked_identifiers_default(self):
        """Default implementation returns empty set (no dependency tracking)."""
        tracker = ConcreteTracker()
        result = await tracker.get_blocked_identifiers("p-1")
        assert result == set()
        assert isinstance(result, set)
