"""VmPodManager hosting a workflow run: one VM (or slot) for the root and every member."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.test_adapters.test_vm_hosted_unit import credential, member_launch
from tests.test_adapters.test_vm_pod_manager import setup  # noqa: F401 - the manager fixture
from volundr.adapters.outbound.vm_hosted_unit import (
    HOSTED_MEMBERS_KEY,
    UNIT_MESH_REGISTRY,
    HostedUnitError,
)
from volundr.domain.models import (
    HOSTED_MEMBERS_KEY as UNIT_MEMBERS_KEY,
)
from volundr.domain.models import (
    PodSpecAdditions,
    Session,
    SessionSpec,
    SessionStatus,
)
from volundr.domain.services.forge_session_launch import with_forge_mcp


def launched(member, launch: str):
    """``member`` as its start prepared it: the session carries its launch id."""
    session = member.session.model_copy(
        update={
            "workload_config": with_forge_mcp({}, grants=[], launch_id=launch),
            "status": SessionStatus.STARTING,
        }
    )
    return type(member)(session, member.spec)


def hosting(unit_session: Session, members) -> Session:
    return unit_session.model_copy(
        update={
            "workload_config": {
                UNIT_MEMBERS_KEY: [
                    {"id": str(m.session.id), "port": m.session.hosted_port} for m in members
                ]
            }
        }
    )


@pytest.fixture
def unit(setup):  # noqa: F811
    manager, service, repository, provider, runtime, store, session = setup
    for operation in (
        "member_running",
        "member_target",
        "member_ready",
        "restart_member",
        "stop_member",
        "stage_forge_session",
        "discard_forge_session",
    ):
        setattr(runtime, operation, AsyncMock())
    runtime.stage_forge_session = MagicMock()
    runtime.discard_forge_session = MagicMock()
    runtime.delivers_forge_session_token = True
    members = [
        launched(member_launch(session, 8082, persona="coder"), "launch-1"),
        launched(member_launch(session, 8083, persona="reviewer"), "launch-1"),
    ]
    session = hosting(session, members)
    source = AsyncMock(
        side_effect=lambda unit_id, only: [
            m for m in members if only is None or m.session.id == only
        ]
    )
    manager.configure_hosted_members(source)
    manager._mesh = {"enabled": True}
    return manager, repository, runtime, session, members, source


def spec_of(session: Session) -> SessionSpec:
    return SessionSpec(
        values={"session": {}},
        pod_spec=PodSpecAdditions(),
        forge_session=credential(session.id),
    )


async def test_starting_a_unit_hands_the_runtime_its_members_and_holds_their_credentials(unit):
    manager, _, runtime, session, members, source = unit
    spec = spec_of(session)

    await manager.start(session, spec)

    source.assert_awaited_once_with(session.id, None)
    entries = spec.values[HOSTED_MEMBERS_KEY]
    assert [e["id"] for e in entries] == [str(m.session.id) for m in members]
    assert [e["port"] for e in entries] == [8082, 8083]
    staged = {call.args[0]: call.args[1] for call in runtime.stage_forge_session.call_args_list}
    assert set(staged) == {session.id, *(m.session.id for m in members)}
    for member in members:
        assert staged[member.session.id] is member.spec.forge_session
    # The credentials stay out of what the runtime stores.
    assert all("token-of" not in str(entry) for entry in entries)


async def test_a_units_skulds_find_each_other_in_the_units_directory(unit, setup):  # noqa: F811
    manager, _, _, session, members, _ = unit
    manager._mesh = {"enabled": True, "transport": "nng"}
    spec = spec_of(session)

    await manager.start(session, spec)

    for env in (spec.values["env"], *(m.spec.values["env"] for m in members)):
        assert env["SKULD__MESH__ENABLED"] == "true"
        assert UNIT_MESH_REGISTRY in env["SKULD__MESH__DISCOVERY_ADAPTERS"]
        assert '"registry"' in env["SKULD__MESH__DISCOVERY_ADAPTERS"]


async def test_a_session_without_members_is_started_as_before(setup):  # noqa: F811
    manager, _, _, _, runtime, _, session = setup
    manager.configure_hosted_members(AsyncMock())
    spec = SessionSpec(values={}, pod_spec=PodSpecAdditions())

    await manager.start(session, spec)

    assert HOSTED_MEMBERS_KEY not in spec.values
    manager._hosted_members.assert_not_awaited()


async def test_a_unit_cannot_start_without_a_way_to_resolve_its_members(setup):  # noqa: F811
    manager, _, _, _, _, _, session = setup
    manager._mesh = {"enabled": True}
    session = hosting(session, [member_launch(session, 8082, persona="coder")])

    with pytest.raises(HostedUnitError, match="configure_hosted_members"):
        await manager.start(session, spec_of(session))


async def test_a_unit_starts_only_with_the_members_it_was_launched_with(unit):
    manager, _, _, session, members, source = unit
    source.side_effect = None
    source.return_value = members[:1]

    with pytest.raises(HostedUnitError, match="fixed when its unit starts"):
        await manager.start(session, spec_of(session))


async def test_a_unit_does_not_start_on_a_runtime_that_cannot_host_it(unit):
    manager, _, _, session, _, _ = unit
    manager._runtime = object.__new__(_Plain)

    with pytest.raises(HostedUnitError, match="cannot host a workflow run's members"):
        await manager.start(session, spec_of(session))


class _Plain:
    """A runtime with nothing of a unit's operations."""


async def started_unit(unit):
    manager, repository, runtime, session, members, _ = unit
    runtime.start.side_effect = None
    await manager.start(session, spec_of(session))
    assert await manager.wait_for_ready(session, 0.2) == SessionStatus.RUNNING
    return manager, repository, runtime, session, members


async def test_a_member_runs_when_its_unit_runs_and_its_own_skuld_answers(unit):
    manager, _, runtime, session, members = await started_unit(unit)
    runtime.member_running.return_value = True
    runtime.member_ready.return_value = True

    assert await manager.status(members[0].session) == SessionStatus.RUNNING

    runtime.member_running.assert_awaited()
    assert runtime.member_running.await_args.args[2] == members[0].session.id


async def test_a_member_whose_skuld_is_up_but_not_answering_is_still_provisioning(unit):
    manager, _, runtime, _, members = await started_unit(unit)
    runtime.member_running.return_value = True
    runtime.member_ready.return_value = False

    assert await manager.status(members[1].session) == SessionStatus.PROVISIONING


async def test_a_member_whose_skuld_is_gone_from_a_running_unit_has_failed(unit):
    manager, _, runtime, _, members = await started_unit(unit)
    runtime.member_running.return_value = False

    assert await manager.status(members[0].session) == SessionStatus.FAILED


async def test_a_member_is_only_as_up_as_its_unit(unit):
    manager, repository, runtime, session, members = await started_unit(unit)
    runtime.ready.return_value = False

    assert await manager.status(members[0].session) == SessionStatus.PROVISIONING
    runtime.member_running.assert_not_awaited()


async def test_a_member_of_a_unit_that_is_gone_is_stopped(unit):
    members = unit[4]
    manager = unit[0]
    member = members[0].session.model_copy(update={"status": SessionStatus.RUNNING})

    assert await manager.status(member) == SessionStatus.STOPPED


async def test_a_member_started_again_by_itself_gets_its_skuld_started_in_place(unit):
    manager, _, runtime, _, members = await started_unit(unit)
    runtime.member_running.return_value = True
    runtime.member_ready.return_value = True
    restarted = launched(members[0], "launch-2")
    # The source mints for the member's current launch.
    unit[5].side_effect = None
    unit[5].return_value = [restarted]
    restarted = restarted.session

    assert await manager.status(restarted) == SessionStatus.PROVISIONING

    runtime.restart_member.assert_awaited_once()
    assert runtime.restart_member.await_args.args[2] == members[0].session.id
    # The new launch's credential was held for the runtime to write.
    assert runtime.stage_forge_session.call_args_list[-1].args[0] == members[0].session.id
    # Once started under that launch it is judged by its Skuld.
    assert await manager.status(restarted) == SessionStatus.RUNNING
    runtime.restart_member.assert_awaited_once()


async def test_stopping_a_member_stops_its_skuld_alone(unit):
    manager, repository, runtime, session, members = await started_unit(unit)

    assert await manager.stop(members[0].session) is True

    runtime.stop_member.assert_awaited_once()
    assert runtime.stop_member.await_args.args[2] == members[0].session.id
    runtime.stop.assert_not_awaited()
    runtime.discard_forge_session.assert_any_call(members[0].session.id)


async def test_stopping_a_member_of_an_ended_unit_has_nothing_left_to_stop(unit):
    manager, _, runtime, _, members, _ = unit

    assert await manager.stop(members[0].session) is True

    runtime.stop_member.assert_not_awaited()


async def test_stopping_the_unit_forgets_every_members_credential(unit):
    manager, _, runtime, session, members = await started_unit(unit)

    assert await manager.stop(session) is True

    forgotten = {call.args[0] for call in runtime.discard_forge_session.call_args_list}
    assert forgotten == {session.id, *(m.session.id for m in members)}


async def test_a_member_is_reached_through_its_unit_on_its_own_port(unit):
    manager, _, runtime, session, members = await started_unit(unit)

    target = await manager.session_proxy_target(members[1].session)

    assert target is runtime.member_target.return_value
    assert runtime.member_target.await_args.args[2] == members[1].session.id
    # The broker target the proxy dials is the same, by the member's own id.
    assert await manager.broker_target(members[1].session) is target


async def test_a_member_has_no_target_while_its_unit_has_none(unit):
    manager, members = unit[0], unit[4]

    assert await manager.session_proxy_target(members[0].session) is None


async def test_a_members_chat_endpoint_is_the_proxy_route_of_its_own_id(unit):
    manager, _, _, _, members, _ = unit
    member = members[0].session

    endpoint = manager.initial_chat_endpoint(member)

    assert endpoint.endswith(f"/s/{member.id}/session")


async def test_a_unit_with_the_mesh_off_is_refused_before_a_machine_is_claimed(unit):
    manager, repository, runtime, session, members, source = unit
    manager._mesh = {}

    with pytest.raises(HostedUnitError, match="mesh is off in this VM pod manager"):
        await manager.start(session, spec_of(session))

    source.assert_not_awaited()
    runtime.stage_forge_session.assert_not_called()


async def test_forge_asks_for_a_members_restart_explicitly(unit):
    manager, _, runtime, _, members = await started_unit(unit)
    runtime.member_running.return_value = True
    runtime.member_ready.return_value = True
    restarted = launched(members[0], "launch-2")
    unit[5].side_effect = None
    unit[5].return_value = [restarted]

    await manager.restart_hosted_member(restarted.session)

    runtime.restart_member.assert_awaited_once()
    assert runtime.restart_member.await_args.args[2] == members[0].session.id
    # It is the new launch now, so the status sweep does not restart it again.
    assert await manager.status(restarted.session) == SessionStatus.RUNNING
    runtime.restart_member.assert_awaited_once()


async def test_a_member_of_a_unit_with_no_machine_cannot_be_restarted(unit):
    manager, _, _, _, members, _ = unit

    with pytest.raises(HostedUnitError, match="holds no machine or slot"):
        await manager.restart_hosted_member(members[0].session)


async def test_stopping_one_member_by_the_default_hook_stops_its_skuld(unit):
    manager, _, runtime, _, members = await started_unit(unit)

    await manager.stop_hosted_member(members[0].session)

    runtime.stop_member.assert_awaited_once()
