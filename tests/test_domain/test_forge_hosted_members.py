"""A run's members are hosted by its unit root: create, start, status, endpoint, stop."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from tests.conftest import make_session_participant_service
from tests.support.forge_session import OWNER, TENANT, ForgeApp, build_forge_app
from volundr.adapters.inbound.rest import SessionCreate
from volundr.domain.models import (
    HOSTED_LAUNCH_KEY,
    HOSTED_MEMBERS_KEY,
    HOSTED_UNIT_KEY,
    Principal,
    Session,
    SessionStatus,
    hosted_status,
)
from volundr.domain.ports import HostedUnitError
from volundr.domain.services.forge import (
    DEFAULT_HOSTED_MEMBER_BASE_PORT,
    ForgeService,
    HostedMemberError,
    HostedUnitInUseError,
    HostedUnitNotFoundError,
)
from volundr.domain.services.forge_session_launch import launch_id_of
from volundr.domain.services.session import SessionNotFoundError, SessionStateError

RUN = uuid4()
PRINCIPAL = Principal(user_id=OWNER, email="owner@example.com", tenant_id=TENANT, roles=[])
OTHER = Principal(user_id="someone-else", email="x@example.com", tenant_id=TENANT, roles=[])


class Unit:
    """A Forge app plus the ForgeService on top of its session service."""

    def __init__(self, base_port: int = DEFAULT_HOSTED_MEMBER_BASE_PORT, **service_kwargs) -> None:
        self.app: ForgeApp = build_forge_app(**service_kwargs)
        self.forge = ForgeService(
            self.app.session_service,
            session_participant_service=make_session_participant_service(self.app.session_service),
            hosted_member_base_port=base_port,
        )

    async def root(self, name: str = "wf-run", run=RUN, **extra) -> Session:
        return await self.forge.create_and_start_session(
            SessionCreate(
                name=name,
                hosts_members=True,
                workflow_run_id=run,
                workflow_run_room=True,
                **{"workflow_orchestration": "engine", **extra},
            ),
            principal=PRINCIPAL,
        )

    async def member(self, root: Session, name: str, member="coder", **extra) -> Session:
        return await self.forge.create_and_start_session(
            SessionCreate(
                name=name,
                hosted_by=root.id,
                workflow_run_id=RUN,
                workflow_member=member,
                definition="skuldClaude",
                **extra,
            ),
            principal=PRINCIPAL,
        )

    async def start(self, root: Session) -> Session:
        started = await self.forge.start_session(root.id, principal=PRINCIPAL)
        task = self.app.session_service._provisioning_tasks.get(root.id)
        if task is not None:
            await task
        return started

    async def start_and_run(self, root: Session) -> Session:
        """Start the unit and let it come up: the pod manager reports it running."""
        started = await self.start(root)
        await self.app.set_status(root.id, SessionStatus.RUNNING)
        return started

    async def stored(self, session: Session) -> Session:
        return await self.app.sessions.get(session.id)


@pytest.fixture
def unit() -> Unit:
    return Unit()


class TestCreate:
    async def test_the_root_is_held_not_started(self, unit: Unit):
        root = await unit.root()

        assert root.status == SessionStatus.CREATED
        assert root.is_unit_root
        assert root.hosted_by is None
        assert unit.app.pod_manager.start_calls == []

    async def test_members_are_hosted_by_the_root_and_not_started(self, unit: Unit):
        root = await unit.root()

        coder = await unit.member(root, "coder-1")
        reviewer = await unit.member(root, "reviewer-1", member="reviewer")

        assert coder.hosted_by == reviewer.hosted_by == root.id
        assert (coder.hosted_port, reviewer.hosted_port) == (
            DEFAULT_HOSTED_MEMBER_BASE_PORT,
            DEFAULT_HOSTED_MEMBER_BASE_PORT + 1,
        )
        assert coder.status == reviewer.status == SessionStatus.CREATED
        assert unit.app.pod_manager.start_calls == []
        assert [m.id for m in await unit.app.sessions.list_hosted(root.id)] == [
            coder.id,
            reviewer.id,
        ]

    async def test_each_member_is_its_own_session_with_its_launch_held(self, unit: Unit):
        root = await unit.root()

        coder = await unit.member(root, "coder-1", system_prompt="write it")
        reviewer = await unit.member(root, "reviewer-1", member="reviewer")

        assert coder.id != reviewer.id != root.id
        held = coder.workload_config[HOSTED_LAUNCH_KEY]
        assert held["system_prompt"] == "write it"
        assert held["definition"] == "skuldClaude"
        assert reviewer.workload_config[HOSTED_LAUNCH_KEY]["system_prompt"] == ""
        assert HOSTED_LAUNCH_KEY not in root.workload_config
        assert HOSTED_UNIT_KEY in root.workload_config

    async def test_the_base_port_is_the_configured_one(self):
        unit = Unit(base_port=9100)
        root = await unit.root()

        assert (await unit.member(root, "coder-1")).hosted_port == 9100

    async def test_a_port_freed_by_a_deleted_member_is_not_given_to_a_live_one(self, unit: Unit):
        root = await unit.root()
        first = await unit.member(root, "coder-1")
        second = await unit.member(root, "reviewer-1", member="reviewer")
        unit.app.sessions._sessions.pop(first.id)  # the first member is gone

        third = await unit.member(root, "tester-1", member="tester")

        assert third.hosted_port == second.hosted_port + 1

    @pytest.mark.parametrize("base_port", [0, 65536, -1])
    def test_a_base_port_must_be_a_tcp_port(self, unit: Unit, base_port: int):
        with pytest.raises(ValueError, match="hosted_member_base_port"):
            ForgeService(
                unit.app.session_service,
                session_participant_service=make_session_participant_service(
                    unit.app.session_service
                ),
                hosted_member_base_port=base_port,
            )


class TestRefusals:
    async def test_a_member_cannot_be_added_after_the_unit_started(self, unit: Unit):
        root = await unit.root()
        await unit.member(root, "coder-1")
        await unit.start(root)

        with pytest.raises(HostedMemberError, match="fixed when its unit starts") as refused:
            await unit.member(root, "late-1", member="late")

        assert isinstance(refused.value, SessionStateError)  # the routes answer it with 409
        assert len(await unit.app.sessions.list_hosted(root.id)) == 1
        assert not [s for s in await unit.app.sessions.list() if s.name == "late-1"]

    async def test_a_member_cannot_be_added_to_a_stopped_unit(self, unit: Unit):
        root = await unit.root()
        await unit.start(root)
        await unit.forge.stop_session(root.id, principal=PRINCIPAL)

        with pytest.raises(HostedMemberError, match="fixed when its unit starts"):
            await unit.member(root, "late-1", member="late")

    async def test_a_unit_that_starts_while_the_member_is_added_refuses_it(self, unit: Unit):
        """The check and the attach are one step: a start in between cannot slip a member in."""
        root = await unit.root()
        repository = unit.app.sessions
        attach = repository.attach_to_unit

        async def started_first(session_id, unit_id, base_port):
            await unit.app.set_status(unit_id, SessionStatus.STARTING)
            return await attach(session_id, unit_id, base_port)

        repository.attach_to_unit = started_first

        with pytest.raises(HostedMemberError, match="started or was removed"):
            await unit.member(root, "racing-1")

        assert not [s for s in await repository.list() if s.name == "racing-1"]

    async def test_the_unit_must_exist(self, unit: Unit):
        ghost = Session(name="ghost", id=uuid4())

        with pytest.raises(HostedUnitNotFoundError, match="does not exist"):
            await unit.member(ghost, "coder-1")

    async def test_the_unit_must_be_a_unit_root(self, unit: Unit):
        plain = await unit.app.session(name="plain")

        with pytest.raises(HostedMemberError, match="not a unit"):
            await unit.member(plain, "coder-1")

    async def test_the_unit_must_belong_to_the_same_owner(self, unit: Unit):
        root = await unit.root()

        with pytest.raises(HostedMemberError, match="another owner"):
            await unit.forge.create_and_start_session(
                SessionCreate(
                    name="coder-1",
                    hosted_by=root.id,
                    workflow_run_id=RUN,
                    workflow_member="coder",
                ),
                principal=OTHER,
            )

    async def test_the_member_must_belong_to_the_units_run(self, unit: Unit):
        root = await unit.root()

        with pytest.raises(HostedMemberError, match="member joins the unit of its own run"):
            await unit.forge.create_and_start_session(
                SessionCreate(
                    name="coder-1",
                    hosted_by=root.id,
                    workflow_run_id=uuid4(),
                    workflow_member="coder",
                ),
                principal=PRINCIPAL,
            )

    async def test_a_unit_with_members_cannot_be_deleted(self, unit: Unit):
        root = await unit.root()
        coder = await unit.member(root, "coder-1")

        with pytest.raises(HostedUnitInUseError, match="delete the members first"):
            await unit.forge.delete_session(root.id, principal=PRINCIPAL)
        assert await unit.forge.delete_session(coder.id, principal=PRINCIPAL) is True
        assert await unit.forge.delete_session(root.id, principal=PRINCIPAL) is True

    async def test_a_member_cannot_be_started_by_itself_before_its_unit(self, unit: Unit):
        root = await unit.root()
        coder = await unit.member(root, "coder-1")

        with pytest.raises(HostedMemberError, match="start the unit"):
            await unit.forge.start_session(coder.id, principal=PRINCIPAL)
        assert unit.app.pod_manager.start_calls == []

    def test_a_member_cannot_also_be_a_unit_or_a_room(self):
        with pytest.raises(ValueError, match="cannot itself be a unit root"):
            SessionCreate(name="x", hosted_by=uuid4(), hosts_members=True)
        with pytest.raises(ValueError, match="cannot itself be a unit root"):
            SessionCreate(name="x", hosted_by=uuid4(), workflow_run_id=RUN, workflow_run_room=True)


STEP_HOST = {
    "workflow_step_host": True,
    "workflow_run_id": RUN,
    "workflow_member": "coder",
    "workflow_persona_alias": "coder-alias",
    "workflow_event_filters": {"language": "python"},
    "workflow_orchestration": "session",
    "hosted_by": uuid4(),
}


class TestWorkflowStepSettings:
    """A member Skuld cannot start without these, so Forge refuses the create without them."""

    def test_a_step_host_with_every_setting_is_accepted(self):
        data = SessionCreate(name="x", **STEP_HOST)

        assert data.workflow_persona_alias == "coder-alias"
        assert data.workflow_event_filters == {"language": "python"}
        assert data.workflow_orchestration == "session"

    def test_a_step_host_without_filters_names_an_empty_mapping_not_a_default(self):
        fields = {**STEP_HOST, "workflow_event_filters": {}}

        assert SessionCreate(name="x", **fields).workflow_event_filters == {}

    @pytest.mark.parametrize(
        "missing",
        [
            "workflow_run_id",
            "workflow_member",
            "workflow_persona_alias",
            "workflow_event_filters",
            "workflow_orchestration",
            "hosted_by",
        ],
    )
    def test_a_step_host_missing_a_setting_is_refused_naming_it(self, missing: str):
        fields = {key: value for key, value in STEP_HOST.items() if key != missing}

        with pytest.raises(ValueError, match="workflow_step_host needs|must be sent together"):
            SessionCreate(name="x", **fields)

    def test_a_step_host_of_no_run_is_refused(self):
        with pytest.raises(ValueError, match="workflow_run_id and workflow_member"):
            SessionCreate(name="x", workflow_step_host=True)

    def test_the_settings_belong_to_a_step_host(self):
        for field, value in (
            ("workflow_persona_alias", "coder"),
            ("workflow_event_filters", {}),
            ("workflow_orchestration", "engine"),
        ):
            with pytest.raises(ValueError, match="belong to a workflow step host"):
                SessionCreate(name="x", **{field: value})

    def test_an_unknown_orchestration_mode_is_refused(self):
        with pytest.raises(ValueError, match="workflow_orchestration"):
            SessionCreate(name="x", **{**STEP_HOST, "workflow_orchestration": "mystery"})

    def test_a_run_room_needs_the_orchestration_and_no_member_settings(self):
        with pytest.raises(ValueError, match="workflow_run_room needs workflow_orchestration"):
            SessionCreate(name="x", workflow_run_id=RUN, workflow_run_room=True)
        with pytest.raises(ValueError, match="runs no agent"):
            SessionCreate(
                name="x",
                workflow_run_id=RUN,
                workflow_run_room=True,
                workflow_orchestration="engine",
                workflow_persona_alias="coder",
            )

    async def test_the_settings_are_kept_on_the_members_and_the_roots_workload(self, unit: Unit):
        root = await unit.root(workflow_orchestration="session")
        member = await unit.forge.create_and_start_session(
            SessionCreate(
                name="coder-1",
                definition="skuldClaude",
                **{**STEP_HOST, "hosted_by": root.id},
            ),
            principal=PRINCIPAL,
        )

        kept = (await unit.stored(member)).workload_config
        assert kept["workflow_persona_alias"] == "coder-alias"
        assert kept["workflow_event_filters"] == {"language": "python"}
        assert kept["workflow_orchestration"] == "session"
        assert kept["workflow_step_host"] is True
        assert (await unit.stored(root)).workload_config["workflow_orchestration"] == "session"


class TestStart:
    async def test_starting_the_root_starts_the_unit_with_every_member(self, unit: Unit):
        root = await unit.root()
        coder = await unit.member(root, "coder-1")
        reviewer = await unit.member(root, "reviewer-1", member="reviewer")

        started = await unit.start(root)

        assert started.status == SessionStatus.STARTING
        # One pod start: the unit's. A member has no pod of its own.
        assert [s.id for s, _ in unit.app.pod_manager.start_calls] == [root.id]
        for member in (coder, reviewer):
            assert (await unit.stored(member)).status == SessionStatus.STARTING

    async def test_a_member_read_while_its_unit_starts_is_not_reaped(self, unit: Unit):
        root = await unit.root()
        coder = await unit.member(root, "coder-1")
        service = unit.app.session_service
        start_root = service.start_session
        seen: list[SessionStatus] = []

        async def read_the_member_first(*args, **kwargs):
            # A reader (Ting's sweep) asks for the member after it is marked STARTING and
            # before the root's start spawns it: no runtime exists yet.
            looked_up = await service.reconcile_session_if_active(coder.id)
            seen.append(looked_up.status)
            return await start_root(*args, **kwargs)

        async def no_runtime_for_members(session):
            return SessionStatus.STOPPED if session.hosted_by else session.status

        unit.app.pod_manager.status = no_runtime_for_members
        service.start_session = read_the_member_first
        await unit.start(root)

        assert seen == [SessionStatus.STARTING]
        assert (await unit.stored(coder)).status == SessionStatus.STARTING

    async def test_the_unit_is_started_with_its_members_fixed(self, unit: Unit):
        root = await unit.root()
        coder = await unit.member(root, "coder-1")
        reviewer = await unit.member(root, "reviewer-1", member="reviewer")

        await unit.start(root)

        config = (await unit.stored(root)).workload_config
        assert config[HOSTED_MEMBERS_KEY] == [
            {"id": str(coder.id), "port": coder.hosted_port},
            {"id": str(reviewer.id), "port": reviewer.hosted_port},
        ]

    async def test_the_root_starts_with_the_launch_it_was_created_with(self, unit: Unit):
        root = await unit.root(system_prompt="host the run", initial_prompt="begin")
        real = unit.app.session_service.start_session
        spy = AsyncMock(wraps=real)
        unit.app.session_service.start_session = spy

        await unit.start(root)

        kwargs = spy.await_args.kwargs
        assert (kwargs["system_prompt"], kwargs["initial_prompt"]) == ("host the run", "begin")
        assert spy.await_args.args == (root.id,)

    async def test_each_member_gets_its_own_launch(self, unit: Unit):
        root = await unit.root()
        coder = await unit.member(root, "coder-1")
        reviewer = await unit.member(root, "reviewer-1", member="reviewer")

        await unit.start(root)

        coder_launch = launch_id_of((await unit.stored(coder)).workload_config)
        reviewer_launch = launch_id_of((await unit.stored(reviewer)).workload_config)
        root_launch = launch_id_of((await unit.stored(root)).workload_config)
        assert coder_launch and reviewer_launch and root_launch
        assert len({coder_launch, reviewer_launch, root_launch}) == 3

    async def test_a_unit_that_cannot_start_leaves_its_members_as_they_were(self, unit: Unit):
        root = await unit.root()
        coder = await unit.member(root, "coder-1")
        unit.app.pod_manager.start_success = False  # the pod start fails in the background
        await unit.app.set_status(root.id, SessionStatus.RUNNING)  # but the root cannot start

        with pytest.raises(SessionStateError):
            await unit.forge.start_session(root.id, principal=PRINCIPAL)

        assert (await unit.stored(coder)).status == SessionStatus.CREATED
        assert launch_id_of((await unit.stored(coder)).workload_config) is None

    async def test_a_unit_that_fails_to_start_puts_its_members_back_as_they_were(self, unit: Unit):
        root = await unit.root()
        coder = await unit.member(root, "coder-1")
        unit.app.session_service.start_session = AsyncMock(side_effect=RuntimeError("no room"))

        with pytest.raises(RuntimeError, match="no room"):
            await unit.forge.start_session(root.id, principal=PRINCIPAL)

        row = await unit.stored(coder)
        assert row.status == SessionStatus.CREATED
        assert launch_id_of(row.workload_config) is None
        assert row.hosted_by == root.id

    async def test_a_started_unit_is_not_started_again(self, unit: Unit):
        root = await unit.root()
        await unit.start(root)

        with pytest.raises(SessionStateError):
            await unit.forge.start_session(root.id, principal=PRINCIPAL)


class TestStatusAndEndpoint:
    async def test_a_members_status_follows_its_unit(self, unit: Unit):
        root = await unit.root()
        coder = await unit.member(root, "coder-1")
        assert (await unit.forge.get_session(coder.id)).status == SessionStatus.CREATED

        await unit.start_and_run(root)

        assert (await unit.forge.get_session(root.id)).status == SessionStatus.RUNNING
        assert (await unit.forge.get_session(coder.id)).status == SessionStatus.RUNNING
        await unit.app.set_status(root.id, SessionStatus.FAILED)
        assert (await unit.forge.get_session(coder.id)).status == SessionStatus.FAILED
        await unit.app.set_status(root.id, SessionStatus.RUNNING)
        assert (await unit.forge.get_session(coder.id)).status == SessionStatus.RUNNING

    async def test_a_members_endpoint_is_the_one_its_runtime_names(self, unit: Unit):
        """Forge asks the pod manager, as it does for an ordinary session at its start."""
        root = await unit.root()
        coder = await unit.member(root, "coder-1")
        reviewer = await unit.member(root, "reviewer-1", member="reviewer")
        await unit.start_and_run(root)
        pod_manager = unit.app.pod_manager
        pod_manager.initial_chat_endpoint = lambda s: (
            f"wss://gw.example.com/s/{s.id}/session" if s.hosted_by else None
        )

        coder_view = await unit.forge.get_session(coder.id)
        reviewer_view = await unit.forge.get_session(reviewer.id)

        assert coder_view.chat_endpoint == f"wss://gw.example.com/s/{coder.id}/session"
        assert reviewer_view.chat_endpoint == f"wss://gw.example.com/s/{reviewer.id}/session"
        assert coder_view.code_endpoint is None

    async def test_a_member_whose_runtime_names_no_endpoint_is_reached_through_forges_proxy(
        self, unit: Unit
    ):
        root = await unit.root()
        coder = await unit.member(root, "coder-1")
        await unit.start_and_run(root)

        view = await unit.forge.get_session(coder.id)

        assert view.chat_endpoint == f"ws://localhost:8080/s/{coder.id}/session"

    async def test_every_route_that_reaches_a_session_gets_the_members_own_target(self, unit: Unit):
        root = await unit.root()
        coder = await unit.member(root, "coder-1")
        await unit.start(root)

        session, base = await unit.forge.get_session_proxy_target(coder.id)

        assert session.id == coder.id
        assert base == f"http://localhost:8080/s/{coder.id}"
        authorized = await unit.forge.get_authorized_session(
            coder.id, principal=PRINCIPAL, action="read"
        )
        assert authorized.chat_endpoint == f"ws://localhost:8080/s/{coder.id}/session"

    async def test_listing_shows_members_as_their_unit_shows_them(self, unit: Unit):
        root = await unit.root()
        coder = await unit.member(root, "coder-1")
        await unit.start_and_run(root)

        listed = {s.id: s for s in await unit.forge.list_sessions(principal=PRINCIPAL)}

        assert listed[coder.id].status == SessionStatus.RUNNING
        assert listed[coder.id].chat_endpoint == f"ws://localhost:8080/s/{coder.id}/session"

    async def test_a_member_whose_unit_is_gone_reads_as_failed_with_the_reason(self, unit: Unit):
        root = await unit.root()
        coder = await unit.member(root, "coder-1")
        unit.app.sessions._sessions.pop(root.id)

        view = await unit.forge.get_session(coder.id)

        assert view.status == SessionStatus.FAILED
        assert "is gone" in view.error
        assert view.chat_endpoint is None

    def test_status_derivation(self):
        up = SessionStatus.RUNNING
        assert hosted_status(SessionStatus.STARTING, up) == up
        assert hosted_status(SessionStatus.CREATED, SessionStatus.CREATED) == SessionStatus.CREATED
        # A member that stopped or failed on its own stays so while its unit is up.
        assert hosted_status(SessionStatus.STOPPED, up) == SessionStatus.STOPPED
        assert hosted_status(SessionStatus.FAILED, up) == SessionStatus.FAILED
        assert (
            hosted_status(SessionStatus.RUNNING, SessionStatus.STOPPING) == SessionStatus.STOPPING
        )
        assert hosted_status(SessionStatus.RUNNING, SessionStatus.FAILED) == SessionStatus.FAILED
        assert hosted_status(SessionStatus.RUNNING, SessionStatus.STOPPED) == SessionStatus.STOPPED
        assert hosted_status(SessionStatus.RUNNING, SessionStatus.ARCHIVED) == SessionStatus.STOPPED
        assert hosted_status(SessionStatus.ARCHIVED, up) == SessionStatus.ARCHIVED

    def test_a_session_is_only_shown_by_its_own_unit(self):
        unit = Session(name="u")
        stranger = Session(name="m", hosted_by=uuid4(), hosted_port=8082)

        with pytest.raises(ValueError, match="not a member"):
            stranger.hosted_in(unit)


class TestStop:
    async def test_stopping_the_root_stops_the_unit_and_every_member(self, unit: Unit):
        root = await unit.root()
        coder = await unit.member(root, "coder-1")
        reviewer = await unit.member(root, "reviewer-1", member="reviewer")
        await unit.start(root)

        stopped = await unit.forge.stop_session(root.id, principal=PRINCIPAL)

        assert stopped.status == SessionStatus.STOPPED
        assert [s.id for s in unit.app.pod_manager.stop_calls] == [root.id]
        for member in (coder, reviewer):
            row = await unit.stored(member)
            assert row.status == SessionStatus.STOPPED
            assert row.chat_endpoint is None
            assert (await unit.forge.get_session(member.id)).status == SessionStatus.STOPPED

    async def test_stopping_one_member_leaves_the_unit_and_the_others_running(self, unit: Unit):
        root = await unit.root()
        coder = await unit.member(root, "coder-1")
        reviewer = await unit.member(root, "reviewer-1", member="reviewer")
        await unit.start_and_run(root)

        stopped = await unit.forge.stop_session(coder.id, principal=PRINCIPAL)

        assert stopped.status == SessionStatus.STOPPED
        # The runtime stopped the member's own process; the unit's pod was not touched.
        assert [s.id for s in unit.app.pod_manager.stop_calls] == [coder.id]
        assert (await unit.forge.get_session(root.id)).status == SessionStatus.RUNNING
        assert (await unit.forge.get_session(reviewer.id)).status == SessionStatus.RUNNING
        assert (await unit.forge.get_session(coder.id)).status == SessionStatus.STOPPED

    async def test_a_member_the_runtime_cannot_confirm_stopped_is_a_conflict_not_a_server_error(
        self, unit: Unit
    ):
        root = await unit.root()
        coder = await unit.member(root, "coder-1")
        await unit.start_and_run(root)

        async def refuse(member):
            raise HostedUnitError(f"its Skuld could not be asked to stop {member.id}")

        unit.app.pod_manager.stop_hosted_member = refuse

        with pytest.raises(HostedMemberError, match="could not be asked to stop") as error:
            await unit.forge.stop_session(coder.id, principal=PRINCIPAL)

        assert isinstance(error.value, SessionStateError)  # the session routes answer it 409
        # The member is as it was: not recorded stopped while its process may still run.
        assert (await unit.forge.get_session(coder.id)).status == SessionStatus.RUNNING

    async def test_a_stopped_member_cannot_be_stopped_again(self, unit: Unit):
        root = await unit.root()
        coder = await unit.member(root, "coder-1")
        await unit.start(root)
        await unit.forge.stop_session(coder.id, principal=PRINCIPAL)

        with pytest.raises(SessionStateError):
            await unit.forge.stop_session(coder.id, principal=PRINCIPAL)

    async def test_a_stopped_member_starts_again_under_a_new_launch_in_its_running_unit(
        self, unit: Unit
    ):
        root = await unit.root()
        coder = await unit.member(root, "coder-1")
        await unit.start_and_run(root)
        first = launch_id_of((await unit.stored(coder)).workload_config)
        await unit.forge.stop_session(coder.id, principal=PRINCIPAL)
        restarted_with = []

        async def restart(member):
            restarted_with.append(launch_id_of(member.workload_config))

        unit.app.pod_manager.restart_hosted_member = restart

        restarted = await unit.forge.start_session(coder.id, principal=PRINCIPAL)

        assert restarted.status == SessionStatus.RUNNING
        second = launch_id_of((await unit.stored(coder)).workload_config)
        assert second != first
        assert restarted_with == [second]  # the runtime was asked to restart it, under that launch
        assert len(unit.app.pod_manager.start_calls) == 1  # still the unit's one start

    async def test_a_restart_the_runtime_cannot_do_fails_loudly_and_changes_nothing(
        self, unit: Unit
    ):
        root = await unit.root()
        coder = await unit.member(root, "coder-1")
        await unit.start_and_run(root)
        await unit.forge.stop_session(coder.id, principal=PRINCIPAL)
        before = await unit.stored(coder)

        with pytest.raises(HostedUnitError, match="cannot start one member of a unit again"):
            await unit.forge.start_session(coder.id, principal=PRINCIPAL)

        after = await unit.stored(coder)
        assert (after.status, launch_id_of(after.workload_config)) == (
            SessionStatus.STOPPED,
            launch_id_of(before.workload_config),
        )

    async def test_a_running_member_is_not_started_again(self, unit: Unit):
        root = await unit.root()
        coder = await unit.member(root, "coder-1")
        await unit.start(root)

        with pytest.raises(SessionStateError):
            await unit.forge.start_session(coder.id, principal=PRINCIPAL)

    async def test_stopping_the_unit_keeps_why_a_failed_member_failed(self, unit: Unit):
        root = await unit.root()
        coder = await unit.member(root, "coder-1")
        await unit.start(root)
        stored = await unit.stored(coder)
        await unit.app.sessions.update(
            stored.model_copy(update={"status": SessionStatus.FAILED, "error": "harness exited"})
        )

        await unit.forge.stop_session(root.id, principal=PRINCIPAL)

        row = await unit.stored(coder)
        assert row.status == SessionStatus.FAILED
        assert row.error == "harness exited"

    async def test_a_unit_stopped_and_started_again_starts_its_members_again(self, unit: Unit):
        root = await unit.root()
        coder = await unit.member(root, "coder-1")
        await unit.start_and_run(root)
        first = launch_id_of((await unit.stored(coder)).workload_config)
        await unit.forge.stop_session(root.id, principal=PRINCIPAL)

        await unit.start_and_run(root)

        assert (await unit.forge.get_session(coder.id)).status == SessionStatus.RUNNING
        assert launch_id_of((await unit.stored(coder)).workload_config) != first
        assert len(unit.app.pod_manager.start_calls) == 2


class TestOwnTranscriptAndCredential:
    async def test_each_member_has_its_own_credential_bound_to_its_own_session(self, unit: Unit):
        root = await unit.root()
        coder = await unit.member(root, "coder-1")
        reviewer = await unit.member(root, "reviewer-1", member="reviewer")
        await unit.start(root)
        tokens = unit.app.tokens

        issued = {
            s.id: await unit.app.session_service.issue_forge_session(s.id)
            for s in (root, coder, reviewer)
        }

        assert all(c is not None for c in issued.values())
        assert len({c.token for c in issued.values()}) == 3
        for session_id, credential in issued.items():
            verified = tokens.verify(credential.token)
            assert verified.session_id == str(session_id)
            assert verified.launch_id == launch_id_of(
                (await unit.app.sessions.get(session_id)).workload_config
            )
        assert len({c.launch_id for c in issued.values()}) == 3

    async def test_stopping_one_member_ends_its_launch_and_leaves_the_others(self, unit: Unit):
        root = await unit.root()
        coder = await unit.member(root, "coder-1")
        reviewer = await unit.member(root, "reviewer-1", member="reviewer")
        await unit.start_and_run(root)
        before = await unit.stored(coder)

        await unit.forge.stop_session(coder.id, principal=PRINCIPAL)

        assert (await unit.stored(coder)).status == SessionStatus.STOPPED
        assert launch_id_of((await unit.stored(coder)).workload_config) == launch_id_of(
            before.workload_config
        )
        assert (await unit.stored(reviewer)).status == SessionStatus.STARTING
        assert (await unit.forge.get_session(reviewer.id)).status == SessionStatus.RUNNING

    async def test_members_keep_their_own_transcript_log_and_outcome_state(self, unit: Unit):
        root = await unit.root()
        coder = await unit.member(root, "coder-1")
        reviewer = await unit.member(root, "reviewer-1", member="reviewer")
        await unit.start(root)

        for session in (coder, reviewer):
            stored = await unit.stored(session)
            await unit.app.sessions.update(
                stored.model_copy(
                    update={"message_count": 3, "cli_session_id": f"cli-{stored.name}"}
                )
            )

        coder_row, reviewer_row = await unit.stored(coder), await unit.stored(reviewer)
        assert (coder_row.cli_session_id, reviewer_row.cli_session_id) == (
            "cli-coder-1",
            "cli-reviewer-1",
        )
        assert coder_row.hosted_by == reviewer_row.hosted_by == root.id  # update kept hosting
        assert coder_row.workload_config["workflow_member"] == "coder"
        assert reviewer_row.workload_config["workflow_member"] == "reviewer"


class TestEndingMembers:
    async def test_a_member_whose_process_was_not_stopped_is_not_recorded_as_stopped(
        self, unit: Unit
    ):
        root = await unit.root()
        coder = await unit.member(root, "coder-1")
        await unit.start_and_run(root)
        unit.app.pod_manager.stop_success = False

        with pytest.raises(RuntimeError, match="Pod stop failed"):
            await unit.forge.stop_session(coder.id, principal=PRINCIPAL)

        assert (await unit.forge.get_session(coder.id)).status == SessionStatus.RUNNING

    async def test_a_runtime_that_answers_false_for_a_member_stop_is_an_error(self, unit: Unit):
        root = await unit.root()
        coder = await unit.member(root, "coder-1")
        await unit.start_and_run(root)

        async def stop(member):
            return False

        unit.app.pod_manager.stop = stop

        with pytest.raises(HostedMemberError, match="did not stop member"):
            await unit.forge.stop_session(coder.id, principal=PRINCIPAL)

        assert (await unit.forge.get_session(coder.id)).status == SessionStatus.RUNNING

    async def test_a_member_of_a_unit_that_failed_is_ended_without_asking_its_gone_skuld(
        self, unit: Unit
    ):
        root = await unit.root()
        coder = await unit.member(root, "coder-1")
        await unit.start_and_run(root)
        failed_root = await unit.stored(root)
        await unit.app.sessions.update(
            failed_root.model_copy(
                update={"status": SessionStatus.FAILED, "error": "the unit's pod never came up"}
            )
        )
        # The unit's pod is gone, so no runtime can stop the member's process.
        unit.app.pod_manager.stop_success = False

        ended = await unit.forge.stop_session(coder.id, principal=PRINCIPAL)

        assert unit.app.pod_manager.stop_calls == []
        assert ended.status == SessionStatus.FAILED
        assert ended.error == "the unit's pod never came up"
        stored = await unit.stored(coder)
        assert stored.status == SessionStatus.FAILED
        assert stored.chat_endpoint is None

    async def test_stopping_the_unit_removes_every_members_credential_secret(self):
        removed = []

        class Projection:
            def supports(self, runtime_backend):
                return True

            async def remove(self, session_id):
                removed.append(session_id)

        unit = Unit(forge_session_projection=Projection())
        root = await unit.root()
        coder = await unit.member(root, "coder-1")
        reviewer = await unit.member(root, "reviewer-1", member="reviewer")
        await unit.start_and_run(root)

        await unit.forge.stop_session(root.id, principal=PRINCIPAL)

        assert sorted(removed, key=str) == sorted([root.id, coder.id, reviewer.id], key=str)

    async def test_stopping_one_member_removes_only_its_secret(self):
        removed = []

        class Projection:
            def supports(self, runtime_backend):
                return True

            async def remove(self, session_id):
                removed.append(session_id)

        unit = Unit(forge_session_projection=Projection())
        root = await unit.root()
        coder = await unit.member(root, "coder-1")
        await unit.member(root, "reviewer-1", member="reviewer")
        await unit.start_and_run(root)

        await unit.forge.stop_session(coder.id, principal=PRINCIPAL)

        assert removed == [coder.id]


class TestMemberSource:
    """The one seam every pod manager resolves a unit's members through."""

    async def test_each_member_gets_its_own_spec_and_credential(self, unit: Unit):
        root = await unit.root()
        coder = await unit.member(root, "coder-1", system_prompt="write it")
        reviewer = await unit.member(root, "reviewer-1", member="reviewer")
        await unit.start(root)

        launches = await unit.app.session_service.hosted_member_launches(root.id)

        assert [launch.session.id for launch in launches] == [coder.id, reviewer.id]
        credentials = [launch.spec.forge_session for launch in launches]
        assert all(credentials)
        verified = [unit.app.tokens.verify(c.token) for c in credentials]
        assert [v.session_id for v in verified] == [str(coder.id), str(reviewer.id)]
        assert len({c.launch_id for c in credentials}) == 2

    async def test_one_member_can_be_asked_for_alone(self, unit: Unit):
        root = await unit.root()
        await unit.member(root, "coder-1")
        reviewer = await unit.member(root, "reviewer-1", member="reviewer")
        await unit.start(root)

        launches = await unit.app.session_service.hosted_member_launches(root.id, reviewer.id)

        assert [launch.session.id for launch in launches] == [reviewer.id]

    async def test_a_session_that_is_not_a_member_is_refused(self, unit: Unit):
        root = await unit.root()
        await unit.member(root, "coder-1")
        await unit.start(root)

        with pytest.raises(HostedUnitError, match="is not a member of unit"):
            await unit.app.session_service.hosted_member_launches(root.id, uuid4())

    async def test_a_unit_this_process_did_not_start_has_nobody_to_build_for(self, unit: Unit):
        root = await unit.root()
        await unit.member(root, "coder-1")

        with pytest.raises(HostedUnitError, match="was not started by this Forge process"):
            await unit.app.session_service.hosted_member_launches(root.id)

    async def test_a_member_runs_where_its_unit_was_placed(self, unit: Unit):
        root = await unit.root()
        coder = await unit.member(root, "coder-1")
        await unit.start(root)
        started = await unit.stored(root)
        await unit.app.sessions.update(
            started.model_copy(
                update={
                    "workload_config": {
                        **started.workload_config,
                        "compute_profile": "valhalla-vm",
                        "host_selector": {"pool": "valhalla"},
                    }
                }
            )
        )

        await unit.app.session_service.hosted_member_launches(root.id)

        placed = (await unit.stored(coder)).workload_config
        assert placed["compute_profile"] == "valhalla-vm"
        assert placed["host_selector"] == {"pool": "valhalla"}

    async def test_a_unit_started_again_does_not_send_its_pinned_execution_back(self, unit: Unit):
        root = await unit.root()
        await unit.member(root, "coder-1")
        await unit.start_and_run(root)
        running = await unit.stored(root)
        await unit.app.sessions.update(
            running.model_copy(
                update={
                    "workload_config": {**running.workload_config, "_compute_execution": {"p": 1}}
                }
            )
        )
        await unit.forge.stop_session(root.id, principal=PRINCIPAL)

        await unit.start(root)  # the service refuses a caller that sends the pin itself

        assert len(unit.app.pod_manager.start_calls) == 2

    async def test_the_pod_manager_resolves_the_members_it_recorded(self, unit: Unit):
        root = await unit.root()
        coder = await unit.member(root, "coder-1")
        await unit.start(root)
        pod_manager = unit.app.pod_manager
        pod_manager.configure_hosted_members(unit.app.session_service.hosted_member_launches)

        launches = await pod_manager.hosted_member_launches(await unit.stored(root))
        one = await pod_manager.hosted_member_launch(await unit.stored(coder))

        assert [launch.session.id for launch in launches] == [coder.id]
        assert one.session.id == coder.id

    async def test_a_manager_without_a_source_refuses_a_unit_with_members(self, unit: Unit):
        root = await unit.root()
        await unit.member(root, "coder-1")
        await unit.start(root)
        pod_manager = unit.app.pod_manager
        pod_manager._hosted_members = None

        with pytest.raises(HostedUnitError, match="no member source"):
            await pod_manager.hosted_member_launches(await unit.stored(root))

    async def test_a_source_that_answers_other_members_is_refused(self, unit: Unit):
        root = await unit.root()
        await unit.member(root, "coder-1")
        await unit.start(root)
        pod_manager = unit.app.pod_manager

        async def wrong(unit_id, member_id):
            return []

        pod_manager.configure_hosted_members(wrong)

        with pytest.raises(HostedUnitError, match="fixed when its unit starts"):
            await pod_manager.hosted_member_launches(await unit.stored(root))

    async def test_a_root_with_no_members_needs_no_source(self, unit: Unit):
        root = await unit.root()
        await unit.start(root)

        assert await unit.app.pod_manager.hosted_member_launches(await unit.stored(root)) == ()

    async def test_the_unit_is_started_with_the_principal_that_asked(self, unit: Unit):
        root = await unit.root()
        await unit.member(root, "coder-1")
        await unit.start(root)

        assert unit.app.session_service._unit_principals[root.id] == PRINCIPAL
        await unit.forge.stop_session(root.id, principal=PRINCIPAL)
        assert root.id not in unit.app.session_service._unit_principals

    async def test_an_unknown_unit_has_no_members(self, unit: Unit):
        with pytest.raises(SessionNotFoundError):
            await unit.app.session_service.hosted_member_launches(uuid4())

    async def test_only_a_member_of_a_unit_can_be_restarted(self, unit: Unit):
        plain = await unit.app.session(name="plain")

        with pytest.raises(HostedUnitError, match="is not a member of a unit"):
            await unit.app.session_service.restart_hosted_member(plain, PRINCIPAL)
        with pytest.raises(HostedUnitError, match="is not a member of a unit"):
            await unit.app.pod_manager.hosted_member_launch(plain)
