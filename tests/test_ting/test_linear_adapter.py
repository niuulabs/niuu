"""Tests for LinearTrackerAdapter."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from niuu.adapters.linear import GraphQLError
from ting.adapters.linear import (
    _LINEAR_TO_RUN,
    _RUN_TO_LINEAR,
    LinearTrackerAdapter,
    _parse_progress,
)
from ting.domain.models import (
    ConfidenceEvent,
    ConfidenceEventType,
    Phase,
    PhaseStatus,
    Run,
    RunStatus,
    Saga,
    SagaStatus,
    SessionMessage,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter() -> LinearTrackerAdapter:
    adapter = LinearTrackerAdapter(
        api_key="test",
        team_id="team-1",
        api_url="https://test.linear.app/graphql",
    )
    return adapter


def _make_adapter_with_pool() -> tuple[LinearTrackerAdapter, AsyncMock]:
    pool = AsyncMock()
    adapter = LinearTrackerAdapter(
        api_key="test",
        team_id="team-1",
        api_url="https://test.linear.app/graphql",
        pool=pool,
    )
    return adapter, pool


def _mock_response(json_data: dict, status_code: int = 200):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.headers = {}
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}",
            request=MagicMock(spec=httpx.Request),
            response=resp,
        )
    else:
        resp.raise_for_status = MagicMock()
    return resp


def _project_node(**overrides) -> dict:
    defaults = {
        "id": "proj-1",
        "name": "Test Project",
        "description": "A test project",
        "state": "started",
        "url": "https://linear.app/test/project/proj-1",
        "projectMilestones": {"nodes": [{"id": "ms-1"}, {"id": "ms-2"}]},
        "issues": {"nodes": [{"id": "i-1"}, {"id": "i-2"}, {"id": "i-3"}]},
    }
    defaults.update(overrides)
    return defaults


def _milestone_node(**overrides) -> dict:
    defaults = {
        "id": "ms-1",
        "name": "Phase 1",
        "description": "First phase",
        "sortOrder": 1,
        "progress": 0.5,
    }
    defaults.update(overrides)
    return defaults


def _issue_node(**overrides) -> dict:
    defaults = {
        "id": "issue-1",
        "identifier": "TEST-1",
        "title": "Test Issue",
        "description": "A test issue",
        "state": {"name": "Todo"},
        "assignee": {"name": "Dev"},
        "labels": {"nodes": [{"name": "bug"}]},
        "priority": 2,
        "url": "https://linear.app/test/issue/TEST-1",
        "projectMilestone": {"id": "ms-1"},
    }
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# list_projects
# ---------------------------------------------------------------------------


class TestListProjects:
    async def test_happy_path(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response(
            {"data": {"projects": {"nodes": [_project_node()]}}}
        )

        projects = await adapter.list_projects()

        assert len(projects) == 1
        assert projects[0].id == "proj-1"
        assert projects[0].name == "Test Project"
        assert projects[0].milestone_count == 2
        assert projects[0].issue_count == 3

    async def test_empty(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response(
            {"data": {"projects": {"nodes": []}}}
        )

        projects = await adapter.list_projects()
        assert projects == []

    async def test_cached(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response(
            {"data": {"projects": {"nodes": [_project_node()]}}}
        )

        first = await adapter.list_projects()
        second = await adapter.list_projects()

        assert first == second
        assert adapter._gql._client.post.call_count == 1


# ---------------------------------------------------------------------------
# get_project
# ---------------------------------------------------------------------------


class TestGetProject:
    async def test_found(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response(
            {"data": {"project": _project_node()}}
        )

        project = await adapter.get_project("proj-1")
        assert project.id == "proj-1"
        assert project.name == "Test Project"

    async def test_not_found(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response({"data": {"project": None}})

        with pytest.raises(GraphQLError, match="Project not found"):
            await adapter.get_project("nonexistent")


# ---------------------------------------------------------------------------
# list_milestones
# ---------------------------------------------------------------------------


class TestListMilestones:
    async def test_happy_path(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response(
            {
                "data": {
                    "project": {
                        "projectMilestones": {
                            "nodes": [_milestone_node(), _milestone_node(id="ms-2", name="Phase 2")]
                        }
                    }
                }
            }
        )

        milestones = await adapter.list_milestones("proj-1")
        assert len(milestones) == 2
        assert milestones[0].name == "Phase 1"
        assert milestones[0].project_id == "proj-1"

    async def test_empty(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response(
            {"data": {"project": {"projectMilestones": {"nodes": []}}}}
        )

        milestones = await adapter.list_milestones("proj-1")
        assert milestones == []


# ---------------------------------------------------------------------------
# list_issues
# ---------------------------------------------------------------------------


class TestListIssues:
    async def test_all_issues(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response(
            {"data": {"issues": {"nodes": [_issue_node(), _issue_node(id="issue-2")]}}}
        )

        issues = await adapter.list_issues("proj-1")
        assert len(issues) == 2
        assert issues[0].id == "issue-1"

    async def test_filtered_by_milestone(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response(
            {"data": {"issues": {"nodes": [_issue_node()]}}}
        )

        issues = await adapter.list_issues("proj-1", milestone_id="ms-1")
        assert len(issues) == 1

        call_kwargs = adapter._gql._client.post.call_args
        payload = call_kwargs[1]["json"]
        assert "milestoneId" in payload["variables"]

    async def test_cached(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response(
            {"data": {"issues": {"nodes": [_issue_node()]}}}
        )

        first = await adapter.list_issues("proj-1")
        second = await adapter.list_issues("proj-1")

        assert first == second
        assert adapter._gql._client.post.call_count == 1


# ---------------------------------------------------------------------------
# create_saga
# ---------------------------------------------------------------------------


class TestCreateSaga:
    async def test_success(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response(
            {"data": {"projectCreate": {"project": {"id": "new-proj"}, "success": True}}}
        )

        now = datetime.now(UTC)
        saga = Saga(
            id=uuid4(),
            tracker_id="",
            tracker_type="linear",
            slug="test-saga",
            name="Test Saga",
            repos=["org/repo"],
            feature_branch="feat/test",
            status=SagaStatus.ACTIVE,
            confidence=0.0,
            created_at=now,
            base_branch="dev",
        )

        result = await adapter.create_saga(saga)
        assert result == "new-proj"

    async def test_failure(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response(
            {"data": {"projectCreate": {"project": None, "success": False}}}
        )

        now = datetime.now(UTC)
        saga = Saga(
            id=uuid4(),
            tracker_id="",
            tracker_type="linear",
            slug="fail",
            name="Fail",
            repos=[],
            feature_branch="feat/test",
            status=SagaStatus.ACTIVE,
            confidence=0.0,
            created_at=now,
            base_branch="dev",
        )

        with pytest.raises(GraphQLError, match="Failed to create Linear project"):
            await adapter.create_saga(saga)


# ---------------------------------------------------------------------------
# create_phase
# ---------------------------------------------------------------------------


class TestCreatePhase:
    async def test_success(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response(
            {
                "data": {
                    "projectMilestoneCreate": {
                        "projectMilestone": {"id": "new-ms"},
                        "success": True,
                    }
                }
            }
        )

        phase = Phase(
            id=uuid4(),
            saga_id=uuid4(),
            tracker_id="proj-1",
            number=1,
            name="Phase 1",
            status=PhaseStatus.PENDING,
            confidence=0.0,
        )

        result = await adapter.create_phase(phase)
        assert result == "new-ms"


# ---------------------------------------------------------------------------
# create_run
# ---------------------------------------------------------------------------


class TestCreateRun:
    async def test_success(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response(
            {
                "data": {
                    "issueCreate": {
                        "issue": {"id": "new-issue", "identifier": "TEST-99"},
                        "success": True,
                    }
                }
            }
        )

        now = datetime.now(UTC)
        run = Run(
            id=uuid4(),
            phase_id=uuid4(),
            tracker_id="proj-1",
            name="Test Run",
            description="Do the thing",
            acceptance_criteria=[],
            declared_files=[],
            estimate_hours=None,
            status=RunStatus.PENDING,
            confidence=0.0,
            session_id=None,
            branch=None,
            chronicle_summary=None,
            pr_url=None,
            pr_id=None,
            retry_count=0,
            created_at=now,
            updated_at=now,
        )

        result = await adapter.create_run(run)
        assert result == "new-issue"

    async def test_with_acceptance_criteria_and_declared_files(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response(
            {
                "data": {
                    "issueCreate": {
                        "issue": {"id": "new-issue", "identifier": "TEST-100"},
                        "success": True,
                    }
                }
            }
        )

        now = datetime.now(UTC)
        run = Run(
            id=uuid4(),
            phase_id=uuid4(),
            tracker_id="proj-1",
            name="Run with extras",
            description="Base description",
            acceptance_criteria=["Tests pass", "No lint errors"],
            declared_files=["src/main.py", "tests/test_main.py"],
            estimate_hours=2.0,
            status=RunStatus.PENDING,
            confidence=0.0,
            session_id=None,
            branch=None,
            chronicle_summary=None,
            pr_url=None,
            pr_id=None,
            retry_count=0,
            created_at=now,
            updated_at=now,
        )

        result = await adapter.create_run(run)
        assert result == "new-issue"

        call_kwargs = adapter._gql._client.post.call_args
        payload = call_kwargs[1]["json"]
        desc = payload["variables"]["description"]
        assert "## Acceptance Criteria" in desc
        assert "- [ ] Tests pass" in desc
        assert "## Declared Files" in desc
        assert "- `src/main.py`" in desc


# ---------------------------------------------------------------------------
# update_run_state
# ---------------------------------------------------------------------------


class TestUpdateRunState:
    async def test_state_mapping(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()

        # Call 1: get issue team, Call 2: get team states, Call 3: update
        adapter._gql._client.post.side_effect = [
            _mock_response({"data": {"issue": {"team": {"id": "team-1"}}}}),
            _mock_response(
                {
                    "data": {
                        "team": {
                            "states": {
                                "nodes": [
                                    {"id": "s1", "name": "Todo"},
                                    {"id": "s2", "name": "In Progress"},
                                    {"id": "s3", "name": "Done"},
                                ]
                            }
                        }
                    }
                }
            ),
            _mock_response(
                {
                    "data": {
                        "issueUpdate": {
                            "issue": {"id": "i-1", "state": {"name": "In Progress"}},
                            "success": True,
                        }
                    }
                }
            ),
        ]

        await adapter.update_run_state("i-1", RunStatus.RUNNING)
        assert adapter._gql._client.post.call_count == 3

    async def test_state_resolution_not_found(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()

        adapter._gql._client.post.side_effect = [
            _mock_response({"data": {"issue": {"team": {"id": "team-1"}}}}),
            _mock_response(
                {
                    "data": {
                        "team": {
                            "states": {
                                "nodes": [
                                    {"id": "s1", "name": "Todo"},
                                ]
                            }
                        }
                    }
                }
            ),
        ]

        with pytest.raises(GraphQLError, match="State 'In Progress' not found"):
            await adapter.update_run_state("i-1", RunStatus.RUNNING)


# ---------------------------------------------------------------------------
# close_run
# ---------------------------------------------------------------------------


class TestCloseRun:
    async def test_close(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()

        adapter._gql._client.post.side_effect = [
            _mock_response({"data": {"issue": {"team": {"id": "team-1"}}}}),
            _mock_response(
                {
                    "data": {
                        "team": {
                            "states": {
                                "nodes": [
                                    {"id": "s1", "name": "Todo"},
                                    {"id": "s3", "name": "Done"},
                                ]
                            }
                        }
                    }
                }
            ),
            _mock_response(
                {
                    "data": {
                        "issueUpdate": {
                            "issue": {"id": "i-1", "state": {"name": "Done"}},
                            "success": True,
                        }
                    }
                }
            ),
        ]

        await adapter.close_run("i-1")
        assert adapter._gql._client.post.call_count == 3


# ---------------------------------------------------------------------------
# get_saga, get_phase, get_run
# ---------------------------------------------------------------------------


class TestGetSaga:
    async def test_found(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response(
            {"data": {"project": _project_node()}}
        )

        saga = await adapter.get_saga("proj-1")
        assert saga.tracker_id == "proj-1"
        assert saga.name == "Test Project"
        assert saga.status == SagaStatus.ACTIVE

    async def test_not_found(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response({"data": {"project": None}})

        with pytest.raises(GraphQLError, match="Project not found"):
            await adapter.get_saga("bad-id")


class TestGetPhase:
    async def test_found(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response(
            {
                "data": {
                    "projectMilestone": {
                        "id": "ms-1",
                        "name": "Phase 1",
                        "description": "desc",
                        "sortOrder": 1,
                        "progress": 0.5,
                        "project": {"id": "proj-1"},
                    }
                }
            }
        )

        phase = await adapter.get_phase("ms-1")
        assert phase.tracker_id == "ms-1"
        assert phase.name == "Phase 1"

    async def test_not_found(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response(
            {"data": {"projectMilestone": None}}
        )

        with pytest.raises(GraphQLError, match="Milestone not found"):
            await adapter.get_phase("bad-id")


class TestGetRun:
    async def test_found(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response({"data": {"issue": _issue_node()}})

        run = await adapter.get_run("issue-1")
        assert run.tracker_id == "issue-1"
        assert run.name == "Test Issue"
        assert run.status == RunStatus.PENDING  # "Todo" maps to PENDING

    async def test_not_found(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response({"data": {"issue": None}})

        with pytest.raises(GraphQLError, match="Issue not found"):
            await adapter.get_run("bad-id")


# ---------------------------------------------------------------------------
# _parse_progress
# ---------------------------------------------------------------------------


class TestParseProgress:
    def test_none(self):
        assert _parse_progress(None) == 0.0

    def test_float(self):
        assert _parse_progress(0.75) == 0.0075

    def test_int(self):
        assert _parse_progress(1) == 0.01

    def test_percentage_string(self):
        assert _parse_progress("50%") == 0.5

    def test_unknown_type(self):
        assert _parse_progress([1, 2]) == 0.0


# ---------------------------------------------------------------------------
# State mapping tables
# ---------------------------------------------------------------------------


class TestStateMappings:
    def test_run_to_linear_completeness(self):
        """Every RunStatus should have a mapping."""
        for status in RunStatus:
            assert status in _RUN_TO_LINEAR

    def test_linear_to_run_known_states(self):
        expected = {"Backlog", "Todo", "In Progress", "In Review", "Done", "Canceled"}
        assert set(_LINEAR_TO_RUN.keys()) == expected

    def test_round_trip_running(self):
        linear_name = _RUN_TO_LINEAR[RunStatus.RUNNING]
        assert _LINEAR_TO_RUN[linear_name] == RunStatus.RUNNING

    def test_round_trip_merged(self):
        linear_name = _RUN_TO_LINEAR[RunStatus.MERGED]
        assert _LINEAR_TO_RUN[linear_name] == RunStatus.MERGED


# ---------------------------------------------------------------------------
# update_run_progress
# ---------------------------------------------------------------------------


class TestUpdateRunProgress:
    async def test_raises_when_pool_is_none(self):
        adapter = _make_adapter()  # no pool
        with pytest.raises(RuntimeError, match="pool is required"):
            await adapter.update_run_progress("t-1", status=RunStatus.REVIEW)

    async def test_executes_upsert_and_returns_run(self):
        adapter, pool = _make_adapter_with_pool()
        adapter._gql._client = AsyncMock()
        pool.fetchrow.return_value = None  # _fetch_progress returns None

        # get_run GQL call
        adapter._gql._client.post.return_value = _mock_response(
            {"data": {"issue": _issue_node(id="t-1")}}
        )

        result = await adapter.update_run_progress("t-1", status=RunStatus.REVIEW)

        pool.execute.assert_called_once()
        sql = pool.execute.call_args[0][0]
        assert "INSERT INTO run_progress" in sql
        assert "chronicle_summary" in sql
        assert result.tracker_id == "t-1"

    async def test_status_sync_failure_is_logged_not_raised(self):
        """update_run_state error should be swallowed, not bubble up."""
        adapter, pool = _make_adapter_with_pool()
        adapter._gql._client = AsyncMock()
        pool.fetchrow.return_value = None

        # First GQL call (get_run → _fetch_progress) returns None;
        # update_run_state calls also go through _gql but raise
        adapter._gql._client.post.side_effect = [
            # update_run_state: get team id
            _mock_response({"data": {"issue": {"team": {"id": "team-1"}}}}),
            # update_run_state: get team states — fails
            _mock_response({"errors": [{"message": "boom"}]}, status_code=200),
            # get_run GQL call after the failure
            _mock_response({"data": {"issue": _issue_node(id="t-1")}}),
        ]
        pool.fetchrow.return_value = None

        # Should not raise even though update_run_state fails
        result = await adapter.update_run_progress("t-1", status=RunStatus.RUNNING)
        assert result.tracker_id == "t-1"

    async def test_no_status_skips_linear_sync(self):
        """When status is None, update_run_state is not called."""
        adapter, pool = _make_adapter_with_pool()
        adapter._gql._client = AsyncMock()
        pool.fetchrow.return_value = None
        adapter._gql._client.post.return_value = _mock_response(
            {"data": {"issue": _issue_node(id="t-1")}}
        )

        result = await adapter.update_run_progress("t-1", confidence=0.9)

        # Only the get_run GQL call (no update_run_state calls)
        assert adapter._gql._client.post.call_count == 1
        assert result.tracker_id == "t-1"


# ---------------------------------------------------------------------------
# _get_team_id discovery path
# ---------------------------------------------------------------------------


class TestGetTeamId:
    async def test_discovery_when_no_team_id_configured(self):
        adapter = LinearTrackerAdapter(
            api_key="test",
            api_url="https://test.linear.app/graphql",
        )
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response(
            {"data": {"teams": {"nodes": [{"id": "discovered-team"}]}}}
        )

        result = await adapter._get_team_id()

        assert result == "discovered-team"
        assert adapter._team_id == "discovered-team"

    async def test_raises_when_no_teams_found(self):
        adapter = LinearTrackerAdapter(
            api_key="test",
            api_url="https://test.linear.app/graphql",
        )
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response({"data": {"teams": {"nodes": []}}})

        from niuu.adapters.linear import GraphQLError as GQLError

        with pytest.raises(GQLError, match="No Linear teams accessible"):
            await adapter._get_team_id()


# ---------------------------------------------------------------------------
# create_phase failure
# ---------------------------------------------------------------------------


class TestCreatePhaseFailure:
    async def test_raises_when_milestone_null(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response(
            {"data": {"projectMilestoneCreate": {"projectMilestone": None, "success": False}}}
        )

        phase = Phase(
            id=uuid4(),
            saga_id=uuid4(),
            tracker_id="proj-1",
            number=1,
            name="Phase Fail",
            status=PhaseStatus.PENDING,
            confidence=0.0,
        )

        with pytest.raises(GraphQLError, match="Failed to create Linear milestone"):
            await adapter.create_phase(phase)


# ---------------------------------------------------------------------------
# create_run with confidence and failure
# ---------------------------------------------------------------------------


class TestCreateRunExtended:
    async def test_includes_confidence_in_description(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response(
            {"data": {"issueCreate": {"issue": {"id": "new-issue"}, "success": True}}}
        )
        now = datetime.now(UTC)
        run = Run(
            id=uuid4(),
            phase_id=uuid4(),
            tracker_id="proj-1",
            name="Run",
            description="desc",
            acceptance_criteria=[],
            declared_files=[],
            estimate_hours=None,
            status=RunStatus.PENDING,
            confidence=0.75,
            session_id=None,
            branch=None,
            chronicle_summary=None,
            pr_url=None,
            pr_id=None,
            retry_count=0,
            created_at=now,
            updated_at=now,
        )

        await adapter.create_run(run)

        payload = adapter._gql._client.post.call_args[1]["json"]
        assert "75%" in payload["variables"]["description"]

    async def test_raises_when_issue_null(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response(
            {"data": {"issueCreate": {"issue": None, "success": False}}}
        )
        now = datetime.now(UTC)
        run = Run(
            id=uuid4(),
            phase_id=uuid4(),
            tracker_id="proj-1",
            name="Run",
            description="desc",
            acceptance_criteria=[],
            declared_files=[],
            estimate_hours=None,
            status=RunStatus.PENDING,
            confidence=0.0,
            session_id=None,
            branch=None,
            chronicle_summary=None,
            pr_url=None,
            pr_id=None,
            retry_count=0,
            created_at=now,
            updated_at=now,
        )

        with pytest.raises(GraphQLError, match="Failed to create Linear issue"):
            await adapter.create_run(run)


# ---------------------------------------------------------------------------
# update_run_state: invalid mapping
# ---------------------------------------------------------------------------


class TestUpdateRunStateInvalid:
    async def test_raises_for_unmapped_status(self):
        adapter = _make_adapter()
        with pytest.raises(ValueError, match="No Linear state mapping"):
            await adapter.update_run_state("i-1", "UNMAPPED_STATUS")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _resolve_state_id: issue not found
# ---------------------------------------------------------------------------


class TestResolveStateIdIssueNotFound:
    async def test_raises_when_issue_node_is_null(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response({"data": {"issue": None}})

        with pytest.raises(GraphQLError, match="Issue not found"):
            await adapter._resolve_state_id("bad-id", "In Progress")


# ---------------------------------------------------------------------------
# list_pending_runs
# ---------------------------------------------------------------------------


class TestListPendingRuns:
    async def test_returns_pending_and_queued(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response(
            {
                "data": {
                    "issues": {
                        "nodes": [
                            _issue_node(id="i-1", state={"name": "Todo"}),
                            _issue_node(id="i-2", state={"name": "Backlog"}),
                            _issue_node(id="i-3", state={"name": "Done"}),
                        ]
                    }
                }
            }
        )

        result = await adapter.list_pending_runs("ms-1")

        # Done maps to MERGED, which is not PENDING/QUEUED — filtered out
        assert len(result) == 2
        assert all(r.status in (RunStatus.PENDING, RunStatus.QUEUED) for r in result)

    async def test_empty(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response({"data": {"issues": {"nodes": []}}})

        result = await adapter.list_pending_runs("ms-1")
        assert result == []


# ---------------------------------------------------------------------------
# list_milestones: cached path
# ---------------------------------------------------------------------------


class TestListMilestonesCached:
    async def test_cache_hit_skips_second_gql_call(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response(
            {"data": {"project": {"projectMilestones": {"nodes": [_milestone_node()]}}}}
        )

        first = await adapter.list_milestones("proj-1")
        second = await adapter.list_milestones("proj-1")

        assert first == second
        assert adapter._gql._client.post.call_count == 1  # cached on second call


# ---------------------------------------------------------------------------
# get_project_full
# ---------------------------------------------------------------------------


class TestGetProjectFull:
    async def test_happy_path(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response(
            {
                "data": {
                    "project": {
                        "id": "proj-1",
                        "name": "Full Project",
                        "description": "desc",
                        "state": "started",
                        "url": "https://linear.app/proj",
                        "slugId": "abc",
                        "issueCount": {"nodes": []},
                        "projectMilestones": {"nodes": [_milestone_node()]},
                        "issuesFull": {"nodes": [_issue_node()]},
                    }
                }
            }
        )

        project, milestones, issues = await adapter.get_project_full("proj-1")

        assert project.id == "proj-1"
        assert len(milestones) == 1
        assert len(issues) == 1

    async def test_raises_when_project_not_found(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response({"data": {"project": None}})

        with pytest.raises(GraphQLError, match="Project not found"):
            await adapter.get_project_full("bad-id")


# ---------------------------------------------------------------------------
# get_blocked_identifiers
# ---------------------------------------------------------------------------


class TestGetBlockedIdentifiers:
    async def test_returns_blocked_set(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response(
            {
                "data": {
                    "issues": {
                        "nodes": [
                            {
                                "id": "i-1",
                                "state": {"type": "started"},
                                "relations": {
                                    "nodes": [
                                        {
                                            "type": "blocks",
                                            "relatedIssue": {"identifier": "TEST-2"},
                                        }
                                    ]
                                },
                            }
                        ]
                    }
                }
            }
        )

        result = await adapter.get_blocked_identifiers("proj-1")

        assert "TEST-2" in result

    async def test_completed_issues_not_blocking(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response(
            {
                "data": {
                    "issues": {
                        "nodes": [
                            {
                                "id": "i-1",
                                "state": {"type": "completed"},
                                "relations": {
                                    "nodes": [
                                        {
                                            "type": "blocks",
                                            "relatedIssue": {"identifier": "TEST-3"},
                                        }
                                    ]
                                },
                            }
                        ]
                    }
                }
            }
        )

        result = await adapter.get_blocked_identifiers("proj-1")
        assert "TEST-3" not in result
        assert result == set()

    async def test_empty(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response({"data": {"issues": {"nodes": []}}})

        result = await adapter.get_blocked_identifiers("proj-1")
        assert result == set()

    async def test_non_blocks_relation_type_not_counted(self):
        """Relations with type != 'blocks' are ignored (branch 598->597)."""
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response(
            {
                "data": {
                    "issues": {
                        "nodes": [
                            {
                                "id": "i-1",
                                "state": {"type": "started"},
                                "relations": {
                                    "nodes": [
                                        {
                                            "type": "related",
                                            "relatedIssue": {"identifier": "TEST-5"},
                                        }
                                    ]
                                },
                            }
                        ]
                    }
                }
            }
        )

        result = await adapter.get_blocked_identifiers("proj-1")
        assert "TEST-5" not in result
        assert result == set()

    async def test_blocks_relation_with_empty_identifier_not_counted(self):
        """Blocks relations with empty identifier are ignored (branch 600->597)."""
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response(
            {
                "data": {
                    "issues": {
                        "nodes": [
                            {
                                "id": "i-1",
                                "state": {"type": "started"},
                                "relations": {
                                    "nodes": [
                                        {
                                            "type": "blocks",
                                            "relatedIssue": {"identifier": ""},
                                        }
                                    ]
                                },
                            }
                        ]
                    }
                }
            }
        )

        result = await adapter.get_blocked_identifiers("proj-1")
        assert result == set()


# ---------------------------------------------------------------------------
# get_run_by_session
# ---------------------------------------------------------------------------


class TestGetRunBySession:
    async def test_no_pool_returns_none(self):
        adapter = _make_adapter()
        result = await adapter.get_run_by_session("sess-1")
        assert result is None

    async def test_with_pool_not_found_returns_none(self):
        adapter, pool = _make_adapter_with_pool()
        pool.fetchrow.return_value = None

        result = await adapter.get_run_by_session("sess-1")
        assert result is None

    async def test_with_pool_found_returns_run(self):
        adapter, pool = _make_adapter_with_pool()
        adapter._gql._client = AsyncMock()
        pool.fetchrow.side_effect = [
            {"tracker_id": "issue-1"},  # run_progress lookup
            None,  # _fetch_progress
        ]
        adapter._gql._client.post.return_value = _mock_response(
            {"data": {"issue": _issue_node(id="issue-1")}}
        )

        result = await adapter.get_run_by_session("sess-1")

        assert result is not None
        assert result.tracker_id == "issue-1"


# ---------------------------------------------------------------------------
# list_runs_by_status
# ---------------------------------------------------------------------------


class TestListRunsByStatus:
    async def test_no_pool_returns_empty(self):
        adapter = _make_adapter()
        result = await adapter.list_runs_by_status(RunStatus.PENDING)
        assert result == []

    async def test_with_pool_returns_runs(self):
        adapter, pool = _make_adapter_with_pool()
        adapter._gql._client = AsyncMock()
        pool.fetch.return_value = [{"tracker_id": "issue-1"}]
        pool.fetchrow.return_value = None  # _fetch_progress
        adapter._gql._client.post.return_value = _mock_response(
            {"data": {"issue": _issue_node(id="issue-1")}}
        )

        result = await adapter.list_runs_by_status(RunStatus.PENDING)

        assert len(result) == 1
        assert result[0].tracker_id == "issue-1"

    async def test_with_pool_gql_failure_skips_run(self):
        adapter, pool = _make_adapter_with_pool()
        adapter._gql._client = AsyncMock()
        pool.fetch.return_value = [{"tracker_id": "bad-id"}]
        pool.fetchrow.return_value = None
        adapter._gql._client.post.return_value = _mock_response({"data": {"issue": None}})

        result = await adapter.list_runs_by_status(RunStatus.PENDING)
        assert result == []


# ---------------------------------------------------------------------------
# get_run_by_id
# ---------------------------------------------------------------------------


class TestGetRunById:
    async def test_no_pool_returns_none(self):
        adapter = _make_adapter()
        result = await adapter.get_run_by_id(uuid4())
        assert result is None

    async def test_with_pool_not_found_returns_none(self):
        adapter, pool = _make_adapter_with_pool()
        pool.fetchrow.return_value = None

        result = await adapter.get_run_by_id(uuid4())
        assert result is None

    async def test_with_pool_found(self):
        from uuid import UUID as _UUID
        from uuid import uuid5

        adapter, pool = _make_adapter_with_pool()
        adapter._gql._client = AsyncMock()
        pool.fetch.return_value = [{"tracker_id": "issue-1"}]
        pool.fetchrow.return_value = None  # _fetch_progress
        adapter._gql._client.post.return_value = _mock_response(
            {"data": {"issue": _issue_node(id="issue-1")}}
        )

        run_id = uuid5(_UUID(int=0), "issue-1")
        result = await adapter.get_run_by_id(run_id)

        assert result is not None
        assert result.tracker_id == "issue-1"


# ---------------------------------------------------------------------------
# add_confidence_event
# ---------------------------------------------------------------------------


class TestAddConfidenceEvent:
    async def test_no_pool_raises(self):
        adapter = _make_adapter()
        event = ConfidenceEvent(
            id=uuid4(),
            run_id=uuid4(),
            event_type=ConfidenceEventType.CI_PASS,
            delta=0.05,
            score_after=0.75,
            created_at=datetime.now(UTC),
        )
        with pytest.raises(RuntimeError, match="pool is required for add_confidence_event"):
            await adapter.add_confidence_event("t-1", event)

    async def test_with_pool_inserts_event(self):
        adapter, pool = _make_adapter_with_pool()
        event = ConfidenceEvent(
            id=uuid4(),
            run_id=uuid4(),
            event_type=ConfidenceEventType.CI_PASS,
            delta=0.05,
            score_after=0.75,
            created_at=datetime.now(UTC),
        )

        await adapter.add_confidence_event("t-1", event)

        assert pool.execute.call_count == 2
        insert_sql = pool.execute.call_args_list[0][0][0]
        assert "INSERT INTO run_confidence_events" in insert_sql
        update_sql = pool.execute.call_args_list[1][0][0]
        assert "UPDATE run_progress SET confidence" in update_sql


# ---------------------------------------------------------------------------
# get_confidence_events
# ---------------------------------------------------------------------------


class TestGetConfidenceEvents:
    async def test_no_pool_returns_empty(self):
        adapter = _make_adapter()
        result = await adapter.get_confidence_events("t-1")
        assert result == []

    async def test_with_pool_returns_events(self):
        adapter, pool = _make_adapter_with_pool()
        run_id = uuid4()
        pool.fetch.return_value = [
            {
                "id": uuid4(),
                "run_id": run_id,
                "event_type": "ci_pass",
                "delta": 0.05,
                "score_after": 0.8,
                "created_at": datetime.now(UTC),
            }
        ]

        result = await adapter.get_confidence_events("t-1")

        assert len(result) == 1
        assert result[0].event_type == ConfidenceEventType.CI_PASS


# ---------------------------------------------------------------------------
# all_runs_merged
# ---------------------------------------------------------------------------


class TestAllRunsMerged:
    async def test_no_pool_returns_false(self):
        adapter = _make_adapter()
        result = await adapter.all_runs_merged("phase-tid")
        assert result is False

    async def test_with_pool_all_merged(self):
        adapter, pool = _make_adapter_with_pool()
        pool.fetchrow.return_value = {"remaining": 0}

        result = await adapter.all_runs_merged("phase-tid")
        assert result is True

    async def test_with_pool_some_not_merged(self):
        adapter, pool = _make_adapter_with_pool()
        pool.fetchrow.return_value = {"remaining": 3}

        result = await adapter.all_runs_merged("phase-tid")
        assert result is False

    async def test_with_pool_unassigned_phase_uses_null_phase_rows(self):
        adapter, pool = _make_adapter_with_pool()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response(
            {
                "data": {
                    "issues": {
                        "nodes": [
                            _issue_node(
                                id="i-1",
                                identifier="NIU-1",
                                state={"name": "Done", "type": "completed"},
                                projectMilestone=None,
                            ),
                            _issue_node(
                                id="i-2",
                                identifier="NIU-2",
                                state={"name": "Done", "type": "completed"},
                                projectMilestone=None,
                            ),
                        ]
                    }
                }
            }
        )

        result = await adapter.all_runs_merged("__unassigned__:proj-id")

        assert result is True


# ---------------------------------------------------------------------------
# list_phases_for_saga
# ---------------------------------------------------------------------------


class TestListPhasesForSaga:
    async def test_uses_persisted_phase_rows_when_pool_available(self):
        adapter, pool = _make_adapter_with_pool()
        pool.fetch.return_value = [
            {
                "id": uuid4(),
                "saga_id": uuid4(),
                "tracker_id": "ms-1",
                "number": 1,
                "name": "Phase 1",
                "status": "ACTIVE",
                "confidence": 0.4,
            },
            {
                "id": uuid4(),
                "saga_id": uuid4(),
                "tracker_id": "ms-2",
                "number": 2,
                "name": "Phase 2",
                "status": "GATED",
                "confidence": 0.1,
            },
        ]
        pool.fetchval.return_value = 0

        result = await adapter.list_phases_for_saga("proj-id")

        assert [phase.tracker_id for phase in result] == ["ms-1", "ms-2"]
        assert [phase.status for phase in result] == [PhaseStatus.ACTIVE, PhaseStatus.GATED]

    async def test_returns_phases_from_milestones(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.side_effect = [
            _mock_response(
                {
                    "data": {
                        "project": {
                            "projectMilestones": {
                                "nodes": [_milestone_node(id="ms-1", name="Phase 1")]
                            }
                        }
                    }
                }
            ),
            _mock_response(
                {
                    "data": {
                        "issues": {
                            "nodes": [
                                _issue_node(
                                    id="i-1",
                                    identifier="NIU-1",
                                    projectMilestone={"id": "ms-1"},
                                )
                            ]
                        }
                    }
                }
            ),
        ]

        result = await adapter.list_phases_for_saga("proj-id")

        assert len(result) == 1
        assert result[0].tracker_id == "ms-1"
        assert result[0].name == "Phase 1"

    async def test_empty_milestones(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.side_effect = [
            _mock_response({"data": {"project": {"projectMilestones": {"nodes": []}}}}),
            _mock_response({"data": {"issues": {"nodes": []}}}),
        ]

        result = await adapter.list_phases_for_saga("proj-id")
        assert result == []

    async def test_appends_unassigned_phase_for_tracker_backed_unassigned_issues(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.side_effect = [
            _mock_response(
                {
                    "data": {
                        "project": {
                            "projectMilestones": {
                                "nodes": [_milestone_node(id="ms-1", name="Phase 1")]
                            }
                        }
                    }
                }
            ),
            _mock_response(
                {
                    "data": {
                        "issues": {
                            "nodes": [
                                _issue_node(
                                    id="i-1",
                                    identifier="NIU-1",
                                    projectMilestone={"id": "ms-1"},
                                ),
                                _issue_node(
                                    id="i-2",
                                    identifier="NIU-2",
                                    projectMilestone=None,
                                ),
                            ]
                        }
                    }
                }
            ),
        ]

        result = await adapter.list_phases_for_saga("proj-id")

        assert [phase.name for phase in result] == ["Phase 1", "Unassigned"]
        assert result[-1].tracker_id == "__unassigned__:proj-id"

    async def test_appends_unassigned_phase_to_persisted_phases_when_progress_rows_exist(self):
        adapter, pool = _make_adapter_with_pool()
        pool.fetch.return_value = [
            {
                "id": uuid4(),
                "saga_id": uuid4(),
                "tracker_id": "ms-1",
                "number": 1,
                "name": "Phase 1",
                "status": "ACTIVE",
                "confidence": 0.4,
            }
        ]
        pool.fetchval.return_value = 1

        result = await adapter.list_phases_for_saga("proj-id")

        assert [phase.name for phase in result] == ["Phase 1", "Unassigned"]
        assert result[-1].tracker_id == "__unassigned__:proj-id"


# ---------------------------------------------------------------------------
# update_phase_status (no-op)
# ---------------------------------------------------------------------------


class TestUpdatePhaseStatus:
    async def test_returns_none_without_pool(self):
        adapter = _make_adapter()
        result = await adapter.update_phase_status("phase-tid", PhaseStatus.GATED)
        assert result is None

    async def test_updates_persisted_phase_status_with_pool(self):
        adapter, pool = _make_adapter_with_pool()
        phase_id = uuid4()
        saga_id = uuid4()
        pool.fetchrow.return_value = {
            "id": phase_id,
            "saga_id": saga_id,
            "tracker_id": "phase-tid",
            "number": 2,
            "name": "Phase 2",
            "status": "ACTIVE",
            "confidence": 0.25,
        }

        result = await adapter.update_phase_status("phase-tid", PhaseStatus.ACTIVE)

        assert result is not None
        assert result.id == phase_id
        assert result.saga_id == saga_id
        assert result.tracker_id == "phase-tid"
        assert result.status == PhaseStatus.ACTIVE


# ---------------------------------------------------------------------------
# get_saga_for_run
# ---------------------------------------------------------------------------


class TestGetSagaForRun:
    async def test_no_pool_raises(self):
        adapter = _make_adapter()
        with pytest.raises(RuntimeError, match="Database pool not configured"):
            await adapter.get_saga_for_run("t-1")

    async def test_with_pool_not_found_returns_none(self):
        adapter, pool = _make_adapter_with_pool()
        pool.fetchrow.side_effect = [None, None]

        result = await adapter.get_saga_for_run("t-1")
        assert result is None

    async def test_with_pool_empty_saga_tracker_id_returns_none(self):
        adapter, pool = _make_adapter_with_pool()
        pool.fetchrow.side_effect = [None, {"saga_tracker_id": None}]

        result = await adapter.get_saga_for_run("t-1")
        assert result is None

    async def test_with_pool_falls_back_to_tracker_project_lookup(self):
        adapter, pool = _make_adapter_with_pool()
        adapter._gql._client = AsyncMock()
        pool.fetchrow.side_effect = [None, {"saga_tracker_id": "proj-1"}]
        adapter._gql._client.post.return_value = _mock_response(
            {"data": {"project": _project_node()}}
        )

        result = await adapter.get_saga_for_run("t-1")

        assert result is not None
        assert result.tracker_id == "proj-1"

    async def test_with_pool_returns_persisted_saga_row_when_available(self):
        adapter, pool = _make_adapter_with_pool()
        saga_id = uuid4()
        created_at = datetime.now(UTC)
        pool.fetchrow.return_value = {
            "id": saga_id,
            "tracker_id": "proj-1",
            "tracker_type": "linear",
            "slug": "proof-import",
            "name": "Proof Import",
            "repos": ["niuulabs/volundr"],
            "feature_branch": "feat/proof-import",
            "base_branch": "dev",
            "status": "ACTIVE",
            "confidence": 0.0,
            "created_at": created_at,
            "owner_id": "dev-user",
            "workflow_id": None,
            "workflow_version": None,
            "workflow_snapshot": None,
        }

        result = await adapter.get_saga_for_run("t-1")

        assert result is not None
        assert result.id == saga_id
        assert result.owner_id == "dev-user"
        assert result.base_branch == "dev"


# ---------------------------------------------------------------------------
# get_phase_for_run
# ---------------------------------------------------------------------------


class TestGetPhaseForRun:
    async def test_no_pool_returns_none(self):
        adapter = _make_adapter()
        result = await adapter.get_phase_for_run("t-1")
        assert result is None

    async def test_with_pool_not_found_returns_none(self):
        adapter, pool = _make_adapter_with_pool()
        pool.fetchrow.return_value = None

        result = await adapter.get_phase_for_run("t-1")
        assert result is None

    async def test_with_pool_empty_phase_tracker_id_returns_synthetic_unassigned_phase(self):
        adapter, pool = _make_adapter_with_pool()
        pool.fetchrow.return_value = {"phase_tracker_id": "", "saga_tracker_id": "proj-1"}

        result = await adapter.get_phase_for_run("t-1")
        assert result is not None
        assert result.name == "Unassigned"
        assert result.tracker_id == "__unassigned__:proj-1"

    async def test_with_pool_found(self):
        adapter, pool = _make_adapter_with_pool()
        adapter._gql._client = AsyncMock()
        pool.fetchrow.return_value = {"phase_tracker_id": "ms-1"}
        adapter._gql._client.post.return_value = _mock_response(
            {
                "data": {
                    "projectMilestone": {
                        "id": "ms-1",
                        "name": "Phase 1",
                        "sortOrder": 1,
                    }
                }
            }
        )

        result = await adapter.get_phase_for_run("t-1")

        assert result is not None
        assert result.tracker_id == "ms-1"


# ---------------------------------------------------------------------------
# get_owner_for_run
# ---------------------------------------------------------------------------


class TestGetOwnerForRun:
    async def test_no_pool_returns_none(self):
        adapter = _make_adapter()
        result = await adapter.get_owner_for_run("t-1")
        assert result is None

    async def test_with_pool_not_found_returns_none(self):
        adapter, pool = _make_adapter_with_pool()
        pool.fetchrow.return_value = None

        result = await adapter.get_owner_for_run("t-1")
        assert result is None

    async def test_with_pool_null_owner_returns_none(self):
        adapter, pool = _make_adapter_with_pool()
        pool.fetchrow.return_value = {"owner_id": None}

        result = await adapter.get_owner_for_run("t-1")
        assert result is None

    async def test_with_pool_found(self):
        adapter, pool = _make_adapter_with_pool()
        pool.fetchrow.return_value = {"owner_id": "owner-99"}

        result = await adapter.get_owner_for_run("t-1")
        assert result == "owner-99"


# ---------------------------------------------------------------------------
# save_session_message
# ---------------------------------------------------------------------------


class TestSaveSessionMessage:
    async def test_no_pool_raises(self):
        adapter = _make_adapter()
        message = SessionMessage(
            id=uuid4(),
            run_id=uuid4(),
            session_id="sess-1",
            content="hi",
            sender="user",
            created_at=datetime.now(UTC),
        )
        with pytest.raises(RuntimeError, match="pool is required for save_session_message"):
            await adapter.save_session_message(message)

    async def test_with_pool_inserts_message(self):
        adapter, pool = _make_adapter_with_pool()
        run_id = uuid4()
        message = SessionMessage(
            id=uuid4(),
            run_id=run_id,
            session_id="sess-1",
            content="hello",
            sender="user",
            created_at=datetime.now(UTC),
        )
        pool.fetchrow.return_value = {"tracker_id": "t-1"}

        await adapter.save_session_message(message)

        pool.execute.assert_called_once()
        sql = pool.execute.call_args[0][0]
        assert "INSERT INTO run_session_messages" in sql

    async def test_with_pool_no_progress_row_uses_run_id(self):
        adapter, pool = _make_adapter_with_pool()
        run_id = uuid4()
        message = SessionMessage(
            id=uuid4(),
            run_id=run_id,
            session_id="sess-1",
            content="hello",
            sender="user",
            created_at=datetime.now(UTC),
        )
        pool.fetchrow.return_value = None  # No progress row

        await adapter.save_session_message(message)

        pool.execute.assert_called_once()


# ---------------------------------------------------------------------------
# get_session_messages
# ---------------------------------------------------------------------------


class TestGetSessionMessages:
    async def test_no_pool_returns_empty(self):
        adapter = _make_adapter()
        result = await adapter.get_session_messages("t-1")
        assert result == []

    async def test_with_pool_returns_messages(self):
        adapter, pool = _make_adapter_with_pool()
        msg_id = uuid4()
        run_id = uuid4()
        pool.fetch.return_value = [
            {
                "id": msg_id,
                "run_id": run_id,
                "session_id": "sess-1",
                "content": "hello",
                "sender": "user",
                "created_at": datetime.now(UTC),
            }
        ]

        result = await adapter.get_session_messages("t-1")

        assert len(result) == 1
        assert result[0].content == "hello"

    async def test_with_pool_empty(self):
        adapter, pool = _make_adapter_with_pool()
        pool.fetch.return_value = []

        result = await adapter.get_session_messages("t-1")
        assert result == []


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------


class TestClose:
    async def test_close_calls_gql_close(self):
        adapter = _make_adapter()
        adapter._gql.close = AsyncMock()

        await adapter.close()

        adapter._gql.close.assert_called_once()


# ---------------------------------------------------------------------------
# _node_to_tracker_project: edge cases
# ---------------------------------------------------------------------------


class TestNodeToTrackerProject:
    def test_progress_from_node_when_no_milestones(self):
        node = {
            "id": "proj-1",
            "name": "Project",
            "description": "desc",
            "state": "started",
            "url": "https://linear.app/proj",
            "projectMilestones": {"nodes": []},
            "issues": {"nodes": []},
            "progress": 50.0,
        }
        project = LinearTrackerAdapter._node_to_tracker_project(node)
        # progress=50.0 → 50.0/100 = 0.5
        assert project.progress == 0.5

    def test_slug_extracted_from_url_with_slug_id(self):
        node = {
            "id": "proj-1",
            "name": "My Project",
            "description": "",
            "state": "started",
            "url": "https://linear.app/workspace/project/my-project-abc123",
            "slugId": "abc123",
            "projectMilestones": {"nodes": []},
            "issues": {"nodes": []},
        }
        project = LinearTrackerAdapter._node_to_tracker_project(node)
        assert project.slug == "my-project"


# ---------------------------------------------------------------------------
# _issue_to_run: progress status override
# ---------------------------------------------------------------------------


class TestIssueToRun:
    def test_progress_status_overrides_linear_state(self):
        node = _issue_node(state={"name": "Todo"})
        progress = {
            "status": "RUNNING",
            "run_id": uuid4(),
            "confidence": None,
            "session_id": None,
            "pr_url": None,
            "pr_id": None,
            "retry_count": None,
        }
        run = LinearTrackerAdapter._issue_to_run(node, progress=progress)
        assert run.status == RunStatus.RUNNING
