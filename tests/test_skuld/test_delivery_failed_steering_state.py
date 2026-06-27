"""INV-7 delivery integrity at the LIVE-BROKER frame tier (Epic F gap).

The delivery-integrity invariant is already pinned at two other tiers:

  * the REDUCER tier — ``test_transcript_reducer_parity.py::test_parity_delivery_state_failed``
    proves ``reduce_frames`` reconstructs ``steering_state=failed`` from the durable log;
  * the REST tier — ``test_rest.py`` proves a logged ``user_delivery_failed`` surfaces as a 502.

What was NOT pinned anywhere is the seam between them: the LIVE BROKER, when a bounded retry
TERMINALLY fails, must (a) write ``user_delivery_failed`` to the durable buffer FIRST and only
then broadcast it (the INV-1 superset ordering, for THIS frame kind, exercised against the real
``_emit_broker_frame`` choke point — the existing INV-7 retry tests mock ``_channels.broadcast``
and never enable the durable buffer, so they cannot observe append-before-broadcast); and (b)
leave the durable log in a state from which the user turn's delivery lifecycle reconstructs to
``pending -> failed`` via ``steering_state_from_frame`` — never stuck ``pending`` and never
``delivered``.

The assertions here are non-tautological: the broadcast-ordering check captures the buffer
contents AT broadcast time (an independent observation of "was it appended first"), and the
steering reconstruction is derived by an independent walk of the logged frames through
``steering_state_from_frame`` AND cross-checked against the shared ``reduce_frames`` rebuild —
the expectation is never compared against the same fold twice.

This box leaks ``SKULD__*``/``VOLUNDR*`` env that poisons ``SkuldSettings``; this file lives
under ``tests/test_skuld`` so ``tests/test_skuld/conftest.py`` autouse-strips them, but we also
build settings explicitly (no env-derived fields) to be belt-and-suspenders safe.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from niuu.domain.transcript_reducer import (
    reduce_frames,
    steering_state_from_frame,
    steering_target_id,
)
from skuld.broker import Broker
from skuld.config import SkuldSettings
from skuld.transports import TransportCapabilities

# Retry budget for the terminal-failure path: small but >1 so the bounded retry is exercised.
_MAX_ATTEMPTS = 3
# Zero backoff so the test never pays retry latency (the loop still runs MAX_ATTEMPTS times).
_NO_BACKOFF = 0.0


# --------------------------------------------------------------------------- local fixtures
#
# Reused-by-local-copy from test_broker.py (_retry_broker / _settle_delivery), kept small and
# self-contained per the file brief. The ONE meaningful difference from the test_broker.py
# helper is that this broker has the durable event-log buffer ENABLED (volundr_api_url truthy +
# event_log_enabled True, never starting the flush loop) so frames accumulate in
# ``broker._event_log_buffer`` and the superset/reconstruction can be observed.


def _broker(tmp_path) -> Broker:
    """A real Broker whose delivery terminally fails after bounded retry, with the durable
    event-log buffer live (so broker-originated frames persist) but never drained."""
    settings = SkuldSettings(
        session={"id": "test-session", "workspace_dir": str(tmp_path)},
        transport="sdk",
        # Truthy URL + enabled => _enqueue_event_log buffers; the flush loop is only started by
        # broker.startup() (never called here), so the buffer fully accumulates and is readable.
        volundr_api_url="http://harness.invalid",
        event_log_enabled=True,
        delivery={
            "max_attempts": _MAX_ATTEMPTS,
            "initial_backoff_seconds": _NO_BACKOFF,
            "max_backoff_seconds": _NO_BACKOFF,
        },
    )
    b = Broker(settings=settings)
    b._transport = AsyncMock()
    b._transport.capabilities = TransportCapabilities()
    b._transport.is_turn_active = False
    b._apply_retrieval_reflex = AsyncMock(side_effect=lambda m: m)
    b._save_conversation_history = MagicMock()
    return b


async def _settle_delivery() -> None:
    """Await the fire-and-forget ``transport-deliver-*`` task dispatch schedules, so the
    terminal failure (and its persisted+broadcast user_delivery_failed) is observable."""
    tasks = [
        t
        for t in asyncio.all_tasks()
        if t is not asyncio.current_task() and t.get_name().startswith("transport-deliver-")
    ]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def _instrument_broadcast(broker: Broker) -> list[tuple[dict, list[dict]]]:
    """Replace ``_channels.broadcast`` with a spy that records, for every broadcast frame, the
    SNAPSHOT of the durable buffer's payloads AT broadcast time. This is the independent
    observation that proves INV-1 append-before-broadcast: if a frame was appended FIRST, it is
    already present in the snapshot captured when its broadcast fires.

    Returns a list of (broadcast_frame, buffer_payloads_at_that_moment)."""
    log: list[tuple[dict, list[dict]]] = []

    async def _spy(frame: dict) -> None:
        snapshot = [dict(e["payload"]) for e in broker._event_log_buffer]
        log.append((frame, snapshot))

    broker._channels.broadcast = _spy  # type: ignore[assignment]
    return log


def _buffer_payloads(broker: Broker) -> list[dict]:
    return [e["payload"] for e in broker._event_log_buffer]


def _reconstruct_steering(payloads: list[dict]) -> dict[str, list[str]]:
    """Independently derive each user turn's delivery-state TRANSITIONS from the logged frames,
    using ONLY the public ``steering_state_from_frame`` policy — not the reducer's internal
    last-writer collapse. Returns {turn_id: [state, state, ...]} in log order. This is the
    independent expectation the broker's behaviour is checked against."""
    transitions: dict[str, list[str]] = {}
    for payload in payloads:
        kind = str(payload.get("type", ""))
        state = steering_state_from_frame(kind, payload)
        if state is None:
            continue
        tid = steering_target_id(kind, payload)
        if not tid:
            continue
        transitions.setdefault(tid, []).append(state)
    return transitions


class _Frame:
    """Minimal duck-typed durable-log frame for ``reduce_frames`` (structurally a
    ``SessionLogEntry`` without importing volundr at module load)."""

    def __init__(self, *, session_id: str, seq: int, payload: dict, request_id: str | None) -> None:
        self.session_id = session_id
        self.seq = seq
        self.kind = str(payload.get("type", "unknown"))
        self.payload = payload
        self.request_id = request_id
        self.ts = None


def _frames_from_buffer(broker: Broker) -> list[_Frame]:
    sid = str(broker.session_id)
    return [
        _Frame(
            session_id=sid,
            seq=int(entry["seq"]),
            payload=entry["payload"],
            request_id=entry.get("request_id"),
        )
        for entry in broker._event_log_buffer
    ]


# --------------------------------------------------------------------------- INV-7 tests


class TestTerminalDeliveryFailureSteeringState:
    """INV-7: a terminally-failed delivery is a SUPERSET frame and reconstructs to
    pending->failed — never stuck pending, never delivered — at the live-broker tier."""

    @pytest.mark.asyncio
    async def test_user_delivery_failed_is_appended_before_broadcast(self, tmp_path):
        """INV-7 / INV-1: when bounded retry terminally fails, ``user_delivery_failed`` is in the
        durable buffer BEFORE it is broadcast (persist-first superset), and it is present in the
        log exactly once with the failing error attached."""
        b = _broker(tmp_path)
        b._transport.send_message = AsyncMock(side_effect=RuntimeError("wedged forever"))
        broadcasts = _instrument_broadcast(b)

        await b._dispatch_browser_message({"content": "steer it", "request_id": "rq-fail"})
        await _settle_delivery()

        assert b._transport.send_message.await_count == _MAX_ATTEMPTS, (
            "bounded retry must exhaust every attempt before declaring terminal failure"
        )

        # The failure frame was broadcast...
        failed_broadcasts = [
            (frame, snap)
            for frame, snap in broadcasts
            if frame.get("type") == "user_delivery_failed"
        ]
        assert len(failed_broadcasts) == 1, "exactly one user_delivery_failed must be broadcast"
        failed_frame, snapshot_at_broadcast = failed_broadcasts[0]
        assert failed_frame["error"] == "wedged forever"

        # ...and it was ALREADY in the durable buffer when that broadcast fired (appended first).
        assert any(p.get("type") == "user_delivery_failed" for p in snapshot_at_broadcast), (
            "INV-1: the frame must be persisted to the durable buffer BEFORE it is broadcast"
        )

        # And it survives in the buffer exactly once (the durable log is the superset).
        logged_failed = [p for p in _buffer_payloads(b) if p.get("type") == "user_delivery_failed"]
        assert len(logged_failed) == 1
        assert logged_failed[0]["error"] == "wedged forever"

    @pytest.mark.asyncio
    async def test_steering_state_reconstructs_pending_then_failed(self, tmp_path):
        """INV-7: the logged frames carry the FULL pending->failed lifecycle for the user turn.

        Independent derivation via steering_state_from_frame must yield exactly [pending, failed]
        for the turn id, and the shared reduce_frames rebuild must stamp the user turn
        steering_state=failed — proving the turn is NEVER left stuck pending and NEVER delivered.
        """
        b = _broker(tmp_path)
        b._transport.send_message = AsyncMock(side_effect=RuntimeError("input channel wedged"))

        await b._dispatch_browser_message({"content": "do it", "request_id": "rq-1"})
        await _settle_delivery()

        payloads = _buffer_payloads(b)

        # The two delivery-lifecycle frames for the human turn must both be logged.
        confirmed = [p for p in payloads if p.get("type") == "user_confirmed"]
        assert len(confirmed) == 1, "the accept (user_confirmed=pending) must be persisted"
        turn_id = confirmed[0]["id"]
        assert confirmed[0]["steering_state"] == "pending"

        # Independent reconstruction (NOT the reducer's internal collapse): exactly pending->failed.
        transitions = _reconstruct_steering(payloads)
        assert transitions.get(turn_id) == ["pending", "failed"], (
            "the logged frames must encode the full pending->failed lifecycle for the turn — "
            f"got {transitions.get(turn_id)!r}"
        )

        # Cross-check against the SHARED reducer: the rebuilt user turn lands on failed.
        result = reduce_frames(_frames_from_buffer(b))
        user_turns = [t for t in result.turns if t["role"] == "user"]
        assert len(user_turns) == 1, "the user/user_confirmed pair must dedup to one user turn"
        rebuilt = user_turns[0]
        assert rebuilt["id"] == turn_id, "the rebuilt turn must carry the broker-minted id"
        assert rebuilt["metadata"]["steering_state"] == "failed", (
            "INV-7: the rebuilt turn must show the VISIBLE failed state"
        )
        assert rebuilt["metadata"]["steering_state"] != "pending", "never left stuck pending"

        # The in-memory live turn agrees with the rebuild (live == durable), and there is no
        # 'user_delivered' success ack masquerading as a delivery.
        live_turn = next(t for t in b._conversation_turns if t.role == "user")
        assert live_turn.metadata.get("steering_state") == "failed"
        assert not any(p.get("type") == "user_delivered" for p in payloads), (
            "a terminally-failed delivery must NOT also emit a success delivery ack"
        )

    @pytest.mark.asyncio
    async def test_durable_log_is_superset_of_failure_broadcast(self, tmp_path):
        """INV-1/INV-7: EVERY broker frame that reached a client during the failed delivery is
        present in the durable buffer (broadcast set ⊆ logged set) — the failure path adds no
        client-visible frame that escapes the log."""
        b = _broker(tmp_path)
        b._transport.send_message = AsyncMock(side_effect=RuntimeError("nope"))
        broadcasts = _instrument_broadcast(b)

        await b._dispatch_browser_message({"content": "hello", "request_id": "rq-7"})
        await _settle_delivery()

        broadcast_kinds = [frame.get("type") for frame, _snap in broadcasts]
        # The failure path must surface the confirm + the failure (a benign superset is allowed,
        # but at minimum these two must have reached the client).
        assert "user_confirmed" in broadcast_kinds
        assert "user_delivery_failed" in broadcast_kinds

        logged_kinds = [p.get("type") for p in _buffer_payloads(b)]
        for frame, _snap in broadcasts:
            # The 'error' frame the failure path also emits is broker-originated and likewise
            # persisted; every broadcast frame type must appear in the durable log.
            assert frame.get("type") in logged_kinds, (
                f"broadcast frame {frame.get('type')!r} escaped the durable log (INV-1 violated)"
            )


class TestMalformedInboundDoesNotTearDown:
    """INV-7 malformed-inbound at the DISPATCH tier (distinct from the handle_websocket-level
    coverage in test_broker.py): a malformed frame fed straight to _dispatch_browser_message
    neither raises nor wedges the broker, and a SUBSEQUENT valid message is still delivered."""

    @pytest.mark.asyncio
    async def test_malformed_then_valid_message_still_delivered(self, tmp_path):
        b = _broker(tmp_path)
        # Healthy transport this time — the point is the malformed frame must not poison it.
        b._transport.send_message = AsyncMock(return_value=None)
        _instrument_broadcast(b)

        # A malformed frame: an unknown type carrying no usable content. It falls through the
        # dispatch match to the default user-message branch, finds no content, and returns early
        # — no exception, no transport call, no teardown.
        await b._dispatch_browser_message({"type": "garbled_kind", "junk": [1, 2, 3]})
        await _settle_delivery()
        b._transport.send_message.assert_not_called()

        # A second, malformed-but-different shape (empty content) must ALSO be a clean no-op.
        await b._dispatch_browser_message({"content": ""})
        await _settle_delivery()
        b._transport.send_message.assert_not_called()

        # The follow-up VALID message must still be delivered — the socket/broker survived.
        await b._dispatch_browser_message({"content": "still here", "request_id": "after-bad"})
        await _settle_delivery()
        b._transport.send_message.assert_awaited_once()
        args, kwargs = b._transport.send_message.await_args
        assert args[0] == "still here"
        assert kwargs.get("request_id") == "after-bad"

    @pytest.mark.asyncio
    async def test_malformed_frame_does_not_corrupt_durable_log(self, tmp_path):
        """A malformed inbound frame contributes NO phantom user turn to the durable log, so a
        later rebuild stays clean (the malformed frame is ignored, not half-recorded)."""
        b = _broker(tmp_path)
        b._transport.send_message = AsyncMock(return_value=None)
        _instrument_broadcast(b)

        await b._dispatch_browser_message({"type": "garbled_kind"})
        await _settle_delivery()

        # Nothing user-facing was persisted for the malformed frame.
        payloads = _buffer_payloads(b)
        assert not any(p.get("type") in ("user", "user_confirmed") for p in payloads), (
            "a malformed frame must not write a phantom user turn to the durable log"
        )

        # The subsequent valid message rebuilds to exactly one clean user turn.
        await b._dispatch_browser_message({"content": "real one", "request_id": "ok"})
        await _settle_delivery()
        result = reduce_frames(_frames_from_buffer(b))
        user_turns = [t for t in result.turns if t["role"] == "user"]
        assert len(user_turns) == 1
        assert user_turns[0]["content"] == "real one"
