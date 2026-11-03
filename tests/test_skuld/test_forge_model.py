"""B1d — the model in effect, end to end through REAL tmux and the fake Claude CLI.

The fake writes a native transcript naming its model on every reply (as Claude
does) and acknowledges ``/model`` on screen. Two paths change the model:

  * typed in the terminal: nothing changes until the next reply names the new
    model, which the transport reads from the transcript;
  * picked in the app (``set_model`` / ``set_runtime_options``): the transport
    types ``/model <id>`` and waits for the CLI's acknowledgement.

Either way the usage frames carry the model Volundr projects onto the session.

    SKULD__TMUX_REMOTE_CONTROL=0 uv run pytest -m integration \\
        tests/test_skuld/test_forge_model.py -p no:cacheprovider -q -rs
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime, timedelta

import pytest

from tests.support.forge import BrokerHarness
from volundr.domain.session_usage import fold_usage, observation, projection_key

AT = datetime(2026, 10, 26, tzinfo=UTC)


def _require_tmux() -> None:
    if shutil.which("tmux") is None:
        pytest.skip("tmux is not installed")


async def _wait_until(predicate, timeout: float = 10.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not met before timeout")


def _project(frames: list[dict]) -> str | None:
    state, identities = None, {}
    for seq, frame in enumerate(frames, start=1):
        event = observation(frame, seq=seq, at=AT + timedelta(seconds=seq))
        if event is None:
            continue
        key = projection_key(event, state)
        state, identities[key] = fold_usage(state, event, identities.get(key), model_windows={})
    return (state or {}).get("model")


def _usage_models(frames: list[dict]) -> list[str]:
    return [
        frame["message"]["model"]
        for frame in frames
        if frame.get("subtype") == "claude_usage" and frame.get("caught_up")
    ]


async def _reply(h: BrokerHarness, frames: list[dict], text: str, model: str) -> None:
    await h.transport.send_control("terminal_input", text=f"say:{text}", enter=True)
    await _wait_until(lambda: model in _usage_models(frames) and not h.transport.is_turn_active)


@pytest.mark.integration
@pytest.mark.tmux
@pytest.mark.asyncio
async def test_model_typed_in_the_terminal_follows_the_next_reply() -> None:
    _require_tmux()
    frames: list[dict] = []
    async with BrokerHarness(model="claude-fable-5-1", transcript=True, start_transport=True) as h:

        async def record(frame: dict) -> None:
            frames.append(frame)

        h.transport.on_event(record)
        await _reply(h, frames, "first", "claude-fable-5-1")
        assert (await h.transport.get_runtime_options())["current"]["model"] == "claude-fable-5-1"

        await h.transport.send_control("terminal_input", text="/model claude-opus-5-5", enter=True)
        for _ in range(200):
            if "Set model to claude-opus-5-5" in await h.transport._capture_pane_text():
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("the CLI never acknowledged /model")
        # The screen acknowledgement alone is not the ground truth.
        assert h.transport._model == "claude-fable-5-1"

        await _reply(h, frames, "second", "claude-opus-5-5")
        options = await h.transport.get_runtime_options()
        assert options["current"]["model"] == "claude-opus-5-5"
        assert _project(frames) == "claude-opus-5-5"


@pytest.mark.integration
@pytest.mark.tmux
@pytest.mark.asyncio
async def test_model_picked_in_the_app_is_typed_and_acknowledged() -> None:
    _require_tmux()
    frames: list[dict] = []
    async with BrokerHarness(model="claude-fable-5-1", transcript=True, start_transport=True) as h:

        async def record(frame: dict) -> None:
            frames.append(frame)

        h.transport.on_event(record)
        await _reply(h, frames, "first", "claude-fable-5-1")

        await h.transport.send_control("set_model", model="claude-opus-5-5")
        sent = [f for f in frames if f.get("type") == "slash_command_sent"]
        assert sent[-1]["input"] == "/model claude-opus-5-5"
        assert (await h.transport.get_runtime_options())["current"]["model"] == "claude-opus-5-5"

        await _reply(h, frames, "second", "claude-opus-5-5")
        assert _project(frames) == "claude-opus-5-5"
