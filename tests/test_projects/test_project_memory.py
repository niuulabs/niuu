"""Memory a project owns: the service and routes, and the launches that inherit it."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from identity.adapters.cedar import CedarAuthorizationAdapter
from niuu.domain.models import Principal
from tests.conftest import (
    InMemorySessionRepository,
    MockPodManager,
    make_session_participant_service,
)
from tests.test_projects.conftest import MemoryProjectMembers, MemoryProjects, MemoryUsers
from volundr.adapters.inbound.rest import SessionCreate, create_router
from volundr.adapters.inbound.rest_project_memory import create_project_memory_router
from volundr.adapters.outbound.contributors.prompt import PromptContributor
from volundr.adapters.outbound.contributors.session_mcp import SessionMCPContributor
from volundr.domain.models import LaunchSpec, SessionStatus
from volundr.domain.ports import LaunchSpecProvider
from volundr.domain.project_memory import (
    MemoryAccess,
    MemoryDirectoryUnavailableError,
    MemoryMountClashError,
    MemoryMountDirectory,
    MemoryMountExistsError,
    MemoryMountNotFoundError,
    MemoryMountUnavailableError,
    MemoryMountUnenforceableError,
    ProjectMemoryMount,
    ProjectMemoryRefs,
    ProjectMemoryRepository,
    ProjectMemoryResolver,
    RegistryTarget,
    has_derived,
    merge_memory,
    with_memory_instructions,
    without_derived,
)
from volundr.domain.project_ports import ProjectAccessDeniedError, ProjectWorkspace
from volundr.domain.projects import ForgeProject, ProjectRole, SessionCoordination
from volundr.domain.services.forge import ForgeService
from volundr.domain.services.project_memory import ProjectMemoryService
from volundr.domain.services.projects import ProjectNotFoundError, ProjectService
from volundr.domain.services.session import SessionService


def person(user_id, tenant="acme"):
    return Principal(user_id, f"{user_id}@x.test", tenant, ["volundr:developer"])


ALICE = person("alice")  # owner
BOB = person("bob")  # viewer
CAROL = person("carol")  # contributor
DAVE = person("dave")  # not on the project


class MemoryMounts(ProjectMemoryRepository):
    def __init__(self) -> None:
        self.rows: dict[tuple, ProjectMemoryMount] = {}

    async def list_for_project(self, project_id):
        return [m for (pid, _), m in self.rows.items() if pid == project_id]

    async def get(self, project_id, name):
        return self.rows.get((project_id, name))

    async def add(self, mount):
        key = (mount.project_id, mount.name)
        if key in self.rows:
            raise MemoryMountExistsError(f"taken: {mount.name}")
        self.rows[key] = mount
        return mount

    async def update(self, project_id, name, *, access, write_prefixes):
        current = self.rows.get((project_id, name))
        if current is None:
            return None
        self.rows[(project_id, name)] = current.model_copy(
            update={"access": access, "write_prefixes": write_prefixes}
        )
        return self.rows[(project_id, name)]

    async def remove(self, project_id, name):
        return self.rows.pop((project_id, name), None) is not None


class Registry(MemoryMountDirectory):
    """Entries by (tenant, id); an entry with tenant '' is global."""

    def __init__(self) -> None:
        self.entries: dict[str, tuple[str, RegistryTarget]] = {}
        self.lookups: list[tuple[str, str]] = []
        self.owners: list[str] = []

    def put(self, entry_id, *, tenant="acme", **fields):
        fields.setdefault("url", f"https://mimir.example/{entry_id}")
        self.entries[entry_id] = (tenant, RegistryTarget(id=entry_id, name=entry_id, **fields))

    async def resolve(self, entry_id, *, tenant_id, owner_id):
        self.lookups.append((entry_id, tenant_id))
        self.owners.append(owner_id)
        found = self.entries.get(entry_id)
        if found is None or found[0] not in ("", tenant_id):
            return None
        return found[1]


class Specs(LaunchSpecProvider):
    def __init__(self, *specs: LaunchSpec) -> None:
        self.specs = {s.name: s for s in specs}

    def get(self, name):
        return self.specs.get(name)

    def list(self, workload_type=None):
        return list(self.specs.values())

    def get_default(self, workload_type):
        return next((s for s in self.specs.values() if s.is_default), None)


class World:
    def __init__(self, *specs: LaunchSpec) -> None:
        self.users = MemoryUsers()
        for name in ("alice", "bob", "carol", "dave"):
            self.users.seed(name, "acme", name=name.title())
        self.members = MemoryProjectMembers(self.users)
        self.repository = MemoryMounts()
        self.registry = Registry()
        self.registry.put("team-notes")
        self.registry.put("playbook", url="https://mimir.example/m")
        self.resolver = ProjectMemoryResolver(self.repository, self.registry)
        self.pods = MockPodManager()
        self.sessions = SessionService(
            InMemorySessionRepository(),
            self.pods,
            authorization=CedarAuthorizationAdapter(),
            validate_repos=False,
            provisioning_initial_delay=0,
            contributors=[SessionMCPContributor(), PromptContributor()],
            launch_spec_provider=Specs(*specs),
            project_memory=self.resolver,
        )
        workspace = AsyncMock(spec=ProjectWorkspace)
        workspace.context.return_value = ("", "rev")
        self.projects = ProjectService(
            MemoryProjects(self.members),
            workspace,
            self.sessions,
            instance_id="thor",
            members=self.members,
            users=self.users,
        )
        self.service = ProjectMemoryService(self.projects, self.repository, self.resolver)
        self.forge = ForgeService(
            self.sessions,
            project_service=self.projects,
            session_participant_service=make_session_participant_service(self.sessions),
        )
        self.project: ForgeProject | None = None

    async def setup(self):
        self.project = await self.projects.register(ForgeProject(name="Payments"), ALICE)
        for user, role in (("bob", ProjectRole.VIEWER), ("carol", ProjectRole.CONTRIBUTOR)):
            await self.projects.member_service.add(self.project.id, ALICE, user_id=user, role=role)

    async def mount(self, name="notes", entry="team-notes", **fields):
        return await self.service.add(
            self.project.id,
            ALICE,
            name=name,
            registry_entry_id=entry,
            access=fields.pop("access", MemoryAccess.READ_WRITE),
            **fields,
        )

    def create(self, **fields):
        return SessionCreate(
            name="task",
            model="test",
            coordination=SessionCoordination(project_id=self.project.id),
            **fields,
        )

    async def launch(self, principal=ALICE, **fields):
        started = await self.forge.create_and_start_session(
            self.create(**fields), principal=principal
        )
        await asyncio.gather(*self.sessions._provisioning_tasks.values())
        return started

    def last_spec(self):
        return self.pods.start_calls[-1][1]

    def servers(self):
        return {s["name"]: s for s in self.last_spec().values.get("mcpServers", [])}


@pytest.fixture
async def world():
    w = World()
    await w.setup()
    return w


# --- who may do what --------------------------------------------------------------------


async def test_owner_adds_and_every_member_lists(world):
    added = await world.mount("notes")
    assert added.added_by == "alice"
    assert added.access is MemoryAccess.READ_WRITE and added.write_prefixes == ()
    for member in (ALICE, BOB, CAROL):
        assert [m.name for m in await world.service.list(world.project.id, member)] == ["notes"]


async def test_outsiders_are_not_told_the_project_exists(world):
    with pytest.raises(ProjectNotFoundError):
        await world.service.list(world.project.id, DAVE)
    with pytest.raises(ProjectNotFoundError):
        await world.service.add(
            world.project.id,
            DAVE,
            name="notes",
            registry_entry_id="team-notes",
            access=MemoryAccess.READ,
        )


@pytest.mark.parametrize("member", [BOB, CAROL])
async def test_only_owners_add_change_and_remove(world, member):
    await world.mount()
    pid = world.project.id
    with pytest.raises(ProjectAccessDeniedError):
        await world.service.add(
            pid,
            member,
            name="other",
            registry_entry_id="team-notes",
            access=MemoryAccess.READ,
        )
    with pytest.raises(ProjectAccessDeniedError):
        await world.service.change(pid, member, "notes", access=MemoryAccess.READ)
    with pytest.raises(ProjectAccessDeniedError):
        await world.service.remove(pid, member, "notes")
    assert [m.name for m in await world.service.list(pid, ALICE)] == ["notes"]


async def test_signing_in_is_required_to_manage(world):
    with pytest.raises(ProjectAccessDeniedError, match="Sign in"):
        await world.service.remove(world.project.id, None, "notes")


async def test_a_name_is_taken_once_per_project(world):
    await world.mount()
    with pytest.raises(MemoryMountExistsError):
        await world.mount()


async def test_access_and_prefixes_must_agree(world):
    with pytest.raises(ValueError, match="no write prefixes"):
        await world.mount(write_prefixes=("project/",))
    with pytest.raises(ValueError, match="relative page path"):
        await world.mount(access=MemoryAccess.READ_WRITE, write_prefixes=("../x",))
    with pytest.raises(ValueError, match="relative page path"):
        await world.mount(access=MemoryAccess.READ_WRITE, write_prefixes=("/x",))
    with pytest.raises(ValueError, match="unique"):
        await world.mount(access=MemoryAccess.READ_WRITE, write_prefixes=("a/", "a/"))


async def test_a_mount_needs_a_registry_entry_its_org_can_reach(world):
    world.registry.put("other-org", tenant="globex")
    world.registry.put("off", enabled=False)
    world.registry.put("nowhere", url="")
    world.registry.put("shared-global", tenant="")
    for entry, why in (
        ("missing", "does not exist or is not reachable"),
        ("other-org", "does not exist or is not reachable"),
        ("off", "disabled"),
        ("nowhere", "no url or adapter"),
    ):
        with pytest.raises(MemoryMountUnavailableError, match=why) as raised:
            await world.mount("notes", entry=entry)
        assert "'notes'" in str(raised.value)
    assert (await world.mount("g", entry="shared-global")).registry_entry_id == "shared-global"
    assert ("other-org", "acme") in world.registry.lookups


async def test_change_and_remove(world):
    pid = world.project.id
    await world.mount()
    with pytest.raises(MemoryMountUnenforceableError, match="'notes'"):
        await world.service.change(
            pid, ALICE, "notes", access=MemoryAccess.READ_WRITE, write_prefixes=("project/",)
        )
    open_access = await world.service.change(pid, ALICE, "notes", access=MemoryAccess.READ_WRITE)
    assert open_access.write_prefixes == () and not open_access.restricts
    with pytest.raises(MemoryMountNotFoundError):
        await world.service.change(pid, ALICE, "ghost", access=MemoryAccess.READ)
    await world.service.remove(pid, ALICE, "notes")
    with pytest.raises(MemoryMountNotFoundError):
        await world.service.remove(pid, ALICE, "notes")
    assert await world.service.list(pid, ALICE) == []


async def test_change_loses_to_a_mount_removed_meanwhile(world, monkeypatch):
    await world.mount()

    async def gone(*_a, **_k):
        return None

    monkeypatch.setattr(world.repository, "update", gone)
    with pytest.raises(MemoryMountNotFoundError):
        await world.service.change(world.project.id, ALICE, "notes", access=MemoryAccess.READ_WRITE)


# --- routes ------------------------------------------------------------------------------


@pytest.fixture
async def client(world):
    app = FastAPI()
    principals = {"alice": ALICE, "bob": BOB, "carol": CAROL, "dave": DAVE}

    async def principal_for(request):
        return principals[request.headers["x-user"]]

    app.include_router(create_project_memory_router(world.service, principal_for))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def test_routes_map_roles_and_errors(world, client):
    base = f"/projects/{world.project.id}/memory"
    owner = {"x-user": "alice"}
    body = {"name": "notes", "registry_entry_id": "team-notes", "access": "read_write"}

    created = await client.post(base, json=body, headers=owner)
    assert created.status_code == 201
    assert created.json()["added_by"] == "alice" and created.json()["access"] == "read_write"

    assert (await client.post(base, json=body, headers=owner)).status_code == 409
    assert (await client.post(base, json=body, headers={"x-user": "bob"})).status_code == 403
    assert (await client.post(base, json=body, headers={"x-user": "dave"})).status_code == 404
    unknown = {"name": "n2", "registry_entry_id": "missing"}
    refused = await client.post(base, json=unknown, headers=owner)
    assert refused.status_code == 422 and "'n2'" in refused.json()["detail"]
    bad = {"name": "n3", "registry_entry_id": "team-notes", "write_prefixes": ["a/"]}
    assert (await client.post(base, json=bad, headers=owner)).status_code == 422
    assert (
        await client.post(base, json={**bad, "name": "Bad Name"}, headers=owner)
    ).status_code == 422

    listed = await client.get(base, headers={"x-user": "bob"})
    assert [m["name"] for m in listed.json()] == ["notes"]
    assert (await client.get(base, headers={"x-user": "dave"})).status_code == 404

    restricted = {"access": "read_write", "write_prefixes": ["project/"]}
    refused_limit = await client.patch(f"{base}/notes", json=restricted, headers=owner)
    assert refused_limit.status_code == 422 and "memory gateway" in refused_limit.json()["detail"]
    read_only = await client.patch(f"{base}/notes", json={"access": "read"}, headers=owner)
    assert read_only.status_code == 422 and "memory gateway" in read_only.json()["detail"]

    change = {"access": "read_write", "write_prefixes": []}
    assert (
        await client.patch(f"{base}/notes", json=change, headers={"x-user": "carol"})
    ).status_code == 403
    patched = await client.patch(f"{base}/notes", json=change, headers=owner)
    assert patched.status_code == 200 and patched.json()["write_prefixes"] == []
    assert (await client.patch(f"{base}/ghost", json=change, headers=owner)).status_code == 404

    assert (await client.delete(f"{base}/notes", headers={"x-user": "bob"})).status_code == 403
    assert (await client.delete(f"{base}/notes", headers=owner)).status_code == 204
    assert (await client.delete(f"{base}/notes", headers=owner)).status_code == 404


# --- sessions inherit --------------------------------------------------------------------


WIKI = with_memory_instructions("", facts=False)
FACTS = with_memory_instructions("", facts=True)


async def test_a_session_gets_its_mounts_and_wiki_instructions(world):
    await world.mount("notes")
    await world.mount("book", entry="playbook")
    started = await world.launch()
    assert started.status in (SessionStatus.STARTING, SessionStatus.RUNNING)

    servers = world.servers()
    assert set(servers) == {"mimir-notes", "mimir-book"}
    assert servers["mimir-notes"]["url"] == "https://mimir.example/team-notes"
    assert servers["mimir-book"]["url"] == "https://mimir.example/m"

    prompt = world.last_spec().values["session"]["systemPrompt"]
    assert WIKI in prompt and FACTS not in prompt
    for tool in ("mimir_search", "mimir_read", "mimir_write"):
        assert tool in WIKI
    assert "mimir_fact" not in WIKI


async def test_a_mount_that_offers_the_fact_tools_gets_the_fact_instructions(world):
    world.registry.put("hub", url="https://hub.example", facts=True)
    await world.mount("notes")
    await world.mount("hub", entry="hub")
    await world.launch()
    prompt = world.last_spec().values["session"]["systemPrompt"]
    assert FACTS in prompt and WIKI not in prompt
    for step in ("mimir_fact_context_pack", "mimir_fact_recall", "mimir_fact_remember"):
        assert step in FACTS
    assert "agent's" in FACTS and "cannot raise it" in FACTS


async def test_instructions_follow_the_prompt_the_session_was_given(world):
    await world.mount()
    await world.launch(system_prompt="Review the diff.")
    prompt = world.last_spec().values["session"]["systemPrompt"]
    assert prompt.startswith("Review the diff.\n\n" + WIKI)


@pytest.mark.parametrize("entry", ["team-notes", "playbook"])
async def test_limits_are_refused_for_every_target_when_adding_and_changing(world, entry):
    with pytest.raises(MemoryMountUnenforceableError, match="'book'.*memory gateway"):
        await world.mount("book", entry=entry, access=MemoryAccess.READ)
    with pytest.raises(MemoryMountUnenforceableError, match="'book'.*read_write and no prefixes"):
        await world.mount("book", entry=entry, write_prefixes=("a/",))
    await world.mount("book", entry=entry)
    with pytest.raises(MemoryMountUnenforceableError, match="'book'"):
        await world.service.change(world.project.id, ALICE, "book", access=MemoryAccess.READ)


async def test_a_limited_mount_stored_earlier_fails_the_launch(world):
    await world.repository.add(
        ProjectMemoryMount(
            project_id=world.project.id,
            name="notes",
            registry_entry_id="team-notes",
            access=MemoryAccess.READ,
        )
    )
    with pytest.raises(MemoryMountUnenforceableError, match="'notes'"):
        await world.forge.create_and_start_session(world.create(), principal=ALICE)
    assert world.pods.start_calls == []


async def test_the_owner_the_registry_is_asked_for_is_the_sessions_owner(world):
    await world.mount("notes")
    await world.launch()
    assert set(world.registry.owners) == {"alice"} and len(world.registry.owners) >= 2


async def test_a_project_without_mounts_changes_nothing(world):
    await world.launch(system_prompt="Hi")
    assert "mcpServers" not in world.last_spec().values
    assert "Project memory" not in world.last_spec().values["session"]["systemPrompt"]
    stored = await world.sessions.get_session(world.pods.start_calls[-1][0].id)
    assert "mimir" not in stored.workload_config


async def test_a_session_outside_any_project_is_untouched(world):
    await world.mount()
    plain = await world.forge.create_and_start_session(
        SessionCreate(name="solo", model="test"), principal=ALICE
    )
    await asyncio.gather(*world.sessions._provisioning_tasks.values())
    assert "mcpServers" not in world.last_spec().values
    assert "mimir" not in (await world.sessions.get_session(plain.id)).workload_config


async def test_mounts_are_resolved_for_the_owners_org_at_launch(world):
    await world.mount()
    await world.launch()
    assert ("team-notes", "acme") in world.registry.lookups


async def test_a_vanished_entry_fails_the_start_naming_the_mount(world):
    await world.mount("notes")
    del world.registry.entries["team-notes"]
    with pytest.raises(MemoryMountUnavailableError, match="'notes'.*'team-notes'"):
        await world.forge.create_and_start_session(world.create(), principal=ALICE)
    assert world.pods.start_calls == []


async def test_an_owner_who_cannot_reach_the_entry_fails_the_start(world):
    await world.mount("notes")
    world.registry.entries["team-notes"] = ("globex", world.registry.entries["team-notes"][1])
    with pytest.raises(MemoryMountUnavailableError, match="'notes'"):
        await world.forge.create_and_start_session(world.create(), principal=ALICE)


async def test_a_restart_picks_up_what_the_project_holds_now(world):
    await world.mount("notes")
    first = await world.launch()
    await world.service.remove(world.project.id, ALICE, "notes")
    await world.mount("later", entry="playbook")
    stored = await world.sessions.get_session(first.id)
    await world.sessions._repository.update(
        stored.model_copy(update={"status": SessionStatus.STOPPED})
    )

    await world.sessions.start_session(first.id, principal=ALICE)
    await asyncio.gather(*world.sessions._provisioning_tasks.values())

    assert set(world.servers()) == {"mimir-later"}
    config = (await world.sessions.get_session(first.id)).workload_config["mimir"]
    assert [r["mount_name"] for r in config["registry_refs"]] == ["later"]


async def test_removing_every_mount_leaves_a_restart_with_only_its_own_memory(world):
    await world.mount("notes")
    first = await world.launch(workload_config={"mimir": {"hosted_url": "https://own.example"}})
    await world.service.remove(world.project.id, ALICE, "notes")
    stored = await world.sessions.get_session(first.id)
    await world.sessions._repository.update(
        stored.model_copy(update={"status": SessionStatus.STOPPED})
    )

    await world.sessions.start_session(first.id, principal=ALICE)
    await asyncio.gather(*world.sessions._provisioning_tasks.values())

    config = (await world.sessions.get_session(first.id)).workload_config["mimir"]
    assert config == {"hosted_url": "https://own.example"}
    assert set(world.servers()) == {"mimir-hosted"}


async def test_a_session_that_sets_its_own_mimir_adds_to_the_projects(world):
    await world.mount("notes")
    await world.launch(
        workload_config={
            "mimir": {"instances": [{"name": "mine", "url": "https://m.example/mimir/mine"}]}
        }
    )
    assert set(world.servers()) == {"mimir-notes", "mimir-mine"}


async def test_a_session_may_repeat_a_project_mount_but_not_redefine_it(world):
    await world.mount("notes")
    await world.launch(
        workload_config={
            "mimir": {"registry_refs": [{"mount_name": "notes", "registry_entry_id": "team-notes"}]}
        }
    )
    assert list(world.servers()) == ["mimir-notes"]

    clash = {
        "mimir": {"instances": [{"name": "notes", "url": "https://m.example/mimir/elsewhere"}]}
    }
    with pytest.raises(MemoryMountClashError, match="'notes'.*project.*session"):
        await world.forge.create_and_start_session(
            world.create(workload_config=clash), principal=ALICE
        )


async def test_a_launch_spec_adds_mounts_and_cannot_redefine_the_projects():
    spec = LaunchSpec(
        name="deep",
        workload_config={
            "mimir": {
                "instances": [{"name": "spec-wide", "url": "https://m.example/mimir/spec-wide"}]
            }
        },
    )
    clashing = LaunchSpec(
        name="clash",
        workload_config={
            "mimir": {"instances": [{"name": "notes", "url": "https://m.example/mimir/elsewhere"}]}
        },
    )
    world = World(spec, clashing)
    await world.setup()
    await world.mount("notes")

    await world.launch(launch_spec="deep")
    assert set(world.servers()) == {"mimir-notes", "mimir-spec-wide"}

    with pytest.raises(MemoryMountClashError, match="'notes'.*project.*launch spec"):
        await world.forge.create_and_start_session(
            world.create(launch_spec="clash"), principal=ALICE
        )


async def test_the_default_spec_of_a_workload_type_adds_mounts_too():
    default = LaunchSpec(
        name="d",
        is_default=True,
        workload_type="pipeline",
        workload_config={
            "mimir_hosted_url": "https://spec.example",
            "mimir": {"instances": [{"name": "spec-wide", "url": "https://m.example/m"}]},
        },
    )
    world = World(default)
    await world.setup()
    await world.mount("notes")
    await world.launch(workload_type="pipeline")
    assert set(world.servers()) == {"mimir-notes", "mimir-spec-wide"}


async def test_session_routes_answer_a_bad_mount_with_a_4xx_naming_it(world):
    # The routes here carry no identity, so the project is one registered without an owner.
    world.sessions._authorization = None
    world.registry.put("team-notes", tenant="")
    open_project = await world.projects.register(ForgeProject(name="Open"), None)
    await world.repository.add(
        ProjectMemoryMount(
            project_id=open_project.id,
            name="notes",
            registry_entry_id="team-notes",
            access=MemoryAccess.READ_WRITE,
        )
    )
    coordination = SessionCoordination(project_id=open_project.id)
    app = FastAPI()
    app.include_router(
        create_router(
            world.sessions,
            project_service=world.projects,
            session_participant_service=make_session_participant_service(world.sessions),
        )
    )
    data = SessionCreate(name="task", model="test", coordination=coordination).model_dump(
        mode="json"
    )
    clash = {
        "mimir": {"instances": [{"name": "notes", "url": "https://m.example/mimir/elsewhere"}]}
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as http:
        clashed = await http.post("/api/v1/forge/sessions", json={**data, "workload_config": clash})
        assert clashed.status_code == 409, clashed.text
        assert "'notes'" in clashed.json()["detail"]

        del world.registry.entries["team-notes"]
        missing = await http.post("/api/v1/forge/sessions", json=data)
        assert missing.status_code == 422 and "'notes'" in missing.json()["detail"]

        waiting = await world.sessions.create_session(
            name="again", model="test", coordination=coordination
        )
        restart = await http.post(f"/api/v1/forge/sessions/{waiting.id}/start")
        assert restart.status_code == 422 and "'notes'" in restart.json()["detail"]


# --- runs inherit ------------------------------------------------------------------------


async def test_every_member_of_a_run_in_the_project_gets_the_mounts(world):
    await world.mount("notes")
    run_id = uuid4()
    for member in ("planner", "reviewer"):
        await world.launch(workflow_run_id=run_id, workflow_member=member, persona_name="coder")
        assert set(world.servers()) == {"mimir-notes"}, member
        assert WIKI in world.last_spec().values["session"]["systemPrompt"]


RUN = uuid4()


async def _unit(world):
    return await world.forge.create_and_start_session(
        world.create(
            hosts_members=True,
            workflow_run_id=RUN,
            workflow_run_room=True,
            workflow_orchestration="engine",
        ),
        principal=ALICE,
    )


def _member(world, unit, member="coder"):
    return world.create(
        hosted_by=unit.id, workflow_run_id=RUN, workflow_member=member, definition="skuldClaude"
    )


async def test_a_member_held_for_its_unit_is_refused_for_a_vanished_mount(world):
    await world.mount("notes")
    unit = await _unit(world)
    del world.registry.entries["team-notes"]
    with pytest.raises(MemoryMountUnavailableError, match="'notes'"):
        await world.forge.create_and_start_session(_member(world, unit), principal=ALICE)


async def test_hosted_members_get_the_mounts_when_their_unit_starts(world):
    await world.mount("notes")
    unit = await _unit(world)
    for member in ("coder", "reviewer"):
        await world.forge.create_and_start_session(_member(world, unit, member), principal=ALICE)
    world.sessions._unit_principals[unit.id] = ALICE

    launches = await world.sessions.hosted_member_launches(unit.id)

    assert len(launches) == 2
    for launch in launches:
        assert [s["name"] for s in launch.spec.values["mcpServers"]] == ["mimir-notes"]
        assert WIKI in launch.spec.values["session"]["systemPrompt"]


# --- pure pieces --------------------------------------------------------------------------


def _project(*names, entry="team-notes"):
    return ProjectMemoryRefs(
        [
            {
                "mount_name": n,
                "registry_entry_id": entry,
                "url": f"https://m.example/{n}",
                "source": "project",
            }
            for n in names
        ],
        [
            {"mount_name": n, "access": "read", "write_prefixes": [], "source": "project"}
            for n in names
        ],
    )


def test_merge_keeps_every_source_and_tags_what_it_derives():
    spec = {
        "registry_refs": [{"mount_name": "s", "url": "https://m.example/s"}],
        "hosted_url": "https://spec",
    }
    merged = merge_memory(
        {"instances": [{"name": "i", "url": "https://m.example/i"}], "default_mounts": ["i"]},
        spec,
        _project("p"),
    )
    assert [r["mount_name"] for r in merged["registry_refs"]] == ["p", "s"]
    assert merged["registry_refs"][1]["source"] == "launch_spec"
    assert merged["instances"] == [{"name": "i", "url": "https://m.example/i"}]
    assert merged["hosted_url"] == "https://spec"  # the spec's, since the session set none
    assert merged["default_mounts"] == ["i"]
    assert has_derived(merged) and not has_derived(without_derived(merged))
    assert without_derived(merged) == {
        "instances": [{"name": "i", "url": "https://m.example/i"}],
        "default_mounts": ["i"],
    }


def test_merge_is_stable_when_applied_to_its_own_output():
    first = merge_memory(
        {"instances": [{"name": "i", "url": "https://m.example/i"}]}, None, _project("p")
    )
    assert merge_memory(first, None, _project("p")) == first


def test_the_session_hosted_url_wins_over_the_specs():
    merged = merge_memory({"hosted_url": "https://own"}, {"hosted_url": "https://spec"}, _project())
    assert merged == {"hosted_url": "https://own"}


def test_same_name_for_different_targets_is_refused_naming_both_sources():
    with pytest.raises(MemoryMountClashError, match="'p'.*project.*session"):
        merge_memory(
            {"registry_refs": [{"mount_name": "p", "registry_entry_id": "other"}]},
            None,
            _project("p"),
        )
    with pytest.raises(MemoryMountClashError, match="'x'.*launch spec.*session"):
        merge_memory(
            {"instances": [{"name": "x", "url": "https://b"}]},
            {"instances": [{"name": "x", "url": "https://a"}]},
            _project(),
        )
    with pytest.raises(MemoryMountClashError, match="'e'"):
        merge_memory(
            {"ephemeral_locals": [{"mount_name": "e", "seed_from_registry_id": "b"}]},
            {"ephemeral_locals": [{"mount_name": "e", "seed_from_registry_id": "a"}]},
            _project(),
        )


def test_a_caller_cannot_hide_a_mount_by_tagging_it_as_derived():
    forged = {
        "registry_refs": [{"mount_name": "p", "registry_entry_id": "other", "source": "project"}]
    }
    # Tagged refs are dropped (the project's own replace them), never trusted over the project's.
    merged = merge_memory(forged, None, _project("p"))
    assert [r["registry_entry_id"] for r in merged["registry_refs"]] == ["team-notes"]


def test_merge_ignores_a_non_dict_config():
    assert without_derived("nope") == {}
    assert not has_derived("nope")
    assert merge_memory(None, None, ProjectMemoryRefs()) == {}
    assert merge_memory(None, {"registry_refs": ["raw"]}, ProjectMemoryRefs()) == {
        "registry_refs": ["raw"]
    }


def test_nothing_in_a_mount_carries_trust():
    ref = _project("p").refs[0]
    mount = ProjectMemoryMount(project_id=uuid4(), name="p", registry_entry_id="e")
    assert not any("trust" in key for key in (*ref, *mount.model_dump()))
    # The only thing a session is told about trust is that it cannot raise its own.
    assert "cannot raise it" in FACTS


def test_the_resolver_hydrates_refs_from_the_registry():
    class One(MemoryMountDirectory):
        async def resolve(self, entry_id, *, tenant_id, owner_id):
            return RegistryTarget(
                id=entry_id,
                name="n",
                adapter="mimir.adapters.gbrain.GBrainMimirAdapter",
                kwargs={"k": 1},
                secret_kwargs_env={"S": "ENV"},
                auth_ref="cred",
                categories=("entity",),
            )

    repo = MemoryMounts()
    pid = uuid4()
    asyncio.run(
        repo.add(
            ProjectMemoryMount(
                project_id=pid, name="g", registry_entry_id="e1", access=MemoryAccess.READ_WRITE
            )
        )
    )
    refs = asyncio.run(
        ProjectMemoryResolver(repo, One()).for_launch(pid, tenant_id="acme", owner_id="alice")
    )
    assert refs.refs[0] == {
        "mount_name": "g",
        "registry_entry_id": "e1",
        "role": "shared",
        "default_read_priority": 10,
        "source": "project",
        "adapter": "mimir.adapters.gbrain.GBrainMimirAdapter",
        "kwargs": {"k": 1},
        "secret_kwargs_env": {"S": "ENV"},
        "auth_ref": "cred",
        "categories": ["entity"],
    }


def test_a_launch_without_a_resolver_leaves_the_config_alone():
    sessions = SessionService(InMemorySessionRepository(), MagicMock())
    session = MagicMock()
    config = {"mimir": {"hosted_url": "x"}}
    assert asyncio.run(sessions.with_project_memory(session, config, None)) is config


def test_a_session_writes_as_an_agent_even_for_an_owner_who_is_a_person():
    from mimir.facts import FactCallerResolver
    from niuu.domain.mimir_facts import TrustLevel

    resolver = FactCallerResolver({"volundr:developer": "person"})
    owner = Principal("alice", "alice@x.test", "acme", ["volundr:developer"])
    session = Principal(
        "alice", "alice@x.test", "acme", ["volundr:developer"], actor="spiffe://c/ns/sa/session-1"
    )

    assert resolver.resolve(owner).trust is TrustLevel.PERSON
    assert resolver.resolve(session).trust is TrustLevel.AGENT


async def test_an_unreachable_registry_is_a_503_naming_the_knob_never_not_found(world, client):
    async def down(*_a, **_k):
        raise MemoryDirectoryUnavailableError("Mimir at x could not be reached; check base_url")

    world.registry.resolve = down
    base = f"/projects/{world.project.id}/memory"

    added = await client.post(
        base, json={"name": "n", "registry_entry_id": "team-notes"}, headers={"x-user": "alice"}
    )
    assert added.status_code == 503 and "could not be reached" in added.json()["detail"]

    open_project = await world.projects.register(ForgeProject(name="Open"), None)
    await world.repository.add(
        ProjectMemoryMount(project_id=open_project.id, name="n", registry_entry_id="team-notes")
    )
    world.sessions._authorization = None
    app = FastAPI()
    app.include_router(
        create_router(
            world.sessions,
            project_service=world.projects,
            session_participant_service=make_session_participant_service(world.sessions),
        )
    )
    body = SessionCreate(
        name="t", model="test", coordination=SessionCoordination(project_id=open_project.id)
    ).model_dump(mode="json")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as http:
        created = await http.post("/api/v1/forge/sessions", json=body)
        assert created.status_code == 503
        waiting = await world.sessions.create_session(
            name="again",
            model="test",
            coordination=SessionCoordination(project_id=open_project.id),
        )
        restarted = await http.post(f"/api/v1/forge/sessions/{waiting.id}/start")
        assert restarted.status_code == 503


def test_only_mounts_that_say_so_offer_the_fact_tools():
    from volundr.domain.project_memory import offers_facts

    assert not offers_facts(None) and not offers_facts({"registry_refs": [{"facts": False}]})
    assert offers_facts({"registry_refs": [{"facts": True}]})
    assert offers_facts({"instances": [{"name": "a", "url": "u", "facts": True}]})
    assert not offers_facts({"ephemeral_locals": [{"facts": True}]})
