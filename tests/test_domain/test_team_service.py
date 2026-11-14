"""Starting a team: the unit it creates, what each agent is told, and all or nothing."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from tests.support.team_rig import OWNER_PRINCIPAL, TeamRig, launch
from volundr.adapters.outbound.contributors.team_room import TeamRoomContributor
from volundr.domain.models import HOSTED_LAUNCH_KEY, SessionStatus
from volundr.domain.ports import PersonaNotPlayableError
from volundr.domain.services.forge import HostedUnitInUseError
from volundr.domain.services.forge_session_launch import FORGE_MCP_KEY, GRANTS_KEY
from volundr.domain.services.team import (
    TEAM_RUNTIME_BACKENDS,
    TeamError,
    TeamMemberSpec,
    TeamRollbackError,
    TeamRuntimeError,
)
from volundr.domain.session_participants import ParticipantRole
from volundr.domain.team import TEAM_KEY, TEAM_MEMBER_KEY

TASK = "Fix the flaky login test."


@pytest.fixture
def rig() -> TeamRig:
    return TeamRig()


async def _start(rig: TeamRig, **fields):
    team = await rig.service.start_team(launch(**fields), OWNER_PRINCIPAL)
    await rig.settle()
    return team


async def _sessions(rig: TeamRig) -> list:
    return await rig.repository.list()


def _held(session) -> dict:
    return session.workload_config[HOSTED_LAUNCH_KEY]


class TestTheUnit:
    async def test_the_root_hosts_the_agents_and_runs_none(self, rig: TeamRig):
        team = await _start(rig)

        assert team.root.name == "ship-it"
        assert team.root.is_unit_root
        assert team.root.workload_config[TEAM_KEY] == {"task_to": None}
        assert [m.hosted_by for m in team.members] == [team.root.id, team.root.id]
        assert [m.name for m in team.members] == ["ship-it-1", "ship-it-2"]
        assert [m.workload_config[TEAM_MEMBER_KEY] for m in team.members] == [
            {"lead": True},
            {"lead": False},
        ]

    async def test_the_unit_starts_once_with_every_member(self, rig: TeamRig):
        team = await _start(rig)

        assert [session.id for session, _ in rig.pod_manager.start_calls] == [team.root.id]
        stored = await rig.repository.list_hosted(team.root.id)
        assert {m.id for m in stored} == {m.id for m in team.members}

    async def test_the_roots_session_record_takes_the_leads_engine(self, rig: TeamRig):
        team = await _start(rig)

        assert team.root.model == "claude-sonnet-4-6"
        assert team.root.session_definition == "skuldClaude"

    async def test_each_agent_plays_the_persona_the_catalog_gave_it(self, rig: TeamRig):
        team = await _start(rig)

        lead, worker = team.members
        assert lead.workload_config["persona_definition"]["id"] == "architect"
        assert worker.workload_config["persona_definition"]["id"] == "coder"
        assert (lead.model, worker.model) == ("claude-sonnet-4-6", "gpt-5")
        assert (lead.session_definition, worker.session_definition) == (
            "skuldClaude",
            "skuldCodex",
        )

    async def test_an_agent_without_a_persona_plays_none(self, rig: TeamRig):
        members = [TeamMemberSpec(model="gpt-5", lead=True)]

        team = await _start(rig, members=members)

        assert "persona_definition" not in team.members[0].workload_config

    async def test_members_share_the_teams_checkout_and_launch_settings(self, rig: TeamRig):
        team = await _start(
            rig,
            credential_names=["github"],
            integration_ids=["i-1"],
            terminal_restricted=True,
            resource_config={"cpu": "2"},
        )

        for session in (team.root, *team.members):
            assert session.source.repo == "https://github.com/acme/repo"
        for member in team.members:
            held = _held(member)
            assert held["credential_names"] == ["github"]
            assert held["integration_ids"] == ["i-1"]
            assert held["terminal_restricted"] is True
            assert held["resource_config"] == {"cpu": "2"}

    async def test_a_long_team_name_still_gives_valid_member_names(self, rig: TeamRig):
        name = "a" * 62 + "b"

        team = await _start(rig, name=name)

        assert [m.name for m in team.members] == ["a" * 61 + "-1", "a" * 61 + "-2"]

    async def test_a_name_that_would_end_in_a_hyphen_is_cut_before_it(self, rig: TeamRig):
        name = "a" * 60 + "-" * 3 + "b"

        team = await _start(rig, name=name)

        assert team.members[0].name == "a" * 60 + "-1"


class TestTheOpeningTask:
    async def test_no_task_gives_no_agent_a_prompt_and_records_no_target(self, rig: TeamRig):
        team = await _start(rig)

        assert team.root.workload_config[TEAM_KEY] == {"task_to": None}
        assert [_held(m)["initial_prompt"] for m in team.members] == ["", ""]

    async def test_a_task_to_the_lead_is_only_the_leads_prompt(self, rig: TeamRig):
        team = await _start(rig, task=TASK, task_to="lead")

        assert team.root.workload_config[TEAM_KEY] == {"task_to": "lead"}
        assert [_held(m)["initial_prompt"] for m in team.members] == [TASK, ""]

    async def test_a_task_to_everyone_is_every_agents_prompt(self, rig: TeamRig):
        team = await _start(rig, task=TASK, task_to="everyone")

        assert team.root.workload_config[TEAM_KEY] == {"task_to": "everyone"}
        assert [_held(m)["initial_prompt"] for m in team.members] == [TASK, TASK]

    async def test_the_root_runs_no_agent_so_it_is_given_no_prompt(self, rig: TeamRig):
        team = await _start(rig, task=TASK, task_to="everyone")

        unit = team.root.workload_config["hosted_unit"]
        assert (unit["initial_prompt"], unit["system_prompt"]) == ("", "")


class TestWhatEachAgentMayDo:
    async def test_every_agent_may_speak_in_the_room_and_message_its_teammates(self, rig: TeamRig):
        team = await _start(rig)

        for member in team.members:
            assert member.workload_config[FORGE_MCP_KEY][GRANTS_KEY] == ["message"]

    async def test_the_room_itself_is_given_no_grant(self, rig: TeamRig):
        team = await _start(rig)

        assert team.root.workload_config[FORGE_MCP_KEY][GRANTS_KEY] == []


class TestWhatEachAgentIsTold:
    async def test_it_names_the_team_its_agents_their_engines_and_who_leads(self, rig: TeamRig):
        team = await _start(rig)

        prompt = _held(team.members[1])["system_prompt"]
        assert "You are ship-it-2" in prompt
        assert "- ship-it-1 (lead): persona Architect, engine claude-sonnet-4-6" in prompt
        assert "- ship-it-2 (you): persona Coder, engine gpt-5" in prompt
        assert "ship-it-1 leads the team" in prompt

    async def test_the_lead_is_told_it_leads(self, rig: TeamRig):
        team = await _start(rig)

        prompt = _held(team.members[0])["system_prompt"]
        assert "- ship-it-1 (you) (lead)" in prompt
        assert "You lead the team" in prompt

    async def test_it_names_the_tools_that_reach_a_teammate(self, rig: TeamRig):
        team = await _start(rig)

        prompt = _held(team.members[1])["system_prompt"]
        assert "`mesh_send`" in prompt and "`mesh_peers`" in prompt and "`mesh_ask`" in prompt
        assert "`peer`" in prompt and "`peer_id`" in prompt

    async def test_it_says_they_share_one_checkout_and_how_to_speak_in_the_room(self, rig: TeamRig):
        team = await _start(rig)

        prompt = _held(team.members[1])["system_prompt"]
        assert "one checkout of the repository, on one branch" in prompt
        assert f'`room_post` with `room_id` "team-{team.root.id}"' in prompt

    async def test_an_agent_without_a_persona_is_listed_as_having_none(self, rig: TeamRig):
        members = [TeamMemberSpec(model="gpt-5", lead=True)]

        team = await _start(rig, members=members)

        assert "persona none, engine gpt-5" in _held(team.members[0])["system_prompt"]


class TestThePeople:
    async def test_each_invitee_is_a_viewer_of_the_root(self, rig: TeamRig):
        team = await _start(rig, invitees=["bo", "cy"])

        grants = await rig.grants.list_for_session(team.root.id)
        assert {(g.user_id, g.role) for g in grants} == {
            ("bo", ParticipantRole.VIEWER),
            ("cy", ParticipantRole.VIEWER),
        }

    async def test_a_team_without_invitees_invites_no_one(self, rig: TeamRig):
        team = await _start(rig)

        assert await rig.grants.list_for_session(team.root.id) == []


class TestRefusedBeforeAnythingIsCreated:
    @pytest.mark.parametrize(
        ("fields", "named"),
        [
            ({"members": []}, "at least one member"),
            ({"members": [TeamMemberSpec(model="gpt-5")]}, "0 members are marked lead"),
            (
                {
                    "members": [
                        TeamMemberSpec(model="a", lead=True),
                        TeamMemberSpec(model="b", lead=True),
                    ]
                },
                "2 members are marked lead",
            ),
            ({"members": [TeamMemberSpec(model=" ", lead=True)]}, "member 1 has no model"),
            ({"task": TASK}, "needs task_to"),
            ({"task": "   ", "task_to": "lead"}, "task_to is only for a team started"),
            ({"task_to": "everyone"}, "task_to is only for a team started"),
            ({"task": TASK, "task_to": "nobody"}, "not one of lead, everyone"),
            ({"invitees": ["bo", "bo"]}, "lists a person twice"),
            (
                {"members": [TeamMemberSpec(model="a", lead=True, persona_name="ghost")]},
                "'ghost' was not found",
            ),
            (
                {"members": [TeamMemberSpec(model="a", lead=True, persona_name="bare")]},
                "carries no pinned document",
            ),
        ],
    )
    async def test_an_invalid_team_is_refused_naming_the_fix(
        self, rig: TeamRig, fields: dict, named: str
    ):
        with pytest.raises(TeamError, match=named):
            await rig.service.start_team(launch(**fields), OWNER_PRINCIPAL)

        assert await _sessions(rig) == []
        assert rig.pod_manager.start_calls == []

    async def test_a_persona_by_name_needs_a_catalog(self):
        rig = TeamRig(persona_provider=None)

        with pytest.raises(TeamError, match="no persona catalog"):
            await rig.service.start_team(launch(), OWNER_PRINCIPAL)

        assert await _sessions(rig) == []

    @pytest.mark.parametrize("backend", ["docker", "something-else"])
    async def test_a_runtime_that_cannot_host_the_room_is_refused_by_name(self, backend: str):
        rig = TeamRig(runtime_backend=backend)

        with pytest.raises(TeamRuntimeError, match=f"{backend!r} runtime") as refused:
            await rig.service.start_team(launch(), OWNER_PRINCIPAL)

        assert "kubernetes, openshell, process, vm" in str(refused.value)
        assert await _sessions(rig) == []

    @pytest.mark.parametrize("backend", sorted(TEAM_RUNTIME_BACKENDS))
    async def test_every_runtime_that_hosts_a_team_starts_one(self, backend: str):
        rig = TeamRig(runtime_backend=backend)

        team = await rig.service.start_team(launch(), OWNER_PRINCIPAL)
        await rig.settle()

        assert len(team.members) == 2

    async def test_invitees_are_refused_where_a_grant_could_never_attach(self):
        rig = TeamRig(invitations_refused="its pods authorize attach by ownership only.")

        with pytest.raises(TeamRuntimeError, match="authorize attach by ownership"):
            await rig.service.start_team(launch(invitees=["bo"]), OWNER_PRINCIPAL)

        assert await _sessions(rig) == []

    async def test_such_a_runtime_still_starts_a_team_without_invitees(self):
        rig = TeamRig(invitations_refused="its pods authorize attach by ownership only.")

        team = await rig.service.start_team(launch(), OWNER_PRINCIPAL)
        await rig.settle()

        assert team.root.is_unit_root


class TestAllOrNothing:
    async def test_an_agent_that_cannot_be_played_removes_the_root_and_earlier_members(self):
        rig = TeamRig(refused_models={"gpt-5": "no harness plays this persona"})

        with pytest.raises(PersonaNotPlayableError, match="no harness plays"):
            await rig.service.start_team(launch(), OWNER_PRINCIPAL)

        assert await _sessions(rig) == []
        assert rig.pod_manager.start_calls == []

    async def test_an_invitee_who_cannot_be_invited_removes_everything(self, rig: TeamRig):
        with pytest.raises(TeamError, match="cannot invite user 'zed'"):
            await rig.service.start_team(launch(invitees=["bo", "zed"]), OWNER_PRINCIPAL)

        assert await _sessions(rig) == []
        assert rig.pod_manager.start_calls == []
        assert await rig.grants.list_invited_for_user("bo") == []

    async def test_a_unit_that_cannot_start_leaves_nothing_and_raises_the_failure(
        self, rig: TeamRig
    ):
        rig.forge.start_session = AsyncMock(side_effect=RuntimeError("no node can host the pod"))

        with pytest.raises(RuntimeError, match="no node can host the pod"):
            await rig.service.start_team(launch(invitees=["bo"]), OWNER_PRINCIPAL)

        assert await _sessions(rig) == []
        assert await rig.grants.list_invited_for_user("bo") == []

    async def test_a_rollback_that_cannot_finish_says_what_remains(self, rig: TeamRig):
        rig.forge.start_session = AsyncMock(side_effect=RuntimeError("no node can host the pod"))
        real_delete = rig.forge.delete_session

        async def stuck_root(session_id, **kwargs):
            if (await rig.repository.get(session_id)).is_unit_root:
                raise RuntimeError("the database refused the delete")
            return await real_delete(session_id, **kwargs)

        rig.forge.delete_session = stuck_root

        with pytest.raises(TeamRollbackError, match="no node can host the pod") as failed:
            await rig.service.start_team(launch(), OWNER_PRINCIPAL)

        assert "root session" in str(failed.value)
        assert "the database refused the delete" in str(failed.value)
        assert isinstance(failed.value.__cause__, RuntimeError)
        assert [s.is_unit_root for s in await _sessions(rig)] == [True]

    async def test_an_invitation_that_cannot_be_withdrawn_is_named(self, rig: TeamRig):
        rig.forge.start_session = AsyncMock(side_effect=RuntimeError("no node can host the pod"))
        rig.participants.revoke = AsyncMock(side_effect=RuntimeError("feed is down"))

        with pytest.raises(TeamRollbackError, match=r"invitation of bo \(feed is down\)"):
            await rig.service.start_team(launch(invitees=["bo"]), OWNER_PRINCIPAL)

        assert await _sessions(rig) == []

    async def test_a_root_that_was_never_created_has_nothing_to_remove(self, rig: TeamRig):
        rig.forge.create_and_start_session = AsyncMock(side_effect=RuntimeError("no slot"))

        with pytest.raises(RuntimeError, match="no slot"):
            await rig.service.start_team(launch(), OWNER_PRINCIPAL)

        assert await _sessions(rig) == []


class TestTheStartedTeam:
    async def test_the_members_are_returned_as_the_unit_shows_them(self, rig: TeamRig):
        team = await rig.service.start_team(launch(), OWNER_PRINCIPAL)
        await rig.settle()

        assert team.root.status in {SessionStatus.STARTING, SessionStatus.PROVISIONING}
        assert all(m.status != SessionStatus.CREATED for m in team.members)

    async def test_a_started_team_cannot_have_its_root_deleted_while_it_hosts_members(
        self, rig: TeamRig
    ):
        team = await _start(rig)

        with pytest.raises(HostedUnitInUseError):
            await rig.forge.delete_session(team.root.id, principal=OWNER_PRINCIPAL)


class TestWhatTheRuntimeIsGiven:
    """With the team contributor in the pipeline, as Forge composes it."""

    async def test_the_root_and_each_member_get_the_teams_realm_and_room(self):
        rig = TeamRig(contributors=[TeamRoomContributor()])

        team = await _start(rig)

        ((root_session, root_spec),) = rig.pod_manager.start_calls
        realm = f"team-{team.root.id}"
        assert root_spec.values["mesh"] == {"enabled": True, "realmId": realm}
        assert root_spec.values["room"]["routed"] is True
        assert root_spec.values["room"]["untargetedDelivery"] == "everyone"
        launches = await rig.session_service.hosted_member_launches(root_session.id)
        assert len(launches) == 2
        for launch_ in launches:
            mesh = launch_.spec.values["mesh"]
            assert (mesh["realmId"], mesh["shareWork"]) == (realm, True)
            assert "workflowSteps" not in launch_.spec.values
        announced = [launch_.spec.values["mesh"].get("announce") for launch_ in launches]
        assert [a["persona"] for a in announced] == ["architect", "coder"]
