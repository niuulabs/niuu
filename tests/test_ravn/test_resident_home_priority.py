"""A resident home turn must not starve behind signal work.

Live, a home turn queued at the default priority (10) waited 16 hours behind a
steady stream of idle-triage windows (9) and Kubernetes warning tasks (8), so
an operator's directed message was never read. Home turns carry operator
messages and A2A updates; only one is ever queued or running.
"""

from __future__ import annotations

import pytest

from ravn.adapters.resident_state.mimir import LocalResidentState
from ravn.config import InitiativeConfig, Settings
from ravn.domain.models import RESIDENT_HOME_PRIORITY, AgentTask, OutputMode
from ravn.drive_loop import DriveLoop
from ravn.resident_inbox import LocalResidentInbox
from ravn.resident_runtime import ResidentRuntime


async def _home_task(tmp_path) -> AgentTask:
    inbox = LocalResidentInbox(tmp_path / "inbox")
    runtime = ResidentRuntime(state=LocalResidentState(tmp_path / "state"), inbox=inbox)
    await inbox.write_directed_message(
        content="Please commission k8s_quantity_parse.",
        metadata={"message_id": "operator-1"},
    )
    task = await runtime.next_home_task(limit=5, persona=None, output_mode=OutputMode.AMBIENT)
    assert task is not None
    return task


@pytest.mark.asyncio
async def test_home_turn_is_queued_at_home_priority(tmp_path) -> None:
    task = await _home_task(tmp_path)

    assert task.priority == RESIDENT_HOME_PRIORITY
    assert task.triggered_by == "resident:home"


@pytest.mark.asyncio
async def test_home_turn_runs_before_a_backlog_of_signal_work(tmp_path) -> None:
    loop = DriveLoop(
        agent_factory=lambda **_kwargs: None,
        config=InitiativeConfig(enabled=True, queue_journal_path=str(tmp_path / "queue.json")),
        settings=Settings(),
    )
    for index in range(20):
        await loop.enqueue(
            AgentTask(
                task_id=f"task-warning-{index}",
                title="Pod warning",
                initiative_context="judge it",
                triggered_by="signal:signal.kubernetes.event",
                output_mode=OutputMode.AMBIENT,
                priority=8,
            )
        )
    home = await _home_task(tmp_path)

    assert await loop.enqueue(home) is True
    assert loop.queued_task_ids()[0] == home.task_id
