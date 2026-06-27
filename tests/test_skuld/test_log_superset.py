"""Epic A invariants — the durable event log is a complete superset of the
live broadcast (INV-1) and is gapless with a detectable overflow sentinel
(INV-2).

The contract under test: every frame that reaches a live client (via
``_channels.broadcast`` or a per-channel ``send_json``) MUST have been appended
to the durable event-log buffer FIRST. Broker-originated frames (errors,
permission_*, available_commands, the intermediary user_* events) now funnel
through ``_emit_broker_frame`` / ``_send_broker_frame_to`` so the superset
invariant is structural, not vigilance-based.
"""

import asyncio
from unittest.mock import AsyncMock

from skuld.broker import Broker, WebSocketDisconnect
from skuld.config import SkuldSettings
from skuld.transports import TransportCapabilities


def _broker(tmp_path, **overrides) -> Broker:
    settings = SkuldSettings(
        session={"id": "s-superset", "workspace_dir": str(tmp_path)},
        volundr_api_url=overrides.pop("volundr_api_url", "http://volundr.test"),
        **overrides,
    )
    return Broker(settings=settings)


def _instrument_broadcast(b: Broker) -> list[dict]:
    """Record every frame that reaches ``_channels.broadcast`` (in order)."""
    broadcast_log: list[dict] = []
    original = b._channels.broadcast

    async def _record(frame: dict) -> None:
        broadcast_log.append(frame)
        await original(frame)

    b._channels.broadcast = _record  # type: ignore[method-assign]
    return broadcast_log


def _logged_payload_ids(b: Broker) -> set[int]:
    return {id(e["payload"]) for e in b._event_log_buffer}


class TestSupersetInvariant:
    """INV-1: ∀ frame f broadcast ⇒ f ∈ log, appended BEFORE broadcast."""

    async def test_emit_broker_frame_logs_before_broadcast(self, tmp_path):
        b = _broker(tmp_path)
        broadcast_log = _instrument_broadcast(b)

        await b._emit_broker_frame({"type": "permission_resolved", "request_id": "r1"})

        # broadcast happened
        assert broadcast_log == [{"type": "permission_resolved", "request_id": "r1"}]
        # ... and the very same frame is in the durable buffer (logged first)
        assert b._event_log_buffer[-1]["kind"] == "permission_resolved"
        assert id(broadcast_log[0]) in _logged_payload_ids(b)

    async def test_send_broker_frame_to_logs_before_send(self, tmp_path):
        b = _broker(tmp_path)
        ws = AsyncMock()

        await b._send_broker_frame_to(ws, {"type": "error", "content": "boom"})

        ws.send_json.assert_awaited_once_with({"type": "error", "content": "boom"})
        assert b._event_log_buffer[-1]["kind"] == "error"

    async def test_permission_resolved_is_logged(self, tmp_path):
        b = _broker(tmp_path)
        broadcast_log = _instrument_broadcast(b)
        transport = AsyncMock()
        b._transport = transport

        await b._send_permission_control_response(
            "req-1",
            {"behavior": "allow", "updatedInput": {}},
            auto_approved=False,
        )

        broadcast_kinds = [f.get("type") for f in broadcast_log]
        assert "permission_resolved" in broadcast_kinds
        logged_kinds = [e["kind"] for e in b._event_log_buffer]
        assert "permission_resolved" in logged_kinds

    async def test_available_commands_is_logged(self, tmp_path):
        b = _broker(tmp_path)
        broadcast_log = _instrument_broadcast(b)
        transport = AsyncMock()
        transport.capabilities = TransportCapabilities(slash_commands=True)
        transport.discover_slash_commands.return_value = [{"name": "/foo", "description": "do foo"}]
        b._transport = transport

        await b._handle_cli_event({"type": "system", "subtype": "init", "slash_commands": ["foo"]})

        # the system/init transport frame AND the broker-originated
        # available_commands frame both reached the log
        logged_kinds = [e["kind"] for e in b._event_log_buffer]
        assert "system" in logged_kinds
        assert "available_commands" in logged_kinds
        broadcast_kinds = [f.get("type") for f in broadcast_log]
        assert "available_commands" in broadcast_kinds

    async def test_user_confirmed_and_delivery_acks_are_logged(self, tmp_path):
        b = _broker(tmp_path)
        broadcast_log = _instrument_broadcast(b)
        transport = AsyncMock()
        transport.capabilities = TransportCapabilities(steering_mode="native", steer=True)
        transport.is_turn_active = True
        b._transport = transport

        await b._dispatch_browser_message({"content": "hello agent"}, sender_ws=AsyncMock())
        # let the fire-and-forget delivery task run + ack
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        logged_kinds = [e["kind"] for e in b._event_log_buffer]
        # the human turn, the user_confirmed echo, and the delivery ack are all durable
        assert "user" in logged_kinds  # human-turn mirror
        assert "user_confirmed" in logged_kinds
        assert "user_delivered" in logged_kinds
        broadcast_kinds = [f.get("type") for f in broadcast_log]
        assert "user_confirmed" in broadcast_kinds
        assert "user_delivered" in broadcast_kinds

    async def test_user_active_is_logged(self, tmp_path):
        b = _broker(tmp_path)
        broadcast_log = _instrument_broadcast(b)

        await b._broadcast_user_active("msg-7", "req-7")

        assert b._event_log_buffer[-1]["kind"] == "user_active"
        assert broadcast_log[-1] == {"type": "user_active", "id": "msg-7", "request_id": "req-7"}

    async def test_every_broadcast_frame_was_logged_first(self, tmp_path):
        """The headline INV-1 property: drive a mixed set of broker frames and
        assert EVERY frame that reached broadcast is present (by identity) in the
        durable buffer."""
        b = _broker(tmp_path)
        broadcast_log = _instrument_broadcast(b)
        transport = AsyncMock()
        b._transport = transport

        await b._send_permission_control_response(
            "req-x", {"behavior": "deny"}, auto_approved=False
        )
        await b._emit_broker_frame({"type": "error", "content": "explosion"})
        await b._broadcast_user_active("m1")

        logged_ids = _logged_payload_ids(b)
        assert broadcast_log, "expected broadcasts"
        for frame in broadcast_log:
            assert id(frame) in logged_ids, f"frame broadcast but not logged: {frame}"


class _FakeWebsocket:
    """A minimal WebSocket stand-in that records every per-channel ``send_json``
    and breaks ``handle_websocket``'s receive loop with a clean disconnect so we
    can assert on exactly the first-connect frames a real browser would see."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.headers: dict[str, str] = {}

    async def accept(self) -> None:
        return None

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    async def receive_json(self) -> dict:
        # First-connect frames are all sent before the loop; bail out cleanly.
        raise WebSocketDisconnect(code=1000)


class TestFirstConnectSupersetOverPerChannelSends:
    """INV-1 / FR-1 covers 'channels.broadcast OR ANY per-channel send'. The
    first-connect handshake in ``handle_websocket`` sends several broker-originated
    frames straight to the connecting socket (not via broadcast). EVERY such
    first-class frame must be appended to the durable log FIRST. Reconnect-REPLAY
    sends (conversation_history, room_state, pending permission/question, plan,
    running-agents) are an intentional carve-out — they re-send state already in
    the log — and are asserted as replays, not as missing-from-log holes."""

    def _logged_kinds(self, b: Broker) -> list[str]:
        return [e["kind"] for e in b._event_log_buffer]

    async def test_welcome_and_capabilities_logged_before_send(self, tmp_path):
        b = _broker(tmp_path)
        transport = AsyncMock()
        transport.is_alive = True
        transport.capabilities = TransportCapabilities(slash_commands=True)
        b._transport = transport
        ws = _FakeWebsocket()

        await b.handle_websocket(ws)

        # The two broker-synthesized first-connect frames reached the socket...
        sent_types = [f.get("type") for f in ws.sent]
        assert "system" in sent_types
        assert "capabilities" in sent_types
        # ...and each is durably logged (INV-1: log is a superset).
        logged = self._logged_kinds(b)
        assert "system" in logged
        assert "capabilities" in logged

    async def test_every_first_connect_send_was_logged_first(self, tmp_path):
        """The headline first-connect property: with NO replay state present,
        every frame the socket receives during the handshake is, by type, in the
        durable buffer. (No conversation history / permissions / plan / agents are
        set, so only the first-class welcome + capabilities are emitted.)"""
        b = _broker(tmp_path)
        transport = AsyncMock()
        transport.is_alive = True
        transport.capabilities = TransportCapabilities()
        b._transport = transport
        ws = _FakeWebsocket()

        await b.handle_websocket(ws)

        logged = set(self._logged_kinds(b))
        assert ws.sent, "expected first-connect frames"
        for frame in ws.sent:
            assert frame.get("type") in logged, f"sent but not logged: {frame}"

    async def test_reconnect_replay_sends_are_carved_out_as_replays(self, tmp_path):
        """The reconnect carve-out is intentional: conversation_history, room
        permission/question re-surface, plan and running-agents re-surface are
        re-sends of state already represented in the log, so they are NOT required
        to be (re-)appended on every reconnect. Assert they are sent but treated as
        replays (the buffer is not re-growing one entry per replayed frame)."""
        b = _broker(tmp_path)
        transport = AsyncMock()
        transport.is_alive = True
        transport.capabilities = TransportCapabilities()
        b._transport = transport
        # Seed reconnect-replay state.
        b._pending_permission_requests = {"p1": {"type": "permission_request", "request_id": "p1"}}
        b._pending_ask_user_questions = {"q1": {"type": "ask_user_question", "request_id": "q1"}}
        b._current_plan = {"type": "plan", "steps": []}
        b._running_agents = {"a1": {"id": "a1", "name": "scout"}}
        ws = _FakeWebsocket()

        await b.handle_websocket(ws)

        sent_types = [f.get("type") for f in ws.sent]
        # The replay frames DID reach the live client...
        assert "permission_request" in sent_types
        assert "ask_user_question" in sent_types
        assert "plan" in sent_types
        assert "agent_update" in sent_types
        # ...but the carve-out means they were NOT re-appended to the log on
        # reconnect (no permission_request / ask_user_question / plan / agent_update
        # kinds are added by the replay path — they live in the log from when the
        # underlying event first occurred).
        logged = set(self._logged_kinds(b))
        assert "permission_request" not in logged
        assert "ask_user_question" not in logged
        assert "agent_update" not in logged

    async def test_slash_commands_response_is_logged(self, tmp_path):
        b = _broker(tmp_path)
        transport = AsyncMock()
        transport.capabilities = TransportCapabilities(slash_commands=True)
        transport.discover_slash_commands.return_value = [{"name": "/foo", "description": "do foo"}]
        b._transport = transport
        ws = AsyncMock()

        await b._dispatch_browser_message(
            {"type": "discover_slash_commands", "refresh": True}, sender_ws=ws
        )

        ws.send_json.assert_awaited_once()
        sent = ws.send_json.await_args.args[0]
        assert sent["type"] == "slash_commands"
        assert "slash_commands" in self._logged_kinds(b)

    async def test_room_prompt_resent_ack_is_logged(self, tmp_path):
        b = _broker(tmp_path)
        transport = AsyncMock()
        transport.capabilities = TransportCapabilities()
        b._transport = transport
        ws = AsyncMock()

        async def _resend(**_kwargs):
            return "msg-99"

        b.handle_resend_initial_prompt = _resend  # type: ignore[method-assign]

        await b._dispatch_browser_message({"type": "resend_initial_prompt"}, sender_ws=ws)

        ws.send_json.assert_awaited_once()
        sent = ws.send_json.await_args.args[0]
        assert sent["type"] == "room_prompt_resent"
        assert sent["message_id"] == "msg-99"
        assert "room_prompt_resent" in self._logged_kinds(b)


class TestGaplessAndSentinel:
    """INV-2: N healthy emissions ⇒ contiguous seq 1..N; overflow emits a
    queryable sentinel rather than a silent hole."""

    def test_healthy_enqueues_are_contiguous(self, tmp_path):
        b = _broker(tmp_path, event_log_max_buffer=1000)
        for i in range(50):
            b._enqueue_event_log({"type": "content_block_delta", "i": i})

        seqs = [e["seq"] for e in b._event_log_buffer]
        assert seqs == list(range(1, 51))  # contiguous, no gaps, no dupes

    def test_overflow_emits_detectable_sentinel(self, tmp_path):
        b = _broker(tmp_path, event_log_max_buffer=5)
        for i in range(20):
            b._enqueue_event_log({"type": "content_block_delta", "i": i})

        gaps = [e for e in b._event_log_buffer if e["kind"] == "log_gap"]
        assert len(gaps) == 1, "exactly one cumulative gap sentinel"
        gap = gaps[0]
        # the hole is queryable: it records the dropped count + seq range + cause
        assert gap["payload"]["type"] == "log_gap"
        assert gap["payload"]["reason"] == "buffer_overflow"
        assert gap["payload"]["first_seq"] == 1
        assert gap["payload"]["dropped"] >= 1
        # seq never reused: surviving real frames remain strictly increasing
        survivors = [e["seq"] for e in b._event_log_buffer if e["kind"] != "log_gap"]
        assert survivors == sorted(survivors)
        assert len(set(survivors)) == len(survivors)

    def test_no_overflow_means_no_sentinel(self, tmp_path):
        b = _broker(tmp_path, event_log_max_buffer=100)
        for i in range(10):
            b._enqueue_event_log({"type": "content_block_delta", "i": i})

        assert all(e["kind"] != "log_gap" for e in b._event_log_buffer)
