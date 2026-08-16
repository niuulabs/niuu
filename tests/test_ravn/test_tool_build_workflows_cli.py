"""`ravn tool-build workflows`: inspect agent-card workflows vs the build selector.

READ-ONLY UX that lists workflow skills from the A2A agent card and marks which
match the realm build grant's workflow (or the static tool_builder_workflow
selector).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

from typer.testing import CliRunner

from ravn.adapters.tool_build import A2AToolBuildBackend
from ravn.adapters.tool_build.http import HttpResponse
from ravn.cli.commands import (
    _discover_workflows,
    _effective_workflow_selector,
    app,
)
from ravn.config import Settings

runner = CliRunner()

_CARD_URL = "http://volundr/.well-known/agent-card.json"


class _FakeHttpClient:
    """Scripted AsyncJsonHttpClient: maps a url-suffix -> HttpResponse."""

    def __init__(self, routes: dict[str, HttpResponse]) -> None:
        self._routes = dict(routes)
        self.calls: list[str] = []

    async def get(self, url: str) -> HttpResponse:
        self.calls.append(url)
        for suffix, response in self._routes.items():
            if url.endswith(suffix):
                return response
        raise AssertionError(f"no scripted response for GET {url}")

    async def post(self, url: str, json_body: dict[str, Any]) -> HttpResponse:
        raise AssertionError("workflows command must never POST — it is read-only")


_CARD_BODY = {
    "name": "Niuu Workflows",
    "supportedInterfaces": [
        {"url": "http://volundr/api/v1/ting/a2a", "protocolBinding": "JSONRPC"}
    ],
    "skills": [
        {"id": "wf-1", "name": "tool-builder", "tags": ["tool-builder"]},
        {"id": "wf-2", "name": "docs-writer", "tags": ["docs"]},
    ],
}


def _settings_with_selector() -> Settings:
    return Settings(
        resident_evolution={
            "tool_build_adapter": "ravn.adapters.tool_build.a2a.A2AToolBuildBackend",
            "tool_build_kwargs": {"card_url": _CARD_URL},
            "tool_builder_workflow": {"names": ["tool-builder"]},
        }
    )


def _a2a_backend(client: _FakeHttpClient) -> A2AToolBuildBackend:
    return A2AToolBuildBackend(client=client, card_url=_CARD_URL)


def test_workflows_lists_and_marks_matching_workflow() -> None:
    settings = _settings_with_selector()
    client = _FakeHttpClient(
        {"/.well-known/agent-card.json": HttpResponse(status_code=200, body=_CARD_BODY)}
    )
    backend = _a2a_backend(client)

    with (
        patch("ravn.cli.commands.Settings", return_value=settings),
        patch("ravn.cli.commands._build_tool_build_backend", return_value=backend),
    ):
        result = runner.invoke(app, ["tool-build", "workflows"])

    assert result.exit_code == 0
    # tool-builder matches the selector (marked with '*'); docs-writer does not.
    lines = result.stdout.strip().splitlines()
    marked = [line for line in lines if line.startswith("*")]
    assert any("tool-builder" in line for line in marked)
    assert all("docs-writer" not in line for line in marked)


def test_workflows_json_output() -> None:
    settings = _settings_with_selector()
    client = _FakeHttpClient(
        {"/.well-known/agent-card.json": HttpResponse(status_code=200, body=_CARD_BODY)}
    )
    backend = _a2a_backend(client)

    with (
        patch("ravn.cli.commands.Settings", return_value=settings),
        patch("ravn.cli.commands._build_tool_build_backend", return_value=backend),
    ):
        result = runner.invoke(app, ["tool-build", "workflows", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["selector"] == {"names": ["tool-builder"], "tags": [], "require_all_tags": False}
    by_id = {row["id"]: row for row in payload["workflows"]}
    assert by_id["wf-1"]["matches_selector"] is True
    assert by_id["wf-2"]["matches_selector"] is False


def test_workflows_no_backend_configured_exits_nonzero() -> None:
    settings = Settings()  # empty adapter -> inline authoring -> no backend

    with patch("ravn.cli.commands.Settings", return_value=settings):
        result = runner.invoke(app, ["tool-build", "workflows"])

    assert result.exit_code == 1
    assert "No tool-build backend configured" in result.output


def test_workflows_reports_unreadable_card() -> None:
    settings = _settings_with_selector()
    client = _FakeHttpClient(
        {"/.well-known/agent-card.json": HttpResponse(status_code=503, body="down")}
    )
    backend = _a2a_backend(client)

    with (
        patch("ravn.cli.commands.Settings", return_value=settings),
        patch("ravn.cli.commands._build_tool_build_backend", return_value=backend),
    ):
        result = runner.invoke(app, ["tool-build", "workflows"])

    assert result.exit_code == 1
    assert "could not read workflow skills" in result.output


def test_workflows_backend_without_card_reports_no_catalog() -> None:
    settings = _settings_with_selector()

    class _NoCatalogBackend:
        card_url = ""

    with (
        patch("ravn.cli.commands.Settings", return_value=settings),
        patch("ravn.cli.commands._build_tool_build_backend", return_value=_NoCatalogBackend()),
    ):
        result = runner.invoke(app, ["tool-build", "workflows"])

    assert result.exit_code == 1
    assert "exposes no workflow catalog" in result.output


def test_workflows_empty_list_reports_none_discovered() -> None:
    settings = _settings_with_selector()
    empty_card = {**_CARD_BODY, "skills": []}
    client = _FakeHttpClient(
        {"/.well-known/agent-card.json": HttpResponse(status_code=200, body=empty_card)}
    )
    backend = _a2a_backend(client)

    with (
        patch("ravn.cli.commands.Settings", return_value=settings),
        patch("ravn.cli.commands._build_tool_build_backend", return_value=backend),
    ):
        result = runner.invoke(app, ["tool-build", "workflows"])

    assert result.exit_code == 0
    assert "No workflows discovered" in result.stdout


def test_workflows_reports_discovery_exception() -> None:
    settings = _settings_with_selector()

    class _Boom:
        card_url = _CARD_URL
        client = object()

    async def _raise(_backend: Any) -> tuple[list[Any], str]:
        raise RuntimeError("discovery exploded")

    with (
        patch("ravn.cli.commands.Settings", return_value=settings),
        patch("ravn.cli.commands._build_tool_build_backend", return_value=_Boom()),
        patch("ravn.cli.commands._discover_workflows", _raise),
    ):
        result = runner.invoke(app, ["tool-build", "workflows"])

    assert result.exit_code == 1
    assert "Failed to list workflows" in result.output


def test_workflows_config_option_sets_env(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("RAVN_CONFIG", raising=False)
    settings = _settings_with_selector()
    empty_card = {**_CARD_BODY, "skills": []}
    client = _FakeHttpClient(
        {"/.well-known/agent-card.json": HttpResponse(status_code=200, body=empty_card)}
    )
    backend = _a2a_backend(client)
    cfg_path = tmp_path / "ravn.yaml"
    cfg_path.write_text("resident_evolution: {}\n")

    with (
        patch("ravn.cli.commands.Settings", return_value=settings),
        patch("ravn.cli.commands._build_tool_build_backend", return_value=backend),
    ):
        result = runner.invoke(app, ["tool-build", "workflows", "--config", str(cfg_path)])

    assert result.exit_code == 0
    import os

    assert os.environ["RAVN_CONFIG"] == str(cfg_path)


# ---------------------------------------------------------------------------
# _effective_workflow_selector / _discover_workflows unit paths
# ---------------------------------------------------------------------------


def test_effective_workflow_selector_prefers_realm_grant() -> None:
    settings = _settings_with_selector()  # static selector = tool-builder
    realm_selector = {"names": ["realm-workflow"]}

    with patch(
        "ravn.cli.commands._resolve_realm_build_config",
        return_value=type("_R", (), {"workflow_selector": realm_selector})(),
    ):
        result = _effective_workflow_selector(settings)

    assert result == realm_selector


def test_effective_workflow_selector_none_when_nothing_configured() -> None:
    settings = Settings(
        resident_evolution={
            "tool_build_adapter": "ravn.adapters.tool_build.a2a.A2AToolBuildBackend",
            "tool_build_kwargs": {"card_url": _CARD_URL},
        }
    )

    assert _effective_workflow_selector(settings) is None


async def test_discover_workflows_errors_without_card_url() -> None:
    class _NoCard:
        card_url = ""

    workflows, error = await _discover_workflows(_NoCard())

    assert workflows == []
    assert "exposes no workflow catalog" in error
