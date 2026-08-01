"""Tests for the event-driven session activity subscriber."""

from __future__ import annotations

import asyncio
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
    SessionActivitySubscriber,
    _coerce_bool,
    _coerce_float,
    _coerce_str,
    _coerce_str_list,
)
from ting.ports.dispatcher_repository import DispatcherRepository
from ting.ports.volundr import ActivityEvent, SpawnRequest, VolundrPort, VolundrSession
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
        confidence=0.5,
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

    async def subscribe_activity(self) -> AsyncGenerator[ActivityEvent, None]:
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
            threshold=0.5,
            max_concurrent_runs=3,
            auto_continue=True,
            updated_at=NOW,
        )

    async def update(self, owner_id: str, **fields: object) -> DispatcherState:
        return await self.get_or_create(owner_id)

    async def list_active_owner_ids(self) -> list[str]:
        return []


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
        "confidence_base": 0.5,
        "confidence_pr_bonus": 0.2,
        "confidence_ci_bonus": 0.2,
        "confidence_idle_bonus": 0.1,
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
    sub = SessionActivitySubscriber(
        volundr_factory=factory,
        tracker_factory=tf,
        dispatcher_repo=d,
        event_bus=e,
        config=c,
        review_engine=review_engine,  # type: ignore[arg-type]
        workflow_campaign_repo=workflow_campaign_repo,
    )
    return sub, v, t, e


# ---------------------------------------------------------------------------
# Tests -- CompletionEvaluation
# ---------------------------------------------------------------------------


class TestCompletionEvaluation:
    def test_defaults(self) -> None:
        ce = CompletionEvaluation(is_complete=False, signals={}, confidence=0.0)
        assert ce.is_complete is False
        assert ce.confidence == 0.0

    def test_complete_with_signals(self) -> None:
        ce = CompletionEvaluation(
            is_complete=True,
            signals={"session_idle": True, "has_turns": True},
            confidence=0.7,
        )
        assert ce.is_complete is True
        assert ce.confidence == 0.7


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
        assert call.kwargs["scope_adherence_threshold"] == pytest.approx(0.7)

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
        assert result.confidence >= 0.5

    @pytest.mark.asyncio
    async def test_pr_increases_confidence(self) -> None:
        """PR existence should increase confidence."""
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
        assert result.confidence >= 0.9

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
    async def test_extended_idle_increases_confidence(self) -> None:
        """Duration above threshold should increase confidence."""
        config = _default_config(idle_threshold=10.0)
        sub, volundr, _, _ = _make_subscriber(config=config)
        run = _make_run()
        volundr.pr_error_sessions.add(run.session_id)

        result = await sub._evaluate_completion(
            run, volundr, {"turn_count": 5, "duration_seconds": 120}
        )
        assert result.signals["extended_idle"] is True
        assert result.confidence >= 0.6

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
            confidence=0.9,
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
            confidence=0.5,
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
        volundr = StubVolundr()
        volundr.sessions["stale-session"] = _make_volundr_session(
            session_id="stale-session",
            status="stopped",
        )
        tracker = MockTracker(runs_by_session={"stale-session": run})
        sub, _, _, _ = _make_subscriber(volundr=volundr, tracker=tracker, run=run)

        await sub._reconcile_running_runs(OWNER_ID, volundr)

        tracker.update_run_progress.assert_called_once()
        progress_call = tracker.update_run_progress.call_args
        assert progress_call.args[0] == "issue-stale"
        assert progress_call.kwargs["status"] == RunStatus.FAILED
        assert progress_call.kwargs["reason"] == "Session stopped"


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
        assert cfg.confidence_base == 0.5
        assert cfg.confidence_pr_bonus == 0.2
        assert cfg.confidence_ci_bonus == 0.2
        assert cfg.confidence_idle_bonus == 0.1
        assert cfg.reconnect_delay == 5.0

    def test_custom(self) -> None:
        cfg = WatcherConfig(
            idle_threshold=60.0,
            completion_check_delay=10.0,
            require_pr=True,
            require_ci=True,
            confidence_base=0.6,
            confidence_pr_bonus=0.15,
            confidence_ci_bonus=0.15,
            confidence_idle_bonus=0.05,
            reconnect_delay=3.0,
        )
        assert cfg.idle_threshold == 60.0
        assert cfg.completion_check_delay == 10.0
        assert cfg.require_pr is True
        assert cfg.require_ci is True
        assert cfg.confidence_base == 0.6
        assert cfg.confidence_pr_bonus == 0.15
        assert cfg.confidence_ci_bonus == 0.15
        assert cfg.confidence_idle_bonus == 0.05
        assert cfg.reconnect_delay == 3.0


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
