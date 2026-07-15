"""`ravn tool-build workflows`: inspect Ting workflows vs the build selector.

READ-ONLY UX that lists discoverable Ting workflows and marks which match the
realm build grant's workflow (or the static tool_builder_workflow selector).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

from typer.testing import CliRunner

from ravn.adapters.tool_build.http import HttpResponse
from ravn.cli.commands import (
    _discover_ting_workflows,
    _effective_workflow_selector,
    app,
)
from ravn.config import Settings

runner = CliRunner()


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


_WORKFLOWS_BODY = [
    {"id": "wf-1", "name": "tool-builder", "tags": ["tool-builder"]},
    {"id": "wf-2", "name": "docs-writer", "tags": ["docs"]},
]


def _settings_with_selector() -> Settings:
    return Settings(
        resident_evolution={
            "tool_build_adapter": "ravn.adapters.tool_build.TingWorkflowToolBuildBackend",
            "tool_build_kwargs": {"base_url": "http://volundr"},
            "tool_builder_workflow": {"names": ["tool-builder"]},
        }
    )


def _ting_backend(settings: Settings, client: _FakeHttpClient) -> Any:
    from ravn.adapters.tool_build import TingWorkflowToolBuildBackend

    return TingWorkflowToolBuildBackend(client=client, base_url="http://volundr")


def test_workflows_lists_and_marks_matching_workflow() -> None:
    settings = _settings_with_selector()
    client = _FakeHttpClient(
        {"/api/v1/ting/workflows": HttpResponse(status_code=200, body=_WORKFLOWS_BODY)}
    )
    backend = _ting_backend(settings, client)

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
        {"/api/v1/ting/workflows": HttpResponse(status_code=200, body=_WORKFLOWS_BODY)}
    )
    backend = _ting_backend(settings, client)

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


def test_workflows_reports_http_error() -> None:
    settings = _settings_with_selector()
    client = _FakeHttpClient({"/api/v1/ting/workflows": HttpResponse(status_code=503, body="down")})
    backend = _ting_backend(settings, client)

    with (
        patch("ravn.cli.commands.Settings", return_value=settings),
        patch("ravn.cli.commands._build_tool_build_backend", return_value=backend),
    ):
        result = runner.invoke(app, ["tool-build", "workflows"])

    assert result.exit_code == 1
    assert "HTTP 503" in result.output


def test_workflows_empty_list_reports_none_discovered() -> None:
    settings = _settings_with_selector()
    client = _FakeHttpClient({"/api/v1/ting/workflows": HttpResponse(status_code=200, body=[])})
    backend = _ting_backend(settings, client)

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
        client = object()
        base_url = "http://volundr"

        async def get(self, url: str) -> HttpResponse:  # pragma: no cover - via patch
            raise RuntimeError("boom")

    async def _raise(_backend: Any) -> tuple[list[Any], str]:
        raise RuntimeError("discovery exploded")

    with (
        patch("ravn.cli.commands.Settings", return_value=settings),
        patch("ravn.cli.commands._build_tool_build_backend", return_value=_Boom()),
        patch("ravn.cli.commands._discover_ting_workflows", _raise),
    ):
        result = runner.invoke(app, ["tool-build", "workflows"])

    assert result.exit_code == 1
    assert "Failed to list workflows" in result.output


def test_workflows_config_option_sets_env(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("RAVN_CONFIG", raising=False)
    settings = _settings_with_selector()
    client = _FakeHttpClient({"/api/v1/ting/workflows": HttpResponse(status_code=200, body=[])})
    backend = _ting_backend(settings, client)
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
# _effective_workflow_selector / _discover_ting_workflows unit paths
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
            "tool_build_adapter": "ravn.adapters.tool_build.TingWorkflowToolBuildBackend",
            "tool_build_kwargs": {"base_url": "http://volundr"},
        }
    )

    assert _effective_workflow_selector(settings) is None


async def test_discover_ting_workflows_errors_without_client() -> None:
    class _NoClient:
        client = None
        base_url = ""

    workflows, error = await _discover_ting_workflows(_NoClient())

    assert workflows == []
    assert "no client/base_url" in error
