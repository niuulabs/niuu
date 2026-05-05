"""Tests for the Ravn FastAPI sub-application."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ravn.api import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture
def client_with_personas(tmp_path) -> TestClient:
    """TestClient wired with a filesystem persona loader."""

    from ravn.adapters.personas.loader import FilesystemPersonaAdapter

    persona_dir = tmp_path / "personas"
    persona_dir.mkdir()
    loader = FilesystemPersonaAdapter(persona_dirs=[str(persona_dir)], include_builtin=True)
    return TestClient(create_app(persona_loader=loader))


def test_status_endpoint(client: TestClient):
    resp = client.get("/api/v1/ravn/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["service"] == "ravn"
    assert data["healthy"] is True
    assert "session_count" in data


def test_list_sessions_returns_seeded_runtime_sessions(client: TestClient):
    resp = client.get("/api/v1/ravn/sessions")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert data
    assert data[0]["ravn_id"]


def test_stop_session(client: TestClient):
    resp = client.post("/api/v1/ravn/sessions/my-session-id/stop")
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == "my-session-id"
    assert data["status"] == "stopped"


def test_personas_not_mounted_without_loader(client: TestClient):
    """Persona routes should not exist when no loader is provided."""
    resp = client.get("/api/v1/ravn/personas")
    assert resp.status_code == 404


def test_personas_mounted_with_loader(client_with_personas: TestClient):
    """Persona list endpoint exists and returns 200 when a loader is wired."""
    resp = client_with_personas.get("/api/v1/ravn/personas")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_create_app_no_args_returns_fastapi():
    from fastapi import FastAPI

    assert isinstance(create_app(), FastAPI)
