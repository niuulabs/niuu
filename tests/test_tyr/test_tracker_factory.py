"""Tests for TrackerAdapterFactory.

Covers: for_owner, pool injection, error handling, credential merging.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest

from niuu.domain.models import IntegrationConnection, IntegrationType
from tests.test_tyr.conftest import StubCredentialStore, StubIntegrationRepo
from tyr.adapters.native import NativeTrackerAdapter
from tyr.adapters.tracker_factory import TrackerAdapterFactory
from tyr.ports.tracker import TrackerPort

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

_NOW = datetime.now(tz=UTC)


def _make_connection(
    *,
    id: str = "conn-1",
    enabled: bool = True,
    credential_name: str = "linear-cred",
    adapter: str = "tests.test_tyr.test_tracker_factory.FakeTracker",
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

    async def create_raid(self, raid, *, project_id="", milestone_id=""):  # noqa: ANN001
        return "r-1"

    async def update_raid_state(self, raid_id, state):  # noqa: ANN001
        pass

    async def close_raid(self, raid_id):  # noqa: ANN001
        pass

    async def get_saga(self, saga_id):  # noqa: ANN001
        raise NotImplementedError

    async def get_phase(self, tracker_id):  # noqa: ANN001
        raise NotImplementedError

    async def get_raid(self, tracker_id):  # noqa: ANN001
        raise NotImplementedError

    async def list_pending_raids(self, phase_id):  # noqa: ANN001
        return []

    async def list_projects(self):
        return []

    async def get_project(self, project_id):  # noqa: ANN001
        raise NotImplementedError

    async def list_milestones(self, project_id):  # noqa: ANN001
        return []

    async def list_issues(self, project_id, milestone_id=None):  # noqa: ANN001
        return []

    async def update_raid_progress(self, tracker_id, **kwargs):  # noqa: ANN001, ANN003
        raise NotImplementedError

    async def get_raid_progress_for_saga(self, saga_tracker_id):  # noqa: ANN001
        return []

    async def get_raid_by_session(self, session_id):  # noqa: ANN001
        return None

    async def list_raids_by_status(self, status):  # noqa: ANN001
        return []

    async def get_raid_by_id(self, raid_id):  # noqa: ANN001
        return None

    async def add_confidence_event(self, tracker_id, event):  # noqa: ANN001
        pass

    async def get_confidence_events(self, tracker_id):  # noqa: ANN001
        return []

    async def all_raids_merged(self, phase_tracker_id):  # noqa: ANN001
        return False

    async def list_phases_for_saga(self, saga_tracker_id):  # noqa: ANN001
        return []

    async def update_phase_status(self, phase_tracker_id, status):  # noqa: ANN001
        return None

    async def get_saga_for_raid(self, tracker_id):  # noqa: ANN001
        return None

    async def get_phase_for_raid(self, tracker_id):  # noqa: ANN001
        return None

    async def get_owner_for_raid(self, tracker_id):  # noqa: ANN001
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

    async def create_raid(self, raid, *, project_id="", milestone_id=""):  # noqa: ANN001
        return ""

    async def update_raid_state(self, raid_id, state):  # noqa: ANN001
        pass

    async def close_raid(self, raid_id):  # noqa: ANN001
        pass

    async def get_saga(self, saga_id):  # noqa: ANN001
        raise NotImplementedError

    async def get_phase(self, tracker_id):  # noqa: ANN001
        raise NotImplementedError

    async def get_raid(self, tracker_id):  # noqa: ANN001
        raise NotImplementedError

    async def list_pending_raids(self, phase_id):  # noqa: ANN001
        return []

    async def list_projects(self):
        return []

    async def get_project(self, project_id):  # noqa: ANN001
        raise NotImplementedError

    async def list_milestones(self, project_id):  # noqa: ANN001
        return []

    async def list_issues(self, project_id, milestone_id=None):  # noqa: ANN001
        return []

    async def update_raid_progress(self, tracker_id, **kwargs):  # noqa: ANN001, ANN003
        raise NotImplementedError

    async def get_raid_progress_for_saga(self, saga_tracker_id):  # noqa: ANN001
        return []

    async def get_raid_by_session(self, session_id):  # noqa: ANN001
        return None

    async def list_raids_by_status(self, status):  # noqa: ANN001
        return []

    async def get_raid_by_id(self, raid_id):  # noqa: ANN001
        return None

    async def add_confidence_event(self, tracker_id, event):  # noqa: ANN001
        pass

    async def get_confidence_events(self, tracker_id):  # noqa: ANN001
        return []

    async def all_raids_merged(self, phase_tracker_id):  # noqa: ANN001
        return False

    async def list_phases_for_saga(self, saga_tracker_id):  # noqa: ANN001
        return []

    async def update_phase_status(self, phase_tracker_id, status):  # noqa: ANN001
        return None

    async def get_saga_for_raid(self, tracker_id):  # noqa: ANN001
        return None

    async def get_phase_for_raid(self, tracker_id):  # noqa: ANN001
        return None

    async def get_owner_for_raid(self, tracker_id):  # noqa: ANN001
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
async def test_adapter_instantiation_failure_logged_and_skipped(caplog) -> None:  # noqa: ANN001
    conn = _make_connection(
        id="conn-bad",
        enabled=True,
        adapter="tests.test_tyr.test_tracker_factory.NonExistentClass",
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
    """Factory must pass pool to adapter so LocalAdapter can use raid_progress table."""

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
    assert len(result) == 1
    assert result[0].kwargs["pool"] is pool


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


@pytest.mark.asyncio
async def test_unexpected_exception_logged_and_skipped(caplog) -> None:  # noqa: ANN001
    """Unexpected exceptions (not ImportError/TypeError/etc.) are caught and logged."""
    conn = _make_connection(
        id="conn-boom",
        enabled=True,
        adapter="tests.test_tyr.test_tracker_factory.BoomTracker",
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
