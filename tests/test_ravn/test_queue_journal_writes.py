"""Regression tests for the drive loop's queue journal writes.

Seen live in a Skuld flock pod: the first mesh event after start enqueued a
task (a synchronous journal write on the event loop) while
`record_workflow_event_consumed` was writing the journal from a worker thread.
Both writers used the same ``queue.json.tmp`` name, so one renamed the other's
temporary file away and the second rename failed with ENOENT. The consumed
ledger write reported failure and the mesh handler raised, which asks the
transport to redeliver an event that had in fact been enqueued.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from unittest.mock import patch

import pytest

from tests.test_ravn.conftest import _make_agent_task, _make_drive_loop


def test_persist_queue_creates_missing_daemon_directory(tmp_path) -> None:
    journal_path = tmp_path / ".ravn" / "research-synthesist" / "daemon" / "queue.json"
    dl = _make_drive_loop(journal_path=str(journal_path))

    assert not journal_path.parent.exists()
    assert dl._persist_queue() is True

    assert json.loads(journal_path.read_text())["queue"] == []
    assert list(journal_path.parent.iterdir()) == [journal_path]


@pytest.mark.asyncio
async def test_record_consumed_event_into_missing_daemon_directory(tmp_path) -> None:
    journal_path = tmp_path / ".ravn" / "research-framer" / "daemon" / "queue.json"
    dl = _make_drive_loop(journal_path=str(journal_path))

    await dl.record_workflow_event_consumed("event-1")

    journal = json.loads(journal_path.read_text())
    assert [entry["event_id"] for entry in journal["consumed_workflow_events"]] == ["event-1"]


@pytest.mark.asyncio
async def test_run_creates_journal_directory_before_events_arrive(tmp_path) -> None:
    journal_path = tmp_path / ".ravn" / "research-framer" / "daemon" / "queue.json"
    dl = _make_drive_loop(journal_path=str(journal_path))

    loop_task = asyncio.create_task(dl.run())
    try:
        await asyncio.sleep(0)
        assert journal_path.parent.is_dir()
        await dl.enqueue(_make_agent_task("event_research_frame_1"))
        await dl.record_workflow_event_consumed("event-1")
    finally:
        loop_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await loop_task

    assert dl.workflow_event_consumed("event-1")


@pytest.mark.asyncio
async def test_concurrent_loop_and_worker_journal_writes_do_not_fail(tmp_path) -> None:
    """An on-loop write racing the off-loop consumed-ledger write must not fail it."""
    journal_path = tmp_path / "daemon" / "queue.json"
    dl = _make_drive_loop(journal_path=str(journal_path))
    real_replace = os.replace
    worker_writing = threading.Event()

    def slow_worker_replace(src, dst):
        # Hold the worker thread between writing its temp file and renaming it,
        # which is where the live pod's event loop wrote the journal as well.
        if threading.current_thread() is not threading.main_thread():
            worker_writing.set()
            time.sleep(0.1)
        real_replace(src, dst)

    with patch("os.replace", side_effect=slow_worker_replace):
        record = asyncio.create_task(dl.record_workflow_event_consumed("event-1"))
        while not worker_writing.is_set():
            await asyncio.sleep(0.005)
        await dl.enqueue(_make_agent_task("task-after-event"))
        await record

    assert dl.workflow_event_consumed("event-1")
    journal = json.loads(journal_path.read_text())
    assert [entry["event_id"] for entry in journal["consumed_workflow_events"]] == ["event-1"]
    assert [record["task_id"] for record in journal["queue"]] == ["task-after-event"]
    assert list(journal_path.parent.iterdir()) == [journal_path]


def test_older_journal_snapshot_never_overwrites_newer(tmp_path) -> None:
    journal_path = tmp_path / "queue.json"
    dl = _make_drive_loop(journal_path=str(journal_path))

    assert dl._write_journal_snapshot('{"generation": 2}', 2) is True
    assert dl._write_journal_snapshot('{"generation": 1}', 1) is True

    assert json.loads(journal_path.read_text()) == {"generation": 2}


def test_failed_journal_write_leaves_no_temp_file(tmp_path) -> None:
    journal_path = tmp_path / "queue.json"
    dl = _make_drive_loop(journal_path=str(journal_path))

    with patch("os.replace", side_effect=OSError("disk full")):
        assert dl._write_journal_snapshot("{}", 1) is False

    assert list(tmp_path.iterdir()) == []
