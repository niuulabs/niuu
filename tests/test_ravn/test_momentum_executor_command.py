from __future__ import annotations

import pytest

from ravn.adapters.momentum_executor.command import CommandMomentumExecutorHandoffAdapter
from ravn.adapters.process_runner import CommandResult
from ravn.ports.momentum_executor import MomentumExecutorInput


async def _runner(argv, **kwargs):
    _runner.calls.append((argv, kwargs))
    return CommandResult(0, "completed handoff\nwith details", "")


_runner.calls = []


async def _failing_runner(argv, **kwargs):
    _failing_runner.calls.append((argv, kwargs))
    return CommandResult(2, "", "boom")


_failing_runner.calls = []


@pytest.mark.asyncio
async def test_command_momentum_executor_adapter_sends_frame_to_command() -> None:
    _runner.calls.clear()
    adapter = CommandMomentumExecutorHandoffAdapter(
        command="codex",
        args=["exec"],
        label="local-codex",
        context_name="codex exec",
        runner=_runner,
    )

    result = await adapter.handoff(
        MomentumExecutorInput(
            brief_ref="resident/continuation/momentum/delegations/demo.md",
            brief_id="delegation-demo",
            input_frame="# frame",
        )
    )

    assert result.status == "completed"
    assert result.executor_label == "local-codex"
    assert result.executor_context == "codex exec"
    assert result.summary == "completed handoff"
    assert result.output == "completed handoff\nwith details"
    assert _runner.calls == [
        (
            ["codex", "exec"],
            {
                "timeout_seconds": 300.0,
                "cwd": None,
                "env": None,
                "input_text": "# frame",
                "check": False,
            },
        )
    ]


@pytest.mark.asyncio
async def test_command_momentum_executor_adapter_records_command_failure() -> None:
    _failing_runner.calls.clear()
    adapter = CommandMomentumExecutorHandoffAdapter(
        command="executor",
        runner=_failing_runner,
    )

    result = await adapter.handoff(
        MomentumExecutorInput(
            brief_ref="resident/continuation/momentum/delegations/demo.md",
            brief_id="delegation-demo",
            input_frame="# frame",
        )
    )

    assert result.status == "failed"
    assert result.summary == (
        "Executor command failed for resident/continuation/momentum/delegations/demo.md."
    )
    assert result.errors == ["boom"]
    assert result.follow_up_recommended == "ask_human"
