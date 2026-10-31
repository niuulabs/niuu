"""The Guild facade carries lightweight projects to whichever host runs the session."""

import json
from uuid import uuid4

import httpx
import respx

from tests.test_niuu.test_rest_volundr import _headers
from tests.test_projects.test_mesh import client

THOR = "http://thor.test/api/v1/forge"
SPARK = "http://spark.test/api/v1/forge"


def project(project_id, **fields):
    return {
        "id": str(project_id),
        "slug": "garden",
        "name": "Garden",
        "description": "",
        "brief": "Grow tomatoes.",
        "repo_url": "",
        "workspace_path": "",
        "home_instance_id": "",
        "status": "active",
        "revision": 1,
        **fields,
    }


def launch_body(project_id, parent_instance="thor"):
    return {
        "name": "garden-worker",
        "instance_id": "spark",
        "coordination": {
            "project_id": str(project_id),
            "role": "worker",
            "parent": {"instance_id": parent_instance, "session_id": str(uuid4())},
        },
    }


@respx.mock
def test_launch_on_another_host_replicates_a_repository_less_project():
    project_id = uuid4()
    home = project(project_id)
    spark_copy = respx.get(f"{SPARK}/projects/{project_id}")
    spark_copy.side_effect = [
        httpx.Response(404, json={"detail": "Project not found"}),
        httpx.Response(200, json={**home, "home_instance_id": "thor"}),
    ]
    respx.get(f"{THOR}/projects/{project_id}").respond(200, json=home)
    register = respx.post(f"{SPARK}/projects").respond(201, json=home)
    respx.get(f"{THOR}/feature-flags").respond(200, json={"project_instance_id": "thor-native"})
    created = respx.post(f"{SPARK}/sessions").respond(201, json={"id": str(uuid4())})
    response = client().post(
        "/api/v1/forge/sessions", json=launch_body(project_id), headers=_headers()
    )
    assert response.status_code == 201, response.text
    replica = json.loads(register.calls[0].request.content)
    assert replica == {
        "id": str(project_id),
        "slug": "garden",
        "name": "Garden",
        "brief": "Grow tomatoes.",
        "home_instance_id": "thor",
        "workspace_path": "",
    }
    forwarded = json.loads(created.calls[0].request.content)
    # The parent's Guild id became that host's project instance id.
    assert forwarded["coordination"]["parent"]["instance_id"] == "thor-native"
    assert "instance_id" not in forwarded


@respx.mock
def test_launch_refreshes_a_stale_replica_from_its_home():
    project_id = uuid4()
    respx.get(f"{THOR}/projects/{project_id}").respond(
        200, json=project(project_id, brief="Grow basil.", name="Herbs")
    )
    respx.get(f"{SPARK}/projects/{project_id}").respond(
        200, json=project(project_id, home_instance_id="thor", revision=4)
    )
    refresh = respx.patch(f"{SPARK}/projects/{project_id}").respond(200, json={})
    respx.get(f"{THOR}/feature-flags").respond(200, json={"project_instance_id": "thor"})
    respx.post(f"{SPARK}/sessions").respond(201, json={"id": str(uuid4())})
    response = client().post(
        "/api/v1/forge/sessions", json=launch_body(project_id), headers=_headers()
    )
    assert response.status_code == 201
    assert json.loads(refresh.calls[0].request.content) == {
        "name": "Herbs",
        "brief": "Grow basil.",
        "revision": 4,
    }


@respx.mock
def test_old_host_without_lightweight_projects_is_reported_before_launch():
    project_id = uuid4()
    respx.get(f"{SPARK}/projects/{project_id}").respond(404)
    respx.get(f"{THOR}/projects/{project_id}").respond(200, json=project(project_id))
    respx.post(f"{SPARK}/projects").respond(422, json={"detail": "repo_url"})
    respx.get(f"{THOR}/feature-flags").respond(200, json={"project_instance_id": "thor"})
    created = respx.post(f"{SPARK}/sessions").respond(201, json={})
    response = client().post(
        "/api/v1/forge/sessions", json=launch_body(project_id), headers=_headers()
    )
    assert response.status_code == 501 and "Update" in response.json()["detail"]
    assert not created.called


@respx.mock
def test_old_host_gets_the_version_1_shape_for_a_repository_project():
    project_id = uuid4()
    repo = "https://github.com/xteo/garden"
    respx.get(f"{SPARK}/projects/{project_id}").side_effect = [
        httpx.Response(404),
        httpx.Response(200, json=project(project_id, repo_url=repo)),
    ]
    respx.get(f"{THOR}/projects/{project_id}").respond(200, json=project(project_id, repo_url=repo))
    register = respx.post(f"{SPARK}/projects")
    register.side_effect = [httpx.Response(422), httpx.Response(201, json={})]
    respx.get(f"{THOR}/feature-flags").respond(200, json={"project_instance_id": "thor"})
    respx.post(f"{SPARK}/sessions").respond(201, json={"id": str(uuid4())})
    response = client().post(
        "/api/v1/forge/sessions", json=launch_body(project_id), headers=_headers()
    )
    assert response.status_code == 201
    assert json.loads(register.calls[1].request.content) == {
        "id": str(project_id),
        "slug": "garden",
        "name": "Garden",
        "description": "",
        "repo_url": repo,
        "workspace_path": "",
    }


@respx.mock
def test_session_without_project_is_forwarded_untouched():
    created = respx.post(f"{SPARK}/sessions").respond(201, json={"id": str(uuid4())})
    body = {"name": "plain", "instance_id": "spark"}
    assert client().post("/api/v1/forge/sessions", json=body, headers=_headers()).status_code == 201
    assert json.loads(created.calls[0].request.content) == {"name": "plain"}


@respx.mock
def test_assignment_forwards_role_parent_and_detach():
    session_id, lead_id = uuid4(), uuid4()
    respx.get(f"{THOR}/sessions/{session_id}").respond(200, json={"id": str(session_id)})
    respx.get(f"{THOR}/sessions/{session_id}/project").respond(
        200, json={"revision": 3, "coordination": None}
    )
    respx.get(f"{THOR}/feature-flags").respond(200, json={"project_instance_id": "thor-native"})
    assignment = respx.put(f"{THOR}/sessions/{session_id}/project").respond(
        200, json={"session_id": str(session_id), "revision": 4, "coordination": None}
    )
    path = f"/api/v1/forge/sessions/{session_id}/project?instance_id=thor"
    body = {
        "project_id": None,
        "expected_revision": 3,
        "role": "worker",
        "parent": {"instance_id": "thor", "session_id": str(lead_id)},
    }
    assert client().put(path, json=body, headers=_headers()).status_code == 200
    assert json.loads(assignment.calls[0].request.content) == {
        "project_id": None,
        "expected_revision": 3,
        "role": "worker",
        "parent": {"instance_id": "thor-native", "session_id": str(lead_id)},
    }
    detach = {"project_id": None, "expected_revision": 3}
    assert client().put(path, json=detach, headers=_headers()).status_code == 200
    assert json.loads(assignment.calls[1].request.content) == detach
    # An old host rejects the new fields; that is an upgrade message, not a crash.
    assignment.respond(422, json={"detail": "extra fields"})
    response = client().put(path, json=body, headers=_headers())
    assert response.status_code == 501 and "Update" in response.json()["detail"]
