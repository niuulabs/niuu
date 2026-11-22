"""Whose projects Ting reads team trackers for, with several Forges and with one."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import respx

from niuu.domain.project_team import TeamMembership, TeamMemberships
from ting.adapters.guild_instances import GuildInstanceRegistryClient
from ting.adapters.team_projects import TeamProject, TeamProjects

HOME, COPY, UNKNOWN = uuid4(), uuid4(), uuid4()
LINEAR = uuid4()


def _forges(*project_ids, error=None):
    forges = AsyncMock()
    primary = forges.primary_for_owner.return_value
    primary.list_member_project_ids.return_value = [str(pid) for pid in project_ids]
    primary.list_member_project_ids.side_effect = error
    return forges


def _guild(*member_of, unrecorded=(), error=None):
    guild = AsyncMock()
    guild.memberships.return_value = TeamMemberships(
        user_id="owner-1",
        member_of=[TeamMembership(project_id=p, connection_ids=[LINEAR]) for p in member_of],
        unrecorded=list(unrecorded),
    )
    guild.memberships.side_effect = error
    return guild


async def test_the_guild_knows_projects_the_primary_forge_does_not_have():
    guild = _guild(HOME)
    projects, failures = await TeamProjects(_forges(), guild)("owner-1")
    assert projects == [TeamProject(str(HOME), frozenset({str(LINEAR)}))]
    assert projects[0].has(str(LINEAR)) and not projects[0].has("another")
    assert failures == []


async def test_a_copy_on_the_primary_forge_does_not_make_anyone_a_member():
    # The primary lists both; the Guild records COPY without this person.
    guild = _guild(unrecorded=[UNKNOWN])
    projects, _ = await TeamProjects(_forges(COPY, UNKNOWN), guild)("owner-1")
    guild.memberships.assert_awaited_once_with("owner-1", [str(COPY), str(UNKNOWN)])
    assert projects == [TeamProject(str(UNKNOWN))]
    assert projects[0].has("anything")


async def test_one_forge_without_a_guild_answers_for_itself():
    projects, failures = await TeamProjects(_forges(HOME), None)("owner-1")
    assert (projects, failures) == ([TeamProject(str(HOME))], [])


async def test_what_could_not_be_asked_is_reported(caplog):
    down = ConnectionError("down")
    projects, failures = await TeamProjects(_forges(error=down), _guild(HOME))("owner-1")
    assert len(projects) == 1
    assert "primary Forge could not list" in failures[0]
    projects, failures = await TeamProjects(_forges(HOME), _guild(error=down))("owner-1")
    assert projects == []
    assert "the Guild could not list" in failures[0]
    forges = AsyncMock()
    forges.primary_for_owner.return_value = None
    projects, failures = await TeamProjects(forges, None)("owner-1")
    assert projects == [] and "connect a Forge" in failures[0]


@respx.mock
async def test_the_guild_must_answer_for_the_owner_asked_about():
    client = GuildInstanceRegistryClient("https://guild.test")
    route = respx.post("https://guild.test/api/v1/forge/projects/memberships")
    answer = {"user_id": "owner-1", "member_of": [], "unrecorded": [str(HOME)]}
    route.respond(json=answer)
    known = await client.memberships("owner-1", [str(HOME)])
    assert known.unrecorded == [HOME]
    assert route.calls.last.request.headers["x-auth-user-id"] == "owner-1"
    route.respond(json={**answer, "user_id": "ting"})
    with pytest.raises(PermissionError, match="guild_registry.auth"):
        await client.memberships("owner-1", [])
    route.respond(405)
    with pytest.raises(httpx.HTTPStatusError):
        await client.memberships("owner-1", [])
    await client.close()
