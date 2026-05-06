"""Tests for the Ravn personas REST API."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ravn.adapters.personas.loader import FilesystemPersonaAdapter
from ravn.api.personas import create_personas_router

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _redirect_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect Path.home() to tmp_path so saves never touch the real home dir."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))


@pytest.fixture()
def tmp_persona_dir(tmp_path: Path) -> Path:
    """Return a temporary directory for persona YAML files."""
    d = tmp_path / "personas"
    d.mkdir()
    return d


@pytest.fixture()
def loader(tmp_persona_dir: Path) -> FilesystemPersonaAdapter:
    """FilesystemPersonaAdapter with a single custom dir + built-ins enabled."""
    return FilesystemPersonaAdapter(persona_dirs=[str(tmp_persona_dir)], include_builtin=True)


@pytest.fixture()
def client(loader: FilesystemPersonaAdapter) -> TestClient:
    """TestClient with the personas router mounted."""
    app = FastAPI()
    app.include_router(create_personas_router(loader))
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CUSTOM_PERSONA = {
    "name": "test-agent",
    "system_prompt_template": "You are a test agent.",
    "allowed_tools": ["file"],
    "permission_mode": "read-only",
    "iteration_budget": 5,
}


def write_persona(directory: Path, name: str, data: dict) -> None:
    """Write a persona YAML file into directory."""
    (directory / f"{name}.yaml").write_text(yaml.dump(data), encoding="utf-8")


# ---------------------------------------------------------------------------
# GET /api/v1/ravn/personas — list
# ---------------------------------------------------------------------------


def test_list_returns_builtins(client: TestClient) -> None:
    resp = client.get("/api/v1/ravn/personas")
    assert resp.status_code == 200
    names = [p["name"] for p in resp.json()]
    # All 13 built-ins should be present
    assert "coding-agent" in names
    assert "research-agent" in names
    assert "reviewer" in names
    assert len(names) >= 13


def test_list_source_builtin_filter(client: TestClient) -> None:
    resp = client.get("/api/v1/ravn/personas?source=builtin")
    assert resp.status_code == 200
    data = resp.json()
    assert all(p["is_builtin"] for p in data)


def test_list_source_custom_filter(client: TestClient, tmp_persona_dir: Path) -> None:
    write_persona(tmp_persona_dir, "test-agent", _CUSTOM_PERSONA)
    resp = client.get("/api/v1/ravn/personas?source=custom")
    assert resp.status_code == 200
    # Custom-only: should include the custom file but not pure built-ins
    names = [p["name"] for p in resp.json()]
    assert "test-agent" in names


def test_list_all_includes_custom_and_builtin(client: TestClient, tmp_persona_dir: Path) -> None:
    write_persona(tmp_persona_dir, "test-agent", _CUSTOM_PERSONA)
    resp = client.get("/api/v1/ravn/personas")
    assert resp.status_code == 200
    names = [p["name"] for p in resp.json()]
    assert "test-agent" in names
    assert "coding-agent" in names


# ---------------------------------------------------------------------------
# GET /api/v1/ravn/personas/{name} — detail
# ---------------------------------------------------------------------------


def test_get_builtin_returns_correct_fields(client: TestClient) -> None:
    resp = client.get("/api/v1/ravn/personas/coding-agent")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "coding-agent"
    assert data["is_builtin"] is True
    assert data["has_override"] is False
    assert data["yaml_source"] == "[built-in]"
    assert "system_prompt_template" in data
    assert "llm" in data
    assert "produces" in data
    assert "consumes" in data
    assert "fan_in" in data
    # coding-agent specific checks
    assert data["permission_mode"] == "workspace-write"
    assert data["iteration_budget"] == 40
    assert "file" in data["allowed_tools"]


def test_get_custom_persona(client: TestClient, tmp_persona_dir: Path) -> None:
    write_persona(tmp_persona_dir, "test-agent", _CUSTOM_PERSONA)
    resp = client.get("/api/v1/ravn/personas/test-agent")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "test-agent"
    assert data["is_builtin"] is False
    assert data["permission_mode"] == "read-only"
    assert data["iteration_budget"] == 5


def test_get_nonexistent_returns_404(client: TestClient) -> None:
    resp = client.get("/api/v1/ravn/personas/does-not-exist")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/v1/ravn/personas/{name}/yaml — raw YAML
# ---------------------------------------------------------------------------


def test_get_yaml_returns_parseable_yaml(client: TestClient) -> None:
    resp = client.get("/api/v1/ravn/personas/coding-agent/yaml")
    assert resp.status_code == 200
    assert "text/yaml" in resp.headers["content-type"]
    parsed = yaml.safe_load(resp.text)
    assert isinstance(parsed, dict)
    assert parsed["name"] == "coding-agent"


def test_get_yaml_nonexistent_returns_404(client: TestClient) -> None:
    resp = client.get("/api/v1/ravn/personas/ghost/yaml")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/v1/ravn/personas — create
# ---------------------------------------------------------------------------


def test_create_persona_writes_file(client: TestClient, tmp_persona_dir: Path) -> None:
    payload = {
        "name": "new-agent",
        "system_prompt_template": "You are new.",
        "permission_mode": "read-only",
        "iteration_budget": 10,
    }
    resp = client.post("/api/v1/ravn/personas", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "new-agent"
    # Verify file on disk (save() writes to the first configured persona_dir)
    saved_file = tmp_persona_dir / "new-agent.yaml"
    assert saved_file.exists()


def test_create_persona_409_if_custom_exists(client: TestClient, tmp_persona_dir: Path) -> None:
    write_persona(tmp_persona_dir, "already-exists", {"name": "already-exists"})
    payload = {"name": "already-exists"}
    resp = client.post("/api/v1/ravn/personas", json=payload)
    assert resp.status_code == 409


def test_create_persona_409_if_builtin(client: TestClient) -> None:
    payload = {"name": "coding-agent"}
    resp = client.post("/api/v1/ravn/personas", json=payload)
    assert resp.status_code == 409


def test_create_persona_rejects_path_traversal(client: TestClient) -> None:
    payload = {"name": "../../evil"}
    resp = client.post("/api/v1/ravn/personas", json=payload)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# PUT /api/v1/ravn/personas/{name} — full replace
# ---------------------------------------------------------------------------


def test_put_builtin_creates_override(client: TestClient, tmp_persona_dir: Path) -> None:
    payload = {
        "name": "coding-agent",
        "system_prompt_template": "Overridden.",
        "permission_mode": "read-only",
    }
    resp = client.put("/api/v1/ravn/personas/coding-agent", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "coding-agent"
    # Override file should exist in the first configured persona_dir
    override_file = tmp_persona_dir / "coding-agent.yaml"
    assert override_file.exists()


def test_put_nonexistent_returns_404(client: TestClient) -> None:
    payload = {"name": "phantom"}
    resp = client.put("/api/v1/ravn/personas/phantom", json=payload)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/v1/ravn/personas/{name}
# ---------------------------------------------------------------------------


def test_delete_custom_removes_file(client: TestClient, tmp_persona_dir: Path) -> None:
    write_persona(tmp_persona_dir, "to-delete", {"name": "to-delete"})
    assert (tmp_persona_dir / "to-delete.yaml").exists()
    resp = client.delete("/api/v1/ravn/personas/to-delete")
    assert resp.status_code == 204
    assert not (tmp_persona_dir / "to-delete.yaml").exists()


def test_delete_pure_builtin_returns_400(client: TestClient) -> None:
    resp = client.delete("/api/v1/ravn/personas/coding-agent")
    assert resp.status_code == 400


def test_delete_nonexistent_returns_404(client: TestClient) -> None:
    resp = client.delete("/api/v1/ravn/personas/nope")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/v1/ravn/personas/{name}/fork
# ---------------------------------------------------------------------------


def test_fork_creates_copy_with_new_name(client: TestClient, tmp_persona_dir: Path) -> None:
    write_persona(tmp_persona_dir, "source-agent", _CUSTOM_PERSONA | {"name": "source-agent"})
    resp = client.post(
        "/api/v1/ravn/personas/source-agent/fork",
        json={"new_name": "forked-agent"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "forked-agent"
    forked_file = tmp_persona_dir / "forked-agent.yaml"
    assert forked_file.exists()


def test_fork_builtin_creates_custom(client: TestClient, tmp_persona_dir: Path) -> None:
    resp = client.post(
        "/api/v1/ravn/personas/coding-agent/fork",
        json={"new_name": "my-coding-agent"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "my-coding-agent"
    forked_file = tmp_persona_dir / "my-coding-agent.yaml"
    assert forked_file.exists()


def test_fork_nonexistent_source_returns_404(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/ravn/personas/nonexistent/fork",
        json={"new_name": "new-fork"},
    )
    assert resp.status_code == 404


def test_fork_409_if_new_name_exists_as_custom(client: TestClient, tmp_persona_dir: Path) -> None:
    write_persona(tmp_persona_dir, "existing", {"name": "existing"})
    resp = client.post(
        "/api/v1/ravn/personas/coding-agent/fork",
        json={"new_name": "existing"},
    )
    assert resp.status_code == 409


def test_fork_409_if_new_name_is_builtin(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/ravn/personas/coding-agent/fork",
        json={"new_name": "research-agent"},
    )
    assert resp.status_code == 409


def test_fork_rejects_path_traversal_new_name(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/ravn/personas/coding-agent/fork",
        json={"new_name": "../../evil"},
    )
    assert resp.status_code == 422
