"""Tool build backends: commission a build over fake Forge/Ting HTTP (NIU-1054)."""

from __future__ import annotations

import json

import pytest

from ravn.adapters.tool_build import (
    ForgeSessionToolBuildBackend,
    HttpResponse,
    TingWorkflowToolBuildBackend,
)
from ravn.adapters.tool_build._contract import parse_tool_build_response, poll_until
from ravn.ports.tool_build_backend import ToolBuildError, ToolBuildRequest


async def _no_sleep(_seconds: float) -> None:
    return None


def _request() -> ToolBuildRequest:
    return ToolBuildRequest(
        name="mimir_metric_window",
        description="Summarize a metric window.",
        build_request="Build a tool that queries a bounded metric window and summarizes it.",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        required_permission="mimir:read",
        declared_reach=[{"kind": "network", "target": "https://mimir", "access": "read"}],
        environment_id="cluster-a",
        valkyrie_id="valkyrie:k8s-a",
        domain="k8s",
        signal_context="OOMKilled spike in payments",
    )


_BUILT_CONTRACT = json.dumps(
    {
        "manifest": {
            "name": "mimir_metric_window",
            "description": "Summarize a metric window.",
            "input_schema": {"type": "object"},
            "required_permission": "mimir:read",
            "declared_reach": [{"kind": "network", "access": "read"}],
            "entry_point": "run",
        },
        "tool_code": "def run(input):\n    return {'points': 0}\n",
    }
)


class _FakeHttpClient:
    """Scripted AsyncJsonHttpClient: maps (method, url-suffix) -> HttpResponse(s)."""

    def __init__(self, routes: dict[tuple[str, str], list[HttpResponse]]) -> None:
        self._routes = {key: list(values) for key, values in routes.items()}
        self.calls: list[tuple[str, str]] = []

    def _match(self, method: str, url: str) -> HttpResponse:
        for (route_method, suffix), responses in self._routes.items():
            if route_method == method and url.endswith(suffix) and responses:
                return responses.pop(0) if len(responses) > 1 else responses[0]
        raise AssertionError(f"no scripted response for {method} {url}")

    async def get(self, url: str) -> HttpResponse:
        self.calls.append(("GET", url))
        return self._match("GET", url)

    async def post(self, url: str, json_body: dict) -> HttpResponse:
        self.calls.append(("POST", url))
        return self._match("POST", url)


# ---------------------------------------------------------------------------
# contract helpers
# ---------------------------------------------------------------------------


def test_parse_tool_build_response_extracts_manifest_and_code() -> None:
    result = parse_tool_build_response(
        f"here is the tool\n```json\n{_BUILT_CONTRACT}\n```\n", tool_name="mimir_metric_window"
    )
    assert result.manifest["name"] == "mimir_metric_window"
    assert result.tool_code.startswith("def run")


def test_parse_tool_build_response_rejects_missing_pieces() -> None:
    with pytest.raises(ToolBuildError, match="no JSON object"):
        parse_tool_build_response("no json here", tool_name="x")
    with pytest.raises(ToolBuildError, match="missing tool_code"):
        parse_tool_build_response('{"manifest": {"name": "x"}}', tool_name="x")


async def test_poll_until_stops_on_done_and_bounds_attempts() -> None:
    states = iter(["running", "running", "completed"])

    async def fetch() -> str:
        return next(states)

    result = await poll_until(
        fetch,
        lambda s: s == "completed",
        max_attempts=5,
        interval_seconds=0,
        sleep=_no_sleep,
    )
    assert result == "completed"


# ---------------------------------------------------------------------------
# Forge session backend
# ---------------------------------------------------------------------------


async def test_forge_session_backend_commissions_and_retrieves() -> None:
    client = _FakeHttpClient(
        {
            ("POST", "/api/v1/forge/sessions"): [HttpResponse(201, {"id": "sess-1"})],
            ("GET", "/api/v1/forge/sessions/sess-1"): [
                HttpResponse(200, {"status": "running"}),
                HttpResponse(200, {"status": "completed"}),
            ],
            ("GET", "/api/v1/forge/sessions/sess-1/chronicle"): [
                HttpResponse(200, {"content": _BUILT_CONTRACT})
            ],
        }
    )
    backend = ForgeSessionToolBuildBackend(
        client=client,
        base_url="http://forge",
        poll_interval_seconds=0,
        sleep=_no_sleep,
    )

    result = await backend.build(_request())

    assert result.manifest["name"] == "mimir_metric_window"
    assert result.tool_code.startswith("def run")
    assert result.provenance["backend"] == "forge_session"
    assert result.provenance["forge_session_id"] == "sess-1"


async def test_forge_session_backend_raises_on_failed_session() -> None:
    client = _FakeHttpClient(
        {
            ("POST", "/api/v1/forge/sessions"): [HttpResponse(201, {"id": "sess-2"})],
            ("GET", "/api/v1/forge/sessions/sess-2"): [HttpResponse(200, {"status": "failed"})],
        }
    )
    backend = ForgeSessionToolBuildBackend(
        client=client, base_url="http://forge", poll_interval_seconds=0, sleep=_no_sleep
    )
    with pytest.raises(ToolBuildError, match="ended in status 'failed'"):
        await backend.build(_request())


async def test_forge_session_backend_raises_on_create_error() -> None:
    client = _FakeHttpClient({("POST", "/api/v1/forge/sessions"): [HttpResponse(500, "boom")]})
    backend = ForgeSessionToolBuildBackend(client=client, base_url="http://forge")
    with pytest.raises(ToolBuildError, match="HTTP 500"):
        await backend.build(_request())


# ---------------------------------------------------------------------------
# Ting workflow backend
# ---------------------------------------------------------------------------


async def test_ting_workflow_backend_commissions_and_retrieves() -> None:
    client = _FakeHttpClient(
        {
            ("POST", "/api/v1/ting/workflows/wf-1/launch"): [
                HttpResponse(200, {"campaign_id": "camp-1", "session_id": "sess-x"})
            ],
            ("GET", "/api/v1/ting/research/campaigns/camp-1"): [
                HttpResponse(200, {"status": "RUNNING"}),
                HttpResponse(
                    200,
                    {
                        "status": "COMPLETED",
                        "artifacts": [{"path": "tools/x.py", "content": _BUILT_CONTRACT}],
                    },
                ),
            ],
        }
    )
    backend = TingWorkflowToolBuildBackend(
        client=client,
        base_url="http://ting",
        workflow_id="wf-1",
        poll_interval_seconds=0,
        sleep=_no_sleep,
    )

    result = await backend.build(_request())

    assert result.tool_code.startswith("def run")
    assert result.provenance["backend"] == "ting_workflow"
    assert result.provenance["ting_campaign_id"] == "camp-1"


async def test_ting_workflow_backend_requires_workflow_id() -> None:
    backend = TingWorkflowToolBuildBackend(
        client=_FakeHttpClient({}), base_url="http://ting", workflow_id=""
    )
    with pytest.raises(ToolBuildError, match="workflow_id"):
        await backend.build(_request())


async def test_ting_workflow_backend_raises_without_artifact() -> None:
    client = _FakeHttpClient(
        {
            ("POST", "/api/v1/ting/workflows/wf-1/launch"): [
                HttpResponse(200, {"campaign_id": "camp-2"})
            ],
            ("GET", "/api/v1/ting/research/campaigns/camp-2"): [
                HttpResponse(200, {"status": "COMPLETED", "artifacts": []})
            ],
        }
    )
    backend = TingWorkflowToolBuildBackend(
        client=client,
        base_url="http://ting",
        workflow_id="wf-1",
        poll_interval_seconds=0,
        sleep=_no_sleep,
    )
    with pytest.raises(ToolBuildError, match="no retrievable artifact"):
        await backend.build(_request())
