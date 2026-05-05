"""Tests for TaskContextChannel."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from ravn.adapters.channels.task_context import TaskContextChannel
from ravn.domain.events import RavnEvent, RavnEventType


def _make_event() -> RavnEvent:
    return RavnEvent(
        type=RavnEventType.THOUGHT,
        source="ravn-test",
        payload={"text": "hello"},
        timestamp=datetime.now(UTC),
        urgency=0.1,
        correlation_id="agent-session-id",
        session_id="agent-session-id",
        task_id=None,
        root_correlation_id="",
    )


@pytest.mark.asyncio
async def test_emit_overrides_event_context_for_mesh_task() -> None:
    downstream = AsyncMock()
    channel = TaskContextChannel(
        downstream,
        correlation_id="task-123",
        session_id="volundr-session-abc",
        task_id="task-123",
        root_correlation_id="volundr-session-abc",
    )

    await channel.emit(_make_event())

    emitted = downstream.emit.await_args.args[0]
    assert emitted.correlation_id == "task-123"
    assert emitted.session_id == "volundr-session-abc"
    assert emitted.task_id == "task-123"
    assert emitted.root_correlation_id == "volundr-session-abc"
    assert emitted.payload == {"text": "hello"}
