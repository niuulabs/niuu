"""Coverage tests for Sleipnir emitter sites — NIU-582.

Each test verifies that when a sleipnir_publisher is wired in, the relevant
service calls publish() with an event of the correct type.  All tests are
purely unit-level: infrastructure (DB, HTTP, etc.) is stubbed or mocked.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from sleipnir.domain import registry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_publisher() -> AsyncMock:
    """Return an AsyncMock that acts as a Sleipnir publisher."""
    pub = AsyncMock()
    pub.publish = AsyncMock()
    return pub


def _published_event_type(pub: AsyncMock) -> str:
    """Return the event_type of the first published event."""
    assert pub.publish.called, "publish() was never called"
    evt = pub.publish.call_args[0][0]
    return evt.event_type


# ---------------------------------------------------------------------------
# Volundr SessionService — _emit_session_started / _emit_session_failed
# ---------------------------------------------------------------------------


class TestVolundrSessionServiceEmitters:
    """Tests for SessionService Sleipnir lifecycle emissions."""

    def _make_service(self, publisher: object) -> object:
        from volundr.domain.services.session import SessionService

        return SessionService(
            repository=MagicMock(),
            pod_manager=MagicMock(),
            sleipnir_publisher=publisher,
        )

    def _make_session(self) -> object:
        from volundr.domain.models import GitSource, Session

        return Session(
            name="test-session",
            source=GitSource(repo="niuulabs/niuu", branch="main"),
            owner_id="user-abc",
        )

    async def test_emit_session_started_calls_publish(self) -> None:
        pub = _mock_publisher()
        svc = self._make_service(pub)
        session = self._make_session()
        await svc._emit_session_started(session)
        assert pub.publish.called
        assert _published_event_type(pub) == registry.VOLUNDR_SESSION_STARTED

    async def test_emit_session_started_payload(self) -> None:
        pub = _mock_publisher()
        svc = self._make_service(pub)
        session = self._make_session()
        await svc._emit_session_started(session)
        evt = pub.publish.call_args[0][0]
        assert evt.payload["repo"] == "niuulabs/niuu"
        assert evt.payload["branch"] == "main"

    async def test_emit_session_started_noop_when_no_publisher(self) -> None:
        svc = self._make_service(None)
        session = self._make_session()
        await svc._emit_session_started(session)  # must not raise

    async def test_emit_session_failed_calls_publish(self) -> None:
        pub = _mock_publisher()
        svc = self._make_service(pub)
        session = self._make_session()
        await svc._emit_session_failed(session, "pod crash")
        assert pub.publish.called
        assert _published_event_type(pub) == registry.VOLUNDR_SESSION_FAILED

    async def test_emit_session_failed_payload(self) -> None:
        pub = _mock_publisher()
        svc = self._make_service(pub)
        session = self._make_session()
        await svc._emit_session_failed(session, "pod crash")
        evt = pub.publish.call_args[0][0]
        assert "pod crash" in evt.summary

    async def test_emit_session_failed_noop_when_no_publisher(self) -> None:
        svc = self._make_service(None)
        session = self._make_session()
        await svc._emit_session_failed(session, "error")  # must not raise

    async def test_emit_session_failed_swallows_error(self) -> None:
        pub = AsyncMock()
        pub.publish = AsyncMock(side_effect=RuntimeError("bus down"))
        svc = self._make_service(pub)
        session = self._make_session()
        await svc._emit_session_failed(session, "pod crash")  # must not raise


# ---------------------------------------------------------------------------
# SessionActivitySubscriber — _handle_completion emits ting.run.needs_approval
# ---------------------------------------------------------------------------


class TestActivitySubscriberEmitter:
    """Tests that _handle_completion emits ting.run.needs_approval."""

    def _make_subscriber(self, publisher: object) -> object:
        from ting.adapters.memory_event_bus import InMemoryEventBus
        from ting.config import WatcherConfig
        from ting.domain.services.activity_subscriber import SessionActivitySubscriber

        return SessionActivitySubscriber(
            volundr_factory=MagicMock(),
            tracker_factory=MagicMock(),
            dispatcher_repo=MagicMock(),
            event_bus=InMemoryEventBus(),
            config=WatcherConfig(),
            sleipnir_publisher=publisher,
        )

    def _make_run(self) -> object:
        from datetime import UTC, datetime
        from uuid import uuid4

        from ting.domain.models import Run, RunStatus

        return Run(
            id=uuid4(),
            phase_id=uuid4(),
            tracker_id="LIN-42",
            name="Fix auth",
            description="Fix auth bug",
            acceptance_criteria=[],
            declared_files=[],
            estimate_hours=None,
            status=RunStatus.RUNNING,
            session_id="sess-abc",
            branch="fix/auth",
            chronicle_summary=None,
            pr_url="https://github.com/niuulabs/niuu/pull/100",
            pr_id="100",
            retry_count=0,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

    async def test_handle_completion_emits_run_needs_approval(self) -> None:
        pub = _mock_publisher()
        sub = self._make_subscriber(pub)
        run = self._make_run()

        tracker = MagicMock()
        tracker.update_run_progress = AsyncMock(return_value=run)
        tracker.update_run_state = AsyncMock()

        volundr = MagicMock()
        volundr.get_chronicle_summary = AsyncMock(return_value="")

        evaluation = MagicMock()
        evaluation.pr_id = "100"
        evaluation.pr_url = "https://github.com/niuulabs/niuu/pull/100"

        sub._event_bus.emit = AsyncMock()

        await sub._handle_completion(run, tracker, volundr, "owner-1", evaluation)

        assert pub.publish.called
        assert _published_event_type(pub) == registry.TING_RUN_NEEDS_APPROVAL
        evt = pub.publish.call_args[0][0]
        assert evt.payload["run_id"] == "LIN-42"

    async def test_handle_completion_saga_id_is_phase_id(self) -> None:
        """saga_id in the event should be run.phase_id (not empty string)."""
        pub = _mock_publisher()
        sub = self._make_subscriber(pub)
        run = self._make_run()

        tracker = MagicMock()
        tracker.update_run_progress = AsyncMock(return_value=run)
        tracker.update_run_state = AsyncMock()

        volundr = MagicMock()
        volundr.get_chronicle_summary = AsyncMock(return_value="")

        sub._event_bus.emit = AsyncMock()

        await sub._handle_completion(run, tracker, volundr, "owner-1")

        evt = pub.publish.call_args[0][0]
        assert evt.payload["saga_id"] == str(run.phase_id)
        assert evt.payload["saga_id"] != ""

    async def test_handle_completion_noop_when_no_publisher(self) -> None:
        sub = self._make_subscriber(None)
        run = self._make_run()

        tracker = MagicMock()
        tracker.update_run_progress = AsyncMock(return_value=run)
        tracker.update_run_state = AsyncMock()

        volundr = MagicMock()
        volundr.get_chronicle_summary = AsyncMock(return_value="")

        sub._event_bus.emit = AsyncMock()

        await sub._handle_completion(run, tracker, volundr, "owner-1")  # must not raise


# ---------------------------------------------------------------------------
# DispatchService.find_ready_issues — emits ting.saga.completed
# ---------------------------------------------------------------------------


class TestDispatchServiceEmitter:
    """Tests that find_ready_issues emits ting.saga.completed on auto-archive."""

    def _make_service(self, publisher: object) -> object:
        from ting.domain.services.dispatch_service import DispatchConfig, DispatchService

        return DispatchService(
            tracker_factory=MagicMock(),
            volundr_factory=MagicMock(),
            saga_repo=MagicMock(),
            dispatcher_repo=MagicMock(),
            config=DispatchConfig(),
            sleipnir_publisher=publisher,
        )

    def _make_completed_saga(self) -> object:
        from ting.domain.models import Saga, SagaStatus

        return Saga(
            id=uuid4(),
            tracker_id="LIN-PROJECT-1",
            tracker_type="linear",
            slug="my-feature",
            name="My Feature",
            repos=["niuulabs/niuu"],
            feature_branch="feat/my-feature",
            status=SagaStatus.ACTIVE,
            created_at=datetime.now(UTC),
            base_branch="main",
            owner_id="owner-1",
        )

    def _mock_tracker_with_completed_project(self) -> object:
        """Return an AsyncMock tracker that reports a completed project."""
        mock_tracker = MagicMock()
        mock_project = MagicMock()
        mock_project.status = "completed"
        # _fetch_saga_data calls get_project_full if it exists; use AsyncMock
        mock_tracker.get_project_full = AsyncMock(return_value=(mock_project, [], []))
        return mock_tracker

    async def test_find_ready_issues_emits_saga_completed_on_auto_archive(self) -> None:
        pub = _mock_publisher()
        svc = self._make_service(pub)
        saga = self._make_completed_saga()

        svc._saga_repo.list_sagas = AsyncMock(return_value=[saga])
        svc._saga_repo.get_phases_by_saga = AsyncMock(return_value=[])
        svc._saga_repo.update_saga_status = AsyncMock()

        mock_volundr = MagicMock()
        mock_volundr.list_sessions = AsyncMock(return_value=[])
        svc._volundr_factory.for_owner = AsyncMock(return_value=[mock_volundr])
        svc._tracker_factory.for_owner = AsyncMock(
            return_value=[self._mock_tracker_with_completed_project()]
        )

        await svc.find_ready_issues("owner-1")

        assert pub.publish.called
        assert _published_event_type(pub) == registry.TING_SAGA_COMPLETED
        evt = pub.publish.call_args[0][0]
        assert evt.payload["outcome"] == "auto_archived"

    async def test_find_ready_issues_noop_when_no_publisher(self) -> None:
        svc = self._make_service(None)
        saga = self._make_completed_saga()

        svc._saga_repo.list_sagas = AsyncMock(return_value=[saga])
        svc._saga_repo.get_phases_by_saga = AsyncMock(return_value=[])
        svc._saga_repo.update_saga_status = AsyncMock()

        mock_volundr = MagicMock()
        mock_volundr.list_sessions = AsyncMock(return_value=[])
        svc._volundr_factory.for_owner = AsyncMock(return_value=[mock_volundr])
        svc._tracker_factory.for_owner = AsyncMock(
            return_value=[self._mock_tracker_with_completed_project()]
        )

        await svc.find_ready_issues("owner-1")  # must not raise


# ---------------------------------------------------------------------------
# bifrost tracking — emit_cost_events emits bifrost.budget.degraded
# ---------------------------------------------------------------------------


class TestBifrostTrackingEmitter:
    """Tests for bifrost.budget.degraded emission from emit_cost_events."""

    def _identity(self) -> object:
        from bifrost.auth import AgentIdentity

        return AgentIdentity(
            agent_id="agent-1",
            tenant_id="tenant-abc",
            session_id="sess-1",
        )

    def _seeded_store(self, cost: float) -> object:
        from datetime import UTC, datetime

        from bifrost.adapters.memory_store import MemoryUsageStore
        from bifrost.ports.usage_store import UsageRecord

        store = MemoryUsageStore()
        if cost > 0.0:
            store._records.append(
                UsageRecord(
                    request_id="seed",
                    agent_id="agent-1",
                    tenant_id="tenant-abc",
                    model="claude-sonnet-4-6",
                    input_tokens=100,
                    output_tokens=50,
                    cost_usd=cost,
                    timestamp=datetime.now(UTC),
                )
            )
        return store

    async def test_emit_cost_events_publishes_budget_degraded(self) -> None:
        from bifrost.adapters.events.null import NullEventEmitter
        from bifrost.inbound.tracking import emit_cost_events

        pub = _mock_publisher()
        identity = self._identity()
        # Store has 0.85 of a 1.0 cap → 85% consumed → above 80% threshold
        store = self._seeded_store(0.85)

        await emit_cost_events(
            emitter=NullEventEmitter(),
            store=store,
            identity=identity,
            cost=0.01,
            tokens_used=100,
            model="claude-sonnet-4-6",
            agent_budget_limit=1.0,
            budget_warning_threshold_pct=20.0,
            sleipnir_publisher=pub,
        )

        assert pub.publish.called
        assert _published_event_type(pub) == registry.BIFROST_BUDGET_DEGRADED
        evt = pub.publish.call_args[0][0]
        assert evt.payload["tenant_id"] == "tenant-abc"

    async def test_emit_cost_events_no_publish_below_threshold(self) -> None:
        from bifrost.adapters.events.null import NullEventEmitter
        from bifrost.inbound.tracking import emit_cost_events

        pub = _mock_publisher()
        identity = self._identity()
        # Only 10% consumed → below 80% threshold, no budget warning
        store = self._seeded_store(0.10)

        await emit_cost_events(
            emitter=NullEventEmitter(),
            store=store,
            identity=identity,
            cost=0.01,
            tokens_used=100,
            model="claude-sonnet-4-6",
            agent_budget_limit=1.0,
            budget_warning_threshold_pct=20.0,
            sleipnir_publisher=pub,
        )

        pub.publish.assert_not_called()

    async def test_emit_cost_events_noop_when_no_publisher(self) -> None:
        from bifrost.adapters.events.null import NullEventEmitter
        from bifrost.inbound.tracking import emit_cost_events

        identity = self._identity()
        store = self._seeded_store(0.85)

        await emit_cost_events(
            emitter=NullEventEmitter(),
            store=store,
            identity=identity,
            cost=0.01,
            tokens_used=100,
            model="claude-sonnet-4-6",
            agent_budget_limit=1.0,
            budget_warning_threshold_pct=20.0,
            sleipnir_publisher=None,
        )  # must not raise

    async def test_emit_cost_events_swallows_publish_error(self) -> None:
        from bifrost.adapters.events.null import NullEventEmitter
        from bifrost.inbound.tracking import emit_cost_events

        pub = AsyncMock()
        pub.publish = AsyncMock(side_effect=RuntimeError("bus down"))
        identity = self._identity()
        store = self._seeded_store(0.85)

        await emit_cost_events(
            emitter=NullEventEmitter(),
            store=store,
            identity=identity,
            cost=0.01,
            tokens_used=100,
            model="claude-sonnet-4-6",
            agent_budget_limit=1.0,
            budget_warning_threshold_pct=20.0,
            sleipnir_publisher=pub,
        )  # must not raise
