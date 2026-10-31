"""CLI-to-REST acceptance: stable requests, host routing, and useful failure status."""

import json
from uuid import uuid4

import httpx
import respx
from typer.testing import CliRunner

from volundr.cli import app

runner = CliRunner()


@respx.mock
def test_launch_preserves_full_contract_and_routes_host(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_TOKEN", "unit-test-token")
    body = {
        "name": "worker",
        "dispatch_id": str(uuid4()),
        "coordination": {"project_id": str(uuid4())},
    }
    path = tmp_path / "launch.json"
    path.write_text(json.dumps(body))
    route = respx.post("http://forge.test/api/v1/forge/sessions").mock(
        return_value=httpx.Response(201, json={"id": "one"})
    )
    result = runner.invoke(
        app, ["--url", "http://forge.test", "--instance", "spark", "sessions", "create", str(path)]
    )
    assert result.exit_code == 0, result.output
    sent = route.calls[0].request
    assert json.loads(sent.content) == {**body, "instance_id": "spark"}
    assert sent.url.params["instance_id"] == "spark"
    assert sent.headers["authorization"] == "Bearer unit-test-token"
    assert "unit-test-token" not in result.output


@respx.mock
def test_missing_stable_dispatch_is_rejected_before_network(tmp_path):
    path = tmp_path / "launch.json"
    path.write_text(json.dumps({"coordination": {"project_id": str(uuid4())}}))
    result = runner.invoke(app, ["sessions", "create", str(path)])
    assert result.exit_code != 0 and "dispatch_id" in result.output
    assert len(respx.calls) == 0


@respx.mock
def test_connection_failure_and_partial_mesh_are_explicit():
    route = respx.get("http://forge.test/api/v1/forge/projects")
    route.mock(side_effect=httpx.ConnectError("offline"))
    result = runner.invoke(app, ["--url", "http://forge.test", "projects", "list"])
    assert result.exit_code == 2 and "retry with the same request ID" in result.output
    route.mock(
        return_value=httpx.Response(
            200, json=[], headers={"X-Forge-Unavailable-Instances": "spark"}
        )
    )
    result = runner.invoke(app, ["--url", "http://forge.test", "projects", "list"])
    assert result.exit_code == 0 and "spark" in result.stderr
    assert json.loads(result.stdout) == []


@respx.mock
def test_error_response_is_not_mistaken_for_completion():
    respx.post("http://forge.test/api/v1/forge/projects").mock(
        return_value=httpx.Response(409, json={"detail": "Project changed"})
    )
    result = runner.invoke(
        app,
        ["--url", "http://forge.test", "request", "POST", "/projects", "--body", "-"],
        input="{}",
    )
    assert result.exit_code == 2 and "409" in result.output


@respx.mock
def test_create_project_by_name_and_update_repository():
    created = respx.post("http://forge.test/api/v1/forge/projects").respond(
        201, json={"id": "p", "slug": "garden"}
    )
    base = ["--url", "http://forge.test", "projects"]
    result = runner.invoke(app, [*base, "create", "Garden", "--brief", "Grow tomatoes."])
    assert result.exit_code == 0, result.output
    assert json.loads(created.calls[0].request.content) == {
        "name": "Garden",
        "brief": "Grow tomatoes.",
        "description": "",
    }
    project_id = str(uuid4())
    patched = respx.patch(f"http://forge.test/api/v1/forge/projects/{project_id}").respond(
        200, json={}
    )
    result = runner.invoke(
        app, [*base, "update", project_id, "--revision", "2", "--checkout", "/p", "--repo-url", ""]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(patched.calls[0].request.content) == {
        "repo_url": "",
        "workspace_path": "/p",
        "revision": 2,
    }


@respx.mock
def test_attach_reads_revision_and_sends_membership():
    session_id, lead, project = uuid4(), uuid4(), uuid4()
    url = f"http://forge.test/api/v1/forge/sessions/{session_id}/project"
    respx.get(url).respond(200, json={"revision": 5, "coordination": None})
    put = respx.put(url).respond(200, json={})
    base = ["--url", "http://forge.test", "sessions", "attach", str(session_id)]
    result = runner.invoke(app, [*base, "--parent", f"thor:{lead}"])
    assert result.exit_code == 0, result.output
    assert json.loads(put.calls[0].request.content) == {
        "expected_revision": 5,
        "project_id": None,
        "parent": {"instance_id": "thor", "session_id": str(lead)},
    }
    result = runner.invoke(app, [*base, "--project", str(project), "--role", "coordinator"])
    assert json.loads(put.calls[1].request.content) == {
        "expected_revision": 5,
        "project_id": str(project),
        "role": "coordinator",
    }
    result = runner.invoke(app, [*base, "--detach"])
    assert json.loads(put.calls[2].request.content) == {"expected_revision": 5, "project_id": None}
    assert runner.invoke(app, [*base, "--role", "worker"]).exit_code != 0
    assert runner.invoke(app, [*base, "--detach", "--role", "worker"]).exit_code != 0
    assert len(put.calls) == 3
