"""Team integrations: connections a project owns, its team routes, and their usage log."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from identity.adapters.cedar import CedarAuthorizationAdapter
from niuu.adapters.memory_credential_store import MemoryCredentialStore
from niuu.domain.models import (
    IntegrationConnection,
    IntegrationType,
    IntegrationUsage,
    Principal,
    credential_owner,
    project_owner_id,
)
from niuu.domain.notifications import NotificationKind, NotificationSeverity, NotificationSource
from niuu.ports.integrations import IntegrationUsageLog
from tests.conftest import InMemorySessionRepository, MockPodManager
from tests.support.notifications import (
    InMemoryNotificationRuleRepository,
    InMemoryNotificationStore,
)
from tests.test_projects.conftest import MemoryProjectMembers, MemoryProjects, MemoryUsers
from volundr.adapters.inbound.rest_project_integrations import create_project_integrations_router
from volundr.adapters.outbound.memory_integrations import InMemoryIntegrationRepository
from volundr.domain.models import (
    IntegrationDefinition,
    SecretType,
    TeamInstallMethod,
    TeamInstallSpec,
)
from volundr.domain.notifications import (
    Notification,
    NotificationRule,
    NotificationRuleMatch,
    TeamDeliverTo,
    TeamRouteEvent,
    rule_owner_keys,
    rules_matching,
    team_route,
)
from volundr.domain.project_ports import ProjectAccessDeniedError, ProjectWorkspace
from volundr.domain.projects import ForgeProject, ProjectRole
from volundr.domain.services.integration_registry import IntegrationRegistry
from volundr.domain.services.project_integrations import (
    ProjectIntegrationService,
    TeamIntegrationError,
    TeamIntegrationNotFoundError,
)
from volundr.domain.services.projects import ProjectNotFoundError, ProjectService
from volundr.domain.services.session import SessionService


def person(user_id, tenant="acme", roles=("volundr:developer",)):
    return Principal(user_id, f"{user_id}@x.test", tenant, list(roles))


ALICE = person("alice")  # owner
BOB = person("bob")  # viewer
DAVE = person("dave")  # not on the project

SLACK = IntegrationDefinition(
    slug="slack",
    name="Slack",
    description="",
    integration_type=IntegrationType.MESSAGING,
    adapter="",
    team_install=TeamInstallSpec(method=TeamInstallMethod.OAUTH, team_only=True),
)
JIRA = IntegrationDefinition(
    slug="jira",
    name="Jira Cloud",
    description="",
    integration_type=IntegrationType.ISSUE_TRACKER,
    adapter="ting.adapters.jira.JiraTrackerAdapter",
    team_install=TeamInstallSpec(
        method=TeamInstallMethod.CLIENT_CREDENTIALS,
        token_url="https://auth.example.test/token",
        required_config=("cloud_id",),
    ),
)
GITHUB = IntegrationDefinition(
    slug="github",
    name="GitHub",
    description="",
    integration_type=IntegrationType.SOURCE_CONTROL,
    adapter="x.Github",
)


class MemoryUsage(IntegrationUsageLog):
    def __init__(self) -> None:
        self.rows: list[IntegrationUsage] = []
        self.clock = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)

    async def record(self, usage: IntegrationUsage) -> None:
        self.clock += timedelta(seconds=1)
        self.rows.append(
            IntegrationUsage(**{**usage.__dict__, "occurred_at": self.clock, "id": str(uuid4())})
        )

    async def list_for_project(self, project_id, *, limit, before=None):
        rows = [r for r in self.rows if r.project_id == project_id]
        if before is not None:
            rows = [r for r in rows if r.occurred_at < before]
        return sorted(rows, key=lambda r: r.occurred_at, reverse=True)[:limit]


class FakeMinter:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def mint(self, spec, values):
        self.calls.append(dict(values))
        return {**values, "access_token": "minted", "expires_at": "2026-11-10T10:00:00+00:00"}


class World:
    def __init__(self, page_size: int = 50):
        self.users = MemoryUsers()
        for name in ("alice", "bob", "dave"):
            self.users.seed(name, "acme", name=name.title())
        self.members = MemoryProjectMembers(self.users)
        self.projects = ProjectService(
            MemoryProjects(self.members),
            AsyncMock(spec=ProjectWorkspace),
            SessionService(
                InMemorySessionRepository(),
                MockPodManager(),
                authorization=CedarAuthorizationAdapter(),
            ),
            instance_id="thor",
            members=self.members,
            users=self.users,
        )
        self.integrations = InMemoryIntegrationRepository()
        self.credentials = MemoryCredentialStore()
        self.store = InMemoryNotificationStore()
        self.rules = InMemoryNotificationRuleRepository(self.store)
        self.usage = MemoryUsage()
        self.minter = FakeMinter()
        self.service = ProjectIntegrationService(
            self.projects,
            integrations=self.integrations,
            credentials=self.credentials,
            registry=IntegrationRegistry([SLACK, JIRA, GITHUB]),
            rules=self.rules,
            usage=self.usage,
            minter=self.minter,
            usage_page_size=page_size,
        )
        self.project: ForgeProject | None = None

    async def setup(self):
        self.project = await self.projects.register(ForgeProject(name="Payments"), ALICE)
        await self.projects.member_service.add(
            self.project.id, ALICE, user_id="bob", role=ProjectRole.VIEWER
        )

    async def slack(self, channel="#payments"):
        return await self.service.save(
            self.project,
            SLACK,
            connected_by="alice",
            credential={"access_token": "xoxb-1"},
            config={"channel": channel},
            secret_type=SecretType.OAUTH_TOKEN,
            metadata={},
        )


@pytest.fixture
async def world():
    w = World()
    await w.setup()
    return w


# --- ownership ----------------------------------------------------------------------


def test_project_connection_must_carry_the_project_owner_key():
    now = datetime.now(UTC)
    base = dict(
        id="c1",
        integration_type=IntegrationType.MESSAGING,
        adapter="",
        credential_name="x",
        config={},
        enabled=True,
        created_at=now,
        updated_at=now,
    )
    with pytest.raises(ValueError, match="owner_id must be"):
        IntegrationConnection(owner_id="alice", project_id="p1", **base)
    with pytest.raises(ValueError, match="no project_id"):
        IntegrationConnection(owner_id=project_owner_id("p1"), **base)
    team = IntegrationConnection(owner_id=project_owner_id("p1"), project_id="p1", **base)
    personal = IntegrationConnection(owner_id="alice", **base)
    assert credential_owner(team) == ("project", "p1")
    assert credential_owner(personal) == ("user", "alice")


async def test_saved_connection_and_credential_belong_to_the_project(world):
    conn = await world.slack()
    assert conn.owner_id == project_owner_id(world.project.id)
    assert conn.project_id == str(world.project.id)
    assert conn.connected_by == "alice"
    stored = await world.credentials.get_value(
        "project", str(world.project.id), conn.credential_name
    )
    assert stored == {"access_token": "xoxb-1"}
    # No person's lookup finds it.
    assert await world.integrations.list_connections("alice") == []
    assert await world.credentials.get_value("user", "alice", conn.credential_name) is None
    assert [u.action for u in world.usage.rows] == ["connect"]


async def test_installing_again_replaces_the_credential_of_the_same_connection(world):
    first = await world.slack()
    again = await world.service.save(
        world.project,
        SLACK,
        connected_by="alice",
        credential={"access_token": "xoxb-2"},
        config={},
        secret_type=SecretType.OAUTH_TOKEN,
        metadata={},
    )
    assert again.id == first.id
    assert again.config["channel"] == "#payments"
    assert (await world.credentials.get_value(*credential_owner(again), again.credential_name)) == {
        "access_token": "xoxb-2"
    }
    assert [u.action for u in world.usage.rows] == ["connect", "reconnect"]


# --- who may do what -----------------------------------------------------------------


async def test_members_read_and_outsiders_are_not_told_it_exists(world):
    conn = await world.slack()
    assert [c.id for c in await world.service.list(world.project.id, BOB)] == [conn.id]
    with pytest.raises(ProjectNotFoundError):
        await world.service.list(world.project.id, DAVE)


async def test_only_owners_manage(world):
    conn = await world.slack()
    with pytest.raises(ProjectAccessDeniedError):
        await world.service.update(world.project.id, BOB, conn.id, enabled=False)
    with pytest.raises(ProjectAccessDeniedError):
        await world.service.remove(world.project.id, BOB, conn.id)
    with pytest.raises(ProjectAccessDeniedError):
        await world.service.set_route(
            world.project.id,
            BOB,
            event=TeamRouteEvent.PROGRESS,
            connection_id=conn.id,
            deliver_to=TeamDeliverTo.CHANNEL,
        )


async def test_another_projects_connection_is_not_found(world):
    other = await world.projects.register(ForgeProject(name="Other"), ALICE)
    conn = await world.slack()
    with pytest.raises(TeamIntegrationNotFoundError):
        await world.service.update(other.id, ALICE, conn.id, enabled=False)


# --- service accounts ----------------------------------------------------------------


async def test_service_account_is_minted_on_connect(world):
    conn = await world.service.connect_service_account(
        world.project.id,
        ALICE,
        slug="jira",
        credential={"client_id": "id", "client_secret": "s"},
        config={"site_url": "https://acme.atlassian.net", "cloud_id": "c-1"},
    )
    assert world.minter.calls == [{"client_id": "id", "client_secret": "s"}]
    stored = await world.credentials.get_value(*credential_owner(conn), conn.credential_name)
    assert stored["access_token"] == "minted"
    assert conn.integration_type == IntegrationType.ISSUE_TRACKER


async def test_service_account_needs_its_required_config(world):
    with pytest.raises(TeamIntegrationError, match="cloud_id"):
        await world.service.connect_service_account(
            world.project.id, ALICE, slug="jira", credential={}, config={}
        )
    assert world.minter.calls == []


async def test_app_installs_and_personal_only_integrations_are_refused_here(world):
    with pytest.raises(TeamIntegrationError, match="through its app"):
        await world.service.connect_service_account(
            world.project.id, ALICE, slug="slack", credential={}, config={}
        )
    with pytest.raises(TeamIntegrationError, match="cannot be installed"):
        await world.service.connect_service_account(
            world.project.id, ALICE, slug="github", credential={}, config={}
        )
    assert [d.slug for d in world.service.catalog()] == ["jira", "slack"]


# --- routes ----------------------------------------------------------------------------


async def test_route_is_one_rule_per_event_owned_by_the_project(world):
    conn = await world.slack()
    first = await world.service.set_route(
        world.project.id,
        ALICE,
        event=TeamRouteEvent.NEEDS_DECISION,
        connection_id=conn.id,
        deliver_to=TeamDeliverTo.CHANNEL,
    )
    second = await world.service.set_route(
        world.project.id,
        ALICE,
        event=TeamRouteEvent.NEEDS_DECISION,
        connection_id=conn.id,
        deliver_to=TeamDeliverTo.RECIPIENT,
    )
    assert first.id == second.id
    routes = await world.service.routes(world.project.id, BOB)
    assert len(routes) == 1
    route = team_route(routes[0], routes[0].owner_id)
    assert route.deliver_to is TeamDeliverTo.RECIPIENT
    await world.service.remove_route(world.project.id, ALICE, TeamRouteEvent.NEEDS_DECISION)
    assert await world.service.routes(world.project.id, ALICE) == []


async def test_channel_route_needs_a_channel(world):
    conn = await world.slack(channel="")
    with pytest.raises(TeamIntegrationError, match="channel"):
        await world.service.set_route(
            world.project.id,
            ALICE,
            event=TeamRouteEvent.PROGRESS,
            connection_id=conn.id,
            deliver_to=TeamDeliverTo.CHANNEL,
        )


async def test_routes_go_through_messaging_only(world):
    jira = await world.service.connect_service_account(
        world.project.id,
        ALICE,
        slug="jira",
        credential={"client_id": "id", "client_secret": "s"},
        config={"cloud_id": "c"},
    )
    with pytest.raises(TeamIntegrationError, match="messaging"):
        await world.service.set_route(
            world.project.id,
            ALICE,
            event=TeamRouteEvent.PROGRESS,
            connection_id=jira.id,
            deliver_to=TeamDeliverTo.RECIPIENT,
        )


async def test_removing_a_connection_removes_its_routes_and_credential(world):
    conn = await world.slack()
    await world.service.set_route(
        world.project.id,
        ALICE,
        event=TeamRouteEvent.ERRORS,
        connection_id=conn.id,
        deliver_to=TeamDeliverTo.CHANNEL,
    )
    await world.service.remove(world.project.id, ALICE, conn.id)
    assert await world.service.list(world.project.id, ALICE) == []
    assert await world.service.routes(world.project.id, ALICE) == []
    assert await world.credentials.get_value(*credential_owner(conn), conn.credential_name) is None
    assert world.usage.rows[-1].action == "disconnect"


# --- usage paging ------------------------------------------------------------------------


async def test_usage_is_paged_never_cut():
    w = World(page_size=2)
    await w.setup()
    for _ in range(3):
        await w.slack()
    page, cursor = await w.service.usage(w.project.id, BOB)
    assert len(page) == 2 and cursor == page[-1].occurred_at
    rest, end = await w.service.usage(w.project.id, BOB, before=cursor)
    assert len(rest) == 1 and end is None


def test_page_size_must_be_positive():
    with pytest.raises(ValueError, match="usage_page_size"):
        ProjectIntegrationService(
            AsyncMock(),
            integrations=AsyncMock(),
            credentials=AsyncMock(),
            registry=IntegrationRegistry([]),
            rules=AsyncMock(),
            usage=AsyncMock(),
            minter=AsyncMock(),
            usage_page_size=0,
        )


# --- which items a team route selects -----------------------------------------------------


PROJECT = "11111111-1111-4111-8111-111111111111"


def notification(kind, owner="alice", project=PROJECT, **metadata) -> Notification:
    return Notification(
        id=uuid4(),
        seq=1,
        dedupe_key=str(uuid4()),
        owner_id=owner,
        project_id=project,
        kind=kind,
        severity=NotificationSeverity.INFO,
        source=NotificationSource.AGENT,
        title="t",
        created_at=datetime.now(UTC),
        metadata=metadata,
    )


def route_rule(event, deliver_to, project=PROJECT) -> NotificationRule:
    now = datetime.now(UTC)
    return NotificationRule(
        id=uuid4(),
        owner_id=project_owner_id(project),
        name="Team",
        match=NotificationRuleMatch(project_ids=[project]),
        sink="integration",
        integration_connection_id="c1",
        config={
            "project_id": project,
            "team_event": event.value,
            "deliver_to": deliver_to.value,
            "channel": "#t",
        },
        created_at=now,
        updated_at=now,
    )


def test_channel_route_posts_the_original_once_and_recipient_route_every_copy():
    original = notification(NotificationKind.ATTENTION)
    copy = notification(NotificationKind.ATTENTION, owner="bob", copy_of=str(original.id))
    channel = route_rule(TeamRouteEvent.NEEDS_DECISION, TeamDeliverTo.CHANNEL)
    dm = route_rule(TeamRouteEvent.NEEDS_DECISION, TeamDeliverTo.RECIPIENT)
    assert rules_matching(original, [channel, dm]) == [channel, dm]
    assert rules_matching(copy, [channel, dm]) == [dm]


@pytest.mark.parametrize(
    ("event", "kind", "metadata", "selected"),
    [
        (TeamRouteEvent.PERSON_QUESTION, NotificationKind.DECISION, {"asked_person": "bob"}, True),
        (TeamRouteEvent.NEEDS_DECISION, NotificationKind.DECISION, {"asked_person": "bob"}, False),
        (TeamRouteEvent.NEEDS_DECISION, NotificationKind.ATTENTION, {}, True),
        (TeamRouteEvent.PROGRESS, NotificationKind.REPLY_READY, {}, True),
        (TeamRouteEvent.PROGRESS, NotificationKind.ERROR, {}, False),
        (TeamRouteEvent.ERRORS, NotificationKind.ERROR, {}, True),
    ],
)
def test_events_select_their_items(event, kind, metadata, selected):
    rule = route_rule(event, TeamDeliverTo.RECIPIENT)
    assert bool(rules_matching(notification(kind, **metadata), [rule])) is selected


def test_a_route_never_selects_another_projects_items():
    rule = route_rule(TeamRouteEvent.ERRORS, TeamDeliverTo.CHANNEL)
    other = notification(NotificationKind.ERROR, project="22222222-2222-4222-8222-222222222222")
    assert rules_matching(other, [rule]) == []
    assert rules_matching(notification(NotificationKind.ERROR, project=None), [rule]) == []


def test_a_personal_rule_cannot_pose_as_a_team_route():
    rule = route_rule(TeamRouteEvent.ERRORS, TeamDeliverTo.CHANNEL)
    personal = rule.model_copy(update={"owner_id": "alice"})
    assert team_route(personal, "alice") is None
    assert rules_matching(notification(NotificationKind.ERROR, owner="bob"), [personal]) == []


def test_a_malformed_team_route_is_loud():
    rule = route_rule(TeamRouteEvent.ERRORS, TeamDeliverTo.CHANNEL)
    broken = rule.model_copy(update={"config": {**rule.config, "deliver_to": "pigeon"}})
    with pytest.raises(ValueError, match="malformed"):
        team_route(broken, broken.owner_id)


def test_rule_owners_include_the_project():
    assert rule_owner_keys(notification(NotificationKind.INFO)) == [
        "alice",
        project_owner_id(PROJECT),
    ]
    assert rule_owner_keys(notification(NotificationKind.INFO, project=None)) == ["alice"]


# --- REST ------------------------------------------------------------------------------------


@pytest.fixture
async def client(world):
    app = FastAPI()
    principals = {"alice": ALICE, "bob": BOB, "dave": DAVE}

    async def principal_for(request):
        return principals[request.headers["x-user"]]

    app.include_router(create_project_integrations_router(world.service, principal_for))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


async def test_rest_reads_routes_and_maps_errors(world, client):
    pid = world.project.id
    conn = await world.slack()
    listed = await client.get(f"/projects/{pid}/integrations", headers={"x-user": "bob"})
    assert listed.status_code == 200
    assert listed.json()[0]["connected_by"] == "alice"
    assert "access_token" not in listed.text

    catalog = await client.get(f"/projects/{pid}/integrations/catalog", headers={"x-user": "bob"})
    assert {e["slug"]: e["team_install"] for e in catalog.json()} == {
        "jira": "client_credentials",
        "slack": "oauth",
    }

    hidden = await client.get(f"/projects/{pid}/integrations", headers={"x-user": "dave"})
    assert hidden.status_code == 404

    body = {"connection_id": conn.id, "deliver_to": "recipient"}
    denied = await client.put(
        f"/projects/{pid}/integrations/routes/person_question", json=body, headers={"x-user": "bob"}
    )
    assert denied.status_code == 403
    put = await client.put(
        f"/projects/{pid}/integrations/routes/person_question",
        json=body,
        headers={"x-user": "alice"},
    )
    assert put.status_code == 200 and put.json()["deliver_to"] == "recipient"
    routes = await client.get(f"/projects/{pid}/integrations/routes", headers={"x-user": "bob"})
    assert [r["event"] for r in routes.json()] == ["person_question"]

    bad = await client.post(
        f"/projects/{pid}/integrations",
        json={"slug": "jira", "credential": {}, "config": {}},
        headers={"x-user": "alice"},
    )
    assert bad.status_code == 422 and "cloud_id" in bad.json()["detail"]

    usage = await client.get(f"/projects/{pid}/integrations/usage", headers={"x-user": "bob"})
    assert usage.json()["items"][0]["action"] == "connect"
    assert usage.json()["next_before"] is None

    gone = await client.delete(
        f"/projects/{pid}/integrations/{conn.id}", headers={"x-user": "alice"}
    )
    assert gone.status_code == 204
    missing = await client.patch(
        f"/projects/{pid}/integrations/{conn.id}",
        json={"enabled": False},
        headers={"x-user": "alice"},
    )
    assert missing.status_code == 404


async def test_rest_service_account_connect_and_change(world, client):
    pid = world.project.id
    made = await client.post(
        f"/projects/{pid}/integrations",
        json={
            "slug": "jira",
            "credential": {"client_id": "i", "client_secret": "s"},
            "config": {"cloud_id": "c"},
        },
        headers={"x-user": "alice"},
    )
    assert made.status_code == 201
    changed = await client.patch(
        f"/projects/{pid}/integrations/{made.json()['id']}",
        json={"config": {"project_keys": ["PAY"]}, "enabled": False},
        headers={"x-user": "alice"},
    )
    assert changed.json()["enabled"] is False
    assert changed.json()["config"] == {"cloud_id": "c", "project_keys": ["PAY"]}
    removed = await client.delete(
        f"/projects/{pid}/integrations/routes/errors", headers={"x-user": "alice"}
    )
    assert removed.status_code == 204
