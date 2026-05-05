"""Tests for Volundr plugin CLI commands (sessions)."""

from __future__ import annotations

import json

import httpx
import respx
import typer
from typer.testing import CliRunner

from volundr.plugin import VolundrPlugin

runner = CliRunner()
BASE = "http://localhost:8080"


def _make_app() -> typer.Typer:
    app = typer.Typer(no_args_is_help=False)
    VolundrPlugin().register_commands(app)
    return app


# ── sessions list ────────────────────────────────────────────────── #


class TestSessionsList:
    @respx.mock
    def test_list_renders_table(self) -> None:
        respx.get(f"{BASE}/api/v1/forge/sessions").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "id": "s1",
                        "name": "demo",
                        "status": "running",
                        "model": "opus",
                        "tokens": 1234,
                        "created_at": "2026-01-01",
                    }
                ],
            )
        )
        result = runner.invoke(_make_app(), ["sessions", "list"])
        assert result.exit_code == 0
        assert "demo" in result.output

    @respx.mock
    def test_list_json_output(self) -> None:
        data = [{"id": "s1", "name": "demo", "status": "running"}]
        respx.get(f"{BASE}/api/v1/forge/sessions").mock(return_value=httpx.Response(200, json=data))
        result = runner.invoke(_make_app(), ["sessions", "list", "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed[0]["id"] == "s1"

    @respx.mock
    def test_list_empty(self) -> None:
        respx.get(f"{BASE}/api/v1/forge/sessions").mock(return_value=httpx.Response(200, json=[]))
        result = runner.invoke(_make_app(), ["sessions", "list"])
        assert result.exit_code == 0
        assert "No active sessions" in result.output

    @respx.mock
    def test_list_401(self) -> None:
        respx.get(f"{BASE}/api/v1/forge/sessions").mock(
            return_value=httpx.Response(401, json={"detail": "unauthorized"})
        )
        result = runner.invoke(_make_app(), ["sessions", "list"])
        assert result.exit_code == 1

    @respx.mock
    def test_list_500(self) -> None:
        respx.get(f"{BASE}/api/v1/forge/sessions").mock(
            return_value=httpx.Response(500, text="internal error")
        )
        result = runner.invoke(_make_app(), ["sessions", "list"])
        assert result.exit_code == 1

    def test_list_connection_refused(self) -> None:
        with respx.mock:
            respx.get(f"{BASE}/api/v1/forge/sessions").mock(
                side_effect=httpx.ConnectError("Connection refused")
            )
            result = runner.invoke(_make_app(), ["sessions", "list"])
            assert result.exit_code == 1

    def test_list_timeout(self) -> None:
        with respx.mock:
            respx.get(f"{BASE}/api/v1/forge/sessions").mock(
                side_effect=httpx.ReadTimeout("timed out")
            )
            result = runner.invoke(_make_app(), ["sessions", "list"])
            assert result.exit_code == 1


# ── sessions create ──────────────────────────────────────────────── #


class TestSessionsCreate:
    @respx.mock
    def test_create_success(self) -> None:
        respx.post(f"{BASE}/api/v1/forge/sessions").mock(
            return_value=httpx.Response(201, json={"id": "s-new", "name": "test"})
        )
        result = runner.invoke(_make_app(), ["sessions", "create", "test"])
        assert result.exit_code == 0
        assert "s-new" in result.output

    @respx.mock
    def test_create_json_output(self) -> None:
        data = {"id": "s-new", "name": "test"}
        respx.post(f"{BASE}/api/v1/forge/sessions").mock(
            return_value=httpx.Response(201, json=data)
        )
        result = runner.invoke(_make_app(), ["sessions", "create", "test", "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["id"] == "s-new"

    @respx.mock
    def test_create_404(self) -> None:
        respx.post(f"{BASE}/api/v1/forge/sessions").mock(
            return_value=httpx.Response(404, json={"detail": "not found"})
        )
        result = runner.invoke(_make_app(), ["sessions", "create", "test"])
        assert result.exit_code == 1

    def test_create_connection_refused(self) -> None:
        with respx.mock:
            respx.post(f"{BASE}/api/v1/forge/sessions").mock(
                side_effect=httpx.ConnectError("Connection refused")
            )
            result = runner.invoke(_make_app(), ["sessions", "create", "test"])
            assert result.exit_code == 1


# ── sessions stop ────────────────────────────────────────────────── #


class TestSessionsStop:
    @respx.mock
    def test_stop_success(self) -> None:
        respx.post(f"{BASE}/api/v1/forge/sessions/s1/stop").mock(
            return_value=httpx.Response(200, json={"status": "stopped"})
        )
        result = runner.invoke(_make_app(), ["sessions", "stop", "s1"])
        assert result.exit_code == 0
        assert "stopped" in result.output.lower()

    @respx.mock
    def test_stop_json_output(self) -> None:
        respx.post(f"{BASE}/api/v1/forge/sessions/s1/stop").mock(
            return_value=httpx.Response(200, json={"status": "stopped"})
        )
        result = runner.invoke(_make_app(), ["sessions", "stop", "s1", "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["status"] == "stopped"

    @respx.mock
    def test_stop_not_found(self) -> None:
        respx.post(f"{BASE}/api/v1/forge/sessions/bad/stop").mock(
            return_value=httpx.Response(404, json={"detail": "session not found"})
        )
        result = runner.invoke(_make_app(), ["sessions", "stop", "bad"])
        assert result.exit_code == 1


# ── sessions delete ──────────────────────────────────────────────── #


class TestSessionsDelete:
    @respx.mock
    def test_delete_success(self) -> None:
        respx.delete(f"{BASE}/api/v1/forge/sessions/s1").mock(
            return_value=httpx.Response(200, json={"status": "deleted"})
        )
        result = runner.invoke(_make_app(), ["sessions", "delete", "s1"])
        assert result.exit_code == 0
        assert "deleted" in result.output.lower()

    @respx.mock
    def test_delete_json_output(self) -> None:
        respx.delete(f"{BASE}/api/v1/forge/sessions/s1").mock(
            return_value=httpx.Response(200, json={"status": "deleted"})
        )
        result = runner.invoke(_make_app(), ["sessions", "delete", "s1", "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["status"] == "deleted"

    @respx.mock
    def test_delete_not_found(self) -> None:
        respx.delete(f"{BASE}/api/v1/forge/sessions/bad").mock(
            return_value=httpx.Response(404, json={"detail": "not found"})
        )
        result = runner.invoke(_make_app(), ["sessions", "delete", "bad"])
        assert result.exit_code == 1

    def test_delete_connection_refused(self) -> None:
        with respx.mock:
            respx.delete(f"{BASE}/api/v1/forge/sessions/s1").mock(
                side_effect=httpx.ConnectError("Connection refused")
            )
            result = runner.invoke(_make_app(), ["sessions", "delete", "s1"])
            assert result.exit_code == 1
