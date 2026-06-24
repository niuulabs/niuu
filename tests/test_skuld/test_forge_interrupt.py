"""Scenario Group C — INTERRUPTS (forge tmux harness, broker tier).

These drive a REAL ``skuld.broker.Broker`` against a REAL tmux session running
``fakeagent``, with an in-process fake browser client. They lock the interrupt
contract end to end:

  * C1 — interrupt cancels a turn: a long ``work:10`` turn is in flight; the
    browser sends ``{"type": "interrupt"}``; the transport sends a ``C-c``
    keystroke (``terminal_key_sent`` with ``key == "C-c"``) and the turn closes
    with a ``result`` carrying ``is_error`` + ``stop_reason == "interrupted"``.
    ``fakeagent`` prints ``interrupted`` on SIGINT, so the cancelled turn's text
    is captured.
  * C2 — interrupt then resume: after C1 the session is still usable — a fresh
    ``say:hi`` message starts a NEW turn and completes (its own ``result``).
  * C3 — interrupt with no active turn: a safe no-op — no spurious ``result``
    frame is produced (the C-c may still be sent, but nothing closes a turn).

All are @pytest.mark.integration (the default pytest addopts deselect them) and
skip when tmux is unavailable. They run on the real-tmux tier:

    SKULD__TMUX_REMOTE_CONTROL=0 uv run pytest -m integration \
        tests/test_skuld/test_forge_interrupt.py -p no:cacheprovider -q -rs

The ``hooks=False`` (idle-watchdog / synthetic-turn) path is used so the
interrupt's own synthetic ``result`` is the single, deterministic completion
signal — there is no concurrent Stop-hook turn to race with.
"""

from __future__ import annotations

import asyncio
import shutil

import pytest

from tests.support.forge import BrokerHarness


def _require_tmux() -> None:
    if shutil.which("tmux") is None:
        pytest.skip("tmux is not installed")


def _recorder(sink: list[dict]):
    async def _on_event(event: dict) -> None:
        sink.append(event)

    return _on_event


def _results(events: list[dict]) -> list[dict]:
    return [e for e in events if e.get("type") == "result"]


def _key_sends(events: list[dict]) -> list[dict]:
    return [e for e in events if e.get("type") == "terminal_key_sent"]


async def _wait_until(predicate, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not met before timeout")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_c1_interrupt_cancels_active_turn() -> None:
    """C1: an in-flight long turn is cancelled — C-c is sent, turn ends interrupted."""
    _require_tmux()

    events: list[dict] = []

    async with BrokerHarness(hooks=False, idle_timeout_s=0.25) as h:
        h.transport.on_event(_recorder(events))
        client = await h.connect()

        # Kick off a long, SIGINT-interruptible turn.
        client.send({"type": "message", "content": "work:10"})

        # Wait until the turn is genuinely in flight (the CLI has begun ticking).
        await _wait_until(lambda: h.transport.is_turn_active, timeout=8.0)
        await _wait_until(lambda: any("...working" in str(e) for e in events), timeout=8.0)

        # No result yet — the long turn must still be running.
        assert not _results(events), "turn finished before we interrupted it"

        # Browser cancels the turn.
        client.send({"type": "interrupt"})

        # The interrupt finishes a synthetic turn → exactly one result.
        await _wait_until(lambda: bool(_results(events)), timeout=8.0)

        results = _results(events)
        key_sends = _key_sends(events)

    # A C-c keystroke was delivered to the pane.
    assert any(e.get("key") == "C-c" for e in key_sends), (
        f"no C-c keystroke sent; key sends={[e.get('key') for e in key_sends]}"
    )

    # Exactly one result, marked as an interrupted error turn.
    assert len(results) == 1, f"expected exactly one result, got {len(results)}"
    last = results[-1]
    assert last.get("is_error") is True, f"interrupt result not is_error: {last}"
    assert last.get("stop_reason") == "interrupted", (
        f"interrupt result stop_reason != 'interrupted': {last}"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_c2_interrupt_then_resume_keeps_session_usable() -> None:
    """C2: after an interrupt, a fresh message starts and completes a new turn."""
    _require_tmux()

    events: list[dict] = []

    async with BrokerHarness(hooks=False, idle_timeout_s=0.25) as h:
        h.transport.on_event(_recorder(events))
        client = await h.connect()

        # --- interrupt a long turn (same as C1) ---
        client.send({"type": "message", "content": "work:10"})
        await _wait_until(lambda: h.transport.is_turn_active, timeout=8.0)
        await _wait_until(lambda: any("...working" in str(e) for e in events), timeout=8.0)
        client.send({"type": "interrupt"})
        await _wait_until(lambda: bool(_results(events)), timeout=8.0)

        first = _results(events)[-1]
        assert first.get("stop_reason") == "interrupted", (
            f"first (interrupted) turn malformed: {first}"
        )
        # The interrupt should have cleared the active turn.
        await _wait_until(lambda: not h.transport.is_turn_active, timeout=4.0)
        results_after_interrupt = len(_results(events))

        # --- resume: a brand-new message must start AND finish a fresh turn ---
        client.send({"type": "message", "content": "say:resumed"})

        # The fresh turn's text reaches the pane and a NEW result is produced.
        await _wait_until(lambda: any("resumed" in str(e) for e in events), timeout=8.0)
        await _wait_until(lambda: len(_results(events)) > results_after_interrupt, timeout=8.0)

        results = _results(events)
        # The browser also saw its message echoed back (session is live).
        confirms = client.frames_of_type("user_confirmed")

    assert len(results) >= 2, f"resume did not produce a second result; results={results}"
    second = results[-1]
    # A normal completion, not another interrupt.
    assert second.get("stop_reason") != "interrupted", (
        f"resumed turn unexpectedly marked interrupted: {second}"
    )
    assert second.get("is_error") is not True, f"resumed turn unexpectedly errored: {second}"
    assert "resumed" in str(second.get("result", "")), (
        f"resumed turn result did not carry the assistant text: {second}"
    )
    assert any(c.get("content") == "say:resumed" for c in confirms), (
        f"resume message was never confirmed to the browser; confirms={confirms}"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_c3_interrupt_with_no_active_turn_is_safe_noop() -> None:
    """C3: interrupting an idle session is a no-op — no spurious result frame."""
    _require_tmux()

    events: list[dict] = []

    async with BrokerHarness(hooks=False, idle_timeout_s=0.25) as h:
        h.transport.on_event(_recorder(events))
        client = await h.connect()

        # Session is idle (no message sent) — confirm there is no active turn.
        assert not h.transport.is_turn_active, "session unexpectedly had an active turn"
        assert not _results(events), "a result appeared before any turn was started"

        # Interrupt with nothing running.
        client.send({"type": "interrupt"})

        # Give the dispatch + any (incorrect) watchdog ample time to misbehave.
        await asyncio.sleep(1.0)

        results = _results(events)
        still_idle = h.transport.is_turn_active

    # The no-op must NOT fabricate a turn result and must leave the session idle.
    assert results == [], f"interrupt on an idle session produced a spurious result: {results}"
    assert still_idle is False, "interrupt on an idle session left a turn active"
