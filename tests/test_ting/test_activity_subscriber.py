"""Tests for the event-driven session activity subscriber."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from ting.adapters.memory_event_bus import InMemoryEventBus
from ting.config import WatcherConfig
from ting.domain.models import (
    DispatcherState,
    PRStatus,
    Run,
    RunStatus,
    SessionMessage,
    WorkflowCampaign,
    WorkflowCampaignStatus,
)
from ting.domain.services.activity_subscriber import (
    CompletionEvaluation,
    DuplicateVolundrClusterError,
    SessionActivitySubscriber,
    _ClusterBackoffState,
    _coerce_bool,
    _coerce_float,
    _coerce_str,
    _coerce_str_list,
)
from ting.domain.services.workflow_campaign_projector import WorkflowCampaignProjector
from ting.ports.dispatcher_repository import DispatcherRepository
from ting.ports.volundr import (
    ActivityEvent,
    ActivityStreamConnected,
    SpawnRequest,
    VolundrPort,
    VolundrSession,
)
from ting.ports.workflow_campaign_repository import WorkflowCampaignRepository

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

NOW = datetime.now(UTC)

SESSION_ID = "session-1"
OWNER_ID = "user-1"
SAGA_ID = UUID("00000000-0000-0000-0000-000000000002")
TRACKER_ISSUE_ID = "issue-1"
PHASE_ID = UUID("00000000-0000-0000-0000-000000000003")
RUN_ID = UUID("00000000-0000-0000-0000-000000000004")


def _make_run(
    session_id: str = SESSION_ID,
    tracker_id: str = TRACKER_ISSUE_ID,
    status: RunStatus = RunStatus.RUNNING,
    review_round: int = 0,
) -> Run:
    """Create a Run domain object for testing."""
    return Run(
        id=RUN_ID,
        phase_id=PHASE_ID,
        tracker_id=tracker_id,
        name="Test Run",
        description="A test run",
        acceptance_criteria=[],
        declared_files=[],
        estimate_hours=None,
        status=status,
        session_id=session_id,
        branch=None,
        chronicle_summary=None,
        pr_url=None,
        pr_id=None,
        retry_count=0,
        created_at=NOW,
        updated_at=NOW,
        review_round=review_round,
    )


class StubVolundr(VolundrPort):
    """In-memory Volundr stub for activity subscriber tests."""

    def __init__(self) -> None:
        self.sessions: dict[str, VolundrSession] = {}
        self.pr_statuses: dict[str, PRStatus] = {}
        self.chronicles: dict[str, str] = {}
        self.pr_error_sessions: set[str] = set()
        self.chronicle_error_sessions: set[str] = set()
        self.activity_events: list[ActivityEvent] = []

    async def spawn_session(
        self, request: SpawnRequest, *, auth_token: str | None = None
    ) -> VolundrSession:
        raise NotImplementedError

    async def get_session(
        self, session_id: str, *, auth_token: str | None = None
    ) -> VolundrSession | None:
        return self.sessions.get(session_id)

    async def list_sessions(self, *, auth_token: str | None = None) -> list[VolundrSession]:
        return list(self.sessions.values())

    async def get_pr_status(self, session_id: str) -> PRStatus:
        if session_id in self.pr_error_sessions:
            raise RuntimeError("PR not found")
        pr = self.pr_statuses.get(session_id)
        if pr is None:
            raise RuntimeError("No PR")
        return pr

    async def get_chronicle_summary(self, session_id: str) -> str:
        if session_id in self.chronicle_error_sessions:
            raise RuntimeError("Chronicle fetch failed")
        return self.chronicles.get(session_id, "")

    async def send_message(
        self, session_id: str, message: str, *, auth_token: str | None = None
    ) -> None:
        pass

    async def stop_session(self, session_id: str, *, auth_token: str | None = None) -> None:
        pass

    async def list_integration_ids(self, *, auth_token: str | None = None) -> list[str]:
        return []

    async def list_repos(self, *, auth_token: str | None = None) -> list[dict]:
        return []

    async def get_last_assistant_message(self, session_id: str) -> str:
        return ""

    async def get_conversation(self, session_id: str) -> dict:
        return {}

    async def subscribe_activity(
        self,
    ) -> AsyncGenerator[ActivityEvent | ActivityStreamConnected, None]:
        yield ActivityStreamConnected()
        for event in self.activity_events:
            yield event


class MockTracker:
    """Mock TrackerPort that records calls and returns configured runs."""

    def __init__(self, runs_by_session: dict[str, Run] | None = None) -> None:
        self._runs_by_session = runs_by_session or {}
        self.messages: dict[UUID, list[SessionMessage]] = {}
        self.update_run_progress = AsyncMock(
            side_effect=self._update_run_progress_impl,
        )
        self.update_run_state = AsyncMock()

    async def _update_run_progress_impl(self, tracker_id: str, **kwargs: object) -> Run:
        """Return a run with updated status when update_run_progress is called."""
        # Find the run by tracker_id
        for session_id, run in list(self._runs_by_session.items()):
            if run.tracker_id == tracker_id:
                status = kwargs.get("status")
                if isinstance(status, RunStatus):
                    updated = Run(
                        **{
                            **run.__dict__,
                            "status": status,
                            "updated_at": NOW,
                        }
                    )
                    self._runs_by_session[session_id] = updated
                    return updated
                return run
        return _make_run()

    async def get_run_by_session(self, session_id: str) -> Run | None:
        return self._runs_by_session.get(session_id)

    async def list_runs_by_status(self, status: RunStatus) -> list[Run]:
        return [run for run in self._runs_by_session.values() if run.status is status]

    async def save_session_message(self, message: SessionMessage) -> None:
        self.messages.setdefault(message.run_id, []).append(message)

    async def get_session_messages(self, tracker_id: str) -> list[SessionMessage]:
        for run in self._runs_by_session.values():
            if run.tracker_id == tracker_id:
                return self.messages.get(run.id, [])
        return []

    async def get_saga_for_run(self, tracker_id: str):  # noqa: ANN001
        return type("SagaStub", (), {"id": SAGA_ID, "name": "Research Council"})()


class StubDispatcherRepo(DispatcherRepository):
    """In-memory dispatcher repo for activity subscriber tests."""

    def __init__(self, running: bool = True) -> None:
        self._running = running

    async def get_or_create(self, owner_id: str) -> DispatcherState:
        return DispatcherState(
            id=uuid4(),
            owner_id=owner_id,
            running=self._running,
            max_concurrent_runs=3,
            auto_continue=True,
            updated_at=NOW,
        )

    async def update(self, owner_id: str, **fields: object) -> DispatcherState:
        return await self.get_or_create(owner_id)

    async def list_active_owner_ids(self) -> list[str]:
        return []


class ActiveOwnersDispatcherRepo(StubDispatcherRepo):
    """StubDispatcherRepo whose list_active_owner_ids is configurable."""

    def __init__(self, owner_ids: list[str], running: bool = True) -> None:
        super().__init__(running=running)
        self._owner_ids = owner_ids

    async def list_active_owner_ids(self) -> list[str]:
        return list(self._owner_ids)


class StubWorkflowCampaignRepo(WorkflowCampaignRepository):
    def __init__(self, campaigns: list[WorkflowCampaign] | None = None) -> None:
        self.campaigns = {campaign.id: campaign for campaign in campaigns or []}

    async def list_campaigns(self, *, owner_id: str) -> list[WorkflowCampaign]:
        return [campaign for campaign in self.campaigns.values() if campaign.owner_id == owner_id]

    async def list_active_campaigns(self) -> list[WorkflowCampaign]:
        return [
            campaign
            for campaign in self.campaigns.values()
            if campaign.status
            in {
                WorkflowCampaignStatus.PENDING,
                WorkflowCampaignStatus.RUNNING,
                WorkflowCampaignStatus.BLOCKED,
            }
        ]

    async def get_campaign(self, campaign_id: UUID) -> WorkflowCampaign | None:
        return self.campaigns.get(campaign_id)

    async def get_campaign_by_slug(
        self,
        slug: str,
        *,
        owner_id: str | None = None,
    ) -> WorkflowCampaign | None:
        for campaign in self.campaigns.values():
            if campaign.slug != slug:
                continue
            if owner_id is not None and campaign.owner_id != owner_id:
                continue
            return campaign
        return None

    async def save_campaign(self, campaign: WorkflowCampaign) -> WorkflowCampaign:
        self.campaigns[campaign.id] = campaign
        return campaign

    async def delete_campaign(self, campaign_id: UUID) -> bool:
        return self.campaigns.pop(campaign_id, None) is not None


class StubVolundrFactory:
    """Stub factory that always returns the same adapter for any owner."""

    def __init__(self, adapter: StubVolundr) -> None:
        self._adapter = adapter

    async def for_owner(self, owner_id: str) -> list[StubVolundr]:
        return [self._adapter]

    async def for_owner_with_unresolved(self, owner_id: str) -> tuple[list[StubVolundr], int]:
        return [self._adapter], 0


class NamedVolundr(StubVolundr):
    """StubVolundr with a configurable cluster identity and failure schedule.

    ``fail_times`` controls how many of the first ``subscribe_activity()``
    calls raise ``error_factory()`` before the stream starts succeeding
    (streams that succeed yield ``ActivityStreamConnected`` then
    ``self.activity_events`` and end). By default a failing attempt raises
    before ever yielding ``ActivityStreamConnected`` — simulating a
    connect that is refused or times out. Pass ``fail_after_connect=True``
    to simulate a connection that opens successfully and then drops.
    """

    def __init__(
        self,
        name: str = "cluster",
        target_id: str = "",
        base_url: str = "",
        fail_times: int = 0,
        error_factory: object = None,
        fail_after_connect: bool = False,
    ) -> None:
        super().__init__()
        self._cluster_name = name
        self._cluster_target_id = target_id or name
        self._cluster_base_url = base_url
        self._fail_times = fail_times
        self._error_factory = error_factory or (lambda: RuntimeError("boom"))
        self._fail_after_connect = fail_after_connect
        self.attempts = 0
        self.get_session_error: Exception | None = None
        self.get_session_delay: float = 0.0
        self.get_session_calls = 0

    @property
    def name(self) -> str:
        return self._cluster_name

    @property
    def target_id(self) -> str:
        return self._cluster_target_id

    @property
    def base_url(self) -> str:
        return self._cluster_base_url

    async def subscribe_activity(
        self,
    ) -> AsyncGenerator[ActivityEvent | ActivityStreamConnected, None]:
        self.attempts += 1
        if self.attempts <= self._fail_times:
            if self._fail_after_connect:
                yield ActivityStreamConnected()
            raise self._error_factory()
        yield ActivityStreamConnected()
        for event in self.activity_events:
            yield event

    async def get_session(
        self, session_id: str, *, auth_token: str | None = None
    ) -> VolundrSession | None:
        self.get_session_calls += 1
        if self.get_session_delay:
            await asyncio.sleep(self.get_session_delay)
        if self.get_session_error is not None:
            raise self.get_session_error
        return await super().get_session(session_id, auth_token=auth_token)


class MultiVolundrFactory:
    """Stub factory that returns a fixed list of adapters for any owner.

    ``unresolved`` simulates VolundrAdapterFactory.for_owner_with_unresolved
    reporting registered-but-unresolvable clusters (bad credential, failed
    construction) — set it to exercise callers that must not treat those
    the same as "genuinely zero clusters registered".
    """

    def __init__(self, adapters: list[VolundrPort], unresolved: int = 0) -> None:
        self._adapters = adapters
        self._unresolved = unresolved

    async def for_owner(self, owner_id: str) -> list[VolundrPort]:
        return list(self._adapters)

    async def for_owner_with_unresolved(self, owner_id: str) -> tuple[list[VolundrPort], int]:
        return list(self._adapters), self._unresolved


class RaisingForOneOwnerFactory:
    """Stub factory that raises resolving one owner, and serves the rest normally."""

    def __init__(self, bad_owner_id: str, adapters: list[VolundrPort]) -> None:
        self._bad_owner_id = bad_owner_id
        self._adapters = adapters

    async def for_owner(self, owner_id: str) -> list[VolundrPort]:
        if owner_id == self._bad_owner_id:
            raise RuntimeError("guild registry unavailable")
        return list(self._adapters)

    async def for_owner_with_unresolved(self, owner_id: str) -> tuple[list[VolundrPort], int]:
        if owner_id == self._bad_owner_id:
            raise RuntimeError("guild registry unavailable")
        return list(self._adapters), 0


class MockTrackerFactory:
    """Stub TrackerFactory that returns a configurable list of tracker adapters."""

    def __init__(self, trackers: list[MockTracker] | None = None) -> None:
        self._trackers = trackers or []

    async def for_owner(self, owner_id: str) -> list[MockTracker]:
        return list(self._trackers)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_config(**overrides: object) -> WatcherConfig:
    defaults: dict = {
        "enabled": True,
        "poll_interval": 1.0,
        "batch_size": 10,
        "chronicle_on_complete": True,
        "idle_threshold": 30.0,
        "completion_check_delay": 0.0,  # No delay for tests
        "require_pr": False,
        "require_ci": False,
        "reconnect_delay": 0.1,
    }
    defaults.update(overrides)
    return WatcherConfig(**defaults)


def _make_volundr_session(
    session_id: str = SESSION_ID,
    status: str = "running",
    workload_type: str = "default",
) -> VolundrSession:
    return VolundrSession(
        id=session_id,
        name="Test Session",
        status=status,
        tracker_issue_id=None,
        workload_type=workload_type,
    )


def _make_workflow_campaign(session_id: str = SESSION_ID) -> WorkflowCampaign:
    return WorkflowCampaign(
        id=uuid4(),
        slug="plan-test",
        name="Plan test",
        owner_id=OWNER_ID,
        workflow_id=uuid4(),
        workflow_version="1.0.0",
        workflow_name="Saga Planning",
        workflow_snapshot={},
        session_id=session_id,
        session_name="Plan test",
        status=WorkflowCampaignStatus.RUNNING,
        active_stage_id="plan-clarify",
        stage_state=[],
        metadata={},
        created_at=NOW,
        updated_at=NOW,
    )


def _make_subscriber(
    volundr: StubVolundr | None = None,
    dispatcher_repo: StubDispatcherRepo | None = None,
    event_bus: InMemoryEventBus | None = None,
    config: WatcherConfig | None = None,
    tracker: MockTracker | None = None,
    run: Run | None = None,
    review_engine: object | None = None,
    workflow_campaign_repo: WorkflowCampaignRepository | None = None,
) -> tuple[SessionActivitySubscriber, StubVolundr, MockTracker, InMemoryEventBus]:
    v = volundr or StubVolundr()
    if SESSION_ID not in v.sessions:
        v.sessions[SESSION_ID] = _make_volundr_session()

    r = run or _make_run()
    t = tracker or MockTracker(runs_by_session={r.session_id: r} if r.session_id else {})
    tf = MockTrackerFactory(trackers=[t])

    d = dispatcher_repo or StubDispatcherRepo()
    e = event_bus or InMemoryEventBus()
    c = config or _default_config()
    factory = StubVolundrFactory(v)
    campaign_projector = (
        WorkflowCampaignProjector(
            repo=workflow_campaign_repo,
            volundr_factory=factory,  # type: ignore[arg-type]
            event_bus=e,
        )
        if workflow_campaign_repo is not None
        else None
    )
    sub = SessionActivitySubscriber(
        volundr_factory=factory,
        tracker_factory=tf,
        dispatcher_repo=d,
        event_bus=e,
        config=c,
        review_engine=review_engine,  # type: ignore[arg-type]
        workflow_campaign_projector=campaign_projector,
    )
    return sub, v, t, e


# ---------------------------------------------------------------------------
# Tests -- CompletionEvaluation
# ---------------------------------------------------------------------------


class TestCompletionEvaluation:
    def test_defaults(self) -> None:
        ce = CompletionEvaluation(is_complete=False, signals={})
        assert ce.is_complete is False

    def test_complete_with_signals(self) -> None:
        ce = CompletionEvaluation(
            is_complete=True,
            signals={"session_idle": True, "has_turns": True},
        )
        assert ce.is_complete is True


# ---------------------------------------------------------------------------
# Tests -- Lifecycle
# ---------------------------------------------------------------------------


class TestSubscriberLifecycle:
    def test_not_running_initially(self) -> None:
        sub, _, _, _ = _make_subscriber()
        assert sub.running is False

    @pytest.mark.asyncio
    async def test_disabled_by_config(self) -> None:
        config = _default_config(enabled=False)
        sub, _, _, _ = _make_subscriber(config=config)
        await sub.start()
        assert sub.running is False

    @pytest.mark.asyncio
    async def test_start_stop(self) -> None:
        sub, _, _, _ = _make_subscriber()
        await sub.start()
        assert sub.running is True
        await sub.stop()
        assert sub.running is False

    @pytest.mark.asyncio
    async def test_stop_when_not_started(self) -> None:
        sub, _, _, _ = _make_subscriber()
        await sub.stop()  # Should not raise

    @pytest.mark.asyncio
    async def test_stop_awaits_cancelled_cluster_tasks(self) -> None:
        """stop() must not return while a cancelled cluster task is still mid-teardown."""
        sub, _, _, _ = _make_subscriber()
        task = asyncio.create_task(asyncio.sleep(60))
        sub._owner_tasks[OWNER_ID] = {"cluster-a": task}

        await sub.stop()

        assert task.done()
        assert task.cancelled()

    @pytest.mark.asyncio
    async def test_campaign_owner_gets_subscription_without_dispatcher(self) -> None:
        sub, _volundr, _, _ = _make_subscriber(
            volundr=NamedVolundr(name="stub-cluster"),
            config=_default_config(reconnect_delay=0),
        )
        projector = AsyncMock()
        projector.list_active_owner_ids.return_value = [OWNER_ID]
        sub._workflow_campaign_projector = projector
        stale = asyncio.create_task(asyncio.sleep(60))
        sub._owner_tasks["stale-owner"] = {"stale-cluster": stale}

        await sub._sync_owner_subscriptions()
        await asyncio.sleep(0)

        projector.reconcile_owner.assert_awaited_once_with(OWNER_ID)
        assert OWNER_ID in sub._owner_tasks
        assert "stale-owner" not in sub._owner_tasks
        assert stale.cancelled()

    @pytest.mark.asyncio
    async def test_no_active_work_clears_existing_subscriptions(self) -> None:
        sub, _volundr, _, _ = _make_subscriber(config=_default_config(reconnect_delay=0))
        stale = asyncio.create_task(asyncio.sleep(60))
        sub._owner_tasks[OWNER_ID] = {"cluster-a": stale}

        await sub._sync_owner_subscriptions()
        await asyncio.sleep(0)

        assert sub._owner_tasks == {}

    @pytest.mark.asyncio
    async def test_zero_adapters_are_not_recorded_as_reconciled(self) -> None:
        """An owner with no Volundr adapters yet must not look "already synced".

        If a zero-adapter owner were left in ``_owner_tasks`` (even as an
        empty entry), the next cycle would see it as a known owner and never
        call ``reconcile_owner`` again — so a PAT configured after the fact
        would never get its catch-up pass. reconcile_owner must keep firing
        every cycle until a cluster actually appears.
        """
        projector = AsyncMock()
        projector.list_active_owner_ids.return_value = [OWNER_ID]
        factory = MultiVolundrFactory([])
        sub = SessionActivitySubscriber(
            volundr_factory=factory,
            tracker_factory=MockTrackerFactory(trackers=[]),
            dispatcher_repo=StubDispatcherRepo(),
            event_bus=InMemoryEventBus(),
            config=_default_config(reconnect_delay=0),
            workflow_campaign_projector=projector,
        )

        await sub._sync_owner_subscriptions()
        assert OWNER_ID not in sub._owner_tasks
        projector.reconcile_owner.assert_awaited_once_with(OWNER_ID)

        await sub._sync_owner_subscriptions()
        assert OWNER_ID not in sub._owner_tasks
        assert projector.reconcile_owner.await_count == 2

        # A cluster finally appears (e.g. the owner just added a PAT).
        factory._adapters.append(NamedVolundr(name="ymir", target_id="ymir-1"))
        await sub._sync_owner_subscriptions()
        await asyncio.sleep(0)

        assert projector.reconcile_owner.await_count == 3
        assert "ymir-1" in sub._owner_tasks[OWNER_ID]


# ---------------------------------------------------------------------------
# Tests -- Activity event handling
# ---------------------------------------------------------------------------


class TestActivityEventHandling:
    @pytest.mark.asyncio
    async def test_terminal_activity_error_fails_run_with_worker_reason(self) -> None:
        sub, volundr, tracker, _ = _make_subscriber()
        event = ActivityEvent(
            session_id=SESSION_ID,
            state="error",
            metadata={"error": "refresh token was already used"},
            owner_id=OWNER_ID,
        )

        await sub._on_activity_event(event, volundr, OWNER_ID)

        tracker.update_run_progress.assert_awaited_once_with(
            TRACKER_ISSUE_ID,
            status=RunStatus.FAILED,
            reason="refresh token was already used",
        )

    @pytest.mark.asyncio
    async def test_idle_event_triggers_completion(self) -> None:
        """Idle event with sufficient turns completes the session."""
        sub, volundr, tracker, event_bus = _make_subscriber()

        event = ActivityEvent(
            session_id=SESSION_ID,
            state="idle",
            metadata={"turn_count": 5, "duration_seconds": 60},
            owner_id=OWNER_ID,
        )

        q = event_bus.subscribe()
        await sub._on_activity_event(event, volundr, OWNER_ID)

        # Wait for debounced evaluation (delay=0)
        await asyncio.sleep(0.1)

        tracker.update_run_progress.assert_called()
        progress_call = tracker.update_run_progress.call_args
        assert progress_call.args[0] == TRACKER_ISSUE_ID
        assert progress_call.kwargs["status"] == RunStatus.REVIEW

        bus_event = await asyncio.wait_for(q.get(), timeout=1.0)
        assert bus_event.event == "run.state_changed"
        assert bus_event.data["status"] == "REVIEW"

    @pytest.mark.asyncio
    async def test_idle_event_skips_flock_sessions_without_completion_metadata(self) -> None:
        """Flock sessions should not enter idle-based REVIEW evaluation on plain idle."""
        sub, volundr, tracker, _ = _make_subscriber()
        volundr.sessions[SESSION_ID] = _make_volundr_session(workload_type="ravn_flock")

        event = ActivityEvent(
            session_id=SESSION_ID,
            state="idle",
            metadata={"turn_count": 5, "duration_seconds": 60},
            owner_id=OWNER_ID,
        )

        await sub._on_activity_event(event, volundr, OWNER_ID)
        await asyncio.sleep(0.1)

        tracker.update_run_progress.assert_not_called()

    @pytest.mark.asyncio
    async def test_idle_event_routes_flock_completion_metadata_to_review_engine(self) -> None:
        """Authoritative flock completion should route through ReviewEngine."""
        review_engine = type("ReviewEngineStub", (), {})()
        review_engine.handle_ravn_outcome = AsyncMock()
        review_engine.get_reviewer_run = lambda session_id: None
        sub, volundr, tracker, _ = _make_subscriber(review_engine=review_engine)
        volundr.sessions[SESSION_ID] = _make_volundr_session(workload_type="ravn_flock")

        event = ActivityEvent(
            session_id=SESSION_ID,
            state="idle",
            metadata={
                "turn_count": 5,
                "duration_seconds": 60,
                "completion_source": "ravn_flock",
                "structured_outcome": {
                    "event_type": "ravn.task.completed",
                    "persona": "run-executor",
                    "success": True,
                    "outcome": {
                        "verdict": "approve",
                        "tests_passing": True,
                        "scope_adherence": 1.0,
                        "summary": "done",
                        "files_changed": ["tmp/e2e/proof.txt"],
                    },
                },
            },
            owner_id=OWNER_ID,
        )

        await sub._on_activity_event(event, volundr, OWNER_ID)
        await asyncio.sleep(0.1)

        tracker.update_run_progress.assert_not_called()
        review_engine.handle_ravn_outcome.assert_awaited_once()
        call = review_engine.handle_ravn_outcome.await_args
        assert call.args[0] == TRACKER_ISSUE_ID
        assert call.args[1] == OWNER_ID
        assert call.args[2].verdict == "approve"
        assert call.args[2].authoritative is False

    @pytest.mark.asyncio
    async def test_idle_event_persists_help_needed_and_emits_feedback_notification(self) -> None:
        sub, volundr, tracker, event_bus = _make_subscriber()
        volundr.sessions[SESSION_ID] = _make_volundr_session(workload_type="ravn_flock")

        event = ActivityEvent(
            session_id=SESSION_ID,
            state="idle",
            metadata={
                "turn_count": 5,
                "duration_seconds": 60,
                "help_needed": {
                    "summary": "Need operator approval before publication",
                    "reason": "require_human_confirmation",
                    "attempted": ["Completed review round"],
                    "recommendation": "Approve or request changes",
                    "persona": "council-chair",
                    "target_peer_id": "flock-council-chair",
                    "context": {"slug": "research/council"},
                },
            },
            owner_id=OWNER_ID,
        )

        q = event_bus.subscribe()
        await sub._on_activity_event(event, volundr, OWNER_ID)

        tracker.update_run_progress.assert_not_called()
        stored = tracker.messages[RUN_ID]
        assert len(stored) == 1
        assert stored[0].sender == "help_needed"
        assert '"target_peer_id": "flock-council-chair"' in stored[0].content

        bus_event = await asyncio.wait_for(q.get(), timeout=1.0)
        assert bus_event.event == "run.feedback_requested"
        assert bus_event.data["tracker_id"] == TRACKER_ISSUE_ID
        assert bus_event.data["summary"] == "Need operator approval before publication"

    @pytest.mark.asyncio
    async def test_help_needed_with_full_payload_session_updates_workflow_campaign(self) -> None:
        full_session_id = "a23145f7-afcb-4184-b683-a6b0a1d2efc5"
        campaign_repo = StubWorkflowCampaignRepo([_make_workflow_campaign(full_session_id)])
        tracker = MockTracker(runs_by_session={})
        sub, _volundr, _tracker, event_bus = _make_subscriber(
            tracker=tracker,
            workflow_campaign_repo=campaign_repo,
        )

        event = ActivityEvent(
            session_id="a23145f7",
            state="idle",
            metadata={
                "help_needed": {
                    "session_id": full_session_id,
                    "summary": "Plan is ready for human review.",
                    "reason": "needs_human_approval",
                    "recommendation": "Approve or request focused changes.",
                    "persona": "workflow-gate",
                    "context": {
                        "gate_id": "plan-review-gate:event_plan_breakdown_a23145f7:1",
                        "gate_node_id": "plan-review-gate",
                        "gate_status": "pending",
                        "instructions": "Approve the draft if it is bounded.",
                    },
                },
            },
            owner_id=OWNER_ID,
        )

        q = event_bus.subscribe()
        await sub._on_activity_event(event, _volundr, OWNER_ID)

        campaign = next(iter(campaign_repo.campaigns.values()))
        assert campaign.status == WorkflowCampaignStatus.BLOCKED
        assert campaign.active_stage_id == "plan-review-gate"
        assert campaign.metadata["pending_workflow_gates"] == [
            {
                "id": "plan-review-gate:event_plan_breakdown_a23145f7:1",
                "node_id": "plan-review-gate",
                "status": "pending",
                "summary": "Plan is ready for human review.",
                "instructions": "Approve the draft if it is bounded.",
                "reason": "needs_human_approval",
            }
        ]
        bus_event = await asyncio.wait_for(q.get(), timeout=1.0)
        if bus_event.event == "workflow.campaign.updated":
            bus_event = await asyncio.wait_for(q.get(), timeout=1.0)
        assert bus_event.event == "workflow.campaign.feedback_requested"
        assert bus_event.data["session_id"] == full_session_id

    @pytest.mark.asyncio
    async def test_idle_event_keeps_waiting_when_review_engine_missing(self) -> None:
        sub, volundr, tracker, _ = _make_subscriber(review_engine=None)
        volundr.sessions[SESSION_ID] = _make_volundr_session(workload_type="ravn_flock")

        event = ActivityEvent(
            session_id=SESSION_ID,
            state="idle",
            metadata={
                "turn_count": 5,
                "duration_seconds": 60,
                "completion_source": "ravn_flock",
                "structured_outcome": {"outcome": {"verdict": "approve"}},
            },
            owner_id=OWNER_ID,
        )

        await sub._on_activity_event(event, volundr, OWNER_ID)
        await asyncio.sleep(0.1)

        tracker.update_run_progress.assert_not_called()

    @pytest.mark.asyncio
    async def test_idle_event_keeps_waiting_when_completion_source_is_not_ravn_flock(self) -> None:
        review_engine = type("ReviewEngineStub", (), {})()
        review_engine.handle_ravn_outcome = AsyncMock()
        review_engine.get_reviewer_run = lambda session_id: None
        sub, volundr, tracker, _ = _make_subscriber(review_engine=review_engine)
        volundr.sessions[SESSION_ID] = _make_volundr_session(workload_type="ravn_flock")

        event = ActivityEvent(
            session_id=SESSION_ID,
            state="idle",
            metadata={
                "turn_count": 5,
                "duration_seconds": 60,
                "completion_source": "manual",
                "structured_outcome": {"outcome": {"verdict": "approve"}},
            },
            owner_id=OWNER_ID,
        )

        await sub._on_activity_event(event, volundr, OWNER_ID)
        await asyncio.sleep(0.1)

        tracker.update_run_progress.assert_not_called()
        review_engine.handle_ravn_outcome.assert_not_called()

    @pytest.mark.asyncio
    async def test_idle_event_keeps_waiting_when_structured_outcome_missing(self) -> None:
        review_engine = type("ReviewEngineStub", (), {})()
        review_engine.handle_ravn_outcome = AsyncMock()
        review_engine.get_reviewer_run = lambda session_id: None
        sub, volundr, tracker, _ = _make_subscriber(review_engine=review_engine)
        volundr.sessions[SESSION_ID] = _make_volundr_session(workload_type="ravn_flock")

        event = ActivityEvent(
            session_id=SESSION_ID,
            state="idle",
            metadata={
                "turn_count": 5,
                "duration_seconds": 60,
                "completion_source": "ravn_flock",
            },
            owner_id=OWNER_ID,
        )

        await sub._on_activity_event(event, volundr, OWNER_ID)
        await asyncio.sleep(0.1)

        tracker.update_run_progress.assert_not_called()
        review_engine.handle_ravn_outcome.assert_not_called()

    @pytest.mark.asyncio
    async def test_idle_event_keeps_waiting_when_nested_outcome_payload_is_empty(self) -> None:
        review_engine = type("ReviewEngineStub", (), {})()
        review_engine.handle_ravn_outcome = AsyncMock()
        review_engine.get_reviewer_run = lambda session_id: None
        sub, volundr, tracker, _ = _make_subscriber(review_engine=review_engine)
        volundr.sessions[SESSION_ID] = _make_volundr_session(workload_type="ravn_flock")

        event = ActivityEvent(
            session_id=SESSION_ID,
            state="idle",
            metadata={
                "turn_count": 5,
                "duration_seconds": 60,
                "completion_source": "ravn_flock",
                "structured_outcome": {"outcome": {}},
            },
            owner_id=OWNER_ID,
        )

        await sub._on_activity_event(event, volundr, OWNER_ID)
        await asyncio.sleep(0.1)

        tracker.update_run_progress.assert_not_called()
        review_engine.handle_ravn_outcome.assert_not_called()

    @pytest.mark.asyncio
    async def test_idle_event_accepts_flat_structured_outcome_and_coerces_fields(self) -> None:
        review_engine = type("ReviewEngineStub", (), {})()
        review_engine.handle_ravn_outcome = AsyncMock()
        review_engine.handle_workflow_completion = AsyncMock()
        review_engine.get_reviewer_run = lambda session_id: None
        sub, volundr, _, _ = _make_subscriber(review_engine=review_engine)
        volundr.sessions[SESSION_ID] = _make_volundr_session(workload_type="ravn_flock")

        event = ActivityEvent(
            session_id=SESSION_ID,
            state="idle",
            metadata={
                "turn_count": 5,
                "duration_seconds": 60,
                "completion_source": "ravn_flock",
                "files_changed": ["  src/one.py  ", "", "src/two.py"],
                "structured_outcome": {
                    "verdict": "approve",
                    "tests_passing": "yes",
                    "scope_adherence": "0.85",
                    "pr_url": " https://github.com/niuulabs/volundr/pull/713 ",
                    "summary": "  all good  ",
                    "checks": [
                        {
                            "persona": "reviewer",
                            "event_type": "review.completed",
                            "verdict": "pass",
                            "summary": "looks good",
                        }
                    ],
                },
                "completion_peer_id": "flock-reviewer",
            },
            owner_id=OWNER_ID,
        )

        await sub._on_activity_event(event, volundr, OWNER_ID)
        await asyncio.sleep(0.1)

        review_engine.handle_workflow_completion.assert_not_awaited()
        review_engine.handle_ravn_outcome.assert_awaited_once()
        outcome = review_engine.handle_ravn_outcome.await_args.args[2]
        assert outcome.tests_passing is True
        assert outcome.scope_adherence == pytest.approx(0.85)
        assert outcome.pr_url == "https://github.com/niuulabs/volundr/pull/713"
        assert outcome.files_changed == ["src/one.py", "src/two.py"]
        assert outcome.summary == "all good"
        assert outcome.authoritative is False
        assert outcome.checks == [
            {
                "persona": "reviewer",
                "event_type": "review.completed",
                "verdict": "pass",
                "summary": "looks good",
            }
        ]

    @pytest.mark.asyncio
    async def test_authoritative_workflow_completion_ignores_follow_up_stopped_event(self) -> None:
        config = _default_config(completion_check_delay=5.0)
        review_engine = type("ReviewEngineStub", (), {})()
        review_engine.handle_ravn_outcome = AsyncMock()
        review_engine.handle_workflow_completion = AsyncMock(return_value=True)
        review_engine.get_reviewer_run = lambda session_id: None
        sub, volundr, tracker, _ = _make_subscriber(config=config, review_engine=review_engine)
        volundr.sessions[SESSION_ID] = _make_volundr_session(
            workload_type="ravn_flock",
            status="running",
        )

        completion_event = ActivityEvent(
            session_id=SESSION_ID,
            state="idle",
            metadata={
                "turn_count": 5,
                "duration_seconds": 60,
                "completion_source": "ravn_flock",
                "completion_event_type": "delivery.completed",
                "completion_peer_id": "workflow-stop:delivery-complete",
                "outcome_valid": True,
                "structured_outcome": {
                    "verdict": "approve",
                    "authoritative": True,
                    "checks": [
                        {
                            "persona": "publisher",
                            "event_type": "delivery.completed",
                            "verdict": "published",
                        }
                    ],
                },
            },
            owner_id=OWNER_ID,
        )
        await sub._on_activity_event(completion_event, volundr, OWNER_ID)

        volundr.sessions[SESSION_ID] = _make_volundr_session(
            workload_type="ravn_flock",
            status="stopped",
        )
        stopped_event = ActivityEvent(
            session_id=SESSION_ID,
            state="",
            metadata={},
            owner_id=OWNER_ID,
            session_status="stopped",
        )
        await sub._on_activity_event(stopped_event, volundr, OWNER_ID)

        review_engine.handle_workflow_completion.assert_awaited_once_with(
            TRACKER_ISSUE_ID,
            OWNER_ID,
        )
        tracker.update_run_progress.assert_not_called()
        review_engine.handle_ravn_outcome.assert_not_called()

    @pytest.mark.asyncio
    async def test_idle_with_no_turns_does_not_complete(self) -> None:
        """Idle with turn_count <= 1 should not trigger completion."""
        sub, volundr, tracker, _ = _make_subscriber()

        event = ActivityEvent(
            session_id=SESSION_ID,
            state="idle",
            metadata={"turn_count": 0, "duration_seconds": 5},
            owner_id=OWNER_ID,
        )

        await sub._on_activity_event(event, volundr, OWNER_ID)
        await asyncio.sleep(0.1)

        tracker.update_run_progress.assert_not_called()

    @pytest.mark.asyncio
    async def test_active_event_cancels_pending_evaluation(self) -> None:
        """An active event should cancel any pending idle evaluation."""
        config = _default_config(completion_check_delay=1.0)
        sub, volundr, tracker, _ = _make_subscriber(config=config)

        idle_event = ActivityEvent(
            session_id=SESSION_ID,
            state="idle",
            metadata={"turn_count": 5, "duration_seconds": 60},
            owner_id=OWNER_ID,
        )
        await sub._on_activity_event(idle_event, volundr, OWNER_ID)
        assert SESSION_ID in sub._pending_evaluations

        # Active event cancels it
        active_event = ActivityEvent(
            session_id=SESSION_ID,
            state="active",
            metadata={},
            owner_id=OWNER_ID,
        )
        await sub._on_activity_event(active_event, volundr, OWNER_ID)
        assert SESSION_ID not in sub._pending_evaluations
        tracker.update_run_progress.assert_not_called()

    @pytest.mark.asyncio
    async def test_tool_executing_cancels_pending_evaluation(self) -> None:
        """A tool_executing event should cancel pending idle evaluation."""
        config = _default_config(completion_check_delay=1.0)
        sub, volundr, tracker, _ = _make_subscriber(config=config)

        idle_event = ActivityEvent(
            session_id=SESSION_ID,
            state="idle",
            metadata={"turn_count": 5, "duration_seconds": 60},
            owner_id=OWNER_ID,
        )
        await sub._on_activity_event(idle_event, volundr, OWNER_ID)

        tool_event = ActivityEvent(
            session_id=SESSION_ID,
            state="tool_executing",
            metadata={},
            owner_id=OWNER_ID,
        )
        await sub._on_activity_event(tool_event, volundr, OWNER_ID)
        assert SESSION_ID not in sub._pending_evaluations

    @pytest.mark.asyncio
    async def test_no_session_record_is_ignored(self) -> None:
        """Idle event for a session with no running record is ignored."""
        # Tracker has no run for "unknown-session"
        sub, volundr, tracker, _ = _make_subscriber()

        event = ActivityEvent(
            session_id="unknown-session",
            state="idle",
            metadata={"turn_count": 5, "duration_seconds": 60},
            owner_id=OWNER_ID,
        )
        await sub._on_activity_event(event, volundr, OWNER_ID)
        await asyncio.sleep(0.1)
        # No crash, no transitions
        tracker.update_run_progress.assert_not_called()


# ---------------------------------------------------------------------------
# Tests -- Completion evaluation
# ---------------------------------------------------------------------------


class TestCompletionEvaluationLogic:
    @pytest.mark.asyncio
    async def test_basic_completion(self) -> None:
        """Session idle with turns > 1 should evaluate as complete."""
        sub, volundr, _, _ = _make_subscriber()
        run = _make_run()
        volundr.pr_error_sessions.add(run.session_id)

        result = await sub._evaluate_completion(
            run, volundr, {"turn_count": 5, "duration_seconds": 60}
        )
        assert result.is_complete is True
        assert result.signals["session_idle"] is True
        assert result.signals["has_turns"] is True

    @pytest.mark.asyncio
    async def test_pr_exists_signal(self) -> None:
        """PR existence should be reflected in the completion signals."""
        sub, volundr, _, _ = _make_subscriber()
        run = _make_run()
        volundr.pr_statuses[run.session_id] = PRStatus(
            pr_id="PR-1",
            url="https://github.com/pull/1",
            state="open",
            mergeable=True,
            ci_passed=True,
        )

        result = await sub._evaluate_completion(
            run, volundr, {"turn_count": 5, "duration_seconds": 60}
        )
        assert result.is_complete is True
        assert result.signals["pr_exists"] is True
        assert result.signals["ci_passed"] is True

    @pytest.mark.asyncio
    async def test_require_pr_blocks_completion(self) -> None:
        """When require_pr=True and no PR, should not complete."""
        config = _default_config(require_pr=True)
        sub, volundr, _, _ = _make_subscriber(config=config)
        run = _make_run()
        volundr.pr_error_sessions.add(run.session_id)

        result = await sub._evaluate_completion(
            run, volundr, {"turn_count": 5, "duration_seconds": 60}
        )
        assert result.is_complete is False

    @pytest.mark.asyncio
    async def test_require_ci_blocks_completion(self) -> None:
        """When require_ci=True and CI not passed, should not complete."""
        config = _default_config(require_ci=True)
        sub, volundr, _, _ = _make_subscriber(config=config)
        run = _make_run()
        volundr.pr_statuses[run.session_id] = PRStatus(
            pr_id="PR-1",
            url="url",
            state="open",
            mergeable=True,
            ci_passed=False,
        )

        result = await sub._evaluate_completion(
            run, volundr, {"turn_count": 5, "duration_seconds": 60}
        )
        assert result.is_complete is False

    @pytest.mark.asyncio
    async def test_extended_idle_signal(self) -> None:
        """Duration above threshold should set the extended_idle signal."""
        config = _default_config(idle_threshold=10.0)
        sub, volundr, _, _ = _make_subscriber(config=config)
        run = _make_run()
        volundr.pr_error_sessions.add(run.session_id)

        result = await sub._evaluate_completion(
            run, volundr, {"turn_count": 5, "duration_seconds": 120}
        )
        assert result.signals["extended_idle"] is True

    @pytest.mark.asyncio
    async def test_no_pr_still_evaluates(self) -> None:
        """Session without PR should still evaluate (pr_exists=False)."""
        sub, volundr, _, _ = _make_subscriber()
        run = _make_run()
        volundr.pr_error_sessions.add(run.session_id)

        result = await sub._evaluate_completion(
            run, volundr, {"turn_count": 5, "duration_seconds": 60}
        )
        assert result.is_complete is True
        assert result.signals["pr_exists"] is False


# ---------------------------------------------------------------------------
# Tests -- Completion handling
# ---------------------------------------------------------------------------


class TestCompletionHandling:
    @pytest.mark.asyncio
    async def test_pr_info_stored_on_completion(self) -> None:
        """PR URL and ID should be emitted when PR is detected."""
        sub, volundr, tracker, event_bus = _make_subscriber()

        run = _make_run()
        evaluation = CompletionEvaluation(
            is_complete=True,
            signals={
                "session_idle": True,
                "has_turns": True,
                "pr_exists": True,
            },
            pr_id="PR-42",
            pr_url="https://github.com/org/repo/pull/42",
        )

        q = event_bus.subscribe()
        await sub._handle_completion(run, tracker, volundr, OWNER_ID, evaluation)

        tracker.update_run_progress.assert_called_once()
        progress_call = tracker.update_run_progress.call_args
        assert progress_call.args[0] == TRACKER_ISSUE_ID
        assert progress_call.kwargs["status"] == RunStatus.REVIEW
        assert progress_call.kwargs["pr_id"] == "PR-42"
        assert progress_call.kwargs["pr_url"] == "https://github.com/org/repo/pull/42"

        bus_event = await asyncio.wait_for(q.get(), timeout=1.0)
        assert bus_event.data["pr_id"] == "PR-42"
        assert bus_event.data["pr_url"] == "https://github.com/org/repo/pull/42"

    @pytest.mark.asyncio
    async def test_no_pr_still_transitions(self) -> None:
        """Completion without a PR should still transition to REVIEW."""
        sub, volundr, tracker, _ = _make_subscriber()

        run = _make_run()
        evaluation = CompletionEvaluation(
            is_complete=True,
            signals={"session_idle": True, "has_turns": True},
        )

        await sub._handle_completion(run, tracker, volundr, OWNER_ID, evaluation)

        tracker.update_run_progress.assert_called_once()
        progress_call = tracker.update_run_progress.call_args
        assert progress_call.kwargs["status"] == RunStatus.REVIEW

    @pytest.mark.asyncio
    async def test_event_emitted_on_completion(self) -> None:
        """Event bus should receive run.state_changed on completion."""
        sub, volundr, tracker, event_bus = _make_subscriber()

        run = _make_run()
        q = event_bus.subscribe()
        await sub._handle_completion(run, tracker, volundr, OWNER_ID)

        bus_event = await asyncio.wait_for(q.get(), timeout=1.0)
        assert bus_event.event == "run.state_changed"
        assert bus_event.data["session_id"] == SESSION_ID
        assert bus_event.data["status"] == "REVIEW"


# ---------------------------------------------------------------------------
# Tests -- Dispatcher pause filtering
# ---------------------------------------------------------------------------


class TestDispatcherPauseFiltering:
    @pytest.mark.asyncio
    async def test_paused_dispatcher_skips_completion(self) -> None:
        """Sessions belonging to paused owners should not complete."""
        dispatcher_repo = StubDispatcherRepo(running=False)

        sub, volundr, tracker, _ = _make_subscriber(dispatcher_repo=dispatcher_repo)

        event = ActivityEvent(
            session_id=SESSION_ID,
            state="idle",
            metadata={"turn_count": 5, "duration_seconds": 60},
            owner_id="user-paused",
        )
        await sub._on_activity_event(event, volundr, OWNER_ID)
        await asyncio.sleep(0.1)

        tracker.update_run_progress.assert_not_called()

    @pytest.mark.asyncio
    async def test_running_dispatcher_allows_completion(self) -> None:
        """Sessions belonging to running owners should complete normally."""
        dispatcher_repo = StubDispatcherRepo(running=True)

        sub, volundr, tracker, _ = _make_subscriber(dispatcher_repo=dispatcher_repo)

        event = ActivityEvent(
            session_id=SESSION_ID,
            state="idle",
            metadata={"turn_count": 5, "duration_seconds": 60},
            owner_id="user-active",
        )
        await sub._on_activity_event(event, volundr, OWNER_ID)
        await asyncio.sleep(0.1)

        tracker.update_run_progress.assert_called()
        progress_call = tracker.update_run_progress.call_args
        assert progress_call.kwargs["status"] == RunStatus.REVIEW


# ---------------------------------------------------------------------------
# Tests -- Failure detection
# ---------------------------------------------------------------------------


class TestFailureDetection:
    @pytest.mark.asyncio
    async def test_session_stopped_transitions_to_failed(self) -> None:
        """A session_status=stopped event should mark the session failed."""
        sub, volundr, tracker, event_bus = _make_subscriber()

        event = ActivityEvent(
            session_id=SESSION_ID,
            state="",
            metadata={},
            owner_id=OWNER_ID,
            session_status="stopped",
        )

        q = event_bus.subscribe()
        await sub._on_activity_event(event, volundr, OWNER_ID)

        tracker.update_run_progress.assert_called_once()
        progress_call = tracker.update_run_progress.call_args
        assert progress_call.kwargs["status"] == RunStatus.FAILED

        bus_event = await asyncio.wait_for(q.get(), timeout=1.0)
        assert bus_event.event == "run.state_changed"
        assert bus_event.data["status"] == "FAILED"

    @pytest.mark.asyncio
    async def test_session_failed_transitions_to_failed(self) -> None:
        """A session_status=failed event should mark the session failed."""
        sub, volundr, tracker, _ = _make_subscriber()

        event = ActivityEvent(
            session_id=SESSION_ID,
            state="",
            metadata={},
            owner_id=OWNER_ID,
            session_status="failed",
        )

        await sub._on_activity_event(event, volundr, OWNER_ID)

        tracker.update_run_progress.assert_called_once()
        progress_call = tracker.update_run_progress.call_args
        assert progress_call.kwargs["status"] == RunStatus.FAILED

    @pytest.mark.asyncio
    async def test_session_failed_cancels_pending_evaluation(self) -> None:
        """A failure event should cancel any pending idle evaluation."""
        config = _default_config(completion_check_delay=1.0)
        sub, volundr, tracker, _ = _make_subscriber(config=config)

        # Schedule an idle evaluation
        idle_event = ActivityEvent(
            session_id=SESSION_ID,
            state="idle",
            metadata={"turn_count": 5, "duration_seconds": 60},
            owner_id=OWNER_ID,
        )
        await sub._on_activity_event(idle_event, volundr, OWNER_ID)
        assert SESSION_ID in sub._pending_evaluations

        # Session crashes
        fail_event = ActivityEvent(
            session_id=SESSION_ID,
            state="",
            metadata={},
            owner_id=OWNER_ID,
            session_status="failed",
        )
        await sub._on_activity_event(fail_event, volundr, OWNER_ID)

        assert SESSION_ID not in sub._pending_evaluations
        tracker.update_run_progress.assert_called_once()
        progress_call = tracker.update_run_progress.call_args
        assert progress_call.kwargs["status"] == RunStatus.FAILED

    @pytest.mark.asyncio
    async def test_session_not_found_during_eval_fails(self) -> None:
        """If get_session returns None during evaluation, session fails."""
        sub, volundr, tracker, _ = _make_subscriber()

        # Remove the session so get_session returns None
        volundr.sessions.pop(SESSION_ID, None)

        event = ActivityEvent(
            session_id=SESSION_ID,
            state="idle",
            metadata={"turn_count": 5, "duration_seconds": 60},
            owner_id=OWNER_ID,
        )
        await sub._on_activity_event(event, volundr, OWNER_ID)
        await asyncio.sleep(0.1)

        tracker.update_run_progress.assert_called_once()
        progress_call = tracker.update_run_progress.call_args
        assert progress_call.kwargs["status"] == RunStatus.FAILED

    @pytest.mark.asyncio
    async def test_session_stopped_during_eval_fails(self) -> None:
        """If get_session returns stopped during evaluation, session fails."""
        sub, volundr, tracker, _ = _make_subscriber()

        # Session is stopped
        volundr.sessions[SESSION_ID] = _make_volundr_session(
            session_id=SESSION_ID, status="stopped"
        )

        event = ActivityEvent(
            session_id=SESSION_ID,
            state="idle",
            metadata={"turn_count": 5, "duration_seconds": 60},
            owner_id=OWNER_ID,
        )
        await sub._on_activity_event(event, volundr, OWNER_ID)
        await asyncio.sleep(0.1)

        tracker.update_run_progress.assert_called_once()
        progress_call = tracker.update_run_progress.call_args
        assert progress_call.kwargs["status"] == RunStatus.FAILED

    @pytest.mark.asyncio
    async def test_failure_no_session_record_is_ignored(self) -> None:
        """A failure event for an unknown session should be ignored."""
        sub, volundr, tracker, _ = _make_subscriber()

        event = ActivityEvent(
            session_id="unknown-session",
            state="",
            metadata={},
            owner_id=OWNER_ID,
            session_status="stopped",
        )
        await sub._on_activity_event(event, volundr, OWNER_ID)
        # No crash, no transitions
        tracker.update_run_progress.assert_not_called()

    @pytest.mark.asyncio
    async def test_event_emitted_on_failure(self) -> None:
        """Event bus should receive run.state_changed on failure."""
        sub, volundr, tracker, event_bus = _make_subscriber()
        run = _make_run()

        q = event_bus.subscribe()
        await sub._handle_failure(run, tracker, OWNER_ID, reason="Session stopped")

        bus_event = await asyncio.wait_for(q.get(), timeout=1.0)
        assert bus_event.event == "run.state_changed"
        assert bus_event.data["session_id"] == SESSION_ID
        assert bus_event.data["status"] == "FAILED"

    @pytest.mark.asyncio
    async def test_failure_delegates_to_review_engine_and_refills_capacity(self) -> None:
        review_engine = type("ReviewEngineStub", (), {})()
        review_engine.handle_run_failure = AsyncMock(return_value=True)
        review_engine.get_reviewer_run = lambda session_id: None
        sub, _, tracker, _ = _make_subscriber(review_engine=review_engine)
        run = _make_run()

        await sub._handle_failure(run, tracker, OWNER_ID, reason="Session stopped")

        review_engine.handle_run_failure.assert_awaited_once_with(
            TRACKER_ISSUE_ID,
            OWNER_ID,
            reason="Session stopped",
        )
        tracker.update_run_progress.assert_not_called()

    @pytest.mark.asyncio
    async def test_reconcile_running_runs_fails_stale_stopped_sessions(self) -> None:
        run = _make_run(session_id="stale-session", tracker_id="issue-stale")
        volundr = NamedVolundr(name="ymir", target_id="ymir-1")
        volundr.sessions["stale-session"] = _make_volundr_session(
            session_id="stale-session",
            status="stopped",
        )
        tracker = MockTracker(runs_by_session={"stale-session": run})
        sub, _, _, _ = _make_subscriber(volundr=volundr, tracker=tracker, run=run)

        await sub._reconcile_running_runs(OWNER_ID)

        tracker.update_run_progress.assert_called_once()
        progress_call = tracker.update_run_progress.call_args
        assert progress_call.args[0] == "issue-stale"
        assert progress_call.kwargs["status"] == RunStatus.FAILED
        assert progress_call.kwargs["reason"] == "Session stopped"

    @pytest.mark.asyncio
    async def test_reconcile_running_runs_ignores_a_stranger_clusters_404(self) -> None:
        """A ``Run`` doesn't record which Forge cluster its session lives on.

        Regression test for the pre-existing bug: reconcile used to ask
        whichever single cluster happened to be reconnecting about every
        owner run, so a cluster that never had a given session (a 404)
        could fail a run that is perfectly healthy on a different cluster.
        With two clusters registered, only the one that actually knows the
        session may speak for it.
        """
        run = _make_run(session_id="shared-session", tracker_id="issue-two-clusters")
        tracker = MockTracker(runs_by_session={"shared-session": run})

        owning_cluster = NamedVolundr(name="ymir", target_id="ymir-1")
        owning_cluster.sessions["shared-session"] = _make_volundr_session(
            session_id="shared-session", status="running"
        )
        stranger_cluster = NamedVolundr(name="valhalla", target_id="valhalla-1")
        # stranger_cluster never heard of "shared-session" (get_session -> None).

        sub = SessionActivitySubscriber(
            volundr_factory=MultiVolundrFactory([stranger_cluster, owning_cluster]),
            tracker_factory=MockTrackerFactory(trackers=[tracker]),
            dispatcher_repo=StubDispatcherRepo(),
            event_bus=InMemoryEventBus(),
            config=_default_config(),
        )

        await sub._reconcile_running_runs(OWNER_ID)

        tracker.update_run_progress.assert_not_called()

    @pytest.mark.asyncio
    async def test_reconcile_running_runs_fails_when_no_cluster_knows_the_session(self) -> None:
        run = _make_run(session_id="orphan-session", tracker_id="issue-orphan")
        tracker = MockTracker(runs_by_session={"orphan-session": run})

        cluster_a = NamedVolundr(name="ymir", target_id="ymir-1")
        cluster_b = NamedVolundr(name="valhalla", target_id="valhalla-1")
        # Neither cluster has ever heard of "orphan-session".

        sub = SessionActivitySubscriber(
            volundr_factory=MultiVolundrFactory([cluster_a, cluster_b]),
            tracker_factory=MockTrackerFactory(trackers=[tracker]),
            dispatcher_repo=StubDispatcherRepo(),
            event_bus=InMemoryEventBus(),
            config=_default_config(),
        )

        await sub._reconcile_running_runs(OWNER_ID)

        tracker.update_run_progress.assert_called_once()
        progress_call = tracker.update_run_progress.call_args
        expected_reason = "Session not found on any registered Volundr cluster"
        assert progress_call.args[0] == "issue-orphan"
        assert progress_call.kwargs["status"] == RunStatus.FAILED
        assert progress_call.kwargs["reason"] == expected_reason


# ---------------------------------------------------------------------------
# Tests -- Per-cluster failure isolation and backoff
# ---------------------------------------------------------------------------


class TestPerClusterIsolationAndBackoff:
    def _build_subscriber(
        self,
        adapters: list[VolundrPort],
        *,
        owner_ids: list[str] | None = None,
        config: WatcherConfig | None = None,
    ) -> SessionActivitySubscriber:
        tracker = MockTracker(runs_by_session={})
        resolved_owner_ids = [OWNER_ID] if owner_ids is None else owner_ids
        return SessionActivitySubscriber(
            volundr_factory=MultiVolundrFactory(adapters),
            tracker_factory=MockTrackerFactory(trackers=[tracker]),
            dispatcher_repo=ActiveOwnersDispatcherRepo(resolved_owner_ids),
            event_bus=InMemoryEventBus(),
            config=config
            or _default_config(
                reconnect_delay=0,
                reconnect_initial_delay=0,
                reconnect_max_delay=0,
                reconnect_jitter=0,
            ),
        )

    @pytest.mark.asyncio
    async def test_one_failing_cluster_does_not_cancel_the_others(self) -> None:
        """A cluster's task is a single connect attempt (see
        ``_adapter_subscription_loop``): it records a failure and returns,
        and sync rebuilds it. This drives several sync cycles — with zero
        backoff/reconnect delay so each rebuild is immediate — and checks
        that the flaky cluster's repeated failures never cancel or
        otherwise disturb the healthy cluster's tasks.
        """
        healthy = NamedVolundr(name="ymir", target_id="ymir-1")
        flaky = NamedVolundr(
            name="laptop",
            target_id="laptop-1",
            base_url="http://192.168.1.38:8080",
            fail_times=10_000,
            error_factory=lambda: ConnectionRefusedError("refused"),
        )
        sub = self._build_subscriber([healthy, flaky])
        sub._running = True

        for _ in range(5):
            await sub._sync_owner_subscriptions()
            await asyncio.gather(*sub._owner_tasks[OWNER_ID].values())

        clusters = sub._owner_tasks[OWNER_ID]
        assert set(clusters) == {"ymir-1", "laptop-1"}
        assert not clusters["ymir-1"].cancelled()
        assert not clusters["laptop-1"].cancelled()
        # The flaky cluster accumulated failures; the healthy one has none.
        assert sub._cluster_backoff[(OWNER_ID, "laptop-1")].consecutive_failures >= 3
        assert (OWNER_ID, "ymir-1") not in sub._cluster_backoff
        assert healthy.attempts >= 3
        assert flaky.attempts >= 3

        sub._running = False

    def test_backoff_grows_and_caps(self) -> None:
        config = _default_config(
            reconnect_initial_delay=1.0,
            reconnect_backoff_multiplier=2.0,
            reconnect_max_delay=10.0,
            reconnect_jitter=0.0,
        )
        sub = self._build_subscriber([], config=config)

        delays = [sub._compute_backoff_delay(attempt) for attempt in range(1, 7)]

        assert delays == [1.0, 2.0, 4.0, 8.0, 10.0, 10.0]

    def test_backoff_jitter_stays_within_bounds(self) -> None:
        config = _default_config(
            reconnect_initial_delay=10.0,
            reconnect_backoff_multiplier=1.0,
            reconnect_max_delay=10.0,
            reconnect_jitter=0.5,
        )
        sub = self._build_subscriber([], config=config)

        for _ in range(20):
            delay = sub._compute_backoff_delay(1)
            assert 5.0 <= delay <= 10.0

    def test_backoff_resets_after_success(self) -> None:
        sub = self._build_subscriber([])

        sub._on_cluster_failure(OWNER_ID, "cluster-a", "cluster-a (http://x)", RuntimeError("x"))
        sub._on_cluster_failure(OWNER_ID, "cluster-a", "cluster-a (http://x)", RuntimeError("x"))
        assert sub._cluster_backoff[(OWNER_ID, "cluster-a")].consecutive_failures == 2

        sub._on_cluster_connected(OWNER_ID, "cluster-a", "cluster-a (http://x)")
        assert (OWNER_ID, "cluster-a") not in sub._cluster_backoff

        # A fresh failure after recovery starts back at attempt 1, not 3.
        delay = sub._on_cluster_failure(
            OWNER_ID, "cluster-a", "cluster-a (http://x)", RuntimeError("x")
        )
        assert sub._cluster_backoff[(OWNER_ID, "cluster-a")].consecutive_failures == 1
        assert delay == pytest.approx(sub._compute_backoff_delay(1), abs=0.05)

    def test_first_failure_logs_error_with_traceback_and_cluster_identity(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        sub = self._build_subscriber([])
        label = "laptop (http://192.168.1.38:8080)"

        with caplog.at_level(logging.ERROR, logger="ting.domain.services.activity_subscriber"):
            try:
                raise ConnectionRefusedError("refused")
            except ConnectionRefusedError as exc:
                sub._on_cluster_failure(OWNER_ID, "laptop-1", label, exc)

        records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(records) == 1
        assert records[0].exc_info is not None
        assert "laptop (http://192.168.1.38:8080)" in records[0].getMessage()

    def test_continued_failures_log_one_warning_line_without_traceback(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        sub = self._build_subscriber([])
        label = "laptop (http://192.168.1.38:8080)"

        with caplog.at_level(logging.WARNING, logger="ting.domain.services.activity_subscriber"):
            sub._on_cluster_failure(OWNER_ID, "laptop-1", label, RuntimeError("first"))
            caplog.clear()
            sub._on_cluster_failure(OWNER_ID, "laptop-1", label, ValueError("second failure"))

        records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(records) == 1
        record = records[0]
        assert record.exc_info is None
        message = record.getMessage()
        assert "laptop (http://192.168.1.38:8080)" in message
        assert "ValueError" in message
        assert "second failure" in message
        assert "attempt 2" in message

    def test_recovery_logs_info(self, caplog: pytest.LogCaptureFixture) -> None:
        sub = self._build_subscriber([])
        label = "laptop (http://192.168.1.38:8080)"
        sub._on_cluster_failure(OWNER_ID, "laptop-1", label, RuntimeError("x"))

        with caplog.at_level(logging.INFO, logger="ting.domain.services.activity_subscriber"):
            sub._on_cluster_connected(OWNER_ID, "laptop-1", label)

        records = [r for r in caplog.records if r.levelno == logging.INFO]
        assert len(records) == 1
        assert "recovered" in records[0].getMessage()
        assert "laptop (http://192.168.1.38:8080)" in records[0].getMessage()

    def test_recovery_is_a_noop_without_a_prior_failure(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        sub = self._build_subscriber([])

        with caplog.at_level(logging.INFO, logger="ting.domain.services.activity_subscriber"):
            sub._on_cluster_connected(OWNER_ID, "laptop-1", "laptop (http://x)")

        assert [r for r in caplog.records if "recovered" in r.getMessage()] == []

    @pytest.mark.asyncio
    async def test_owner_removal_cancels_every_cluster_for_that_owner(self) -> None:
        sub = self._build_subscriber([], owner_ids=[])
        task_a = asyncio.create_task(asyncio.sleep(60))
        task_b = asyncio.create_task(asyncio.sleep(60))
        sub._owner_tasks[OWNER_ID] = {"cluster-a": task_a, "cluster-b": task_b}
        sub._cluster_backoff[(OWNER_ID, "cluster-a")] = _ClusterBackoffState(consecutive_failures=3)

        await sub._sync_owner_subscriptions()
        await asyncio.sleep(0)

        assert OWNER_ID not in sub._owner_tasks
        assert (OWNER_ID, "cluster-a") not in sub._cluster_backoff
        assert task_a.cancelled()
        assert task_b.cancelled()

    def test_cluster_key_raises_without_identity(self) -> None:
        """No shared 'unknown' placeholder — an unidentified adapter is a hard error."""
        volundr = StubVolundr()  # base VolundrPort defaults: name="" and target_id=""

        with pytest.raises(ValueError, match="neither target_id nor name"):
            SessionActivitySubscriber._cluster_key(volundr)

    @pytest.mark.asyncio
    async def test_duplicate_cluster_key_raises_instead_of_silently_dropping_one(self) -> None:
        dup_a = NamedVolundr(name="ymir", target_id="dup-1")
        dup_b = NamedVolundr(name="ymir-mirror", target_id="dup-1")
        sub = self._build_subscriber([dup_a, dup_b])

        with pytest.raises(DuplicateVolundrClusterError, match="dup-1"):
            await sub._sync_owner_clusters(OWNER_ID)

    @pytest.mark.asyncio
    async def test_one_owners_sync_failure_does_not_abort_other_owners(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        good_owner = "user-good"
        bad_owner = "user-bad"
        healthy = NamedVolundr(name="ymir", target_id="ymir-1")
        sub = SessionActivitySubscriber(
            volundr_factory=RaisingForOneOwnerFactory(bad_owner, [healthy]),
            tracker_factory=MockTrackerFactory(trackers=[]),
            dispatcher_repo=ActiveOwnersDispatcherRepo([good_owner, bad_owner]),
            event_bus=InMemoryEventBus(),
            config=_default_config(reconnect_delay=0),
        )

        with caplog.at_level(logging.ERROR, logger="ting.domain.services.activity_subscriber"):
            await sub._sync_owner_subscriptions()
        await asyncio.sleep(0)

        assert "ymir-1" in sub._owner_tasks[good_owner]
        assert bad_owner not in sub._owner_tasks
        error_messages = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
        assert any(bad_owner[:8] in message for message in error_messages)

    @pytest.mark.asyncio
    async def test_orphaned_task_exits_without_doing_any_work(self) -> None:
        """A task no longer recorded as the tracked one for its cluster must not run.

        Simulates the defensive scenario the identity check guards: some
        other task is (or becomes) the one ``_owner_tasks`` points to for
        this ``(owner, key)`` before this one gets to run — it must not
        subscribe, reconcile, or handle events.
        """
        volundr = NamedVolundr(name="ymir", target_id="ymir-1")
        sub = self._build_subscriber([volundr])
        sub._running = True
        placeholder = asyncio.create_task(asyncio.sleep(60))
        sub._owner_tasks[OWNER_ID] = {"ymir-1": placeholder}

        orphan = asyncio.create_task(sub._adapter_subscription_loop(OWNER_ID, volundr))
        orphan_result = await orphan

        assert orphan_result is None
        assert volundr.attempts == 0

        placeholder.cancel()
        await asyncio.sleep(0)

    # -- Round-3 review fixes -------------------------------------------

    @pytest.mark.asyncio
    async def test_reconcile_owner_runs_again_after_a_cluster_resubscribes_following_failure(
        self,
    ) -> None:
        """reconcile_owner must fire again for a resubscribe after a failure.

        It previously only ran once, for a brand-new owner — so a campaign
        terminal event missed while one cluster was down was never caught
        up once that cluster came back.
        """
        flaky = NamedVolundr(
            name="laptop",
            target_id="laptop-1",
            fail_times=1,
            error_factory=lambda: ConnectionRefusedError("refused"),
        )
        projector = AsyncMock()
        projector.list_active_owner_ids = AsyncMock(return_value=[])
        sub = self._build_subscriber([flaky])
        sub._workflow_campaign_projector = projector
        sub._running = True

        await sub._sync_owner_subscriptions()  # new owner -> reconcile_owner #1; cluster fails
        await asyncio.gather(*sub._owner_tasks[OWNER_ID].values())
        assert projector.reconcile_owner.await_count == 1

        await sub._sync_owner_subscriptions()  # cluster due -> reconcile_owner #2 (the fix)
        await asyncio.gather(*sub._owner_tasks[OWNER_ID].values())

        assert projector.reconcile_owner.await_count == 2
        projector.reconcile_owner.assert_awaited_with(OWNER_ID)

    @pytest.mark.asyncio
    async def test_reconcile_run_session_skips_when_a_cluster_errors_during_lookup(self) -> None:
        """A cluster that errors while being asked leaves the run's status unknown.

        It must never be treated as a 404, and the error must never
        propagate out of this call — a peer cluster's outage is not this
        run's business.
        """
        run = _make_run(session_id="mystery-session", tracker_id="issue-mystery")
        tracker = MockTracker(runs_by_session={"mystery-session": run})
        down_cluster = NamedVolundr(name="ymir", target_id="ymir-1")
        down_cluster.get_session_error = ConnectionRefusedError("ymir is down")
        clean_cluster = NamedVolundr(name="valhalla", target_id="valhalla-1")
        sub = self._build_subscriber([down_cluster, clean_cluster])

        await sub._reconcile_run_session(
            run, tracker, OWNER_ID, [down_cluster, clean_cluster], unresolved=0
        )

        tracker.update_run_progress.assert_not_called()

    @pytest.mark.asyncio
    async def test_reconcile_error_on_one_cluster_does_not_fail_a_peer_clusters_subscription(
        self,
    ) -> None:
        """A down cluster's error during the shared reconcile pass must not get
        misattributed to a healthy peer cluster's own subscription — the
        peer's ``_adapter_subscription_loop`` must complete normally.
        """
        run = _make_run(session_id="shared-session", tracker_id="issue-shared")
        tracker = MockTracker(runs_by_session={"shared-session": run})
        down_cluster = NamedVolundr(name="ymir", target_id="ymir-1")
        down_cluster.get_session_error = ConnectionRefusedError("ymir is down")
        healthy_cluster = NamedVolundr(name="valhalla", target_id="valhalla-1")
        sub = SessionActivitySubscriber(
            volundr_factory=MultiVolundrFactory([down_cluster, healthy_cluster]),
            tracker_factory=MockTrackerFactory(trackers=[tracker]),
            dispatcher_repo=ActiveOwnersDispatcherRepo([OWNER_ID]),
            event_bus=InMemoryEventBus(),
            config=_default_config(
                reconnect_delay=0,
                reconnect_initial_delay=0,
                reconnect_max_delay=0,
                reconnect_jitter=0,
            ),
        )
        sub._running = True
        task = asyncio.create_task(sub._adapter_subscription_loop(OWNER_ID, healthy_cluster))
        sub._owner_tasks[OWNER_ID] = {"valhalla-1": task}

        task_result = await task

        assert task_result is None
        assert (OWNER_ID, "valhalla-1") not in sub._cluster_backoff
        tracker.update_run_progress.assert_not_called()

    @pytest.mark.asyncio
    async def test_reconcile_skips_querying_a_cluster_currently_in_backoff(self) -> None:
        """A cluster with recorded backoff state has an unknown status —

        it must not be queried at all (its own connect/read timeout would
        otherwise delay this reconcile pass for no benefit; its recovery
        is its own subscription loop's job), and skipping it must not fail
        the run either.
        """
        run = _make_run(session_id="mystery-session", tracker_id="issue-mystery")
        tracker = MockTracker(runs_by_session={"mystery-session": run})
        backing_off = NamedVolundr(name="ymir", target_id="ymir-1")
        healthy = NamedVolundr(name="valhalla", target_id="valhalla-1")
        sub = self._build_subscriber([backing_off, healthy])
        sub._cluster_backoff[(OWNER_ID, "ymir-1")] = _ClusterBackoffState(
            consecutive_failures=1, next_retry_at=time.monotonic() + 60.0
        )

        await sub._reconcile_run_session(
            run, tracker, OWNER_ID, [backing_off, healthy], unresolved=0
        )

        assert backing_off.get_session_calls == 0
        assert healthy.get_session_calls == 1
        tracker.update_run_progress.assert_not_called()

    @pytest.mark.asyncio
    async def test_reconcile_queries_multiple_clusters_concurrently(self) -> None:
        """A slow cluster must not delay the others — every queryable

        cluster is asked at once, not one after another. Two clusters each
        sleeping 0.1s must together take about 0.1s, not 0.2s.
        """
        run = _make_run(session_id="mystery-session", tracker_id="issue-mystery")
        tracker = MockTracker(runs_by_session={"mystery-session": run})
        slow_a = NamedVolundr(name="ymir", target_id="ymir-1")
        slow_a.get_session_delay = 0.1
        slow_b = NamedVolundr(name="valhalla", target_id="valhalla-1")
        slow_b.get_session_delay = 0.1
        sub = self._build_subscriber([slow_a, slow_b])

        started_at = time.monotonic()
        await sub._reconcile_run_session(run, tracker, OWNER_ID, [slow_a, slow_b], unresolved=0)
        elapsed = time.monotonic() - started_at

        assert elapsed < 0.18, (
            f"took {elapsed:.3f}s — clusters were queried serially, not concurrently"
        )
        assert slow_a.get_session_calls == 1
        assert slow_b.get_session_calls == 1

    @pytest.mark.asyncio
    async def test_reconcile_run_session_skips_when_a_cluster_is_unresolved(self) -> None:
        """A cluster the factory could not build an adapter for was never
        queried — "not found on every adapter we got" must not become
        "not found on every registered cluster".
        """
        run = _make_run(session_id="mystery-session", tracker_id="issue-mystery")
        tracker = MockTracker(runs_by_session={"mystery-session": run})
        known_cluster = NamedVolundr(name="ymir", target_id="ymir-1")
        sub = self._build_subscriber([known_cluster])

        await sub._reconcile_run_session(run, tracker, OWNER_ID, [known_cluster], unresolved=1)

        tracker.update_run_progress.assert_not_called()

    @pytest.mark.asyncio
    async def test_reconcile_run_session_fails_when_every_cluster_answered_and_none_know_it(
        self,
    ) -> None:
        """Regression guard: the original correct-failure path still works
        once every registered cluster is accounted for and none of them
        errored or went unresolved.
        """
        run = _make_run(session_id="mystery-session", tracker_id="issue-mystery")
        tracker = MockTracker(runs_by_session={"mystery-session": run})
        known_cluster = NamedVolundr(name="ymir", target_id="ymir-1")
        sub = self._build_subscriber([known_cluster])

        await sub._reconcile_run_session(run, tracker, OWNER_ID, [known_cluster], unresolved=0)

        tracker.update_run_progress.assert_called_once()
        progress_call = tracker.update_run_progress.call_args
        assert progress_call.kwargs["status"] == RunStatus.FAILED

    @pytest.mark.asyncio
    async def test_resolve_owner_adapters_for_reconcile_uses_factory_unresolved_count(
        self,
    ) -> None:
        sub = self._build_subscriber([])
        sub._factory = MultiVolundrFactory(
            [NamedVolundr(name="ymir", target_id="ymir-1")], unresolved=2
        )

        adapters, unresolved = await sub._resolve_owner_adapters_for_reconcile(OWNER_ID)

        assert len(adapters) == 1
        assert unresolved == 2

    @pytest.mark.asyncio
    async def test_resolve_owner_adapters_for_reconcile_has_no_getattr_fallback(self) -> None:
        """for_owner_with_unresolved is a required VolundrFactory port method,

        called directly — a factory that doesn't implement it fails loudly
        (AttributeError) rather than being silently degraded through a
        getattr-based fallback (see .claude/rules/no-fallbacks.md).
        """

        class BareForOwnerFactory:
            async def for_owner(self, owner_id: str) -> list[VolundrPort]:
                return []

        sub = self._build_subscriber([])
        sub._factory = BareForOwnerFactory()

        with pytest.raises(AttributeError):
            await sub._resolve_owner_adapters_for_reconcile(OWNER_ID)

    def test_attempt_was_stable_uses_configured_threshold(self) -> None:
        config = _default_config(reconnect_stable_after_seconds=100.0)
        sub = self._build_subscriber([], config=config)

        assert sub._attempt_was_stable(time.monotonic()) is False
        assert sub._attempt_was_stable(time.monotonic() - 200.0) is True

    @pytest.mark.asyncio
    async def test_unstable_immediate_drop_does_not_reset_backoff(self) -> None:
        """A cluster that fails fast keeps accumulating its failure streak."""

        class DropsImmediately(NamedVolundr):
            async def subscribe_activity(self):  # type: ignore[override]
                self.attempts += 1
                raise RuntimeError("dropped")
                yield  # pragma: no cover - keeps this an async generator

        volundr = DropsImmediately(name="flaky", target_id="flaky-1")
        config = _default_config(
            reconnect_delay=0,
            reconnect_initial_delay=1.0,
            reconnect_max_delay=60.0,
            reconnect_jitter=0.0,
            reconnect_stable_after_seconds=3600.0,
        )
        sub = self._build_subscriber([volundr], config=config)
        sub._running = True
        sub._cluster_backoff[(OWNER_ID, "flaky-1")] = _ClusterBackoffState(
            consecutive_failures=3, next_retry_at=0.0
        )
        task = asyncio.create_task(sub._adapter_subscription_loop(OWNER_ID, volundr))
        sub._owner_tasks[OWNER_ID] = {"flaky-1": task}

        task_result = await task

        assert task_result is None
        assert sub._cluster_backoff[(OWNER_ID, "flaky-1")].consecutive_failures == 4

    @pytest.mark.asyncio
    async def test_connect_that_hangs_to_timeout_never_counts_as_stable(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Regression test for the round-4 blocker: the stability clock

        must start only once the connection is genuinely open, not at task
        launch. A connect that hangs until its own timeout — even for
        longer than the configured stability window — must never be
        credited as "connected": the backoff keeps growing and no recovery
        is logged.
        """

        class HangsUntilTimeout(NamedVolundr):
            async def subscribe_activity(self):  # type: ignore[override]
                self.attempts += 1
                await asyncio.sleep(0.05)  # simulate hanging until the connect times out
                raise TimeoutError("connect timed out")
                yield  # pragma: no cover - keeps this an async generator

        volundr = HangsUntilTimeout(name="slow", target_id="slow-1")
        config = _default_config(
            reconnect_delay=0,
            reconnect_initial_delay=1.0,
            reconnect_max_delay=60.0,
            reconnect_jitter=0.0,
            # Smaller than the simulated hang: under the old bug (clock
            # started at task launch) this alone would have been enough to
            # count the timed-out attempt as "stable".
            reconnect_stable_after_seconds=0.01,
        )
        sub = self._build_subscriber([volundr], config=config)
        sub._running = True
        sub._cluster_backoff[(OWNER_ID, "slow-1")] = _ClusterBackoffState(
            consecutive_failures=2, next_retry_at=0.0
        )
        task = asyncio.create_task(sub._adapter_subscription_loop(OWNER_ID, volundr))
        sub._owner_tasks[OWNER_ID] = {"slow-1": task}

        with caplog.at_level(logging.INFO, logger="ting.domain.services.activity_subscriber"):
            task_result = await task

        assert task_result is None
        assert sub._cluster_backoff[(OWNER_ID, "slow-1")].consecutive_failures == 3
        assert not any("recovered" in record.getMessage() for record in caplog.records)

    @pytest.mark.asyncio
    async def test_stable_mid_stream_drop_resets_backoff(self) -> None:
        """An idle cluster (never has sessions) that stays connected long
        enough before dropping must reset its backoff — even though it
        raised instead of closing cleanly, and even though it never sent a
        single event.
        """

        class DropsAfterBeingStable(NamedVolundr):
            async def subscribe_activity(self):  # type: ignore[override]
                self.attempts += 1
                yield ActivityStreamConnected()  # the connection genuinely opens...
                raise RuntimeError("dropped")  # ...then drops, with zero events

        volundr = DropsAfterBeingStable(name="idle", target_id="idle-1")
        config = _default_config(
            reconnect_delay=0,
            reconnect_initial_delay=1.0,
            reconnect_max_delay=60.0,
            reconnect_jitter=0.0,
            reconnect_stable_after_seconds=0.0,
        )
        sub = self._build_subscriber([volundr], config=config)
        sub._running = True
        sub._cluster_backoff[(OWNER_ID, "idle-1")] = _ClusterBackoffState(
            consecutive_failures=3, next_retry_at=0.0
        )
        task = asyncio.create_task(sub._adapter_subscription_loop(OWNER_ID, volundr))
        sub._owner_tasks[OWNER_ID] = {"idle-1": task}

        task_result = await task

        assert task_result is None
        assert sub._cluster_backoff[(OWNER_ID, "idle-1")].consecutive_failures == 1


# ---------------------------------------------------------------------------
# Tests -- WatcherConfig new fields
# ---------------------------------------------------------------------------


class TestWatcherConfigNewFields:
    def test_defaults(self) -> None:
        cfg = WatcherConfig()
        assert cfg.idle_threshold == 30.0
        assert cfg.completion_check_delay == 5.0
        assert cfg.require_pr is False
        assert cfg.require_ci is False
        assert cfg.reconnect_delay == 5.0
        assert cfg.reconnect_initial_delay == 2.0
        assert cfg.reconnect_max_delay == 120.0
        assert cfg.reconnect_backoff_multiplier == 2.0
        assert cfg.reconnect_jitter == 0.2
        assert cfg.reconnect_stable_after_seconds == 30.0

    def test_custom(self) -> None:
        cfg = WatcherConfig(
            idle_threshold=60.0,
            completion_check_delay=10.0,
            require_pr=True,
            require_ci=True,
            reconnect_delay=3.0,
            reconnect_initial_delay=1.5,
            reconnect_max_delay=60.0,
            reconnect_backoff_multiplier=3.0,
            reconnect_jitter=0.1,
            reconnect_stable_after_seconds=15.0,
        )
        assert cfg.idle_threshold == 60.0
        assert cfg.completion_check_delay == 10.0
        assert cfg.require_pr is True
        assert cfg.require_ci is True
        assert cfg.reconnect_delay == 3.0
        assert cfg.reconnect_initial_delay == 1.5
        assert cfg.reconnect_max_delay == 60.0
        assert cfg.reconnect_backoff_multiplier == 3.0
        assert cfg.reconnect_jitter == 0.1
        assert cfg.reconnect_stable_after_seconds == 15.0


class TestFlockOutcomeCoercion:
    def test_coerce_bool(self) -> None:
        assert _coerce_bool(True) is True
        assert _coerce_bool("yes") is True
        assert _coerce_bool("0") is False
        assert _coerce_bool("maybe") is None

    def test_coerce_float(self) -> None:
        assert _coerce_float(1) == 1.0
        assert _coerce_float("0.75") == pytest.approx(0.75)
        assert _coerce_float(" ") is None
        assert _coerce_float("abc") is None

    def test_coerce_str(self) -> None:
        assert _coerce_str("  hello  ") == "hello"
        assert _coerce_str("") is None
        assert _coerce_str(None) is None

    def test_coerce_str_list(self) -> None:
        assert _coerce_str_list([" one ", "", "two", 3]) == ["one", "two", "3"]
        assert _coerce_str_list("not-a-list") == []
