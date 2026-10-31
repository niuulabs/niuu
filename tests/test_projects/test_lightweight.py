"""Lightweight projects: a name is enough, the repository is optional, sessions form a tree."""

import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from tests.conftest import make_session_participant_service
from volundr.adapters.inbound.rest import SessionCreate, create_router
from volundr.adapters.outbound.k8s_storage import InMemoryStorageAdapter
from volundr.domain.models import Session
from volundr.domain.project_ports import ProjectConflictError
from volundr.domain.projects import ForgeProject, SessionCoordination, SessionReference


def member(project, role="worker", parent=None, **fields):
    return Session(
        name=fields.pop("name", role),
        coordination=SessionCoordination(project_id=project.id, role=role, parent=parent),
        **fields,
    )


def ref(session, instance="thor"):
    return SessionReference(instance_id=instance, session_id=session.id)


async def test_a_name_is_a_complete_project_with_unique_slugs(rig):
    service, *_ = rig
    first = await service.register(ForgeProject(name="Side Quest!"), None)
    second = await service.register(ForgeProject(name="side quest"), None)
    assert (first.slug, first.repo_url, first.workspace_path) == ("side-quest", "", "")
    assert second.slug == "side-quest-2"
    assert (await service.register(ForgeProject(name="✨"), None)).slug == "project"
    # A retry with the same client-generated id is idempotent.
    retry = await service.register(ForgeProject(id=first.id, name="Side Quest!"), None)
    assert retry.id == first.id and retry.slug == "side-quest"
    service.workspace.context.assert_not_awaited()


async def test_checkout_without_repository_is_rejected():
    with pytest.raises(ValueError, match="repository URL"):
        ForgeProject(name="Lexi", workspace_path="/projects/lexi")


async def test_stored_document_omits_unused_lightweight_fields():
    plain = ForgeProject(name="Lexi", slug="lexi", repo_url="https://github.com/xteo/project-lexi")
    assert {"brief", "home_instance_id"}.isdisjoint(json.loads(plain.document_json()))
    briefed = plain.model_copy(update={"brief": "Ship it"})
    assert json.loads(briefed.document_json())["brief"] == "Ship it"
    assert ForgeProject.model_validate_json(briefed.document_json()) == briefed


async def test_repository_less_launch_carries_brief_and_role_guidance(rig):
    service, forge, *_ = rig
    project = await service.register(
        ForgeProject(name="Garden", brief="Grow tomatoes; never use pesticide."), None
    )
    coordinator = await forge.create_and_start_session(
        SessionCreate(
            name="garden-lead",
            model="test",
            coordination=SessionCoordination(project_id=project.id, role="coordinator"),
        )
    )
    context = coordinator.workload_config["project_context"]
    assert "## Project brief\n\nGrow tomatoes; never use pesticide." in context
    assert "Workers you create with the Forge MCP create_session tool" in context
    assert "Meta-repository" not in context and "Project repository" not in context
    assert coordinator.coordination.context_revision.startswith("brief@")
    worker = await forge.create_and_start_session(
        SessionCreate(
            name="garden-worker",
            model="test",
            coordination=SessionCoordination(
                project_id=project.id, parent=ref(coordinator), objective="Water daily"
            ),
        )
    )
    context = worker.workload_config["project_context"]
    assert f"session_id={coordinator.id}" in context and "Assignment: Water daily" in context
    assert "You are a worker in this project." in context
    service.workspace.context.assert_not_awaited()


async def test_repository_without_local_checkout_still_launches(rig):
    service, forge, *_ = rig
    replica = await service.register(
        ForgeProject(
            name="Remote",
            repo_url="https://github.com/xteo/project-remote",
            home_instance_id="spark",
        ),
        None,
    )
    session = await forge.create_and_start_session(
        SessionCreate(
            name="remote-task",
            model="test",
            coordination=SessionCoordination(project_id=replica.id),
        )
    )
    context = session.workload_config["project_context"]
    assert "Project repository: https://github.com/xteo/project-remote" in context
    assert "Local project checkout: none on this host" in context
    assert session.coordination.context_revision == "empty"


async def test_brief_and_checkout_context_are_combined(rig):
    service, _, project, *_ = rig
    project = await service.update(project.id, {"brief": "Voice first."}, 1, None)
    context, revision = await service.context(project)
    assert context.startswith("## Project brief\n\nVoice first.\n\n## Repository context")
    assert "Keep project decisions in Git." in context
    assert revision.startswith("brief@") and revision.endswith("+revision-a")


async def test_repository_can_be_attached_replaced_and_detached(rig):
    service, *_ = rig
    project = await service.register(ForgeProject(name="Later"), None)
    service.workspace.inspect.return_value = ("https://github.com/xteo/later", None)
    attached = await service.update(project.id, {"workspace_path": "/projects/later"}, 1, None)
    assert (attached.repo_url, attached.workspace_path) == (
        "https://github.com/xteo/later",
        "/projects/later",
    )
    # Git spellings of the same repository match; a different remote does not.
    service.workspace.inspect.return_value = ("https://github.com/xteo/later", None)
    again = await service.update(
        attached.id,
        {"repo_url": "git@github.com:XTEO/later.git", "workspace_path": "/elsewhere/later"},
        2,
        None,
    )
    assert again.workspace_path == "/elsewhere/later"
    service.workspace.inspect.return_value = ("https://github.com/xteo/other", None)
    with pytest.raises(ProjectConflictError, match="not the project's repository"):
        await service.update(again.id, {"workspace_path": "/projects/other"}, 3, None)
    service.workspace.inspect.return_value = ("https://github.com/xteo/later", uuid4())
    with pytest.raises(ProjectConflictError, match="different project"):
        await service.update(again.id, {"workspace_path": "/projects/later"}, 3, None)
    replaced = await service.update(again.id, {"repo_url": "https://github.com/xteo/new"}, 3, None)
    assert (replaced.repo_url, replaced.workspace_path) == ("https://github.com/xteo/new", "")
    detached = await service.update(replaced.id, {"repo_url": ""}, 4, None)
    assert (detached.repo_url, detached.workspace_path) == ("", "")
    with pytest.raises(ValueError, match="also removes its checkout"):
        await service.update(detached.id, {"repo_url": "", "workspace_path": "/x"}, 5, None)


async def test_attach_with_role_and_join_parent_project(rig):
    service, _, project, repo, _ = rig
    lead = await repo.create(Session(name="lead"))
    lead = await service.assign_session(lead.id, project.id, 0, None, role="coordinator")
    assert (lead.coordination.role, lead.coordination.parent) == ("coordinator", None)
    free = await repo.create(Session(name="free"))
    joined = await service.assign_session(free.id, None, 0, None, parent=ref(lead))
    assert joined.coordination.project_id == project.id
    assert (joined.coordination.role, joined.coordination.parent) == ("worker", ref(lead))
    # Within a project, omitting the parent keeps it; a role change keeps it too.
    reviewer = await service.assign_session(free.id, project.id, 1, None, role="reviewer")
    assert (reviewer.coordination.role, reviewer.coordination.parent) == ("reviewer", ref(lead))
    unparented = await service.assign_session(free.id, project.id, 2, None, parent=None)
    assert unparented.coordination.parent is None


async def test_parent_rules_refuse_self_cycles_and_other_projects(rig):
    service, _, project, repo, _ = rig
    lead = await repo.create(member(project, "coordinator"))
    worker = await repo.create(member(project, parent=ref(lead)))
    with pytest.raises(ValueError, match="own parent"):
        await service.assign_session(lead.id, project.id, 0, None, parent=ref(lead))
    with pytest.raises(ValueError, match="own workers"):
        await service.assign_session(lead.id, project.id, 0, None, parent=ref(worker))
    elsewhere = await service.register(ForgeProject(name="Elsewhere"), None)
    stranger = await repo.create(member(elsewhere, "coordinator"))
    with pytest.raises(ValueError, match="different project"):
        await service.assign_session(worker.id, project.id, 0, None, parent=ref(stranger))
    with pytest.raises(ValueError, match="Name the project"):
        await service.assign_session(
            worker.id,
            None,
            0,
            None,
            parent=SessionReference(instance_id="spark", session_id=uuid4()),
        )
    # A foreign parent is a link, accepted when the project is named.
    remote = await service.assign_session(
        worker.id,
        project.id,
        0,
        None,
        parent=SessionReference(instance_id="spark", session_id=uuid4()),
    )
    assert remote.coordination.parent.instance_id == "spark"


async def test_moving_a_coordinator_brings_its_local_subtree(rig):
    service, _, project, repo, _ = rig
    lead = await repo.create(member(project, "coordinator"))
    worker = await repo.create(member(project, parent=ref(lead)))
    helper = await repo.create(member(project, parent=ref(worker)))
    remote_child = await repo.create(member(project, parent=ref(lead, "spark")))
    bystander = await repo.create(member(project))
    destination = await service.register(ForgeProject(name="Destination"), None)
    moved = await service.assign_session(lead.id, destination.id, 0, None)
    assert moved.coordination.project_id == destination.id
    for follower, parent in ((worker, lead), (helper, worker)):
        current = (await repo.get(follower.id)).coordination
        assert (current.project_id, current.parent) == (destination.id, ref(parent))
    for stayed in (remote_child, bystander):
        assert (await repo.get(stayed.id)).coordination.project_id == project.id


async def test_detaching_leaves_workers_in_the_project_without_a_parent(rig):
    service, _, project, repo, _ = rig
    lead = await repo.create(member(project, "coordinator"))
    worker = await repo.create(member(project, parent=ref(lead)))
    detached = await service.assign_session(lead.id, None, 0, None)
    assert detached.coordination is None and detached.coordination_revision == 1
    orphan = (await repo.get(worker.id)).coordination
    assert (orphan.project_id, orphan.parent) == (project.id, None)
    free = await repo.create(Session(name="free"))
    assert await service.assign_session(free.id, None, 0, None) == free


async def test_rest_contract_v2(rig):
    service, *_ = rig
    app = FastAPI()
    app.state.settings = SimpleNamespace(
        local_mounts=SimpleNamespace(enabled=False, mini_mode=False, allowed_prefixes=[])
    )
    app.state.admin_settings = {}
    app.state.storage = InMemoryStorageAdapter()
    app.include_router(
        create_router(
            service.sessions,
            project_service=service,
            session_participant_service=make_session_participant_service(service.sessions),
        )
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as api:
        created = await api.post("/api/v1/forge/projects", json={"name": "Quick"})
        assert created.status_code == 201
        project = created.json()
        assert (project["slug"], project["repo_url"], project["workspace_path"]) == (
            "quick",
            "",
            "",
        )
        path = f"/api/v1/forge/projects/{project['id']}"
        patched = await api.patch(
            path, json={"revision": 1, "brief": "Be quick.", "repo_url": None}
        )
        assert patched.status_code == 200 and patched.json()["brief"] == "Be quick."
        context = (await api.get(f"{path}/context")).json()
        assert context["context"] == "## Project brief\n\nBe quick."
        assert (await api.post(f"{path}/export")).status_code == 422
        lead = await service.sessions._repository.create(Session(name="lead"))
        membership = f"/api/v1/forge/sessions/{lead.id}/project"
        assigned = await api.put(
            membership,
            json={"project_id": project["id"], "expected_revision": 0, "role": "coordinator"},
        )
        assert assigned.json()["coordination"]["role"] == "coordinator"
        free = await service.sessions._repository.create(Session(name="free"))
        joined = await api.put(
            f"/api/v1/forge/sessions/{free.id}/project",
            json={
                "expected_revision": 0,
                "parent": {"instance_id": "thor", "session_id": str(lead.id)},
            },
        )
        assert joined.json()["coordination"]["project_id"] == project["id"]
        features = (await api.get("/api/v1/forge/feature-flags")).json()
        assert features["project_contract_version"] == 2 and features["project_lightweight"]
