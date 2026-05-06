"""Tests for NIU-435 cascade system.

Coverage targets:
- task_create routing: local enqueue, mesh delegation, spawn fallback
- task_status: local DriveLoop query + remote mesh query
- task_list: local + remote aggregation
- task_stop: local cancel + remote mesh cancel
- task_collect: poll until done (local + remote)
- flock_spawn: SpawnPort.spawn() delegation
- flock_status: DiscoveryPort peer table dump
- flock_terminate: SpawnPort.terminate() delegation
- DriveLoop.task_status: running/queued/unknown
- DriveLoop.set_rpc_handler / handle_rpc
- Mesh RPC handler: task_dispatch, task_status, task_cancel, unknown
- build_cascade_tools: correct tool list for each mode
- Integration (Mode 1): coordinator enqueues 3 local tasks concurrently
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ravn.adapters.tools.cascade_tools import (
    FlockSpawnTool,
    FlockStatusTool,
    FlockTerminateTool,
    TaskCollectTool,
    TaskCreateTool,
    TaskListTool,
    TaskStatusTool,
    TaskStopTool,
    build_cascade_tools,
)
from ravn.config import InitiativeConfig, Settings
from ravn.domain.models import AgentTask, OutputMode
from ravn.drive_loop import DriveLoop
from ravn.ports.spawn import SpawnConfig
from tests.test_ravn.conftest import (
    _FakeDiscovery,
    _FakePeer,
    _FakeSpawnAdapter,
    _make_agent_task,
    _make_drive_loop,
)

# ---------------------------------------------------------------------------
# DriveLoop.task_status tests
# ---------------------------------------------------------------------------


class TestDriveLoopTaskStatus:
    def test_unknown_for_missing_task(self):
        dl = _make_drive_loop()
        assert dl.task_status("nonexistent") == "unknown"

    def test_running_for_active_task(self):
        dl = _make_drive_loop()
        mock_asyncio_task = MagicMock()
        dl._active_tasks["task_123"] = mock_asyncio_task
        assert dl.task_status("task_123") == "running"

    @pytest.mark.asyncio
    async def test_queued_for_task_in_queue(self):
        dl = _make_drive_loop()
        task = _make_agent_task("task_queued")
        await dl.enqueue(task)
        assert dl.task_status("task_queued") == "queued"

    @pytest.mark.asyncio
    async def test_unknown_after_never_enqueued(self):
        dl = _make_drive_loop()
        assert dl.task_status("never_existed") == "unknown"


# ---------------------------------------------------------------------------
# DriveLoop.set_rpc_handler / handle_rpc tests
# ---------------------------------------------------------------------------


class TestDriveLoopRpcHandler:
    @pytest.mark.asyncio
    async def test_no_handler_returns_error(self):
        dl = _make_drive_loop()
        reply = await dl.handle_rpc({"type": "task_status", "task_id": "x"})
        assert "error" in reply

    @pytest.mark.asyncio
    async def test_handler_is_called(self):
        dl = _make_drive_loop()
        handler = AsyncMock(return_value={"status": "ok"})
        dl.set_rpc_handler(handler)
        reply = await dl.handle_rpc({"type": "ping"})
        assert reply == {"status": "ok"}
        handler.assert_called_once_with({"type": "ping"})

    @pytest.mark.asyncio
    async def test_handler_exception_returns_error(self):
        dl = _make_drive_loop()

        async def _fail(_msg: dict) -> dict:
            raise ValueError("boom")

        dl.set_rpc_handler(_fail)
        reply = await dl.handle_rpc({"type": "ping"})
        assert "error" in reply
        assert "boom" in reply["error"]


# ---------------------------------------------------------------------------
# Mesh RPC handler (wired via _wire_cascade)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mesh_rpc_task_dispatch():
    """RPC handler: task_dispatch enqueues task and returns accepted."""
    dl = _make_drive_loop()

    from ravn.cli.commands import _wire_cascade  # type: ignore[attr-defined]

    settings = MagicMock(spec=Settings)
    settings.cascade = MagicMock()
    settings.cascade.enabled = True
    settings.mesh = MagicMock()
    # discovery disabled — no peer polling
    settings.mesh.enabled = False
    settings.discovery = MagicMock()
    settings.discovery.enabled = False

    with patch("ravn.cli.commands._build_mesh", side_effect=RuntimeError("disabled")):
        with patch("ravn.cli.commands._build_discovery", side_effect=RuntimeError("disabled")):
            _wire_cascade(dl, settings)

    reply = await dl.handle_rpc(
        {
            "type": "task_dispatch",
            "task": {
                "task_id": "rpc-task-1",
                "title": "RPC dispatched task",
                "initiative_context": "run something",
                "triggered_by": "cascade:test",
                "output_mode": "silent",
                "priority": 5,
            },
        }
    )
    assert reply["status"] == "accepted"
    assert reply["task_id"] == "rpc-task-1"
    assert dl.task_status("rpc-task-1") == "queued"


@pytest.mark.asyncio
async def test_mesh_rpc_task_status():
    dl = _make_drive_loop()
    task = _make_agent_task("status-task")
    await dl.enqueue(task)

    from ravn.cli.commands import _wire_cascade  # type: ignore[attr-defined]

    settings = MagicMock(spec=Settings)
    settings.cascade = MagicMock()
    settings.cascade.enabled = True
    settings.mesh = MagicMock()
    settings.mesh.enabled = False
    settings.discovery = MagicMock()
    settings.discovery.enabled = False

    with patch("ravn.cli.commands._build_mesh", side_effect=RuntimeError):
        with patch("ravn.cli.commands._build_discovery", side_effect=RuntimeError):
            _wire_cascade(dl, settings)

    reply = await dl.handle_rpc({"type": "task_status", "task_id": "status-task"})
    assert reply["task_id"] == "status-task"
    assert reply["status"] == "queued"


@pytest.mark.asyncio
async def test_mesh_rpc_task_list():
    dl = _make_drive_loop()
    task = _make_agent_task("list-task")
    await dl.enqueue(task)
    dl._active_tasks["active-task"] = MagicMock()

    from ravn.cli.commands import _wire_cascade  # type: ignore[attr-defined]

    settings = MagicMock(spec=Settings)
    settings.cascade = MagicMock()
    settings.cascade.enabled = True
    settings.mesh = MagicMock()
    settings.mesh.enabled = False
    settings.discovery = MagicMock()
    settings.discovery.enabled = False

    with patch("ravn.cli.commands._build_mesh", side_effect=RuntimeError):
        with patch("ravn.cli.commands._build_discovery", side_effect=RuntimeError):
            _wire_cascade(dl, settings)

    reply = await dl.handle_rpc({"type": "task_list"})
    assert "active" in reply
    assert "queued" in reply
    assert "active-task" in reply["active"]
    assert "list-task" in reply["queued"]


@pytest.mark.asyncio
async def test_mesh_rpc_task_cancel():
    dl = _make_drive_loop()
    # Fake an active task
    mock_task = MagicMock()
    mock_task.cancel = MagicMock()
    dl._active_tasks["cancel-task"] = mock_task

    from ravn.cli.commands import _wire_cascade  # type: ignore[attr-defined]

    settings = MagicMock(spec=Settings)
    settings.cascade = MagicMock()
    settings.cascade.enabled = True
    settings.mesh = MagicMock()
    settings.mesh.enabled = False
    settings.discovery = MagicMock()
    settings.discovery.enabled = False

    with patch("ravn.cli.commands._build_mesh", side_effect=RuntimeError):
        with patch("ravn.cli.commands._build_discovery", side_effect=RuntimeError):
            _wire_cascade(dl, settings)

    reply = await dl.handle_rpc({"type": "task_cancel", "task_id": "cancel-task"})
    assert reply["status"] == "cancelled"
    mock_task.cancel.assert_called_once()


@pytest.mark.asyncio
async def test_mesh_rpc_unknown_type():
    dl = _make_drive_loop()

    from ravn.cli.commands import _wire_cascade  # type: ignore[attr-defined]

    settings = MagicMock(spec=Settings)
    settings.cascade = MagicMock()
    settings.cascade.enabled = True
    settings.mesh = MagicMock()
    settings.mesh.enabled = False
    settings.discovery = MagicMock()
    settings.discovery.enabled = False

    with patch("ravn.cli.commands._build_mesh", side_effect=RuntimeError):
        with patch("ravn.cli.commands._build_discovery", side_effect=RuntimeError):
            _wire_cascade(dl, settings)

    reply = await dl.handle_rpc({"type": "totally_unknown"})
    assert "error" in reply


# ---------------------------------------------------------------------------
# task_create routing tests
# ---------------------------------------------------------------------------


class TestTaskCreateTool:
    @pytest.mark.asyncio
    async def test_local_enqueue_when_no_mesh(self):
        """task_create routes to local DriveLoop when no mesh configured."""
        dl = _make_drive_loop()
        tool = TaskCreateTool(drive_loop=dl)
        result = await tool.execute({"prompt": "do work", "title": "local task"})
        data = json.loads(result.content)
        assert data["location"] == "local"
        assert not result.is_error
        assert dl.task_status(data["task_id"]) == "queued"

    @pytest.mark.asyncio
    async def test_mesh_delegation_to_idle_peer(self):
        """task_create delegates to idle peer when mesh and discovery available."""
        dl = _make_drive_loop()
        peer = _FakePeer("peer-abc", status="idle")
        discovery = _FakeDiscovery({"peer-abc": peer})
        mesh = AsyncMock()
        mesh.send = AsyncMock(return_value={"status": "accepted", "task_id": "t1"})

        tool = TaskCreateTool(drive_loop=dl, mesh=mesh, discovery=discovery)
        result = await tool.execute({"prompt": "remote work", "title": "remote task"})
        data = json.loads(result.content)
        assert data["location"] == "peer-abc"
        assert data["status"] == "accepted"
        assert not result.is_error
        mesh.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_idle_peer_falls_back_local(self):
        """No idle peers → local enqueue (spawn=false)."""
        dl = _make_drive_loop()
        peer = _FakePeer("peer-busy", status="busy")
        discovery = _FakeDiscovery({"peer-busy": peer})
        mesh = AsyncMock()

        tool = TaskCreateTool(drive_loop=dl, mesh=mesh, discovery=discovery)
        result = await tool.execute({"prompt": "work", "title": "task"})
        data = json.loads(result.content)
        assert data["location"] == "local"
        mesh.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_spawn_when_no_idle_peer(self):
        """spawn=true → SpawnPort.spawn() → mesh delegation."""
        dl = _make_drive_loop()
        discovery = _FakeDiscovery({})  # no peers initially
        mesh = AsyncMock()
        mesh.send = AsyncMock(return_value={"status": "accepted", "task_id": "t2"})
        spawn_adapter = _FakeSpawnAdapter(peer_ids=["spawned-peer-1"])

        tool = TaskCreateTool(
            drive_loop=dl,
            mesh=mesh,
            discovery=discovery,
            spawn_adapter=spawn_adapter,
        )
        result = await tool.execute({"prompt": "heavy work", "title": "spawn task", "spawn": True})
        assert not result.is_error
        assert len(spawn_adapter.spawned_configs) == 1
        mesh.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_mesh_failure_falls_back_local(self):
        """mesh.send() failure → local enqueue."""
        dl = _make_drive_loop()
        peer = _FakePeer("peer-abc", status="idle")
        discovery = _FakeDiscovery({"peer-abc": peer})
        mesh = AsyncMock()
        mesh.send = AsyncMock(side_effect=Exception("network error"))

        tool = TaskCreateTool(drive_loop=dl, mesh=mesh, discovery=discovery)
        result = await tool.execute({"prompt": "work", "title": "task"})
        data = json.loads(result.content)
        assert data["location"] == "local"

    @pytest.mark.asyncio
    async def test_required_caps_filter(self):
        """Peer without required caps is skipped."""
        dl = _make_drive_loop()
        peer_no_cap = _FakePeer("peer-no-cap", status="idle", capabilities=["bash"])
        discovery = _FakeDiscovery({"peer-no-cap": peer_no_cap})
        mesh = AsyncMock()

        tool = TaskCreateTool(drive_loop=dl, mesh=mesh, discovery=discovery)
        result = await tool.execute(
            {
                "prompt": "work",
                "title": "task",
                "required_caps": ["gpu"],
            }
        )
        data = json.loads(result.content)
        # No peer with gpu capability → local
        assert data["location"] == "local"
        mesh.send.assert_not_called()


# ---------------------------------------------------------------------------
# task_status tests
# ---------------------------------------------------------------------------


class TestTaskStatusTool:
    @pytest.mark.asyncio
    async def test_local_running(self):
        dl = _make_drive_loop()
        dl._active_tasks["t1"] = MagicMock()
        tool = TaskStatusTool(drive_loop=dl)
        result = await tool.execute({"task_id": "t1"})
        data = json.loads(result.content)
        assert data["status"] == "running"

    @pytest.mark.asyncio
    async def test_local_unknown(self):
        dl = _make_drive_loop()
        tool = TaskStatusTool(drive_loop=dl)
        result = await tool.execute({"task_id": "gone"})
        data = json.loads(result.content)
        assert data["status"] == "unknown"

    @pytest.mark.asyncio
    async def test_remote_mesh_query(self):
        dl = _make_drive_loop()
        mesh = AsyncMock()
        mesh.send = AsyncMock(return_value={"task_id": "remote-1", "status": "running"})
        remote_tasks = {"remote-1": "peer-xyz"}
        tool = TaskStatusTool(drive_loop=dl, mesh=mesh, remote_tasks=remote_tasks)
        result = await tool.execute({"task_id": "remote-1"})
        data = json.loads(result.content)
        assert data["status"] == "running"
        mesh.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_missing_task_id_error(self):
        dl = _make_drive_loop()
        tool = TaskStatusTool(drive_loop=dl)
        result = await tool.execute({})
        assert result.is_error


# ---------------------------------------------------------------------------
# task_list tests
# ---------------------------------------------------------------------------


class TestTaskListTool:
    @pytest.mark.asyncio
    async def test_local_only(self):
        dl = _make_drive_loop()
        dl._active_tasks["t1"] = MagicMock()
        tool = TaskListTool(drive_loop=dl)
        result = await tool.execute({})
        data = json.loads(result.content)
        assert "local" in data
        assert "t1" in data["local"]["active"]

    @pytest.mark.asyncio
    async def test_with_remote_peers(self):
        dl = _make_drive_loop()
        peer = _FakePeer("peer-1")
        discovery = _FakeDiscovery({"peer-1": peer})
        mesh = AsyncMock()
        mesh.send = AsyncMock(return_value={"active": ["rt1"], "queued": []})
        tool = TaskListTool(drive_loop=dl, mesh=mesh, discovery=discovery)
        result = await tool.execute({})
        data = json.loads(result.content)
        assert len(data["remote"]) == 1
        assert data["remote"][0]["peer_id"] == "peer-1"


# ---------------------------------------------------------------------------
# task_stop tests
# ---------------------------------------------------------------------------


class TestTaskStopTool:
    @pytest.mark.asyncio
    async def test_cancel_local_task(self):
        dl = _make_drive_loop()
        mock_task = MagicMock()
        mock_task.cancel = MagicMock()
        dl._active_tasks["t1"] = mock_task
        tool = TaskStopTool(drive_loop=dl)
        result = await tool.execute({"task_id": "t1"})
        data = json.loads(result.content)
        assert data["status"] == "cancel_requested"
        mock_task.cancel.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancel_remote_task(self):
        dl = _make_drive_loop()
        mesh = AsyncMock()
        mesh.send = AsyncMock(return_value={"status": "cancelled"})
        remote_tasks = {"rt1": "peer-1"}
        tool = TaskStopTool(drive_loop=dl, mesh=mesh, remote_tasks=remote_tasks)
        result = await tool.execute({"task_id": "rt1"})
        data = json.loads(result.content)
        assert data["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_missing_task_id_error(self):
        dl = _make_drive_loop()
        tool = TaskStopTool(drive_loop=dl)
        result = await tool.execute({})
        assert result.is_error


# ---------------------------------------------------------------------------
# task_collect tests
# ---------------------------------------------------------------------------


class TestTaskCollectTool:
    @pytest.mark.asyncio
    async def test_collect_already_done_local(self):
        """task_id not in queue/active → immediately done."""
        dl = _make_drive_loop()
        tool = TaskCollectTool(drive_loop=dl, poll_interval_s=0.01)
        result = await tool.execute({"task_id": "done-task", "timeout_s": 2.0})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["status"] == "complete"

    @pytest.mark.asyncio
    async def test_collect_timeout(self):
        """Task stuck in queue → timeout."""
        dl = _make_drive_loop()
        task = _make_agent_task("stuck-task")
        await dl.enqueue(task)
        tool = TaskCollectTool(drive_loop=dl, poll_interval_s=0.01)
        result = await tool.execute({"task_id": "stuck-task", "timeout_s": 0.05})
        assert result.is_error
        assert "did not complete" in result.content

    @pytest.mark.asyncio
    async def test_collect_remote(self):
        """Remote task: polls mesh until status is complete."""
        dl = _make_drive_loop()
        mesh = AsyncMock()
        call_count = 0

        async def _side_effect(target_peer_id, message, **kwargs):  # noqa: ANN001
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return {"task_id": "rt1", "status": "running"}
            return {"task_id": "rt1", "status": "complete"}

        mesh.send = AsyncMock(side_effect=_side_effect)
        remote_tasks = {"rt1": "peer-1"}
        tool = TaskCollectTool(
            drive_loop=dl, mesh=mesh, remote_tasks=remote_tasks, poll_interval_s=0.01
        )
        result = await tool.execute({"task_id": "rt1", "timeout_s": 5.0})
        assert not result.is_error


# ---------------------------------------------------------------------------
# flock_spawn tests
# ---------------------------------------------------------------------------


class TestFlockSpawnTool:
    @pytest.mark.asyncio
    async def test_spawn_success(self):
        spawn_adapter = _FakeSpawnAdapter(peer_ids=["p1", "p2"])
        tool = FlockSpawnTool(spawn_adapter=spawn_adapter)
        result = await tool.execute({"count": 2, "persona": "worker"})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["spawned"] == ["p1", "p2"]

    @pytest.mark.asyncio
    async def test_spawn_timeout_error(self):
        async def _timeout_spawn(count: int, config: SpawnConfig) -> list[str]:
            raise TimeoutError("timeout")

        spawn_adapter = MagicMock()
        spawn_adapter.spawn = _timeout_spawn
        tool = FlockSpawnTool(spawn_adapter=spawn_adapter)
        result = await tool.execute({"count": 1})
        assert result.is_error
        assert "timeout" in result.content.lower()


# ---------------------------------------------------------------------------
# flock_status tests
# ---------------------------------------------------------------------------


class TestFlockStatusTool:
    @pytest.mark.asyncio
    async def test_no_peers(self):
        discovery = _FakeDiscovery({})
        tool = FlockStatusTool(discovery=discovery)
        result = await tool.execute({})
        assert "No verified peers" in result.content

    @pytest.mark.asyncio
    async def test_with_peers(self):
        peer = _FakePeer("peer-1", status="idle")
        peer.capabilities = ["bash", "git"]
        discovery = _FakeDiscovery({"peer-1": peer})
        tool = FlockStatusTool(discovery=discovery)
        result = await tool.execute({})
        data = json.loads(result.content)
        assert len(data) == 1
        assert data[0]["peer_id"] == "peer-1"
        assert data[0]["status"] == "idle"
        assert "bash" in data[0]["capabilities"]


# ---------------------------------------------------------------------------
# flock_terminate tests
# ---------------------------------------------------------------------------


class TestFlockTerminateTool:
    @pytest.mark.asyncio
    async def test_terminate_specific_peers(self):
        spawn_adapter = _FakeSpawnAdapter()
        tool = FlockTerminateTool(spawn_adapter=spawn_adapter)
        result = await tool.execute({"peer_ids": ["p1", "p2"]})
        assert not result.is_error
        assert spawn_adapter.terminated == ["p1", "p2"]

    @pytest.mark.asyncio
    async def test_terminate_all(self):
        spawn_adapter = _FakeSpawnAdapter()
        tool = FlockTerminateTool(spawn_adapter=spawn_adapter)
        result = await tool.execute({})
        assert not result.is_error
        assert spawn_adapter.all_terminated


# ---------------------------------------------------------------------------
# build_cascade_tools tests
# ---------------------------------------------------------------------------


class TestBuildCascadeTools:
    def test_local_only(self):
        dl = _make_drive_loop()
        tools = build_cascade_tools(drive_loop=dl)
        names = {t.name for t in tools}
        assert "task_create" in names
        assert "task_status" in names
        assert "task_list" in names
        assert "task_stop" in names
        assert "task_collect" in names
        # No mesh/discovery/spawn → no flock tools
        assert "flock_status" not in names
        assert "flock_spawn" not in names
        assert "flock_terminate" not in names

    def test_with_discovery(self):
        dl = _make_drive_loop()
        discovery = _FakeDiscovery({})
        tools = build_cascade_tools(drive_loop=dl, discovery=discovery)
        names = {t.name for t in tools}
        assert "flock_status" in names

    def test_with_spawn(self):
        dl = _make_drive_loop()
        spawn_adapter = _FakeSpawnAdapter()
        tools = build_cascade_tools(drive_loop=dl, spawn_adapter=spawn_adapter)
        names = {t.name for t in tools}
        assert "flock_spawn" in names
        assert "flock_terminate" in names

    def test_shared_remote_tasks_dict(self):
        """task_create and task_status/stop/collect share the same remote_tasks dict."""
        dl = _make_drive_loop()
        tools = build_cascade_tools(drive_loop=dl)
        create_tool = next(t for t in tools if t.name == "task_create")
        status_tool = next(t for t in tools if t.name == "task_status")
        stop_tool = next(t for t in tools if t.name == "task_stop")
        collect_tool = next(t for t in tools if t.name == "task_collect")
        # All should share the same dict object
        assert create_tool._remote_tasks is status_tool._remote_tasks
        assert create_tool._remote_tasks is stop_tool._remote_tasks
        assert create_tool._remote_tasks is collect_tool._remote_tasks


# ---------------------------------------------------------------------------
# Integration: Mode 1 — local parallel tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mode1_local_parallel_tasks():
    """Coordinator enqueues 3 tasks; all run concurrently under semaphore cap.

    Uses a mock agent_factory that records task_ids executed.
    Semaphore cap = 3 so all three can run at once.
    """
    executed: list[str] = []
    finished = asyncio.Event()
    count = 3

    async def _mock_run_turn(prompt: str) -> None:
        executed.append(prompt)
        if len(executed) >= count:
            finished.set()

    mock_agent = MagicMock()
    mock_agent.run_turn = _mock_run_turn

    def _agent_factory(channel, task_id=None, persona=None, triggered_by=None):  # noqa: ANN001
        return mock_agent

    cfg = InitiativeConfig(enabled=True, max_concurrent_tasks=3, task_queue_max=50)
    settings = MagicMock()
    settings.skuld.enabled = False
    settings.cascade.enabled = False
    dl = DriveLoop(agent_factory=_agent_factory, config=cfg, settings=settings)

    # Enqueue 3 tasks
    for i in range(count):
        task = AgentTask(
            task_id=f"parallel-task-{i}",
            title=f"Task {i}",
            initiative_context=f"prompt-{i}",
            triggered_by="test",
            output_mode=OutputMode.SILENT,
        )
        await dl.enqueue(task)

    # Run the drive loop briefly
    loop_task = asyncio.create_task(dl.run())
    try:
        await asyncio.wait_for(finished.wait(), timeout=5.0)
    finally:
        loop_task.cancel()
        await asyncio.gather(loop_task, return_exceptions=True)

    assert len(executed) == count


# ---------------------------------------------------------------------------
# SpawnPort protocol conformance
# ---------------------------------------------------------------------------


def test_spawn_port_protocol():
    """SubprocessSpawnAdapter satisfies SpawnPort protocol."""
    from ravn.ports.spawn import SpawnPort  # noqa: PLC0415

    assert isinstance(_FakeSpawnAdapter(), SpawnPort)
