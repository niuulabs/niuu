"""Tests for session activity state updates."""

from __future__ import annotations

import asyncio

import pytest

from volundr.domain.models import (
    EventType,
    GitSource,
    Session,
    SessionActivityState,
    SessionStatus,
)
from volundr.domain.services import SessionService
from volundr.domain.services.session import SessionNotFoundError


class TestUpdateActivity:
    """Tests for SessionService.update_activity."""

    @pytest.fixture
    def service(self, repository, pod_manager, broadcaster):
        return SessionService(
            repository=repository,
            pod_manager=pod_manager,
            broadcaster=broadcaster,
            provisioning_initial_delay=0,
            provisioning_timeout=1.0,
        )

    @pytest.fixture
    def service_no_broadcaster(self, repository, pod_manager):
        return SessionService(
            repository=repository,
            pod_manager=pod_manager,
            broadcaster=None,
            provisioning_initial_delay=0,
            provisioning_timeout=1.0,
        )

    @pytest.mark.asyncio
    async def test_update_activity_sets_state(self, service, broadcaster):
        """update_activity should set activity_state and metadata on the session."""
        session = await service.create_session(
            name="Test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
        )

        metadata = {"turn_count": 3, "duration_seconds": 45}
        updated = await service.update_activity(session.id, SessionActivityState.IDLE, metadata)

        assert updated.activity_state == SessionActivityState.IDLE
        assert updated.activity_metadata == metadata

    @pytest.mark.asyncio
    async def test_update_activity_clears_stale_liveness_error(self, service, repository):
        """A heartbeat is proof of life — a lingering liveness verdict is
        demonstrably false and must clear (the clients render `error` as a
        Session-error banner on an otherwise healthy running session)."""
        session = await service.create_session(
            name="Test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
        )
        stamped = session.model_copy(
            update={"error": "liveness: no activity heartbeat — broker presumed dead"}
        )
        await repository.update(stamped)

        updated = await service.update_activity(session.id, SessionActivityState.ACTIVE, {})
        assert updated.error is None

    @pytest.mark.asyncio
    async def test_update_activity_keeps_non_liveness_errors(self, service, repository):
        """Only liveness verdicts clear on heartbeat — real failure detail from
        other paths stays visible."""
        session = await service.create_session(
            name="Test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
        )
        stamped = session.model_copy(update={"error": "provisioning failed: no disk"})
        await repository.update(stamped)

        updated = await service.update_activity(session.id, SessionActivityState.ACTIVE, {})
        assert updated.error == "provisioning failed: no disk"

    @pytest.mark.asyncio
    async def test_update_activity_persists_cli_session_id(self, service):
        """A cli_session_id in the report is persisted on the session and kept
        out of activity_metadata (so it survives a stop for resume)."""
        session = await service.create_session(
            name="Resumable",
            model="claude-opus-4-8",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
        )
        assert session.cli_session_id is None

        updated = await service.update_activity(
            session.id,
            SessionActivityState.ACTIVE,
            {"turn_count": 1, "cli_session_id": "claude-abc-123"},
        )

        assert updated.cli_session_id == "claude-abc-123"
        assert "cli_session_id" not in updated.activity_metadata
        reloaded = await service.get_session(session.id)
        assert reloaded.cli_session_id == "claude-abc-123"

    @pytest.mark.asyncio
    async def test_update_activity_broadcasts_event(self, service, broadcaster):
        """update_activity should broadcast a SESSION_ACTIVITY event."""
        session = await service.create_session(
            name="Test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
        )

        broadcaster._events.clear()

        await service.update_activity(
            session.id,
            SessionActivityState.ACTIVE,
            {"turn_count": 1},
        )

        activity_events = [e for e in broadcaster._events if e.type == EventType.SESSION_ACTIVITY]
        assert len(activity_events) == 1
        assert activity_events[0].data["state"] == "active"
        assert activity_events[0].data["session_id"] == str(session.id)

    @pytest.mark.asyncio
    async def test_update_activity_not_found(self, service):
        """update_activity should raise SessionNotFoundError for missing session."""
        from uuid import uuid4

        with pytest.raises(SessionNotFoundError):
            await service.update_activity(uuid4(), SessionActivityState.IDLE, {})

    @pytest.mark.asyncio
    async def test_update_activity_without_broadcaster(self, service_no_broadcaster, repository):
        """update_activity should work without a broadcaster (no crash)."""
        session = await service_no_broadcaster.create_session(
            name="Test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
        )

        updated = await service_no_broadcaster.update_activity(
            session.id,
            SessionActivityState.TOOL_EXECUTING,
            {"turn_count": 2},
        )

        assert updated.activity_state == SessionActivityState.TOOL_EXECUTING

    @pytest.mark.asyncio
    async def test_update_activity_transitions(self, service, broadcaster):
        """update_activity should correctly transition between states."""
        session = await service.create_session(
            name="Test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
        )

        # Start active
        updated = await service.update_activity(
            session.id, SessionActivityState.ACTIVE, {"turn_count": 1}
        )
        assert updated.activity_state == SessionActivityState.ACTIVE

        # Transition to tool_executing
        updated = await service.update_activity(
            session.id, SessionActivityState.TOOL_EXECUTING, {"turn_count": 1}
        )
        assert updated.activity_state == SessionActivityState.TOOL_EXECUTING

        # Transition to idle
        updated = await service.update_activity(
            session.id, SessionActivityState.IDLE, {"turn_count": 2}
        )
        assert updated.activity_state == SessionActivityState.IDLE

    @pytest.mark.asyncio
    async def test_update_activity_auto_stops_completed_ravn_flock(
        self, service, repository, pod_manager, broadcaster
    ):
        """Authoritative ravn_flock completion should stop the session after broadcast."""
        session = Session(
            name="Research",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
            status=SessionStatus.RUNNING,
            workload_type="ravn_flock",
            chat_endpoint="wss://chat.example.com/session",
            code_endpoint="https://code.example.com/session",
            pod_name="volundr-test-pod",
        )
        created = await repository.create(session)
        broadcaster._events.clear()
        broadcaster._session_updated_events.clear()

        updated = await service.update_activity(
            created.id,
            SessionActivityState.IDLE,
            {
                "completion_source": "ravn_flock",
                "structured_outcome": {"verdict": "pass", "summary": "published"},
            },
        )

        assert updated.activity_state == SessionActivityState.IDLE
        activity_events = [e for e in broadcaster._events if e.type == EventType.SESSION_ACTIVITY]
        assert len(activity_events) == 1

        await asyncio.sleep(0)

        persisted = await repository.get(created.id)
        assert persisted is not None
        assert persisted.status == SessionStatus.STOPPED
        assert persisted.chat_endpoint is None
        assert persisted.code_endpoint is None
        assert len(pod_manager.stop_calls) == 1
        assert [event.status for event in broadcaster.session_updated_events] == [
            SessionStatus.STOPPING,
            SessionStatus.STOPPED,
        ]

    @pytest.mark.asyncio
    async def test_update_activity_does_not_auto_stop_non_terminal_idle(
        self, service, repository, pod_manager
    ):
        """Idle activity without authoritative flock completion should not stop the session."""
        session = Session(
            name="Research",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
            status=SessionStatus.RUNNING,
            workload_type="ravn_flock",
            chat_endpoint="wss://chat.example.com/session",
            code_endpoint="https://code.example.com/session",
            pod_name="volundr-test-pod",
        )
        created = await repository.create(session)

        await service.update_activity(
            created.id,
            SessionActivityState.IDLE,
            {"completion_source": "manual"},
        )

        await asyncio.sleep(0)

        persisted = await repository.get(created.id)
        assert persisted is not None
        assert persisted.status == SessionStatus.RUNNING
        assert pod_manager.stop_calls == []


class TestSessionActivityState:
    """Tests for the SessionActivityState enum."""

    def test_values(self) -> None:
        assert SessionActivityState.ACTIVE == "active"
        assert SessionActivityState.IDLE == "idle"
        assert SessionActivityState.TOOL_EXECUTING == "tool_executing"

    def test_from_string(self) -> None:
        assert SessionActivityState("active") == SessionActivityState.ACTIVE
        assert SessionActivityState("idle") == SessionActivityState.IDLE
        assert SessionActivityState("tool_executing") == SessionActivityState.TOOL_EXECUTING

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError):
            SessionActivityState("invalid")
