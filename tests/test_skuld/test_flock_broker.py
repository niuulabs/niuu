"""Skuld as the broker of a Ravn flock: it hands work over and reports only what arrives.

Regression for a coder+reviewer flock whose broker answered "Reviewer verdict:
APPROVED" after doing the work with its own sub-agents: Skuld then announced the
broker's turn as the coder's ``code.changed`` while the reviewer Ravn never ran.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from niuu.forge_mcp.models import FlockError
from skuld.broker import Broker
from skuld.config import ForgeMcpConfig, MeshConfig, SkuldSettings
from skuld.flock_outcomes import FlockActivityLog
from skuld.forge_mcp.host import BrokerForgeMcpFlock
from skuld.transports.session_tools import capability_instructions

SKULD_PEER = "skuld-13d0f004"


def _broker_settings(tmp_path, **overrides) -> SkuldSettings:
    mesh = {
        "enabled": True,
        "peer_id": SKULD_PEER,
        "flock_broker": True,
        "persona": "skuld",
        "consumes_event_types": [],
    }
    return SkuldSettings(
        session={"id": "13d0f004-14ce-48af-8ac2-d16372cd8fbc", "workspace_dir": str(tmp_path)},
        room={"enabled": True},
        mesh=mesh,
        chronicle_watcher_enabled=False,
        **overrides,
    )


def _participant(persona: str, participant_type: str = "ravn") -> MagicMock:
    return MagicMock(persona=persona, participant_type=participant_type, status="idle")


@pytest.fixture
def broker(tmp_path) -> Broker:
    b = Broker(settings=_broker_settings(tmp_path))
    b._room_bridge = MagicMock()
    b._room_bridge.participants = {
        "flock-coder": _participant("coder"),
        "flock-reviewer": _participant("reviewer"),
        SKULD_PEER: _participant("Skuld", "skuld"),
    }
    b._mesh_adapter = MagicMock(peer_id=SKULD_PEER)
    b._emit_pipeline_event = AsyncMock()
    b._maybe_activate_workflow_gate = AsyncMock()
    b._maybe_emit_workflow_terminal_outcome = AsyncMock()
    b._report_activity_state = AsyncMock(return_value=True)
    return b


def _outcome_frame(event_type: str, *, persona_claim: str, verdict: str = "") -> dict:
    fields = {"verdict": verdict, "summary": f"{event_type} done"} if verdict else {}
    return {
        "metadata": {"event_type": event_type, "task_id": "task-1"},
        "data": {"event_type": event_type, "persona": persona_claim, "fields": fields},
    }


class TestConfiguration:
    def test_broker_that_consumes_peer_events_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="consumes_event_types"):
            MeshConfig(flock_broker=True, consumes_event_types=["code.requested"])

    def test_broker_without_room_is_refused(self, tmp_path) -> None:
        with pytest.raises(ValidationError, match="room.enabled"):
            SkuldSettings(
                session={"id": "s", "workspace_dir": str(tmp_path)},
                mesh={"enabled": True, "flock_broker": True, "consumes_event_types": []},
            )

    def test_broker_without_forge_mcp_is_refused(self, tmp_path) -> None:
        with pytest.raises(ValidationError, match="forge_mcp.enabled"):
            _broker_settings(tmp_path, forge_mcp={"enabled": False})

    def test_outcome_wait_must_fit_inside_one_broker_call(self) -> None:
        with pytest.raises(ValidationError, match="flock_outcome_wait_max_s"):
            ForgeMcpConfig(flock_outcome_wait_max_s=60.0, broker_request_timeout_s=60.0)

    def test_broker_settings_are_accepted(self, tmp_path) -> None:
        assert _broker_settings(tmp_path).mesh.flock_broker is True


class TestBrokerNeverSpeaksForAMember:
    @pytest.mark.asyncio
    async def test_broker_turn_is_not_published_as_code_changed(self, broker) -> None:
        mesh = AsyncMock()
        broker._mesh_adapter = MagicMock(peer_id=SKULD_PEER, _mesh=mesh)

        await broker._publish_mesh_outcome()

        mesh.publish.assert_not_awaited()

    def test_codex_runs_without_native_sub_agents(self, broker) -> None:
        assert broker._build_transport_kwargs()["multi_agent_enabled"] is False

    def test_ordinary_session_leaves_sub_agents_to_the_engine(self, tmp_path) -> None:
        plain = Broker(settings=SkuldSettings(session={"id": "s", "workspace_dir": str(tmp_path)}))
        assert plain._build_transport_kwargs()["multi_agent_enabled"] is None

    def test_agent_is_told_to_delegate_and_report_only_member_results(self) -> None:
        text = capability_instructions(forge_mcp_server="forge", flock_broker=True)
        assert "flock_delegate" in text
        assert "flock_outcomes" in text
        assert "sub-agents" in text
        assert "flock_delegate" not in capability_instructions(forge_mcp_server="forge")

    def test_broker_gets_flock_tools_and_instructions(self, broker) -> None:
        names = {tool["name"] for tool in broker._forge_mcp_toolbox().list_tools()}
        assert {"flock_members", "flock_delegate", "flock_outcomes"} <= names
        assert "flock_delegate" in broker._session_tools().instructions

    def test_replaced_forge_server_is_fatal_for_a_broker(self, tmp_path) -> None:
        settings = _broker_settings(
            tmp_path, mcp_servers=[{"name": "forge", "type": "stdio", "command": "x"}]
        )
        with pytest.raises(RuntimeError, match="flock_broker"):
            Broker(settings=settings)._session_tools()


class TestActivityComesFromAuthenticatedMembers:
    @pytest.mark.asyncio
    async def test_outcome_is_attributed_to_the_room_participant(self, broker) -> None:
        await broker._emit_peer_outcome_pipeline_event(
            "flock-reviewer",
            _outcome_frame("review.completed", persona_claim="coder", verdict="pass"),
        )

        [entry] = broker._flock_activity.after(0)
        assert entry.kind == "outcome"
        assert entry.peer_id == "flock-reviewer"
        assert entry.persona == "reviewer"
        assert entry.verdict == "pass"
        assert entry.event_type == "review.completed"

    @pytest.mark.asyncio
    async def test_unregistered_sender_is_not_recorded(self, broker) -> None:
        await broker._emit_peer_outcome_pipeline_event(
            "flock-impostor",
            _outcome_frame("review.completed", persona_claim="reviewer", verdict="pass"),
        )

        assert broker._flock_activity.after(0) == []

    @pytest.mark.asyncio
    async def test_member_failure_is_recorded(self, broker) -> None:
        broker._maybe_report_flock_failure = AsyncMock()

        await broker._observe_room_peer_event(
            "flock-coder",
            "error",
            {"type": "error", "task_id": "task-9", "data": "model refused the request"},
        )

        [entry] = broker._flock_activity.after(0)
        assert entry.kind == "error"
        assert entry.persona == "coder"
        assert entry.task_id == "task-9"
        assert "model refused" in entry.error

    def test_ordinary_session_keeps_no_flock_log(self, tmp_path) -> None:
        plain = Broker(settings=SkuldSettings(session={"id": "s", "workspace_dir": str(tmp_path)}))
        assert plain._flock_activity is None


class TestBrokerForgeMcpFlock:
    @pytest.fixture
    def flock(self, broker) -> BrokerForgeMcpFlock:
        broker.handle_directed_room_message = AsyncMock(return_value="msg-1")
        return BrokerForgeMcpFlock(broker, broker._flock_activity, max_wait_s=1.0)

    @pytest.mark.asyncio
    async def test_members_are_the_ravns_only(self, flock) -> None:
        members = await flock.members()
        assert [member["peer_id"] for member in members] == ["flock-coder", "flock-reviewer"]
        assert [member["persona"] for member in members] == ["coder", "reviewer"]

    @pytest.mark.asyncio
    async def test_delegate_routes_over_the_room_as_the_broker(self, flock, broker) -> None:
        await broker._flock_activity.record(kind="outcome", peer_id="flock-coder", persona="coder")

        handed = await flock.delegate("flock-coder", "Write add.py")

        assert handed == {
            "message_id": "msg-1",
            "peer_id": "flock-coder",
            "persona": "coder",
            "cursor": 1,
        }
        broker.handle_directed_room_message.assert_awaited_once_with(
            "flock-coder",
            "Write add.py",
            source="broker",
            metadata={"participant_id": SKULD_PEER},
        )

    @pytest.mark.asyncio
    async def test_delegate_to_a_non_member_fails(self, flock) -> None:
        with pytest.raises(FlockError, match="not a member"):
            await flock.delegate(SKULD_PEER, "do it yourself")

    @pytest.mark.asyncio
    async def test_refused_hand_off_fails_loudly(self, flock, broker) -> None:
        broker.handle_directed_room_message = AsyncMock(
            side_effect=RuntimeError("Mesh-directed room message failed")
        )
        with pytest.raises(FlockError, match="not accepted"):
            await flock.delegate("flock-coder", "Write add.py")

    @pytest.mark.asyncio
    async def test_activity_returns_what_arrives_after_the_cursor(self, flock, broker) -> None:
        log = broker._flock_activity

        async def reviewer_reports() -> None:
            await asyncio.sleep(0.01)
            await log.record(
                kind="outcome",
                peer_id="flock-reviewer",
                persona="reviewer",
                event_type="review.completed",
                verdict="pass",
            )

        task = asyncio.create_task(reviewer_reports())
        result = await flock.activity(after=0, peer_id="", wait_s=1.0)
        await task

        assert result["cursor"] == 1
        assert result["activity"][0]["persona"] == "reviewer"
        assert result["activity"][0]["verdict"] == "pass"

    @pytest.mark.asyncio
    async def test_activity_is_empty_when_nothing_arrives(self, flock) -> None:
        result = await flock.activity(after=0, peer_id="", wait_s=0.01)
        assert result == {"cursor": 0, "activity": []}


class TestFlockActivityLog:
    @pytest.mark.asyncio
    async def test_filters_by_member_and_cursor(self) -> None:
        log = FlockActivityLog(capacity=10)
        await log.record(kind="outcome", peer_id="flock-coder", persona="coder")
        await log.record(kind="outcome", peer_id="flock-reviewer", persona="reviewer")

        assert [entry.seq for entry in log.after(0)] == [1, 2]
        assert [entry.seq for entry in log.after(1)] == [2]
        assert [entry.peer_id for entry in log.after(0, peer_id="flock-coder")] == ["flock-coder"]

    @pytest.mark.asyncio
    async def test_capacity_bounds_the_log(self) -> None:
        log = FlockActivityLog(capacity=2)
        for _ in range(3):
            await log.record(kind="outcome", peer_id="flock-coder", persona="coder")

        assert [entry.seq for entry in log.after(0)] == [2, 3]
        assert log.cursor == 3
