"""Existing-session assignment preserves execution and routes across independent hosts."""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import respx
from fastapi import FastAPI

from tests.test_niuu.test_rest_volundr import _headers
from tests.test_projects.test_mesh import client
from volundr.adapters.inbound.rest import create_router
from volundr.domain.models import Principal, Session, SessionStatus
from volundr.domain.project_ports import ProjectConflictError
from volundr.domain.projects import ForgeProject, SessionCoordination, SessionReference
from volundr.domain.services.session import SessionAccessDeniedError


async def test_assigns_running_session_without_launch_context_or_runtime_changes(rig):
    service, _, project, repo, pods = rig
    session = await repo.create(
        Session(
            name="free-session",
            status=SessionStatus.RUNNING,
            message_count=50,
            pod_name="existing-process",
            workload_config={"effort": "high"},
        )
    )
    broadcaster = AsyncMock()
    service.sessions._broadcaster = broadcaster
    assigned = await service.assign_session(session.id, project.id, 0, None)
    assert assigned.coordination == SessionCoordination(project_id=project.id)
    assert assigned.coordination_revision == 1
    assert assigned.model_dump(exclude={"coordination", "coordination_revision", "updated_at"}) == (
        session.model_dump(exclude={"coordination", "coordination_revision", "updated_at"})
    )
    assert not pods.start_calls
    broadcaster.publish_session_updated.assert_awaited_once_with(assigned)
    service.workspace.context.assert_not_awaited()


async def test_moves_worker_clears_old_parent_and_brief_preserves_objective(rig):
    service, _, destination, repo, _ = rig
    session = await repo.create(
        Session(
            name="worker",
            coordination=SessionCoordination(
                project_id=uuid4(),
                role="reviewer",
                objective="Review code",
                labels=["ui"],
                parent=SessionReference(instance_id="build", session_id=uuid4()),
                context_revision="old-context",
            ),
            workload_config={"project_context": "Old project instructions", "effort": "high"},
        )
    )
    moved = await service.assign_session(session.id, destination.id, 0, None)
    assert moved.coordination == SessionCoordination(
        project_id=destination.id, role="reviewer", objective="Review code", labels=["ui"]
    )
    assert moved.workload_config == {"effort": "high"}
    # An overlapping ordinary lifecycle write cannot restore the old membership or prompt.
    stale = await repo.update(session.model_copy(update={"name": "renamed"}))
    assert stale.name == "renamed"
    assert stale.coordination == moved.coordination
    assert stale.coordination_revision == 1
    assert "project_context" not in stale.workload_config


async def test_same_project_is_noop_and_keeps_coordinator_context(rig):
    service, _, project, repo, _ = rig
    session = await repo.create(
        Session(
            name="coordinator",
            coordination=SessionCoordination(
                project_id=project.id, role="coordinator", context_revision="current"
            ),
            workload_config={"project_context": "Current instructions"},
        )
    )
    assert await service.assign_session(session.id, project.id, 0, None) == session
    another = await service.register(
        ForgeProject(name="Other", slug="other", repo_url="https://example.test/other"), None
    )
    with pytest.raises(ProjectConflictError, match="Create a coordinator"):
        await service.assign_session(session.id, another.id, 0, None)


async def test_two_competing_assignments_cannot_silently_overwrite(rig):
    service, _, project, repo, _ = rig
    other = await service.register(
        ForgeProject(name="Other", slug="other", repo_url="https://example.test/other"), None
    )
    session = await repo.create(Session(name="free-session"))
    results = await asyncio.gather(
        service.assign_session(session.id, project.id, 0, None),
        service.assign_session(session.id, other.id, 0, None),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ProjectConflictError) for result in results) == 1
    assert (await repo.get(session.id)).coordination_revision == 1


async def test_atomic_repository_conflict_is_reported(rig):
    service, _, project, repo, _ = rig
    session = await repo.create(Session(name="free-session"))
    repo.update_coordination = AsyncMock(return_value=None)
    with pytest.raises(ProjectConflictError, match="reload"):
        await service.assign_session(session.id, project.id, 0, None)


async def test_api_reports_missing_archived_stale_and_unknown_fields(rig):
    service, _, project, repo, _ = rig
    session = await repo.create(Session(name="free-session"))
    app = FastAPI()
    app.include_router(create_router(service.sessions, project_service=service))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as api:
        path = f"/api/v1/forge/sessions/{session.id}/project"
        state = (await api.get(path)).json()
        assert state == {"session_id": str(session.id), "revision": 0, "coordination": None}
        body = {"project_id": str(project.id), "expected_revision": 0}
        assert (await api.put(path, json={**body, "parent": None})).status_code == 422
        assert (await api.put(path, json={**body, "project_id": None})).status_code == 422
        assert (await api.put(path, json={**body, "project_id": str(uuid4())})).status_code == 404
        assert (await api.get(f"/api/v1/forge/sessions/{uuid4()}/project")).status_code == 404
        assigned = await api.put(path, json=body)
        assert assigned.status_code == 200 and assigned.json()["revision"] == 1
        assert (await api.put(path, json=body)).status_code == 409
        await service.update(project.id, {"status": "archived"}, 1, None)
        assert (await api.put(path, json={**body, "expected_revision": 1})).status_code == 409
        assert (await repo.get(session.id)).coordination.project_id == project.id


async def test_session_update_access_and_project_scope_are_enforced(rig):
    service, _, project, repo, _ = rig
    session = await repo.create(Session(name="private"))
    service.sessions._check_access = AsyncMock(
        side_effect=SessionAccessDeniedError(session.id, "outsider")
    )
    with pytest.raises(SessionAccessDeniedError):
        await service.assign_session(
            session.id,
            project.id,
            0,
            Principal(user_id="outsider", email="", tenant_id="", roles=[]),
        )
    assert (await repo.get(session.id)).coordination is None


def mesh_routes(*, existing=False, owner_supported=True, source_status=200):
    session_id, project_id = uuid4(), uuid4()
    project = {
        "id": str(project_id),
        "slug": "kit",
        "name": "Kit",
        "status": "active",
        "repo_url": "https://example.test/kit",
        "workspace_path": "/build/projects/kit",
        "owner_id": "do-not-copy",
        "tenant_id": "do-not-copy",
    }
    owner = "http://thor.test/api/v1/forge"
    source = "http://spark.test/api/v1/forge"
    respx.get(f"{owner}/sessions/{session_id}").respond(200, json={"id": str(session_id)})
    state = respx.get(f"{owner}/sessions/{session_id}/project").respond(
        200 if owner_supported else 404, json={"revision": 0, "coordination": None}
    )
    source_read = respx.get(f"{source}/projects/{project_id}").respond(source_status, json=project)
    local = respx.get(f"{owner}/projects/{project_id}")
    local.side_effect = [
        httpx.Response(200 if existing else 404, json=project),
        httpx.Response(200, json={**project, "workspace_path": ""}),
    ]
    register = respx.post(f"{owner}/projects").respond(201, json=project)
    assignment = respx.put(f"{owner}/sessions/{session_id}/project").respond(
        200,
        json={
            "session_id": str(session_id),
            "revision": 1,
            "coordination": {"project_id": str(project_id), "role": "worker"},
        },
    )
    path = f"/api/v1/forge/sessions/{session_id}/project?instance_id=thor"
    body = {"project_id": str(project_id), "project_instance_id": "spark", "expected_revision": 0}
    return path, body, register, assignment, source_read, state


@respx.mock
@pytest.mark.parametrize("existing", [False, True])
def test_mesh_assigns_to_foreign_project_without_copying_its_checkout(existing):
    path, body, register, assignment, _, _ = mesh_routes(existing=existing)
    response = client().put(path, json=body, headers=_headers())
    assert response.status_code == 200
    assert response.json()["instance_id"] == "thor"
    assert assignment.calls[0].request.content == (
        f'{{"project_id":"{body["project_id"]}","expected_revision":0}}'.encode()
    )
    assert register.called != existing
    if register.called:
        import json

        payload = json.loads(register.calls[0].request.content)
        assert payload["id"] == body["project_id"] and payload["workspace_path"] == ""
        assert "owner_id" not in payload and "tenant_id" not in payload


@respx.mock
@pytest.mark.parametrize("supported,status,expected", [(False, 200, 501), (True, 503, 503)])
def test_mesh_does_not_mutate_when_owner_unsupported_or_holder_unavailable(
    supported, status, expected
):
    path, body, register, assignment, _, _ = mesh_routes(
        owner_supported=supported, source_status=status
    )
    assert client().put(path, json=body, headers=_headers()).status_code == expected
    assert not register.called and not assignment.called


@respx.mock
def test_mesh_conflict_stops_before_registration_and_get_uses_owner():
    path, body, register, assignment, source, state = mesh_routes()
    assert client().get(path, headers=_headers()).status_code == 200
    state.respond(200, json={"revision": 2})
    assert client().put(path, json=body, headers=_headers()).status_code == 409
    assert not register.called and not assignment.called and not source.called


@respx.mock
def test_feature_flags_honor_selected_host_identity():
    route = respx.get("http://spark.test/api/v1/forge/feature-flags").respond(
        200, json={"projects_enabled": True, "project_instance_id": "spark-native"}
    )
    result = client().get("/api/v1/forge/feature-flags?instance_id=spark", headers=_headers())
    assert result.status_code == 200 and route.called
    assert result.json()["project_instance_id"] == "spark-native"
