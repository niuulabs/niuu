"""Tests for the workflow campaign projector's blocked-state derivation."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from ting.domain.models import WorkflowCampaign, WorkflowCampaignStatus
from ting.domain.services.workflow_campaign_lifecycle import TERMINAL_SESSION_STOPPED_KEY
from ting.domain.services.workflow_campaign_projector import (
    WorkflowCampaignProjector,
    _status_from_session,
)
from ting.ports.volundr import ActivityEvent


def _campaign(
    status: WorkflowCampaignStatus = WorkflowCampaignStatus.RUNNING,
    connection_id: str | None = None,
    completion_event: str = "",
) -> WorkflowCampaign:
    now = datetime.now(UTC)
    workflow_snapshot: dict = {}
    if completion_event:
        workflow_snapshot = {
            "graph": {"nodes": [{"id": "done", "kind": "end", "completionEvent": completion_event}]}
        }
    return WorkflowCampaign(
        id=uuid4(),
        slug="tool-build-test",
        name="tool-build-test",
        owner_id="user-1",
        workflow_id=uuid4(),
        workflow_name="Tool & Skill Builder",
        workflow_version="1.0.0",
        workflow_snapshot=workflow_snapshot,
        session_id="session-123",
        session_name="tool-build-test",
        status=status,
        active_stage_id=None,
        stage_state=[],
        metadata={},
        created_at=now,
        updated_at=now,
        last_activity_at=now,
        completed_at=None,
        connection_id=connection_id,
    )


class _Adapter:
    def __init__(
        self,
        *,
        session_status: str = "running",
        help_requests: list | None = None,
        gates: list | None = None,
        blocker_error: Exception | None = None,
        activity_state: str | None = None,
        activity_metadata: dict | None = None,
        stop_failures: int = 0,
    ) -> None:
        self._session_status = session_status
        self._help_requests = help_requests or []
        self._gates = gates or []
        self._blocker_error = blocker_error
        self._activity_state = activity_state
        self._activity_metadata = activity_metadata or {}
        self._stop_failures = stop_failures
        self.stop_attempts: list[str] = []
        self.stopped: list[str] = []

    async def get_session(self, session_id, *, auth_token=None, principal=None):
        return SimpleNamespace(
            status=self._session_status,
            name="tool-build-test",
            activity_state=self._activity_state,
            activity_metadata=self._activity_metadata,
        )

    async def get_help_requests(self, session_id, *, auth_token=None, principal=None):
        if self._blocker_error is not None:
            raise self._blocker_error
        return list(self._help_requests)

    async def get_workflow_gates(self, session_id, *, auth_token=None, principal=None):
        if self._blocker_error is not None:
            raise self._blocker_error
        return list(self._gates)

    async def stop_session(self, session_id, *, auth_token=None, principal=None):
        self.stop_attempts.append(session_id)
        if self._stop_failures:
            self._stop_failures -= 1
            raise ConnectionError("runtime control temporarily unavailable")
        self.stopped.append(session_id)


class _Factory:
    def __init__(
        self,
        adapter: _Adapter,
        *,
        connections: dict[str, _Adapter] | None = None,
    ) -> None:
        self._adapter = adapter
        self._connections = connections or {}
        self.primary_calls = 0
        self.connection_calls: list[str] = []

    async def primary_for_owner(self, owner_id: str):
        self.primary_calls += 1
        return self._adapter

    async def for_connection(self, owner_id: str, connection_id: str):
        self.connection_calls.append(connection_id)
        return self._connections.get(connection_id)


def _projector(adapter: _Adapter) -> tuple[WorkflowCampaignProjector, AsyncMock, AsyncMock]:
    repo = AsyncMock()
    repo.save_campaign = AsyncMock(side_effect=lambda campaign: campaign)
    event_bus = AsyncMock()
    projector = WorkflowCampaignProjector(
        repo=repo,
        volundr_factory=_Factory(adapter),
        event_bus=event_bus,
    )
    return projector, repo, event_bus


@pytest.mark.asyncio
async def test_lifecycle_and_active_owner_projection() -> None:
    projector, repo, _ = _projector(_Adapter())
    repo.list_active_owner_ids.return_value = ["owner-1", "owner-2"]

    assert projector.running is False
    await projector.start()
    assert projector.running is True
    assert await projector.list_active_owner_ids() == ["owner-1", "owner-2"]
    await projector.stop()
    assert projector.running is False


@pytest.mark.asyncio
async def test_reconcile_owner_isolated_and_best_effort() -> None:
    projector, repo, _ = _projector(_Adapter())
    first = _campaign()
    second = _campaign()
    other_owner = replace(_campaign(), owner_id="user-2")
    repo.list_active_campaigns.return_value = [first, other_owner, second]
    projector._refresh_campaign = AsyncMock(side_effect=[None, RuntimeError("unreachable")])

    await projector.reconcile_owner("user-1")

    assert projector._refresh_campaign.await_count == 2


@pytest.mark.asyncio
async def test_stream_events_cover_missing_pending_and_failure_states() -> None:
    projector, repo, _ = _projector(_Adapter())
    repo.get_active_campaign_by_session.side_effect = [
        None,
        _campaign(WorkflowCampaignStatus.PENDING),
        _campaign(),
    ]

    assert (
        await projector.handle_activity(ActivityEvent("missing", "active", {}, "user-1"), "user-1")
        is False
    )
    assert (
        await projector.handle_activity(
            ActivityEvent("session-123", "active", {}, "user-1"), "user-1"
        )
        is True
    )
    assert repo.save_campaign.await_args_list[0].args[0].status == WorkflowCampaignStatus.RUNNING

    await projector.handle_activity(
        ActivityEvent(
            "session-123",
            "",
            {},
            "user-1",
            session_status="failed",
        ),
        "user-1",
        connection_id="volundr-west",
    )
    assert repo.get_active_campaign_by_session.await_args.kwargs == {
        "owner_id": "user-1",
        "session_id": "session-123",
        "connection_id": "volundr-west",
    }
    failed = repo.save_campaign.await_args_list[1].args[0]
    assert failed.status == WorkflowCampaignStatus.FAILED
    assert failed.metadata["failure_error"] == "Session failed"


@pytest.mark.asyncio
async def test_missing_campaign_and_session_are_ignored() -> None:
    adapter = _Adapter()
    adapter.get_session = AsyncMock(return_value=None)
    projector, repo, _ = _projector(adapter)
    repo.get_active_campaign_by_session.return_value = None

    assert (
        await projector.record_help_needed(
            {"summary": "help"},
            "user-1",
            session_id="missing",
            gate={},
        )
        is False
    )
    await projector._refresh_campaign(_campaign())
    repo.save_campaign.assert_not_awaited()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("queued", WorkflowCampaignStatus.PENDING),
        ("paused", WorkflowCampaignStatus.BLOCKED),
        ("cancelled", WorkflowCampaignStatus.FAILED),
        ("unknown", WorkflowCampaignStatus.RUNNING),
    ],
)
def test_status_projection_covers_recovery_states(
    raw: str, expected: WorkflowCampaignStatus
) -> None:
    assert _status_from_session(raw) == expected


@pytest.mark.asyncio
async def test_pending_help_request_flips_campaign_to_blocked() -> None:
    adapter = _Adapter(help_requests=[{"id": "help-1", "status": "pending"}])
    projector, repo, _ = _projector(adapter)

    await projector._refresh_campaign(_campaign())

    saved = repo.save_campaign.await_args.args[0]
    assert saved.status == WorkflowCampaignStatus.BLOCKED


@pytest.mark.asyncio
async def test_pending_gate_flips_campaign_to_blocked() -> None:
    adapter = _Adapter(gates=[{"id": "gate-1", "status": "pending"}])
    projector, repo, _ = _projector(adapter)

    await projector._refresh_campaign(_campaign())

    saved = repo.save_campaign.await_args.args[0]
    assert saved.status == WorkflowCampaignStatus.BLOCKED


@pytest.mark.asyncio
async def test_answered_blockers_resume_campaign() -> None:
    adapter = _Adapter(
        help_requests=[{"id": "help-1", "status": "answered"}],
        gates=[{"id": "gate-1", "status": "resolved"}],
    )
    projector, repo, _ = _projector(adapter)

    await projector._refresh_campaign(_campaign(WorkflowCampaignStatus.BLOCKED))

    saved = repo.save_campaign.await_args.args[0]
    assert saved.status == WorkflowCampaignStatus.RUNNING


@pytest.mark.asyncio
async def test_no_blockers_keeps_running_without_save() -> None:
    adapter = _Adapter()
    projector, repo, _ = _projector(adapter)

    await projector._refresh_campaign(_campaign())

    repo.save_campaign.assert_not_awaited()


@pytest.mark.asyncio
async def test_blocker_fetch_failure_reads_as_not_blocked() -> None:
    adapter = _Adapter(blocker_error=RuntimeError("session pod unreachable"))
    projector, repo, _ = _projector(adapter)

    await projector._refresh_campaign(_campaign())

    repo.save_campaign.assert_not_awaited()


@pytest.mark.asyncio
async def test_stopped_session_blocks_without_claiming_completion() -> None:
    adapter = _Adapter(
        session_status="stopped",
        blocker_error=RuntimeError("must not be called"),
    )
    projector, repo, _ = _projector(adapter)

    await projector._refresh_campaign(_campaign())

    saved = repo.save_campaign.await_args.args[0]
    assert saved.status == WorkflowCampaignStatus.BLOCKED
    assert saved.completed_at is None


@pytest.mark.asyncio
async def test_terminal_activity_error_fails_running_campaign() -> None:
    adapter = _Adapter(
        session_status="running",
        activity_state="error",
        activity_metadata={"error": "refresh token was already used"},
        blocker_error=RuntimeError("must not be called"),
    )
    projector, repo, event_bus = _projector(adapter)

    await projector._refresh_campaign(_campaign())

    saved = repo.save_campaign.await_args.args[0]
    assert saved.status == WorkflowCampaignStatus.FAILED
    assert saved.metadata["failure_error"] == "refresh token was already used"
    emitted = event_bus.emit.await_args.args[0]
    assert emitted.event == "workflow.campaign.failed"
    assert emitted.data["error"] == "refresh token was already used"


@pytest.mark.asyncio
async def test_sse_stopped_event_blocks_and_queues_push_without_session_read() -> None:
    adapter = _Adapter()
    campaign = _campaign()
    repo = AsyncMock()
    repo.get_active_campaign_by_session.return_value = campaign
    repo.save_campaign = AsyncMock(side_effect=lambda value: value)
    push_dispatcher = AsyncMock()
    projector = WorkflowCampaignProjector(
        repo=repo,
        volundr_factory=_Factory(adapter),
        event_bus=AsyncMock(),
        push_dispatcher=push_dispatcher,
    )

    handled = await projector.handle_activity(
        ActivityEvent(
            session_id=campaign.session_id,
            state="",
            metadata={},
            owner_id=campaign.owner_id,
            session_status="stopped",
        ),
        campaign.owner_id,
    )

    assert handled is True
    saved = repo.save_campaign.await_args.args[0]
    assert saved.status == WorkflowCampaignStatus.BLOCKED
    assert saved.completed_at is None
    push_dispatcher.queue_campaign.assert_awaited_once_with(saved)


@pytest.mark.asyncio
async def test_sse_error_event_fails_and_queues_error_push() -> None:
    campaign = _campaign()
    adapter = _Adapter()
    repo = AsyncMock()
    repo.get_active_campaign_by_session.return_value = campaign
    repo.save_campaign = AsyncMock(side_effect=lambda value: value)
    event_bus = AsyncMock()
    push_dispatcher = AsyncMock()
    projector = WorkflowCampaignProjector(
        repo=repo,
        volundr_factory=_Factory(adapter),
        event_bus=event_bus,
        push_dispatcher=push_dispatcher,
    )

    await projector.handle_activity(
        ActivityEvent(
            session_id=campaign.session_id,
            state="error",
            metadata={"error": "refresh token was already used"},
            owner_id=campaign.owner_id,
        ),
        campaign.owner_id,
    )

    terminal = repo.save_campaign.await_args_list[0].args[0]
    cleanup_receipt = repo.save_campaign.await_args_list[1].args[0]
    assert terminal.status == WorkflowCampaignStatus.FAILED
    assert terminal.metadata["failure_error"] == "refresh token was already used"
    assert cleanup_receipt.metadata[TERMINAL_SESSION_STOPPED_KEY] is True
    assert adapter.stopped == [campaign.session_id]
    assert event_bus.emit.await_args.args[0].event == "workflow.campaign.failed"
    push_dispatcher.queue_campaign.assert_awaited_once_with(terminal)


_AUTHORITATIVE_COMPLETION_EVENT = "workflow.child.completed"
"""Deliberately distinct from any bundled workflow's own vocabulary: the
projector must read the expected event from the campaign's pinned graph, not
assume a fixed name."""


def _authoritative_delivery_metadata(delivery: dict) -> dict:
    return {
        "completion_source": "ravn_flock",
        "completion_event_type": _AUTHORITATIVE_COMPLETION_EVENT,
        "completion_peer_id": "workflow-stop:workstream-result",
        "structured_outcome": {"result": delivery["result"]},
        "outcome_valid": True,
        "delivery": delivery,
    }


@pytest.mark.asyncio
async def test_idle_delivery_event_completes_before_releasing_session() -> None:
    campaign = _campaign(completion_event=_AUTHORITATIVE_COMPLETION_EVENT)
    delivery = {
        "schemaVersion": 1,
        "result": {"attemptId": "attempt-1", "candidateSha": "b" * 40},
        "reviews": [],
    }
    adapter = _Adapter()
    repo = AsyncMock()
    repo.get_active_campaign_by_session.return_value = campaign
    repo.save_campaign = AsyncMock(side_effect=lambda value: value)
    projector = WorkflowCampaignProjector(
        repo=repo,
        volundr_factory=_Factory(adapter),
        event_bus=AsyncMock(),
    )

    handled = await projector.handle_activity(
        ActivityEvent(
            session_id=campaign.session_id,
            state="idle",
            metadata=_authoritative_delivery_metadata(delivery),
            owner_id=campaign.owner_id,
        ),
        campaign.owner_id,
    )

    assert handled is True
    terminal = repo.save_campaign.await_args_list[0].args[0]
    cleanup_receipt = repo.save_campaign.await_args_list[1].args[0]
    assert terminal.status == WorkflowCampaignStatus.COMPLETED
    assert terminal.metadata["delivery"] == delivery
    assert terminal.completed_at is not None
    assert cleanup_receipt.metadata["delivery"] == delivery
    assert cleanup_receipt.metadata[TERMINAL_SESSION_STOPPED_KEY] is True
    assert adapter.stopped == [campaign.session_id]


@pytest.mark.asyncio
async def test_reconnect_projects_persisted_authoritative_idle_delivery() -> None:
    delivery = {
        "schemaVersion": 1,
        "result": {"attemptId": "attempt-1", "candidateSha": "b" * 40},
        "reviews": [],
    }
    adapter = _Adapter(
        session_status="running",
        activity_state="idle",
        activity_metadata=_authoritative_delivery_metadata(delivery),
    )
    projector, repo, _ = _projector(adapter)

    await projector._refresh_campaign(_campaign(completion_event=_AUTHORITATIVE_COMPLETION_EVENT))

    terminal = repo.save_campaign.await_args_list[0].args[0]
    assert terminal.status == WorkflowCampaignStatus.COMPLETED
    assert terminal.metadata["delivery"] == delivery
    assert adapter.stopped == [terminal.session_id]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata",
    [
        {
            "delivery": {
                "schemaVersion": 1,
                "result": {"attemptId": "attempt-1"},
                "reviews": [],
            }
        },
        _authoritative_delivery_metadata(
            {"schemaVersion": 1, "result": {"attemptId": "attempt-1"}, "reviews": []}
        )
        | {"outcome_valid": False},
        _authoritative_delivery_metadata(
            {"schemaVersion": 1, "result": {"attemptId": "attempt-1"}, "reviews": []}
        )
        | {"delivery": {"schemaVersion": 1, "result": {}, "reviews": []}},
    ],
)
async def test_idle_delivery_fails_closed_without_authoritative_matching_envelope(
    metadata: dict,
) -> None:
    campaign = _campaign(completion_event=_AUTHORITATIVE_COMPLETION_EVENT)
    adapter = _Adapter()
    projector, repo, _ = _projector(adapter)
    repo.get_active_campaign_by_session.return_value = campaign

    assert (
        await projector.handle_activity(
            ActivityEvent(campaign.session_id, "idle", metadata, campaign.owner_id),
            campaign.owner_id,
        )
        is True
    )

    repo.save_campaign.assert_not_awaited()
    assert adapter.stop_attempts == []


@pytest.mark.asyncio
async def test_terminal_cohort_releases_more_than_runtime_concurrency_limit() -> None:
    delivery = {
        "schemaVersion": 1,
        "result": {"attemptId": "attempt-1", "candidateSha": "b" * 40},
        "reviews": [{"eventId": "review-1", "valid": True}],
    }
    adapter = _Adapter(
        session_status="completed",
        activity_metadata={"delivery": delivery},
    )
    projector, repo, _ = _projector(adapter)
    campaigns = [replace(_campaign(), session_id=f"session-{index}") for index in range(10)]

    for campaign in campaigns:
        await projector._refresh_campaign(campaign)

    assert adapter.stopped == [campaign.session_id for campaign in campaigns]
    terminal_records = [call.args[0] for call in repo.save_campaign.await_args_list[::2]]
    cleanup_receipts = [call.args[0] for call in repo.save_campaign.await_args_list[1::2]]
    assert all(record.status == WorkflowCampaignStatus.COMPLETED for record in terminal_records)
    assert all(record.metadata["delivery"] == delivery for record in terminal_records)
    assert all(
        receipt.metadata[TERMINAL_SESSION_STOPPED_KEY] is True
        and receipt.metadata["delivery"] == delivery
        for receipt in cleanup_receipts
    )


@pytest.mark.asyncio
async def test_terminal_cleanup_failure_leaves_durable_result_retryable() -> None:
    delivery = {"result": {"attemptId": "attempt-1"}, "reviews": [{"valid": True}]}
    adapter = _Adapter(
        session_status="completed",
        activity_metadata={"delivery": delivery},
        stop_failures=1,
    )
    projector, repo, _ = _projector(adapter)

    await projector._refresh_campaign(_campaign())

    terminal = repo.save_campaign.await_args_list[0].args[0]
    assert terminal.status == WorkflowCampaignStatus.COMPLETED
    assert terminal.metadata["delivery"] == delivery
    assert TERMINAL_SESSION_STOPPED_KEY not in terminal.metadata

    cleaned = await projector.cleanup_terminal_session(terminal)

    assert cleaned is not None
    assert cleaned.status == WorkflowCampaignStatus.COMPLETED
    assert cleaned.metadata["delivery"] == delivery
    assert cleaned.metadata[TERMINAL_SESSION_STOPPED_KEY] is True
    assert adapter.stop_attempts == [terminal.session_id, terminal.session_id]


class TestConnectionAffinity:
    """Campaign reads must target the connection the session launched on."""

    @staticmethod
    def _build(factory) -> tuple[WorkflowCampaignProjector, AsyncMock]:
        repo = AsyncMock()
        repo.save_campaign = AsyncMock(side_effect=lambda campaign: campaign)
        return (
            WorkflowCampaignProjector(repo=repo, volundr_factory=factory, event_bus=AsyncMock()),
            repo,
        )

    @pytest.mark.asyncio
    async def test_campaign_connection_is_preferred_over_primary(self) -> None:
        valhalla = _Adapter(session_status="running")
        primary = _Adapter(session_status="stopped")  # would wrongly complete the campaign
        factory = _Factory(primary, connections={"conn-valhalla": valhalla})
        projector, repo = self._build(factory)

        await projector._refresh_campaign(
            _campaign(status=WorkflowCampaignStatus.PENDING, connection_id="conn-valhalla")
        )

        assert factory.connection_calls == ["conn-valhalla"]
        assert factory.primary_calls == 0
        saved = repo.save_campaign.await_args.args[0]
        assert saved.status == WorkflowCampaignStatus.RUNNING

    @pytest.mark.asyncio
    async def test_vanished_connection_does_not_retarget_primary(self) -> None:
        primary = _Adapter(session_status="running")
        factory = _Factory(primary, connections={})
        projector, _ = self._build(factory)

        await projector._refresh_campaign(_campaign(connection_id="conn-gone"))

        assert factory.connection_calls == ["conn-gone"]
        assert factory.primary_calls == 0

    @pytest.mark.asyncio
    async def test_legacy_campaign_without_connection_uses_primary(self) -> None:
        primary = _Adapter(session_status="running")
        factory = _Factory(primary)
        projector, _ = self._build(factory)

        await projector._refresh_campaign(_campaign())

        assert factory.connection_calls == []
        assert factory.primary_calls == 1
