"""tool-build doctor CLI: diagnose the Valkyrie -> Ting/Forge path (Phase 0.2)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from ravn.adapters.tool_build import (
    A2AToolBuildBackend,
    ForgeSessionToolBuildBackend,
    HttpResponse,
    TingWorkflowToolBuildBackend,
)
from ravn.cli.commands import app
from ravn.config import Settings
from ravn.valkyrie_evolution.tool_build_doctor import (
    HopStatus,
    diagnose_tool_build,
)

runner = CliRunner()


class _FakeHttpClient:
    """Scripted AsyncJsonHttpClient: maps (method, url-suffix) -> HttpResponse."""

    def __init__(
        self,
        routes: dict[tuple[str, str], HttpResponse] | None = None,
        *,
        raise_on_get: Exception | None = None,
    ) -> None:
        self._routes = dict(routes or {})
        self._raise_on_get = raise_on_get
        self.calls: list[tuple[str, str]] = []

    async def get(self, url: str) -> HttpResponse:
        self.calls.append(("GET", url))
        if self._raise_on_get is not None:
            raise self._raise_on_get
        for (method, suffix), response in self._routes.items():
            if method == "GET" and url.endswith(suffix):
                return response
        raise AssertionError(f"no scripted response for GET {url}")

    async def post(self, url: str, json_body: dict[str, Any]) -> HttpResponse:
        self.calls.append(("POST", url))
        raise AssertionError("doctor must never POST — it is read-only")


def _settings(
    *,
    adapter: str = "",
    kwargs: dict[str, Any] | None = None,
    selector: dict[str, Any] | None = None,
) -> Settings:
    settings = Settings()
    settings.resident_evolution.tool_build_adapter = adapter
    settings.resident_evolution.tool_build_kwargs = dict(kwargs or {})
    if selector is not None:
        settings.resident_evolution.tool_builder_workflow.names = list(selector.get("names", []))
        settings.resident_evolution.tool_builder_workflow.tags = list(selector.get("tags", []))
    return settings


def _forge_backend(client: _FakeHttpClient) -> ForgeSessionToolBuildBackend:
    return ForgeSessionToolBuildBackend(client=client, base_url="http://forge")


def _ting_backend(
    client: _FakeHttpClient,
    *,
    workflow_id: str = "",
    workflow_selector: dict[str, Any] | None = None,
) -> TingWorkflowToolBuildBackend:
    return TingWorkflowToolBuildBackend(
        client=client,
        base_url="http://ting",
        workflow_id=workflow_id,
        workflow_selector=workflow_selector,
    )


def _statuses(report: Any) -> dict[int, HopStatus]:
    return {hop.number: hop.status for hop in report.hops}


# ---------------------------------------------------------------------------
# Adapter accessors (the minimal read-only surface the doctor relies on)
# ---------------------------------------------------------------------------


def test_backend_accessors_expose_base_url_and_client() -> None:
    client = _FakeHttpClient()
    forge = _forge_backend(client)
    ting = _ting_backend(client, workflow_id="wf-1")

    assert forge.base_url == "http://forge"
    assert forge.client is client
    assert ting.base_url == "http://ting"
    assert ting.client is client
    assert ting.workflow_id == "wf-1"
    assert ting.workflow_selector.configured is False


# ---------------------------------------------------------------------------
# Hop 1 — no adapter configured: everything SKIP
# ---------------------------------------------------------------------------


async def test_no_adapter_configured_skips_rest() -> None:
    report = await diagnose_tool_build(_settings(adapter=""))

    assert _statuses(report) == {1: HopStatus.SKIP}
    assert report.ok is True
    assert "inline authoring" in report.hops[0].reason


# ---------------------------------------------------------------------------
# Happy path — Ting backend, all PASS
# ---------------------------------------------------------------------------


async def test_ting_happy_path_all_pass() -> None:
    workflows = [
        {"id": "wf-research", "name": "Research", "tags": ["research"]},
        {"id": "wf-builder", "name": "Tool Builder", "tags": ["tool-builder"]},
    ]
    client = _FakeHttpClient({("GET", "/api/v1/ting/workflows"): HttpResponse(200, workflows)})
    backend = _ting_backend(client, workflow_selector={"tags": ["tool-builder"]})
    settings = _settings(
        adapter="ravn.adapters.tool_build.TingWorkflowToolBuildBackend",
        selector={"tags": ["tool-builder"]},
    )

    with patch("ravn.cli.commands._build_tool_build_backend", return_value=backend):
        report = await diagnose_tool_build(settings)

    statuses = _statuses(report)
    assert statuses == {
        1: HopStatus.PASS,
        2: HopStatus.PASS,
        3: HopStatus.PASS,
        4: HopStatus.PASS,
        5: HopStatus.PASS,
    }
    assert report.ok is True
    assert "Tool Builder" in report.hops[4].reason


async def test_forge_happy_path_skips_workflow_hop() -> None:
    client = _FakeHttpClient({("GET", "/api/v1/forge/health"): HttpResponse(200, {"status": "ok"})})
    backend = _forge_backend(client)
    settings = _settings(adapter="ravn.adapters.tool_build.ForgeSessionToolBuildBackend")

    with patch("ravn.cli.commands._build_tool_build_backend", return_value=backend):
        report = await diagnose_tool_build(settings)

    statuses = _statuses(report)
    assert statuses[4] is HopStatus.PASS
    assert statuses[5] is HopStatus.SKIP
    assert "forge_session" in report.hops[4].details.get("url", "") + report.hops[4].reason


# ---------------------------------------------------------------------------
# Hop 2 — construction failure
# ---------------------------------------------------------------------------


async def test_construct_failure_fails_and_skips_rest() -> None:
    settings = _settings(adapter="ravn.adapters.tool_build.ForgeSessionToolBuildBackend")

    with patch(
        "ravn.cli.commands._build_tool_build_backend",
        side_effect=RuntimeError("bad kwargs"),
    ):
        report = await diagnose_tool_build(settings)

    statuses = _statuses(report)
    assert statuses[2] is HopStatus.FAIL
    assert statuses[3] is HopStatus.SKIP
    assert statuses[4] is HopStatus.SKIP
    assert statuses[5] is HopStatus.SKIP
    assert "bad kwargs" in report.hops[1].reason
    assert report.ok is False


async def test_construct_returns_none_fails() -> None:
    settings = _settings(adapter="ravn.adapters.tool_build.ForgeSessionToolBuildBackend")

    with patch("ravn.cli.commands._build_tool_build_backend", return_value=None):
        report = await diagnose_tool_build(settings)

    assert _statuses(report)[2] is HopStatus.FAIL
    assert "returned None" in report.hops[1].reason


# ---------------------------------------------------------------------------
# Hop 3 — auth mechanisms
# ---------------------------------------------------------------------------


async def test_auth_external_token_env_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TB_TOKEN", "secret-value")
    client = _FakeHttpClient({("GET", "/api/v1/forge/health"): HttpResponse(200, {})})
    backend = _forge_backend(client)
    settings = _settings(
        adapter="ravn.adapters.tool_build.ForgeSessionToolBuildBackend",
        kwargs={"external_token_env": "TB_TOKEN"},
    )

    with patch("ravn.cli.commands._build_tool_build_backend", return_value=backend):
        report = await diagnose_tool_build(settings)

    hop3 = report.hops[2]
    assert hop3.status is HopStatus.PASS
    assert hop3.details["mechanism"] == "external_token_env"
    assert hop3.details["env_set"] is True
    # never leak the token value
    assert "secret-value" not in json.dumps(report.to_dict())


async def test_auth_external_token_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TB_TOKEN_MISSING", raising=False)
    client = _FakeHttpClient({("GET", "/api/v1/forge/health"): HttpResponse(200, {})})
    backend = _forge_backend(client)
    settings = _settings(
        adapter="ravn.adapters.tool_build.ForgeSessionToolBuildBackend",
        kwargs={"external_token_env": "TB_TOKEN_MISSING"},
    )

    with patch("ravn.cli.commands._build_tool_build_backend", return_value=backend):
        report = await diagnose_tool_build(settings)

    hop3 = report.hops[2]
    assert hop3.status is HopStatus.FAIL
    assert hop3.details["env_set"] is False
    assert report.ok is False


# ---------------------------------------------------------------------------
# Hop 4 — reachability
# ---------------------------------------------------------------------------


async def test_reachability_bad_status_fails() -> None:
    client = _FakeHttpClient(
        {("GET", "/api/v1/forge/health"): HttpResponse(503, {"status": "down"})}
    )
    backend = _forge_backend(client)
    settings = _settings(adapter="ravn.adapters.tool_build.ForgeSessionToolBuildBackend")

    with patch("ravn.cli.commands._build_tool_build_backend", return_value=backend):
        report = await diagnose_tool_build(settings)

    hop4 = report.hops[3]
    assert hop4.status is HopStatus.FAIL
    assert "HTTP 503" in hop4.reason
    assert report.ok is False


async def test_reachability_connection_error_fails() -> None:
    client = _FakeHttpClient(raise_on_get=ConnectionError("refused"))
    backend = _forge_backend(client)
    settings = _settings(adapter="ravn.adapters.tool_build.ForgeSessionToolBuildBackend")

    with patch("ravn.cli.commands._build_tool_build_backend", return_value=backend):
        report = await diagnose_tool_build(settings)

    hop4 = report.hops[3]
    assert hop4.status is HopStatus.FAIL
    assert "ConnectionError" in hop4.reason


async def test_ting_reachability_failure_skips_workflow_hop() -> None:
    client = _FakeHttpClient({("GET", "/api/v1/ting/workflows"): HttpResponse(500, "boom")})
    backend = _ting_backend(client, workflow_selector={"tags": ["tool-builder"]})
    settings = _settings(adapter="ravn.adapters.tool_build.TingWorkflowToolBuildBackend")

    with patch("ravn.cli.commands._build_tool_build_backend", return_value=backend):
        report = await diagnose_tool_build(settings)

    statuses = _statuses(report)
    assert statuses[4] is HopStatus.FAIL
    assert statuses[5] is HopStatus.SKIP


# ---------------------------------------------------------------------------
# Hop 5 — workflow discovery
# ---------------------------------------------------------------------------


async def test_workflow_id_configured_directly_passes() -> None:
    client = _FakeHttpClient({("GET", "/api/v1/ting/workflows"): HttpResponse(200, [])})
    backend = _ting_backend(client, workflow_id="wf-explicit")
    settings = _settings(adapter="ravn.adapters.tool_build.TingWorkflowToolBuildBackend")

    with patch("ravn.cli.commands._build_tool_build_backend", return_value=backend):
        report = await diagnose_tool_build(settings)

    hop5 = report.hops[4]
    assert hop5.status is HopStatus.PASS
    assert "wf-explicit" in hop5.reason


async def test_workflow_no_selector_and_no_id_fails() -> None:
    client = _FakeHttpClient({("GET", "/api/v1/ting/workflows"): HttpResponse(200, [])})
    backend = _ting_backend(client)
    settings = _settings(adapter="ravn.adapters.tool_build.TingWorkflowToolBuildBackend")

    with patch("ravn.cli.commands._build_tool_build_backend", return_value=backend):
        report = await diagnose_tool_build(settings)

    hop5 = report.hops[4]
    assert hop5.status is HopStatus.FAIL
    assert "no workflow_id" in hop5.reason


async def test_workflow_zero_matches_fails() -> None:
    workflows = [{"id": "wf-research", "name": "Research", "tags": ["research"]}]
    client = _FakeHttpClient({("GET", "/api/v1/ting/workflows"): HttpResponse(200, workflows)})
    backend = _ting_backend(client, workflow_selector={"tags": ["tool-builder"]})
    settings = _settings(adapter="ravn.adapters.tool_build.TingWorkflowToolBuildBackend")

    with patch("ravn.cli.commands._build_tool_build_backend", return_value=backend):
        report = await diagnose_tool_build(settings)

    hop5 = report.hops[4]
    assert hop5.status is HopStatus.FAIL
    assert "zero workflows" in hop5.reason


async def test_workflow_multiple_matches_fails() -> None:
    workflows = [
        {"id": "wf-a", "name": "Builder A", "tags": ["tool-builder"]},
        {"id": "wf-b", "name": "Builder B", "tags": ["tool-builder"]},
    ]
    client = _FakeHttpClient({("GET", "/api/v1/ting/workflows"): HttpResponse(200, workflows)})
    backend = _ting_backend(client, workflow_selector={"tags": ["tool-builder"]})
    settings = _settings(adapter="ravn.adapters.tool_build.TingWorkflowToolBuildBackend")

    with patch("ravn.cli.commands._build_tool_build_backend", return_value=backend):
        report = await diagnose_tool_build(settings)

    hop5 = report.hops[4]
    assert hop5.status is HopStatus.FAIL
    assert "matched 2 workflows" in hop5.reason


async def test_workflow_discovery_non_list_body_fails() -> None:
    client = _FakeHttpClient(
        {("GET", "/api/v1/ting/workflows"): HttpResponse(200, {"not": "a list"})}
    )
    backend = _ting_backend(client, workflow_selector={"tags": ["tool-builder"]})
    settings = _settings(adapter="ravn.adapters.tool_build.TingWorkflowToolBuildBackend")

    with patch("ravn.cli.commands._build_tool_build_backend", return_value=backend):
        report = await diagnose_tool_build(settings)

    hop5 = report.hops[4]
    assert hop5.status is HopStatus.FAIL
    assert "did not return a workflow list" in hop5.reason


async def test_workflow_relist_non_200_fails() -> None:
    """Hop 4 lists workflows once; Hop 5 re-lists — a flapping endpoint FAILs."""
    workflows = [{"id": "wf-builder", "name": "Tool Builder", "tags": ["tool-builder"]}]
    responses = iter([HttpResponse(200, workflows), HttpResponse(500, "flap")])

    class _FlappingClient(_FakeHttpClient):
        async def get(self, url: str) -> HttpResponse:
            self.calls.append(("GET", url))
            return next(responses)

    backend = _ting_backend(_FlappingClient(), workflow_selector={"tags": ["tool-builder"]})
    settings = _settings(adapter="ravn.adapters.tool_build.TingWorkflowToolBuildBackend")

    with patch("ravn.cli.commands._build_tool_build_backend", return_value=backend):
        report = await diagnose_tool_build(settings)

    assert report.hops[3].status is HopStatus.PASS
    assert report.hops[4].status is HopStatus.FAIL
    assert "did not return a workflow list" in report.hops[4].reason


# ---------------------------------------------------------------------------
# Defensive fallbacks — backend missing base_url / client
# ---------------------------------------------------------------------------


class _NoBaseUrlBackend:
    name = "forge_session"
    base_url = ""
    client = None


class _NoClientBackend:
    name = "forge_session"
    base_url = "http://forge"
    client = None


async def test_reachability_without_base_url_fails() -> None:
    settings = _settings(adapter="ravn.adapters.tool_build.ForgeSessionToolBuildBackend")

    with patch("ravn.cli.commands._build_tool_build_backend", return_value=_NoBaseUrlBackend()):
        report = await diagnose_tool_build(settings)

    assert report.hops[3].status is HopStatus.FAIL
    assert "no base_url" in report.hops[3].reason


async def test_reachability_without_client_fails() -> None:
    settings = _settings(adapter="ravn.adapters.tool_build.ForgeSessionToolBuildBackend")

    with patch("ravn.cli.commands._build_tool_build_backend", return_value=_NoClientBackend()):
        report = await diagnose_tool_build(settings)

    assert report.hops[3].status is HopStatus.FAIL
    assert "no HTTP client" in report.hops[3].reason


# ---------------------------------------------------------------------------
# CLI surface — CliRunner, text + JSON, exit codes
# ---------------------------------------------------------------------------


def test_cli_no_adapter_all_skip_exit_zero() -> None:
    with patch("ravn.cli.commands.Settings", return_value=_settings(adapter="")):
        result = runner.invoke(app, ["tool-build", "doctor"])

    assert result.exit_code == 0
    assert "Hop 1 — config: SKIP" in result.stdout


def test_cli_json_output_happy_path() -> None:
    client = _FakeHttpClient({("GET", "/api/v1/forge/health"): HttpResponse(200, {"status": "ok"})})
    backend = _forge_backend(client)
    settings = _settings(adapter="ravn.adapters.tool_build.ForgeSessionToolBuildBackend")

    with (
        patch("ravn.cli.commands.Settings", return_value=settings),
        patch("ravn.cli.commands._build_tool_build_backend", return_value=backend),
    ):
        result = runner.invoke(app, ["tool-build", "doctor", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["hops"][0]["status"] == "PASS"
    assert {hop["hop"] for hop in payload["hops"]} == {1, 2, 3, 4, 5}


def test_cli_failure_exits_nonzero() -> None:
    client = _FakeHttpClient({("GET", "/api/v1/forge/health"): HttpResponse(503, "down")})
    backend = _forge_backend(client)
    settings = _settings(adapter="ravn.adapters.tool_build.ForgeSessionToolBuildBackend")

    with (
        patch("ravn.cli.commands.Settings", return_value=settings),
        patch("ravn.cli.commands._build_tool_build_backend", return_value=backend),
    ):
        result = runner.invoke(app, ["tool-build", "doctor"])

    assert result.exit_code == 1
    assert "FAIL" in result.stdout


# ---------------------------------------------------------------------------
# A2A backend
# ---------------------------------------------------------------------------


_A2A_CARD_URL = "http://ting/.well-known/agent-card.json"


def _a2a_doctor_backend(
    client: _FakeHttpClient,
    *,
    workflow_id: str = "",
    workflow_selector: dict[str, Any] | None = None,
) -> A2AToolBuildBackend:
    return A2AToolBuildBackend(
        client=client,
        card_url=_A2A_CARD_URL,
        workflow_id=workflow_id,
        workflow_selector=workflow_selector,
    )


def _a2a_doctor_card(skills: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "name": "Niuu Workflows",
        "supportedInterfaces": [
            {"url": "http://ting/api/v1/ting/a2a", "protocolBinding": "JSONRPC"}
        ],
        "skills": skills,
    }


async def test_a2a_happy_path_all_pass() -> None:
    card = _a2a_doctor_card(
        [
            {"id": "wf-research", "name": "Research", "tags": ["research"]},
            {"id": "wf-builder", "name": "Tool Builder", "tags": ["tool-builder"]},
        ]
    )
    client = _FakeHttpClient({("GET", "/.well-known/agent-card.json"): HttpResponse(200, card)})
    backend = _a2a_doctor_backend(client, workflow_selector={"tags": ["tool-builder"]})
    settings = _settings(adapter="ravn.adapters.tool_build.a2a.A2AToolBuildBackend")

    with patch("ravn.cli.commands._build_tool_build_backend", return_value=backend):
        report = await diagnose_tool_build(settings)

    statuses = _statuses(report)
    assert statuses == {
        1: HopStatus.PASS,
        2: HopStatus.PASS,
        3: HopStatus.PASS,
        4: HopStatus.PASS,
        5: HopStatus.PASS,
    }
    assert report.ok is True
    # Reachability probes the card URL itself.
    assert _A2A_CARD_URL in report.hops[3].reason
    assert "Tool Builder" in report.hops[4].reason


async def test_a2a_selector_without_matching_skill_fails() -> None:
    card = _a2a_doctor_card([{"id": "wf-deploy", "name": "Deploy", "tags": ["deploy"]}])
    client = _FakeHttpClient({("GET", "/.well-known/agent-card.json"): HttpResponse(200, card)})
    backend = _a2a_doctor_backend(client, workflow_selector={"tags": ["tool-builder"]})
    settings = _settings(adapter="ravn.adapters.tool_build.a2a.A2AToolBuildBackend")

    with patch("ravn.cli.commands._build_tool_build_backend", return_value=backend):
        report = await diagnose_tool_build(settings)

    statuses = _statuses(report)
    assert statuses[4] is HopStatus.PASS
    assert statuses[5] is HopStatus.FAIL
    assert "zero workflows" in report.hops[4].reason
    assert report.ok is False


async def test_a2a_unreachable_card_fails_and_skips_discovery() -> None:
    client = _FakeHttpClient(raise_on_get=ConnectionError("dns failure"))
    backend = _a2a_doctor_backend(client, workflow_id="wf-1")
    settings = _settings(adapter="ravn.adapters.tool_build.a2a.A2AToolBuildBackend")

    with patch("ravn.cli.commands._build_tool_build_backend", return_value=backend):
        report = await diagnose_tool_build(settings)

    statuses = _statuses(report)
    assert statuses[4] is HopStatus.FAIL
    assert report.ok is False
