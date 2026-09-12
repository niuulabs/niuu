"""Denied Cedar decisions must stop persistence and exclude list results."""

from unittest.mock import AsyncMock

import pytest

from identity.adapters.cedar import CedarAuthorizationAdapter
from niuu.domain.models import Principal
from tests.conftest import InMemorySessionRepository, MockPodManager
from volundr.domain.models import Session, SessionStatus
from volundr.domain.services.session import SessionAccessDeniedError, SessionService


async def test_viewer_creation_denied_before_persistence_or_broadcast():
    repository = InMemorySessionRepository()
    broadcaster = AsyncMock()
    service = SessionService(
        repository,
        MockPodManager(),
        authorization=CedarAuthorizationAdapter(),
        broadcaster=broadcaster,
    )
    with pytest.raises(SessionAccessDeniedError):
        await service.create_session(
            "forbidden",
            "model",
            principal=Principal(
                "viewer",
                "",
                "acme",
                ["volundr:viewer"],
            ),
        )
    assert await repository.list() == []
    broadcaster.publish_session_created.assert_not_called()


@pytest.mark.parametrize("status", [None, SessionStatus.CREATED])
async def test_list_applies_cedar_even_when_repository_returns_candidates(status):
    repository = AsyncMock()
    repository.list.return_value = [
        Session(name="allowed", model="m", owner_id="alice", tenant_id="acme"),
        Session(name="other-tenant", model="m", owner_id="alice", tenant_id="other"),
        Session(name="unowned", model="m", owner_id="alice", tenant_id=None),
    ]
    service = SessionService(
        repository, MockPodManager(), authorization=CedarAuthorizationAdapter()
    )
    result = await service.list_sessions(
        status=status,
        principal=Principal(
            "alice",
            "",
            "acme",
            ["volundr:admin"],
        ),
    )
    assert [s.name for s in result] == ["allowed"]


@pytest.mark.parametrize("action", ["read", "stop", "update", "delete", "emit_event"])
async def test_missing_principal_cannot_bypass_configured_authorization(action):
    repository = InMemorySessionRepository()
    service = SessionService(
        repository, MockPodManager(), authorization=CedarAuthorizationAdapter()
    )
    session = Session(name="private", model="m", owner_id="alice", tenant_id="acme")
    with pytest.raises(SessionAccessDeniedError):
        await service._check_access(session, None, action)


async def test_missing_principal_cannot_list_sessions():
    repository = AsyncMock()
    service = SessionService(
        repository, MockPodManager(), authorization=CedarAuthorizationAdapter()
    )
    with pytest.raises(PermissionError):
        await service.list_sessions()
    repository.list.assert_not_called()


@pytest.mark.parametrize(
    "action",
    [
        "read",
        "report_activity",
        "report_usage",
        "report_timeline",
        "report_chronicle",
        "emit_event",
    ],
)
async def test_session_read_and_telemetry_actions_work_for_owner_only(action):
    service = SessionService(
        InMemorySessionRepository(), MockPodManager(), authorization=CedarAuthorizationAdapter()
    )
    session = Session(name="owned", model="m", owner_id="alice", tenant_id="acme")
    await service._check_access(
        session, Principal("alice", "", "acme", ["volundr:developer"]), action
    )
    for principal in [
        Principal("bob", "", "acme", ["volundr:developer"]),
        Principal("alice", "", "other", ["volundr:admin"]),
    ]:
        with pytest.raises(SessionAccessDeniedError):
            await service._check_access(session, principal, action)
