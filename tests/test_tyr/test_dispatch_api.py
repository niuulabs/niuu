"""Tests for dispatch REST API endpoints.

Tests the dispatch queue, approve, and config endpoints by
overriding FastAPI dependencies with mock implementations.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from niuu.domain.models import IntegrationConnection, IntegrationType
from tyr.api.dispatch import (
    create_dispatch_router,
    resolve_dispatch_service,
    resolve_dispatcher_repo,
    resolve_saga_repo,
    resolve_volundr,
    resolve_volundr_factory,
)
from tyr.api.tracker import resolve_trackers
from tyr.config import AIModelConfig, AuthConfig, Settings
from tyr.config import DispatchConfig as DispatchCfg
from tyr.domain.models import (
    DispatcherState,
    Saga,
    SagaStatus,
    TrackerIssue,
    TrackerMilestone,
    TrackerProject,
)
from tyr.domain.services.dispatch_service import (
    DispatchConfig as ServiceDispatchConfig,
)
from tyr.domain.services.dispatch_service import (
    DispatchResult,
    DispatchService,
    build_prompt,
    is_ready,
    resolve_target_adapter,
)
from tyr.ports.dispatcher_repository import DispatcherRepository
from tyr.ports.volundr import SpawnRequest, VolundrPort, VolundrSession

from .test_tracker_api import MockSagaRepo, MockTracker

# -------------------------------------------------------------------
# Mock VolundrPort
# -------------------------------------------------------------------


class MockVolundr(VolundrPort):
    """In-memory mock for Volundr session management."""

    def __init__(self) -> None:
        self.sessions: list[VolundrSession] = []
        self.spawned: list[SpawnRequest] = []
        self.last_auth_token: str | None = None
        self.fail_spawn: bool = False
        self.integration_ids: list[str] = []

    async def spawn_session(
        self,
        request: SpawnRequest,
        *,
        auth_token: str | None = None,
    ) -> VolundrSession:
        self.last_auth_token = auth_token
        if self.fail_spawn:
            raise RuntimeError("spawn failed")
        self.spawned.append(request)
        session = VolundrSession(
            id=f"ses-{len(self.spawned)}",
            name=request.name,
            status="running",
            tracker_issue_id=request.tracker_issue_id,
        )
        self.sessions.append(session)
        return session

    async def get_session(
        self,
        session_id: str,
        *,
        auth_token: str | None = None,
    ) -> VolundrSession | None:
        return next(
            (s for s in self.sessions if s.id == session_id),
            None,
        )

    async def list_sessions(
        self,
        *,
        auth_token: str | None = None,
    ) -> list[VolundrSession]:
        self.last_auth_token = auth_token
        return list(self.sessions)

    async def get_pr_status(self, session_id: str):  # noqa: ANN201
        from tyr.domain.models import PRStatus

        return PRStatus(
            pr_id="pr-1",
            url="https://github.com/org/repo/pull/1",
            state="open",
            mergeable=True,
            ci_passed=True,
        )

    async def get_chronicle_summary(self, session_id: str) -> str:
        return "summary"

    async def send_message(
        self,
        session_id: str,
        message: str,
        *,
        auth_token: str | None = None,
    ) -> None:
        pass

    async def stop_session(self, session_id, *, auth_token=None):
        pass

    async def list_integration_ids(
        self,
        *,
        auth_token: str | None = None,
    ) -> list[str]:
        self.last_auth_token = auth_token
        return list(self.integration_ids)

    async def list_repos(self, *, auth_token: str | None = None) -> list[dict]:
        return []

    async def get_conversation(self, session_id: str) -> dict:
        return {"turns": []}

    async def get_last_assistant_message(self, session_id: str) -> str:
        return ""

    async def subscribe_activity(self):
        return
        yield  # type: ignore[misc]  # pragma: no cover


def _auth_headers(user_id: str = "dev-user") -> dict[str, str]:
    return {"x-auth-user-id": user_id}


class MockVolundrFactory:
    """Stub VolundrFactory that returns a configurable list of adapters."""

    def __init__(self, adapters: list[VolundrPort] | None = None) -> None:
        self._adapters = adapters or []

    async def for_owner(self, owner_id: str) -> list[VolundrPort]:
        if self._adapters:
            return self._adapters
        return [MockVolundr()]

    async def primary_for_owner(self, owner_id: str) -> VolundrPort | None:
        if self._adapters:
            return self._adapters[0]
        return None


class MockTrackerFactory:
    """Stub TrackerFactory that returns a configurable list of adapters."""

    def __init__(self, adapters: list | None = None) -> None:
        self._adapters = adapters or []

    async def for_owner(self, owner_id: str) -> list:
        return list(self._adapters)


class MockDispatcherRepo(DispatcherRepository):
    """In-memory mock for DispatcherRepository."""

    async def get_or_create(self, owner_id: str) -> DispatcherState:
        return DispatcherState(
            id=uuid4(),
            owner_id=owner_id,
            running=False,
            threshold=0.5,
            max_concurrent_raids=3,
            auto_continue=False,
            updated_at=datetime.now(UTC),
        )

    async def update(self, owner_id: str, **fields: object) -> DispatcherState:
        return await self.get_or_create(owner_id)

    async def list_active_owner_ids(self) -> list[str]:
        return []


class DispatchAliasTracker(MockTracker):
    def __init__(self) -> None:
        super().__init__()
        now = datetime.now(UTC)
        self._saga = Saga(
            id=uuid4(),
            tracker_id="proj-1",
            tracker_type="mock",
            slug="alpha",
            name="Alpha",
            repos=["org/repo"],
            feature_branch="feat/alpha",
            status=SagaStatus.ACTIVE,
            confidence=0.0,
            created_at=now,
            base_branch="main",
            owner_id="dev-user",
        )
        self.issues = {
            "proj-1": [
                TrackerIssue(
                    id="i-1",
                    identifier="i-1",
                    title="Dispatch me",
                    description="",
                    status="Todo",
                    priority=1,
                    priority_label="Urgent",
                    url="https://linear.app/i-1",
                )
            ]
        }

    async def get_raid(self, tracker_id: str):
        from tyr.domain.models import Raid, RaidStatus

        return Raid(
            id=uuid4(),
            phase_id=uuid4(),
            tracker_id=tracker_id,
            name="Dispatch me",
            description="",
            acceptance_criteria=[],
            declared_files=[],
            estimate_hours=None,
            status=RaidStatus.PENDING,
            confidence=0.0,
            session_id=None,
            branch=None,
            chronicle_summary=None,
            pr_url=None,
            pr_id=None,
            retry_count=0,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            identifier=tracker_id,
        )

    async def get_saga_for_raid(self, tracker_id: str):
        return self._saga


# -------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------


def _make_settings(**overrides) -> Settings:
    """Build a Settings with dispatch defaults and anonymous dev enabled."""
    overrides.setdefault("auth", AuthConfig(allow_anonymous_dev=True))
    return Settings(
        dispatch=DispatchCfg(
            default_system_prompt="Be helpful.",
            default_model="claude-sonnet-4-6",
        ),
        ai_models=[
            AIModelConfig(id="claude-sonnet-4-6", name="Sonnet"),
            AIModelConfig(id="claude-opus-4-6", name="Opus"),
        ],
        **overrides,
    )


@pytest.fixture
def mock_tracker() -> MockTracker:
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


@pytest.fixture
def mock_volundr() -> MockVolundr:
    return MockVolundr()


@pytest.fixture
def saga_repo() -> MockSagaRepo:
    repo = MockSagaRepo()
    repo.sagas.append(
        Saga(
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
    )
    return repo


@pytest.fixture
def mock_factory(mock_volundr: MockVolundr) -> MockVolundrFactory:
    return MockVolundrFactory(adapters=[mock_volundr])


@pytest.fixture
def mock_dispatcher_repo() -> MockDispatcherRepo:
    return MockDispatcherRepo()


def _make_dispatch_service(
    tracker: MockTracker,
    volundr_factory: MockVolundrFactory,
    saga_repo: MockSagaRepo,
    dispatcher_repo: MockDispatcherRepo,
    settings: Settings | None = None,
) -> DispatchService:
    settings = settings or _make_settings()
    return DispatchService(
        tracker_factory=MockTrackerFactory([tracker]),
        volundr_factory=volundr_factory,
        saga_repo=saga_repo,
        dispatcher_repo=dispatcher_repo,
        config=ServiceDispatchConfig(
            default_system_prompt=settings.dispatch.default_system_prompt,
            default_model=settings.dispatch.default_model,
            dispatch_prompt_template=settings.dispatch.dispatch_prompt_template,
        ),
    )


def _make_test_app(
    tracker: MockTracker,
    saga_repo: MockSagaRepo,
    volundr: MockVolundr,
    volundr_factory: MockVolundrFactory,
    dispatcher_repo: MockDispatcherRepo,
    settings: Settings | None = None,
) -> FastAPI:
    settings = settings or _make_settings()
    app = FastAPI()
    app.include_router(create_dispatch_router())
    app.state.settings = settings
    svc = _make_dispatch_service(tracker, volundr_factory, saga_repo, dispatcher_repo, settings)
    app.dependency_overrides[resolve_trackers] = lambda: [tracker]
    app.dependency_overrides[resolve_saga_repo] = lambda: saga_repo
    app.dependency_overrides[resolve_volundr] = lambda: volundr
    app.dependency_overrides[resolve_volundr_factory] = lambda: volundr_factory
    app.dependency_overrides[resolve_dispatcher_repo] = lambda: dispatcher_repo
    app.dependency_overrides[resolve_dispatch_service] = lambda: svc
    return app


@pytest.fixture
def client(
    mock_tracker: MockTracker,
    mock_volundr: MockVolundr,
    saga_repo: MockSagaRepo,
    mock_factory: MockVolundrFactory,
    mock_dispatcher_repo: MockDispatcherRepo,
) -> TestClient:
    app = _make_test_app(mock_tracker, saga_repo, mock_volundr, mock_factory, mock_dispatcher_repo)
    return TestClient(app)


# -------------------------------------------------------------------
# Unit tests: helper functions
# -------------------------------------------------------------------


class TestIsReady:
    def test_ready_todo(self):
        issue = TrackerIssue(
            id="1",
            identifier="X-1",
            title="t",
            description="",
            status="Todo",
        )
        assert is_ready(issue, set(), set()) is True

    def test_ready_backlog(self):
        issue = TrackerIssue(
            id="1",
            identifier="X-1",
            title="t",
            description="",
            status="Backlog",
        )
        assert is_ready(issue, set(), set()) is True

    def test_ready_triage(self):
        issue = TrackerIssue(
            id="1",
            identifier="X-1",
            title="t",
            description="",
            status="Triage",
        )
        assert is_ready(issue, set(), set()) is True

    def test_not_ready_in_progress(self):
        issue = TrackerIssue(
            id="1",
            identifier="X-1",
            title="t",
            description="",
            status="In Progress",
        )
        assert is_ready(issue, set(), set()) is False

    def test_not_ready_active_session(self):
        issue = TrackerIssue(
            id="1",
            identifier="X-1",
            title="t",
            description="",
            status="Todo",
        )
        assert is_ready(issue, {"X-1"}, set()) is False

    def test_not_ready_blocked(self):
        issue = TrackerIssue(
            id="1",
            identifier="X-1",
            title="t",
            description="",
            status="Todo",
        )
        assert is_ready(issue, set(), {"X-1"}) is False


class TestBuildPrompt:
    def test_contains_identifier_and_title(self):
        issue = TrackerIssue(
            id="1",
            identifier="X-1",
            title="Setup CI",
            description="Configure pipelines",
            status="Todo",
            url="https://example.com/X-1",
        )
        prompt = build_prompt(issue, "org/repo", "feat/alpha")
        assert "X-1" in prompt
        assert "Setup CI" in prompt
        assert "Configure pipelines" in prompt
        assert "org/repo" in prompt
        assert "feat/alpha" in prompt

    def test_empty_description(self):
        issue = TrackerIssue(
            id="1",
            identifier="X-2",
            title="No desc",
            description="",
            status="Todo",
        )
        prompt = build_prompt(issue, "org/repo", "main")
        assert "X-2" in prompt
        assert "No desc" in prompt

    def test_fallback_contains_essentials(self):
        issue = TrackerIssue(
            id="1",
            identifier="X-3",
            title="Task",
            description="Do stuff",
            status="Todo",
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
        assert "Implement OAuth" in prompt
        assert "niu-42" in prompt  # raid_branch is lowercased identifier
        assert "feat/saga" in prompt


# -------------------------------------------------------------------
# API endpoint tests
# -------------------------------------------------------------------


class TestGetConfig:
    def test_returns_defaults(self, client: TestClient):
        resp = client.get("/api/v1/tyr/dispatch/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["default_system_prompt"] == "Be helpful."
        assert data["default_model"] == "claude-sonnet-4-6"
        assert len(data["models"]) == 2
        assert data["models"][0]["id"] == "claude-sonnet-4-6"


class TestGetQueue:
    def test_returns_ready_issues(self, client: TestClient):
        resp = client.get("/api/v1/tyr/dispatch/queue")
        assert resp.status_code == 200
        data = resp.json()
        # i-1 (Todo) and i-3 (Backlog) should be ready
        # i-2 (In Progress) should not
        ids = [item["identifier"] for item in data]
        assert "ALPHA-1" in ids
        assert "ALPHA-3" in ids
        assert "ALPHA-2" not in ids

    def test_returns_unstarted_linear_issues(
        self,
        mock_tracker: MockTracker,
        saga_repo: MockSagaRepo,
        mock_volundr: MockVolundr,
    ):
        mock_tracker.issues["proj-1"].append(
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
        factory = MockVolundrFactory(adapters=[mock_volundr])
        app = _make_test_app(mock_tracker, saga_repo, mock_volundr, factory, MockDispatcherRepo())
        client = TestClient(app)

        resp = client.get("/api/v1/tyr/dispatch/queue")
        assert resp.status_code == 200
        ids = [item["identifier"] for item in resp.json()]
        assert "ALPHA-4" in ids

    def test_queue_sorted_by_priority(self, client: TestClient):
        resp = client.get("/api/v1/tyr/dispatch/queue")
        data = resp.json()
        priorities = [item["priority"] for item in data]
        assert priorities == sorted(priorities)

    def test_queue_excludes_active_sessions(
        self,
        mock_tracker: MockTracker,
        saga_repo: MockSagaRepo,
    ):
        volundr = MockVolundr()
        volundr.sessions = [
            VolundrSession(
                id="ses-1",
                name="alpha-1",
                status="running",
                tracker_issue_id="ALPHA-1",
            ),
        ]
        factory = MockVolundrFactory(adapters=[volundr])
        app = _make_test_app(mock_tracker, saga_repo, volundr, factory, MockDispatcherRepo())
        client = TestClient(app)

        resp = client.get("/api/v1/tyr/dispatch/queue")
        data = resp.json()
        ids = [item["identifier"] for item in data]
        assert "ALPHA-1" not in ids
        assert "ALPHA-3" in ids

    def test_queue_excludes_blocked(
        self,
        mock_tracker: MockTracker,
        saga_repo: MockSagaRepo,
        mock_volundr: MockVolundr,
    ):
        mock_tracker._blocked = {"ALPHA-1"}
        factory = MockVolundrFactory(adapters=[mock_volundr])
        app = _make_test_app(mock_tracker, saga_repo, mock_volundr, factory, MockDispatcherRepo())
        client = TestClient(app)

        resp = client.get("/api/v1/tyr/dispatch/queue")
        data = resp.json()
        ids = [item["identifier"] for item in data]
        assert "ALPHA-1" not in ids
        assert "ALPHA-3" in ids

    def test_queue_empty_when_no_sagas(
        self,
        mock_tracker: MockTracker,
        mock_volundr: MockVolundr,
    ):
        factory = MockVolundrFactory(adapters=[mock_volundr])
        app = _make_test_app(
            mock_tracker, MockSagaRepo(), mock_volundr, factory, MockDispatcherRepo()
        )
        client = TestClient(app)

        resp = client.get("/api/v1/tyr/dispatch/queue")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_queue_forwards_auth_token(
        self,
        mock_tracker: MockTracker,
        saga_repo: MockSagaRepo,
    ):
        volundr = MockVolundr()
        factory = MockVolundrFactory(adapters=[volundr])
        app = _make_test_app(mock_tracker, saga_repo, volundr, factory, MockDispatcherRepo())
        client = TestClient(app)

        resp = client.get(
            "/api/v1/tyr/dispatch/queue",
            headers={"Authorization": "Bearer test-tok"},
        )
        assert resp.status_code == 200
        assert volundr.last_auth_token == "test-tok"

    def test_queue_item_has_saga_fields(self, client: TestClient, saga_repo: MockSagaRepo):
        resp = client.get("/api/v1/tyr/dispatch/queue")
        data = resp.json()
        item = next(i for i in data if i["identifier"] == "ALPHA-1")
        assert item["saga_name"] == "Alpha"
        assert item["saga_slug"] == "alpha"
        assert item["repos"] == ["org/repo-a", "org/repo-b"]
        assert item["feature_branch"] == "feat/alpha"
        assert item["phase_name"] == "Phase 1"

    def test_queue_handles_tracker_error(
        self,
        saga_repo: MockSagaRepo,
        mock_volundr: MockVolundr,
    ):
        """Queue endpoint gracefully handles tracker failures."""

        class FailingTracker(MockTracker):
            async def get_project_full(self, project_id):
                raise ConnectionError("down")

        tracker = FailingTracker()
        factory = MockVolundrFactory(adapters=[mock_volundr])
        app = _make_test_app(tracker, saga_repo, mock_volundr, factory, MockDispatcherRepo())
        client = TestClient(app)

        resp = client.get("/api/v1/tyr/dispatch/queue")
        assert resp.status_code == 200
        assert resp.json() == []


class TestApproveDispatch:
    def test_spawns_sessions(
        self,
        client: TestClient,
        saga_repo: MockSagaRepo,
        mock_volundr: MockVolundr,
    ):
        saga_id = str(saga_repo.sagas[0].id)
        resp = client.post(
            "/api/v1/tyr/dispatch/approve",
            json={
                "items": [
                    {
                        "saga_id": saga_id,
                        "issue_id": "i-1",
                        "repo": "org/repo-a",
                    },
                ],
                "model": "claude-opus-4-6",
                "system_prompt": "Custom prompt.",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["status"] == "spawned"
        assert data[0]["session_id"] == "ses-1"
        assert data[0]["session_name"] == "alpha-1"

        # Verify spawn request details
        assert len(mock_volundr.spawned) == 1
        req = mock_volundr.spawned[0]
        assert req.model == "claude-opus-4-6"
        assert req.system_prompt == "Custom prompt."
        assert req.repo == "org/repo-a"
        assert req.branch == "feat/alpha"
        assert req.tracker_issue_id == "ALPHA-1"

    def test_forwards_session_definition_override(
        self,
        client: TestClient,
        saga_repo: MockSagaRepo,
        mock_volundr: MockVolundr,
    ):
        saga_id = str(saga_repo.sagas[0].id)
        resp = client.post(
            "/api/v1/tyr/dispatch/approve",
            json={
                "items": [
                    {
                        "saga_id": saga_id,
                        "issue_id": "i-1",
                        "repo": "org/repo-a",
                    },
                ],
                "session_definition": "skuldCodex",
            },
        )

        assert resp.status_code == 200
        assert mock_volundr.spawned[0].definition == "skuldCodex"

    def test_uses_server_defaults(
        self,
        client: TestClient,
        saga_repo: MockSagaRepo,
        mock_volundr: MockVolundr,
    ):
        saga_id = str(saga_repo.sagas[0].id)
        resp = client.post(
            "/api/v1/tyr/dispatch/approve",
            json={
                "items": [
                    {
                        "saga_id": saga_id,
                        "issue_id": "i-1",
                        "repo": "org/repo-a",
                    },
                ],
            },
        )
        assert resp.status_code == 200
        req = mock_volundr.spawned[0]
        assert req.model == "claude-sonnet-4-6"
        assert req.system_prompt == "Be helpful."

    def test_skips_unknown_saga(
        self,
        client: TestClient,
        mock_volundr: MockVolundr,
    ):
        resp = client.post(
            "/api/v1/tyr/dispatch/approve",
            json={
                "items": [
                    {
                        "saga_id": str(uuid4()),
                        "issue_id": "i-1",
                        "repo": "org/repo-a",
                    },
                ],
            },
        )
        assert resp.status_code == 200
        assert resp.json() == []
        assert len(mock_volundr.spawned) == 0

    def test_skips_unknown_issue(
        self,
        client: TestClient,
        saga_repo: MockSagaRepo,
        mock_volundr: MockVolundr,
    ):
        saga_id = str(saga_repo.sagas[0].id)
        resp = client.post(
            "/api/v1/tyr/dispatch/approve",
            json={
                "items": [
                    {
                        "saga_id": saga_id,
                        "issue_id": "nonexistent",
                        "repo": "org/repo-a",
                    },
                ],
            },
        )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_spawn_failure_returns_failed(
        self,
        saga_repo: MockSagaRepo,
        mock_tracker: MockTracker,
    ):
        volundr = MockVolundr()
        volundr.fail_spawn = True
        factory = MockVolundrFactory(adapters=[volundr])
        app = _make_test_app(mock_tracker, saga_repo, volundr, factory, MockDispatcherRepo())
        client = TestClient(app)

        saga_id = str(saga_repo.sagas[0].id)
        resp = client.post(
            "/api/v1/tyr/dispatch/approve",
            json={
                "items": [
                    {
                        "saga_id": saga_id,
                        "issue_id": "i-1",
                        "repo": "org/repo-a",
                    },
                ],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["status"] == "failed"
        assert data[0]["session_id"] == ""

    def test_approve_forwards_auth_token(
        self,
        saga_repo: MockSagaRepo,
        mock_tracker: MockTracker,
    ):
        volundr = MockVolundr()
        factory = MockVolundrFactory(adapters=[volundr])
        app = _make_test_app(mock_tracker, saga_repo, volundr, factory, MockDispatcherRepo())
        client = TestClient(app)

        saga_id = str(saga_repo.sagas[0].id)
        client.post(
            "/api/v1/tyr/dispatch/approve",
            json={
                "items": [
                    {
                        "saga_id": saga_id,
                        "issue_id": "i-1",
                        "repo": "org/repo-a",
                    },
                ],
            },
            headers={"Authorization": "Bearer my-token"},
        )
        assert volundr.last_auth_token == "my-token"

    def test_multiple_items(
        self,
        client: TestClient,
        saga_repo: MockSagaRepo,
        mock_volundr: MockVolundr,
    ):
        saga_id = str(saga_repo.sagas[0].id)
        resp = client.post(
            "/api/v1/tyr/dispatch/approve",
            json={
                "items": [
                    {
                        "saga_id": saga_id,
                        "issue_id": "i-1",
                        "repo": "org/repo-a",
                    },
                    {
                        "saga_id": saga_id,
                        "issue_id": "i-3",
                        "repo": "org/repo-b",
                    },
                ],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert all(d["status"] == "spawned" for d in data)
        assert len(mock_volundr.spawned) == 2

    def test_connection_id_on_request(
        self,
        client: TestClient,
        saga_repo: MockSagaRepo,
        mock_volundr: MockVolundr,
    ):
        """connection_id at request level is accepted and doesn't break dispatch."""
        saga_id = str(saga_repo.sagas[0].id)
        resp = client.post(
            "/api/v1/tyr/dispatch/approve",
            json={
                "items": [
                    {"saga_id": saga_id, "issue_id": "i-1", "repo": "org/repo-a"},
                ],
                "connection_id": "some-cluster",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["status"] == "spawned"

    def test_connection_id_on_item(
        self,
        client: TestClient,
        saga_repo: MockSagaRepo,
        mock_volundr: MockVolundr,
    ):
        """Per-item connection_id is accepted and doesn't break dispatch."""
        saga_id = str(saga_repo.sagas[0].id)
        resp = client.post(
            "/api/v1/tyr/dispatch/approve",
            json={
                "items": [
                    {
                        "saga_id": saga_id,
                        "issue_id": "i-1",
                        "repo": "org/repo-a",
                        "connection_id": "cluster-a",
                    },
                ],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["status"] == "spawned"


# -------------------------------------------------------------------
# Unit tests: _resolve_target_adapter
# -------------------------------------------------------------------


class TestResolveTargetAdapter:
    def test_no_connection_id_returns_fallback(self):
        fallback = MockVolundr()
        result = resolve_target_adapter(None, {}, fallback)
        assert result is fallback

    def test_empty_connection_id_returns_fallback(self):
        fallback = MockVolundr()
        result = resolve_target_adapter("", {}, fallback)
        assert result is fallback

    def test_matching_connection_id_returns_adapter(self):
        fallback = MockVolundr()
        target = MockVolundr()
        adapters = {"cluster-a": target}
        result = resolve_target_adapter("cluster-a", adapters, fallback)
        assert result is target

    def test_unknown_connection_id_returns_fallback(self):
        fallback = MockVolundr()
        target = MockVolundr()
        adapters = {"cluster-a": target}
        result = resolve_target_adapter("cluster-b", adapters, fallback)
        assert result is fallback


# -------------------------------------------------------------------
# API endpoint tests: clusters
# -------------------------------------------------------------------


class TestListClusters:
    def test_returns_empty_when_no_integration_repo(self):
        volundr = MockVolundr()
        factory = MockVolundrFactory(adapters=[volundr])
        app = _make_test_app(MockTracker(), MockSagaRepo(), volundr, factory, MockDispatcherRepo())
        client = TestClient(app)

        resp = client.get("/api/v1/tyr/dispatch/clusters")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_clusters_from_code_forge_connections(self):
        from tests.test_tyr.conftest import StubIntegrationRepo

        now = datetime.now(UTC)
        connections = [
            IntegrationConnection(
                id="conn-1",
                owner_id="dev-user",
                integration_type=IntegrationType.CODE_FORGE,
                adapter="tyr.adapters.volundr_http.VolundrHTTPAdapter",
                credential_name="pat-a",
                config={"url": "http://cluster-a:8000", "name": "alpha"},
                enabled=True,
                created_at=now,
                updated_at=now,
            ),
            IntegrationConnection(
                id="conn-2",
                owner_id="dev-user",
                integration_type=IntegrationType.CODE_FORGE,
                adapter="tyr.adapters.volundr_http.VolundrHTTPAdapter",
                credential_name="pat-b",
                config={"url": "http://cluster-b:8000", "name": "beta"},
                enabled=False,
                created_at=now,
                updated_at=now,
            ),
        ]
        integration_repo = StubIntegrationRepo(connections=connections)

        volundr = MockVolundr()
        factory = MockVolundrFactory(adapters=[volundr])
        app = _make_test_app(MockTracker(), MockSagaRepo(), volundr, factory, MockDispatcherRepo())
        app.state.integration_repo = integration_repo
        client = TestClient(app)

        resp = client.get("/api/v1/tyr/dispatch/clusters")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["connection_id"] == "conn-1"
        assert data[0]["name"] == "alpha"
        assert data[0]["url"] == "http://cluster-a:8000"
        assert data[0]["enabled"] is True
        assert data[1]["connection_id"] == "conn-2"
        assert data[1]["name"] == "beta"
        assert data[1]["enabled"] is False

    def test_cluster_name_falls_back_to_id(self):
        from tests.test_tyr.conftest import StubIntegrationRepo

        now = datetime.now(UTC)
        connections = [
            IntegrationConnection(
                id="conn-x",
                owner_id="dev-user",
                integration_type=IntegrationType.CODE_FORGE,
                adapter="tyr.adapters.volundr_http.VolundrHTTPAdapter",
                credential_name="pat-x",
                config={"url": "http://cluster-x:8000"},
                enabled=True,
                created_at=now,
                updated_at=now,
            ),
        ]
        integration_repo = StubIntegrationRepo(connections=connections)

        volundr = MockVolundr()
        factory = MockVolundrFactory(adapters=[volundr])
        app = _make_test_app(MockTracker(), MockSagaRepo(), volundr, factory, MockDispatcherRepo())
        app.state.integration_repo = integration_repo
        client = TestClient(app)

        resp = client.get("/api/v1/tyr/dispatch/clusters")
        data = resp.json()
        assert data[0]["name"] == "conn-x"


class TestDispatchAliases:
    def _client(self, tracker: DispatchAliasTracker, service: AsyncMock) -> TestClient:
        app = FastAPI()
        app.include_router(create_dispatch_router())
        app.dependency_overrides[resolve_trackers] = lambda: [tracker]
        app.dependency_overrides[resolve_dispatch_service] = lambda: service
        app.dependency_overrides[resolve_dispatcher_repo] = lambda: MockDispatcherRepo()
        app.state.settings = _make_settings()
        return TestClient(app)

    def test_dispatch_single_raid_alias(self) -> None:
        tracker = DispatchAliasTracker()
        service = AsyncMock()
        service.dispatch_issues.return_value = [
            DispatchResult(
                issue_id="i-1",
                session_id="sess-1",
                session_name="dispatch-me",
                status="running",
            )
        ]
        client = self._client(tracker, service)

        response = client.post("/api/v1/tyr/dispatch/i-1", headers=_auth_headers())

        assert response.status_code == 202
        dispatched_items = service.dispatch_issues.await_args.kwargs["items"]
        assert dispatched_items[0].issue_id == "i-1"
        assert dispatched_items[0].repo == "org/repo"

    def test_dispatch_batch_alias_reports_dispatches_and_failures(self) -> None:
        tracker = DispatchAliasTracker()
        service = AsyncMock()
        service.dispatch_issues.return_value = [
            DispatchResult(
                issue_id="i-1",
                session_id="sess-1",
                session_name="dispatch-me",
                status="running",
            )
        ]
        client = self._client(tracker, service)

        response = client.post(
            "/api/v1/tyr/dispatch/batch",
            json={"raid_ids": ["i-1", "missing"]},
            headers=_auth_headers(),
        )

        assert response.status_code == 200
        assert response.json() == {
            "dispatched": ["i-1"],
            "failed": [{"raidId": "missing", "reason": "raid issue not found in tracker"}],
        }
