from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ravn.cli.commands import _wire_mimir_triggers
from ravn.config import Settings


def test_workflow_runtime_skips_background_mimir_triggers() -> None:
    drive_loop = MagicMock()
    mimir = MagicMock()
    llm = MagicMock()
    interaction_tracker = MagicMock()
    source_trigger = MagicMock(name="source-trigger")
    staleness_trigger = MagicMock(name="staleness-trigger")
    thread_queue_trigger = MagicMock(name="thread-queue-trigger")

    settings = Settings(
        workflow={"graph": {"nodes": [{"id": "dispatch-root", "kind": "trigger"}]}},
        mimir={
            "source_trigger": {"enabled": True},
            "staleness_trigger": {"enabled": True},
        },
    )

    with (
        patch(
            "ravn.adapters.triggers.mimir_source.MimirSourceTrigger",
            return_value=source_trigger,
        ) as mock_source,
        patch(
            "ravn.adapters.triggers.mimir_staleness.MimirStalenessTrigger",
            return_value=staleness_trigger,
        ) as mock_staleness,
        patch("ravn.adapters.mimir.usage_log.LogBasedUsageAdapter") as mock_usage,
        patch(
            "ravn.adapters.triggers.thread_queue.ThreadQueueTrigger",
            return_value=thread_queue_trigger,
        ) as mock_thread_queue,
    ):
        _wire_mimir_triggers(
            drive_loop,
            mimir,
            settings,
            llm=llm,
            interaction_tracker=interaction_tracker,
        )

    mock_source.assert_not_called()
    mock_staleness.assert_not_called()
    mock_usage.assert_not_called()
    mock_thread_queue.assert_called_once_with(mimir=mimir, config=settings.thread)
    assert drive_loop.register_trigger.call_count == 1
    assert drive_loop.register_trigger.call_args.args == (thread_queue_trigger,)


def test_plain_runtime_registers_mimir_background_triggers() -> None:
    drive_loop = MagicMock()
    mimir = MagicMock()
    source_trigger = MagicMock(name="source-trigger")
    staleness_trigger = MagicMock(name="staleness-trigger")
    thread_queue_trigger = MagicMock(name="thread-queue-trigger")
    usage = SimpleNamespace()

    settings = Settings(
        workflow={"graph": {}},
        mimir={
            "path": "/tmp/mimir-test",
            "source_trigger": {"enabled": True},
            "staleness_trigger": {"enabled": True},
        },
    )

    with (
        patch(
            "ravn.adapters.triggers.mimir_source.MimirSourceTrigger",
            return_value=source_trigger,
        ) as mock_source,
        patch(
            "ravn.adapters.triggers.mimir_staleness.MimirStalenessTrigger",
            return_value=staleness_trigger,
        ) as mock_staleness,
        patch(
            "ravn.adapters.triggers.thread_queue.ThreadQueueTrigger",
            return_value=thread_queue_trigger,
        ) as mock_thread_queue,
        patch(
            "ravn.adapters.mimir.usage_log.LogBasedUsageAdapter",
            return_value=usage,
        ) as mock_usage,
    ):
        _wire_mimir_triggers(drive_loop, mimir, settings)

    mock_source.assert_called_once()
    mock_usage.assert_called_once_with(mimir_root="/tmp/mimir-test")
    mock_staleness.assert_called_once_with(
        mimir=mimir,
        usage=usage,
        config=settings.mimir.staleness_trigger,
    )
    mock_thread_queue.assert_called_once_with(mimir=mimir, config=settings.thread)
    assert drive_loop.register_trigger.call_args_list[0].args == (source_trigger,)
    assert drive_loop.register_trigger.call_args_list[1].args == (staleness_trigger,)
    assert drive_loop.register_trigger.call_args_list[2].args == (thread_queue_trigger,)
