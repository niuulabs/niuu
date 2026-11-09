"""A unit root's pipeline sees what its hosted members need; the pod is the root's."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

from tests.support.forge_session import OWNER
from tests.test_domain.test_forge_hosted_members import PRINCIPAL, Unit
from volundr.domain.models import IntegrationConnection, Session
from volundr.domain.ports import SessionContext, SessionContribution, SessionContributor


def _connection(connection_id: str, credential_name: str) -> IntegrationConnection:
    return IntegrationConnection(
        id=connection_id,
        owner_id=OWNER,
        integration_type="ai_provider",
        adapter="some.adapter",
        credential_name=credential_name,
        config={},
        enabled=True,
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
        slug="test",
    )


class Recorder(SessionContributor):
    """Notes the context each session's pipeline ran with."""

    def __init__(self) -> None:
        self.seen: dict = {}

    @property
    def name(self) -> str:
        return "recorder"

    async def contribute(self, session: Session, context: SessionContext) -> SessionContribution:
        self.seen[session.id] = context
        return SessionContribution()


def _repository(*connections: IntegrationConnection) -> AsyncMock:
    by_id = {c.id: c for c in connections}
    repo = AsyncMock()
    repo.get_connection.side_effect = by_id.get
    repo.list_connections.return_value = list(connections)
    return repo


async def test_the_root_sees_the_connections_each_member_is_launched_with():
    recorder = Recorder()
    unit = Unit(
        contributors=[recorder],
        integration_repo=_repository(_connection("c-1", "first"), _connection("c-2", "second")),
    )
    root = await unit.root()
    coder = await unit.member(root, "coder-1", integration_ids=["c-1"])
    reviewer = await unit.member(root, "reviewer-1", member="reviewer", integration_ids=["c-2"])

    await unit.start(root)

    members = {m.session_id: m for m in recorder.seen[root.id].unit_members}
    assert list(members) == [str(coder.id), str(reviewer.id)]
    assert [c.credential_name for c in members[str(coder.id)].integration_connections] == ["first"]
    assert [c.credential_name for c in members[str(reviewer.id)].integration_connections] == [
        "second"
    ]
    assert members[str(coder.id)].owner_id == PRINCIPAL.user_id


async def test_a_member_launched_with_no_selection_gets_the_owners_enabled_connections():
    recorder = Recorder()
    unit = Unit(
        contributors=[recorder],
        integration_repo=_repository(_connection("c-1", "first"), _connection("c-2", "second")),
    )
    root = await unit.root()
    coder = await unit.member(root, "coder-1")

    await unit.start(root)

    (member,) = recorder.seen[root.id].unit_members
    assert member.session_id == str(coder.id)
    assert [c.id for c in member.integration_connections] == ["c-1", "c-2"]


async def test_only_a_unit_root_carries_its_members():
    recorder = Recorder()
    unit = Unit(contributors=[recorder], integration_repo=_repository(_connection("c-1", "first")))
    root = await unit.root()
    coder = await unit.member(root, "coder-1")
    await unit.start(root)

    await unit.app.session_service.hosted_member_launches(root.id)
    plain = await unit.app.session(name="plain")
    await unit.app.start(plain)

    assert recorder.seen[coder.id].unit_members == ()
    assert recorder.seen[plain.id].unit_members == ()
