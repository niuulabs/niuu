"""Tests for ravn.api.personas — the Ravn persona REST router (NIU-647).

These tests exercise the ``create_personas_router`` factory directly, wired
to a real ``FilesystemPersonaAdapter`` with a temporary directory so no
external services are required.
"""

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
    """Redirect Path.home() so saves never touch the real home dir."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))


@pytest.fixture()
def tmp_persona_dir(tmp_path: Path) -> Path:
    d = tmp_path / "personas"
    d.mkdir()
    return d


@pytest.fixture()
def loader(tmp_persona_dir: Path) -> FilesystemPersonaAdapter:
    return FilesystemPersonaAdapter(persona_dirs=[str(tmp_persona_dir)], include_builtin=True)


@pytest.fixture()
def client(loader: FilesystemPersonaAdapter) -> TestClient:
    app = FastAPI()
    app.include_router(create_personas_router(loader))
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture()
def loader_no_builtin(tmp_persona_dir: Path) -> FilesystemPersonaAdapter:
    return FilesystemPersonaAdapter(persona_dirs=[str(tmp_persona_dir)], include_builtin=False)


@pytest.fixture()
def client_no_builtin(loader_no_builtin: FilesystemPersonaAdapter) -> TestClient:
    app = FastAPI()
    app.include_router(create_personas_router(loader_no_builtin))
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

_CREATE_PAYLOAD = {
    "name": "test-agent",
    "system_prompt_template": "You are a test agent.",
    "allowed_tools": ["file"],
    "permission_mode": "read-only",
    "iteration_budget": 5,
}


def write_persona(directory: Path, name: str, data: dict) -> None:
    (directory / f"{name}.yaml").write_text(yaml.dump(data), encoding="utf-8")


# ---------------------------------------------------------------------------
# GET /api/v1/ravn/personas — list
# ---------------------------------------------------------------------------


def test_list_returns_builtins(client: TestClient) -> None:
    resp = client.get("/api/v1/ravn/personas")
    assert resp.status_code == 200
    names = [p["name"] for p in resp.json()]
    assert "coding-agent" in names
    assert len(names) >= 1


def test_list_source_filter_builtin(client: TestClient) -> None:
    resp = client.get("/api/v1/ravn/personas?source=builtin")
    assert resp.status_code == 200
    data = resp.json()
    assert all(p["is_builtin"] for p in data)


def test_list_source_filter_custom(client_no_builtin: TestClient, tmp_persona_dir: Path) -> None:
    # Write a YAML whose `name` field matches the filename we give it.
    write_persona(tmp_persona_dir, "custom-only-agent", {"name": "custom-only-agent"})
    resp = client_no_builtin.get("/api/v1/ravn/personas?source=custom")
    assert resp.status_code == 200
    names = [p["name"] for p in resp.json()]
    assert "custom-only-agent" in names


def test_list_returns_summary_shape(client: TestClient) -> None:
    resp = client.get("/api/v1/ravn/personas")
    assert resp.status_code == 200
    persona = resp.json()[0]
    assert "name" in persona
    assert "is_builtin" in persona
    assert "allowed_tools" in persona
    assert "iteration_budget" in persona


# ---------------------------------------------------------------------------
# POST /api/v1/ravn/personas/validate — validate
# ---------------------------------------------------------------------------


def test_validate_valid_persona(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/ravn/personas/validate",
        json={"name": "my-agent", "fan_in_strategy": "merge"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is True
    assert data["errors"] == []


def test_validate_invalid_fan_in_strategy(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/ravn/personas/validate",
        json={"name": "my-agent", "fan_in_strategy": "nonsense"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is False
    assert any("fan_in_strategy" in e for e in data["errors"])


def test_validate_does_not_save(client: TestClient, loader: FilesystemPersonaAdapter) -> None:
    client.post(
        "/api/v1/ravn/personas/validate",
        json={"name": "ephemeral", "fan_in_strategy": "merge"},
    )
    assert loader.load("ephemeral") is None


# ---------------------------------------------------------------------------
# GET /api/v1/ravn/personas/{name} — detail
# ---------------------------------------------------------------------------


def test_get_builtin_persona(client: TestClient) -> None:
    resp = client.get("/api/v1/ravn/personas/coding-agent")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "coding-agent"
    assert data["is_builtin"] is True
    assert "system_prompt_template" in data
    assert "llm" in data


def test_get_nonexistent_persona_returns_404(client: TestClient) -> None:
    resp = client.get("/api/v1/ravn/personas/no-such-persona")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/v1/ravn/personas/{name}/yaml — raw YAML
# ---------------------------------------------------------------------------


def test_get_persona_yaml_returns_text(client: TestClient) -> None:
    resp = client.get("/api/v1/ravn/personas/coding-agent/yaml")
    assert resp.status_code == 200
    assert "yaml" in resp.headers.get("content-type", "")
    assert "coding-agent" in resp.text


def test_get_persona_yaml_404_for_missing(client: TestClient) -> None:
    resp = client.get("/api/v1/ravn/personas/missing/yaml")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/v1/ravn/personas — create
# ---------------------------------------------------------------------------


def test_create_persona_returns_201(client_no_builtin: TestClient) -> None:
    resp = client_no_builtin.post("/api/v1/ravn/personas", json=_CREATE_PAYLOAD)
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "test-agent"
    assert data["permission_mode"] == "read-only"


def test_create_persona_conflict_for_duplicate(
    client_no_builtin: TestClient, tmp_persona_dir: Path
) -> None:
    write_persona(tmp_persona_dir, "test-agent", _CUSTOM_PERSONA)
    resp = client_no_builtin.post("/api/v1/ravn/personas", json=_CREATE_PAYLOAD)
    assert resp.status_code == 409


def test_create_builtin_returns_conflict(client: TestClient) -> None:
    payload = {**_CREATE_PAYLOAD, "name": "coding-agent"}
    resp = client.post("/api/v1/ravn/personas", json=payload)
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# PUT /api/v1/ravn/personas/{name} — replace
# ---------------------------------------------------------------------------


def test_replace_custom_persona(
    client_no_builtin: TestClient,
) -> None:
    # Create the persona first so it exists in tmp_persona_dir.
    client_no_builtin.post("/api/v1/ravn/personas", json=_CREATE_PAYLOAD)

    updated = {**_CREATE_PAYLOAD, "iteration_budget": 99}
    resp = client_no_builtin.put("/api/v1/ravn/personas/test-agent", json=updated)
    assert resp.status_code == 200
    assert resp.json()["iteration_budget"] == 99


def test_replace_nonexistent_returns_404(client_no_builtin: TestClient) -> None:
    resp = client_no_builtin.put("/api/v1/ravn/personas/ghost", json=_CREATE_PAYLOAD)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/v1/ravn/personas/{name} — delete
# ---------------------------------------------------------------------------


def test_delete_custom_persona(client_no_builtin: TestClient, tmp_persona_dir: Path) -> None:
    write_persona(tmp_persona_dir, "test-agent", _CUSTOM_PERSONA)
    resp = client_no_builtin.delete("/api/v1/ravn/personas/test-agent")
    assert resp.status_code == 204


def test_delete_nonexistent_returns_404(client_no_builtin: TestClient) -> None:
    resp = client_no_builtin.delete("/api/v1/ravn/personas/ghost")
    assert resp.status_code == 404


def test_delete_builtin_without_override_returns_400(client: TestClient) -> None:
    resp = client.delete("/api/v1/ravn/personas/coding-agent")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/v1/ravn/personas/{name}/fork
# ---------------------------------------------------------------------------


def test_fork_persona(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/ravn/personas/coding-agent/fork",
        json={"new_name": "my-coder"},
    )
    assert resp.status_code == 201
    assert resp.json()["name"] == "my-coder"


def test_fork_nonexistent_source_returns_404(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/ravn/personas/ghost/fork",
        json={"new_name": "my-ghost"},
    )
    assert resp.status_code == 404


def test_fork_to_existing_builtin_returns_409(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/ravn/personas/coding-agent/fork",
        json={"new_name": "coding-agent"},
    )
    assert resp.status_code == 409
