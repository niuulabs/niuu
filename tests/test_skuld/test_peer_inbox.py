"""The peer inbox: other agents' messages reaching a harness session (NIU-1184).

The sending side is faked: every message here is a :class:`PeerMessage` built in the test, handed
to the broker the way Forge's WebSocket frame, the mesh and a room relay hand it over.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from niuu import cap_telemetry
from niuu.domain.peer_message import (
    PEER_MESSAGE_FRAME,
    PeerAddress,
    PeerKind,
    PeerMessage,
    PeerMessageError,
    ReplyKind,
    ReplyRoute,
    render_for_harness,
)
from niuu.ports.cli.transport import TransportCapabilities
from skuld import peer_inbox
from skuld.broker import Broker
from skuld.config import MeshConfig, SkuldSettings
from skuld.forge_mcp.host import BrokerForgeMcpHost
from skuld.mesh_adapter import SkuldMeshAdapter
from skuld.peer_inbox import (
    PeerInboxEntry,
    PeerMessageRefusedError,
    peer_channel,
    peer_turn_id,
    render_peer_turn,
)

SESSION_ID = "6f1f0b52-7f4e-4a53-9b0b-3f4a2f2b9a11"
SENDER_ID = "0c2d9b7e-1a4f-4c60-8f35-2b9d7e1a5c43"
OTHER_SENDER_ID = "7e3a1c9d-5b28-4f16-a0d4-9c8b2e6f1a37"
RUN_ID = "0b7e6d5c-4a39-4281-9f10-e1d2c3b4a596"
ACTIVATION_ID = "3f2b8c1e-5a47-4d0e-9b61-7c1e2a9d4f10"

STEP = {
    "activation_id": ACTIVATION_ID,
    "run_id": RUN_ID,
    "attempt": 1,
    "generation": 0,
    "node_id": "implement",
    "node_label": "Implement",
    "member": "coder",
    "valid_endings": ["verdict", "done", "failed"],
    "valid_verdicts": ["implemented"],
    "outcome_schema": None,
    "deadline_at": None,
    "instructions": "# Workflow step: Implement",
}


def _message(
    message_id: str = "msg-1",
    *,
    content: str = "Please review the diff",
    sender_id: str = SENDER_ID,
    recipient_id: str = SESSION_ID,
    **changes,
) -> PeerMessage:
    fields = {
        "message_id": message_id,
        "sender": PeerAddress(PeerKind.SESSION, sender_id, name="planner"),
        "recipient": PeerAddress(PeerKind.SESSION, recipient_id),
        "sent_at": datetime(2026, 11, 8, 10, 0, tzinfo=UTC),
        "content": content,
        "reply": ReplyRoute(ReplyKind.SESSION, sender_id),
    }
    return PeerMessage(**{**fields, **changes})


def _inbox_broker(tmp_path, *, activity: str = "idle", capabilities=None, **settings) -> Broker:
    broker = Broker(
        SkuldSettings(
            session={"id": SESSION_ID, "workspace_dir": str(tmp_path)},
            volundr_api_url="http://forge.test",
            transport="subprocess",
            chronicle_watcher_enabled=False,
            **settings,
        )
    )
    transport = MagicMock()
    transport.is_alive = True
    transport.capabilities = capabilities or TransportCapabilities()
    transport.send_message = AsyncMock(return_value=True)
    transport.send_control = AsyncMock(return_value=True)
    broker._transport = transport
    broker._activity_state = activity
    broker._complete_trace_span = AsyncMock()
    broker._start_trace_span = AsyncMock(return_value=None)
    broker._await_event_log_receipt = AsyncMock(return_value=True)
    broker._apply_retrieval_reflex = AsyncMock(side_effect=lambda message: message)
    posted: list[dict] = []

    async def post(url, json=None, **_kwargs):
        posted.append(json)
        return SimpleNamespace(status_code=204, url=url)

    broker._http_client = SimpleNamespace(post=post)
    broker._http_client_jwt = None
    broker._posted = posted
    return broker


def _peer_frames(broker: Broker) -> list[dict]:
    return [e["payload"] for e in broker._event_log_buffer if e["kind"] == PEER_MESSAGE_FRAME]


def _acks(broker: Broker) -> list[dict]:
    """The acknowledgements of Forge's frames (not the harness turn's own delivery ack)."""
    turn_ids = {turn.id for turn in broker._conversation_turns}
    return [
        e["payload"]
        for e in broker._event_log_buffer
        if e["kind"] in ("user_delivered", "user_delivery_failed")
        and e["payload"]["id"] not in turn_ids
    ]


def _sent_texts(broker: Broker) -> list[str]:
    return [call.args[0] for call in broker._transport.send_message.await_args_list]


def _restart_frames(broker: Broker) -> list[SimpleNamespace]:
    """The broker's durable log as hydration hands it back after a restart."""
    return [
        SimpleNamespace(kind=e["kind"], payload=e["payload"])
        for e in broker._event_log_buffer
        if e["kind"] == PEER_MESSAGE_FRAME
    ]


async def _settle() -> None:
    for _ in range(4):
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        await asyncio.gather(*pending, return_exceptions=True)


async def _accept(broker: Broker, envelope: PeerMessage, **kwargs):
    acceptance = await broker.accept_peer_message(envelope, **kwargs)
    await _settle()
    return acceptance


@pytest.fixture
def counted(monkeypatch):
    """Every ``skuld.peer.messages`` increment as (attributes)."""
    calls: list[dict] = []
    telemetry = SimpleNamespace(
        count=lambda name, value=1, *, attributes=None, description="": calls.append(
            {"name": name, **(attributes or {})}
        )
    )
    monkeypatch.setattr(peer_inbox, "get_observability", lambda: telemetry)
    return calls


class TestAccepting:
    async def test_a_message_is_persisted_then_handed_to_the_harness(self, tmp_path) -> None:
        broker = _inbox_broker(tmp_path)
        envelope = _message()

        acceptance = await _accept(broker, envelope)

        assert acceptance.duplicate is False
        frames = _peer_frames(broker)
        assert frames[0] == {
            "type": PEER_MESSAGE_FRAME,
            "envelope": envelope.to_wire(),
            "accepted_at": frames[0]["accepted_at"],
            "injected": False,
        }
        assert frames[-1]["injected"] is True
        assert _sent_texts(broker) == [render_for_harness(envelope)]

    async def test_accepting_waits_for_forge_to_confirm_the_log(self, tmp_path) -> None:
        broker = _inbox_broker(tmp_path)
        confirmed = asyncio.Event()

        async def confirm(_receipt, _timeout):
            await confirmed.wait()
            return True

        broker._await_event_log_receipt = confirm
        accepting = asyncio.create_task(broker.accept_peer_message(_message()))
        await asyncio.sleep(0.05)

        assert not accepting.done()
        assert len(_peer_frames(broker)) == 1
        assert broker._transport.send_message.await_count == 0

        confirmed.set()
        assert (await accepting).duplicate is False
        await _settle()

    async def test_the_message_is_held_only_once_stored(self, tmp_path) -> None:
        broker = _inbox_broker(tmp_path)
        broker._await_event_log_receipt = AsyncMock(return_value=False)

        with pytest.raises(PeerMessageRefusedError, match="was not stored"):
            await broker.accept_peer_message(_message())

        with pytest.raises(PeerMessageError, match="received no peer message"):
            broker.received_peer_message("msg-1")
        assert broker._transport.send_message.await_count == 0

    async def test_an_unconfirmed_log_flush_refuses_naming_the_knob(self, tmp_path) -> None:
        broker = _inbox_broker(tmp_path)
        broker._await_event_log_receipt = AsyncMock(return_value=None)

        with pytest.raises(PeerMessageRefusedError, match="forge_mcp.notify_confirm_timeout_s"):
            await broker.accept_peer_message(_message())

        with pytest.raises(PeerMessageError):
            broker.received_peer_message("msg-1")

    async def test_a_message_for_another_session_is_refused_with_the_reason(
        self, tmp_path, counted
    ) -> None:
        broker = _inbox_broker(tmp_path)

        with pytest.raises(PeerMessageRefusedError, match=f"but this is session {SESSION_ID}"):
            await broker.accept_peer_message(_message(recipient_id="some-other-session"))

        assert _peer_frames(broker) == []
        assert [c["outcome"] for c in counted] == ["refused"]
        assert counted[0]["recipient"] == "some-other-session"

    async def test_a_session_that_takes_no_peer_messages_refuses_them(self, tmp_path) -> None:
        broker = _inbox_broker(tmp_path, peer_inbox={"enabled": False})

        with pytest.raises(PeerMessageRefusedError, match="peer_inbox.enabled is off"):
            await broker.accept_peer_message(_message())

    async def test_without_a_durable_log_a_message_is_refused_never_held_in_memory(
        self, tmp_path
    ) -> None:
        broker = _inbox_broker(tmp_path, event_log_enabled=False)

        with pytest.raises(PeerMessageRefusedError, match="no durable Forge log"):
            await broker.accept_peer_message(_message())

        with pytest.raises(PeerMessageError):
            broker.received_peer_message("msg-1")

    async def test_a_broker_that_failed_to_start_refuses(self, tmp_path) -> None:
        broker = _inbox_broker(tmp_path)
        broker._fail_startup(RuntimeError("mesh down"))

        with pytest.raises(PeerMessageRefusedError, match="failed to start"):
            await broker.accept_peer_message(_message())

    async def test_a_room_routed_session_has_no_harness_to_read_it(self, tmp_path) -> None:
        broker = _inbox_broker(tmp_path)
        broker._is_room_routed_session = lambda: True

        with pytest.raises(PeerMessageRefusedError, match="no harness of its own"):
            await broker.accept_peer_message(_message())

    async def test_the_log_refusing_the_frame_refuses_the_message(self, tmp_path) -> None:
        broker = _inbox_broker(tmp_path)
        broker._enqueue_event_log = lambda _frame, **_kw: None

        with pytest.raises(PeerMessageRefusedError, match="durable event log refused"):
            await broker.accept_peer_message(_message())


class TestDuplicates:
    async def test_a_message_delivered_twice_is_handled_once(self, tmp_path, counted) -> None:
        broker = _inbox_broker(tmp_path)

        first = await _accept(broker, _message())
        second = await _accept(broker, _message())

        assert (first.duplicate, second.duplicate) == (False, True)
        assert len(_sent_texts(broker)) == 1
        assert [c["outcome"] for c in counted] == ["accepted", "delivered", "duplicate"]

    async def test_concurrent_deliveries_of_one_message_are_handled_once(self, tmp_path) -> None:
        broker = _inbox_broker(tmp_path)

        results = await asyncio.gather(
            broker.accept_peer_message(_message()), broker.accept_peer_message(_message())
        )
        await _settle()

        assert sorted(r.duplicate for r in results) == [False, True]
        assert len(_sent_texts(broker)) == 1

    async def test_a_message_delivered_again_after_a_restart_is_still_handled_once(
        self, tmp_path
    ) -> None:
        before = _inbox_broker(tmp_path)
        await _accept(before, _message())
        assert len(_sent_texts(before)) == 1

        after = _inbox_broker(tmp_path)
        after._restore_durable_runtime_state(_restart_frames(before))
        again = await _accept(after, _message())

        assert again.duplicate is True
        assert _sent_texts(after) == []
        assert after.received_peer_message("msg-1") == _message()


class TestTheTurnBoundary:
    @pytest.mark.parametrize("state", ["active", "tool_executing", "awaiting_input"])
    async def test_a_message_waits_while_a_turn_runs_or_a_person_is_asked(
        self, tmp_path, state
    ) -> None:
        broker = _inbox_broker(tmp_path, activity=state)

        await _accept(broker, _message())

        assert _sent_texts(broker) == []
        assert _peer_frames(broker)[-1]["injected"] is False

    async def test_a_waiting_message_is_handed_over_when_the_turn_ends(self, tmp_path) -> None:
        broker = _inbox_broker(tmp_path, activity="active")
        await _accept(broker, _message())

        await broker._report_activity_state("idle")
        await _settle()

        assert _sent_texts(broker) == [render_for_harness(_message())]
        assert _peer_frames(broker)[-1]["injected"] is True

    async def test_a_transport_that_says_its_turn_is_active_holds_the_message(
        self, tmp_path
    ) -> None:
        class Tracking(MagicMock):
            is_turn_active = True

        broker = _inbox_broker(tmp_path)
        tracked = Tracking()
        tracked.is_alive = True
        tracked.capabilities = TransportCapabilities()
        tracked.send_message = AsyncMock(return_value=True)
        broker._transport = tracked

        await _accept(broker, _message())

        assert tracked.send_message.await_count == 0

    async def test_the_message_turn_is_recorded_like_a_user_message(self, tmp_path) -> None:
        broker = _inbox_broker(tmp_path)
        envelope = _message()

        await _accept(broker, envelope)

        turn = broker._conversation_turns[0]
        assert turn.role == "user"
        assert turn.content == render_for_harness(envelope)
        assert turn.id == peer_turn_id(SESSION_ID, ["msg-1"])
        assert turn.metadata["peer_messages"] == ["msg-1"]
        broker._complete_trace_span.assert_awaited()
        assert broker._complete_trace_span.await_args.kwargs["attributes"] == {
            "source": "peer_message"
        }

    async def test_several_waiting_messages_go_in_as_one_turn_oldest_first(self, tmp_path) -> None:
        broker = _inbox_broker(tmp_path, activity="active")
        messages = [
            _message("m-1", content="first"),
            _message("m-2", content="second", sender_id=OTHER_SENDER_ID),
            _message("m-3", content="third"),
        ]
        for message in messages:
            await _accept(broker, message)

        await broker._report_activity_state("idle")
        await _settle()

        assert _sent_texts(broker) == [render_peer_turn([PeerInboxEntry(m, "t") for m in messages])]
        text = _sent_texts(broker)[0]
        assert text.startswith("[3 messages arrived while you were working, oldest first]")
        assert text.index("first") < text.index("second") < text.index("third")
        assert broker._conversation_turns[0].metadata["peer_messages"] == ["m-1", "m-2", "m-3"]
        assert [f["injected"] for f in _peer_frames(broker)[-3:]] == [True, True, True]

    async def test_a_delivered_message_is_not_handed_over_a_second_time(self, tmp_path) -> None:
        broker = _inbox_broker(tmp_path)
        await _accept(broker, _message())

        await broker._report_activity_state("active")
        await broker._report_activity_state("idle")
        await _settle()

        assert len(_sent_texts(broker)) == 1

    async def test_nothing_is_handed_to_a_stopped_session(self, tmp_path) -> None:
        broker = _inbox_broker(tmp_path, activity="stopped")

        await _accept(broker, _message())

        assert _sent_texts(broker) == []

    async def test_a_session_with_no_transport_holds_the_message(self, tmp_path) -> None:
        broker = _inbox_broker(tmp_path)
        broker._transport = None

        await _accept(broker, _message())

        assert broker._peer_messages_waiting() is True

    async def test_the_gate_is_shared_with_workflow_steps(self, tmp_path) -> None:
        broker = _inbox_broker(tmp_path)

        assert broker._turn_boundary_open() is True
        broker._activity_state = "active"
        assert broker._turn_boundary_open() is False
        broker._activity_state = "awaiting_input"
        assert broker._turn_boundary_open() is False


class TestUrgent:
    STEERING = TransportCapabilities(steer=True, steering_mode="live")

    async def test_an_urgent_message_does_not_wait_for_the_boundary(self, tmp_path) -> None:
        broker = _inbox_broker(tmp_path, activity="active")

        await _accept(broker, _message(urgent=True))

        assert len(_sent_texts(broker)) == 1
        assert _peer_frames(broker)[-1]["injected"] is True

    async def test_an_urgent_message_takes_the_steering_path_a_persons_message_takes(
        self, tmp_path
    ) -> None:
        broker = _inbox_broker(tmp_path, activity="active", capabilities=self.STEERING)
        broker._transport.is_turn_active = True
        envelope = _message(urgent=True)

        await _accept(broker, envelope)

        broker._transport.send_control.assert_awaited_once()
        args = broker._transport.send_control.await_args
        assert args.args == ("steer",)
        assert args.kwargs["content"] == render_for_harness(envelope)
        assert broker._transport.send_message.await_count == 0

    async def test_an_ordinary_message_does_not_steer_a_running_turn(self, tmp_path) -> None:
        broker = _inbox_broker(tmp_path, activity="active", capabilities=self.STEERING)
        broker._transport.is_turn_active = True

        await _accept(broker, _message())

        assert broker._transport.send_control.await_count == 0
        assert broker._transport.send_message.await_count == 0

    async def test_an_urgent_message_takes_the_waiting_ones_with_it_in_order(
        self, tmp_path
    ) -> None:
        broker = _inbox_broker(tmp_path, activity="active")
        await _accept(broker, _message("m-1", content="earlier"))
        assert _sent_texts(broker) == []

        await _accept(broker, _message("m-2", content="now!", urgent=True))

        (text,) = _sent_texts(broker)
        assert text.index("earlier") < text.index("now!")

    async def test_a_persons_own_message_is_not_a_peer_message_and_still_interrupts(
        self, tmp_path
    ) -> None:
        broker = _inbox_broker(tmp_path, activity="active", capabilities=self.STEERING)
        broker._transport.is_turn_active = True

        await broker._dispatch_browser_message({"content": "stop and look at this"}, None)
        await _settle()

        broker._transport.send_control.assert_awaited_once()
        assert broker._transport.send_control.await_args.kwargs["content"] == (
            "stop and look at this"
        )
        assert _peer_frames(broker) == []


class TestWorkflowStepsGoFirst:
    async def test_a_pending_step_is_handed_over_before_waiting_messages(self, tmp_path) -> None:
        from skuld.workflow_steps import WorkflowStepRequest

        broker = _inbox_broker(tmp_path, activity="active", workflow_steps={"enabled": True})
        await broker.accept_workflow_step(WorkflowStepRequest(**STEP))
        await _accept(broker, _message("m-1"))
        assert _sent_texts(broker) == []

        await broker._report_activity_state("idle")
        await _settle()

        assert _sent_texts(broker) == [STEP["instructions"]]

        await broker._report_activity_state("active")
        await broker._report_activity_state("idle")
        await _settle()

        assert _sent_texts(broker) == [STEP["instructions"], render_for_harness(_message("m-1"))]

    async def test_without_a_step_the_messages_go_in_at_once(self, tmp_path) -> None:
        broker = _inbox_broker(tmp_path, workflow_steps={"enabled": True})

        await _accept(broker, _message())

        assert _sent_texts(broker) == [render_for_harness(_message())]


class TestRestart:
    async def test_a_fresh_start_restores_the_inbox_even_without_a_resume(
        self, tmp_path, monkeypatch
    ) -> None:
        before = _inbox_broker(tmp_path, activity="active")
        await _accept(before, _message())
        frames = _restart_frames(before)

        async def fetch(*_args, on_frames, **_kwargs):
            on_frames(frames)
            return []

        monkeypatch.setattr("skuld.broker.fetch_durable_history", fetch)
        after = _inbox_broker(tmp_path)
        after._rejoin_room_members = AsyncMock()
        assert not after._settings.session.resume_session_id

        await after._hydrate_conversation_history()

        assert after._peer_messages_waiting() is True

    async def test_an_inbox_that_could_not_be_restored_refuses_peers_loudly(
        self, tmp_path, monkeypatch
    ) -> None:
        cap_telemetry.reset_cap_hits()

        async def fetch(*_args, **_kwargs):
            raise RuntimeError("forge log unreachable")

        monkeypatch.setattr("skuld.broker.fetch_durable_history", fetch)
        broker = _inbox_broker(tmp_path)

        await broker._hydrate_conversation_history()

        assert cap_telemetry.degraded_counts()["skuld.peer_inbox.restore"] == 1
        with pytest.raises(PeerMessageRefusedError, match="could not restore its inbox"):
            await broker.accept_peer_message(_message())
        assert _peer_frames(broker) == []

    async def test_a_message_not_yet_handed_over_is_delivered_after_the_restart(
        self, tmp_path
    ) -> None:
        before = _inbox_broker(tmp_path, activity="active")
        await _accept(before, _message())
        assert _sent_texts(before) == []

        after = _inbox_broker(tmp_path)
        after._restore_durable_runtime_state(_restart_frames(before))
        assert after._peer_messages_waiting() is True
        await after._inject_at_turn_boundary()
        await _settle()

        assert _sent_texts(after) == [render_for_harness(_message())]

    async def test_a_message_already_handed_over_is_never_delivered_twice(self, tmp_path) -> None:
        before = _inbox_broker(tmp_path)
        await _accept(before, _message())

        after = _inbox_broker(tmp_path)
        after._restore_durable_runtime_state(_restart_frames(before))
        assert after._peer_messages_waiting() is False
        await after._inject_at_turn_boundary()
        await _settle()

        assert _sent_texts(after) == []

    async def test_the_latest_logged_state_of_a_message_wins(self, tmp_path) -> None:
        broker = _inbox_broker(tmp_path)
        entry = PeerInboxEntry(_message(), "2026-11-08T10:00:00+00:00")
        waiting = entry.frame()
        handed = {**waiting, "injected": True}
        frames = [SimpleNamespace(kind=PEER_MESSAGE_FRAME, payload=p) for p in (waiting, handed)]

        broker._restore_durable_peer_inbox(frames)

        assert broker._peer_messages_waiting() is False

    async def test_other_log_frames_are_left_alone(self, tmp_path) -> None:
        broker = _inbox_broker(tmp_path)

        broker._restore_durable_peer_inbox([SimpleNamespace(kind="room_member", payload={})])

        assert broker._peer_inbox == {}

    async def test_a_damaged_frame_fails_the_restore_loudly(self, tmp_path) -> None:
        broker = _inbox_broker(tmp_path)
        damaged = SimpleNamespace(
            kind=PEER_MESSAGE_FRAME, payload={"type": PEER_MESSAGE_FRAME, "envelope": {}}
        )

        with pytest.raises(PeerMessageError, match="needs 'accepted_at'"):
            broker._restore_durable_peer_inbox([damaged])

    async def test_the_counts_come_back_from_the_log(self, tmp_path) -> None:
        before = _inbox_broker(tmp_path, activity="active")
        await _accept(before, _message("m-1"))
        await before._report_activity_state("idle")
        await _settle()
        await _accept(before, _message("m-2"))

        after = _inbox_broker(tmp_path)
        after._restore_durable_peer_inbox(_restart_frames(before))

        assert after.peer_message_status() == before.peer_message_status()
        (pair,) = after.peer_message_status()["pairs"]
        assert (pair["accepted"], pair["delivered"]) == (2, 2)


class TestWhenTheHarnessDoesNotGetIt:
    async def test_a_failed_handover_puts_the_message_back_durably(self, tmp_path) -> None:
        broker = _inbox_broker(tmp_path)
        broker._ensure_transport_started = AsyncMock(side_effect=RuntimeError("no harness"))

        await broker.accept_peer_message(_message())
        results = await asyncio.gather(*list(broker._peer_inbox_tasks), return_exceptions=True)

        assert any(isinstance(r, RuntimeError) for r in results)
        assert broker._peer_messages_waiting() is True
        assert _peer_frames(broker)[-1]["injected"] is False

    async def test_a_message_that_cannot_be_put_back_is_reported_not_lost_quietly(
        self, tmp_path
    ) -> None:
        cap_telemetry.reset_cap_hits()
        broker = _inbox_broker(tmp_path)
        broker._ensure_transport_started = AsyncMock(side_effect=RuntimeError("no harness"))
        await broker.accept_peer_message(_message())
        broker._await_event_log_receipt = AsyncMock(return_value=False)

        await asyncio.gather(*list(broker._peer_inbox_tasks), return_exceptions=True)

        assert cap_telemetry.degraded_counts()["skuld.peer_inbox.inject"] == 1

    async def test_a_turn_the_harness_never_accepted_is_reported_and_waits_again(
        self, tmp_path
    ) -> None:
        cap_telemetry.reset_cap_hits()
        broker = _inbox_broker(
            tmp_path, delivery={"max_attempts": 1, "initial_backoff_seconds": 0.001}
        )
        broker._transport.send_message = AsyncMock(side_effect=RuntimeError("wedged"))

        await _accept(broker, _message())

        assert cap_telemetry.degraded_counts()["skuld.peer_inbox.delivery"] == 1
        assert broker._conversation_turns[0].metadata["steering_state"] == "failed"
        assert broker._peer_messages_waiting() is True
        assert _peer_frames(broker)[-1]["injected"] is False

    async def test_a_background_task_that_fails_is_logged(self, tmp_path, caplog) -> None:
        broker = _inbox_broker(tmp_path)

        async def boom() -> None:
            raise RuntimeError("inbox exploded")

        broker._spawn_peer_inbox_task(boom(), "boom")
        await _settle()

        assert "peer inbox task boom failed" in caplog.text


class TestTheForgeFrame:
    @staticmethod
    def _frame(envelope=None, request_id="req-1") -> dict:
        return {
            "type": "peer_message",
            "envelope": (envelope or _message()).to_wire(),
            "request_id": request_id,
        }

    async def test_an_accepted_message_is_acknowledged_for_its_request(self, tmp_path) -> None:
        broker = _inbox_broker(tmp_path)

        await broker._dispatch_browser_message(self._frame(), None)
        await _settle()

        (ack,) = _acks(broker)
        assert ack["type"] == "user_delivered"
        assert ack["request_id"] == "req-1"
        assert ack["id"] == "msg-1"
        assert _sent_texts(broker) == [render_for_harness(_message())]

    async def test_the_acknowledgement_comes_after_the_inbox_has_it_durably(self, tmp_path) -> None:
        broker = _inbox_broker(tmp_path)
        confirmed = asyncio.Event()

        async def confirm(_receipt, _timeout):
            await confirmed.wait()
            return True

        broker._await_event_log_receipt = confirm
        dispatching = asyncio.create_task(broker._dispatch_browser_message(self._frame(), None))
        await asyncio.sleep(0.05)

        assert _acks(broker) == []

        confirmed.set()
        await dispatching
        await _settle()
        assert [a["type"] for a in _acks(broker)] == ["user_delivered"]

    async def test_a_message_delivered_twice_is_acknowledged_both_times_but_handled_once(
        self, tmp_path
    ) -> None:
        broker = _inbox_broker(tmp_path)

        await broker._dispatch_browser_message(self._frame(request_id="a"), None)
        await broker._dispatch_browser_message(self._frame(request_id="b"), None)
        await _settle()

        assert [(a["type"], a["request_id"]) for a in _acks(broker)] == [
            ("user_delivered", "a"),
            ("user_delivered", "b"),
        ]
        assert len(_sent_texts(broker)) == 1

    @pytest.mark.parametrize(
        "mutate, reason",
        [
            (lambda wire: wire.update(version=7), "unsupported peer message version 7"),
            (lambda wire: wire.update(content=""), "content is required"),
            (lambda wire: wire.update(sent_at="yesterday"), "sent_at is not ISO 8601"),
            (lambda wire: wire.update(urgent="yes"), "urgent must be true or false"),
            (lambda wire: wire["sender"].update(kind="robot"), "sender.kind must be one of"),
            (lambda wire: wire.pop("recipient"), "recipient must be an object"),
        ],
    )
    async def test_a_malformed_envelope_fails_the_request_with_the_reason(
        self, tmp_path, mutate, reason
    ) -> None:
        broker = _inbox_broker(tmp_path)
        wire = _message().to_wire()
        mutate(wire)

        await broker._dispatch_browser_message(
            {"type": "peer_message", "envelope": wire, "request_id": "req-1"}, None
        )

        (ack,) = _acks(broker)
        assert ack["type"] == "user_delivery_failed"
        assert ack["request_id"] == "req-1"
        assert ack["id"] == "msg-1" or "recipient" in reason
        assert reason in ack["error"]
        assert _peer_frames(broker) == []

    async def test_a_frame_without_an_envelope_fails_the_request(self, tmp_path) -> None:
        broker = _inbox_broker(tmp_path)

        await broker._dispatch_browser_message({"type": "peer_message", "request_id": "r"}, None)

        (ack,) = _acks(broker)
        assert ack["type"] == "user_delivery_failed"
        assert ack["error"] == "a peer message must be an object"

    async def test_a_refused_message_fails_the_request_with_the_reason(self, tmp_path) -> None:
        broker = _inbox_broker(tmp_path)

        await broker._dispatch_browser_message(
            self._frame(_message(recipient_id="someone-else")), None
        )

        (ack,) = _acks(broker)
        assert ack["type"] == "user_delivery_failed"
        assert "but this is session" in ack["error"]

    async def test_a_frame_without_a_request_id_is_still_accepted(self, tmp_path) -> None:
        broker = _inbox_broker(tmp_path)
        frame = self._frame()
        del frame["request_id"]

        await broker._dispatch_browser_message(frame, None)
        await _settle()

        (ack,) = _acks(broker)
        assert "request_id" not in ack
        assert len(_sent_texts(broker)) == 1


class TestLookupForReplies:
    async def test_the_reply_tool_finds_a_received_message_by_id(self, tmp_path) -> None:
        broker = _inbox_broker(tmp_path)
        envelope = _message()
        await _accept(broker, envelope)
        host = BrokerForgeMcpHost(broker, MagicMock())

        assert await host.received_peer_message("msg-1") == envelope

    async def test_an_unknown_id_raises(self, tmp_path) -> None:
        broker = _inbox_broker(tmp_path)
        host = BrokerForgeMcpHost(broker, MagicMock())

        with pytest.raises(PeerMessageError, match="received no peer message 'nope'"):
            await host.received_peer_message("nope")

    async def test_a_restored_message_can_still_be_answered(self, tmp_path) -> None:
        before = _inbox_broker(tmp_path)
        await _accept(before, _message())
        after = _inbox_broker(tmp_path)
        after._restore_durable_runtime_state(_restart_frames(before))

        assert after.received_peer_message("msg-1").sender.id == SENDER_ID


class TestCounting:
    async def test_every_outcome_is_counted_with_sender_recipient_channel_and_outcome(
        self, tmp_path, counted
    ) -> None:
        broker = _inbox_broker(tmp_path)

        await _accept(broker, _message("m-1"))
        await _accept(broker, _message("m-1"))
        with pytest.raises(PeerMessageRefusedError):
            await broker.accept_peer_message(_message("m-2", recipient_id="elsewhere"))

        assert counted == [
            {
                "name": "skuld.peer.messages",
                "sender": SENDER_ID,
                "recipient": SESSION_ID,
                "channel": "session",
                "outcome": outcome,
            }
            for outcome in ("accepted", "delivered", "duplicate")
        ] + [
            {
                "name": "skuld.peer.messages",
                "sender": SENDER_ID,
                "recipient": "elsewhere",
                "channel": "session",
                "outcome": "refused",
            }
        ]

    async def test_the_channel_is_where_it_came_from(self, tmp_path, counted) -> None:
        broker = _inbox_broker(tmp_path, activity="active")
        mesh_sender = PeerAddress(PeerKind.MESH_PEER, "ravn-3")

        await _accept(broker, _message("m-room", room_id="room-1"))
        await _accept(broker, _message("m-mesh", sender=mesh_sender))
        await _accept(broker, _message("m-forced"), channel="mesh")

        assert [c["channel"] for c in counted] == ["room", "mesh", "mesh"]
        assert peer_channel(_message()) == "session"

    async def test_the_counts_per_pair_are_in_the_session_status(self, tmp_path) -> None:
        broker = _inbox_broker(tmp_path, activity="active")
        await _accept(broker, _message("m-1"))
        await _accept(broker, _message("m-1"))
        await _accept(broker, _message("m-2", sender_id=OTHER_SENDER_ID))
        await broker._report_activity_state("idle")
        await _settle()

        status = broker.peer_message_status()

        by_sender = {pair["sender"]: pair for pair in status["pairs"]}
        assert by_sender[SENDER_ID] == {
            "sender": SENDER_ID,
            "recipient": SESSION_ID,
            "accepted": 1,
            "duplicate": 1,
            "refused": 0,
            "delivered": 1,
        }
        assert by_sender[OTHER_SENDER_ID] == {
            "sender": OTHER_SENDER_ID,
            "recipient": SESSION_ID,
            "accepted": 1,
            "duplicate": 0,
            "refused": 0,
            "delivered": 1,
        }
        assert len(status["pairs"]) == 2
        await broker._report_activity_state("active")
        reported = [p["metadata"]["peer_messages"] for p in broker._posted if p]
        assert reported[-1] == status

    async def test_there_is_no_cap_on_a_conversation(self, tmp_path) -> None:
        broker = _inbox_broker(tmp_path)

        for index in range(60):
            await _accept(broker, _message(f"m-{index}"))

        (pair,) = broker.peer_message_status()["pairs"]
        assert pair["accepted"] == pair["delivered"] == 60
        assert pair["refused"] == 0

    async def test_a_session_with_no_peer_traffic_reports_no_peer_status(self, tmp_path) -> None:
        broker = _inbox_broker(tmp_path)

        await broker._report_activity_state("active")

        assert all("peer_messages" not in p["metadata"] for p in broker._posted)


class TestRendering:
    def test_one_message_is_rendered_exactly_as_the_contract_has_it(self) -> None:
        envelope = _message()
        assert render_peer_turn([PeerInboxEntry(envelope, "t")]) == render_for_harness(envelope)

    def test_a_batch_names_its_size_and_separates_the_messages(self) -> None:
        entries = [PeerInboxEntry(_message(f"m-{i}"), "t") for i in range(2)]

        text = render_peer_turn(entries)

        assert text.startswith("[2 messages arrived while you were working, oldest first]")
        assert text.count("\n\n---\n\n") == 1

    def test_the_turn_id_is_stable_for_a_batch_and_differs_between_batches(self) -> None:
        assert peer_turn_id("s", ["a", "b"]) == peer_turn_id("s", ["a", "b"])
        assert peer_turn_id("s", ["a", "b"]) != peer_turn_id("s", ["a"])
        assert peer_turn_id("s", ["a"]) != peer_turn_id("t", ["a"])


class TestStartup:
    async def test_a_restored_message_is_handed_over_once_the_transport_is_up(
        self, tmp_path
    ) -> None:
        before = _inbox_broker(tmp_path, activity="active")
        await _accept(before, _message())
        broker = Broker(
            SkuldSettings(
                session={"id": SESSION_ID, "workspace_dir": str(tmp_path / "workspace")},
                transport="subprocess",
                chronicle_watcher_enabled=False,
            )
        )
        broker._restore_durable_peer_inbox(_restart_frames(before))
        injected = asyncio.Event()

        async def inject() -> None:
            injected.set()

        broker._inject_at_turn_boundary = inject

        try:
            await broker.startup()
            await asyncio.wait_for(injected.wait(), timeout=5)
        finally:
            await broker.shutdown()


class TestMeshEndToEnd:
    """The sending host and the receiving inbox, joined by a fake mesh: their shapes agree."""

    def _adapter(self, broker: Broker, peer_id: str, mesh=None) -> SkuldMeshAdapter:
        return SkuldMeshAdapter(
            participant=SimpleNamespace(peer_id=peer_id, mesh=mesh),
            config=MeshConfig(enabled=True),
            session_id=str(broker.session_id),
            peer_inbox=broker,
        )

    async def test_a_mesh_send_lands_in_the_peer_inbox_addressed_by_mesh_peer_id(
        self, tmp_path
    ) -> None:
        receiver = _inbox_broker(tmp_path / "receiver")
        receiver._mesh_adapter = self._adapter(receiver, "receiver-peer")

        class FakeMesh:
            async def send(self, target, payload, timeout_s=None):
                assert target == "receiver-peer"
                return await receiver._mesh_adapter._handle_rpc(payload)

        sender = Broker(
            SkuldSettings(
                session={"id": SENDER_ID, "workspace_dir": str(tmp_path / "sender")},
                transport="subprocess",
                mesh={"enabled": True},
                chronicle_watcher_enabled=False,
            )
        )
        sender._mesh_adapter = self._adapter(sender, "sender-peer", mesh=FakeMesh())
        host = BrokerForgeMcpHost(sender, MagicMock())

        result = await host.send_mesh_message(
            "receiver-peer",
            "Is the cluster healthy?",
            expect_reply=True,
            request_id="r-1",
            in_reply_to="",
        )
        await _settle()

        held = receiver.received_peer_message(result["message_id"])
        assert held.sender.id == SENDER_ID
        assert held.recipient.id == "receiver-peer"
        assert held.reply == ReplyRoute(ReplyKind.MESH, "sender-peer")
        assert _sent_texts(receiver) == [render_for_harness(held)]

    async def test_a_mesh_message_for_another_peer_is_refused_back_to_the_sender(
        self, tmp_path
    ) -> None:
        receiver = _inbox_broker(tmp_path / "receiver")
        receiver._mesh_adapter = self._adapter(receiver, "receiver-peer")
        stray = _message(recipient=PeerAddress(PeerKind.MESH_PEER, "someone-else"))

        answer = await receiver._mesh_adapter._handle_rpc(
            {"type": PEER_MESSAGE_FRAME, "envelope": stray.to_wire()}
        )

        assert answer["status"] == "refused"
        assert "mesh peer receiver-peer" in answer["error"]
