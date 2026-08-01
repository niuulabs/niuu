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
    def attention_notifier(self):
        from volundr.domain.ports import AttentionNotifier

        class RecordingNotifier(AttentionNotifier):
            def __init__(self):
                self.calls = []

            async def notify_needs_input(self, session, *, kind, prompt, request_id):
                self.calls.append((session.id, kind, prompt, request_id))

        return RecordingNotifier()

    @pytest.fixture
    def service_with_notifier(self, repository, pod_manager, broadcaster, attention_notifier):
        return SessionService(
            repository=repository,
            pod_manager=pod_manager,
            broadcaster=broadcaster,
            attention_notifier=attention_notifier,
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
    async def test_terminal_error_activity_persists_failure_reason(self, service):
        session = await service.create_session(
            name="Test",
            model="codex",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
        )

        updated = await service.update_activity(
            session.id,
            SessionActivityState.ERROR,
            {"error": "refresh token was already used"},
        )

        assert updated.activity_state == SessionActivityState.ERROR
        assert updated.error == "refresh token was already used"

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
    async def test_awaiting_input_emits_needs_input_event(self, service, broadcaster):
        """Entering awaiting_input fires a dedicated SESSION_NEEDS_INPUT event."""
        session = await service.create_session(
            name="Blocked",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
        )
        broadcaster._events.clear()

        await service.update_activity(
            session.id,
            SessionActivityState.AWAITING_INPUT,
            {"kind": "question", "prompt": "Which DB?", "request_id": "askq-1"},
        )

        needs = [e for e in broadcaster._events if e.type == EventType.SESSION_NEEDS_INPUT]
        assert len(needs) == 1
        assert needs[0].data["kind"] == "question"
        assert needs[0].data["prompt"] == "Which DB?"
        assert needs[0].data["request_id"] == "askq-1"
        assert needs[0].data["session_id"] == str(session.id)
        # The routine activity event is still emitted alongside it.
        activity = [e for e in broadcaster._events if e.type == EventType.SESSION_ACTIVITY]
        assert len(activity) == 1

    @pytest.mark.asyncio
    async def test_needs_input_not_re_emitted_for_same_request(self, service, broadcaster):
        """A repeated report for the same pending request must not re-fire."""
        session = await service.create_session(
            name="Blocked",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
        )
        meta = {"kind": "question", "request_id": "askq-1"}
        await service.update_activity(session.id, SessionActivityState.AWAITING_INPUT, dict(meta))
        broadcaster._events.clear()

        await service.update_activity(session.id, SessionActivityState.AWAITING_INPUT, dict(meta))

        needs = [e for e in broadcaster._events if e.type == EventType.SESSION_NEEDS_INPUT]
        assert needs == []

    @pytest.mark.asyncio
    async def test_needs_input_re_emitted_for_new_request(self, service, broadcaster):
        """A second question (new request_id) while still awaiting fires again."""
        session = await service.create_session(
            name="Blocked",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
        )
        await service.update_activity(
            session.id, SessionActivityState.AWAITING_INPUT, {"request_id": "askq-1"}
        )
        broadcaster._events.clear()

        await service.update_activity(
            session.id, SessionActivityState.AWAITING_INPUT, {"request_id": "askq-2"}
        )

        needs = [e for e in broadcaster._events if e.type == EventType.SESSION_NEEDS_INPUT]
        assert len(needs) == 1
        assert needs[0].data["request_id"] == "askq-2"

    @pytest.mark.asyncio
    async def test_heartbeat_report_does_not_refire_needs_input(self, service, broadcaster):
        """A Skuld heartbeat re-reporting awaiting_input must not re-fire the
        needs-input event (which would re-trigger a push)."""
        session = await service.create_session(
            name="Blocked",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
        )
        await service.update_activity(
            session.id,
            SessionActivityState.AWAITING_INPUT,
            {"kind": "question", "request_id": "askq-1"},
        )
        broadcaster._events.clear()

        await service.update_activity(
            session.id,
            SessionActivityState.AWAITING_INPUT,
            {"kind": "question", "request_id": "askq-1", "heartbeat": True},
        )

        needs = [e for e in broadcaster._events if e.type == EventType.SESSION_NEEDS_INPUT]
        assert needs == []

    @pytest.mark.asyncio
    async def test_attention_notifier_fires_on_new_request(
        self, service_with_notifier, attention_notifier
    ):
        """Entering awaiting_input dispatches a push via the attention notifier."""
        session = await service_with_notifier.create_session(
            name="Blocked",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
        )

        await service_with_notifier.update_activity(
            session.id,
            SessionActivityState.AWAITING_INPUT,
            {"kind": "question", "prompt": "Which DB?", "request_id": "askq-1"},
        )
        await asyncio.sleep(0)  # let the fire-and-forget notify task run

        assert attention_notifier.calls == [(session.id, "question", "Which DB?", "askq-1")]

    @pytest.mark.asyncio
    async def test_attention_notifier_not_fired_for_busy(
        self, service_with_notifier, attention_notifier
    ):
        session = await service_with_notifier.create_session(
            name="Working",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
        )

        await service_with_notifier.update_activity(
            session.id, SessionActivityState.ACTIVE, {"turn_count": 1}
        )
        await asyncio.sleep(0)

        assert attention_notifier.calls == []

    @pytest.mark.asyncio
    async def test_busy_states_do_not_emit_needs_input(self, service, broadcaster):
        """active/idle/tool_executing never raise a needs-input signal."""
        session = await service.create_session(
            name="Working",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
        )
        broadcaster._events.clear()

        for state in (
            SessionActivityState.ACTIVE,
            SessionActivityState.TOOL_EXECUTING,
            SessionActivityState.IDLE,
        ):
            await service.update_activity(session.id, state, {"turn_count": 1})

        needs = [e for e in broadcaster._events if e.type == EventType.SESSION_NEEDS_INPUT]
        assert needs == []

    @pytest.mark.asyncio
    async def test_update_activity_persists_state_since(self, service):
        """A broker-stamped state_since is persisted on the session and round-trips."""
        from datetime import UTC, datetime

        session = await service.create_session(
            name="Test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
        )
        since = datetime(2026, 6, 26, 12, 0, 0, tzinfo=UTC)

        updated = await service.update_activity(
            session.id,
            SessionActivityState.ACTIVE,
            {"turn_count": 1},
            state_since=since,
        )

        assert updated.activity_state_since == since
        reloaded = await service.get_session(session.id)
        assert reloaded.activity_state_since == since

    @pytest.mark.asyncio
    async def test_update_activity_defaults_state_since_when_omitted(self, service):
        """An older broker that omits state_since still gets a non-null timestamp."""
        session = await service.create_session(
            name="Test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
        )

        updated = await service.update_activity(session.id, SessionActivityState.IDLE, {})

        assert updated.activity_state_since is not None

    @pytest.mark.asyncio
    async def test_activity_event_carries_state_since(self, service, broadcaster):
        """The SESSION_ACTIVITY SSE payload includes activity_state_since (ISO8601)."""
        from datetime import UTC, datetime

        session = await service.create_session(
            name="Test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
        )
        broadcaster._events.clear()
        since = datetime(2026, 6, 26, 12, 0, 0, tzinfo=UTC)

        await service.update_activity(
            session.id, SessionActivityState.ACTIVE, {"turn_count": 1}, state_since=since
        )

        activity = [e for e in broadcaster._events if e.type == EventType.SESSION_ACTIVITY]
        assert len(activity) == 1
        assert activity[0].data["activity_state_since"] == since.isoformat()

    @pytest.mark.asyncio
    async def test_provisioning_and_stopped_states_round_trip(self, service):
        """The new provisioning / stopped states are accepted and persisted."""
        session = await service.create_session(
            name="Test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
        )

        prov = await service.update_activity(session.id, SessionActivityState.PROVISIONING, {})
        assert prov.activity_state == SessionActivityState.PROVISIONING

        stopped = await service.update_activity(session.id, SessionActivityState.STOPPED, {})
        assert stopped.activity_state == SessionActivityState.STOPPED

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
        assert SessionActivityState.PROVISIONING == "provisioning"
        assert SessionActivityState.ACTIVE == "active"
        assert SessionActivityState.IDLE == "idle"
        assert SessionActivityState.TOOL_EXECUTING == "tool_executing"
        assert SessionActivityState.AWAITING_INPUT == "awaiting_input"
        assert SessionActivityState.STOPPED == "stopped"
        assert SessionActivityState.ERROR == "error"

    def test_from_string(self) -> None:
        assert SessionActivityState("provisioning") == SessionActivityState.PROVISIONING
        assert SessionActivityState("active") == SessionActivityState.ACTIVE
        assert SessionActivityState("idle") == SessionActivityState.IDLE
        assert SessionActivityState("tool_executing") == SessionActivityState.TOOL_EXECUTING
        assert SessionActivityState("awaiting_input") == SessionActivityState.AWAITING_INPUT
        assert SessionActivityState("stopped") == SessionActivityState.STOPPED
        assert SessionActivityState("error") == SessionActivityState.ERROR

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError):
            SessionActivityState("invalid")

    def test_is_busy(self) -> None:
        assert SessionActivityState.ACTIVE.is_busy
        assert SessionActivityState.TOOL_EXECUTING.is_busy
        assert not SessionActivityState.IDLE.is_busy
        assert not SessionActivityState.AWAITING_INPUT.is_busy

    def test_needs_attention(self) -> None:
        assert SessionActivityState.AWAITING_INPUT.needs_attention
        assert not SessionActivityState.ACTIVE.needs_attention
        assert not SessionActivityState.IDLE.needs_attention
