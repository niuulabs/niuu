"""Plan + running-agents surfacing for Claude tmux sessions.

Integration tests drive a REAL ``TmuxInteractiveTransport`` + ``fakeagent`` (which
POSTs the real Claude hook shapes — ``TodoWrite`` PreToolUse for the plan, ``Task``
Pre/PostToolUse for subagents) through a real ``Broker`` over a fake browser
client, and assert the structured ``plan`` / ``agent_update`` frames surface, are
tracked, and replay on reconnect. Default-tier tests cover the broker helpers and
the ``/api/plan`` / ``/api/agents`` read endpoints without tmux.

See docs/forge-plan-and-agents-surfacing.md for the contract + decision log.
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


# ──────────────────────────── plan (TodoWrite) ────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_plan_surfaces_to_client_and_is_tracked() -> None:
    """A TodoWrite call surfaces a structured `plan` frame + is tracked live."""
    _require_tmux()
    async with BrokerHarness(hooks=True, idle_timeout_s=0.3) as h:
        client = await h.connect()
        client.send(
            {
                "type": "message",
                "content": "todo:design API=completed;build it=in_progress;write tests=pending",
            }
        )
        plan = await client.wait_for_type("plan", timeout=8.0)

        assert plan.get("event_type") == "claude.plan"
        by_content = {t["content"]: t["status"] for t in plan["tasks"]}
        assert by_content == {
            "design API": "completed",
            "build it": "in_progress",
            "write tests": "pending",
        }
        assert plan["counts"] == {
            "total": 3,
            "pending": 1,
            "in_progress": 1,
            "completed": 1,
        }
        # Tracked live so a reconnect/REST can answer.
        await _wait_until(lambda: h.broker._current_plan is not None, timeout=5.0)  # noqa: SLF001
        assert h.broker._current_plan["tasks"][1]["content"] == "build it"  # noqa: SLF001


@pytest.mark.integration
@pytest.mark.asyncio
async def test_plan_replays_on_reconnect() -> None:
    """A client joining after the plan was set immediately receives it."""
    _require_tmux()
    async with BrokerHarness(
        hooks=True,
        idle_timeout_s=0.3,
        start_transport=True,
        boot="todo:scope=completed;ship=in_progress",
    ) as h:
        await _wait_until(lambda: h.broker._current_plan is not None, timeout=8.0)  # noqa: SLF001
        # A fresh client gets the current plan on connect (no new TodoWrite needed).
        client = await h.connect()
        plan = await client.wait_for_type("plan", timeout=5.0)
        assert {t["content"] for t in plan["tasks"]} == {"scope", "ship"}


# ──────────────────────────── running agents (Task) ────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_agent_lifecycle_surfaces_and_tracks() -> None:
    """A Task subagent surfaces started -> stopped agent_update frames + is tracked."""
    _require_tmux()
    async with BrokerHarness(hooks=True, idle_timeout_s=0.3) as h:
        client = await h.connect()

        client.send({"type": "message", "content": "agent:code-reviewer|review the diff"})
        started = await client.wait_for_type("agent_update", timeout=8.0)
        assert started["action"] == "started"
        agent = started["agent"]
        assert agent["kind"] == "subagent"
        assert agent["name"] == "code-reviewer"
        assert agent["status"] == "running"
        agent_id = agent["id"]
        await _wait_until(lambda: agent_id in h.broker._running_agents, timeout=5.0)  # noqa: SLF001

        client.send({"type": "message", "content": "agent_done:code-reviewer"})
        await _wait_until(
            lambda: any(f["action"] == "stopped" for f in client.frames_of_type("agent_update")),
            timeout=8.0,
        )
        stopped = [f for f in client.frames_of_type("agent_update") if f["action"] == "stopped"][-1]
        assert stopped["agent"]["status"] == "done"
        # Removed from the live set once stopped.
        await _wait_until(lambda: agent_id not in h.broker._running_agents, timeout=5.0)  # noqa: SLF001


@pytest.mark.integration
@pytest.mark.asyncio
async def test_running_agents_replay_on_reconnect() -> None:
    """A still-running agent is replayed to a freshly-connecting client."""
    _require_tmux()
    async with BrokerHarness(
        hooks=True,
        idle_timeout_s=0.3,
        start_transport=True,
        boot="agent:researcher|dig into the logs",
    ) as h:
        await _wait_until(lambda: len(h.broker._running_agents) == 1, timeout=8.0)  # noqa: SLF001
        client = await h.connect()
        replayed = await client.wait_for_type("agent_update", timeout=5.0)
        assert replayed["action"] == "started"
        assert replayed["agent"]["name"] == "researcher"
        assert replayed["metadata"]["source"] == "reconnect_replay"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_subagent_start_stop_surfaces_and_tracks() -> None:
    """The SubagentStart/SubagentStop hooks surface + track like the Task path."""
    _require_tmux()
    async with BrokerHarness(hooks=True, idle_timeout_s=0.3) as h:
        client = await h.connect()

        client.send({"type": "message", "content": "subagent:test-runner|run the suite"})
        started = await client.wait_for_type("agent_update", timeout=8.0)
        assert started["action"] == "started"
        assert started["agent"]["kind"] == "subagent"
        assert started["agent"]["name"] == "test-runner"
        agent_id = started["agent"]["id"]
        await _wait_until(lambda: agent_id in h.broker._running_agents, timeout=5.0)  # noqa: SLF001

        client.send({"type": "message", "content": "subagent_done:test-runner"})
        await _wait_until(lambda: agent_id not in h.broker._running_agents, timeout=8.0)  # noqa: SLF001
        stopped = [f for f in client.frames_of_type("agent_update") if f["action"] == "stopped"][-1]
        assert stopped["agent"]["status"] == "done"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_task_and_subagent_signals_dedup_by_id() -> None:
    """A SubagentStart carrying the Task's tool_use_id merges, not duplicates.

    The Task tool fires PreToolUse with tool_use_id `fakeagent-task-builder`; a
    SubagentStart pinned to that same id (`id=fakeagent-task-builder`) must enrich
    the existing agent rather than create a second one.
    """
    _require_tmux()
    async with BrokerHarness(hooks=True, idle_timeout_s=0.3) as h:
        client = await h.connect()

        client.send({"type": "message", "content": "agent:builder|build the thing"})
        await _wait_until(lambda: len(h.broker._running_agents) == 1, timeout=8.0)  # noqa: SLF001

        client.send(
            {
                "type": "message",
                "content": "subagent:builder|build the thing|id=fakeagent-task-builder",
            }
        )
        # Still exactly one agent (deduped by the shared id), now enriched.
        await asyncio.sleep(1.0)
        assert len(h.broker._running_agents) == 1, (
            f"Task + SubagentStart for the same id must NOT duplicate; "
            f"got {h.broker._running_agents}"  # noqa: SLF001
        )
        assert "fakeagent-task-builder" in h.broker._running_agents  # noqa: SLF001


# ──────────────────────────── broker helpers + endpoints (no tmux) ────────────────────────────


@pytest.mark.asyncio
async def test_track_pane_agent_excludes_primary_and_tracks_teammates() -> None:
    """Pane index 0 is the main REPL (not an agent); others are teammate agents."""
    from skuld.broker import Broker

    broker = Broker()
    broker._track_pane_agent(  # noqa: SLF001
        {"type": "terminal_pane_opened", "pane_id": "%0", "pane_index": "0"}, opened=True
    )
    assert broker._running_agents == {}  # noqa: SLF001 - primary pane excluded

    broker._track_pane_agent(  # noqa: SLF001
        {
            "type": "terminal_pane_opened",
            "pane_id": "%1",
            "pane_index": "1",
            "window_name": "reviewer",
            "current_command": "claude",
        },
        opened=True,
    )
    assert broker._running_agents["%1"]["kind"] == "teammate"  # noqa: SLF001
    assert broker._running_agents["%1"]["name"] == "reviewer"  # noqa: SLF001

    broker._track_pane_agent(  # noqa: SLF001
        {"type": "terminal_pane_closed", "pane_id": "%1", "pane_index": "1"}, opened=False
    )
    assert "%1" not in broker._running_agents  # noqa: SLF001


@pytest.mark.asyncio
async def test_get_plan_and_get_agents_endpoints(monkeypatch) -> None:
    """The /api/plan and /api/agents endpoints answer from live broker state."""
    import skuld.broker as broker_mod

    fresh = broker_mod.Broker()
    monkeypatch.setattr(broker_mod, "broker", fresh)

    # Empty by default.
    assert await broker_mod.get_plan() == {"tasks": [], "counts": {"total": 0}}
    assert await broker_mod.get_agents() == {"agents": []}

    fresh._current_plan = {  # noqa: SLF001
        "tasks": [{"content": "x", "status": "in_progress"}],
        "counts": {"total": 1, "in_progress": 1},
    }
    fresh._running_agents = {  # noqa: SLF001
        "t1": {"id": "t1", "kind": "subagent", "name": "rev", "status": "running"}
    }
    plan = await broker_mod.get_plan()
    assert plan["tasks"][0]["content"] == "x"
    assert plan["counts"]["in_progress"] == 1
    agents = await broker_mod.get_agents()
    assert agents["agents"][0]["name"] == "rev"
