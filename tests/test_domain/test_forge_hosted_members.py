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
    hosted_endpoint,
    hosted_status,
)
from volundr.domain.services.forge import (
    DEFAULT_HOSTED_MEMBER_BASE_PORT,
    ForgeService,
    HostedMemberError,
    HostedUnitInUseError,
)
from volundr.domain.services.forge_session_launch import launch_id_of
from volundr.domain.services.session import SessionStateError

RUN = uuid4()
PRINCIPAL = Principal(user_id=OWNER, email="owner@example.com", tenant_id=TENANT, roles=[])
OTHER = Principal(user_id="someone-else", email="x@example.com", tenant_id=TENANT, roles=[])


class Unit:
    """A Forge app plus the ForgeService on top of its session service."""

    def __init__(self, base_port: int = DEFAULT_HOSTED_MEMBER_BASE_PORT) -> None:
        self.app: ForgeApp = build_forge_app()
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
                **extra,
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

        with pytest.raises(HostedMemberError, match="does not exist"):
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

    async def test_a_members_endpoint_is_the_units_address_and_its_port(self, unit: Unit):
        root = await unit.root()
        coder = await unit.member(root, "coder-1")
        reviewer = await unit.member(root, "reviewer-1", member="reviewer")
        await unit.start(root)

        unit_session = await unit.forge.get_session(root.id)
        coder_view = await unit.forge.get_session(coder.id)
        reviewer_view = await unit.forge.get_session(reviewer.id)

        assert unit_session.chat_endpoint == "wss://chat.example.com/session"
        assert coder_view.chat_endpoint == f"wss://chat.example.com:{coder.hosted_port}/session"
        assert (
            reviewer_view.chat_endpoint == f"wss://chat.example.com:{reviewer.hosted_port}/session"
        )
        assert coder_view.code_endpoint is None

    async def test_every_route_that_reaches_a_session_gets_the_members_own_target(self, unit: Unit):
        root = await unit.root()
        coder = await unit.member(root, "coder-1")
        await unit.start(root)

        session, base = await unit.forge.get_session_proxy_target(coder.id)

        assert session.id == coder.id
        assert base == f"https://chat.example.com:{coder.hosted_port}"
        authorized = await unit.forge.get_authorized_session(
            coder.id, principal=PRINCIPAL, action="read"
        )
        assert authorized.chat_endpoint == f"wss://chat.example.com:{coder.hosted_port}/session"

    async def test_listing_shows_members_as_their_unit_shows_them(self, unit: Unit):
        root = await unit.root()
        coder = await unit.member(root, "coder-1")
        await unit.start_and_run(root)

        listed = {s.id: s for s in await unit.forge.list_sessions(principal=PRINCIPAL)}

        assert listed[coder.id].status == SessionStatus.RUNNING
        assert listed[coder.id].chat_endpoint.endswith(f":{coder.hosted_port}/session")

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

    def test_endpoint_derivation(self):
        assert hosted_endpoint("ws://10.0.0.5:8081/session", 8083) == "ws://10.0.0.5:8083/session"
        assert hosted_endpoint("wss://host/s/abc/session", 8082) == "wss://host:8082/s/abc/session"
        assert hosted_endpoint("ws://[::1]:8081/session", 8082) == "ws://[::1]:8082/session"
        with pytest.raises(ValueError, match="no host"):
            hosted_endpoint("/s/abc/session", 8082)

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
        assert unit.app.pod_manager.stop_calls == []  # no pod to stop for a member
        assert (await unit.forge.get_session(root.id)).status == SessionStatus.RUNNING
        assert (await unit.forge.get_session(reviewer.id)).status == SessionStatus.RUNNING
        assert (await unit.forge.get_session(coder.id)).status == SessionStatus.STOPPED

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

        restarted = await unit.forge.start_session(coder.id, principal=PRINCIPAL)

        assert restarted.status == SessionStatus.RUNNING
        assert launch_id_of((await unit.stored(coder)).workload_config) != first
        assert len(unit.app.pod_manager.start_calls) == 1  # still the unit's one start

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
