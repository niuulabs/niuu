"""Tests for the checkpoint domain, port, disk adapter, and agent integration."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ravn.adapters.checkpoint.disk import DiskCheckpointAdapter, _from_dict, _to_dict
from ravn.adapters.permission.allow_deny import AllowAllPermission
from ravn.agent import RavnAgent
from ravn.domain.checkpoint import Checkpoint, InterruptReason
from ravn.domain.exceptions import MaxIterationsError
from ravn.domain.models import (
    LLMResponse,
    Session,
    StopReason,
    StreamEvent,
    StreamEventType,
    TokenUsage,
    ToolCall,
)
from ravn.ports.checkpoint import CheckpointPort
from tests.ravn.conftest import InMemoryChannel, MockLLM, make_text_response
from tests.ravn.fixtures.fakes import EchoTool

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_checkpoint(
    task_id: str = "task_001",
    interrupted_by: InterruptReason | None = None,
) -> Checkpoint:
    return Checkpoint(
        task_id=task_id,
        user_input="do something",
        messages=[{"role": "user", "content": "do something"}],
        todos=[],
        iteration_budget_consumed=3,
        iteration_budget_total=90,
        last_tool_call={"id": "tc1", "name": "echo", "input": {"message": "hi"}},
        last_tool_result={"tool_call_id": "tc1", "content": "hi", "is_error": False},
        partial_response="partial text",
        interrupted_by=interrupted_by,
        created_at=datetime(2026, 4, 7, 12, 0, 0, tzinfo=UTC),
    )


class InMemoryCheckpointAdapter(CheckpointPort):
    """In-memory checkpoint adapter for tests."""

    def __init__(self) -> None:
        self._store: dict[str, Checkpoint] = {}
        self._snapshots: dict[str, Checkpoint] = {}
        self._seq: dict[str, int] = {}

    async def save(self, checkpoint: Checkpoint) -> None:
        self._store[checkpoint.task_id] = checkpoint

    async def load(self, task_id: str) -> Checkpoint | None:
        return self._store.get(task_id)

    async def delete(self, task_id: str) -> None:
        self._store.pop(task_id, None)

    async def list_task_ids(self) -> list[str]:
        return list(self._store.keys())

    async def save_snapshot(self, checkpoint: Checkpoint) -> str:
        seq = self._seq.get(checkpoint.task_id, 0) + 1
        self._seq[checkpoint.task_id] = seq
        cid = Checkpoint.make_snapshot_id(checkpoint.task_id, seq)
        checkpoint.checkpoint_id = cid
        checkpoint.seq = seq
        self._snapshots[cid] = checkpoint
        return cid

    async def list_for_task(self, task_id: str) -> list[Checkpoint]:
        return sorted(
            (cp for cp in self._snapshots.values() if cp.task_id == task_id),
            key=lambda c: c.seq,
            reverse=True,
        )

    async def load_snapshot(self, checkpoint_id: str) -> Checkpoint | None:
        return self._snapshots.get(checkpoint_id)

    async def delete_snapshot(self, checkpoint_id: str) -> None:
        self._snapshots.pop(checkpoint_id, None)


# ---------------------------------------------------------------------------
# Domain model tests
# ---------------------------------------------------------------------------


class TestCheckpointModel:
    def test_fields_stored_correctly(self) -> None:
        cp = _make_checkpoint(interrupted_by=InterruptReason.SIGINT)
        assert cp.task_id == "task_001"
        assert cp.interrupted_by == InterruptReason.SIGINT
        assert cp.iteration_budget_consumed == 3
        assert cp.partial_response == "partial text"

    def test_interrupt_reason_enum_values(self) -> None:
        assert InterruptReason.SIGINT == "sigint"
        assert InterruptReason.SIGTERM == "sigterm"
        assert InterruptReason.BUDGET_EXHAUSTED == "budget_exhausted"
        assert InterruptReason.TING_CANCEL == "ting_cancel"

    def test_default_created_at_is_utc(self) -> None:
        cp = Checkpoint(
            task_id="x",
            user_input="hi",
            messages=[],
            todos=[],
            iteration_budget_consumed=0,
            iteration_budget_total=10,
            last_tool_call=None,
            last_tool_result=None,
            partial_response="",
            interrupted_by=None,
        )
        assert cp.created_at.tzinfo is not None


# ---------------------------------------------------------------------------
# CheckpointPort — abstract interface
# ---------------------------------------------------------------------------


class TestCheckpointPort:
    @pytest.mark.asyncio
    async def test_in_memory_save_load_roundtrip(self) -> None:
        store = InMemoryCheckpointAdapter()
        cp = _make_checkpoint()
        await store.save(cp)
        loaded = await store.load("task_001")
        assert loaded is not None
        assert loaded.task_id == "task_001"
        assert loaded.user_input == "do something"

    @pytest.mark.asyncio
    async def test_load_missing_returns_none(self) -> None:
        store = InMemoryCheckpointAdapter()
        assert await store.load("nonexistent") is None

    @pytest.mark.asyncio
    async def test_delete_removes_checkpoint(self) -> None:
        store = InMemoryCheckpointAdapter()
        cp = _make_checkpoint()
        await store.save(cp)
        await store.delete("task_001")
        assert await store.load("task_001") is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_is_noop(self) -> None:
        store = InMemoryCheckpointAdapter()
        await store.delete("no_such_task")  # must not raise

    @pytest.mark.asyncio
    async def test_list_task_ids(self) -> None:
        store = InMemoryCheckpointAdapter()
        await store.save(_make_checkpoint("a"))
        await store.save(_make_checkpoint("b"))
        ids = await store.list_task_ids()
        assert set(ids) == {"a", "b"}

    @pytest.mark.asyncio
    async def test_overwrite_on_same_task_id(self) -> None:
        store = InMemoryCheckpointAdapter()
        cp1 = _make_checkpoint(interrupted_by=InterruptReason.SIGINT)
        cp2 = _make_checkpoint(interrupted_by=InterruptReason.SIGTERM)
        await store.save(cp1)
        await store.save(cp2)
        loaded = await store.load("task_001")
        assert loaded is not None
        assert loaded.interrupted_by == InterruptReason.SIGTERM


# ---------------------------------------------------------------------------
# DiskCheckpointAdapter
# ---------------------------------------------------------------------------


class TestDiskCheckpointAdapter:
    @pytest.mark.asyncio
    async def test_save_creates_json_file(self, tmp_path: Path) -> None:
        adapter = DiskCheckpointAdapter(checkpoint_dir=tmp_path)
        cp = _make_checkpoint()
        await adapter.save(cp)
        files = list(tmp_path.glob("*.json.gz"))
        assert len(files) == 1
        assert files[0].name == "task_001.json.gz"

    @pytest.mark.asyncio
    async def test_roundtrip_all_fields(self, tmp_path: Path) -> None:
        adapter = DiskCheckpointAdapter(checkpoint_dir=tmp_path)
        cp = _make_checkpoint(interrupted_by=InterruptReason.BUDGET_EXHAUSTED)
        await adapter.save(cp)
        loaded = await adapter.load("task_001")
        assert loaded is not None
        assert loaded.task_id == cp.task_id
        assert loaded.user_input == cp.user_input
        assert loaded.messages == cp.messages
        assert loaded.todos == cp.todos
        assert loaded.iteration_budget_consumed == cp.iteration_budget_consumed
        assert loaded.iteration_budget_total == cp.iteration_budget_total
        assert loaded.last_tool_call == cp.last_tool_call
        assert loaded.last_tool_result == cp.last_tool_result
        assert loaded.partial_response == cp.partial_response
        assert loaded.interrupted_by == InterruptReason.BUDGET_EXHAUSTED
        assert loaded.created_at == cp.created_at

    @pytest.mark.asyncio
    async def test_roundtrip_none_fields(self, tmp_path: Path) -> None:
        adapter = DiskCheckpointAdapter(checkpoint_dir=tmp_path)
        cp = Checkpoint(
            task_id="no_tools",
            user_input="hi",
            messages=[],
            todos=[],
            iteration_budget_consumed=0,
            iteration_budget_total=10,
            last_tool_call=None,
            last_tool_result=None,
            partial_response="",
            interrupted_by=None,
        )
        await adapter.save(cp)
        loaded = await adapter.load("no_tools")
        assert loaded is not None
        assert loaded.last_tool_call is None
        assert loaded.last_tool_result is None
        assert loaded.interrupted_by is None

    @pytest.mark.asyncio
    async def test_load_nonexistent_returns_none(self, tmp_path: Path) -> None:
        adapter = DiskCheckpointAdapter(checkpoint_dir=tmp_path)
        assert await adapter.load("ghost") is None

    @pytest.mark.asyncio
    async def test_delete_removes_file(self, tmp_path: Path) -> None:
        adapter = DiskCheckpointAdapter(checkpoint_dir=tmp_path)
        await adapter.save(_make_checkpoint())
        await adapter.delete("task_001")
        assert not (tmp_path / "task_001.json").exists()

    @pytest.mark.asyncio
    async def test_delete_nonexistent_no_raise(self, tmp_path: Path) -> None:
        adapter = DiskCheckpointAdapter(checkpoint_dir=tmp_path)
        await adapter.delete("ghost")  # must not raise

    @pytest.mark.asyncio
    async def test_list_task_ids_newest_first(self, tmp_path: Path) -> None:
        adapter = DiskCheckpointAdapter(checkpoint_dir=tmp_path)
        await adapter.save(_make_checkpoint("first"))
        # Touch second file slightly later to ensure ordering.
        time.sleep(0.01)
        await adapter.save(_make_checkpoint("second"))
        ids = await adapter.list_task_ids()
        assert ids == ["second", "first"]

    @pytest.mark.asyncio
    async def test_load_corrupt_json_returns_none(self, tmp_path: Path) -> None:
        corrupt_file = tmp_path / "bad.json"
        corrupt_file.write_text("{invalid json")
        adapter = DiskCheckpointAdapter(checkpoint_dir=tmp_path)
        result = await adapter.load("bad")
        assert result is None

    @pytest.mark.asyncio
    async def test_save_is_atomic(self, tmp_path: Path) -> None:
        """Verifies no .tmp file is left behind after a successful save."""
        adapter = DiskCheckpointAdapter(checkpoint_dir=tmp_path)
        await adapter.save(_make_checkpoint())
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []

    @pytest.mark.asyncio
    async def test_path_traversal_sanitised(self, tmp_path: Path) -> None:
        adapter = DiskCheckpointAdapter(checkpoint_dir=tmp_path)
        cp = _make_checkpoint(task_id="../evil")
        await adapter.save(cp)
        # The file should be inside tmp_path, not outside it.
        saved_files = list(tmp_path.glob("*.json.gz"))
        assert len(saved_files) == 1
        assert saved_files[0].parent == tmp_path

    def test_to_dict_from_dict_roundtrip(self) -> None:
        cp = _make_checkpoint(interrupted_by=InterruptReason.TING_CANCEL)
        d = _to_dict(cp)
        assert d["interrupted_by"] == "ting_cancel"
        restored = _from_dict(d)
        assert restored.interrupted_by == InterruptReason.TING_CANCEL
        assert restored.created_at == cp.created_at

    def test_from_dict_none_interrupted_by(self) -> None:
        d = _to_dict(_make_checkpoint())
        d["interrupted_by"] = None
        cp = _from_dict(d)
        assert cp.interrupted_by is None

    def test_creates_dir_if_not_exists(self, tmp_path: Path) -> None:
        new_dir = tmp_path / "deep" / "checkpoints"
        DiskCheckpointAdapter(checkpoint_dir=new_dir)
        assert new_dir.exists()

    def test_default_dir_is_home_ravn_checkpoints(self) -> None:
        adapter = DiskCheckpointAdapter()
        assert adapter._dir == Path.home() / ".ravn" / "checkpoints"


# ---------------------------------------------------------------------------
# Agent integration tests
# ---------------------------------------------------------------------------


def _make_tool_response(
    tool_name: str = "echo",
    tool_input: dict | None = None,
    *,
    tool_id: str = "tc1",
) -> LLMResponse:
    """Build an LLMResponse that triggers a single tool call."""
    return LLMResponse(
        content="",
        tool_calls=[ToolCall(id=tool_id, name=tool_name, input=tool_input or {"message": "hi"})],
        stop_reason=StopReason.TOOL_USE,
        usage=TokenUsage(input_tokens=10, output_tokens=5),
    )


@pytest.mark.asyncio
async def test_checkpoint_written_after_tool_call() -> None:
    """Checkpoint is persisted once after a tool call completes."""
    store = InMemoryCheckpointAdapter()
    llm = MockLLM(
        [
            _make_tool_response(),
            make_text_response("done"),
        ]
    )
    channel = InMemoryChannel()
    agent = RavnAgent(
        llm=llm,
        tools=[EchoTool()],
        channel=channel,
        permission=AllowAllPermission(),
        system_prompt="test",
        model="claude-sonnet-4-6",
        max_tokens=1024,
        max_iterations=10,
        checkpoint_port=store,
        task_id="test_task",
    )
    await agent.run_turn("hello")
    assert "test_task" in store._store
    cp = store._store["test_task"]
    assert cp.last_tool_call is not None
    assert cp.last_tool_call["name"] == "echo"


@pytest.mark.asyncio
async def test_checkpoint_not_written_without_tool_call() -> None:
    """No checkpoint is written for pure text turns (no tool calls)."""
    store = InMemoryCheckpointAdapter()
    llm = MockLLM([make_text_response("hello")])
    channel = InMemoryChannel()
    agent = RavnAgent(
        llm=llm,
        tools=[],
        channel=channel,
        permission=AllowAllPermission(),
        system_prompt="test",
        model="claude-sonnet-4-6",
        max_tokens=1024,
        max_iterations=10,
        checkpoint_port=store,
        task_id="pure_text_task",
    )
    await agent.run_turn("hi")
    assert "pure_text_task" not in store._store


@pytest.mark.asyncio
async def test_checkpoint_written_on_budget_exhausted() -> None:
    """Checkpoint with BUDGET_EXHAUSTED reason when max iterations exceeded."""
    store = InMemoryCheckpointAdapter()
    # LLM always wants a tool call — exhausts the 2-iteration budget.
    llm = MockLLM(
        [
            _make_tool_response(tool_id="tc1"),
            _make_tool_response(tool_id="tc2"),
            _make_tool_response(tool_id="tc3"),
        ]
    )
    channel = InMemoryChannel()
    agent = RavnAgent(
        llm=llm,
        tools=[EchoTool()],
        channel=channel,
        permission=AllowAllPermission(),
        system_prompt="test",
        model="claude-sonnet-4-6",
        max_tokens=1024,
        max_iterations=2,
        checkpoint_port=store,
        task_id="budget_task",
    )
    with pytest.raises(MaxIterationsError):
        await agent.run_turn("loop forever")

    assert "budget_task" in store._store
    cp = store._store["budget_task"]
    assert cp.interrupted_by == InterruptReason.BUDGET_EXHAUSTED


@pytest.mark.asyncio
async def test_cancel_event_stops_loop_and_checkpoints() -> None:
    """When agent.interrupt() is called, the loop stops with TING_CANCEL reason."""
    store = InMemoryCheckpointAdapter()

    # Tool call triggers first iteration; we call interrupt before the second.
    call_count = 0

    class CancellingLLM(MockLLM):
        async def stream(  # type: ignore[override]
            self, *args: Any, **kwargs: Any
        ) -> AsyncIterator[StreamEvent]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield StreamEvent(
                    type=StreamEventType.TOOL_CALL,
                    tool_call=ToolCall(id="tc1", name="echo", input={"message": "hi"}),
                )
                yield StreamEvent(
                    type=StreamEventType.MESSAGE_DONE,
                    usage=TokenUsage(input_tokens=5, output_tokens=2),
                )
            else:
                # Second LLM call: interrupt was called before entering this iteration.
                yield StreamEvent(type=StreamEventType.TEXT_DELTA, text="cancelled")
                yield StreamEvent(
                    type=StreamEventType.MESSAGE_DONE,
                    usage=TokenUsage(input_tokens=5, output_tokens=2),
                )

    channel = InMemoryChannel()
    agent = RavnAgent(
        llm=CancellingLLM([]),
        tools=[EchoTool()],
        channel=channel,
        permission=AllowAllPermission(),
        system_prompt="test",
        model="claude-sonnet-4-6",
        max_tokens=1024,
        max_iterations=10,
        checkpoint_port=store,
        task_id="cancel_task",
    )

    # Signal cancellation before the second iteration fires.
    agent.interrupt(InterruptReason.TING_CANCEL)
    with pytest.raises(MaxIterationsError):
        await agent.run_turn("do work")

    assert "cancel_task" in store._store
    cp = store._store["cancel_task"]
    assert cp.interrupted_by == InterruptReason.TING_CANCEL


@pytest.mark.asyncio
async def test_checkpoint_save_failure_does_not_crash_agent() -> None:
    """A failing checkpoint adapter must not propagate its error to the caller."""

    class BrokenCheckpoint(CheckpointPort):
        async def save(self, checkpoint: Checkpoint) -> None:
            raise OSError("disk full")

        async def load(self, task_id: str) -> Checkpoint | None:
            return None

        async def delete(self, task_id: str) -> None:
            pass

        async def list_task_ids(self) -> list[str]:
            return []

        async def save_snapshot(self, checkpoint: Checkpoint) -> str:
            return ""

        async def list_for_task(self, task_id: str) -> list[Checkpoint]:
            return []

        async def load_snapshot(self, checkpoint_id: str) -> Checkpoint | None:
            return None

        async def delete_snapshot(self, checkpoint_id: str) -> None:
            pass

    llm = MockLLM(
        [
            _make_tool_response(),
            make_text_response("ok"),
        ]
    )
    channel = InMemoryChannel()
    agent = RavnAgent(
        llm=llm,
        tools=[EchoTool()],
        channel=channel,
        permission=AllowAllPermission(),
        system_prompt="test",
        model="claude-sonnet-4-6",
        max_tokens=1024,
        max_iterations=10,
        checkpoint_port=BrokenCheckpoint(),
        task_id="fragile",
    )
    # Must not raise even though checkpointing fails.
    result = await agent.run_turn("test")
    assert result.response == "ok"


@pytest.mark.asyncio
async def test_no_checkpoint_port_is_noop() -> None:
    """Agent without a checkpoint port runs normally without writing checkpoints."""
    llm = MockLLM(
        [
            _make_tool_response(),
            make_text_response("done"),
        ]
    )
    channel = InMemoryChannel()
    agent = RavnAgent(
        llm=llm,
        tools=[EchoTool()],
        channel=channel,
        permission=AllowAllPermission(),
        system_prompt="test",
        model="claude-sonnet-4-6",
        max_tokens=1024,
        max_iterations=10,
        # No checkpoint_port
    )
    result = await agent.run_turn("no checkpoint")
    assert result.response == "done"


@pytest.mark.asyncio
async def test_checkpoint_contains_todos() -> None:
    """Checkpoint captures the todo list from the session."""
    from ravn.domain.models import TodoItem, TodoStatus

    store = InMemoryCheckpointAdapter()
    llm = MockLLM([_make_tool_response(), make_text_response("done")])
    session = Session()
    session.upsert_todo(
        TodoItem(id="todo1", content="do thing", status=TodoStatus.IN_PROGRESS, priority=0)
    )
    channel = InMemoryChannel()
    agent = RavnAgent(
        llm=llm,
        tools=[EchoTool()],
        channel=channel,
        permission=AllowAllPermission(),
        system_prompt="test",
        model="claude-sonnet-4-6",
        max_tokens=1024,
        max_iterations=10,
        checkpoint_port=store,
        task_id="todo_task",
        session=session,
    )
    await agent.run_turn("work")
    cp = store._store["todo_task"]
    assert len(cp.todos) == 1
    assert cp.todos[0]["id"] == "todo1"
    assert cp.todos[0]["status"] == "in_progress"


@pytest.mark.asyncio
async def test_task_id_property() -> None:
    """agent.task_id returns the configured task_id."""
    agent = RavnAgent(
        llm=MockLLM([]),
        tools=[],
        channel=InMemoryChannel(),
        permission=AllowAllPermission(),
        system_prompt="",
        model="claude-sonnet-4-6",
        max_tokens=512,
        max_iterations=5,
        task_id="my_custom_task",
    )
    assert agent.task_id == "my_custom_task"


def test_agent_task_id_defaults_to_session_id() -> None:
    """When task_id is not supplied, it defaults to the session UUID."""
    session = Session()
    agent = RavnAgent(
        llm=MockLLM([]),
        tools=[],
        channel=InMemoryChannel(),
        permission=AllowAllPermission(),
        system_prompt="",
        model="claude-sonnet-4-6",
        max_tokens=512,
        max_iterations=5,
        session=session,
    )
    assert agent.task_id == str(session.id)
