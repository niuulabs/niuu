"""One project's team on several Forges, through the real Guild and Forge routes.

Valhalla is the project's home, Noatun has a copy of it, and Ymir does not have it but its
connection store is the Guild's shared one. Every hop is HTTP with the caller's own login.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import httpx
import pytest
import respx
from fastapi import FastAPI

from identity.adapters.cedar import CedarAuthorizationAdapter
from niuu.adapters.inbound.rest_volundr import create_volundr_router
from niuu.adapters.memory_credential_store import MemoryCredentialStore
from niuu.domain.guild_membership import GuildInstance, GuildMembership
from niuu.domain.models import (
    InstanceVisibility,
    IntegrationType,
    Principal,
    SecretType,
    project_owner_id,
)
from niuu.domain.project_team import ProjectTeam, ProjectTeamRecord
from niuu.ports.identity import IdentityPort
from niuu.ports.project_teams import ProjectTeamHomeConflictError, ProjectTeamRecords
from tests.conftest import (
    InMemorySessionRepository,
    MockPodManager,
    make_session_participant_service,
)
from tests.support.notifications import (
    InMemoryNotificationRuleRepository,
    InMemoryNotificationStore,
)
from tests.test_niuu.test_rest_volundr import StubInstanceService, _instance
from tests.test_projects.conftest import MemoryProjectMembers, MemoryProjects, MemoryUsers
from tests.test_projects.test_team_integrations import GITHUB, JIRA, SLACK, FakeMinter, MemoryUsage
from volundr.adapters.inbound.rest import create_router
from volundr.adapters.inbound.rest_oauth import create_team_oauth_router
from volundr.adapters.outbound.http_team_guild import HttpTeamGuild
from volundr.adapters.outbound.memory_integrations import InMemoryIntegrationRepository
from volundr.adapters.outbound.oauth2_provider import OAuth2Provider
from volundr.composition_builders import create_project_team_service
from volundr.config import OAuthClientConfig, OAuthConfig, Settings
from volundr.domain.models import OAuthSpec
from volundr.domain.notifications import TeamDeliverTo, TeamRouteEvent
from volundr.domain.project_ports import (
    ProjectConflictError,
    ProjectTeamSyncRepository,
    ProjectWorkspace,
    TeamGuildError,
    TeamNotVisibleError,
)
from volundr.domain.projects import ForgeProject, ProjectRole, ProjectTeamSync
from volundr.domain.services.integration_registry import IntegrationRegistry
from volundr.domain.services.project_integrations import (
    ProjectIntegrationService,
    TeamIntegrationError,
)
from volundr.domain.services.project_teams import ProjectTeamService
from volundr.domain.services.projects import ProjectService
from volundr.domain.services.session import SessionService

GUILD = "http://guild.test"
SLACK_APP = replace(
    SLACK,
    oauth=OAuthSpec(
        authorize_url="https://slack.test/authorize",
        token_url="https://slack.test/token",
        token_field_mapping={"access_token": "access_token"},
        extra_authorize_params={},
        extra_token_params={},
    ),
)
API = "/api/v1/forge"


class NameIsTheToken(IdentityPort):
    """``Bearer alice`` is Alice, of the org in her name when it has one (``eve@other``)."""

    async def validate_token(self, raw_token: str) -> Principal:
        name, _, tenant = raw_token.removeprefix("Bearer ").partition("@")
        roles = ["volundr:admin"] if name == "admin" else ["volundr:developer"]
        return Principal(name, f"{name}@x.test", tenant or "acme", roles)

    async def get_or_provision_user(self, principal):
        return principal


class MemoryRecords(ProjectTeamRecords):
    def __init__(self):
        self.rows: dict = {}

    async def get(self, project_id):
        return self.rows.get(project_id)

    async def put(self, record):
        held = self.rows.get(record.team.project_id)
        if held is not None and held.home_instance_id != record.home_instance_id:
            raise ProjectTeamHomeConflictError(f"published by Forge {held.home_instance_id}")
        if held is None or held.team.version <= record.team.version:
            self.rows[record.team.project_id] = record
        return self.rows[record.team.project_id]

    async def of_member(self, user_id, tenant_id):
        return [
            record
            for record in self.rows.values()
            if record.team.tenant_id == tenant_id and record.team.has_member(user_id)
        ]

    async def recorded(self, project_ids):
        return set(project_ids) & set(self.rows)


class MemorySync(ProjectTeamSyncRepository):
    def __init__(self):
        self.rows: dict = {}

    async def get(self, project_id):
        return self.rows.get(project_id, ProjectTeamSync())

    async def _set(self, project_id, **changes):
        self.rows[project_id] = (await self.get(project_id)).model_copy(update=changes)
        return self.rows[project_id]

    async def changed(self, project_id):
        return await self._set(
            project_id, version=(await self.get(project_id)).version + 1, pending=True
        )

    async def published(self, project_id, version, at):
        pending = (await self.get(project_id)).version > version
        return await self._set(project_id, synced_at=at, error="", pending=pending)

    async def copied(self, project_id, version, at):
        return await self._set(project_id, version=version, synced_at=at, error="", pending=False)

    async def failed(self, project_id, error):
        return await self._set(project_id, error=error)


class Forge:
    """One Forge's real routes and services over its own stores."""

    def __init__(self, name: str, credentials, *, shared=False, linked=True, refresh_after=0.0):
        self.name = name
        self.users = MemoryUsers()
        for person in ("alice", "bob", "cara"):
            self.users.seed(person, "acme", name=person.title())
        self.members = MemoryProjectMembers(self.users)
        self.sessions = SessionService(
            InMemorySessionRepository(),
            MockPodManager(),
            authorization=CedarAuthorizationAdapter(),
        )
        self.projects = ProjectService(
            MemoryProjects(self.members),
            AsyncMock(spec=ProjectWorkspace),
            self.sessions,
            instance_id=name,
            members=self.members,
            users=self.users,
        )
        self.integrations = InMemoryIntegrationRepository()
        self.rules = InMemoryNotificationRuleRepository(InMemoryNotificationStore())
        self.service = ProjectIntegrationService(
            self.projects,
            integrations=self.integrations,
            credentials=credentials,
            registry=IntegrationRegistry([SLACK_APP, JIRA, GITHUB]),
            rules=self.rules,
            usage=MemoryUsage(),
            minter=FakeMinter(),
            usage_page_size=50,
        )
        self.sync = MemorySync()
        self.clock = datetime(2026, 11, 22, tzinfo=UTC)
        self.teams = ProjectTeamService(
            self.projects,
            members=self.members,
            integrations=self.integrations,
            rules=self.rules,
            sync=self.sync,
            guild=HttpTeamGuild(GUILD, timeout_seconds=5) if linked else None,
            instance_id=name,
            keeps_shared_connections=shared,
            refresh_after_seconds=refresh_after,
            now=lambda: self.clock,
        )
        if linked:
            self.projects.team_changed = self.teams.changed
            self.projects.team_is_copy = self.teams.is_copy
        self.app = FastAPI()
        self.app.state.identity = NameIsTheToken()
        self.app.state.project_team_service = self.teams
        self.app.include_router(
            create_router(
                self.sessions,
                project_service=self.projects,
                project_integration_service=self.service,
                project_team_service=self.teams,
                session_participant_service=make_session_participant_service(self.sessions),
            )
        )

    async def call(self, method, path, who, **kwargs):
        return await ask(self.app, method, f"{API}{path}", who, **kwargs)

    async def copy(self, project, who):
        """What the Guild registers here when someone launches into the project."""
        await self.projects.register(
            ForgeProject(id=project.id, name=project.name, home_instance_id="valhalla"),
            await NameIsTheToken().validate_token(who),
        )

    async def roles(self, project):
        return {m.user_id: m.role.value for m in await self.members.list_for_project(project.id)}

    async def connections(self, project):
        return await self.integrations.list_connections(project_owner_id(project.id))


async def ask(app, method, path, who, **kwargs):
    transport = httpx.ASGITransport(app)
    async with httpx.AsyncClient(transport=transport, base_url="http://here.test") as client:
        headers = {"authorization": f"Bearer {who}"}
        return await client.request(method, path, headers=headers, **kwargs)


class World:
    def __init__(self, router: respx.Router):
        self.router = router
        self.credentials = MemoryCredentialStore()
        self.valhalla = Forge("valhalla", self.credentials)
        self.noatun = Forge("noatun", self.credentials)
        self.ymir = Forge("ymir", self.credentials, shared=True)
        self.records = MemoryRecords()
        self.guild = FastAPI()
        self.guild.state.identity = NameIsTheToken()
        forges = [self.valhalla, self.noatun, self.ymir]
        self.instances = [
            _instance(forge.name, base_url=f"http://{forge.name}.test", tenant_id="acme")
            for forge in forges
        ]
        self.guild.include_router(
            create_volundr_router(StubInstanceService(self.instances), project_teams=self.records)
        )
        self.reach("guild.test", self.guild)
        for forge in forges:
            self.reach(f"{forge.name}.test", forge.app)
            self.installs(forge)

    def reach(self, host, app):
        async def deliver(request: httpx.Request) -> httpx.Response:
            answer = await httpx.ASGITransport(app).handle_async_request(request)
            return httpx.Response(
                answer.status_code, headers=answer.headers, content=await answer.aread()
            )

        self.router.route(host=host).mock(side_effect=deliver)

    def installs(self, forge):
        """The Forge's team install routes, for a Slack app its operator configured."""
        config = OAuthConfig(
            redirect_base_url=f"https://{forge.name}.public",
            clients={"slack": OAuthClientConfig(client_id="id", client_secret="secret")},
        )
        forge.app.include_router(
            create_team_oauth_router(
                oauth_config=config,
                integration_registry=forge.service._registry,
                credential_store=self.credentials,
                integration_repo=forge.integrations,
                team_integrations=forge.service,
            )
        )

    def cut_off(self, host):
        self.router.route(host=host).mock(side_effect=httpx.ConnectError("no route"))

    async def start(self):
        alice = await NameIsTheToken().validate_token("alice")
        self.project = await self.valhalla.projects.register(ForgeProject(name="Payments"), alice)
        await self.valhalla.projects.member_service.add(
            self.project.id, alice, user_id="bob", role=ProjectRole.CONTRIBUTOR
        )
        self.path = f"/projects/{self.project.id}"

    async def slack(self):
        """An app install's callback, which carries no login of its own."""
        await self.valhalla.service.save(
            self.project,
            SLACK,
            connected_by="alice",
            credential={"access_token": "xoxb-1"},
            config={"channel": "#pay"},
            secret_type=SecretType.OAUTH_TOKEN,
            metadata={},
        )
        return (await self.valhalla.connections(self.project))[0]


@pytest.fixture
async def world():
    with respx.mock(assert_all_called=False) as router:
        made = World(router)
        await made.start()
        yield made


async def route_errors(world, connection, forge=None, who="alice"):
    return await (forge or world.valhalla).call(
        "PUT",
        f"{world.path}/integrations/routes/errors",
        who,
        json={"connection_id": connection.id, "deliver_to": "channel", "channel": "#pay"},
    )


# --- from the home to every other Forge ---------------------------------------------


async def test_a_change_at_the_home_reaches_every_forge_that_has_the_project(world):
    await world.noatun.copy(world.project, "bob")
    connection = await world.slack()
    assert (await route_errors(world, connection)).status_code == 200

    # Whoever launched on Noatun was its copy's owner; the home says who they are.
    assert await world.noatun.roles(world.project) == {"alice": "owner", "bob": "contributor"}
    assert await world.noatun.connections(world.project) == [connection]
    [rule] = await world.noatun.rules.list_for_owner(project_owner_id(world.project.id))
    assert rule.integration_connection_id == connection.id

    # Ymir does not have the project, but its store is where the Guild's Ting reads team
    # connections and their tokens are renewed. It takes nothing else.
    assert await world.ymir.connections(world.project) == [connection]
    assert await world.ymir.roles(world.project) == {}
    assert (await world.valhalla.sync.get(world.project.id)).pending is False


async def test_removing_it_at_the_home_removes_it_everywhere(world):
    await world.noatun.copy(world.project, "bob")
    connection = await world.slack()
    await route_errors(world, connection)

    gone = await world.valhalla.call(
        "DELETE", f"{world.path}/integrations/{connection.id}", "alice"
    )
    assert gone.status_code == 204
    out = await world.valhalla.call("DELETE", f"{world.path}/members/bob", "alice")
    assert out.status_code == 204

    for forge in (world.valhalla, world.noatun, world.ymir):
        assert await forge.connections(world.project) == []
    assert await world.noatun.rules.list_for_owner(project_owner_id(world.project.id)) == []
    assert await world.noatun.roles(world.project) == {"alice": "owner"}


async def test_an_install_started_on_a_copy_is_made_at_the_home_and_seen_on_the_copy(world):
    await world.noatun.copy(world.project, "bob")
    started = await world.noatun.call(
        "POST",
        "/integrations/oauth/authorize-team",
        "alice",
        json={"slug": "slack", "project_id": str(world.project.id), "config": {"channel": "#p"}},
    )
    assert started.status_code == 200, started.text
    sent_to = parse_qs(urlparse(started.json()["url"]).query)
    assert sent_to["redirect_uri"] == [
        "https://valhalla.public/api/v1/forge/integrations/oauth/callback"
    ]
    refused = await world.noatun.call(
        "POST",
        "/integrations/oauth/authorize-team",
        "bob",
        json={"slug": "slack", "project_id": str(world.project.id)},
    )
    assert refused.status_code == 403

    with patch.object(
        OAuth2Provider, "exchange_code", AsyncMock(return_value={"access_token": "xoxb-2"})
    ):
        back = await world.valhalla.call(
            "GET", f"/integrations/oauth/callback?code=c&state={sent_to['state'][0]}", ""
        )
    assert back.status_code == 200, back.text
    # Nobody has read it at the home, which the provider's callback could not publish for.
    listed = await world.noatun.call("GET", f"{world.path}/integrations", "bob")
    assert [(c["slug"], c["available"]) for c in listed.json()] == [("slack", True)]
    assert len(await world.ymir.connections(world.project)) == 1


async def test_with_the_guild_away_no_install_starts_on_a_copy(world):
    await world.noatun.copy(world.project, "bob")
    world.cut_off("guild.test")
    started = await world.noatun.call(
        "POST",
        "/integrations/oauth/authorize-team",
        "alice",
        json={"slug": "slack", "project_id": str(world.project.id)},
    )
    assert started.status_code == 503 and "Nothing was changed" in started.json()["detail"]


async def test_an_install_with_no_login_is_published_on_the_next_read(world):
    await world.noatun.copy(world.project, "bob")
    await world.slack()
    assert (await world.valhalla.sync.get(world.project.id)).pending is True
    assert await world.noatun.connections(world.project) == []

    listed = await world.valhalla.call("GET", f"{world.path}/integrations", "bob")
    assert [c["slug"] for c in listed.json()] == ["slack"]
    assert len(await world.noatun.connections(world.project)) == 1


# --- from a copy, through the home ----------------------------------------------------


async def test_a_change_on_a_copy_is_made_at_the_home(world):
    await world.noatun.copy(world.project, "bob")
    connection = await world.slack()
    await world.valhalla.call("GET", f"{world.path}/integrations", "alice")

    changed = await world.noatun.call(
        "PATCH", f"{world.path}/integrations/{connection.id}", "alice", json={"enabled": False}
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["enabled"] is False
    added = await world.noatun.call(
        "POST", f"{world.path}/members", "alice", json={"user_id": "cara", "role": "viewer"}
    )
    assert added.status_code == 201, added.text

    expected = {"alice": "owner", "bob": "contributor", "cara": "viewer"}
    for forge in (world.valhalla, world.noatun):
        assert await forge.roles(world.project) == expected
        assert [c.enabled for c in await forge.connections(world.project)] == [False]


async def test_the_home_decides_who_may_change_the_team_not_the_copy(world):
    # Bob launched on Noatun, so before the team is copied he owns the copy there.
    await world.noatun.copy(world.project, "bob")
    assert await world.noatun.roles(world.project) == {"bob": "owner"}

    refused = await world.noatun.call(
        "POST", f"{world.path}/members", "bob", json={"user_id": "cara", "role": "owner"}
    )
    assert refused.status_code == 403
    assert "cara" not in await world.valhalla.roles(world.project)
    # A stranger who registered a copy of their own sees and changes nothing.
    hidden = await world.noatun.call(
        "POST", f"{world.path}/members", "cara", json={"user_id": "cara", "role": "owner"}
    )
    assert hidden.status_code == 404


async def test_a_copy_reads_the_team_as_the_home_has_it(world):
    await world.slack()
    await world.noatun.copy(world.project, "bob")

    listed = await world.noatun.call("GET", f"{world.path}/integrations", "bob")
    assert [(c["slug"], c["available"]) for c in listed.json()] == [("slack", True)]
    people = await world.noatun.call("GET", f"{world.path}/members", "bob")
    assert {m["user_id"]: m["role"] for m in people.json()} == {
        "alice": "owner",
        "bob": "contributor",
    }
    view = (await world.noatun.call("GET", f"{world.path}/team", "bob")).json()
    assert (view["home"], view["home_instance_id"], view["guild"]) == (False, "valhalla", True)
    assert view["sync"]["error"] == ""


async def test_a_forge_without_the_shared_credentials_does_not_look_connected(world):
    laptop = Forge("noatun", MemoryCredentialStore())
    world.reach("noatun.test", laptop.app)
    await laptop.copy(world.project, "bob")
    await world.slack()

    listed = await laptop.call("GET", f"{world.path}/integrations", "bob")
    assert [(c["slug"], c["available"]) for c in listed.json()] == [("slack", False)]


async def test_a_launch_on_a_copy_first_copies_the_team(world):
    await world.slack()
    await world.noatun.copy(world.project, "bob")

    started = await world.noatun.call(
        "POST",
        "/sessions",
        "bob",
        json={
            "name": "on-noatun",
            "model": "claude-sonnet-4-6",
            "dispatch_id": str(uuid4()),
            "source": {"type": "git", "repo": "https://github.com/acme/repo", "branch": "main"},
            "coordination": {"project_id": str(world.project.id), "role": "worker"},
        },
    )
    assert started.status_code == 201, started.text
    assert await world.noatun.roles(world.project) == {"alice": "owner", "bob": "contributor"}
    assert len(await world.noatun.connections(world.project)) == 1


async def test_reads_close_together_copy_once(world):
    world.noatun = Forge("noatun", world.credentials, refresh_after=15)
    world.reach("noatun.test", world.noatun.app)
    await world.noatun.copy(world.project, "bob")
    read = AsyncMock(wraps=world.records.get)
    world.records.get = read

    await world.noatun.call("GET", f"{world.path}/members", "bob")
    first = read.await_count
    await world.noatun.call("GET", f"{world.path}/integrations", "bob")
    assert read.await_count == first
    world.noatun.clock += timedelta(seconds=16)
    await world.noatun.call("GET", f"{world.path}/integrations", "bob")
    assert read.await_count > first


# --- when something cannot be reached -----------------------------------------------


async def test_an_org_admins_change_reaches_the_copies_though_they_are_not_on_the_project(world):
    await world.noatun.copy(world.project, "bob")
    await world.noatun.call("GET", f"{world.path}/members", "bob")
    for forge, person in ((world.valhalla, "cara"), (world.noatun, "dana")):
        world.valhalla.users.seed("dana", "acme")
        added = await forge.call(
            "POST", f"{world.path}/members", "admin", json={"user_id": person, "role": "viewer"}
        )
        assert added.status_code == 201, added.text
    for forge in (world.valhalla, world.noatun):
        assert set(await forge.roles(world.project)) == {"alice", "bob", "cara", "dana"}
    assert (await memberships(world, "admin")).json()["member_of"] == []
    assert (await record(world, "admin@other")).status_code == 404


async def test_someone_taken_off_at_the_home_reads_nothing_new_on_a_copy(world):
    await publish(world)
    await world.noatun.copy(world.project, "bob")
    # The home could not publish: its Guild was away when Bob was removed.
    await world.valhalla.members.remove(world.project.id, "bob")
    await world.valhalla.sync.changed(world.project.id)
    fresh = await ask(world.guild, "GET", f"{API}{world.path}/team/record?fresh=true", "bob")
    assert fresh.status_code == 404
    kept = await ask(world.guild, "GET", f"{API}{world.path}/team/record?fresh=true", "alice")
    assert [m["user_id"] for m in kept.json()["team"]["members"]] == ["alice"]


async def test_an_owner_who_leaves_is_published_by_whoever_reads_next(world):
    await world.noatun.copy(world.project, "bob")
    await world.valhalla.call("PATCH", f"{world.path}/members/bob", "alice", json={"role": "owner"})
    left = await world.valhalla.call("DELETE", f"{world.path}/members/alice", "alice")
    assert left.status_code == 204
    assert (await world.valhalla.sync.get(world.project.id)).pending is True

    await world.valhalla.call("GET", f"{world.path}/members", "bob")
    assert await world.noatun.roles(world.project) == {"bob": "owner"}


async def test_with_the_guild_away_the_home_keeps_working_and_publishes_later(world):
    await world.noatun.copy(world.project, "bob")
    world.cut_off("guild.test")
    added = await world.valhalla.call(
        "POST", f"{world.path}/members", "alice", json={"user_id": "cara", "role": "viewer"}
    )
    assert added.status_code == 201
    state = await world.valhalla.sync.get(world.project.id)
    assert state.pending is True and "could not be reached" in state.error

    world.reach("guild.test", world.guild)
    await world.valhalla.call("GET", f"{world.path}/members", "alice")
    state = await world.valhalla.sync.get(world.project.id)
    assert (state.pending, state.error) == (False, "")
    assert "cara" in await world.noatun.roles(world.project)


async def test_with_the_guild_away_a_copy_reads_what_it_has_and_changes_nothing(world):
    await world.noatun.copy(world.project, "bob")
    await world.noatun.call("GET", f"{world.path}/members", "bob")
    world.cut_off("guild.test")

    people = await world.noatun.call("GET", f"{world.path}/members", "bob")
    assert {m["user_id"] for m in people.json()} == {"alice", "bob"}
    view = (await world.noatun.call("GET", f"{world.path}/team", "bob")).json()
    assert "could not be reached" in view["sync"]["error"]

    refused = await world.noatun.call(
        "POST", f"{world.path}/members", "alice", json={"user_id": "cara", "role": "viewer"}
    )
    assert refused.status_code == 503
    assert "Nothing was changed" in refused.json()["detail"]
    assert "cara" not in await world.valhalla.roles(world.project)


async def test_a_forge_that_is_away_is_reported_and_copies_when_it_is_back(world):
    await world.noatun.copy(world.project, "bob")
    await world.noatun.call("GET", f"{world.path}/members", "bob")
    world.cut_off("noatun.test")
    added = await world.valhalla.call(
        "POST", f"{world.path}/members", "alice", json={"user_id": "cara", "role": "viewer"}
    )
    assert added.status_code == 201

    published = await ask(
        world.guild,
        "POST",
        f"{API}{world.path}/team/publish",
        "alice",
        json={"instance_id": "valhalla"},
    )
    outcome = published.json()["forges"]
    assert outcome["noatun"].startswith("not copied: ")
    assert outcome["ymir"] == "connections"

    world.reach("noatun.test", world.noatun.app)
    await world.noatun.call("GET", f"{world.path}/members", "bob")
    assert "cara" in await world.noatun.roles(world.project)


async def test_a_copy_still_learns_the_team_when_its_home_is_away(world):
    await world.slack()
    await world.valhalla.call("GET", f"{world.path}/integrations", "alice")
    world.cut_off("valhalla.test")
    await world.noatun.copy(world.project, "bob")

    people = await world.noatun.call("GET", f"{world.path}/members", "bob")
    assert {m["user_id"] for m in people.json()} == {"alice", "bob"}
    assert len(await world.noatun.connections(world.project)) == 1


async def test_reads_do_not_each_wait_for_a_guild_that_is_away(world):
    world.valhalla = Forge("valhalla", world.credentials, refresh_after=15)
    world.noatun = Forge("noatun", world.credentials, refresh_after=15)
    alice = await NameIsTheToken().validate_token("alice")
    home = await world.valhalla.projects.register(ForgeProject(name="Later"), alice)
    await world.noatun.copy(home, "alice")
    world.cut_off("guild.test")
    for forge in (world.valhalla, world.noatun):
        guild = AsyncMock(wraps=forge.teams._guild._send)
        forge.teams._guild._send = guild
        for _ in range(3):
            await forge.call("GET", f"/projects/{home.id}/members", "alice")
        assert guild.await_count == 1
        forge.clock += timedelta(seconds=16)
        await forge.call("GET", f"/projects/{home.id}/members", "alice")
        assert guild.await_count == 2
    # A change always tries.
    tried = world.valhalla.teams._guild._send.await_count
    await world.valhalla.call(
        "POST", f"/projects/{home.id}/members", "alice", json={"user_id": "bob", "role": "viewer"}
    )
    assert world.valhalla.teams._guild._send.await_count == tried + 1


# --- one Forge on its own -----------------------------------------------------------


async def test_a_forge_without_a_guild_keeps_its_teams_to_itself():
    with respx.mock(assert_all_called=False) as router:
        alone = Forge("alone", MemoryCredentialStore(), linked=False)
        alice = await NameIsTheToken().validate_token("alice")
        project = await alone.projects.register(ForgeProject(name="Solo"), alice)
        added = await alone.call(
            "POST",
            f"/projects/{project.id}/members",
            "alice",
            json={"user_id": "bob", "role": "viewer"},
        )
        assert added.status_code == 201
        view = (await alone.call("GET", f"/projects/{project.id}/team", "alice")).json()
        assert (view["home"], view["guild"]) == (True, False)
        refreshed = await alone.call("POST", f"/projects/{project.id}/team/refresh", "alice")
        assert refreshed.json() == {"state": "unlinked", "version": 0}
        # Even a project that came from another Forge is its own to change here.
        other = ForgeProject(name="Left behind", home_instance_id="gone")
        left = await alone.projects.register(other, alice)
        assert await alone.teams.copy_of(left.id) is None
        with pytest.raises(TeamGuildError, match="no Guild"):
            await alone.teams.forward(
                left, method="POST", operation="members", query="", body=b"", bearer="alice"
            )
        assert not router.calls


def test_a_forge_is_composed_with_the_guild_it_belongs_to():
    def compose(settings, membership):
        return create_project_team_service(
            settings,
            MagicMock(),
            members=MagicMock(),
            integrations=MagicMock(),
            rules=MagicMock(),
            pool=MagicMock(),
            guild_membership=membership,
        )

    instance = GuildInstance(id="valhalla", name="Valhalla", slug="valhalla")
    linked = compose(Settings(), GuildMembership(guild_url=GUILD, instance=instance))
    assert linked.linked and linked._instance_id == "valhalla" and not linked._keeps_shared
    shared = Settings(integrations={"database_name": "niuu_shared"})
    assert compose(shared, None)._keeps_shared is True
    assert not compose(Settings(), None).linked
    assert not compose(Settings(), GuildMembership(guild_url=GUILD)).linked


# --- what the Guild and a Forge refuse ------------------------------------------------


async def publish(world, who="alice", instance="valhalla", project_id=None):
    return await ask(
        world.guild,
        "POST",
        f"{API}/projects/{project_id or world.project.id}/team/publish",
        who,
        json={"instance_id": instance},
    )


async def record(world, who):
    return await ask(world.guild, "GET", f"{API}{world.path}/team/record", who)


async def test_the_guild_hands_the_team_to_the_people_on_it_only(world):
    await publish(world)
    assert (await record(world, "bob")).status_code == 200
    assert "xoxb" not in (await record(world, "bob")).text
    assert (await record(world, "cara")).status_code == 404
    assert (await record(world, "bob@other")).status_code == 404
    assert (await publish(world, "cara")).status_code == 404
    assert (await publish(world, project_id=uuid4())).status_code == 404


async def memberships(world, who, *project_ids):
    return await ask(
        world.guild,
        "POST",
        f"{API}/projects/memberships",
        who,
        json={"project_ids": [str(pid) for pid in project_ids]},
    )


async def test_the_guild_says_which_projects_someone_is_on(world):
    unheard = uuid4()
    asked = (world.project.id, unheard)
    before = (await memberships(world, "alice", *asked)).json()
    assert (before["member_of"], len(before["unrecorded"])) == ([], 2)
    connection = await world.slack()
    await publish(world)

    for who, on_it in (("alice", True), ("bob", True), ("cara", False), ("alice@other", False)):
        told = (await memberships(world, who, *asked)).json()
        assert told["user_id"] == who.split("@")[0]
        assert told["unrecorded"] == [str(unheard)]
        assert told["member_of"] == (
            [{"project_id": str(world.project.id), "connection_ids": [connection.id]}]
            if on_it
            else []
        )


async def test_one_persons_own_forge_publishes_no_team(world):
    own = replace(world.instances[0], visibility=InstanceVisibility.USER, owner_id="alice")
    world.instances[0] = own
    refused = await publish(world)
    assert refused.status_code == 403
    assert "register it for the org" in refused.json()["detail"]
    assert world.records.rows == {}


async def test_another_forge_cannot_take_over_a_projects_team(world):
    await publish(world)
    # Cara registers a project of the same id on Noatun, where it is hers.
    cara = await NameIsTheToken().validate_token("cara")
    await world.noatun.projects.register(ForgeProject(id=world.project.id, name="Mine"), cara)

    taken = await publish(world, "cara", "noatun")
    assert taken.status_code == 409
    assert "valhalla" in taken.json()["detail"]
    assert world.records.rows[world.project.id].home_instance_id == "valhalla"
    assert await world.valhalla.roles(world.project) == {"alice": "owner", "bob": "contributor"}


async def test_a_project_registered_on_two_forges_is_the_earlier_ones(world):
    # Noatun's registration says it is a project of its own, but Valhalla had it first.
    alice = await NameIsTheToken().validate_token("alice")
    await world.noatun.projects.register(ForgeProject(id=world.project.id, name="Again"), alice)

    refused = await publish(world, instance="noatun")
    assert refused.status_code == 409
    assert "Instance valhalla registered" in refused.json()["detail"]
    world.cut_off("noatun.test")
    assert (await publish(world)).status_code == 200
    assert world.records.rows[world.project.id].home_instance_id == "valhalla"


async def test_someone_on_the_team_can_launch_on_a_copy_another_person_made(world):
    await world.valhalla.call(
        "POST", f"{world.path}/members", "alice", json={"user_id": "cara", "role": "contributor"}
    )
    for who in ("bob", "cara"):
        launched = await ask(
            world.guild,
            "POST",
            f"{API}/sessions",
            who,
            json={
                "name": f"{who}-on-noatun",
                "instance_id": "noatun",
                "model": "claude-sonnet-4-6",
                "dispatch_id": str(uuid4()),
                "source": {"type": "git", "repo": "https://github.com/acme/repo", "branch": "main"},
                "coordination": {"project_id": str(world.project.id), "role": "worker"},
            },
        )
        assert launched.status_code == 201, launched.text
    assert await world.noatun.roles(world.project) == {
        "alice": "owner",
        "bob": "contributor",
        "cara": "contributor",
    }


async def test_a_home_never_takes_a_team_from_the_guild(world):
    other = ProjectTeamRecord(
        home_instance_id="noatun",
        team=ProjectTeam(project_id=world.project.id, tenant_id="acme", version=9),
    )
    world.records.rows[world.project.id] = other
    assert (await world.valhalla.teams.refresh(world.project.id, "alice"))[0] == "home"
    assert await world.valhalla.roles(world.project) == {"alice": "owner", "bob": "contributor"}


async def test_a_copy_refuses_a_team_that_is_not_its_projects(world):
    await publish(world)
    await world.noatun.copy(world.project, "bob")
    kept = world.records.rows[world.project.id]
    world.records.rows[world.project.id] = kept.model_copy(update={"home_instance_id": "ymir"})
    with pytest.raises(ProjectConflictError, match="belongs to Forge valhalla"):
        await world.noatun.teams.refresh(world.project.id, "bob")
    refused = await world.noatun.call("POST", f"{world.path}/team/refresh", "bob")
    assert refused.status_code == 409
    assert await world.noatun.roles(world.project) == {"bob": "owner"}

    elsewhere = kept.team.model_copy(update={"tenant_id": "other"})
    world.records.rows[world.project.id] = kept.model_copy(update={"team": elsewhere})
    with pytest.raises(TeamNotVisibleError):
        await HttpTeamGuild(GUILD, timeout_seconds=5).record(world.project.id, "bob")


async def test_a_team_never_overwrites_a_connection_that_is_not_its_own(world):
    connection = await world.slack()
    await world.noatun.copy(world.project, "bob")
    personal = replace(connection, owner_id="cara", project_id=None, connected_by="")
    await world.noatun.integrations.save_connection(personal)

    await world.noatun.call("GET", f"{world.path}/members", "bob")
    state = await world.noatun.sync.get(world.project.id)
    assert "is not project" in state.error
    assert await world.noatun.integrations.get_connection(connection.id) == personal


async def test_an_install_cannot_be_kept_on_a_copy(world):
    await world.noatun.copy(world.project, "bob")
    bob = await NameIsTheToken().validate_token("bob")
    with pytest.raises(TeamIntegrationError, match="project's home, Forge valhalla"):
        await world.noatun.service.owner_project(world.project.id, bob)


async def test_scoped_credentials_get_no_team(world):
    class Scoped(NameIsTheToken):
        async def validate_token(self, raw_token):
            principal = await super().validate_token(raw_token)
            return Principal(**{**principal.__dict__, "token_use": "valkyrie_build"})

    await publish(world)
    world.guild.state.identity = Scoped()
    assert (await record(world, "alice")).status_code == 403
    assert (await publish(world)).status_code == 403
    assert (await memberships(world, "alice")).status_code == 403


async def test_the_guild_takes_a_team_only_from_a_home_that_answers_as_one(world):
    await world.noatun.copy(world.project, "bob")
    refused = await publish(world, "bob", "noatun")
    assert refused.status_code == 409 and "is not the home" in refused.json()["detail"]
    for body in ({"home": True, "team": {"nonsense": 1}}, {"home": True}):
        world.router.route(host="valhalla.test").respond(json=body)
        assert (await publish(world)).status_code == 502
    other = ProjectTeam(project_id=uuid4(), tenant_id="acme", version=1)
    world.router.route(host="valhalla.test").respond(
        json={"home": True, "team": other.model_dump(mode="json")}
    )
    assert "another project" in (await publish(world)).json()["detail"]
    world.router.route(host="valhalla.test").respond(503, text="busy")
    assert (await publish(world)).status_code == 503


async def test_a_home_from_before_project_teams_leaves_its_copies_as_they_are(world):
    await world.noatun.copy(world.project, "bob")
    world.router.route(host="valhalla.test").respond(404, json={"detail": "Not Found"})
    assert await world.noatun.teams.refresh(world.project.id, "bob") == ("unrecorded", 0)
    world.router.route(host="noatun.test").respond(500, text="broken")
    world.reach("valhalla.test", world.valhalla.app)
    assert (await publish(world)).json()["forges"]["noatun"] == "not copied: HTTP 500"


async def test_a_person_who_is_not_on_the_project_copies_nothing(world):
    await publish(world)
    await world.noatun.copy(world.project, "cara")
    assert await world.noatun.teams.refresh(world.project.id, "cara") == ("hidden", 0)
    assert await world.ymir.teams.refresh(world.project.id, "cara") == ("hidden", 0)
    assert await world.noatun.teams.refresh(uuid4(), "cara") == ("absent", 0)


async def test_a_copy_that_is_current_is_left_alone(world):
    await publish(world)
    await world.noatun.copy(world.project, "bob")
    assert (await world.noatun.teams.refresh(world.project.id, "bob"))[0] == "updated"
    assert (await world.noatun.teams.refresh(world.project.id, "bob"))[0] == "current"


async def test_a_route_can_be_changed_again_and_again(world):
    await world.noatun.copy(world.project, "bob")
    connection = await world.slack()
    await route_errors(world, connection)
    again = await world.noatun.call(
        "PUT",
        f"{world.path}/integrations/routes/errors",
        "alice",
        json={"connection_id": connection.id, "deliver_to": "recipient", "enabled": False},
    )
    assert again.status_code == 200, again.text
    owner = project_owner_id(world.project.id)
    for forge in (world.valhalla, world.noatun):
        [rule] = await forge.rules.list_for_owner(owner)
        assert rule.enabled is False
        assert rule.config["deliver_to"] == TeamDeliverTo.RECIPIENT.value
    dropped = await world.noatun.call(
        "DELETE", f"{world.path}/integrations/routes/{TeamRouteEvent.ERRORS.value}", "alice"
    )
    assert dropped.status_code == 204
    assert await world.noatun.rules.list_for_owner(owner) == []


# --- the Guild over HTTP --------------------------------------------------------------


@respx.mock
async def test_the_guild_client_says_what_went_wrong():
    guild = HttpTeamGuild(GUILD, timeout_seconds=1)
    project = uuid4()
    with pytest.raises(TeamGuildError, match="caller's own login"):
        await guild.record(project, "")
    record = respx.get(f"{GUILD}{API}/projects/{project}/team/record")
    record.respond(json={"team": "?"})
    with pytest.raises(TeamGuildError, match="unreadable project team"):
        await guild.record(project, "alice")
    record.respond(404, json={"detail": "Not Found"})
    with pytest.raises(TeamGuildError) as missing:
        await guild.record(project, "alice")
    assert missing.value.route_missing and not isinstance(missing.value, TeamNotVisibleError)
    record.respond(500, text="<html>")
    with pytest.raises(TeamGuildError, match="answered HTTP 500$"):
        await guild.record(project, "alice")
    publish = respx.post(f"{GUILD}{API}/projects/{project}/team/publish")
    publish.respond(json={})
    with pytest.raises(TeamGuildError, match="which team version"):
        await guild.publish(project, "valhalla", "alice")


def test_a_team_may_only_route_through_its_own_connections():
    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="not the team's"):
        ProjectTeam(
            project_id=uuid4(),
            tenant_id="acme",
            version=1,
            routes=[
                {
                    "event": "errors",
                    "connection_id": uuid4(),
                    "deliver_to": "channel",
                    "enabled": True,
                    "created_at": now,
                    "updated_at": now,
                }
            ],
        )
    assert IntegrationType.MESSAGING == SLACK.integration_type
