"""Tests for TeamRoomContributor: a team's root hosts its room, its members join the realm."""

from uuid import UUID

import pytest

from volundr.adapters.outbound.contributors.team_room import TeamRoomContributor
from volundr.domain.models import GitSource, Session
from volundr.domain.ports import SessionContext, UnitMemberContext
from volundr.domain.team import TEAM_KEY, TEAM_MEMBER_KEY, team_realm

ROOT_ID = UUID("7a3f0b1e-2d5c-4e8a-9b6d-1c2e3f4a5b6c")
REALM = f"team-{ROOT_ID}"


def _session(hosted_by: UUID | None = None, session_id: UUID = ROOT_ID) -> Session:
    return Session(id=session_id, name="team", model="gpt", source=GitSource(), hosted_by=hosted_by)


def _unit_member(session_id: str, lead: bool) -> UnitMemberContext:
    return UnitMemberContext(
        session_id=session_id,
        owner_id="ada",
        tenant_id="acme",
        workload_config={TEAM_MEMBER_KEY: {"lead": lead}},
    )


def _root_context(task_to: str | None = "lead", members=None) -> SessionContext:
    unit_members = (
        (_unit_member("m1", True), _unit_member("m2", False)) if members is None else members
    )
    return SessionContext(
        workload_config={TEAM_KEY: {"task_to": task_to}}, unit_members=tuple(unit_members)
    )


def _member_context(**extra) -> SessionContext:
    return SessionContext(workload_config={TEAM_MEMBER_KEY: {"lead": False}, **extra})


async def test_the_root_hosts_the_teams_routed_room_and_sends_untargeted_messages_to_everyone():
    result = await TeamRoomContributor().contribute(_session(), _root_context())

    assert result.values == {
        "team": {"enabled": True},
        "mesh": {"enabled": True, "realmId": REALM},
        "room": {
            "enabled": True,
            "environmentId": REALM,
            "realmRoom": True,
            "routed": True,
            "untargetedDelivery": "everyone",
        },
    }


async def test_a_root_started_without_a_task_hosts_the_same_room():
    result = await TeamRoomContributor().contribute(_session(), _root_context(task_to=None))

    assert result.values["room"]["routed"] is True


async def test_a_member_joins_the_roots_realm_and_shares_its_work():
    result = await TeamRoomContributor().contribute(_session(hosted_by=ROOT_ID), _member_context())

    assert result.values == {
        "team": {"enabled": True},
        "mesh": {"enabled": True, "realmId": REALM, "shareWork": True},
    }


async def test_a_member_is_not_a_workflow_step_host_and_hosts_no_room():
    result = await TeamRoomContributor().contribute(_session(hosted_by=ROOT_ID), _member_context())

    assert "workflowSteps" not in result.values
    assert "room" not in result.values


async def test_a_member_announces_its_persona_and_topics_from_its_pinned_persona():
    document = {
        "schema_version": 1,
        "id": "reviewer",
        "revision": "r1",
        "definition": {
            "name": "reviewer",
            "consumes": {"event_types": ["code.changed"]},
            "produces": {"event_type": "review.passed"},
        },
    }

    result = await TeamRoomContributor().contribute(
        _session(hosted_by=ROOT_ID), _member_context(persona_definition=document)
    )

    assert result.values["mesh"]["announce"] == {
        "persona": "reviewer",
        "consumes": ["code.changed"],
        "emits": ["code.changed", "review.passed"],
    }


async def test_a_member_without_a_persona_announces_none():
    result = await TeamRoomContributor().contribute(_session(hosted_by=ROOT_ID), _member_context())

    assert "announce" not in result.values["mesh"]


async def test_the_realm_is_the_roots_session_id_for_both_root_and_member():
    other = UUID("0d1c2b3a-4f5e-4d6c-8b7a-9a8b7c6d5e4f")

    root = await TeamRoomContributor().contribute(_session(session_id=other), _root_context())
    member = await TeamRoomContributor().contribute(
        _session(hosted_by=other, session_id=ROOT_ID), _member_context()
    )

    assert (
        root.values["mesh"]["realmId"] == member.values["mesh"]["realmId"] == team_realm(str(other))
    )


async def test_an_ordinary_session_gets_nothing():
    result = await TeamRoomContributor().contribute(_session(), SessionContext())

    assert result.values == {}


async def test_a_workflow_step_host_is_not_a_team():
    context = SessionContext(workload_config={"workflow_step_host": True})

    result = await TeamRoomContributor().contribute(_session(), context)

    assert result.values == {}


def test_the_contributor_is_named_for_its_wiring():
    assert TeamRoomContributor().name == "team_room"


class TestRefusals:
    async def test_a_session_that_is_both_root_and_member_is_refused(self):
        context = SessionContext(
            workload_config={TEAM_KEY: {"task_to": None}, TEAM_MEMBER_KEY: {"lead": True}}
        )

        with pytest.raises(ValueError, match="not both"):
            await TeamRoomContributor().contribute(_session(), context)

    @pytest.mark.parametrize("key", [TEAM_KEY, TEAM_MEMBER_KEY])
    async def test_a_marker_that_is_not_a_mapping_is_refused(self, key: str):
        context = SessionContext(workload_config={key: True})

        with pytest.raises(ValueError, match="must be a mapping"):
            await TeamRoomContributor().contribute(_session(hosted_by=ROOT_ID), context)

    async def test_a_task_target_that_is_not_known_is_refused(self):
        with pytest.raises(ValueError, match="task_to is 'nobody'"):
            await TeamRoomContributor().contribute(_session(), _root_context(task_to="nobody"))

    async def test_a_root_with_no_members_is_refused(self):
        with pytest.raises(ValueError, match="hosts no member sessions"):
            await TeamRoomContributor().contribute(_session(), _root_context(members=()))

    async def test_a_root_hosting_a_session_that_is_not_a_team_member_is_refused(self):
        stranger = UnitMemberContext(session_id="s9", owner_id="ada", tenant_id="acme")

        with pytest.raises(ValueError, match="not team members \\(s9\\)"):
            await TeamRoomContributor().contribute(
                _session(), _root_context(members=(_unit_member("m1", True), stranger))
            )

    @pytest.mark.parametrize(
        ("leads", "count"), [((False, False), 0), ((True, True), 2)], ids=["none", "two"]
    )
    async def test_a_root_without_exactly_one_lead_is_refused(self, leads, count):
        members = tuple(_unit_member(f"m{i}", lead) for i, lead in enumerate(leads))

        with pytest.raises(ValueError, match=f"{count} lead members"):
            await TeamRoomContributor().contribute(_session(), _root_context(members=members))

    async def test_a_member_outside_a_unit_is_refused(self):
        with pytest.raises(ValueError, match="needs hosted_by"):
            await TeamRoomContributor().contribute(_session(hosted_by=None), _member_context())
