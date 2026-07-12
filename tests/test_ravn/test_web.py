"""Tests for ravn.web — standalone Ravn web server (NIU-647)."""

from __future__ import annotations

import os
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
    for name in tuple(os.environ):
        if name.startswith("RAVN_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("RAVN_CONFIG", str(tmp_path / "missing-ravn.yaml"))
    monkeypatch.setenv("RAVN_GATEWAY__PLATFORM__BASE_URL", "http://localhost:8080")
    monkeypatch.setenv("RAVN_RESIDENT_DISCOVERY__ENABLED", "false")


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
    import httpx
    import respx

    with respx.mock(assert_all_called=False) as router:
        router.get("http://localhost:8080/api/v1/forge/sessions").mock(
            return_value=httpx.Response(200, json=[])
        )
        router.get("http://localhost:8080/api/v1/forge/resident-runtimes").mock(
            return_value=httpx.Response(200, json=[])
        )
        resp = client.get("/api/v1/ravn/status")

    assert resp.status_code == 200
    assert resp.json()["service"] == "ravn"


def test_ravn_sessions_available(client: TestClient) -> None:
    import httpx
    import respx

    # /sessions is real discovery now — proxies the Forge sessions API.
    with respx.mock(assert_all_called=False) as router:
        router.get("http://localhost:8080/api/v1/forge/sessions").mock(
            return_value=httpx.Response(200, json=[])
        )
        router.get("http://localhost:8080/api/v1/forge/resident-runtimes").mock(
            return_value=httpx.Response(200, json=[])
        )
        resp = client.get("/api/v1/ravn/sessions")
    assert resp.status_code == 200
    assert resp.json() == []


def test_ravn_settings_available(client: TestClient) -> None:
    import httpx
    import respx

    # The active-session count comes from real discovery now, which proxies
    # the Forge sessions API (the fleet count is cluster discovery, empty here).
    with respx.mock(assert_all_called=False) as router:
        router.get("http://localhost:8080/api/v1/forge/sessions").mock(
            return_value=httpx.Response(200, json=[])
        )
        router.get("http://localhost:8080/api/v1/forge/resident-runtimes").mock(
            return_value=httpx.Response(200, json=[])
        )
        resp = client.get("/api/v1/ravn/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Ravn"
    assert data["sections"][0]["id"] == "runtime"
    fields = {field["key"]: field for field in data["sections"][0]["fields"]}
    assert fields["trigger_store_available"]["value"] is False
    assert fields["budget_store_available"]["value"] is False


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
    """When the built UI dist exists, unknown routes return index.html."""
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


def test_create_standalone_app_warns_when_web_dist_missing(tmp_path: Path) -> None:
    """Missing UI assets should only disable static serving, not app startup."""
    missing_dist = tmp_path / "missing-dist"

    with patch("ravn.web._WEB_DIST", missing_dist), patch("ravn.web.logger.warning") as warning:
        app = create_standalone_app()

    assert isinstance(app, FastAPI)
    warning.assert_called_once()
    assert "static file serving disabled" in warning.call_args.args[0]
    assert warning.call_args.args[1] == missing_dist


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


def test_serve_reload_uses_import_string_factory() -> None:
    """Reload mode should use the factory import path instead of a materialized app."""
    mock_uvicorn = MagicMock()

    with patch.dict("sys.modules", {"uvicorn": mock_uvicorn}):
        serve(host="0.0.0.0", port=7478, reload=True)

    mock_uvicorn.run.assert_called_once_with(
        "ravn.web:create_standalone_app",
        host="0.0.0.0",
        port=7478,
        reload=True,
        factory=True,
    )
