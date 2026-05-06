"""Tests for RavnPlugin — ServicePlugin implementation."""

from __future__ import annotations

import pytest

from niuu.ports.plugin import Service, ServiceDefinition, TUIPageSpec
from ravn.plugin import RavnPlugin, _RavnService

# ---------------------------------------------------------------------------
# _RavnService lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ravn_service_start_is_noop():
    svc = _RavnService()
    await svc.start()  # Should not raise


@pytest.mark.asyncio
async def test_ravn_service_stop_is_noop():
    svc = _RavnService()
    await svc.stop()  # Should not raise


@pytest.mark.asyncio
async def test_ravn_service_health_check_returns_true():
    svc = _RavnService()
    assert await svc.health_check() is True


# ---------------------------------------------------------------------------
# RavnPlugin identity
# ---------------------------------------------------------------------------


def test_plugin_name():
    plugin = RavnPlugin()
    assert plugin.name == "ravn"


def test_plugin_description():
    plugin = RavnPlugin()
    assert "agent" in plugin.description.lower()


# ---------------------------------------------------------------------------
# ServiceDefinition
# ---------------------------------------------------------------------------


def test_register_service_returns_definition():
    plugin = RavnPlugin()
    defn = plugin.register_service()
    assert isinstance(defn, ServiceDefinition)
    assert defn.name == "ravn"


def test_register_service_factory_creates_service():
    plugin = RavnPlugin()
    defn = plugin.register_service()
    svc = defn.factory()
    assert isinstance(svc, Service)


def test_create_service_returns_service():
    plugin = RavnPlugin()
    svc = plugin.create_service()
    assert isinstance(svc, Service)


def test_depends_on_returns_empty():
    plugin = RavnPlugin()
    assert list(plugin.depends_on()) == []


def test_register_service_depends_on_postgres():
    plugin = RavnPlugin()
    defn = plugin.register_service()
    assert "postgres" in defn.depends_on


def test_register_service_description_mentions_agent_runtime():
    plugin = RavnPlugin()
    defn = plugin.register_service()
    assert "agent" in defn.description.lower()


# ---------------------------------------------------------------------------
# TUI pages
# ---------------------------------------------------------------------------


def test_tui_pages_returns_agents_page():
    plugin = RavnPlugin()
    pages = plugin.tui_pages()
    assert len(pages) == 1
    spec = pages[0]
    assert isinstance(spec, TUIPageSpec)
    assert spec.name == "Agents"


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


def test_register_commands_adds_ravn_typer():
    import typer

    plugin = RavnPlugin()
    app = typer.Typer()
    plugin.register_commands(app)

    # The ravn sub-command group should now be registered.
    group_names = {g.name for g in app.registered_groups}
    assert "ravn" in group_names


def test_list_sessions_empty():
    """list command outputs message when no sessions."""
    from unittest.mock import MagicMock

    import typer
    from typer.testing import CliRunner

    plugin = RavnPlugin()
    app = typer.Typer()
    plugin.register_commands(app)

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.json.return_value = []
    mock_client.request_or_exit.return_value = mock_resp

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(plugin, "create_api_client", lambda: mock_client)
        result = CliRunner().invoke(app, ["ravn", "list"])

    assert result.exit_code == 0


def test_list_sessions_with_data():
    """list command outputs table when sessions exist."""
    from unittest.mock import MagicMock

    import typer
    from typer.testing import CliRunner

    plugin = RavnPlugin()
    app = typer.Typer()
    plugin.register_commands(app)

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.json.return_value = [
        {"id": "abc", "status": "running", "model": "gpt-4", "created_at": "now"}
    ]
    mock_client.request_or_exit.return_value = mock_resp

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(plugin, "create_api_client", lambda: mock_client)
        result = CliRunner().invoke(app, ["ravn", "list"])

    assert result.exit_code == 0


def test_list_sessions_json_output():
    """list --json command returns raw JSON."""
    from unittest.mock import MagicMock

    import typer
    from typer.testing import CliRunner

    plugin = RavnPlugin()
    app = typer.Typer()
    plugin.register_commands(app)

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.json.return_value = []
    mock_client.request_or_exit.return_value = mock_resp

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(plugin, "create_api_client", lambda: mock_client)
        result = CliRunner().invoke(app, ["ravn", "list", "--json"])

    assert result.exit_code == 0


def test_stop_session_command():
    """stop command calls API and prints success."""
    from unittest.mock import MagicMock

    import typer
    from typer.testing import CliRunner

    plugin = RavnPlugin()
    app = typer.Typer()
    plugin.register_commands(app)

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"status": "stopped"}
    mock_resp.text = '{"status": "stopped"}'
    mock_client.request_or_exit.return_value = mock_resp

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(plugin, "create_api_client", lambda: mock_client)
        result = CliRunner().invoke(app, ["ravn", "stop", "session-123"])

    assert result.exit_code == 0


def test_stop_session_json_output():
    """stop --json command returns raw JSON."""
    from unittest.mock import MagicMock

    import typer
    from typer.testing import CliRunner

    plugin = RavnPlugin()
    app = typer.Typer()
    plugin.register_commands(app)

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"status": "stopped"}
    mock_resp.text = '{"status": "stopped"}'
    mock_client.request_or_exit.return_value = mock_resp

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(plugin, "create_api_client", lambda: mock_client)
        result = CliRunner().invoke(app, ["ravn", "stop", "--json", "session-123"])

    assert result.exit_code == 0


def test_platform_status_command():
    """status command shows session count."""
    from unittest.mock import MagicMock

    import typer
    from typer.testing import CliRunner

    plugin = RavnPlugin()
    app = typer.Typer()
    plugin.register_commands(app)

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"session_count": 3}
    mock_client.request_or_exit.return_value = mock_resp

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(plugin, "create_api_client", lambda: mock_client)
        result = CliRunner().invoke(app, ["ravn", "status"])

    assert result.exit_code == 0


def test_platform_status_json_output():
    """status --json outputs raw JSON."""
    from unittest.mock import MagicMock

    import typer
    from typer.testing import CliRunner

    plugin = RavnPlugin()
    app = typer.Typer()
    plugin.register_commands(app)

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"session_count": 0}
    mock_client.request_or_exit.return_value = mock_resp

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(plugin, "create_api_client", lambda: mock_client)
        result = CliRunner().invoke(app, ["ravn", "status", "--json"])

    assert result.exit_code == 0


def test_create_api_client_returns_instance():
    """create_api_client returns a CLIAPIClient."""
    from niuu.cli_api_client import CLIAPIClient

    plugin = RavnPlugin()
    client = plugin.create_api_client()
    assert isinstance(client, CLIAPIClient)


# ---------------------------------------------------------------------------
# API app
# ---------------------------------------------------------------------------


def test_create_api_app_returns_fastapi():
    from pathlib import Path

    from fastapi import FastAPI

    plugin = RavnPlugin()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "home", staticmethod(lambda: Path("/tmp/ravn-plugin-test")))
        app = plugin.create_api_app()

    assert isinstance(app, FastAPI)


def test_create_api_app_does_not_mount_personas_endpoint():
    from pathlib import Path

    from fastapi.testclient import TestClient

    plugin = RavnPlugin()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "home", staticmethod(lambda: Path("/tmp/ravn-plugin-test")))
        app = plugin.create_api_app()

    client = TestClient(app)
    resp = client.get("/api/v1/ravn/personas")
    assert resp.status_code == 404


def test_create_api_app_lists_ravens_sessions_and_triggers():
    from pathlib import Path

    from fastapi.testclient import TestClient

    plugin = RavnPlugin()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "home", staticmethod(lambda: Path("/tmp/ravn-plugin-test")))
        app = plugin.create_api_app()

    client = TestClient(app)

    ravens = client.get("/api/v1/ravn/ravens")
    assert ravens.status_code == 200
    assert isinstance(ravens.json(), list)
    assert ravens.json()[0]["persona_name"]

    sessions = client.get("/api/v1/ravn/sessions")
    assert sessions.status_code == 200
    assert isinstance(sessions.json(), list)
    assert sessions.json()[0]["ravn_id"]

    triggers = client.get("/api/v1/ravn/triggers")
    assert triggers.status_code == 200
    assert isinstance(triggers.json(), list)
    assert triggers.json()[0]["persona_name"]


def test_create_api_app_supports_session_messages_and_budget_routes():
    from pathlib import Path

    from fastapi.testclient import TestClient

    plugin = RavnPlugin()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "home", staticmethod(lambda: Path("/tmp/ravn-plugin-test")))
        app = plugin.create_api_app()

    client = TestClient(app)

    session = client.get("/api/v1/ravn/sessions/10000001-0000-4000-8000-000000000001")
    assert session.status_code == 200
    assert session.json()["persona_name"] == "sindri"

    messages = client.get("/api/v1/ravn/sessions/10000001-0000-4000-8000-000000000001/messages")
    assert messages.status_code == 200
    assert isinstance(messages.json(), list)
    assert messages.json()[0]["session_id"] == "10000001-0000-4000-8000-000000000001"

    budget = client.get("/api/v1/ravn/budget/a3f1b2c4-8e7d-4a6f-9b0c-1d2e3f4a5b6c")
    assert budget.status_code == 200
    assert budget.json()["spent_usd"] > 0

    fleet = client.get("/api/v1/ravn/budget/fleet")
    assert fleet.status_code == 200
    assert fleet.json()["cap_usd"] > 0


def test_create_api_app_supports_trigger_crud():
    from pathlib import Path

    from fastapi.testclient import TestClient

    plugin = RavnPlugin()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "home", staticmethod(lambda: Path("/tmp/ravn-plugin-test")))
        app = plugin.create_api_app()

    client = TestClient(app)

    created = client.post(
        "/api/v1/ravn/triggers",
        json={
            "kind": "manual",
            "persona_name": "muninn",
            "spec": "index-now",
            "enabled": True,
        },
    )
    assert created.status_code == 201
    created_id = created.json()["id"]

    deleted = client.delete(f"/api/v1/ravn/triggers/{created_id}")
    assert deleted.status_code == 204
