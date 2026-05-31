"""Tests for Ting session compatibility endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ting.api.runs import resolve_git, resolve_volundr
from ting.api.sessions import create_sessions_router
from ting.api.tracker import resolve_trackers
from ting.config import AuthConfig, ReviewConfig, Settings
from ting.domain.models import Phase, PhaseStatus, Run, RunStatus, Saga, SagaStatus
from ting.ports.tracker import TrackerPort
from ting.ports.volundr import VolundrPort, VolundrSession


class MockVolundr(VolundrPort):
    def __init__(
        self,
        *,
        name: str = "Mac mini",
        sessions: dict[str, VolundrSession] | None = None,
    ) -> None:
        self._name = name
        self.sessions = (
            sessions
            if sessions is not None
            else {
                "sess-1": VolundrSession(
                    id="sess-1",
                    name="Implement auth refresh",
                    status="running",
                    tracker_issue_id="RUN-1",
                    branch="feat/auth-refresh",
                    cluster_name=name,
                )
            }
        )

    @property
    def name(self) -> str:
        return self._name

    async def spawn_session(self, request, *, auth_token=None):  # noqa: ANN001, ANN201
        raise NotImplementedError

    async def get_session(
        self, session_id: str, *, auth_token: str | None = None
    ) -> VolundrSession | None:
        return self.sessions.get(session_id)

    async def list_sessions(self, *, auth_token: str | None = None) -> list[VolundrSession]:
        return list(self.sessions.values())

    async def get_pr_status(self, session_id: str):  # noqa: ANN201
        return _build_pr_status()

    async def get_chronicle_summary(self, session_id: str) -> str:
        return "line 1\nline 2"

    async def send_message(
        self, session_id: str, message: str, *, auth_token: str | None = None
    ) -> None:
        return None

    async def stop_session(self, session_id: str, *, auth_token: str | None = None) -> None:
        return None

    async def list_integration_ids(self, *, auth_token: str | None = None) -> list[str]:
        return []

    async def list_repos(self, *, auth_token: str | None = None) -> list[dict]:
        return []

    async def get_last_assistant_message(self, session_id: str) -> str:
        return ""

    async def get_conversation(self, session_id: str) -> dict:
        return {}

    async def subscribe_activity(self):
        return
        yield  # type: ignore[misc]


class MockVolundrFactory:
    def __init__(self, adapters: list[MockVolundr]) -> None:
        self._adapters = adapters

    async def for_owner(self, owner_id: str) -> list[VolundrPort]:
        del owner_id
        return self._adapters

    async def primary_for_owner(self, owner_id: str) -> VolundrPort | None:
        del owner_id
        return self._adapters[0] if self._adapters else None


class MockTracker(TrackerPort):
    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.run = Run(
            id=uuid4(),
            phase_id=uuid4(),
            tracker_id="RUN-1",
            name="Implement auth refresh",
            description="",
            acceptance_criteria=[],
            declared_files=[],
            estimate_hours=None,
            status=RunStatus.REVIEW,
            confidence=0.82,
            session_id="sess-1",
            branch="feat/auth-refresh",
            chronicle_summary=None,
            pr_url=None,
            pr_id=None,
            retry_count=0,
            created_at=now,
            updated_at=now,
        )
        self.saga = Saga(
            id=uuid4(),
            tracker_id="PROJ-1",
            tracker_type="mock",
            slug="auth-rewrite",
            name="Auth Rewrite",
            repos=["org/repo"],
            feature_branch="feat/auth-rewrite",
            status=SagaStatus.ACTIVE,
            confidence=0.0,
            created_at=now,
            base_branch="main",
            owner_id="dev-user",
        )
        self.closed: list[str] = []
        self.updated_states: list[tuple[str, RunStatus]] = []

    async def create_saga(self, saga: Saga, *, description: str = "") -> str:
        return ""

    async def create_phase(self, phase: Phase, *, project_id: str = "") -> str:
        return ""

    async def create_run(self, run: Run, *, project_id: str = "", milestone_id: str = "") -> str:
        return ""

    async def update_run_state(self, run_id: str, state: RunStatus) -> None:
        self.updated_states.append((run_id, state))

    async def close_run(self, run_id: str) -> None:
        self.closed.append(run_id)

    async def get_saga(self, saga_id: str) -> Saga:
        return self.saga

    async def get_phase(self, tracker_id: str) -> Phase:
        return Phase(
            id=uuid4(),
            saga_id=self.saga.id,
            tracker_id=tracker_id,
            number=1,
            name="Phase 1",
            status=PhaseStatus.ACTIVE,
            confidence=0.0,
        )

    async def get_run(self, tracker_id: str) -> Run:
        return self.run

    async def list_pending_runs(self, phase_id: str) -> list[Run]:
        return []

    async def list_projects(self) -> list:
        return []

    async def get_project(self, project_id: str):  # noqa: ANN201
        raise NotImplementedError

    async def list_milestones(self, project_id: str) -> list:
        return []

    async def list_issues(self, project_id: str, milestone_id: str | None = None) -> list:
        return []

    async def update_run_progress(self, tracker_id: str, **kwargs: object) -> Run:  # noqa: ANN003
        return self.run

    async def get_run_progress_for_saga(self, saga_tracker_id: str) -> list[Run]:
        return [self.run]

    async def get_run_by_session(self, session_id: str) -> Run | None:
        return self.run if session_id == "sess-1" else None

    async def list_runs_by_status(self, status: RunStatus) -> list[Run]:
        return [self.run] if status is self.run.status else []

    async def get_run_by_id(self, run_id: UUID) -> Run | None:
        return self.run if run_id == self.run.id else None

    async def add_confidence_event(self, tracker_id: str, event: object) -> None:  # noqa: ANN001
        return None

    async def get_confidence_events(self, tracker_id: str) -> list:
        return []

    async def all_runs_merged(self, phase_tracker_id: str) -> bool:
        return False

    async def list_phases_for_saga(self, saga_tracker_id: str) -> list[Phase]:
        return []

    async def update_phase_status(self, phase_tracker_id: str, status: PhaseStatus) -> Phase | None:
        return None

    async def get_saga_for_run(self, tracker_id: str) -> Saga | None:
        return self.saga

    async def get_phase_for_run(self, tracker_id: str) -> Phase | None:
        return None

    async def get_owner_for_run(self, tracker_id: str) -> str | None:
        return "dev-user"

    async def save_session_message(self, message: object) -> None:  # noqa: ANN001
        return None

    async def get_session_messages(self, tracker_id: str) -> list:
        return []


def _client(
    tracker: MockTracker | None = None,
    volundrs: list[MockVolundr] | None = None,
) -> tuple[TestClient, MockTracker]:
    resolved_tracker = tracker or MockTracker()
    resolved_volundrs = volundrs or [MockVolundr()]
    app = FastAPI()
    app.include_router(create_sessions_router())
    app.dependency_overrides[resolve_trackers] = lambda: [resolved_tracker]
    app.dependency_overrides[resolve_volundr] = lambda: resolved_volundrs[0]
    app.dependency_overrides[resolve_git] = AsyncMock
    app.state.settings = Settings(
        auth=AuthConfig(allow_anonymous_dev=True),
        review=ReviewConfig(),
    )
    app.state.event_bus = None
    app.state.volundr_factory = MockVolundrFactory(resolved_volundrs)
    return TestClient(app), resolved_tracker


def _auth_headers(user_id: str = "dev-user") -> dict[str, str]:
    return {"x-auth-user-id": user_id}


class TestSessionsAPI:
    def test_lists_sessions_with_context(self) -> None:
        client, _tracker = _client()

        response = client.get("/api/v1/ting/sessions", headers=_auth_headers())

        assert response.status_code == 200
        assert response.json() == [
            {
                "session_id": "sess-1",
                "status": "awaiting_approval",
                "chronicle_lines": ["line 1", "line 2"],
                "branch": "feat/auth-refresh",
                "confidence": 82.0,
                "run_name": "Implement auth refresh",
                "saga_name": "Auth Rewrite",
                "cluster_name": "Mac mini",
            }
        ]

    def test_lists_sessions_across_multiple_clusters(self) -> None:
        client, _tracker = _client(
            volundrs=[
                MockVolundr(name="Mac mini"),
                MockVolundr(
                    name="MacBook Pro",
                    sessions={
                        "sess-2": VolundrSession(
                            id="sess-2",
                            name="Implement dispatch target picker",
                            status="running",
                            tracker_issue_id=None,
                            branch="feat/dispatch-picker",
                            cluster_name="MacBook Pro",
                        )
                    },
                ),
            ]
        )

        response = client.get("/api/v1/ting/sessions", headers=_auth_headers())

        assert response.status_code == 200
        assert [session["session_id"] for session in response.json()] == ["sess-1", "sess-2"]
        assert response.json()[1]["cluster_name"] == "MacBook Pro"

    def test_get_session_returns_single_session(self) -> None:
        client, _tracker = _client(
            volundrs=[
                MockVolundr(
                    name="Mac mini",
                    sessions={},
                ),
                MockVolundr(
                    name="MacBook Pro",
                    sessions={
                        "sess-1": VolundrSession(
                            id="sess-1",
                            name="Implement auth refresh",
                            status="running",
                            tracker_issue_id="RUN-1",
                            branch="feat/auth-refresh",
                            cluster_name="MacBook Pro",
                        )
                    },
                ),
            ]
        )

        response = client.get("/api/v1/ting/sessions/sess-1", headers=_auth_headers())

        assert response.status_code == 200
        assert response.json()["session_id"] == "sess-1"
        assert response.json()["cluster_name"] == "MacBook Pro"

    def test_approve_session_updates_tracker(self) -> None:
        primary = MockVolundr(name="Mac mini", sessions={})
        secondary = MockVolundr(name="MacBook Pro")
        primary.get_pr_status = AsyncMock(side_effect=AssertionError("wrong adapter"))  # type: ignore[method-assign]
        secondary.get_pr_status = AsyncMock(  # type: ignore[method-assign]
            return_value=awaitable_pr_status()
        )
        client, tracker = _client(volundrs=[primary, secondary])

        response = client.post("/api/v1/ting/sessions/sess-1/approve", headers=_auth_headers())

        assert response.status_code == 202
        assert tracker.updated_states[-1] == ("RUN-1", RunStatus.MERGED)
        assert tracker.closed[-1] == "RUN-1"
        secondary.get_pr_status.assert_awaited_once_with("sess-1")


def awaitable_pr_status():
    return _build_pr_status()


def _build_pr_status():
    from ting.domain.models import PRStatus

    return PRStatus(
        pr_id="pr-1",
        url="https://example.test/pr/1",
        state="open",
        mergeable=True,
        ci_passed=True,
    )
