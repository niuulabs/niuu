"""Tests for TeamTrackerRecorder: writes on a team tracker are recorded, reads are not."""

from __future__ import annotations

from uuid import uuid4

import pytest

from niuu import cap_telemetry
from niuu.domain.models import IntegrationUsage
from niuu.ports.integrations import IntegrationUsageLog
from tests.test_ting.test_tracker_factory import FakeTracker
from ting.adapters.team_tracker import DEGRADED_PATH, TeamTrackerRecorder
from ting.domain.models import PhaseStatus, RunStatus, SessionMessage
from ting.ports.tracker import TrackerPort

CONNECTION_ID = "conn-team"
PROJECT_ID = "proj-1"


class RecordingUsageLog(IntegrationUsageLog):
    def __init__(self, *, fail: bool = False) -> None:
        self.recorded: list[IntegrationUsage] = []
        self._fail = fail

    async def record(self, usage: IntegrationUsage) -> None:
        if self._fail:
            raise ConnectionError("database down")
        self.recorded.append(usage)

    async def list_for_project(self, project_id, *, limit, before=None):  # noqa: ANN001
        raise NotImplementedError


class WritableTracker(FakeTracker):
    """FakeTracker with the writes it leaves unimplemented, and one adapter-only method."""

    async def update_run_progress(self, tracker_id, **kwargs):  # noqa: ANN001, ANN003
        return "run-after"

    async def attach_document(self, project_id, title, content):  # noqa: ANN001
        return "doc-1"

    def provider_only(self) -> str:
        return "adapter-specific"


def _recorder(log: IntegrationUsageLog) -> TeamTrackerRecorder:
    inner = WritableTracker()
    inner.bind_connection(connection_id=CONNECTION_ID, provider="linear", name="Team Linear")
    return TeamTrackerRecorder(
        inner,
        usage_log=log,
        connection_id=CONNECTION_ID,
        project_id=PROJECT_ID,
        acting_for="owner-1",
    )


def test_recorder_is_a_tracker_with_the_inner_connection_binding() -> None:
    recorder = _recorder(RecordingUsageLog())

    assert isinstance(recorder, TrackerPort)
    assert recorder.connection_id == CONNECTION_ID
    assert recorder.provider == "linear"
    assert recorder.connection_name == "Team Linear"
    assert recorder.provider_only() == "adapter-specific"


def test_rebinding_the_recorder_rebinds_the_adapter() -> None:
    recorder = _recorder(RecordingUsageLog())

    recorder.bind_connection(connection_id="other", provider="jira", name="Other")

    assert (recorder.connection_id, recorder.provider, recorder.connection_name) == (
        "other",
        "jira",
        "Other",
    )


@pytest.mark.asyncio
async def test_every_write_is_recorded_with_who_it_acted_for() -> None:
    log = RecordingUsageLog()
    recorder = _recorder(log)
    message = SessionMessage(
        id=uuid4(),
        run_id=uuid4(),
        session_id="ses-1",
        content="hi",
        sender="owner-1",
        created_at=None,  # type: ignore[arg-type]
    )

    assert await recorder.create_saga(object()) == "s-1"  # type: ignore[arg-type]
    assert await recorder.create_phase(object(), project_id="x") == "p-1"  # type: ignore[arg-type]
    assert await recorder.create_run(object()) == "r-1"  # type: ignore[arg-type]
    assert await recorder.attach_document("proj", "t", "c") == "doc-1"
    await recorder.add_comment("issue-1", "body")
    await recorder.attach_issue_document("issue-2", "t", "c")
    await recorder.update_run_state("run-1", RunStatus.RUNNING)
    await recorder.close_run("run-2")
    assert await recorder.update_run_progress("run-3", status=RunStatus.RUNNING) == "run-after"
    assert await recorder.update_phase_status("phase-1", PhaseStatus.ACTIVE) is None
    await recorder.save_session_message(message)

    assert [(u.action, u.subject) for u in log.recorded] == [
        ("create_saga", "s-1"),
        ("create_phase", "p-1"),
        ("create_run", "r-1"),
        ("attach_document", "doc-1"),
        ("add_comment", "issue-1"),
        ("attach_issue_document", "issue-2"),
        ("update_run_state", "run-1"),
        ("close_run", "run-2"),
        ("update_run_progress", "run-3"),
        ("update_phase_status", "phase-1"),
        ("save_session_message", str(message.run_id)),
    ]
    for usage in log.recorded:
        assert usage.connection_id == CONNECTION_ID
        assert usage.project_id == PROJECT_ID
        assert usage.acting_for == "owner-1"
        assert usage.actor == "ting"


@pytest.mark.asyncio
async def test_reads_are_not_recorded() -> None:
    log = RecordingUsageLog()
    recorder = _recorder(log)

    assert await recorder.list_projects() == []
    assert await recorder.list_issues("p") == []
    assert await recorder.list_milestones("p") == []
    assert await recorder.list_pending_runs("ph") == []
    assert await recorder.get_run_by_session("s") is None
    assert await recorder.get_blocked_identifiers("p") == set()
    assert await recorder.get_session_messages("r") == []
    assert await recorder.all_runs_merged("ph") is False

    assert log.recorded == []


@pytest.mark.asyncio
async def test_a_failed_write_is_not_recorded() -> None:
    log = RecordingUsageLog()
    recorder = _recorder(log)

    async def broken(run_id):  # noqa: ANN001, ANN202
        raise RuntimeError("tracker refused")

    recorder._inner.close_run = broken  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="tracker refused"):
        await recorder.close_run("run-1")

    assert log.recorded == []


@pytest.mark.asyncio
async def test_a_usage_record_failure_after_the_write_is_reported_as_degraded() -> None:
    recorder = _recorder(RecordingUsageLog(fail=True))
    before = cap_telemetry.degraded_counts().get(DEGRADED_PATH, 0)

    await recorder.close_run("run-1")  # the write already happened: it is not undone

    assert cap_telemetry.degraded_counts()[DEGRADED_PATH] == before + 1
