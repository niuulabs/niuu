"""Phase-0 proof-of-life suite for the forge tmux harness (broker tier).

Every test here drives a REAL tmux session via the REAL transport (and, for the
end-to-end case, a REAL ``skuld.broker.Broker``) against the dependency-free
``fakeagent``. They REPLACE the role of the old 4-line bash smoke (which stays
in place) with assertions on the actual event pipeline:

  * A2 — synthetic turn (no hooks): the idle watchdog closes exactly one turn and
    a terminal_frame carries the assistant text.
  * A3 — hook turn: the Stop hook closes the turn and ``result.modelUsage`` is
    populated (drives message_count).
  * A1 — capabilities: a fake browser client receives the handshake advertising
    terminal_* + steer + interrupt + slash_commands, plus terminal_pane_opened
    and system/init.
  * E2E — a normal browser message round-trips to the agent, the assistant
    response is broadcast to the client, the durable log captured the turn, and
    ``rebuild_turns`` reproduces a turn from that log.

All are @pytest.mark.integration (the default pytest addopts deselect them) and
skip when tmux is unavailable.
"""

from __future__ import annotations

import asyncio
import shutil

import pytest

from tests.support.forge import BrokerHarness


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


def _terminal_frames(events: list[dict]) -> list[str]:
    out: list[str] = []
    for event in events:
        if event.get("type") not in {"terminal_frame", "terminal_snapshot"}:
            continue
        rows = event.get("rows")
        if isinstance(rows, list):
            out.append("\n".join(rows))
            continue
        out.append(str(event.get("text", "")))
    return out


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a2_synthetic_turn_closes_via_idle_watchdog() -> None:
    """A2: no hooks — idle watchdog closes exactly one turn; frame carries text."""
    _require_tmux()

    events: list[dict] = []

    async with BrokerHarness(hooks=False, idle_timeout_s=0.25, start_transport=True) as h:
        h.transport.on_event(_recorder(events))

        await h.transport.send_message("say:hello")

        await _wait_until(lambda: any(e.get("type") == "result" for e in events), timeout=8.0)
        # Give the watchdog a beat to confirm it does NOT double-close.
        await asyncio.sleep(0.6)

        results = [e for e in events if e.get("type") == "result"]
        frames = _terminal_frames(events)

    assert len(results) == 1, f"expected exactly one result, got {len(results)}"
    assert any("hello" in frame for frame in frames), (
        "no terminal_frame carried the assistant text 'hello'"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a3_hook_turn_closes_on_stop_with_model_usage() -> None:
    """A3: hooks on — the Stop hook closes the turn; result.modelUsage is non-empty."""
    _require_tmux()

    events: list[dict] = []
    hook_names: list[str] = []

    async with BrokerHarness(hooks=True, idle_timeout_s=0.3, start_transport=True) as h:
        h.transport.on_event(_recorder(events))
        # Spy on hook payloads by wrapping the broker's hook handler.
        original = h.broker.handle_claude_hook

        async def spy(payload: dict) -> None:
            hook_names.append(str(payload.get("hook_event_name", "")))
            await original(payload)

        h.broker.handle_claude_hook = spy  # type: ignore[method-assign]

        await h.transport.send_message("say:hello")

        await _wait_until(lambda: "Stop" in hook_names, timeout=8.0)
        await _wait_until(lambda: any(e.get("type") == "result" for e in events), timeout=8.0)

        results = [e for e in events if e.get("type") == "result"]

    assert "Stop" in hook_names, "Stop hook never reached the broker"
    assert results, "no result event emitted"
    last = results[-1]
    assert last.get("result") == "hello"
    assert last.get("stop_reason") == "stop"
    assert last.get("modelUsage"), "result.modelUsage was empty for a non-empty turn"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a1_capabilities_handshake() -> None:
    """A1: the handshake advertises terminal controls + emits pane/init frames."""
    _require_tmux()

    async with BrokerHarness(hooks=True, idle_timeout_s=0.3) as h:
        client = await h.connect()

        caps = client.frames_of_type("capabilities")[-1]
        assert caps["terminal_output"] is True
        assert caps["terminal_input"] is True
        assert caps["terminal_keys"] is True
        assert caps["terminal_resize"] is True
        assert caps["terminal_panes"] is True
        assert caps["steer"] is True
        assert caps["interrupt"] is True
        assert caps["slash_commands"] is True

        # system(connected) handshake.
        system_frames = client.frames_of_type("system")
        assert any("Connected to session" in str(f.get("content", "")) for f in system_frames)

        # The transport emits system/init and terminal_pane_opened on start; both
        # flow through the broker to the client.
        await client.wait_for(
            lambda frames: any(
                f.get("type") == "system" and f.get("subtype") == "init" for f in frames
            ),
            timeout=5.0,
        )
        await client.wait_for(
            lambda frames: any(f.get("type") == "terminal_pane_opened" for f in frames),
            timeout=5.0,
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_e2e_message_broadcasts_and_rebuilds_from_log() -> None:
    """E2E: a browser message round-trips, broadcasts, logs, and rebuilds."""
    _require_tmux()

    from volundr.domain.services.transcript_rebuild import rebuild_turns

    async with BrokerHarness(hooks=True, idle_timeout_s=0.3) as h:
        client = await h.connect()

        client.send({"type": "message", "content": "say:pong"})

        # The assistant response is broadcast as an assistant frame carrying the text.
        await client.wait_for(
            lambda frames: any(_assistant_says(f, "pong") for f in frames),
            timeout=8.0,
        )
        # And the turn closes with a result.
        await client.wait_for(
            lambda frames: any(f.get("type") == "result" for f in frames),
            timeout=8.0,
        )

        # The durable log captured the turn frames.
        log = h.event_log
        kinds = [entry.get("kind") for entry in log]
        assert "result" in kinds, f"durable log missing result frame; kinds={kinds}"

        # And rebuild_turns reproduces a turn from the durable log.
        entries = h.event_log_entries()
        rebuilt = rebuild_turns(entries)

    assert any("pong" in _turn_text(t) for t in rebuilt.turns), (
        f"rebuilt turns did not reproduce the assistant text; turns={rebuilt.turns}"
    )


# --------------------------------------------------------------------------- helpers


def _recorder(sink: list[dict]):
    async def _on_event(event: dict) -> None:
        sink.append(event)

    return _on_event


def _assistant_says(frame: dict, needle: str) -> bool:
    if frame.get("type") != "assistant":
        return False
    message = frame.get("message", {})
    content = message.get("content", []) if isinstance(message, dict) else []
    if isinstance(content, str):
        return needle in content
    if not isinstance(content, list):
        return False
    for block in content:
        if isinstance(block, dict) and needle in str(block.get("text", "")):
            return True
    return False


def _turn_text(turn: dict) -> str:
    content = turn.get("content", "")
    if isinstance(content, str) and content:
        return content
    parts = turn.get("parts", [])
    if not isinstance(parts, list):
        return str(content)
    texts: list[str] = []
    for part in parts:
        if isinstance(part, dict):
            texts.append(str(part.get("text", "")))
    return "\n".join(texts) or str(content)
