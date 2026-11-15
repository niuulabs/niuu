"""Tests for TrackerAdapterFactory.

Covers: for_owner, pool injection, error handling, credential merging.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest

from niuu.domain.models import IntegrationConnection, IntegrationType, IntegrationUsage
from niuu.ports.integrations import IntegrationUsageLog
from tests.test_ting.conftest import StubCredentialStore, StubIntegrationRepo
from ting.adapters.native import NativeTrackerAdapter
from ting.adapters.team_tracker import TeamTrackerRecorder
from ting.adapters.tracker_factory import TrackerAdapterFactory
from ting.ports.tracker import TrackerPort

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

_NOW = datetime.now(tz=UTC)


def _make_connection(
    *,
    id: str = "conn-1",
    enabled: bool = True,
    credential_name: str = "linear-cred",
    adapter: str = "tests.test_ting.test_tracker_factory.FakeTracker",
    config: dict | None = None,
) -> IntegrationConnection:
    return IntegrationConnection(
        id=id,
        owner_id="owner-1",
        integration_type=IntegrationType.ISSUE_TRACKER,
        adapter=adapter,
        credential_name=credential_name,
        config=config or {"team_id": "TEAM-1"},
        enabled=enabled,
        created_at=_NOW,
        updated_at=_NOW,
    )


class FakeTracker(TrackerPort):
    """Minimal TrackerPort stub for dynamic import testing."""

    def __init__(self, **kwargs) -> None:  # noqa: ANN003
        self.kwargs = kwargs

    async def create_saga(self, saga, *, description=""):  # noqa: ANN001
        return "s-1"

    async def create_phase(self, phase, *, project_id=""):  # noqa: ANN001
        return "p-1"

    async def create_run(self, run, *, project_id="", milestone_id=""):  # noqa: ANN001
        return "r-1"

    async def update_run_state(self, run_id, state):  # noqa: ANN001
        pass

    async def close_run(self, run_id):  # noqa: ANN001
        pass

    async def get_saga(self, saga_id):  # noqa: ANN001
        raise NotImplementedError

    async def get_phase(self, tracker_id):  # noqa: ANN001
        raise NotImplementedError

    async def get_run(self, tracker_id):  # noqa: ANN001
        raise NotImplementedError

    async def list_pending_runs(self, phase_id):  # noqa: ANN001
        return []

    async def list_projects(self):
        return []

    async def get_project(self, project_id):  # noqa: ANN001
        raise NotImplementedError

    async def list_milestones(self, project_id):  # noqa: ANN001
        return []

    async def list_issues(self, project_id, milestone_id=None):  # noqa: ANN001
        return []

    async def update_run_progress(self, tracker_id, **kwargs):  # noqa: ANN001, ANN003
        raise NotImplementedError

    async def get_run_progress_for_saga(self, saga_tracker_id):  # noqa: ANN001
        return []

    async def get_run_by_session(self, session_id):  # noqa: ANN001
        return None

    async def list_runs_by_status(self, status):  # noqa: ANN001
        return []

    async def get_run_by_id(self, run_id):  # noqa: ANN001
        return None

    async def all_runs_merged(self, phase_tracker_id):  # noqa: ANN001
        return False

    async def list_phases_for_saga(self, saga_tracker_id):  # noqa: ANN001
        return []

    async def update_phase_status(self, phase_tracker_id, status):  # noqa: ANN001
        return None

    async def get_saga_for_run(self, tracker_id):  # noqa: ANN001
        return None

    async def get_phase_for_run(self, tracker_id):  # noqa: ANN001
        return None

    async def get_owner_for_run(self, tracker_id):  # noqa: ANN001
        return None

    async def save_session_message(self, message):  # noqa: ANN001
        pass

    async def get_session_messages(self, tracker_id):  # noqa: ANN001
        return []


class BoomTracker(TrackerPort):
    """TrackerPort stub that raises RuntimeError on instantiation."""

    def __init__(self, **kwargs) -> None:  # noqa: ANN003
        raise RuntimeError("unexpected boom")

    async def create_saga(self, saga, *, description=""):  # noqa: ANN001
        return ""

    async def create_phase(self, phase, *, project_id=""):  # noqa: ANN001
        return ""

    async def create_run(self, run, *, project_id="", milestone_id=""):  # noqa: ANN001
        return ""

    async def update_run_state(self, run_id, state):  # noqa: ANN001
        pass

    async def close_run(self, run_id):  # noqa: ANN001
        pass

    async def get_saga(self, saga_id):  # noqa: ANN001
        raise NotImplementedError

    async def get_phase(self, tracker_id):  # noqa: ANN001
        raise NotImplementedError

    async def get_run(self, tracker_id):  # noqa: ANN001
        raise NotImplementedError

    async def list_pending_runs(self, phase_id):  # noqa: ANN001
        return []

    async def list_projects(self):
        return []

    async def get_project(self, project_id):  # noqa: ANN001
        raise NotImplementedError

    async def list_milestones(self, project_id):  # noqa: ANN001
        return []

    async def list_issues(self, project_id, milestone_id=None):  # noqa: ANN001
        return []

    async def update_run_progress(self, tracker_id, **kwargs):  # noqa: ANN001, ANN003
        raise NotImplementedError

    async def get_run_progress_for_saga(self, saga_tracker_id):  # noqa: ANN001
        return []

    async def get_run_by_session(self, session_id):  # noqa: ANN001
        return None

    async def list_runs_by_status(self, status):  # noqa: ANN001
        return []

    async def get_run_by_id(self, run_id):  # noqa: ANN001
        return None

    async def all_runs_merged(self, phase_tracker_id):  # noqa: ANN001
        return False

    async def list_phases_for_saga(self, saga_tracker_id):  # noqa: ANN001
        return []

    async def update_phase_status(self, phase_tracker_id, status):  # noqa: ANN001
        return None

    async def get_saga_for_run(self, tracker_id):  # noqa: ANN001
        return None

    async def get_phase_for_run(self, tracker_id):  # noqa: ANN001
        return None

    async def get_owner_for_run(self, tracker_id):  # noqa: ANN001
        return None

    async def save_session_message(self, message):  # noqa: ANN001
        pass

    async def get_session_messages(self, tracker_id):  # noqa: ANN001
        return []


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_connections_returns_empty_list() -> None:
    factory = TrackerAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[]),
        credential_store=StubCredentialStore(),
    )
    result = await factory.for_owner("owner-1")
    assert result == []


@pytest.mark.asyncio
async def test_one_enabled_one_disabled_returns_only_enabled() -> None:
    enabled = _make_connection(id="conn-1", enabled=True, credential_name="cred-a")
    disabled = _make_connection(id="conn-2", enabled=False, credential_name="cred-b")
    factory = TrackerAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[enabled, disabled]),
        credential_store=StubCredentialStore(
            values={
                "user:owner-1:cred-a": {"api_key": "tok-a"},
                "user:owner-1:cred-b": {"api_key": "tok-b"},
            }
        ),
    )
    result = await factory.for_owner("owner-1")
    assert len(result) == 1
    assert isinstance(result[0], FakeTracker)
    assert result[0].kwargs["api_key"] == "tok-a"
    assert result[0].kwargs["team_id"] == "TEAM-1"


@pytest.mark.asyncio
async def test_credential_missing_skips_adapter() -> None:
    conn = _make_connection(enabled=True)
    factory = TrackerAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[conn]),
        credential_store=StubCredentialStore(values={}),
    )
    result = await factory.for_owner("owner-1")
    assert result == []


@pytest.mark.asyncio
async def test_resolution_reports_enabled_connection_failures_without_secrets() -> None:
    missing = _make_connection(id="conn-missing", credential_name="missing")
    unsupported = _make_connection(
        id="conn-unsupported",
        credential_name="shared",
        adapter="volundr.adapters.outbound.github.GitHubAdapter",
    )
    broken = _make_connection(
        id="conn-broken",
        credential_name="shared",
        adapter="tests.test_ting.test_tracker_factory.NonExistentClass",
    )
    factory = TrackerAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[missing, unsupported, broken]),
        credential_store=StubCredentialStore(
            values={"user:owner-1:shared": {"api_key": "must-not-leak"}}
        ),
    )

    resolution = await factory.for_owner_with_resolution("owner-1")

    assert resolution.adapters == ()
    assert [(item.connection_id, item.code) for item in resolution.failures] == [
        ("conn-missing", "credentialUnavailable"),
        ("conn-unsupported", "unsupportedAdapter"),
        ("conn-broken", "adapterUnavailable"),
    ]
    assert "must-not-leak" not in repr(resolution.failures)


@pytest.mark.asyncio
async def test_adapter_instantiation_failure_logged_and_skipped(caplog) -> None:  # noqa: ANN001
    conn = _make_connection(
        id="conn-bad",
        enabled=True,
        adapter="tests.test_ting.test_tracker_factory.NonExistentClass",
    )
    good_conn = _make_connection(id="conn-good", enabled=True, credential_name="cred-good")
    factory = TrackerAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[conn, good_conn]),
        credential_store=StubCredentialStore(
            values={
                "user:owner-1:linear-cred": {"api_key": "tok-bad"},
                "user:owner-1:cred-good": {"api_key": "tok-good"},
            }
        ),
    )
    with caplog.at_level(logging.ERROR):
        result = await factory.for_owner("owner-1")

    assert len(result) == 1
    assert isinstance(result[0], FakeTracker)
    assert "Failed to create tracker adapter for connection conn-bad" in caplog.text


@pytest.mark.asyncio
async def test_config_merged_with_credentials() -> None:
    conn = _make_connection(
        enabled=True,
        config={"team_id": "TEAM-X", "extra_setting": "val"},
    )
    factory = TrackerAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[conn]),
        credential_store=StubCredentialStore(
            values={"user:owner-1:linear-cred": {"api_key": "tok-merge"}}
        ),
    )
    result = await factory.for_owner("owner-1")
    assert len(result) == 1
    assert result[0].kwargs["api_key"] == "tok-merge"
    assert result[0].kwargs["team_id"] == "TEAM-X"
    assert result[0].kwargs["extra_setting"] == "val"


@pytest.mark.asyncio
async def test_pool_injected_into_adapter() -> None:
    """Factory must pass pool to adapter so LocalAdapter can use run_progress table."""

    class FakePool:
        pass

    pool = FakePool()
    conn = _make_connection(enabled=True)
    factory = TrackerAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[conn]),
        credential_store=StubCredentialStore(
            values={"user:owner-1:linear-cred": {"api_key": "tok-x"}}
        ),
        pool=pool,
    )
    result = await factory.for_owner("owner-1")
    assert len(result) == 2
    assert result[0].kwargs["pool"] is pool
    assert isinstance(result[1], NativeTrackerAdapter)


@pytest.mark.asyncio
async def test_no_pool_does_not_inject_pool_kwarg() -> None:
    """When factory has no pool, pool kwarg is not forwarded to the adapter."""
    conn = _make_connection(enabled=True)
    factory = TrackerAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[conn]),
        credential_store=StubCredentialStore(
            values={"user:owner-1:linear-cred": {"api_key": "tok-y"}}
        ),
    )
    result = await factory.for_owner("owner-1")
    assert len(result) == 1
    assert "pool" not in result[0].kwargs


@pytest.mark.asyncio
async def test_maps_shared_linear_adapter_to_ting_linear_tracker() -> None:
    conn = _make_connection(
        adapter="volundr.adapters.outbound.linear.LinearAdapter",
        credential_name="shared-linear",
    )
    factory = TrackerAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[conn]),
        credential_store=StubCredentialStore(
            values={"user:owner-1:shared-linear": {"token": "lin-api-token"}}
        ),
        pool=object(),
    )

    result = await factory.for_owner("owner-1")

    assert len(result) == 2
    assert result[0].__class__.__name__ == "LinearTrackerAdapter"
    assert isinstance(result[1], NativeTrackerAdapter)


@pytest.mark.asyncio
async def test_maps_shared_jira_adapter_to_ting_jira_tracker() -> None:
    conn = _make_connection(
        adapter="volundr.adapters.outbound.jira.JiraAdapter",
        credential_name="jira-signin",
        config={
            "site_url": "https://example.atlassian.net",
            "project_keys": ["platform"],
            "labels": ["agent-work"],
        },
    )
    factory = TrackerAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[conn]),
        credential_store=StubCredentialStore(
            values={"user:owner-1:jira-signin": {"access_token": "jira-token"}}
        ),
        pool=object(),
    )

    result = await factory.for_owner("owner-1")

    assert len(result) == 2
    assert result[0].__class__.__name__ == "JiraTrackerAdapter"
    assert result[0]._project_keys == ("PLATFORM",)
    assert isinstance(result[1], NativeTrackerAdapter)
    await result[0].close()


@pytest.mark.asyncio
async def test_falls_back_to_native_tracker_when_no_connections_and_pool_available() -> None:
    class FakePool:
        pass

    pool = FakePool()
    factory = TrackerAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[]),
        credential_store=StubCredentialStore(),
        pool=pool,
    )

    result = await factory.for_owner("owner-1")

    assert len(result) == 1
    assert isinstance(result[0], NativeTrackerAdapter)
    assert result[0].connection_id == "native"
    assert result[0].provider == "native"


@pytest.mark.asyncio
async def test_unexpected_exception_logged_and_skipped(caplog) -> None:  # noqa: ANN001
    """Unexpected exceptions (not ImportError/TypeError/etc.) are caught and logged."""
    conn = _make_connection(
        id="conn-boom",
        enabled=True,
        adapter="tests.test_ting.test_tracker_factory.BoomTracker",
    )
    factory = TrackerAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[conn]),
        credential_store=StubCredentialStore(
            values={"user:owner-1:linear-cred": {"api_key": "tok-z"}}
        ),
    )
    with caplog.at_level(logging.ERROR):
        result = await factory.for_owner("owner-1")

    assert result == []
    assert "Unexpected error creating tracker adapter for connection conn-boom" in caplog.text


def test_linear_access_token_stays_an_access_token() -> None:
    from ting.adapters.tracker_factory import _normalize_tracker_credentials

    normalized = _normalize_tracker_credentials(
        "ting.adapters.linear.LinearTrackerAdapter",
        {"access_token": "oauth-token", "token": "ignored"},
    )

    assert normalized == {"access_token": "oauth-token", "token": "ignored"}


def test_linear_tracker_sends_an_access_token_as_bearer() -> None:
    from ting.adapters.linear import LinearTrackerAdapter

    adapter = LinearTrackerAdapter(access_token="oauth-token")

    assert adapter._gql._client.headers["Authorization"] == "Bearer oauth-token"
    with pytest.raises(ValueError):
        LinearTrackerAdapter(api_key="k", access_token="t")
    with pytest.raises(ValueError):
        LinearTrackerAdapter()


# ---------------------------------------------------------------------------
# Team (project-owned) trackers
# ---------------------------------------------------------------------------


class _UsageLog(IntegrationUsageLog):
    def __init__(self) -> None:
        self.recorded: list[IntegrationUsage] = []

    async def record(self, usage: IntegrationUsage) -> None:
        self.recorded.append(usage)

    async def list_for_project(self, project_id, *, limit, before=None):  # noqa: ANN001
        raise NotImplementedError


def _team_connection(
    project_id: str = "proj-1", *, id: str = "team-conn-1", enabled: bool = True
) -> IntegrationConnection:
    return IntegrationConnection(
        id=id,
        owner_id=f"project:{project_id}",
        integration_type=IntegrationType.ISSUE_TRACKER,
        adapter="tests.test_ting.test_tracker_factory.FakeTracker",
        credential_name="team-cred",
        config={"team_id": "TEAM-BOT"},
        enabled=enabled,
        created_at=_NOW,
        updated_at=_NOW,
        slug="linear",
        project_id=project_id,
        connected_by="owner-2",
    )


def _team_factory(
    connections: list[IntegrationConnection],
    member_projects,  # noqa: ANN001
    usage_log: IntegrationUsageLog | None,
) -> TrackerAdapterFactory:
    return TrackerAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=connections),
        credential_store=StubCredentialStore(
            values={
                "user:owner-1:linear-cred": {"api_key": "tok-own"},
                "project:proj-1:team-cred": {"access_token": "tok-team-1"},
                "project:proj-2:team-cred": {"access_token": "tok-team-2"},
            }
        ),
        member_projects=member_projects,
        usage_log=usage_log,
    )


@pytest.mark.asyncio
async def test_team_trackers_come_after_the_owners_own_and_record_their_writes() -> None:
    log = _UsageLog()
    asked: list[str] = []

    async def member_projects(owner_id: str) -> list[str]:
        asked.append(owner_id)
        return ["proj-1", "proj-2"]

    factory = _team_factory(
        [
            _make_connection(id="own-1", credential_name="linear-cred"),
            _team_connection("proj-1", id="team-1"),
            _team_connection("proj-2", id="team-2"),
        ],
        member_projects,
        log,
    )

    resolution = await factory.for_owner_with_resolution("owner-1")

    assert asked == ["owner-1"]
    assert resolution.failures == ()
    own, team_1, team_2 = resolution.adapters
    assert isinstance(own, FakeTracker)
    assert not isinstance(own, TeamTrackerRecorder)
    assert isinstance(team_1, TeamTrackerRecorder)
    assert isinstance(team_2, TeamTrackerRecorder)
    assert [a.connection_id for a in resolution.adapters] == ["own-1", "team-1", "team-2"]
    assert team_1._inner.kwargs["access_token"] == "tok-team-1"
    assert team_2._inner.kwargs["access_token"] == "tok-team-2"

    await team_2.close_run("run-9")

    assert [
        (u.connection_id, u.project_id, u.action, u.acting_for, u.actor, u.subject)
        for u in log.recorded
    ] == [("team-2", "proj-2", "close_run", "owner-1", "ting", "run-9")]


@pytest.mark.asyncio
async def test_a_team_connection_is_resolved_once() -> None:
    async def member_projects(owner_id: str) -> list[str]:
        return ["proj-1", "proj-1"]

    factory = _team_factory([_team_connection("proj-1")], member_projects, _UsageLog())

    resolution = await factory.for_owner_with_resolution("owner-1")

    assert [a.connection_id for a in resolution.adapters] == ["team-conn-1"]


@pytest.mark.asyncio
async def test_disabled_team_connection_is_skipped() -> None:
    async def member_projects(owner_id: str) -> list[str]:
        return ["proj-1"]

    factory = _team_factory(
        [_team_connection("proj-1", enabled=False)], member_projects, _UsageLog()
    )

    resolution = await factory.for_owner_with_resolution("owner-1")

    assert resolution.adapters == ()
    assert resolution.failures == ()


@pytest.mark.asyncio
async def test_membership_failure_is_a_loud_failure_not_only_personal_trackers(caplog) -> None:  # noqa: ANN001
    async def member_projects(owner_id: str) -> list[str]:
        raise ConnectionError("forge down")

    factory = _team_factory(
        [_make_connection(id="own-1", credential_name="linear-cred")], member_projects, _UsageLog()
    )

    with caplog.at_level(logging.ERROR):
        resolution = await factory.for_owner_with_resolution("owner-1")

    assert [a.connection_id for a in resolution.adapters] == ["own-1"]
    assert [f.code for f in resolution.failures] == ["teamTrackersUnavailable"]
    assert "Could not list the projects of owner owner-1" in caplog.text


@pytest.mark.asyncio
async def test_team_tracker_without_a_usage_log_is_refused(caplog) -> None:  # noqa: ANN001
    async def member_projects(owner_id: str) -> list[str]:
        return ["proj-1"]

    factory = _team_factory([_team_connection("proj-1")], member_projects, None)

    with caplog.at_level(logging.ERROR):
        resolution = await factory.for_owner_with_resolution("owner-1")

    assert resolution.adapters == ()
    assert [(f.connection_id, f.code) for f in resolution.failures] == [
        ("team-conn-1", "teamUsageUnrecorded")
    ]
    assert "no integration usage log is wired" in caplog.text


@pytest.mark.asyncio
async def test_no_member_projects_lookup_means_no_team_trackers() -> None:
    factory = _team_factory(
        [_team_connection("proj-1"), _make_connection(id="own-1", credential_name="linear-cred")],
        None,
        _UsageLog(),
    )

    resolution = await factory.for_owner_with_resolution("owner-1")

    assert [a.connection_id for a in resolution.adapters] == ["own-1"]
