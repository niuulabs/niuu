"""Tests for DispatchService — domain service for dispatch logic.

Tests the service directly without any HTTP context, verifying that
find_ready_issues and dispatch_issues behave correctly.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
import yaml

from tyr.config import FlockConfig, PersonaOverride
from tyr.domain.flock_flow import FlockFlowConfig, FlockPersonaOverride
from tyr.domain.models import (
    Phase,
    PhaseStatus,
    Raid,
    RaidStatus,
    Saga,
    SagaStatus,
    TrackerIssue,
    TrackerMilestone,
    TrackerProject,
    WorkflowDefinition,
    WorkflowScope,
)
from tyr.domain.services.dispatch_service import (
    DispatchConfig,
    DispatchItem,
    DispatchService,
    _format_persona_label,
    build_prompt,
    is_ready,
    resolve_target_adapter,
)
from tyr.domain.templates import TemplatePhase, TemplateRaid
from tyr.ports.workflow_repository import WorkflowRepository

from ..stubs import StubFlockFlowProvider
from ..test_dispatch_api import (
    MockDispatcherRepo,
    MockTrackerFactory,
    MockVolundr,
    MockVolundrFactory,
)
from ..test_tracker_api import MockSagaRepo, MockTracker

# -------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------


def _make_tracker() -> MockTracker:
    tracker = MockTracker()
    tracker.projects = [
        TrackerProject(
            id="proj-1",
            name="Alpha",
            description="First project",
            status="started",
            url="https://linear.app/proj-1",
            milestone_count=1,
            issue_count=3,
        ),
    ]
    tracker.milestones = {
        "proj-1": [
            TrackerMilestone(
                id="ms-1",
                project_id="proj-1",
                name="Phase 1",
                description="First phase",
                sort_order=1,
                progress=0.0,
            ),
        ],
    }
    tracker.issues = {
        "proj-1": [
            TrackerIssue(
                id="i-1",
                identifier="ALPHA-1",
                title="Setup CI",
                description="Configure CI pipeline",
                status="Todo",
                priority=1,
                priority_label="Urgent",
                estimate=2.0,
                url="https://linear.app/i-1",
                milestone_id="ms-1",
            ),
            TrackerIssue(
                id="i-2",
                identifier="ALPHA-2",
                title="Add tests",
                description="Write unit tests",
                status="In Progress",
                priority=2,
                priority_label="High",
                url="https://linear.app/i-2",
                milestone_id="ms-1",
            ),
            TrackerIssue(
                id="i-3",
                identifier="ALPHA-3",
                title="Fix bug",
                description="Fix the bug",
                status="Backlog",
                priority=3,
                priority_label="Medium",
                url="https://linear.app/i-3",
                milestone_id="ms-1",
            ),
        ],
    }
    return tracker


def _make_saga() -> Saga:
    return Saga(
        id=uuid4(),
        tracker_id="proj-1",
        tracker_type="linear",
        slug="alpha",
        name="Alpha",
        repos=["org/repo-a", "org/repo-b"],
        feature_branch="feat/alpha",
        status=SagaStatus.ACTIVE,
        confidence=0.0,
        created_at=datetime.now(UTC),
        base_branch="dev",
    )


@pytest.fixture
def tracker() -> MockTracker:
    return _make_tracker()


@pytest.fixture
def volundr() -> MockVolundr:
    return MockVolundr()


@pytest.fixture
def saga_repo() -> MockSagaRepo:
    repo = MockSagaRepo()
    repo.sagas.append(_make_saga())
    return repo


@pytest.fixture
def dispatcher_repo() -> MockDispatcherRepo:
    return MockDispatcherRepo()


@pytest.fixture
def service(
    tracker: MockTracker,
    volundr: MockVolundr,
    saga_repo: MockSagaRepo,
    dispatcher_repo: MockDispatcherRepo,
) -> DispatchService:
    return DispatchService(
        tracker_factory=MockTrackerFactory([tracker]),
        volundr_factory=MockVolundrFactory(adapters=[volundr]),
        saga_repo=saga_repo,
        dispatcher_repo=dispatcher_repo,
        config=DispatchConfig(
            default_system_prompt="Be helpful.",
            default_model="claude-sonnet-4-6",
        ),
    )


# -------------------------------------------------------------------
# Unit tests: pure helpers
# -------------------------------------------------------------------


class TestIsReady:
    def test_ready_todo(self):
        issue = TrackerIssue(id="1", identifier="X-1", title="t", description="", status="Todo")
        assert is_ready(issue, set(), set()) is True

    def test_ready_backlog(self):
        issue = TrackerIssue(id="1", identifier="X-1", title="t", description="", status="Backlog")
        assert is_ready(issue, set(), set()) is True

    def test_ready_triage(self):
        issue = TrackerIssue(id="1", identifier="X-1", title="t", description="", status="Triage")
        assert is_ready(issue, set(), set()) is True

    def test_ready_unstarted_status_type(self):
        issue = TrackerIssue(
            id="1",
            identifier="X-1",
            title="t",
            description="",
            status="Planned",
            status_type="unstarted",
        )
        assert is_ready(issue, set(), set()) is True

    def test_not_ready_in_progress(self):
        issue = TrackerIssue(
            id="1", identifier="X-1", title="t", description="", status="In Progress"
        )
        assert is_ready(issue, set(), set()) is False

    def test_not_ready_active_session(self):
        issue = TrackerIssue(id="1", identifier="X-1", title="t", description="", status="Todo")
        assert is_ready(issue, {"X-1"}, set()) is False

    def test_not_ready_blocked(self):
        issue = TrackerIssue(id="1", identifier="X-1", title="t", description="", status="Todo")
        assert is_ready(issue, set(), {"X-1"}) is False


class TestBuildPrompt:
    def test_fallback_contains_essentials(self):
        issue = TrackerIssue(
            id="1", identifier="X-3", title="Task", description="Do stuff", status="Todo"
        )
        prompt = build_prompt(issue, "org/repo", "feat/test")
        assert "feat/test" in prompt
        assert "x-3" in prompt
        assert "org/repo" in prompt

    def test_template_renders_placeholders(self):
        issue = TrackerIssue(
            id="1",
            identifier="NIU-42",
            title="Add auth",
            description="Implement OAuth",
            status="Todo",
        )
        template = (
            "Task: {identifier} — {title}\n{description}\n"
            "Branch: {raid_branch}\nPR target: {feature_branch}"
        )
        prompt = build_prompt(issue, "org/repo", "feat/saga", template=template)
        assert "NIU-42" in prompt
        assert "Add auth" in prompt
        assert "niu-42" in prompt


class TestResolveTargetAdapter:
    def test_no_connection_id_returns_fallback(self):
        fallback = MockVolundr()
        assert resolve_target_adapter(None, {}, fallback) is fallback

    def test_matching_returns_adapter(self):
        fallback = MockVolundr()
        target = MockVolundr()
        assert resolve_target_adapter("a", {"a": target}, fallback) is target

    def test_unknown_returns_fallback(self):
        fallback = MockVolundr()
        assert resolve_target_adapter("b", {"a": MockVolundr()}, fallback) is fallback


# -------------------------------------------------------------------
# Service tests: find_ready_issues
# -------------------------------------------------------------------


class TestFindReadyIssues:
    @pytest.mark.asyncio
    async def test_returns_ready_issues(self, service: DispatchService):
        items = await service.find_ready_issues("dev-user")
        ids = [i.identifier for i in items]
        assert "ALPHA-1" in ids
        assert "ALPHA-3" in ids
        assert "ALPHA-2" not in ids

    @pytest.mark.asyncio
    async def test_sorted_by_priority(self, service: DispatchService):
        items = await service.find_ready_issues("dev-user")
        priorities = [i.priority for i in items]
        assert priorities == sorted(priorities)

    @pytest.mark.asyncio
    async def test_excludes_active_sessions(
        self,
        tracker: MockTracker,
        saga_repo: MockSagaRepo,
        dispatcher_repo: MockDispatcherRepo,
    ):
        from tyr.ports.volundr import VolundrSession

        volundr = MockVolundr()
        volundr.sessions = [
            VolundrSession(
                id="ses-1",
                name="alpha-1",
                status="running",
                tracker_issue_id="ALPHA-1",
            ),
        ]
        svc = DispatchService(
            tracker_factory=MockTrackerFactory([tracker]),
            volundr_factory=MockVolundrFactory(adapters=[volundr]),
            saga_repo=saga_repo,
            dispatcher_repo=dispatcher_repo,
            config=DispatchConfig(),
        )
        items = await svc.find_ready_issues("dev-user")
        ids = [i.identifier for i in items]
        assert "ALPHA-1" not in ids
        assert "ALPHA-3" in ids

    @pytest.mark.asyncio
    async def test_excludes_blocked(
        self,
        tracker: MockTracker,
        service: DispatchService,
    ):
        tracker._blocked = {"ALPHA-1"}
        items = await service.find_ready_issues("dev-user")
        ids = [i.identifier for i in items]
        assert "ALPHA-1" not in ids
        assert "ALPHA-3" in ids

    @pytest.mark.asyncio
    async def test_includes_unstarted_linear_issues(
        self,
        tracker: MockTracker,
        service: DispatchService,
    ):
        tracker.issues["proj-1"].append(
            TrackerIssue(
                id="i-4",
                identifier="ALPHA-4",
                title="Plan rollout",
                description="Create rollout checklist",
                status="Planned",
                status_type="unstarted",
                priority=4,
                priority_label="Low",
                url="https://linear.app/i-4",
                milestone_id="ms-1",
            )
        )

        items = await service.find_ready_issues("dev-user")
        ids = [i.identifier for i in items]
        assert "ALPHA-4" in ids

    @pytest.mark.asyncio
    async def test_empty_when_no_sagas(
        self,
        tracker: MockTracker,
        volundr: MockVolundr,
        dispatcher_repo: MockDispatcherRepo,
    ):
        svc = DispatchService(
            tracker_factory=MockTrackerFactory([tracker]),
            volundr_factory=MockVolundrFactory(adapters=[volundr]),
            saga_repo=MockSagaRepo(),
            dispatcher_repo=dispatcher_repo,
            config=DispatchConfig(),
        )
        items = await svc.find_ready_issues("dev-user")
        assert items == []

    @pytest.mark.asyncio
    async def test_empty_when_no_volundr(
        self,
        tracker: MockTracker,
        saga_repo: MockSagaRepo,
        dispatcher_repo: MockDispatcherRepo,
    ):
        svc = DispatchService(
            tracker_factory=MockTrackerFactory([tracker]),
            volundr_factory=MockVolundrFactory(adapters=[]),
            saga_repo=saga_repo,
            dispatcher_repo=dispatcher_repo,
            config=DispatchConfig(),
        )
        items = await svc.find_ready_issues("dev-user")
        assert items == []

    @pytest.mark.asyncio
    async def test_scoped_to_saga_tracker_id(
        self,
        tracker: MockTracker,
        volundr: MockVolundr,
        dispatcher_repo: MockDispatcherRepo,
    ):
        """When saga_tracker_id is given, only that saga is queried."""
        repo = MockSagaRepo()
        saga1 = _make_saga()
        repo.sagas.append(saga1)
        repo.sagas.append(
            Saga(
                id=uuid4(),
                tracker_id="proj-other",
                tracker_type="linear",
                slug="beta",
                name="Beta",
                repos=["org/repo-b"],
                feature_branch="feat/beta",
                status=SagaStatus.ACTIVE,
                confidence=0.0,
                created_at=datetime.now(UTC),
                base_branch="main",
            )
        )
        svc = DispatchService(
            tracker_factory=MockTrackerFactory([tracker]),
            volundr_factory=MockVolundrFactory(adapters=[volundr]),
            saga_repo=repo,
            dispatcher_repo=dispatcher_repo,
            config=DispatchConfig(),
        )
        items = await svc.find_ready_issues("dev-user", saga_tracker_id="proj-1")
        assert len(items) > 0
        assert all(i.saga_id == str(saga1.id) for i in items)

    @pytest.mark.asyncio
    async def test_handles_tracker_error(
        self,
        volundr: MockVolundr,
        saga_repo: MockSagaRepo,
        dispatcher_repo: MockDispatcherRepo,
    ):
        class FailingTracker(MockTracker):
            async def get_project_full(self, project_id):
                raise ConnectionError("down")

        svc = DispatchService(
            tracker_factory=MockTrackerFactory([FailingTracker()]),
            volundr_factory=MockVolundrFactory(adapters=[volundr]),
            saga_repo=saga_repo,
            dispatcher_repo=dispatcher_repo,
            config=DispatchConfig(),
        )
        items = await svc.find_ready_issues("dev-user")
        assert items == []

    @pytest.mark.asyncio
    async def test_queue_item_has_saga_fields(self, service: DispatchService):
        items = await service.find_ready_issues("dev-user")
        item = next(i for i in items if i.identifier == "ALPHA-1")
        assert item.saga_name == "Alpha"
        assert item.saga_slug == "alpha"
        assert item.repos == ["org/repo-a", "org/repo-b"]
        assert item.feature_branch == "feat/alpha"
        assert item.phase_name == "Phase 1"


# -------------------------------------------------------------------
# Service tests: dispatch_issues
# -------------------------------------------------------------------


class TestDispatchIssues:
    @pytest.mark.asyncio
    async def test_spawns_sessions(
        self,
        service: DispatchService,
        saga_repo: MockSagaRepo,
        volundr: MockVolundr,
    ):
        saga_id = str(saga_repo.sagas[0].id)
        results = await service.dispatch_issues(
            owner_id="dev-user",
            items=[DispatchItem(saga_id=saga_id, issue_id="i-1", repo="org/repo-a")],
            model="claude-opus-4-6",
            system_prompt="Custom prompt.",
        )
        assert len(results) == 1
        assert results[0].status == "spawned"
        assert results[0].session_id == "ses-1"
        assert results[0].session_name == "alpha-1"

        assert len(volundr.spawned) == 1
        req = volundr.spawned[0]
        assert req.model == "claude-opus-4-6"
        assert req.system_prompt == "Custom prompt."
        assert req.repo == "org/repo-a"
        assert req.branch == "feat/alpha"
        assert req.tracker_issue_id == "ALPHA-1"

    @pytest.mark.asyncio
    async def test_uses_config_defaults(
        self,
        service: DispatchService,
        saga_repo: MockSagaRepo,
        volundr: MockVolundr,
    ):
        saga_id = str(saga_repo.sagas[0].id)
        await service.dispatch_issues(
            owner_id="dev-user",
            items=[DispatchItem(saga_id=saga_id, issue_id="i-1", repo="org/repo-a")],
        )
        req = volundr.spawned[0]
        assert req.model == "claude-sonnet-4-6"
        assert req.system_prompt == "Be helpful."

    @pytest.mark.asyncio
    async def test_skips_unknown_saga(
        self,
        service: DispatchService,
        volundr: MockVolundr,
    ):
        results = await service.dispatch_issues(
            owner_id="dev-user",
            items=[DispatchItem(saga_id=str(uuid4()), issue_id="i-1", repo="org/repo-a")],
        )
        assert results == []
        assert len(volundr.spawned) == 0

    @pytest.mark.asyncio
    async def test_skips_unknown_issue(
        self,
        service: DispatchService,
        saga_repo: MockSagaRepo,
        volundr: MockVolundr,
    ):
        saga_id = str(saga_repo.sagas[0].id)
        results = await service.dispatch_issues(
            owner_id="dev-user",
            items=[DispatchItem(saga_id=saga_id, issue_id="nonexistent", repo="org/repo-a")],
        )
        assert results == []
        assert len(volundr.spawned) == 0

    @pytest.mark.asyncio
    async def test_spawn_failure_returns_failed(
        self,
        tracker: MockTracker,
        saga_repo: MockSagaRepo,
        dispatcher_repo: MockDispatcherRepo,
    ):
        volundr = MockVolundr()
        volundr.fail_spawn = True
        svc = DispatchService(
            tracker_factory=MockTrackerFactory([tracker]),
            volundr_factory=MockVolundrFactory(adapters=[volundr]),
            saga_repo=saga_repo,
            dispatcher_repo=dispatcher_repo,
            config=DispatchConfig(),
        )
        saga_id = str(saga_repo.sagas[0].id)
        results = await svc.dispatch_issues(
            owner_id="dev-user",
            items=[DispatchItem(saga_id=saga_id, issue_id="i-1", repo="org/repo-a")],
        )
        assert len(results) == 1
        assert results[0].status == "failed"
        assert results[0].session_id == ""

    @pytest.mark.asyncio
    async def test_multiple_items(
        self,
        service: DispatchService,
        saga_repo: MockSagaRepo,
        volundr: MockVolundr,
    ):
        saga_id = str(saga_repo.sagas[0].id)
        results = await service.dispatch_issues(
            owner_id="dev-user",
            items=[
                DispatchItem(saga_id=saga_id, issue_id="i-1", repo="org/repo-a"),
                DispatchItem(saga_id=saga_id, issue_id="i-3", repo="org/repo-b"),
            ],
        )
        assert len(results) == 2
        assert all(r.status == "spawned" for r in results)
        assert len(volundr.spawned) == 2

    @pytest.mark.asyncio
    async def test_no_volundr_returns_all_failed(
        self,
        tracker: MockTracker,
        saga_repo: MockSagaRepo,
        dispatcher_repo: MockDispatcherRepo,
    ):
        svc = DispatchService(
            tracker_factory=MockTrackerFactory([tracker]),
            volundr_factory=MockVolundrFactory(adapters=[]),
            saga_repo=saga_repo,
            dispatcher_repo=dispatcher_repo,
            config=DispatchConfig(),
        )
        saga_id = str(saga_repo.sagas[0].id)
        results = await svc.dispatch_issues(
            owner_id="dev-user",
            items=[DispatchItem(saga_id=saga_id, issue_id="i-1", repo="org/repo-a")],
        )
        assert len(results) == 1
        assert results[0].status == "failed"

    @pytest.mark.asyncio
    async def test_updates_raid_progress(
        self,
        saga_repo: MockSagaRepo,
        dispatcher_repo: MockDispatcherRepo,
    ):
        """Verify raid progress is updated after spawn."""

        class TrackingTracker(MockTracker):
            def __init__(self) -> None:
                super().__init__()
                self.progress_calls: list[dict] = []

            async def update_raid_progress(self, tracker_id: str, **kwargs):
                self.progress_calls.append({"tracker_id": tracker_id, **kwargs})
                return await super().update_raid_progress(tracker_id, **kwargs)

        t = TrackingTracker()
        t.projects = _make_tracker().projects
        t.milestones = _make_tracker().milestones
        t.issues = _make_tracker().issues
        volundr = MockVolundr()

        svc = DispatchService(
            tracker_factory=MockTrackerFactory([t]),
            volundr_factory=MockVolundrFactory(adapters=[volundr]),
            saga_repo=saga_repo,
            dispatcher_repo=dispatcher_repo,
            config=DispatchConfig(default_system_prompt="Be helpful."),
        )
        saga_id = str(saga_repo.sagas[0].id)
        await svc.dispatch_issues(
            owner_id="dev-user",
            items=[DispatchItem(saga_id=saga_id, issue_id="i-1", repo="org/repo-a")],
        )
        assert len(t.progress_calls) == 1
        assert t.progress_calls[0]["tracker_id"] == "i-1"
        assert t.progress_calls[0]["session_id"] == "ses-1"

    @pytest.mark.asyncio
    async def test_forwards_auth_token(
        self,
        service: DispatchService,
        saga_repo: MockSagaRepo,
        volundr: MockVolundr,
    ):
        saga_id = str(saga_repo.sagas[0].id)
        await service.dispatch_issues(
            owner_id="dev-user",
            items=[DispatchItem(saga_id=saga_id, issue_id="i-1", repo="org/repo-a")],
            auth_token="my-token",
        )
        assert volundr.last_auth_token == "my-token"


# -------------------------------------------------------------------
# Service tests: active-saga filtering and auto-archive
# -------------------------------------------------------------------


class TestActiveSagaFiltering:
    @pytest.mark.asyncio
    async def test_skips_complete_sagas(
        self,
        tracker: MockTracker,
        volundr: MockVolundr,
        dispatcher_repo: MockDispatcherRepo,
    ):
        """Sagas with status COMPLETE are not queried from Linear at all."""
        repo = MockSagaRepo()
        repo.sagas.append(
            Saga(
                id=uuid4(),
                tracker_id="proj-1",
                tracker_type="linear",
                slug="done-project",
                name="Done",
                repos=["org/repo"],
                feature_branch="feat/done",
                status=SagaStatus.COMPLETE,
                confidence=0.0,
                created_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
                base_branch="dev",
            )
        )
        svc = DispatchService(
            tracker_factory=MockTrackerFactory([tracker]),
            volundr_factory=MockVolundrFactory(adapters=[volundr]),
            saga_repo=repo,
            dispatcher_repo=dispatcher_repo,
            config=DispatchConfig(),
        )
        items = await svc.find_ready_issues("dev-user")
        assert items == []

    @pytest.mark.asyncio
    async def test_auto_archives_when_linear_project_completed(
        self,
        volundr: MockVolundr,
        dispatcher_repo: MockDispatcherRepo,
    ):
        """When a Linear project is completed, the saga is auto-archived."""
        completed_tracker = MockTracker()
        completed_tracker.projects = [
            TrackerProject(
                id="proj-done",
                name="Done Project",
                description="",
                status="completed",
                url="https://linear.app/done",
                milestone_count=0,
                issue_count=0,
            )
        ]
        completed_tracker.milestones = {"proj-done": []}
        completed_tracker.issues = {"proj-done": []}

        class TrackingRepo(MockSagaRepo):
            def __init__(self) -> None:
                super().__init__()
                self.archived: list[tuple] = []

            async def update_saga_status(self, saga_id, status) -> None:
                self.archived.append((saga_id, status))

        repo = TrackingRepo()
        saga = Saga(
            id=uuid4(),
            tracker_id="proj-done",
            tracker_type="linear",
            slug="done",
            name="Done",
            repos=["org/repo"],
            feature_branch="feat/done",
            status=SagaStatus.ACTIVE,
            confidence=0.0,
            created_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            base_branch="dev",
        )
        repo.sagas.append(saga)

        svc = DispatchService(
            tracker_factory=MockTrackerFactory([completed_tracker]),
            volundr_factory=MockVolundrFactory(adapters=[volundr]),
            saga_repo=repo,
            dispatcher_repo=dispatcher_repo,
            config=DispatchConfig(),
        )
        items = await svc.find_ready_issues("dev-user")

        assert items == []
        assert len(repo.archived) == 1
        assert repo.archived[0] == (saga.id, SagaStatus.COMPLETE)

    @pytest.mark.asyncio
    async def test_fetch_saga_data_fallback_without_get_project_full(
        self,
        volundr: MockVolundr,
        saga_repo: MockSagaRepo,
        dispatcher_repo: MockDispatcherRepo,
    ):
        """When an adapter lacks get_project_full, falls back to list_milestones/list_issues."""
        source = _make_tracker()

        class MinimalTracker:
            """Tracker without get_project_full — exercises the fallback path."""

            async def list_milestones(self, project_id: str) -> list:
                return source.milestones.get(project_id, [])

            async def list_issues(self, project_id: str, milestone_id=None) -> list:
                return source.issues.get(project_id, [])

            async def get_blocked_identifiers(self, project_id: str) -> set:
                return set()

        svc = DispatchService(
            tracker_factory=MockTrackerFactory([MinimalTracker()]),
            volundr_factory=MockVolundrFactory(adapters=[volundr]),
            saga_repo=saga_repo,
            dispatcher_repo=dispatcher_repo,
            config=DispatchConfig(),
        )
        # Fallback returns None for project → no auto-archive, issues still returned
        items = await svc.find_ready_issues("dev-user")
        assert len(items) > 0


# -------------------------------------------------------------------
# Per-persona LLM override tests (NIU-637)
# -------------------------------------------------------------------


class TestFormatPersonaLabel:
    """_format_persona_label produces the correct log string."""

    def test_no_llm_shows_inherit(self):
        assert _format_persona_label({"name": "coordinator"}) == "coordinator(inherit)"

    def test_alias_shown(self):
        assert (
            _format_persona_label({"name": "auditor", "llm": {"primary_alias": "balanced"}})
            == "auditor(balanced)"
        )

    def test_alias_with_thinking(self):
        assert (
            _format_persona_label(
                {"name": "reviewer", "llm": {"primary_alias": "powerful", "thinking_enabled": True}}
            )
            == "reviewer(powerful/thinking)"
        )

    def test_thinking_false_no_suffix(self):
        persona = {
            "name": "reviewer",
            "llm": {"primary_alias": "powerful", "thinking_enabled": False},
        }
        assert _format_persona_label(persona) == "reviewer(powerful)"

    def test_empty_llm_shows_inherit(self):
        assert _format_persona_label({"name": "coder", "llm": {}}) == "coder(inherit)"


class TestPersonaOverrideModel:
    """PersonaOverride pydantic model: construction and serialisation."""

    def test_bare_string_coerced_by_flock_config(self):
        cfg = FlockConfig(default_personas=["coordinator", "reviewer"])  # type: ignore[arg-type]
        assert len(cfg.default_personas) == 2
        assert all(isinstance(p, PersonaOverride) for p in cfg.default_personas)
        assert cfg.default_personas[0].name == "coordinator"
        assert cfg.default_personas[1].name == "reviewer"

    def test_dict_accepted(self):
        cfg = FlockConfig(
            default_personas=[
                {"name": "coordinator"},
                {"name": "reviewer", "llm": {"primary_alias": "powerful"}, "iteration_budget": 40},
            ]
        )
        assert cfg.default_personas[1].llm == {"primary_alias": "powerful"}
        assert cfg.default_personas[1].iteration_budget == 40

    def test_to_dict_minimal(self):
        p = PersonaOverride(name="coordinator")
        assert p.to_dict() == {"name": "coordinator"}

    def test_to_dict_with_overrides(self):
        p = PersonaOverride(
            name="reviewer",
            llm={"primary_alias": "powerful", "thinking_enabled": True},
            iteration_budget=40,
        )
        d = p.to_dict()
        assert d == {
            "name": "reviewer",
            "llm": {"primary_alias": "powerful", "thinking_enabled": True},
            "iteration_budget": 40,
        }

    def test_to_dict_omits_empty_llm(self):
        p = PersonaOverride(name="coordinator")
        assert "llm" not in p.to_dict()

    def test_to_dict_omits_none_iteration_budget(self):
        p = PersonaOverride(name="coordinator")
        assert "iteration_budget" not in p.to_dict()

    def test_to_dict_omits_none_system_prompt_extra(self):
        p = PersonaOverride(name="coordinator")
        assert "system_prompt_extra" not in p.to_dict()


class TestFlockConfigYamlParse:
    """FlockConfig handles legacy string lists and per-persona dicts from YAML."""

    def test_legacy_string_list(self):
        raw = yaml.safe_load(
            """
            enabled: true
            default_personas:
              - coordinator
              - reviewer
            """
        )
        cfg = FlockConfig(**raw)
        assert [p.name for p in cfg.default_personas] == ["coordinator", "reviewer"]
        assert all(p.llm == {} for p in cfg.default_personas)

    def test_per_persona_dict_list(self):
        raw = yaml.safe_load(
            """
            enabled: true
            default_personas:
              - name: coordinator
              - name: reviewer
                llm:
                  primary_alias: powerful
                  thinking_enabled: true
                iteration_budget: 40
              - name: security-auditor
                llm:
                  primary_alias: balanced
            """
        )
        cfg = FlockConfig(**raw)
        names = [p.name for p in cfg.default_personas]
        assert names == ["coordinator", "reviewer", "security-auditor"]
        coordinator = cfg.default_personas[0]
        reviewer = cfg.default_personas[1]
        auditor = cfg.default_personas[2]
        assert coordinator.llm == {}
        assert reviewer.llm == {"primary_alias": "powerful", "thinking_enabled": True}
        assert reviewer.iteration_budget == 40
        assert auditor.llm == {"primary_alias": "balanced"}


class TestBuildSpawnRequestPersonaOverrides:
    """_build_spawn_request emits the correct personas list-of-dicts."""

    def _make_saga(self) -> Saga:
        return Saga(
            id=uuid4(),
            tracker_id="proj-1",
            tracker_type="linear",
            slug="alpha",
            name="Alpha",
            repos=["org/repo"],
            feature_branch="feat/alpha",
            base_branch="main",
            status=SagaStatus.ACTIVE,
            confidence=0.5,
            created_at=datetime.now(UTC),
        )

    def _make_issue(self) -> TrackerIssue:
        return TrackerIssue(
            id="i-1",
            identifier="ALPHA-1",
            title="Task",
            description="Do it",
            status="Todo",
            url="https://linear.app/i-1",
        )

    def _call(self, config: DispatchConfig, saga, issue):
        svc = MagicMock()
        svc._config = config
        item = DispatchItem(saga_id=str(saga.id), issue_id="i-1", repo="org/repo")
        return DispatchService._build_spawn_request(
            svc,
            item=item,
            saga=saga,
            issue=issue,
            effective_model="claude-sonnet-4-6",
            effective_prompt="",
            integration_ids=[],
        )

    def test_legacy_string_personas_emit_dicts(self):
        """Legacy list[str] → list-of-dicts in workload_config."""
        config = DispatchConfig(
            flock_enabled=True,
            flock_default_personas=[{"name": "coordinator"}, {"name": "reviewer"}],
        )
        req = self._call(config, self._make_saga(), self._make_issue())
        assert req.workload_config["personas"] == [{"name": "coordinator"}, {"name": "reviewer"}]

    def test_per_persona_overrides_forwarded(self):
        """Per-persona llm dict and iteration_budget are included in workload_config."""
        personas = [
            {"name": "coordinator"},
            {
                "name": "reviewer",
                "llm": {"primary_alias": "powerful", "thinking_enabled": True},
                "iteration_budget": 40,
            },
            {"name": "security-auditor", "llm": {"primary_alias": "balanced"}},
        ]
        config = DispatchConfig(flock_enabled=True, flock_default_personas=personas)
        req = self._call(config, self._make_saga(), self._make_issue())
        emitted = req.workload_config["personas"]
        assert emitted[0] == {"name": "coordinator"}
        assert emitted[1] == {
            "name": "reviewer",
            "llm": {"primary_alias": "powerful", "thinking_enabled": True},
            "iteration_budget": 40,
        }
        assert emitted[2] == {"name": "security-auditor", "llm": {"primary_alias": "balanced"}}

    def test_global_llm_config_still_present(self):
        """Global llm_config key is still included alongside per-persona personas."""
        llm = {"primary_alias": "balanced"}
        config = DispatchConfig(
            flock_enabled=True,
            flock_default_personas=[{"name": "coordinator"}],
            flock_llm_config=llm,
        )
        req = self._call(config, self._make_saga(), self._make_issue())
        assert req.workload_config["llm_config"] == llm
        assert req.workload_config["personas"] == [{"name": "coordinator"}]

    def test_info_log_emitted(self, caplog):
        """INFO log includes session name and formatted persona labels."""
        config = DispatchConfig(
            flock_enabled=True,
            flock_default_personas=[
                {"name": "coordinator"},
                {
                    "name": "reviewer",
                    "llm": {"primary_alias": "powerful", "thinking_enabled": True},
                },
                {"name": "security-auditor", "llm": {"primary_alias": "balanced"}},
            ],
        )
        saga = self._make_saga()
        issue = self._make_issue()
        with caplog.at_level(logging.INFO, logger="tyr.domain.services.dispatch_service"):
            self._call(config, saga, issue)
        assert any("coordinator(inherit)" in r.message for r in caplog.records)
        assert any("reviewer(powerful/thinking)" in r.message for r in caplog.records)
        assert any("security-auditor(balanced)" in r.message for r in caplog.records)

    def test_workflow_snapshot_forces_flock_dispatch(self):
        config = DispatchConfig(flock_enabled=False, flock_default_personas=[])
        saga = self._make_saga()
        issue = self._make_issue()
        workflow_snapshot = {
            "workflow_id": str(uuid4()),
            "name": "Review Flow",
            "version": "1.0.0",
            "graph": {
                "nodes": [
                    {
                        "id": "stage-1",
                        "kind": "stage",
                        "label": "Review",
                        "stageMembers": [{"personaId": "reviewer", "budget": 40}],
                    }
                ]
            },
        }

        svc = MagicMock()
        svc._config = config
        svc._flow_provider = None
        item = DispatchItem(saga_id=str(saga.id), issue_id="i-1", repo="org/repo")
        req = DispatchService._build_spawn_request(
            svc,
            item=item,
            saga=saga,
            issue=issue,
            effective_model="claude-sonnet-4-6",
            effective_prompt="",
            integration_ids=[],
            workflow_snapshot=workflow_snapshot,
        )

        assert req.workload_type == "ravn_flock"
        assert req.workload_config["workflow"]["name"] == "Review Flow"
        assert req.workload_config["personas"] == [{"name": "reviewer", "iteration_budget": 40}]


class _StubWorkflowRepo(WorkflowRepository):
    def __init__(self, workflows: list[WorkflowDefinition]) -> None:
        self._workflows = {workflow.id: workflow for workflow in workflows}

    async def list_workflows(self, *, owner_id: str, scope: WorkflowScope | None = None):
        return list(self._workflows.values())

    async def get_workflow(self, workflow_id):
        return self._workflows.get(workflow_id)

    async def save_workflow(self, workflow: WorkflowDefinition) -> WorkflowDefinition:
        self._workflows[workflow.id] = workflow
        return workflow

    async def delete_workflow(self, workflow_id) -> bool:
        return self._workflows.pop(workflow_id, None) is not None


class TestResolveWorkflowSnapshot:
    @pytest.mark.asyncio
    async def test_explicit_override_resolves_snapshot(self):
        workflow = WorkflowDefinition(
            id=uuid4(),
            name="Review Flow",
            description="",
            version="1.0.0",
            scope=WorkflowScope.USER,
            owner_id="owner-1",
            definition_yaml="name: Review Flow\nstages: []",
            graph={"nodes": [], "edges": []},
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        service = DispatchService(
            tracker_factory=MockTrackerFactory([]),
            volundr_factory=MockVolundrFactory(adapters=[MockVolundr()]),
            saga_repo=MockSagaRepo(),
            dispatcher_repo=MockDispatcherRepo(),
            config=DispatchConfig(default_system_prompt="Be helpful.", default_model="claude"),
            workflow_repo=_StubWorkflowRepo([workflow]),
        )
        saga = Saga(
            id=uuid4(),
            tracker_id="proj-1",
            tracker_type="linear",
            slug="alpha",
            name="Alpha",
            repos=["org/repo"],
            feature_branch="feat/alpha",
            base_branch="main",
            status=SagaStatus.ACTIVE,
            confidence=0.5,
            created_at=datetime.now(UTC),
            owner_id="owner-1",
        )
        item = DispatchItem(
            saga_id=str(saga.id),
            issue_id="i-1",
            repo="org/repo",
            workflow_id=str(workflow.id),
        )

        snapshot, error = await service._resolve_workflow_snapshot(item, saga)

        assert error is None
        assert snapshot is not None
        assert snapshot["workflow_id"] == str(workflow.id)

    @pytest.mark.asyncio
    async def test_invalid_override_returns_error(self):
        service = DispatchService(
            tracker_factory=MockTrackerFactory([]),
            volundr_factory=MockVolundrFactory(adapters=[MockVolundr()]),
            saga_repo=MockSagaRepo(),
            dispatcher_repo=MockDispatcherRepo(),
            config=DispatchConfig(default_system_prompt="Be helpful.", default_model="claude"),
            workflow_repo=_StubWorkflowRepo([]),
        )
        saga = Saga(
            id=uuid4(),
            tracker_id="proj-1",
            tracker_type="linear",
            slug="alpha",
            name="Alpha",
            repos=["org/repo"],
            feature_branch="feat/alpha",
            base_branch="main",
            status=SagaStatus.ACTIVE,
            confidence=0.5,
            created_at=datetime.now(UTC),
            owner_id="owner-1",
        )
        item = DispatchItem(
            saga_id=str(saga.id),
            issue_id="i-1",
            repo="org/repo",
            workflow_id="not-a-uuid",
        )

        snapshot, error = await service._resolve_workflow_snapshot(item, saga)

        assert snapshot is None
        assert error is not None


# ---------------------------------------------------------------------------
# NIU-644: _dispatch_template_phase with flock_flow_name
# ---------------------------------------------------------------------------


def _make_template_raid(persona: str = "reviewer", prompt: str = "review it") -> TemplateRaid:
    return TemplateRaid(
        name=f"{persona}-raid",
        description="",
        acceptance_criteria=[],
        declared_files=[],
        estimate_hours=1.0,
        prompt=prompt,
        persona=persona,
    )


def _make_template_phase(raids: list[TemplateRaid] | None = None) -> TemplatePhase:
    raids = raids or [_make_template_raid()]
    return TemplatePhase(name="review", raids=raids, fan_in="all_must_pass")


def _make_phase(saga_id, number: int = 1) -> Phase:
    phase_id = uuid4()
    return Phase(
        id=phase_id,
        saga_id=saga_id,
        tracker_id=str(phase_id),
        number=number,
        name="review",
        status=PhaseStatus.ACTIVE,
        confidence=0.0,
    )


def _make_raid(phase_id, persona: str = "reviewer") -> Raid:
    raid_id = uuid4()
    now = datetime.now(UTC)
    return Raid(
        id=raid_id,
        phase_id=phase_id,
        tracker_id=str(raid_id),
        name=f"{persona}-raid",
        description="",
        acceptance_criteria=[],
        declared_files=[],
        estimate_hours=1.0,
        status=RaidStatus.PENDING,
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


def _make_dispatch_service(
    volundr: MockVolundr,
    flow_provider=None,
    saga_repo: MockSagaRepo | None = None,
) -> DispatchService:
    return DispatchService(
        tracker_factory=MockTrackerFactory([]),
        volundr_factory=MockVolundrFactory(adapters=[volundr]),
        saga_repo=saga_repo or MockSagaRepo(),
        dispatcher_repo=MockDispatcherRepo(),
        config=DispatchConfig(
            default_system_prompt="Be helpful.",
            default_model="claude-sonnet-4-6",
        ),
        flow_provider=flow_provider,
    )


class TestDispatchTemplatePhaseFlockFlow:
    """NIU-644: _dispatch_template_phase flock_flow_name integration."""

    @pytest.mark.asyncio
    async def test_flock_flow_name_resolves_to_ravn_flock(self):
        """When flock_flow_name resolves, workload_type is ravn_flock."""
        flow = FlockFlowConfig(
            name="code-review-flow",
            personas=[FlockPersonaOverride(name="reviewer")],
        )
        provider = StubFlockFlowProvider({"code-review-flow": flow})
        volundr = MockVolundr()
        volundr.integration_ids = ["int-telegram", "int-linear"]
        repo = MockSagaRepo()
        service = _make_dispatch_service(volundr, provider, repo)

        saga = _make_saga()
        phase = _make_phase(saga.id)
        raid = _make_raid(phase.id)
        repo.sagas.append(saga)
        await repo.save_phase(phase)
        await repo.save_raid(raid)

        tpl_phase = _make_template_phase()
        await service._dispatch_template_phase(
            saga, phase, [raid], tpl_phase, "owner-1", flock_flow_name="code-review-flow"
        )

        assert len(volundr.spawned) == 1
        assert volundr.spawned[0].workload_type == "ravn_flock"
        assert "personas" in volundr.spawned[0].workload_config
        assert volundr.spawned[0].integration_ids == ["int-telegram", "int-linear"]

    @pytest.mark.asyncio
    async def test_empty_flock_flow_name_is_solo_dispatch(self):
        """Without flock_flow_name, workload_type is default."""
        volundr = MockVolundr()
        volundr.integration_ids = ["int-telegram"]
        repo = MockSagaRepo()
        service = _make_dispatch_service(volundr, saga_repo=repo)

        saga = _make_saga()
        phase = _make_phase(saga.id)
        raid = _make_raid(phase.id)
        repo.sagas.append(saga)
        await repo.save_phase(phase)
        await repo.save_raid(raid)

        tpl_phase = _make_template_phase()
        await service._dispatch_template_phase(
            saga, phase, [raid], tpl_phase, "owner-1", flock_flow_name=""
        )

        assert len(volundr.spawned) == 1
        assert volundr.spawned[0].workload_type == "default"
        assert volundr.spawned[0].workload_config == {}
        assert volundr.spawned[0].integration_ids == ["int-telegram"]
