"""Tests for NativeTrackerAdapter — PostgreSQL-backed TrackerPort."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from tyr.adapters.native import (
    _DISPLAY_TO_RAID,
    _RAID_STATUS_DISPLAY,
    NativeTrackerAdapter,
)
from tyr.domain.models import (
    ConfidenceEvent,
    ConfidenceEventType,
    Phase,
    PhaseStatus,
    Raid,
    RaidStatus,
    Saga,
    SagaStatus,
    SessionMessage,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime(2026, 3, 22, 12, 0, 0, tzinfo=UTC)


def _make_pool() -> AsyncMock:
    pool = AsyncMock()
    return pool


def _make_adapter(pool: AsyncMock | None = None) -> NativeTrackerAdapter:
    return NativeTrackerAdapter(pool=pool or _make_pool())


def _make_saga(
    saga_id: UUID | None = None,
    slug: str = "test-saga",
    name: str = "Test Saga",
) -> Saga:
    return Saga(
        id=saga_id or uuid4(),
        tracker_id="",
        tracker_type="native",
        slug=slug,
        name=name,
        repos=["org/repo"],
        feature_branch=f"feat/{slug}",
        status=SagaStatus.ACTIVE,
        confidence=0.8,
        created_at=NOW,
        base_branch="dev",
    )


def _make_phase(
    phase_id: UUID | None = None,
    saga_id: UUID | None = None,
) -> Phase:
    return Phase(
        id=phase_id or uuid4(),
        saga_id=saga_id or uuid4(),
        tracker_id="",
        number=1,
        name="Phase 1",
        status=PhaseStatus.PENDING,
        confidence=0.5,
    )


def _make_raid(
    raid_id: UUID | None = None,
    phase_id: UUID | None = None,
    status: RaidStatus = RaidStatus.PENDING,
) -> Raid:
    return Raid(
        id=raid_id or uuid4(),
        phase_id=phase_id or uuid4(),
        tracker_id="",
        name="Test Raid",
        description="Do the thing",
        acceptance_criteria=["Tests pass", "No lint errors"],
        declared_files=["src/main.py"],
        estimate_hours=2.0,
        status=status,
        confidence=0.6,
        session_id="sess-1",
        branch="feat/test",
        chronicle_summary="Summary",
        pr_url=None,
        pr_id=None,
        retry_count=0,
        created_at=NOW,
        updated_at=NOW,
    )


def _saga_record(saga: Saga, tracker_id: str) -> dict:
    """Simulate an asyncpg Record as a dict for a saga row."""
    return {
        "id": saga.id,
        "tracker_id": tracker_id,
        "tracker_type": "native",
        "slug": saga.slug,
        "name": saga.name,
        "repos": saga.repos,
        "feature_branch": saga.feature_branch,
        "status": saga.status.value,
        "confidence": saga.confidence,
        "created_at": saga.created_at,
        "base_branch": saga.base_branch,
    }


def _phase_record(phase: Phase, tracker_id: str) -> dict:
    return {
        "id": phase.id,
        "saga_id": phase.saga_id,
        "tracker_id": tracker_id,
        "number": phase.number,
        "name": phase.name,
        "status": phase.status.value,
        "confidence": phase.confidence,
    }


def _raid_record(raid: Raid, tracker_id: str) -> dict:
    return {
        "id": raid.id,
        "phase_id": raid.phase_id,
        "tracker_id": tracker_id,
        "name": raid.name,
        "description": raid.description,
        "acceptance_criteria": raid.acceptance_criteria,
        "declared_files": raid.declared_files,
        "estimate_hours": raid.estimate_hours,
        "status": raid.status.value,
        "confidence": raid.confidence,
        "session_id": raid.session_id,
        "branch": raid.branch,
        "chronicle_summary": raid.chronicle_summary,
        "retry_count": raid.retry_count,
        "created_at": raid.created_at,
        "updated_at": raid.updated_at,
    }


# ---------------------------------------------------------------------------
# create_saga
# ---------------------------------------------------------------------------


class TestCreateSaga:
    async def test_returns_tracker_id(self):
        pool = _make_pool()
        adapter = _make_adapter(pool)
        saga = _make_saga()

        result = await adapter.create_saga(saga)

        assert result == str(saga.id)
        pool.execute.assert_called_once()

    async def test_inserts_correct_values(self):
        pool = _make_pool()
        adapter = _make_adapter(pool)
        saga = _make_saga()

        await adapter.create_saga(saga)

        args = pool.execute.call_args[0]
        assert "INSERT INTO sagas" in args[0]
        assert args[8] == saga.base_branch
        assert args[12] == saga.owner_id
        assert args[13] == saga.workflow_id
        assert args[14] == saga.workflow_version
        assert args[15] is None

    async def test_serializes_workflow_snapshot(self):
        pool = _make_pool()
        adapter = _make_adapter(pool)
        saga = Saga(
            id=uuid4(),
            tracker_id="",
            tracker_type="native",
            slug="workflow-saga",
            name="Workflow Saga",
            repos=["org/repo"],
            feature_branch="feat/workflow-saga",
            status=SagaStatus.ACTIVE,
            confidence=0.8,
            created_at=NOW,
            base_branch="main",
            owner_id="dev-user",
            workflow_snapshot={"name": "Review Flow"},
        )

        await adapter.create_saga(saga)

        args = pool.execute.call_args[0]
        assert args[15] == json.dumps({"name": "Review Flow"})
        assert args[1] == saga.id
        assert args[2] == str(saga.id)  # tracker_id = local UUID
        assert args[3] == "native"
        assert args[4] == saga.slug
        assert args[5] == saga.name

    async def test_tracker_type_is_native(self):
        pool = _make_pool()
        adapter = _make_adapter(pool)
        saga = _make_saga()

        await adapter.create_saga(saga)

        args = pool.execute.call_args[0]
        assert args[3] == "native"


# ---------------------------------------------------------------------------
# create_phase
# ---------------------------------------------------------------------------


class TestCreatePhase:
    async def test_returns_tracker_id(self):
        pool = _make_pool()
        adapter = _make_adapter(pool)
        phase = _make_phase()

        result = await adapter.create_phase(phase)

        assert result == str(phase.id)
        pool.execute.assert_called_once()

    async def test_inserts_correct_values(self):
        pool = _make_pool()
        adapter = _make_adapter(pool)
        phase = _make_phase()

        await adapter.create_phase(phase)

        args = pool.execute.call_args[0]
        assert "INSERT INTO phases" in args[0]
        assert args[1] == phase.id
        assert args[2] == phase.saga_id
        assert args[4] == phase.number
        assert args[5] == phase.name


# ---------------------------------------------------------------------------
# create_raid
# ---------------------------------------------------------------------------


class TestCreateRaid:
    async def test_returns_tracker_id(self):
        pool = _make_pool()
        adapter = _make_adapter(pool)
        raid = _make_raid()

        result = await adapter.create_raid(raid)

        assert result == str(raid.id)
        pool.execute.assert_called_once()

    async def test_inserts_description_and_criteria(self):
        pool = _make_pool()
        adapter = _make_adapter(pool)
        raid = _make_raid()

        await adapter.create_raid(raid)

        args = pool.execute.call_args[0]
        assert "INSERT INTO raids" in args[0]
        assert args[5] == raid.description
        assert args[6] == raid.acceptance_criteria

    async def test_inserts_all_fields(self):
        pool = _make_pool()
        adapter = _make_adapter(pool)
        raid = _make_raid()

        await adapter.create_raid(raid)

        args = pool.execute.call_args[0]
        assert args[1] == raid.id
        assert args[4] == raid.name
        assert args[7] == raid.declared_files
        assert args[8] == raid.estimate_hours
        assert args[11] == raid.session_id
        assert args[12] == raid.branch


# ---------------------------------------------------------------------------
# update_raid_state
# ---------------------------------------------------------------------------


class TestUpdateRaidState:
    async def test_updates_status(self):
        pool = _make_pool()
        pool.execute.return_value = "UPDATE 1"
        adapter = _make_adapter(pool)

        await adapter.update_raid_state("raid-1", RaidStatus.RUNNING)

        args = pool.execute.call_args[0]
        assert args[1] == "RUNNING"
        assert args[3] == "raid-1"

    async def test_not_found_raises(self):
        pool = _make_pool()
        pool.execute.return_value = "UPDATE 0"
        adapter = _make_adapter(pool)

        with pytest.raises(LookupError, match="Raid not found"):
            await adapter.update_raid_state("bad-id", RaidStatus.RUNNING)

    async def test_updates_timestamp(self):
        pool = _make_pool()
        pool.execute.return_value = "UPDATE 1"
        adapter = _make_adapter(pool)

        await adapter.update_raid_state("raid-1", RaidStatus.REVIEW)

        args = pool.execute.call_args[0]
        # Second arg is datetime
        assert isinstance(args[2], datetime)


# ---------------------------------------------------------------------------
# update_raid_progress
# ---------------------------------------------------------------------------


class TestUpdateRaidProgress:
    async def test_updates_fields_and_returns_raid(self):
        raid = _make_raid()
        record = _raid_record(raid, "t-1")
        pool = _make_pool()
        pool.fetchrow.return_value = record
        adapter = _make_adapter(pool)

        result = await adapter.update_raid_progress(
            "t-1", status=RaidStatus.REVIEW, chronicle_summary="summary"
        )

        assert result.tracker_id == "t-1"
        sql = pool.fetchrow.call_args[0][0]
        assert "chronicle_summary" in sql

    async def test_not_found_raises(self):
        pool = _make_pool()
        pool.fetchrow.return_value = None
        adapter = _make_adapter(pool)

        with pytest.raises(LookupError, match="Raid not found"):
            await adapter.update_raid_progress("missing", status=RaidStatus.REVIEW)


# ---------------------------------------------------------------------------
# close_raid
# ---------------------------------------------------------------------------


class TestCloseRaid:
    async def test_sets_merged_status(self):
        pool = _make_pool()
        pool.execute.return_value = "UPDATE 1"
        adapter = _make_adapter(pool)

        await adapter.close_raid("raid-1")

        args = pool.execute.call_args[0]
        assert args[1] == "MERGED"

    async def test_not_found_raises(self):
        pool = _make_pool()
        pool.execute.return_value = "UPDATE 0"
        adapter = _make_adapter(pool)

        with pytest.raises(LookupError, match="Raid not found"):
            await adapter.close_raid("bad-id")


# ---------------------------------------------------------------------------
# get_saga
# ---------------------------------------------------------------------------


class TestGetSaga:
    async def test_found(self):
        pool = _make_pool()
        adapter = _make_adapter(pool)
        saga = _make_saga()
        tracker_id = str(saga.id)
        pool.fetchrow.return_value = _saga_record(saga, tracker_id)

        result = await adapter.get_saga(tracker_id)

        assert result.tracker_id == tracker_id
        assert result.name == "Test Saga"
        assert result.slug == "test-saga"
        assert result.status == SagaStatus.ACTIVE

    async def test_not_found(self):
        pool = _make_pool()
        pool.fetchrow.return_value = None
        adapter = _make_adapter(pool)

        with pytest.raises(LookupError, match="Saga not found"):
            await adapter.get_saga("bad-id")


# ---------------------------------------------------------------------------
# get_phase
# ---------------------------------------------------------------------------


class TestGetPhase:
    async def test_found(self):
        pool = _make_pool()
        adapter = _make_adapter(pool)
        phase = _make_phase()
        tracker_id = str(phase.id)
        pool.fetchrow.return_value = _phase_record(phase, tracker_id)

        result = await adapter.get_phase(tracker_id)

        assert result.tracker_id == tracker_id
        assert result.name == "Phase 1"
        assert result.number == 1
        assert result.status == PhaseStatus.PENDING

    async def test_not_found(self):
        pool = _make_pool()
        pool.fetchrow.return_value = None
        adapter = _make_adapter(pool)

        with pytest.raises(LookupError, match="Phase not found"):
            await adapter.get_phase("bad-id")


# ---------------------------------------------------------------------------
# get_raid
# ---------------------------------------------------------------------------


class TestGetRaid:
    async def test_found(self):
        pool = _make_pool()
        adapter = _make_adapter(pool)
        raid = _make_raid()
        tracker_id = str(raid.id)
        pool.fetchrow.return_value = _raid_record(raid, tracker_id)

        result = await adapter.get_raid(tracker_id)

        assert result.tracker_id == tracker_id
        assert result.name == "Test Raid"
        assert result.description == "Do the thing"
        assert result.acceptance_criteria == ["Tests pass", "No lint errors"]
        assert result.status == RaidStatus.PENDING

    async def test_not_found(self):
        pool = _make_pool()
        pool.fetchrow.return_value = None
        adapter = _make_adapter(pool)

        with pytest.raises(LookupError, match="Raid not found"):
            await adapter.get_raid("bad-id")

    async def test_preserves_all_fields(self):
        pool = _make_pool()
        adapter = _make_adapter(pool)
        raid = _make_raid()
        tracker_id = str(raid.id)
        pool.fetchrow.return_value = _raid_record(raid, tracker_id)

        result = await adapter.get_raid(tracker_id)

        assert result.declared_files == ["src/main.py"]
        assert result.estimate_hours == 2.0
        assert result.session_id == "sess-1"
        assert result.branch == "feat/test"
        assert result.chronicle_summary == "Summary"
        assert result.retry_count == 0


# ---------------------------------------------------------------------------
# list_pending_raids
# ---------------------------------------------------------------------------


class TestListPendingRaids:
    async def test_returns_pending_and_queued(self):
        pool = _make_pool()
        adapter = _make_adapter(pool)
        raid1 = _make_raid(status=RaidStatus.PENDING)
        raid2 = _make_raid(status=RaidStatus.QUEUED)
        tid1, tid2 = str(raid1.id), str(raid2.id)
        pool.fetch.return_value = [
            _raid_record(raid1, tid1),
            _raid_record(raid2, tid2),
        ]

        result = await adapter.list_pending_raids("phase-tid")

        assert len(result) == 2
        assert result[0].status == RaidStatus.PENDING
        assert result[1].status == RaidStatus.QUEUED

    async def test_empty(self):
        pool = _make_pool()
        pool.fetch.return_value = []
        adapter = _make_adapter(pool)

        result = await adapter.list_pending_raids("phase-tid")
        assert result == []


# ---------------------------------------------------------------------------
# list_projects
# ---------------------------------------------------------------------------


class TestListProjects:
    async def test_happy_path(self):
        pool = _make_pool()
        adapter = _make_adapter(pool)
        saga = _make_saga()
        tracker_id = str(saga.id)
        pool.fetch.return_value = [_saga_record(saga, tracker_id)]
        pool.fetchval.side_effect = [2, 5]  # milestone_count, issue_count

        projects = await adapter.list_projects()

        assert len(projects) == 1
        assert projects[0].id == tracker_id
        assert projects[0].name == "Test Saga"
        assert projects[0].milestone_count == 2
        assert projects[0].issue_count == 5

    async def test_empty(self):
        pool = _make_pool()
        pool.fetch.return_value = []
        adapter = _make_adapter(pool)

        projects = await adapter.list_projects()
        assert projects == []


# ---------------------------------------------------------------------------
# get_project
# ---------------------------------------------------------------------------


class TestGetProject:
    async def test_found(self):
        pool = _make_pool()
        adapter = _make_adapter(pool)
        saga = _make_saga()
        tracker_id = str(saga.id)
        pool.fetchrow.return_value = _saga_record(saga, tracker_id)
        pool.fetchval.side_effect = [1, 3]

        project = await adapter.get_project(tracker_id)

        assert project.id == tracker_id
        assert project.name == "Test Saga"
        assert project.slug == "test-saga"

    async def test_not_found(self):
        pool = _make_pool()
        pool.fetchrow.return_value = None
        adapter = _make_adapter(pool)

        with pytest.raises(LookupError, match="Project not found"):
            await adapter.get_project("bad-id")


# ---------------------------------------------------------------------------
# list_milestones
# ---------------------------------------------------------------------------


class TestListMilestones:
    async def test_happy_path(self):
        pool = _make_pool()
        adapter = _make_adapter(pool)
        phase = _make_phase()
        tracker_id = str(phase.id)
        pool.fetch.return_value = [_phase_record(phase, tracker_id)]

        milestones = await adapter.list_milestones("proj-tid")

        assert len(milestones) == 1
        assert milestones[0].id == tracker_id
        assert milestones[0].project_id == "proj-tid"
        assert milestones[0].name == "Phase 1"
        assert milestones[0].sort_order == 1

    async def test_empty(self):
        pool = _make_pool()
        pool.fetch.return_value = []
        adapter = _make_adapter(pool)

        milestones = await adapter.list_milestones("proj-tid")
        assert milestones == []


# ---------------------------------------------------------------------------
# list_issues
# ---------------------------------------------------------------------------


class TestListIssues:
    async def test_all_issues(self):
        pool = _make_pool()
        adapter = _make_adapter(pool)
        raid = _make_raid()
        tracker_id = str(raid.id)
        pool.fetch.return_value = [
            {
                **_raid_record(raid, tracker_id),
                "milestone_tracker_id": "phase-tid-1",
            }
        ]

        issues = await adapter.list_issues("proj-tid")

        assert len(issues) == 1
        assert issues[0].id == tracker_id
        assert issues[0].title == "Test Raid"
        assert issues[0].status_type == "unstarted"
        assert issues[0].milestone_id == "phase-tid-1"

    async def test_filtered_by_milestone(self):
        pool = _make_pool()
        adapter = _make_adapter(pool)
        raid = _make_raid()
        tracker_id = str(raid.id)
        pool.fetch.return_value = [_raid_record(raid, tracker_id)]

        issues = await adapter.list_issues("proj-tid", milestone_id="ms-tid")

        assert len(issues) == 1
        # Verify the query included the milestone filter
        call_args = pool.fetch.call_args[0]
        assert "p.tracker_id = $2" in call_args[0]

    async def test_empty(self):
        pool = _make_pool()
        pool.fetch.return_value = []
        adapter = _make_adapter(pool)

        issues = await adapter.list_issues("proj-tid")
        assert issues == []


# ---------------------------------------------------------------------------
# close (no-op)
# ---------------------------------------------------------------------------


class TestClose:
    async def test_close_is_noop(self):
        adapter = _make_adapter()
        await adapter.close()  # Should not raise


# ---------------------------------------------------------------------------
# State mapping tables
# ---------------------------------------------------------------------------


class TestStateMappings:
    def test_raid_to_display_completeness(self):
        """Every RaidStatus should have a display mapping."""
        for status in RaidStatus:
            assert status in _RAID_STATUS_DISPLAY

    def test_display_to_raid_round_trip(self):
        """Display names should map back to raid statuses."""
        for status, display in _RAID_STATUS_DISPLAY.items():
            assert _DISPLAY_TO_RAID[display] == status

    def test_pending_display(self):
        assert _RAID_STATUS_DISPLAY[RaidStatus.PENDING] == "Pending"

    def test_running_display(self):
        assert _RAID_STATUS_DISPLAY[RaidStatus.RUNNING] == "In Progress"

    def test_merged_display(self):
        assert _RAID_STATUS_DISPLAY[RaidStatus.MERGED] == "Done"


# ---------------------------------------------------------------------------
# Row conversion edge cases
# ---------------------------------------------------------------------------


class TestRowConversion:
    async def test_saga_with_null_confidence(self):
        pool = _make_pool()
        adapter = _make_adapter(pool)
        record = _saga_record(_make_saga(), "tid-1")
        record["confidence"] = None
        pool.fetchrow.return_value = record

        result = await adapter.get_saga("tid-1")
        assert result.confidence == 0.0

    async def test_saga_with_null_status(self):
        pool = _make_pool()
        adapter = _make_adapter(pool)
        record = _saga_record(_make_saga(), "tid-1")
        record["status"] = None
        pool.fetchrow.return_value = record

        result = await adapter.get_saga("tid-1")
        assert result.status == SagaStatus.ACTIVE

    async def test_raid_with_null_optional_fields(self):
        pool = _make_pool()
        adapter = _make_adapter(pool)
        raid = _make_raid()
        record = _raid_record(raid, "tid-1")
        record["description"] = None
        record["acceptance_criteria"] = None
        record["declared_files"] = None
        record["session_id"] = None
        record["branch"] = None
        record["chronicle_summary"] = None
        record["retry_count"] = None
        pool.fetchrow.return_value = record

        result = await adapter.get_raid("tid-1")

        assert result.description == ""
        assert result.acceptance_criteria == []
        assert result.declared_files == []
        assert result.session_id is None
        assert result.branch is None
        assert result.retry_count == 0

    async def test_phase_with_null_status(self):
        pool = _make_pool()
        adapter = _make_adapter(pool)
        record = _phase_record(_make_phase(), "tid-1")
        record["status"] = None
        pool.fetchrow.return_value = record

        result = await adapter.get_phase("tid-1")
        assert result.status == PhaseStatus.GATED

    async def test_issue_identifier_format(self):
        pool = _make_pool()
        adapter = _make_adapter(pool)
        raid = _make_raid()
        tracker_id = str(raid.id)
        pool.fetch.return_value = [_raid_record(raid, tracker_id)]

        issues = await adapter.list_issues("proj-tid")

        assert issues[0].identifier.startswith("NAT-")
        assert issues[0].identifier == f"NAT-{tracker_id[:8]}"

    async def test_project_description_includes_slug(self):
        pool = _make_pool()
        adapter = _make_adapter(pool)
        saga = _make_saga(slug="my-project")
        tracker_id = str(saga.id)
        pool.fetchrow.return_value = _saga_record(saga, tracker_id)
        pool.fetchval.side_effect = [0, 0]

        project = await adapter.get_project(tracker_id)

        assert "my-project" in project.description


# ---------------------------------------------------------------------------
# TrackerPort contract: NativeAdapter is a valid implementation
# ---------------------------------------------------------------------------


class TestTrackerPortContract:
    def test_implements_tracker_port(self):
        """NativeTrackerAdapter must be a subclass of TrackerPort."""
        from tyr.ports.tracker import TrackerPort

        assert issubclass(NativeTrackerAdapter, TrackerPort)

    def test_all_abstract_methods_implemented(self):
        """Should be instantiable (all abstract methods overridden)."""
        pool = _make_pool()
        adapter = NativeTrackerAdapter(pool=pool)
        assert isinstance(adapter, NativeTrackerAdapter)

    def test_extra_kwargs_ignored(self):
        """Dynamic adapter pattern: extra kwargs should not raise."""
        pool = _make_pool()
        adapter = NativeTrackerAdapter(pool=pool, foo="bar", baz=42)
        assert isinstance(adapter, NativeTrackerAdapter)


# ---------------------------------------------------------------------------
# get_raid_by_session
# ---------------------------------------------------------------------------


class TestGetRaidBySession:
    async def test_found(self):
        pool = _make_pool()
        adapter = _make_adapter(pool)
        raid = _make_raid()
        tracker_id = str(raid.id)
        pool.fetchrow.return_value = _raid_record(raid, tracker_id)

        result = await adapter.get_raid_by_session("sess-1")

        assert result is not None
        assert result.tracker_id == tracker_id

    async def test_not_found_returns_none(self):
        pool = _make_pool()
        pool.fetchrow.return_value = None
        adapter = _make_adapter(pool)

        result = await adapter.get_raid_by_session("sess-missing")
        assert result is None


# ---------------------------------------------------------------------------
# list_raids_by_status
# ---------------------------------------------------------------------------


class TestListRaidsByStatus:
    async def test_returns_matching_raids(self):
        pool = _make_pool()
        adapter = _make_adapter(pool)
        raid = _make_raid(status=RaidStatus.RUNNING)
        tracker_id = str(raid.id)
        pool.fetch.return_value = [_raid_record(raid, tracker_id)]

        result = await adapter.list_raids_by_status(RaidStatus.RUNNING)

        assert len(result) == 1
        assert result[0].status == RaidStatus.RUNNING

    async def test_empty(self):
        pool = _make_pool()
        pool.fetch.return_value = []
        adapter = _make_adapter(pool)

        result = await adapter.list_raids_by_status(RaidStatus.PENDING)
        assert result == []


# ---------------------------------------------------------------------------
# get_raid_by_id
# ---------------------------------------------------------------------------


class TestGetRaidById:
    async def test_found(self):
        pool = _make_pool()
        adapter = _make_adapter(pool)
        raid = _make_raid()
        tracker_id = str(raid.id)
        pool.fetchrow.return_value = _raid_record(raid, tracker_id)

        result = await adapter.get_raid_by_id(raid.id)

        assert result is not None
        assert result.tracker_id == tracker_id

    async def test_not_found_returns_none(self):
        pool = _make_pool()
        pool.fetchrow.return_value = None
        adapter = _make_adapter(pool)

        result = await adapter.get_raid_by_id(uuid4())
        assert result is None


# ---------------------------------------------------------------------------
# add_confidence_event
# ---------------------------------------------------------------------------


class TestAddConfidenceEvent:
    async def test_inserts_event_and_updates_confidence(self):
        pool = _make_pool()
        adapter = _make_adapter(pool)
        raid = _make_raid()
        event = ConfidenceEvent(
            id=uuid4(),
            raid_id=raid.id,
            event_type=ConfidenceEventType.CI_PASS,
            delta=0.05,
            score_after=0.75,
            created_at=NOW,
        )

        await adapter.add_confidence_event("tracker-1", event)

        assert pool.execute.call_count == 2
        insert_sql = pool.execute.call_args_list[0][0][0]
        assert "INSERT INTO confidence_events" in insert_sql
        update_sql = pool.execute.call_args_list[1][0][0]
        assert "UPDATE raids SET confidence" in update_sql


# ---------------------------------------------------------------------------
# get_confidence_events
# ---------------------------------------------------------------------------


class TestGetConfidenceEvents:
    async def test_returns_events(self):
        pool = _make_pool()
        adapter = _make_adapter(pool)
        raid_id = uuid4()
        pool.fetch.return_value = [
            {
                "id": uuid4(),
                "raid_id": raid_id,
                "event_type": "ci_pass",
                "delta": 0.05,
                "score_after": 0.75,
                "created_at": NOW,
            }
        ]

        result = await adapter.get_confidence_events("tracker-1")

        assert len(result) == 1
        assert result[0].event_type == ConfidenceEventType.CI_PASS
        assert result[0].score_after == 0.75

    async def test_empty(self):
        pool = _make_pool()
        pool.fetch.return_value = []
        adapter = _make_adapter(pool)

        result = await adapter.get_confidence_events("tracker-1")
        assert result == []


# ---------------------------------------------------------------------------
# all_raids_merged
# ---------------------------------------------------------------------------


class TestAllRaidsMerged:
    async def test_true_when_remaining_is_zero(self):
        pool = _make_pool()
        pool.fetchrow.return_value = {"remaining": 0}
        adapter = _make_adapter(pool)

        result = await adapter.all_raids_merged("phase-tid")
        assert result is True

    async def test_false_when_remaining_nonzero(self):
        pool = _make_pool()
        pool.fetchrow.return_value = {"remaining": 2}
        adapter = _make_adapter(pool)

        result = await adapter.all_raids_merged("phase-tid")
        assert result is False

    async def test_false_when_row_is_none(self):
        pool = _make_pool()
        pool.fetchrow.return_value = None
        adapter = _make_adapter(pool)

        result = await adapter.all_raids_merged("phase-tid")
        assert result is False


# ---------------------------------------------------------------------------
# list_phases_for_saga
# ---------------------------------------------------------------------------


class TestListPhasesForSaga:
    async def test_returns_phases(self):
        pool = _make_pool()
        adapter = _make_adapter(pool)
        phase = _make_phase()
        tracker_id = str(phase.id)
        pool.fetch.return_value = [_phase_record(phase, tracker_id)]

        result = await adapter.list_phases_for_saga("saga-tid")

        assert len(result) == 1
        assert result[0].tracker_id == tracker_id

    async def test_empty(self):
        pool = _make_pool()
        pool.fetch.return_value = []
        adapter = _make_adapter(pool)

        result = await adapter.list_phases_for_saga("saga-tid")
        assert result == []


# ---------------------------------------------------------------------------
# update_phase_status
# ---------------------------------------------------------------------------


class TestUpdatePhaseStatus:
    async def test_returns_updated_phase(self):
        pool = _make_pool()
        adapter = _make_adapter(pool)
        phase = _make_phase()
        tracker_id = str(phase.id)
        updated_record = _phase_record(phase, tracker_id)
        updated_record["status"] = PhaseStatus.GATED.value
        pool.fetchrow.return_value = updated_record

        result = await adapter.update_phase_status(tracker_id, PhaseStatus.GATED)

        assert result is not None
        assert result.tracker_id == tracker_id

    async def test_returns_none_when_not_found(self):
        pool = _make_pool()
        pool.fetchrow.return_value = None
        adapter = _make_adapter(pool)

        result = await adapter.update_phase_status("missing-tid", PhaseStatus.GATED)
        assert result is None


# ---------------------------------------------------------------------------
# get_saga_for_raid
# ---------------------------------------------------------------------------


class TestGetSagaForRaid:
    async def test_found(self):
        pool = _make_pool()
        adapter = _make_adapter(pool)
        saga = _make_saga()
        tracker_id = str(saga.id)
        pool.fetchrow.return_value = _saga_record(saga, tracker_id)

        result = await adapter.get_saga_for_raid("raid-tid")

        assert result is not None
        assert result.name == "Test Saga"

    async def test_not_found_returns_none(self):
        pool = _make_pool()
        pool.fetchrow.return_value = None
        adapter = _make_adapter(pool)

        result = await adapter.get_saga_for_raid("raid-tid")
        assert result is None


# ---------------------------------------------------------------------------
# get_phase_for_raid
# ---------------------------------------------------------------------------


class TestGetPhaseForRaid:
    async def test_found(self):
        pool = _make_pool()
        adapter = _make_adapter(pool)
        phase = _make_phase()
        tracker_id = str(phase.id)
        pool.fetchrow.return_value = _phase_record(phase, tracker_id)

        result = await adapter.get_phase_for_raid("raid-tid")

        assert result is not None
        assert result.name == "Phase 1"

    async def test_not_found_returns_none(self):
        pool = _make_pool()
        pool.fetchrow.return_value = None
        adapter = _make_adapter(pool)

        result = await adapter.get_phase_for_raid("raid-tid")
        assert result is None


# ---------------------------------------------------------------------------
# get_owner_for_raid
# ---------------------------------------------------------------------------


class TestGetOwnerForRaid:
    async def test_found(self):
        pool = _make_pool()
        pool.fetchrow.return_value = {"owner_id": "owner-42"}
        adapter = _make_adapter(pool)

        result = await adapter.get_owner_for_raid("raid-tid")
        assert result == "owner-42"

    async def test_not_found_returns_none(self):
        pool = _make_pool()
        pool.fetchrow.return_value = None
        adapter = _make_adapter(pool)

        result = await adapter.get_owner_for_raid("raid-tid")
        assert result is None

    async def test_null_owner_id_returns_none(self):
        pool = _make_pool()
        pool.fetchrow.return_value = {"owner_id": None}
        adapter = _make_adapter(pool)

        result = await adapter.get_owner_for_raid("raid-tid")
        assert result is None


# ---------------------------------------------------------------------------
# save_session_message
# ---------------------------------------------------------------------------


class TestSaveSessionMessage:
    async def test_inserts_message(self):
        pool = _make_pool()
        adapter = _make_adapter(pool)
        raid_id = uuid4()
        message = SessionMessage(
            id=uuid4(),
            raid_id=raid_id,
            session_id="sess-1",
            content="hello",
            sender="user",
            created_at=NOW,
        )

        await adapter.save_session_message(message)

        pool.execute.assert_called_once()
        sql = pool.execute.call_args[0][0]
        assert "INSERT INTO session_messages" in sql


# ---------------------------------------------------------------------------
# get_session_messages
# ---------------------------------------------------------------------------


class TestGetSessionMessages:
    async def test_returns_messages(self):
        pool = _make_pool()
        adapter = _make_adapter(pool)
        msg_id = uuid4()
        raid_id = uuid4()
        pool.fetch.return_value = [
            {
                "id": msg_id,
                "raid_id": raid_id,
                "session_id": "sess-1",
                "content": "hello",
                "sender": "user",
                "created_at": NOW,
            }
        ]

        result = await adapter.get_session_messages("tracker-1")

        assert len(result) == 1
        assert result[0].content == "hello"
        assert result[0].sender == "user"
        assert result[0].session_id == "sess-1"

    async def test_empty(self):
        pool = _make_pool()
        pool.fetch.return_value = []
        adapter = _make_adapter(pool)

        result = await adapter.get_session_messages("tracker-1")
        assert result == []
