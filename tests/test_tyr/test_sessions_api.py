"""Tests for Tyr session compatibility endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tyr.api.raids import resolve_git, resolve_volundr
from tyr.api.sessions import create_sessions_router
from tyr.api.tracker import resolve_trackers
from tyr.config import AuthConfig, ReviewConfig, Settings
from tyr.domain.models import Phase, PhaseStatus, Raid, RaidStatus, Saga, SagaStatus
from tyr.ports.tracker import TrackerPort
from tyr.ports.volundr import VolundrPort, VolundrSession


class MockVolundr(VolundrPort):
    def __init__(self) -> None:
        self.sessions = {
            "sess-1": VolundrSession(
                id="sess-1",
                name="Implement auth refresh",
                status="running",
                tracker_issue_id="RAID-1",
                branch="feat/auth-refresh",
            )
        }

    async def spawn_session(self, request, *, auth_token=None):  # noqa: ANN001, ANN201
        raise NotImplementedError

    async def get_session(
        self, session_id: str, *, auth_token: str | None = None
    ) -> VolundrSession | None:
        return self.sessions.get(session_id)

    async def list_sessions(self, *, auth_token: str | None = None) -> list[VolundrSession]:
        return list(self.sessions.values())

    async def get_pr_status(self, session_id: str):  # noqa: ANN201
        from tyr.domain.models import PRStatus

        return PRStatus(exists=True, merged=False, url="https://example.test/pr/1", ci_passed=True)

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


class MockTracker(TrackerPort):
    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.raid = Raid(
            id=uuid4(),
            phase_id=uuid4(),
            tracker_id="RAID-1",
            name="Implement auth refresh",
            description="",
            acceptance_criteria=[],
            declared_files=[],
            estimate_hours=None,
            status=RaidStatus.REVIEW,
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
        self.updated_states: list[tuple[str, RaidStatus]] = []

    async def create_saga(self, saga: Saga, *, description: str = "") -> str:
        return ""

    async def create_phase(self, phase: Phase, *, project_id: str = "") -> str:
        return ""

    async def create_raid(self, raid: Raid, *, project_id: str = "", milestone_id: str = "") -> str:
        return ""

    async def update_raid_state(self, raid_id: str, state: RaidStatus) -> None:
        self.updated_states.append((raid_id, state))

    async def close_raid(self, raid_id: str) -> None:
        self.closed.append(raid_id)

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

    async def get_raid(self, tracker_id: str) -> Raid:
        return self.raid

    async def list_pending_raids(self, phase_id: str) -> list[Raid]:
        return []

    async def list_projects(self) -> list:
        return []

    async def get_project(self, project_id: str):  # noqa: ANN201
        raise NotImplementedError

    async def list_milestones(self, project_id: str) -> list:
        return []

    async def list_issues(self, project_id: str, milestone_id: str | None = None) -> list:
        return []

    async def update_raid_progress(self, tracker_id: str, **kwargs: object) -> Raid:  # noqa: ANN003
        return self.raid

    async def get_raid_progress_for_saga(self, saga_tracker_id: str) -> list[Raid]:
        return [self.raid]

    async def get_raid_by_session(self, session_id: str) -> Raid | None:
        return self.raid if session_id == "sess-1" else None

    async def list_raids_by_status(self, status: RaidStatus) -> list[Raid]:
        return [self.raid] if status is self.raid.status else []

    async def get_raid_by_id(self, raid_id: UUID) -> Raid | None:
        return self.raid if raid_id == self.raid.id else None

    async def add_confidence_event(self, tracker_id: str, event: object) -> None:  # noqa: ANN001
        return None

    async def get_confidence_events(self, tracker_id: str) -> list:
        return []

    async def all_raids_merged(self, phase_tracker_id: str) -> bool:
        return False

    async def list_phases_for_saga(self, saga_tracker_id: str) -> list[Phase]:
        return []

    async def update_phase_status(self, phase_tracker_id: str, status: PhaseStatus) -> Phase | None:
        return None

    async def get_saga_for_raid(self, tracker_id: str) -> Saga | None:
        return self.saga

    async def get_phase_for_raid(self, tracker_id: str) -> Phase | None:
        return None

    async def get_owner_for_raid(self, tracker_id: str) -> str | None:
        return "dev-user"

    async def save_session_message(self, message: object) -> None:  # noqa: ANN001
        return None

    async def get_session_messages(self, tracker_id: str) -> list:
        return []


def _client(tracker: MockTracker | None = None) -> tuple[TestClient, MockTracker]:
    resolved_tracker = tracker or MockTracker()
    app = FastAPI()
    app.include_router(create_sessions_router())
    app.dependency_overrides[resolve_trackers] = lambda: [resolved_tracker]
    app.dependency_overrides[resolve_volundr] = lambda: MockVolundr()
    app.dependency_overrides[resolve_git] = lambda: AsyncMock()
    app.state.settings = Settings(
        auth=AuthConfig(allow_anonymous_dev=True),
        review=ReviewConfig(),
    )
    app.state.event_bus = None
    return TestClient(app), resolved_tracker


def _auth_headers(user_id: str = "dev-user") -> dict[str, str]:
    return {"x-auth-user-id": user_id}


class TestSessionsAPI:
    def test_lists_sessions_with_context(self) -> None:
        client, _tracker = _client()

        response = client.get("/api/v1/tyr/sessions", headers=_auth_headers())

        assert response.status_code == 200
        assert response.json() == [
            {
                "session_id": "sess-1",
                "status": "awaiting_approval",
                "chronicle_lines": ["line 1", "line 2"],
                "branch": "feat/auth-refresh",
                "confidence": 82.0,
                "raid_name": "Implement auth refresh",
                "saga_name": "Auth Rewrite",
            }
        ]

    def test_get_session_returns_single_session(self) -> None:
        client, _tracker = _client()

        response = client.get("/api/v1/tyr/sessions/sess-1", headers=_auth_headers())

        assert response.status_code == 200
        assert response.json()["session_id"] == "sess-1"

    def test_approve_session_updates_tracker(self) -> None:
        client, tracker = _client()

        response = client.post("/api/v1/tyr/sessions/sess-1/approve", headers=_auth_headers())

        assert response.status_code == 202
        assert tracker.updated_states[-1] == ("RAID-1", RaidStatus.MERGED)
        assert tracker.closed[-1] == "RAID-1"
