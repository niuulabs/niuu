"""Project members: access rules, member management and room roles on project sessions."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from identity.adapters.cedar import CedarAuthorizationAdapter
from identity.models import UserStatus
from niuu.domain.agent_directory import AgentSkill
from niuu.domain.models import Principal
from tests.conftest import (
    InMemorySessionParticipantRepository,
    InMemorySessionRepository,
    MockPodManager,
)
from tests.test_projects.conftest import MemoryProjectMembers, MemoryProjects, MemoryUsers
from volundr.adapters.inbound.rest_projects import create_projects_router
from volundr.domain.models import Session, SessionStatus
from volundr.domain.project_ports import (
    LastOwnerError,
    MemberNotAllowedError,
    ProjectAccessDeniedError,
    ProjectConflictError,
    ProjectMemberNotFoundError,
    ProjectWorkspace,
)
from volundr.domain.projects import (
    ForgeProject,
    ProjectMember,
    ProjectRole,
    SessionCoordination,
)
from volundr.domain.services.projects import ProjectNotFoundError, ProjectService
from volundr.domain.services.session import SessionService
from volundr.domain.services.session_participants import SessionParticipantService
from volundr.domain.session_participants import (
    ParticipantRole,
    compute_room_grants,
    room_role_for_project_role,
)


def person(user_id, tenant="acme", roles=("volundr:developer",)):
    return Principal(user_id, f"{user_id}@x.test", tenant, list(roles))


ALICE = person("alice")
BOB = person("bob")
CAROL = person("carol")
ADMIN = person("root", roles=("volundr:admin",))
OTHER_ADMIN = person("root2", tenant="other", roles=("volundr:admin",))


class World:
    def __init__(self):
        self.users = MemoryUsers()
        for name in ("alice", "bob", "carol", "owner"):
            self.users.seed(name, "acme", name=name.title())
        self.users.seed("dave", "other")
        self.users.seed("erin", "acme", status=UserStatus.SUSPENDED)
        self.members = MemoryProjectMembers(self.users)
        self.sessions_repo = InMemorySessionRepository()
        self.sessions = SessionService(
            self.sessions_repo, MockPodManager(), authorization=CedarAuthorizationAdapter()
        )
        self.service = ProjectService(
            MemoryProjects(self.members),
            AsyncMock(spec=ProjectWorkspace),
            self.sessions,
            instance_id="thor",
            members=self.members,
            users=self.users,
        )
        self.participants = SessionParticipantService(
            InMemorySessionParticipantRepository(),
            self.sessions,
            self.users,
            project_members=self.members,
        )
        self.project: ForgeProject | None = None

    async def create_project(self, principal=ALICE, name="Lexi"):
        self.project = await self.service.register(ForgeProject(name=name), principal)
        return self.project

    async def add(self, user_id, role, principal=ALICE, **kwargs):
        return await self.service.member_service.add(
            self.project.id, principal, user_id=user_id, role=role, **kwargs
        )

    async def session(self, project=True, **kwargs):
        coordination = SessionCoordination(project_id=self.project.id) if project else None
        session = Session(
            name="s", model="m", owner_id="owner", tenant_id="acme", coordination=coordination
        )
        return await self.sessions_repo.create(session.model_copy(update=kwargs))


@pytest.fixture
async def world():
    w = World()
    await w.create_project()
    return w


# --- creator --------------------------------------------------------------------------


async def test_creator_is_an_owner_member(world):
    members = await world.service.member_service.list(world.project.id, ALICE)
    assert [(m.user_id, m.role) for m in members] == [("alice", ProjectRole.OWNER)]
    assert members[0].tenant_id == "acme"


async def test_project_without_identity_belongs_to_the_identityless_caller():
    w = World()
    project = await w.service.register(ForgeProject(name="Solo"), None)
    assert (await w.service.get(project.id, None)).id == project.id
    assert await w.members.list_for_project(project.id) == []
    with pytest.raises(ProjectNotFoundError):
        await w.service.get(project.id, ALICE)


# --- reading and editing the project --------------------------------------------------


async def test_non_member_cannot_see_the_project(world):
    with pytest.raises(ProjectNotFoundError):
        await world.service.get(world.project.id, BOB)
    assert await world.service.list(BOB) == []


async def test_every_role_can_see_the_project_and_list_it(world):
    for role in ProjectRole:
        await world.add("bob", role)
        assert (await world.service.get(world.project.id, BOB)).id == world.project.id
        assert [p.id for p in await world.service.list(BOB)] == [world.project.id]
        await world.service.member_service.remove(world.project.id, ALICE, "bob")


async def test_unknown_project_is_not_found(world):
    with pytest.raises(ProjectNotFoundError):
        await world.service.get(uuid4(), ALICE)


async def test_tenant_admin_is_admitted_to_the_tenants_projects_only(world):
    assert (await world.service.get(world.project.id, ADMIN)).id == world.project.id
    with pytest.raises(ProjectNotFoundError):
        await world.service.get(world.project.id, OTHER_ADMIN)


async def test_update_needs_the_contributor_role(world):
    await world.add("bob", ProjectRole.VIEWER)
    with pytest.raises(ProjectAccessDeniedError):
        await world.service.update(world.project.id, {"description": "x"}, 1, BOB)
    await world.service.member_service.change(
        world.project.id, ALICE, "bob", role=ProjectRole.CONTRIBUTOR
    )
    updated = await world.service.update(world.project.id, {"description": "x"}, 1, BOB)
    assert updated.description == "x"


async def test_starting_work_in_the_project_needs_the_contributor_role(world):
    await world.add("bob", ProjectRole.VIEWER)
    coordination = SessionCoordination(project_id=world.project.id)
    with pytest.raises(ProjectAccessDeniedError):
        await world.service.briefing(coordination, BOB)
    await world.add("carol", ProjectRole.CONTRIBUTOR)
    coordination, _ = await world.service.briefing(coordination, CAROL)
    assert coordination.project_id == world.project.id


# --- managing members -----------------------------------------------------------------


async def test_owner_can_grant_any_role_including_owner(world):
    for role in ProjectRole:
        member = await world.add("bob", role)
        assert member.role == role
        assert member.added_by == "alice"
        await world.service.member_service.remove(world.project.id, ALICE, "bob")


async def test_added_member_keeps_expertise_and_skills(world):
    skill = AgentSkill(id="infra", name="Infrastructure")
    member = await world.add("bob", ProjectRole.VIEWER, expertise="Kubernetes", skills=[skill])
    assert member.expertise == "Kubernetes"
    assert [s.id for s in member.skills] == ["infra"]


async def test_tenant_admin_manages_members_without_being_one(world):
    await world.add("bob", ProjectRole.VIEWER, principal=ADMIN)
    assert (await world.members.get(world.project.id, "bob")).added_by == "root"


@pytest.mark.parametrize(
    "role", [ProjectRole.VIEWER, ProjectRole.CONTRIBUTOR, ProjectRole.APPROVER]
)
async def test_only_owners_manage_members(world, role):
    await world.add("bob", role)
    service = world.service.member_service
    with pytest.raises(ProjectAccessDeniedError):
        await world.add("carol", ProjectRole.VIEWER, principal=BOB)
    with pytest.raises(ProjectAccessDeniedError):
        await service.change(world.project.id, BOB, "bob", role=ProjectRole.OWNER)
    with pytest.raises(ProjectAccessDeniedError):
        await service.remove(world.project.id, BOB, "alice")
    with pytest.raises(ProjectAccessDeniedError):
        await service.candidates(world.project.id, BOB, "")


async def test_a_non_member_cannot_learn_about_the_project_through_members(world):
    service = world.service.member_service
    with pytest.raises(ProjectNotFoundError):
        await service.list(world.project.id, BOB)
    with pytest.raises(ProjectNotFoundError):
        await world.add("carol", ProjectRole.VIEWER, principal=BOB)


async def test_management_needs_a_signed_in_user(world):
    with pytest.raises(ProjectAccessDeniedError):
        await world.service.member_service.add(
            world.project.id, None, user_id="bob", role=ProjectRole.VIEWER
        )


@pytest.mark.parametrize("user_id", ["dave", "erin", "ghost"])
async def test_only_active_members_of_the_projects_org_can_be_added(world, user_id):
    with pytest.raises(MemberNotAllowedError) as refused:
        await world.add(user_id, ProjectRole.VIEWER)
    # One message for every reason: it must not reveal whether the user exists.
    assert str(refused.value) == "Cannot add this person to the project"
    assert await world.members.get(world.project.id, user_id) is None


async def test_adding_an_existing_member_conflicts(world):
    await world.add("bob", ProjectRole.VIEWER)
    with pytest.raises(ProjectConflictError):
        await world.add("bob", ProjectRole.OWNER)


async def test_the_last_owner_cannot_be_removed_or_demoted(world):
    service = world.service.member_service
    with pytest.raises(LastOwnerError):
        await service.remove(world.project.id, ALICE, "alice")
    with pytest.raises(LastOwnerError):
        await service.change(world.project.id, ALICE, "alice", role=ProjectRole.APPROVER)
    await world.add("bob", ProjectRole.OWNER)
    demoted = await service.change(world.project.id, ALICE, "alice", role=ProjectRole.VIEWER)
    assert demoted.role == ProjectRole.VIEWER
    with pytest.raises(LastOwnerError):
        await service.remove(world.project.id, BOB, "bob")


async def test_removed_member_loses_access(world):
    await world.add("bob", ProjectRole.APPROVER)
    await world.service.member_service.remove(world.project.id, ALICE, "bob")
    with pytest.raises(ProjectNotFoundError):
        await world.service.get(world.project.id, BOB)
    with pytest.raises(ProjectMemberNotFoundError):
        await world.service.member_service.remove(world.project.id, ALICE, "bob")


async def test_members_edit_their_own_expertise_and_skills(world):
    await world.add("bob", ProjectRole.VIEWER)
    await world.add("carol", ProjectRole.VIEWER)
    service = world.service.member_service
    skill = AgentSkill(id="db", name="Databases")
    mine = await service.change(
        world.project.id, BOB, "bob", expertise="Postgres tuning", skills=[skill]
    )
    assert mine.expertise == "Postgres tuning" and [s.id for s in mine.skills] == ["db"]
    with pytest.raises(ProjectAccessDeniedError):
        await service.change(world.project.id, BOB, "carol", expertise="no")
    with pytest.raises(ProjectAccessDeniedError):
        await service.change(world.project.id, BOB, "bob", role=ProjectRole.OWNER)
    assert (await world.members.get(world.project.id, "bob")).role == ProjectRole.VIEWER


async def test_owners_edit_anyones_expertise(world):
    await world.add("bob", ProjectRole.VIEWER)
    changed = await world.service.member_service.change(
        world.project.id, ALICE, "bob", expertise="Frontend", skills=[]
    )
    assert changed.expertise == "Frontend" and changed.skills == []


async def test_changing_a_missing_member_is_not_found(world):
    with pytest.raises(ProjectMemberNotFoundError):
        await world.service.member_service.change(world.project.id, ALICE, "bob", expertise="x")


async def test_candidates_are_active_org_members_not_yet_on_the_project(world):
    await world.add("bob", ProjectRole.VIEWER)
    found = await world.service.member_service.candidates(world.project.id, ALICE, " CAR ")
    assert [c.user_id for c in found] == ["carol"]
    everyone = await world.service.member_service.candidates(world.project.id, ALICE, "")
    assert {c.user_id for c in everyone} == {"carol", "owner"}


async def test_platform_roles_are_never_touched(world):
    before = list(world.users.memberships["bob"])
    await world.add("bob", ProjectRole.OWNER)
    assert world.users.memberships["bob"] == before


# --- room roles on the project's sessions ---------------------------------------------


@pytest.mark.parametrize(
    ("project_role", "room_role"),
    [
        (ProjectRole.VIEWER, "viewer"),
        (ProjectRole.CONTRIBUTOR, "viewer"),
        (ProjectRole.APPROVER, "approver"),
        (ProjectRole.OWNER, "approver"),
    ],
)
async def test_project_members_get_a_room_role_on_the_projects_sessions(
    world, project_role, room_role
):
    await world.add("bob", project_role)
    session = await world.session()
    assert await world.participants.effective_room_role(session, BOB) == room_role
    grants = await world.participants.active_grants(session)
    assert "bob" in grants.viewer_ids
    assert ("bob" in grants.approver_ids) == (room_role == "approver")


async def test_the_session_owner_stays_its_room_owner(world):
    await world.add("owner", ProjectRole.VIEWER)
    session = await world.session()
    assert await world.participants.effective_room_role(session, person("owner")) == "owner"


async def test_non_members_and_other_sessions_get_no_room_role(world):
    await world.add("bob", ProjectRole.OWNER)
    attached = await world.session()
    loose = await world.session(project=False)
    assert await world.participants.effective_room_role(attached, CAROL) is None
    assert await world.participants.effective_room_role(loose, BOB) is None


async def test_another_tenants_member_row_grants_nothing(world):
    await world.members.add(
        ProjectMember(
            project_id=world.project.id, user_id="dave", tenant_id="other", role=ProjectRole.OWNER
        )
    )
    session = await world.session()
    assert "dave" not in (await world.participants.active_grants(session)).viewer_ids


async def test_the_more_senior_of_a_session_grant_and_a_project_grant_applies(world):
    session = await world.session()
    owner = person("owner")
    # per-session approver, project viewer
    await world.participants.invite(
        session, owner, user_id="bob", role=ParticipantRole.APPROVER, expires_at=None
    )
    await world.participants.accept(session, BOB)
    await world.add("bob", ProjectRole.VIEWER)
    assert await world.participants.effective_room_role(session, BOB) == "approver"
    # per-session viewer, project approver
    await world.participants.invite(
        session, owner, user_id="carol", role=ParticipantRole.VIEWER, expires_at=None
    )
    await world.participants.accept(session, CAROL)
    await world.add("carol", ProjectRole.APPROVER)
    assert await world.participants.effective_room_role(session, CAROL) == "approver"


async def test_a_host_without_projects_derives_nothing(world):
    await world.add("bob", ProjectRole.OWNER)
    session = await world.session()
    participants = SessionParticipantService(
        InMemorySessionParticipantRepository(), world.sessions, world.users
    )
    assert await participants.effective_room_role(session, BOB) is None
    assert await participants.list_participant_sessions(BOB) == []


def test_project_role_to_room_role_ladder():
    assert [room_role_for_project_role(role).value for role in ProjectRole] == [
        "viewer",
        "viewer",
        "approver",
        "approver",
    ]
    assert compute_room_grants([], []).viewer_ids == frozenset()


async def test_project_sessions_appear_with_the_derived_room_role(world):
    await world.add("bob", ProjectRole.APPROVER)
    await world.add("carol", ProjectRole.VIEWER)
    attached = await world.session()
    await world.session(project=False)
    archived = await world.session(status=SessionStatus.ARCHIVED)

    pairs = await world.participants.list_participant_sessions(BOB)
    assert [(s.id, g.role.value, g.status.value) for s, g in pairs] == [
        (attached.id, "approver", "active")
    ]
    assert pairs[0][1].expires_at is None and pairs[0][1].invited_by == "alice"
    with_archived = await world.participants.list_participant_sessions(BOB, include_archived=True)
    assert {s.id for s, _ in with_archived} == {attached.id, archived.id}
    assert (await world.participants.list_participant_sessions(CAROL))[0][1].role.value == "viewer"
    assert await world.participants.list_participant_sessions(person("nobody")) == []


async def test_listing_prefers_the_more_senior_grant(world):
    session = await world.session()
    await world.participants.invite(
        session, person("owner"), user_id="bob", role=ParticipantRole.APPROVER, expires_at=None
    )
    await world.participants.accept(session, BOB)
    await world.add("bob", ProjectRole.VIEWER)
    [(_, grant)] = await world.participants.list_participant_sessions(BOB)
    assert grant.role == ParticipantRole.APPROVER
    await world.service.member_service.change(
        world.project.id, ALICE, "bob", role=ProjectRole.OWNER
    )
    [(_, grant)] = await world.participants.list_participant_sessions(BOB)
    # Equal seniority: the explicit per-session grant stays.
    assert grant.role == ParticipantRole.APPROVER and grant.invited_by == "owner"


# --- REST -----------------------------------------------------------------------------


@pytest.fixture
async def client(world):
    async def principal_for_request(request):
        user = request.headers.get("x-user")
        if user == "root":
            return ADMIN
        return person(user) if user else None

    app = FastAPI()
    app.include_router(create_projects_router(world.service, principal_for_request))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as c:
        yield c


def as_user(user):
    return {"x-user": user}


async def test_rest_members_roundtrip(world, client):
    base = f"/projects/{world.project.id}"
    listed = await client.get(f"{base}/members", headers=as_user("alice"))
    assert listed.status_code == 200
    assert [(m["user_id"], m["role"], m["display_name"]) for m in listed.json()] == [
        ("alice", "owner", "")
    ]

    added = await client.post(
        f"{base}/members",
        headers=as_user("alice"),
        json={"user_id": "bob", "role": "approver", "expertise": "Rust"},
    )
    assert added.status_code == 201
    assert added.json()["role"] == "approver" and added.json()["expertise"] == "Rust"

    again = await client.post(
        f"{base}/members", headers=as_user("alice"), json={"user_id": "bob", "role": "viewer"}
    )
    assert again.status_code == 409

    patched = await client.patch(
        f"{base}/members/bob", headers=as_user("bob"), json={"expertise": "Rust and Go"}
    )
    assert patched.status_code == 200 and patched.json()["expertise"] == "Rust and Go"

    promoted = await client.patch(
        f"{base}/members/bob", headers=as_user("alice"), json={"role": "owner"}
    )
    assert promoted.json()["role"] == "owner"

    removed = await client.delete(f"{base}/members/bob", headers=as_user("alice"))
    assert removed.status_code == 204
    gone = await client.delete(f"{base}/members/bob", headers=as_user("alice"))
    assert gone.status_code == 404


async def test_rest_refuses_what_the_rules_refuse(world, client):
    base = f"/projects/{world.project.id}"
    await world.add("bob", ProjectRole.CONTRIBUTOR)
    forbidden = await client.post(
        f"{base}/members", headers=as_user("bob"), json={"user_id": "carol", "role": "viewer"}
    )
    assert forbidden.status_code == 403
    stranger = await client.get(f"{base}/members", headers=as_user("carol"))
    assert stranger.status_code == 404
    outsider = await client.post(
        f"{base}/members", headers=as_user("alice"), json={"user_id": "dave", "role": "viewer"}
    )
    ghost = await client.post(
        f"{base}/members", headers=as_user("alice"), json={"user_id": "ghost", "role": "viewer"}
    )
    assert outsider.status_code == ghost.status_code == 422
    assert outsider.json() == ghost.json()
    last = await client.delete(f"{base}/members/alice", headers=as_user("alice"))
    assert last.status_code == 409
    nothing = await client.patch(f"{base}/members/bob", headers=as_user("alice"), json={})
    assert nothing.status_code == 422
    bad_role = await client.post(
        f"{base}/members", headers=as_user("alice"), json={"user_id": "carol", "role": "god"}
    )
    assert bad_role.status_code == 422
    unsigned = await client.post(f"{base}/members", json={"user_id": "carol", "role": "viewer"})
    assert unsigned.status_code == 403


async def test_rest_member_candidates(world, client):
    base = f"/projects/{world.project.id}"
    found = await client.get(
        f"{base}/member-candidates", params={"q": "bo"}, headers=as_user("alice")
    )
    assert found.status_code == 200
    assert found.json() == [{"user_id": "bob", "display_name": "Bob", "email": "bob@x.test"}]
    denied = await client.get(f"{base}/member-candidates", headers=as_user("bob"))
    assert denied.status_code == 404


async def test_rest_project_reads_and_receipt_acks_follow_roles(world, client):
    base = f"/projects/{world.project.id}"
    await world.add("bob", ProjectRole.VIEWER)
    assert (await client.get(base, headers=as_user("bob"))).status_code == 200
    assert (await client.get("/projects", headers=as_user("bob"))).json()[0]["id"] == str(
        world.project.id
    )
    ack = await client.post(f"{base}/receipts/{uuid4()}/ack", headers=as_user("bob"))
    assert ack.status_code == 403
    admin = await client.get(base, headers=as_user("root"))
    assert admin.status_code == 200


# --- the project layer's memory write prefixes ----------------------------------------


async def test_only_an_owner_sets_the_memory_write_prefixes(world):
    await world.add("bob", ProjectRole.CONTRIBUTOR)
    await world.add("carol", ProjectRole.APPROVER)
    for member in (BOB, CAROL):
        with pytest.raises(ProjectAccessDeniedError, match="owner"):
            await world.service.update(
                world.project.id, {"memory_write_prefixes": ["decisions/"]}, 1, member
            )
    assert (await world.service.get(world.project.id, ALICE)).memory_write_prefixes == ()

    updated = await world.service.update(
        world.project.id, {"memory_write_prefixes": ["decisions/", "runbooks/"]}, 1, ALICE
    )

    assert updated.memory_write_prefixes == ("decisions/", "runbooks/")
    assert updated.revision == 2


async def test_a_contributor_still_edits_the_rest_of_the_project(world):
    await world.add("bob", ProjectRole.CONTRIBUTOR)
    await world.service.update(world.project.id, {"memory_write_prefixes": ["plans/"]}, 1, ALICE)

    updated = await world.service.update(world.project.id, {"description": "x"}, 2, BOB)

    assert updated.description == "x"
    assert updated.memory_write_prefixes == ("plans/",)


async def test_an_empty_list_clears_the_prefixes(world):
    await world.service.update(world.project.id, {"memory_write_prefixes": ["plans/"]}, 1, ALICE)

    cleared = await world.service.update(world.project.id, {"memory_write_prefixes": []}, 2, ALICE)

    assert cleared.memory_write_prefixes == ()


@pytest.mark.parametrize("prefix", ["", "  ", "/abs/", "a/../b/", "../up/"])
async def test_a_prefix_that_is_not_a_relative_page_path_is_refused(world, prefix):
    with pytest.raises(ValueError, match="relative page path"):
        await world.service.update(world.project.id, {"memory_write_prefixes": [prefix]}, 1, ALICE)


async def test_repeated_prefixes_are_refused(world):
    with pytest.raises(ValueError, match="unique"):
        await world.service.update(
            world.project.id, {"memory_write_prefixes": ["a/", " a/"]}, 1, ALICE
        )


def test_prefixes_use_the_same_rule_as_a_mounts():
    from volundr.domain.project_memory import ProjectMemoryMount

    for bad in ("/abs/", "a/../b/", ""):
        with pytest.raises(ValueError, match="relative page path"):
            ForgeProject(name="p", memory_write_prefixes=(bad,))
        with pytest.raises(ValueError, match="relative page path"):
            ProjectMemoryMount(
                project_id=uuid4(),
                name="m",
                registry_entry_id="e",
                access="read_write",
                write_prefixes=(bad,),
            )


def test_a_project_without_prefixes_stores_the_document_it_always_did():
    """A rolled-back Forge binary reads the document with extra='forbid'."""
    assert "memory_write_prefixes" not in ForgeProject(name="p").document_json()
    stored = ForgeProject(name="p", memory_write_prefixes=("a/",)).document_json()
    assert ForgeProject.model_validate_json(stored).memory_write_prefixes == ("a/",)


async def test_rest_owner_sets_and_everyone_reads_the_memory_write_prefixes(world, client):
    base = f"/projects/{world.project.id}"
    await world.add("bob", ProjectRole.CONTRIBUTOR)

    denied = await client.patch(
        base,
        headers=as_user("bob"),
        json={"revision": 1, "memory_write_prefixes": ["decisions/"]},
    )
    assert denied.status_code == 403

    invalid = await client.patch(
        base, headers=as_user("alice"), json={"revision": 1, "memory_write_prefixes": ["/x/"]}
    )
    assert invalid.status_code == 422

    patched = await client.patch(
        base,
        headers=as_user("alice"),
        json={"revision": 1, "memory_write_prefixes": ["decisions/"]},
    )
    assert patched.status_code == 200
    assert patched.json()["memory_write_prefixes"] == ["decisions/"]

    read = await client.get(base, headers=as_user("bob"))
    assert read.json()["memory_write_prefixes"] == ["decisions/"]

    untouched = await client.patch(
        base, headers=as_user("alice"), json={"revision": 2, "brief": "Be brief."}
    )
    assert untouched.json()["memory_write_prefixes"] == ["decisions/"]
