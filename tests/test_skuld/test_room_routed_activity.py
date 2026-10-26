"""Room-routed sessions must leave ``provisioning`` without a CLI transport.

Flock workflow and resident sessions never start Skuld's own CLI, so the
``system/init`` frame that normally moves a session out of ``provisioning``
never arrives. These tests pin the transitions that replace it.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from skuld.broker import Broker
from skuld.config import SkuldSettings


def _flock_settings(tmp_path) -> SkuldSettings:
    return SkuldSettings(
        session={
            "id": "wf-flock",
            "workspace_dir": str(tmp_path),
            "initial_prompt": "Implement the requested change",
        },
        room={"enabled": True},
        mesh={"enabled": True, "peer_id": "skuld-wf"},
        workflow_trigger={
            "enabled": True,
            "node_id": "trigger-1",
            "event_type": "delivery.requested",
        },
        chronicle_watcher_enabled=False,
    )


@pytest.fixture
def flock_broker(tmp_path) -> Broker:
    broker = Broker(settings=_flock_settings(tmp_path))
    broker._room_bridge = MagicMock()
    broker._room_bridge.participants = {"flock-coder": MagicMock(persona="coder")}
    broker._mesh_adapter = MagicMock(peer_id="skuld-wf")
    broker._activity_state = "provisioning"
    broker._report_activity_state = AsyncMock(return_value=True)
    broker._start_trace_span = AsyncMock(return_value="span-1")
    return broker


class TestWorkflowKickoffLeavesProvisioning:
    @pytest.mark.asyncio
    async def test_acknowledged_kickoff_reports_active(self, flock_broker):
        with patch.object(flock_broker, "_run_workflow_trigger_task", new=AsyncMock()):
            await flock_broker._run_supervised_workflow_trigger()

        assert flock_broker.startup_status()["startup_state"] == "ready"
        flock_broker._report_activity_state.assert_awaited_once_with("active")

    @pytest.mark.asyncio
    async def test_acknowledged_kickoff_keeps_state_a_peer_already_set(self, flock_broker):
        flock_broker._activity_state = "idle"

        with patch.object(flock_broker, "_run_workflow_trigger_task", new=AsyncMock()):
            await flock_broker._run_supervised_workflow_trigger()

        flock_broker._report_activity_state.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failed_kickoff_reports_error_not_active(self, flock_broker):
        failing = AsyncMock(side_effect=RuntimeError("never acknowledged"))
        with patch.object(flock_broker, "_run_workflow_trigger_task", new=failing):
            await flock_broker._run_supervised_workflow_trigger()

        flock_broker._report_activity_state.assert_awaited_once()
        assert flock_broker._report_activity_state.await_args.args == ("error",)


class TestFlockPeerWorkReportsActive:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("event_type", ["task_started", "thought", "thinking", "tool_start"])
    async def test_peer_work_event_reports_active(self, flock_broker, event_type):
        await flock_broker._observe_room_peer_event(
            "flock-coder",
            event_type,
            {"metadata": {"task_id": "task-1", "title": "Handle delivery.requested"}},
        )

        flock_broker._report_activity_state.assert_awaited_once_with("active")

    @pytest.mark.asyncio
    async def test_peer_result_does_not_report_active(self, flock_broker):
        await flock_broker._observe_room_peer_event(
            "flock-coder", "tool_result", {"metadata": {"task_id": "task-1"}}
        )

        flock_broker._report_activity_state.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resumed_peer_work_after_answered_help_reports_active(self, flock_broker):
        flock_broker._activity_state = "idle"
        flock_broker._pending_help_requests = {"req-1": MagicMock(status="answered")}

        await flock_broker._report_room_peer_working()

        flock_broker._report_activity_state.assert_awaited_once_with("active")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "blocker",
        [
            "pending_help",
            "pending_attention",
            "completion_reported",
            "failure_reported",
            "terminal_pending",
            "stopped",
            "error",
        ],
    )
    async def test_peer_work_never_overrides_owned_state(self, flock_broker, blocker):
        match blocker:
            case "pending_help":
                flock_broker._pending_help_requests = {"req-1": MagicMock(status="pending")}
            case "pending_attention":
                flock_broker._pending_attention = {"q-1": "question"}
            case "completion_reported":
                flock_broker._flock_completion_reported = True
            case "failure_reported":
                flock_broker._flock_failure_reported = True
            case "terminal_pending":
                flock_broker._pending_terminal_activity = {"kind": "completion", "payload": {}}
            case "stopped" | "error":
                flock_broker._activity_state = blocker

        await flock_broker._report_room_peer_working()

        flock_broker._report_activity_state.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_peer_work_ignored_outside_flock_workflow_sessions(self, tmp_path):
        broker = Broker(
            settings=SkuldSettings(
                session={"id": "plain", "workspace_dir": str(tmp_path)},
                chronicle_watcher_enabled=False,
            )
        )
        broker._report_activity_state = AsyncMock(return_value=True)

        await broker._report_room_peer_working()

        broker._report_activity_state.assert_not_awaited()


class TestFlockGoesIdleWhenNoPeerWorks:
    """Two builder sessions heartbeated ``active`` for eleven hours after their last
    peer finished, so nothing could tell them from sessions still working."""

    @pytest.fixture
    def working_broker(self, flock_broker):
        async def _report(state, *, extra_metadata=None):
            flock_broker._activity_state = state
            return True

        flock_broker._report_activity_state = AsyncMock(side_effect=_report)
        flock_broker._room_bridge.participants = {
            "flock-coder": MagicMock(persona="coder"),
            "flock-reviewer": MagicMock(persona="reviewer"),
        }
        flock_broker._emit_peer_outcome_pipeline_event = AsyncMock()
        flock_broker._maybe_report_flock_completion = AsyncMock()
        return flock_broker

    @staticmethod
    async def _event(broker, peer_id, event_type, task_id, data=None):
        await broker._observe_room_peer_event(
            peer_id,
            event_type,
            {"data": data or {}, "metadata": {"task_id": task_id}},
        )

    @pytest.mark.asyncio
    async def test_last_peer_task_completing_reports_idle_with_its_outcome(self, working_broker):
        await self._event(working_broker, "flock-coder", "task_started", "build-1")
        await working_broker._observe_room_peer_event(
            "flock-coder",
            "outcome",
            {
                "data": {"event_type": "code.completed", "verdict": "help_needed"},
                "metadata": {"event_type": "code.completed", "task_id": "build-1"},
            },
        )
        await self._event(working_broker, "flock-coder", "task_complete", "build-1")

        assert working_broker._activity_state == "idle"
        working_broker._report_activity_state.assert_awaited_with(
            "idle",
            extra_metadata={
                "flock_idle": {
                    "event_type": "code.completed",
                    "verdict": "help_needed",
                    "persona": "coder",
                }
            },
        )

    @pytest.mark.asyncio
    async def test_an_outcome_without_an_event_type_is_not_named(self, working_broker):
        await self._event(working_broker, "flock-coder", "task_started", "build-1")
        await working_broker._observe_room_peer_event(
            "flock-coder", "outcome", {"data": "plain text", "metadata": {}}
        )
        await self._event(working_broker, "flock-coder", "task_complete", "build-1")

        working_broker._report_activity_state.assert_awaited_with("idle", extra_metadata=None)

    @pytest.mark.asyncio
    async def test_session_stays_active_while_another_peer_task_runs(self, working_broker):
        await self._event(working_broker, "flock-coder", "task_started", "build-1")
        await self._event(working_broker, "flock-reviewer", "task_started", "review-1")
        await self._event(working_broker, "flock-reviewer", "task_started", "review-2")

        await self._event(working_broker, "flock-coder", "task_complete", "build-1")
        await self._event(working_broker, "flock-reviewer", "task_complete", "review-1")
        assert working_broker._activity_state == "active"

        await self._event(working_broker, "flock-reviewer", "task_complete", "review-2")
        assert working_broker._activity_state == "idle"
        working_broker._report_activity_state.assert_awaited_with("idle", extra_metadata=None)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "blocker",
        [
            "pending_help",
            "pending_attention",
            "completion_reported",
            "failure_reported",
            "terminal_pending",
        ],
    )
    async def test_idle_never_overrides_owned_state(self, working_broker, blocker):
        await self._event(working_broker, "flock-coder", "task_started", "build-1")
        match blocker:
            case "pending_help":
                working_broker._pending_help_requests = {"req-1": MagicMock(status="pending")}
            case "pending_attention":
                working_broker._pending_attention = {"q-1": "question"}
            case "completion_reported":
                working_broker._flock_completion_reported = True
            case "failure_reported":
                working_broker._flock_failure_reported = True
            case "terminal_pending":
                working_broker._pending_terminal_activity = {"kind": "completion", "payload": {}}
        working_broker._report_activity_state.reset_mock()

        await self._event(working_broker, "flock-coder", "task_complete", "build-1")

        working_broker._report_activity_state.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_already_idle_session_is_not_reported_again(self, working_broker):
        working_broker._activity_state = "idle"

        await self._event(working_broker, "flock-coder", "task_complete", "build-1")

        working_broker._report_activity_state.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_flock_idle_ignored_outside_flock_workflow_sessions(self, tmp_path):
        broker = Broker(
            settings=SkuldSettings(
                session={"id": "plain", "workspace_dir": str(tmp_path)},
                chronicle_watcher_enabled=False,
            )
        )
        broker._activity_state = "active"
        broker._report_activity_state = AsyncMock(return_value=True)

        await broker._report_room_flock_idle()

        broker._report_activity_state.assert_not_awaited()


class TestRoomRoutedStartupLeavesProvisioning:
    async def _startup(self, broker: Broker) -> AsyncMock:
        report = AsyncMock(return_value=True)

        async def start_mesh() -> None:
            broker._mesh_adapter = MagicMock(peer_id="skuld-wf")

        transport = AsyncMock()
        transport.on_event = MagicMock()
        with (
            patch.object(broker, "_report_activity_state", new=report),
            patch.object(broker, "_hydrate_conversation_history", new=AsyncMock()),
            patch.object(broker, "_create_transport", return_value=transport),
            patch("skuld.broker.ServiceManager") as service_manager_cls,
            patch.object(broker, "_start_mesh_adapter", new=AsyncMock(side_effect=start_mesh)),
            patch.object(broker, "_ensure_workflow_prompt_turn", new=AsyncMock()),
            patch.object(broker, "_run_supervised_workflow_trigger", new=AsyncMock()),
        ):
            service_manager_cls.return_value = AsyncMock()
            await broker.startup()
            await asyncio.sleep(0)
        if broker._room_bridge is not None:
            await broker._room_bridge.stop_presence_sweep()
        if broker._workflow_trigger_task is not None:
            await broker._workflow_trigger_task
        return report

    @pytest.mark.asyncio
    async def test_resident_session_reports_idle_once_started(self, tmp_path):
        broker = Broker(
            settings=SkuldSettings(
                session={"id": "resident", "workspace_dir": str(tmp_path)},
                room={"enabled": True, "default_target_peer_id": "regin"},
                chronicle_watcher_enabled=False,
            )
        )

        report = await self._startup(broker)

        assert broker.startup_status()["startup_state"] == "ready"
        report.assert_awaited_once_with("idle")

    @pytest.mark.asyncio
    async def test_flock_workflow_waits_for_kickoff_instead_of_idling(self, tmp_path):
        broker = Broker(settings=_flock_settings(tmp_path))

        report = await self._startup(broker)

        assert broker._workflow_trigger_task is not None
        assert broker.startup_status()["startup_state"] == "workflow_kickoff_pending"
        report.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cli_session_waits_for_system_init(self, tmp_path):
        broker = Broker(
            settings=SkuldSettings(
                session={"id": "cli", "workspace_dir": str(tmp_path)},
                chronicle_watcher_enabled=False,
            )
        )

        report = await self._startup(broker)

        report.assert_not_awaited()
