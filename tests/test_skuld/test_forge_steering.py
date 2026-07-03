"""Scenario Group B — STEERING / restart (broker + real-tmux tier).

Every test here drives a REAL ``skuld.broker.Broker`` wired to a REAL
``TmuxInteractiveTransport`` running ``fakeagent`` inside tmux, exercising the
native-steering inbound path end to end:

  * B1 — an idle browser message starts EXACTLY one turn (one ``result``).
  * B2 — a mid-turn steer keeps the in-flight turn alive (no restart, no second
    ``result`` for the one ``work`` turn), delivers the steer text
    (``terminal_input_sent`` + ``user_delivered`` ACK), and refreshes the idle
    clock.
  * B3 — a wedged ``_send_lock`` (a stuck prior delivery) is BOUNDED: a steer
    surfaces a loud ``user_delivery_failed`` within ~``_deliver_timeout_s``
    rather than deadlocking.
  * B4 — steering AFTER a WS crash + reconnect (Bug 3): the ACK with the matching
    ``request_id`` reaches the NEW client and the agent actually received the
    text (``terminal_input_sent`` on the pane).
  * B6 — ordering under burst: 5 rapid messages during a ``work`` turn deliver in
    order (send-lock serialization) and each is ACKed.

All are @pytest.mark.integration (the default pytest addopts deselect them) and
skip when tmux is unavailable. Run them on the real-tmux tier:

    SKULD__TMUX_REMOTE_CONTROL=0 uv run pytest -m integration \
        tests/test_skuld/test_forge_steering.py -p no:cacheprovider -q -rs
"""

from __future__ import annotations

import asyncio
import shutil

import pytest

from tests.support.forge import BrokerHarness
from tests.support.forge.tmux_page import TmuxPage


def _require_tmux() -> None:
    if shutil.which("tmux") is None:
        pytest.skip("tmux is not installed")


async def _wait_until(predicate, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not met before timeout")


def _page(h: BrokerHarness) -> TmuxPage:
    """Bind a TmuxPage to the harness transport's private socket + session."""
    transport = h.transport
    return TmuxPage(
        str(transport._socket_path),  # noqa: SLF001 - test seam
        transport.session_id or "",
    )


def _delivered_acks(frames: list[dict]) -> list[dict]:
    return [f for f in frames if f.get("type") == "user_delivered"]


def _failed_acks(frames: list[dict]) -> list[dict]:
    return [f for f in frames if f.get("type") == "user_delivery_failed"]


# --------------------------------------------------------------------------- B1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_b1_idle_message_starts_exactly_one_turn() -> None:
    """B1: an idle browser message starts exactly one turn -> exactly one result."""
    _require_tmux()

    async with BrokerHarness(hooks=True, idle_timeout_s=0.3) as h:
        client = await h.connect()

        client.send({"type": "message", "content": "say:pong"})

        await client.wait_for(
            lambda frames: any(f.get("type") == "result" for f in frames),
            timeout=8.0,
        )
        # Give the idle watchdog a generous beat to confirm it does NOT
        # double-close the single idle turn.
        await asyncio.sleep(0.8)

        results = client.frames_of_type("result")

    assert len(results) == 1, (
        f"an idle message must start exactly one turn; got {len(results)} results: {results}"
    )


# --------------------------------------------------------------------------- B2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_b2_mid_turn_steer_keeps_turn_and_delivers() -> None:
    """B2: a mid-turn steer keeps the SAME turn (no restart) and delivers the text.

    Broker + real-tmux analogue of test_mid_turn_message_steers_without_stopping_turn.
    """
    _require_tmux()

    async with BrokerHarness(hooks=True, idle_timeout_s=0.3) as h:
        client = await h.connect()

        # Start a long (~3s) turn. In hook mode the turn ends only on the Stop
        # hook the agent posts after `work:` completes, so we have a wide window
        # to steer into.
        client.send({"type": "message", "content": "work:3"})
        await _wait_until(
            lambda: getattr(h.transport, "is_turn_active", False) is True,
            timeout=5.0,
        )
        # Confirm the turn is genuinely streaming before steering.
        await client.wait_for(
            lambda frames: (
                any("working" in str(f.get("text", "")) for f in frames)
                or getattr(h.transport, "is_turn_active", False) is True
            ),
            timeout=5.0,
        )

        results_before = len(client.frames_of_type("result"))
        assert results_before == 0, "the work turn must not have closed yet"

        # Steer ~1s into the in-flight turn.
        await asyncio.sleep(1.0)
        assert getattr(h.transport, "is_turn_active", False) is True, (
            "the long turn ended before we could steer it"
        )
        idle_clock_before = h.transport._turn_last_output_at  # noqa: SLF001

        client.send(
            {
                "type": "message",
                "content": "say:STEERED",
                "request_id": "b2-steer",
            }
        )

        # The steer is delivered (terminal_input_sent flows transport->client)
        # and ACKed by request_id — WITHOUT restarting the turn.
        await client.wait_for(
            lambda frames: any(f.get("type") == "terminal_input_sent" for f in frames),
            timeout=5.0,
        )
        await client.wait_for(
            lambda frames: any(
                f.get("type") == "user_delivered" and f.get("request_id") == "b2-steer"
                for f in frames
            ),
            timeout=5.0,
        )

        # The idle clock was refreshed by the steer (mid-turn steer path).
        idle_clock_after = h.transport._turn_last_output_at  # noqa: SLF001
        assert idle_clock_after is not None
        if idle_clock_before is not None:
            assert idle_clock_after >= idle_clock_before, (
                "the mid-turn steer must refresh the idle clock, not rewind it"
            )

        # The PRODUCT contract (mirrors the unit test
        # test_mid_turn_message_steers_without_stopping_turn): a mid-turn steer is
        # a NON-DISRUPTIVE native delivery — at steer time the in-flight turn is
        # NOT stopped or restarted. So the moment the steer is delivered/ACKed:
        #   * the same turn is still active, and
        #   * NO new result has been emitted for it (no premature close), and
        #   * no interrupt (C-c) was sent to the pane.
        assert getattr(h.transport, "is_turn_active", False) is True, (
            "the steer must keep the in-flight turn alive, not restart it"
        )
        results_at_steer = client.frames_of_type("result")
        assert results_at_steer == [], (
            "a mid-turn steer must not close/restart the running turn: a result "
            f"was emitted at steer time: {results_at_steer}"
        )
        page = _page(h)
        snapshot_at_steer = await page.snapshot()
        assert "interrupted" not in snapshot_at_steer, (
            "the steer must NOT interrupt the running work turn (saw 'interrupted')"
        )

        # The steered text reaches the pane (the agent received the keystrokes).
        await page.wait_for_text("STEERED", timeout=8.0)

        # Let the original work turn complete via its Stop hook. NOTE: fakeagent
        # reads stdin line-by-line and cannot truly interleave a steer INTO a
        # running `work:` loop the way the real Claude CLI does — the buffered
        # steer line is processed as a follow-up after `work:` returns, so a
        # second result eventually appears. That extra result is a fakeagent
        # stdin-buffering artifact, NOT a broker/transport restart; the product
        # contract proven above is that the broker/transport did NOT stop or
        # restart the turn at steer time.
        await client.wait_for(
            lambda frames: any(
                f.get("type") == "result" and f.get("result") == "done" for f in frames
            ),
            timeout=10.0,
        )

    work_results = [r for r in client.frames_of_type("result") if r.get("result") == "done"]
    assert len(work_results) == 1, (
        f"the original work turn must close exactly once; got {work_results}"
    )


# --------------------------------------------------------------------------- B3


@pytest.mark.integration
@pytest.mark.asyncio
async def test_b3_wedged_send_lock_is_bounded_not_deadlocked() -> None:
    """B3: a wedged send-lock surfaces a loud user_delivery_failed, not a deadlock."""
    _require_tmux()

    async with BrokerHarness(hooks=True, idle_timeout_s=0.3) as h:
        client = await h.connect()
        # Make the bounded acquire fail fast for the test.
        h.transport._deliver_timeout_s = 0.1  # noqa: SLF001

        # Simulate a stuck prior delivery: hold the send lock so the next steer's
        # bounded acquire cannot proceed and must time out -> RuntimeError ->
        # broker emits user_delivery_failed.
        await h.transport._send_lock.acquire()  # noqa: SLF001
        try:
            client.send(
                {
                    "type": "message",
                    "content": "say:wedged",
                    "request_id": "b3-wedge",
                }
            )

            # Within ~_deliver_timeout_s the broker must surface a failure ACK
            # (correlated by request_id) rather than hanging forever.
            await client.wait_for(
                lambda frames: any(
                    f.get("type") == "user_delivery_failed" and f.get("request_id") == "b3-wedge"
                    for f in frames
                ),
                timeout=4.0,
            )
            failed = _failed_acks(client.frames)
            assert failed, "expected a user_delivery_failed ACK for the wedged steer"
            assert failed[-1].get("status") == "failed"
            assert "busy" in str(failed[-1].get("error", "")), (
                f"the failure must explain the wedge; got {failed[-1].get('error')!r}"
            )
        finally:
            h.transport._send_lock.release()  # noqa: SLF001

        # The lock is reusable once the wedged holder releases — no permanent
        # deadlock. A fresh idle message now delivers and ACKs cleanly.
        assert not h.transport._send_lock.locked()  # noqa: SLF001
        client.send(
            {
                "type": "message",
                "content": "say:recovered",
                "request_id": "b3-recover",
            }
        )
        await client.wait_for(
            lambda frames: any(
                f.get("type") == "user_delivered" and f.get("request_id") == "b3-recover"
                for f in frames
            ),
            timeout=8.0,
        )


# --------------------------------------------------------------------------- B4


@pytest.mark.integration
@pytest.mark.asyncio
async def test_b4_steer_after_reconnect_reaches_new_client_and_agent() -> None:
    """B4 (Bug 3): a steer after WS crash+reconnect ACKs to the NEW client and lands.

    The highest-value row: connect, start a long turn, abruptly kill the client
    mid-turn, reconnect a FRESH client, send a steer, and assert (a) the
    user_delivered ACK with the matching request_id reaches the new client and
    (b) the agent actually received the text (a terminal_input_sent for the steer
    flows to the new client and the pane shows the typed text).
    """
    _require_tmux()

    async with BrokerHarness(hooks=True, idle_timeout_s=0.3) as h:
        first = await h.connect()

        # Start a long (~5s) turn so it is still in flight when we crash + steer.
        first.send({"type": "message", "content": "work:5"})
        await _wait_until(
            lambda: getattr(h.transport, "is_turn_active", False) is True,
            timeout=5.0,
        )
        # Make sure the turn is genuinely running before we crash.
        await first.wait_for(
            lambda frames: (
                any("working" in str(f.get("text", "")) for f in frames)
                or getattr(h.transport, "is_turn_active", False) is True
            ),
            timeout=5.0,
        )

        # Abruptly crash the browser socket mid-turn (next receive raises
        # WebSocketDisconnect, unwinding handle_websocket like a real drop).
        await h.kill(first)

        # The turn must survive the client crash — the transport keeps running.
        assert getattr(h.transport, "is_turn_active", False) is True, (
            "killing the client must not stop the in-flight agent turn"
        )

        # Reconnect a FRESH browser client.
        second = await h.connect()
        # Count terminal_input_sent already present on the fresh client (the
        # reconnect replay may carry some) so we assert on the NEW steer's input.
        inputs_before = len(second.frames_of_type("terminal_input_sent"))

        # Steer through the reconnected client.
        second.send(
            {
                "type": "message",
                "content": "say:RECONNECT_STEER",
                "request_id": "b4-reconnect",
            }
        )

        # (a) The ACK with the matching request_id reaches the NEW client.
        await second.wait_for(
            lambda frames: any(
                f.get("type") == "user_delivered" and f.get("request_id") == "b4-reconnect"
                for f in frames
            ),
            timeout=8.0,
        )
        delivered = [
            f for f in _delivered_acks(second.frames) if f.get("request_id") == "b4-reconnect"
        ]
        assert delivered, "the steer ACK must reach the reconnected client"
        assert delivered[-1].get("status") == "delivered"

        # (b) The agent actually received the text: a NEW terminal_input_sent for
        # the steer flowed to the new client, and the pane shows the typed text.
        await second.wait_for(
            lambda frames: (
                len([f for f in frames if f.get("type") == "terminal_input_sent"]) > inputs_before
            ),
            timeout=8.0,
        )
        page = _page(h)
        await page.wait_for_text("RECONNECT_STEER", timeout=8.0)


# --------------------------------------------------------------------------- B6


@pytest.mark.integration
@pytest.mark.asyncio
async def test_b6_burst_messages_deliver_in_order_and_each_acked() -> None:
    """B6: a burst of 5 messages during a turn delivers in order, each ACKed."""
    _require_tmux()

    burst = [f"say:BURST_{i}" for i in range(5)]
    request_ids = [f"b6-{i}" for i in range(5)]

    async with BrokerHarness(hooks=True, idle_timeout_s=0.3) as h:
        client = await h.connect()

        # Start a long turn so the burst lands while the agent is busy and the
        # send-lock has to serialise the deliveries.
        client.send({"type": "message", "content": "work:3"})
        await _wait_until(
            lambda: getattr(h.transport, "is_turn_active", False) is True,
            timeout=5.0,
        )

        # Fire 5 messages back-to-back with no awaits between them.
        for content, rid in zip(burst, request_ids, strict=True):
            client.send({"type": "message", "content": content, "request_id": rid})

        # Each message is ACKed (delivered) — correlated by request_id.
        await client.wait_for(
            lambda frames: {
                f.get("request_id") for f in frames if f.get("type") == "user_delivered"
            }.issuperset(set(request_ids)),
            timeout=12.0,
        )

        # Send-lock serialization preserves order: the terminal_input_sent /
        # paste events for the burst land in the order they were sent. We use the
        # pane snapshot as ground truth — the typed lines appear in order.
        page = _page(h)
        for content in burst:
            needle = content[len("say:") :]
            await page.wait_for_text(needle, timeout=12.0)

        snapshot = await page.snapshot()

    positions = []
    for content in burst:
        needle = content[len("say:") :]
        idx = snapshot.find(needle)
        assert idx != -1, f"{needle!r} missing from the pane snapshot:\n{snapshot}"
        positions.append(idx)
    assert positions == sorted(positions), (
        f"burst messages must appear in send order; positions={positions}\n{snapshot}"
    )
