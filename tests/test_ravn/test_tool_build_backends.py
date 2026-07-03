"""Tool build backends: commission a build over fake Forge/Ting HTTP (NIU-1054)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from ravn.adapters.tool_build import (
    ForgeSessionToolBuildBackend,
    HttpResponse,
    TingWorkflowToolBuildBackend,
)
from ravn.adapters.tool_build._contract import (
    build_prompts,
    parse_tool_build_document,
    parse_tool_build_response,
    poll_until,
)
from ravn.adapters.tool_build.forge_session import _decode_canonical_body
from ravn.adapters.tool_build.http import client_from_workload_identity
from ravn.adapters.tool_build.ting_workflow import _decode_canonical_content
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


_CONTRACT_DOCUMENT = {
    "manifest": {
        "name": "mimir_metric_window",
        "description": "Summarize a metric window.",
        "input_schema": {"type": "object"},
        "required_permission": "mimir:read",
        "declared_reach": [{"kind": "network", "access": "read"}],
        "entry_point": "run",
    },
    "tool_code": "def run(input):\n    return {'points': 0}\n",
    "test_code": "def test_run():\n    assert run({}) == {'points': 0}\n",
    "requirements": ["httpx>=0.27"],
}

_BUILT_CONTRACT = json.dumps(_CONTRACT_DOCUMENT)

#: A pre-contract-v2 builder emits only manifest + tool_code.
_LEGACY_CONTRACT = json.dumps(
    {
        "manifest": _CONTRACT_DOCUMENT["manifest"],
        "tool_code": _CONTRACT_DOCUMENT["tool_code"],
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


def test_parse_tool_build_response_extracts_expanded_contract() -> None:
    result = parse_tool_build_response(
        f"here is the tool\n```json\n{_BUILT_CONTRACT}\n```\n", tool_name="mimir_metric_window"
    )
    assert result.manifest["name"] == "mimir_metric_window"
    assert result.tool_code.startswith("def run")
    assert result.test_code.startswith("def test_run")
    assert result.requirements == ["httpx>=0.27"]


def test_parse_tool_build_response_is_backward_compatible() -> None:
    # A legacy builder that omits test_code/requirements still parses.
    result = parse_tool_build_response(_LEGACY_CONTRACT, tool_name="mimir_metric_window")
    assert result.tool_code.startswith("def run")
    assert result.test_code == ""
    assert result.requirements == []


def test_parse_tool_build_response_rejects_missing_pieces() -> None:
    with pytest.raises(ToolBuildError, match="no JSON object"):
        parse_tool_build_response("no json here", tool_name="x")
    with pytest.raises(ToolBuildError, match="missing tool_code"):
        parse_tool_build_response('{"manifest": {"name": "x"}}', tool_name="x")


def test_parse_tool_build_document_rejects_non_object() -> None:
    with pytest.raises(ToolBuildError, match="not a JSON object"):
        parse_tool_build_document([1, 2, 3], tool_name="x")


def test_parse_tool_build_document_requires_manifest_and_code() -> None:
    with pytest.raises(ToolBuildError, match="missing a manifest object"):
        parse_tool_build_document(
            {"tool_code": "def run(input):\n    return {}\n"}, tool_name="x"
        )
    with pytest.raises(ToolBuildError, match="missing tool_code"):
        parse_tool_build_document({"manifest": {"name": "x"}}, tool_name="x")


def test_parse_tool_build_document_defines_the_shape_once() -> None:
    result = parse_tool_build_document(_CONTRACT_DOCUMENT, tool_name="mimir_metric_window")
    assert result.manifest["name"] == "mimir_metric_window"
    assert result.tool_code.startswith("def run")
    assert result.test_code.startswith("def test_run")
    assert result.requirements == ["httpx>=0.27"]


def test_parse_tool_build_document_defaults_optional_fields() -> None:
    result = parse_tool_build_document(
        {"manifest": {"name": "x"}, "tool_code": "def run(input):\n    return {}\n"},
        tool_name="x",
    )
    assert result.test_code == ""
    assert result.requirements == []


def test_parse_tool_build_document_names_manifest_when_absent() -> None:
    result = parse_tool_build_document(
        {"manifest": {"description": "d"}, "tool_code": "def run(input):\n    return {}\n"},
        tool_name="fallback_name",
    )
    assert result.manifest["name"] == "fallback_name"


def test_parse_tool_build_document_drops_non_string_requirements() -> None:
    result = parse_tool_build_document(
        {
            "manifest": {"name": "x"},
            "tool_code": "def run(input):\n    return {}\n",
            "requirements": ["  httpx>=0.27  ", "", "  ", 0, None, {"pkg": "no"}],
        },
        tool_name="x",
    )
    # Non-strings and blank entries are dropped; kept strings are trimmed.
    assert result.requirements == ["httpx>=0.27"]


def test_build_prompts_instructs_canonical_file_and_new_fields() -> None:
    _system, initial = build_prompts(_request())
    assert "learned_tool.json" in initial
    assert "test_code" in initial
    assert "requirements" in initial


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


async def test_forge_session_backend_prefers_canonical_file() -> None:
    client = _FakeHttpClient(
        {
            ("POST", "/api/v1/forge/sessions"): [HttpResponse(201, {"id": "sess-1"})],
            ("GET", "/api/v1/forge/sessions/sess-1"): [
                HttpResponse(200, {"status": "running"}),
                HttpResponse(200, {"status": "completed"}),
            ],
            # The download surface returns the file as parsed JSON.
            ("GET", "path=learned_tool.json&root=workspace"): [
                HttpResponse(200, _CONTRACT_DOCUMENT)
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
    assert result.test_code.startswith("def test_run")
    assert result.requirements == ["httpx>=0.27"]
    assert result.build_evidence == {"retrieval": "canonical_file"}
    assert result.provenance["backend"] == "forge_session"
    assert result.provenance["forge_session_id"] == "sess-1"
    # The chronicle is not fetched when the canonical file resolves.
    assert not any(url.endswith("/chronicle") for _method, url in client.calls)


async def test_forge_session_backend_decodes_raw_json_file_body() -> None:
    client = _FakeHttpClient(
        {
            ("POST", "/api/v1/forge/sessions"): [HttpResponse(201, {"id": "sess-1"})],
            ("GET", "/api/v1/forge/sessions/sess-1"): [HttpResponse(200, {"status": "completed"})],
            # A non-JSON transport returns the file body as raw text.
            ("GET", "path=learned_tool.json&root=workspace"): [
                HttpResponse(200, _BUILT_CONTRACT)
            ],
        }
    )
    backend = ForgeSessionToolBuildBackend(
        client=client, base_url="http://forge", poll_interval_seconds=0, sleep=_no_sleep
    )

    result = await backend.build(_request())

    assert result.tool_code.startswith("def run")
    assert result.build_evidence == {"retrieval": "canonical_file"}


async def test_forge_session_backend_falls_back_to_chronicle_scrape() -> None:
    client = _FakeHttpClient(
        {
            ("POST", "/api/v1/forge/sessions"): [HttpResponse(201, {"id": "sess-1"})],
            ("GET", "/api/v1/forge/sessions/sess-1"): [
                HttpResponse(200, {"status": "running"}),
                HttpResponse(200, {"status": "completed"}),
            ],
            # Canonical file is absent (404) -> chronicle scrape.
            ("GET", "path=learned_tool.json&root=workspace"): [HttpResponse(404, "not found")],
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

    assert result.tool_code.startswith("def run")
    assert result.build_evidence == {"retrieval": "chronicle_scrape"}


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


async def test_ting_workflow_backend_prefers_canonical_artifact() -> None:
    client = _FakeHttpClient(
        {
            ("POST", "/api/v1/ting/workflows/wf-1/launch"): [
                HttpResponse(200, {"campaign_id": "camp-1", "session_id": "sess-x"})
            ],
            ("GET", "/api/v1/ting/research/campaigns/camp-1"): [
                HttpResponse(200, {"status": "RUNNING"}),
                HttpResponse(200, {"status": "COMPLETED"}),
            ],
            # The campaign artifact endpoint returns the file body under "content".
            ("GET", "/artifact?path=learned_tool.json"): [
                HttpResponse(200, {"path": "learned_tool.json", "content": _BUILT_CONTRACT})
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
    assert result.test_code.startswith("def test_run")
    assert result.requirements == ["httpx>=0.27"]
    assert result.build_evidence == {"retrieval": "canonical_file"}
    assert result.provenance["backend"] == "ting_workflow"
    assert result.provenance["ting_campaign_id"] == "camp-1"


async def test_ting_workflow_backend_falls_back_to_scrape() -> None:
    client = _FakeHttpClient(
        {
            ("POST", "/api/v1/ting/workflows/wf-1/launch"): [
                HttpResponse(200, {"campaign_id": "camp-1"})
            ],
            ("GET", "/api/v1/ting/research/campaigns/camp-1"): [
                HttpResponse(
                    200,
                    {
                        "status": "COMPLETED",
                        "artifacts": [{"path": "tools/x.py", "content": _BUILT_CONTRACT}],
                    },
                ),
            ],
            # No canonical artifact -> 404 -> scrape the campaign artifacts.
            ("GET", "/artifact?path=learned_tool.json"): [HttpResponse(404, "not found")],
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
    assert result.build_evidence == {"retrieval": "chronicle_scrape"}


async def test_ting_workflow_backend_requires_workflow_id() -> None:
    backend = TingWorkflowToolBuildBackend(
        client=_FakeHttpClient({}), base_url="http://ting", workflow_id=""
    )
    with pytest.raises(ToolBuildError, match="workflow_id or workflow_selector"):
        await backend.build(_request())


async def test_ting_workflow_backend_resolves_workflow_selector() -> None:
    client = _FakeHttpClient(
        {
            ("GET", "/api/v1/ting/workflows"): [
                HttpResponse(
                    200,
                    [
                        {"id": "wf-research", "name": "Research", "tags": ["research"]},
                        {
                            "id": "wf-builder",
                            "name": "Tool Builder",
                            "tags": ["tool-builder", "capability-builder"],
                        },
                    ],
                )
            ],
            ("POST", "/api/v1/ting/workflows/wf-builder/launch"): [
                HttpResponse(200, {"campaign_id": "camp-builder"})
            ],
            ("GET", "/api/v1/ting/research/campaigns/camp-builder"): [
                HttpResponse(200, {"status": "COMPLETED", "result": _BUILT_CONTRACT})
            ],
            ("GET", "/artifact?path=learned_tool.json"): [HttpResponse(404, "not found")],
        }
    )
    backend = TingWorkflowToolBuildBackend(
        client=client,
        base_url="http://ting",
        workflow_selector={"tags": ["tool-builder"]},
        poll_interval_seconds=0,
        sleep=_no_sleep,
    )

    result = await backend.build(_request())

    assert result.tool_code.startswith("def run")
    assert result.provenance["ting_workflow_id"] == "wf-builder"


def test_backends_construct_from_plain_yaml_kwargs() -> None:
    """The dynamic-adapter contract: dotted path + plain kwargs, no client."""
    forge = ForgeSessionToolBuildBackend(base_url="http://forge")
    ting = TingWorkflowToolBuildBackend(
        base_url="http://ting",
        workflow_id="wf-1",
    )
    assert forge.name == "forge_session"
    assert ting.name == "ting_workflow"


async def test_forge_create_rejects_non_object_body() -> None:
    client = _FakeHttpClient({("POST", "/api/v1/forge/sessions"): [HttpResponse(200, "nope")]})
    backend = ForgeSessionToolBuildBackend(client=client, base_url="http://forge")
    with pytest.raises(ToolBuildError, match="non-object body"):
        await backend.build(_request())


async def test_forge_create_without_session_id_raises() -> None:
    client = _FakeHttpClient({("POST", "/api/v1/forge/sessions"): [HttpResponse(201, {})]})
    backend = ForgeSessionToolBuildBackend(client=client, base_url="http://forge")
    with pytest.raises(ToolBuildError, match="no session id"):
        await backend.build(_request())


async def test_forge_chronicle_fetch_error_raises() -> None:
    client = _FakeHttpClient(
        {
            ("POST", "/api/v1/forge/sessions"): [HttpResponse(201, {"id": "s1"})],
            ("GET", "/api/v1/forge/sessions/s1"): [HttpResponse(200, {"status": "completed"})],
            ("GET", "path=learned_tool.json&root=workspace"): [HttpResponse(404, "not found")],
            ("GET", "/api/v1/forge/sessions/s1/chronicle"): [HttpResponse(500, "boom")],
        }
    )
    backend = ForgeSessionToolBuildBackend(
        client=client, base_url="http://forge", poll_interval_seconds=0, sleep=_no_sleep
    )
    with pytest.raises(ToolBuildError, match="chronicle fetch"):
        await backend.build(_request())


async def test_forge_empty_chronicle_has_no_contract() -> None:
    # Neither the canonical file nor the chronicle yields a contract -> loud failure.
    client = _FakeHttpClient(
        {
            ("POST", "/api/v1/forge/sessions"): [HttpResponse(201, {"id": "s1"})],
            ("GET", "/api/v1/forge/sessions/s1"): [HttpResponse(200, {"status": "completed"})],
            ("GET", "path=learned_tool.json&root=workspace"): [HttpResponse(404, "not found")],
            ("GET", "/api/v1/forge/sessions/s1/chronicle"): [HttpResponse(200, {"unused": "x"})],
        }
    )
    backend = ForgeSessionToolBuildBackend(
        client=client, base_url="http://forge", poll_interval_seconds=0, sleep=_no_sleep
    )
    with pytest.raises(ToolBuildError, match="no JSON object"):
        await backend.build(_request())


async def test_ting_launch_rejects_non_object_body() -> None:
    client = _FakeHttpClient(
        {("POST", "/api/v1/ting/workflows/wf-1/launch"): [HttpResponse(200, "nope")]}
    )
    backend = TingWorkflowToolBuildBackend(
        client=client, base_url="http://ting", workflow_id="wf-1"
    )
    with pytest.raises(ToolBuildError, match="non-object body"):
        await backend.build(_request())


async def test_ting_uses_inline_campaign_result() -> None:
    client = _FakeHttpClient(
        {
            ("POST", "/api/v1/ting/workflows/wf-1/launch"): [
                HttpResponse(200, {"campaign_id": "c1"})
            ],
            ("GET", "/api/v1/ting/research/campaigns/c1"): [
                HttpResponse(200, {"status": "COMPLETED", "result": _BUILT_CONTRACT})
            ],
            ("GET", "/artifact?path=learned_tool.json"): [HttpResponse(404, "not found")],
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
    assert result.build_evidence == {"retrieval": "chronicle_scrape"}


def test_client_from_workload_identity_exchanges_projected_token(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("projected-proof", encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://forge.example/api/v1/tokens/workload/exchange"
        assert json.loads(request.content) == {
            "token": "projected-proof",
            "audiences": ["forge"],
        }
        return httpx.Response(200, json={"token": "workload-jwt", "expires_in": 300})

    client = client_from_workload_identity(
        base_url="https://forge.example",
        workload_token_file=str(token_file),
        workload_audiences=["forge"],
        transport=httpx.MockTransport(handler),
    )

    assert client._headers()["Authorization"] == "Bearer workload-jwt"


def test_client_external_token_env_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXTERNAL_TOOL_BUILD_TOKEN", "external-123")

    client = client_from_workload_identity(
        base_url="https://forge.example",
        external_token_env="EXTERNAL_TOOL_BUILD_TOKEN",
    )

    assert client._headers()["Authorization"] == "Bearer external-123"


async def test_ting_workflow_backend_raises_without_artifact() -> None:
    client = _FakeHttpClient(
        {
            ("POST", "/api/v1/ting/workflows/wf-1/launch"): [
                HttpResponse(200, {"campaign_id": "camp-2"})
            ],
            ("GET", "/api/v1/ting/research/campaigns/camp-2"): [
                HttpResponse(200, {"status": "COMPLETED", "artifacts": []})
            ],
            ("GET", "/artifact?path=learned_tool.json"): [HttpResponse(404, "not found")],
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


# ---------------------------------------------------------------------------
# canonical-artifact decode helpers
# ---------------------------------------------------------------------------


def test_decode_canonical_body_handles_object_text_and_junk() -> None:
    assert _decode_canonical_body({"manifest": {}}) == {"manifest": {}}
    assert _decode_canonical_body(_BUILT_CONTRACT)["manifest"]["name"] == "mimir_metric_window"
    assert _decode_canonical_body("") is None
    assert _decode_canonical_body(b"bytes-not-str") is None
    assert _decode_canonical_body("not json") is None
    assert _decode_canonical_body("[1, 2, 3]") is None  # valid JSON, wrong shape


def test_decode_canonical_content_handles_object_text_and_junk() -> None:
    assert _decode_canonical_content({"manifest": {}}) == {"manifest": {}}
    assert _decode_canonical_content(_BUILT_CONTRACT)["tool_code"].startswith("def run")
    assert _decode_canonical_content("   ") is None
    assert _decode_canonical_content(None) is None
    assert _decode_canonical_content("not json") is None
    assert _decode_canonical_content("[1, 2, 3]") is None  # valid JSON, wrong shape


async def test_forge_malformed_canonical_file_falls_back_to_chronicle() -> None:
    client = _FakeHttpClient(
        {
            ("POST", "/api/v1/forge/sessions"): [HttpResponse(201, {"id": "s1"})],
            ("GET", "/api/v1/forge/sessions/s1"): [HttpResponse(200, {"status": "completed"})],
            # 200 but the body is not a JSON object -> treat as absent, scrape.
            ("GET", "path=learned_tool.json&root=workspace"): [HttpResponse(200, "garbage")],
            ("GET", "/api/v1/forge/sessions/s1/chronicle"): [
                HttpResponse(200, {"content": _BUILT_CONTRACT})
            ],
        }
    )
    backend = ForgeSessionToolBuildBackend(
        client=client, base_url="http://forge", poll_interval_seconds=0, sleep=_no_sleep
    )
    result = await backend.build(_request())
    assert result.build_evidence == {"retrieval": "chronicle_scrape"}


async def test_forge_canonical_download_transport_error_falls_back() -> None:
    class _RaisingClient(_FakeHttpClient):
        async def get(self, url: str) -> HttpResponse:
            if url.endswith("path=learned_tool.json&root=workspace"):
                raise RuntimeError("pod unreachable")
            return await super().get(url)

    client = _RaisingClient(
        {
            ("POST", "/api/v1/forge/sessions"): [HttpResponse(201, {"id": "s1"})],
            ("GET", "/api/v1/forge/sessions/s1"): [HttpResponse(200, {"status": "completed"})],
            ("GET", "/api/v1/forge/sessions/s1/chronicle"): [
                HttpResponse(200, {"content": _BUILT_CONTRACT})
            ],
        }
    )
    backend = ForgeSessionToolBuildBackend(
        client=client, base_url="http://forge", poll_interval_seconds=0, sleep=_no_sleep
    )
    result = await backend.build(_request())
    assert result.build_evidence == {"retrieval": "chronicle_scrape"}


async def test_ting_canonical_artifact_transport_error_falls_back() -> None:
    class _RaisingClient(_FakeHttpClient):
        async def get(self, url: str) -> HttpResponse:
            if url.endswith("/artifact?path=learned_tool.json"):
                raise RuntimeError("gateway down")
            return await super().get(url)

    client = _RaisingClient(
        {
            ("POST", "/api/v1/ting/workflows/wf-1/launch"): [
                HttpResponse(200, {"campaign_id": "c1"})
            ],
            ("GET", "/api/v1/ting/research/campaigns/c1"): [
                HttpResponse(200, {"status": "COMPLETED", "result": _BUILT_CONTRACT})
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
    assert result.build_evidence == {"retrieval": "chronicle_scrape"}
