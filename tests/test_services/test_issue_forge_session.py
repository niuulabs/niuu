"""SessionService.issue_forge_session: a refresh credential for the current launch."""

from __future__ import annotations

from uuid import uuid4

from tests.conftest import InMemorySessionRepository, MockPodManager
from tests.support.forge_session import OWNER, build_forge_app
from volundr.domain.models import Session, SessionStatus
from volundr.domain.services.forge_session_launch import launch_id_of
from volundr.domain.services.session import SessionService


async def test_mints_for_the_current_launch_without_starting_a_new_one():
    forge = build_forge_app()
    session = await forge.session()
    launched = await forge.start(session)
    before = await forge.sessions.get(session.id)

    refreshed = await forge.session_service.issue_forge_session(session.id)

    assert refreshed is not None
    assert refreshed.launch_id == launched.launch_id
    assert refreshed.session_id == str(session.id)
    assert forge.tokens.verify(refreshed.token).owner_id == OWNER
    assert (
        launch_id_of((await forge.sessions.get(session.id)).workload_config) == launched.launch_id
    )
    assert (await forge.sessions.get(session.id)).workload_config == before.workload_config
    assert len(forge.pod_manager.start_calls) == 1


async def test_carries_the_grants_of_the_session():
    forge = build_forge_app()
    session = await forge.session()
    stored = await forge.sessions.get(session.id)
    await forge.sessions.update(
        stored.model_copy(update={"workload_config": {"forge_mcp": {"grants": ["message"]}}})
    )
    launched = await forge.start(session)

    refreshed = await forge.session_service.issue_forge_session(session.id)

    assert refreshed.grants == launched.grants
    assert refreshed.scopes == launched.scopes


async def test_a_restart_moves_the_credential_to_the_new_launch():
    forge = build_forge_app()
    session = await forge.session()
    first = await forge.start(session)
    stale = await forge.session_service.issue_forge_session(session.id)

    await forge.set_status(session.id, SessionStatus.STOPPED)
    second = await forge.start(session)
    current = await forge.session_service.issue_forge_session(session.id)

    assert stale.launch_id == first.launch_id != second.launch_id
    assert current.launch_id == second.launch_id


async def test_an_unknown_session_gets_none():
    forge = build_forge_app()

    assert await forge.session_service.issue_forge_session(uuid4()) is None


async def test_a_session_that_never_launched_gets_none():
    forge = build_forge_app()
    session = await forge.session()

    assert await forge.session_service.issue_forge_session(session.id) is None


async def test_a_session_without_an_owner_gets_none():
    forge = build_forge_app()
    session = await forge.session()
    await forge.start(session)
    stored = await forge.sessions.get(session.id)
    await forge.sessions.update(stored.model_copy(update={"owner_id": None}))

    assert await forge.session_service.issue_forge_session(session.id) is None


async def test_none_when_no_broker_can_receive_a_credential():
    sessions = InMemorySessionRepository()
    service = SessionService(sessions, MockPodManager(), provisioning_initial_delay=0)
    session = await sessions.create(Session(name="s", owner_id=OWNER))
    await sessions.update(
        session.model_copy(update={"workload_config": {"forge_mcp": {"launch_id": "abc"}}})
    )

    assert await service.issue_forge_session(session.id) is None
