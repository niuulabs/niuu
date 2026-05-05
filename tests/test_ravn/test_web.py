"""Tests for ravn.web — standalone Ravn web server (NIU-647)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ravn.web import DEFAULT_WEB_PORT, create_standalone_app, serve

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _redirect_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))


@pytest.fixture()
def app() -> FastAPI:
    return create_standalone_app()


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Health and config
# ---------------------------------------------------------------------------


def test_health_endpoint(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["mode"] == "standalone"


def test_config_endpoint_returns_ravn_only(client: TestClient) -> None:
    resp = client.get("/config.json")
    assert resp.status_code == 200
    data = resp.json()
    assert data["modules"] == ["ravn"]


# ---------------------------------------------------------------------------
# Ravn API routes are mounted
# ---------------------------------------------------------------------------


def test_ravn_status_available(client: TestClient) -> None:
    resp = client.get("/api/v1/ravn/status")
    assert resp.status_code == 200
    assert resp.json()["service"] == "ravn"


def test_ravn_sessions_available(client: TestClient) -> None:
    resp = client.get("/api/v1/ravn/sessions")
    assert resp.status_code == 200


def test_ravn_settings_available(client: TestClient) -> None:
    resp = client.get("/api/v1/ravn/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Ravn"
    assert data["sections"][0]["id"] == "runtime"


def test_personas_list_available(client: TestClient) -> None:
    resp = client.get("/api/v1/ravn/personas")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_personas_validate_available(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/ravn/personas/validate",
        json={"name": "test", "fan_in_strategy": "merge"},
    )
    assert resp.status_code == 200
    assert resp.json()["valid"] is True


# ---------------------------------------------------------------------------
# CORS middleware is applied
# ---------------------------------------------------------------------------


def test_cors_header_on_persona_list(client: TestClient) -> None:
    resp = client.get(
        "/api/v1/ravn/personas",
        headers={"Origin": "http://localhost:3000"},
    )
    assert resp.status_code == 200
    assert "access-control-allow-origin" in resp.headers


# ---------------------------------------------------------------------------
# Default port constant
# ---------------------------------------------------------------------------


def test_default_port_is_7477() -> None:
    assert DEFAULT_WEB_PORT == 7477


# ---------------------------------------------------------------------------
# custom persona_dirs wired through
# ---------------------------------------------------------------------------


def test_custom_persona_dirs_accepted(tmp_path: Path) -> None:
    persona_dir = tmp_path / "my-personas"
    persona_dir.mkdir()
    app = create_standalone_app(persona_dirs=[str(persona_dir)])
    client = TestClient(app)
    resp = client.get("/api/v1/ravn/personas")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Static file serving (SPA mode with mock dist dir)
# ---------------------------------------------------------------------------


def test_spa_fallback_serves_index_for_unknown_route(tmp_path: Path) -> None:
    """When web/dist exists, unknown routes return index.html."""
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    index = dist / "index.html"
    index.write_text("<html>ravn</html>", encoding="utf-8")

    with patch("ravn.web._WEB_DIST", dist):
        app = create_standalone_app()

    client = TestClient(app)
    resp = client.get("/ravn/personas")
    assert resp.status_code == 200
    assert b"ravn" in resp.content


def test_spa_fallback_serves_existing_static_file(tmp_path: Path) -> None:
    """When the requested path exists as a file in dist, serve it directly."""
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    index = dist / "index.html"
    index.write_text("<html>root</html>", encoding="utf-8")
    existing = dist / "manifest.json"
    existing.write_text('{"app": true}', encoding="utf-8")

    with patch("ravn.web._WEB_DIST", dist):
        app = create_standalone_app()

    client = TestClient(app)
    resp = client.get("/manifest.json")
    assert resp.status_code == 200
    assert b"app" in resp.content


# ---------------------------------------------------------------------------
# serve() — calls uvicorn.run
# ---------------------------------------------------------------------------


def test_serve_calls_uvicorn_run(tmp_path: Path) -> None:
    """serve() should call uvicorn.run with the configured host and port."""
    mock_uvicorn = MagicMock()

    with patch.dict("sys.modules", {"uvicorn": mock_uvicorn}):
        serve(host="127.0.0.1", port=9999)

    mock_uvicorn.run.assert_called_once()
    call_kwargs = mock_uvicorn.run.call_args
    assert call_kwargs.kwargs.get("host") == "127.0.0.1" or call_kwargs.args[1:] == ("127.0.0.1",)


def test_serve_default_port(tmp_path: Path) -> None:
    """serve() passes the default port to uvicorn."""
    mock_uvicorn = MagicMock()

    with patch.dict("sys.modules", {"uvicorn": mock_uvicorn}):
        serve()

    mock_uvicorn.run.assert_called_once()
    _, kwargs = mock_uvicorn.run.call_args
    assert kwargs.get("port") == DEFAULT_WEB_PORT
