"""Tool build backends: commission a build over fake Forge/Ting HTTP (NIU-1054)."""

from __future__ import annotations

import json
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from ravn.adapters.tool_build import (
    A2AToolBuildBackend,
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
from ravn.adapters.tool_build.http import (
    HttpxJsonClient,
    client_from_workload_identity,
    normalize_http_origin,
)
from ravn.adapters.tool_build.ting_workflow import _decode_canonical_content
from ravn.ports.tool_build_backend import (
    ToolBuildError,
    ToolBuildInputRequiredError,
    ToolBuildPendingError,
    ToolBuildRequest,
)


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
        self.post_bodies: list[dict] = []
        self.headers_seen: list[dict[str, str]] = []

    def _match(self, method: str, url: str) -> HttpResponse:
        for (route_method, suffix), responses in self._routes.items():
            if route_method == method and url.endswith(suffix) and responses:
                return responses.pop(0) if len(responses) > 1 else responses[0]
        raise AssertionError(f"no scripted response for {method} {url}")

    async def get(self, url: str, *, headers: dict[str, str] | None = None) -> HttpResponse:
        self.calls.append(("GET", url))
        self.headers_seen.append(dict(headers or {}))
        return self._match("GET", url)

    async def post(
        self,
        url: str,
        json_body: dict,
        *,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        self.calls.append(("POST", url))
        self.post_bodies.append(json_body)
        self.headers_seen.append(dict(headers or {}))
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
        parse_tool_build_document({"tool_code": "def run(input):\n    return {}\n"}, tool_name="x")
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
    assert "imports\n  `_verify_tool`" in initial
    assert "must not read `learned_tool.json`" in initial


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
            ("GET", "path=learned_tool.json&root=workspace"): [HttpResponse(200, _BUILT_CONTRACT)],
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
    # The launch body attributes the campaign to the commissioning Valkyrie.
    launch_body = client.post_bodies[0]
    provenance = launch_body["provenance"]
    assert provenance["builder"] == "ravn.tool_build"
    assert provenance["valkyrie_id"] == _request().valkyrie_id
    assert provenance["environment_id"] == _request().environment_id
    assert provenance["tool_name"] == _request().name


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


async def test_http_client_refreshes_rejected_workload_identity_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RefreshableAuth:
        def __init__(self) -> None:
            self.token = "expired"
            self.invalidations = 0

        def headers(self) -> dict[str, str]:
            return {"Authorization": f"Bearer {self.token}"}

        def invalidate(self) -> bool:
            self.invalidations += 1
            self.token = "fresh"
            return True

    class FakeAsyncClient:
        def __init__(self, **_kwargs: object) -> None:
            self.authorizations: list[str] = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, _url: str, *, headers: dict[str, str]) -> httpx.Response:
            authorization = headers["Authorization"]
            self.authorizations.append(authorization)
            status = 401 if authorization == "Bearer expired" else 200
            return httpx.Response(status, json={"ok": status == 200})

    created: list[FakeAsyncClient] = []

    def build_client(**kwargs: object) -> FakeAsyncClient:
        client = FakeAsyncClient(**kwargs)
        created.append(client)
        return client

    monkeypatch.setattr(httpx, "AsyncClient", build_client)
    auth = RefreshableAuth()
    client = HttpxJsonClient(auth=auth, allowed_origins=["https://forge.example"])

    response = await client.get("https://forge.example/api/v1/forge/sessions")

    assert response.status_code == 200
    assert auth.invalidations == 1
    assert created[0].authorizations == ["Bearer expired", "Bearer fresh"]


def test_ting_backend_requests_end_to_end_build_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_client_from_workload_identity(**kwargs: object) -> _FakeHttpClient:
        captured.update(kwargs)
        return _FakeHttpClient({})

    monkeypatch.setattr(
        "ravn.adapters.tool_build.ting_workflow.client_from_workload_identity",
        fake_client_from_workload_identity,
    )

    TingWorkflowToolBuildBackend(base_url="https://ting.example", workflow_id="wf-1")

    assert captured["workload_scopes"] == [
        "ting:workflow:launch",
        "forge:session:create",
    ]


def test_client_external_token_env_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXTERNAL_TOOL_BUILD_TOKEN", "external-123")

    client = client_from_workload_identity(
        base_url="https://forge.example",
        external_token_env="EXTERNAL_TOOL_BUILD_TOKEN",
    )

    assert client._headers()["Authorization"] == "Bearer external-123"


def test_authenticated_http_client_is_bound_to_configured_origins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EXTERNAL_TOOL_BUILD_TOKEN", "external-123")
    client = client_from_workload_identity(
        base_url="https://forge.example/api",
        external_token_env="EXTERNAL_TOOL_BUILD_TOKEN",
        allowed_origins=["https://forge.example", "https://peer.example:443/a2a"],
    )

    client._assert_allowed_origin("https://forge.example/launch")
    client._assert_allowed_origin("https://peer.example/task")
    with pytest.raises(ValueError, match="untrusted origin"):
        client._assert_allowed_origin("https://attacker.example/collect")
    with pytest.raises(ValueError, match="absolute http"):
        client._assert_allowed_origin("file:///tmp/token")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://[2001:db8::1]/a2a", "https://[2001:db8::1]"),
        ("https://[2001:db8::1]:8443/a2a", "https://[2001:db8::1]:8443"),
    ],
)
def test_http_origin_normalization_preserves_ipv6_authority(url: str, expected: str) -> None:
    assert normalize_http_origin(url) == expected


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


# ---------------------------------------------------------------------------
# A2A backend
# ---------------------------------------------------------------------------


_A2A_ENDPOINT = "https://ting.example/api/v1/ting/a2a"


def _a2a_card(
    *,
    skills: list[dict] | None = None,
    interfaces: list[dict] | None = None,
    push_notifications: bool = False,
) -> dict:
    return {
        "name": "Niuu Workflows",
        "description": "Launchable workflows",
        "version": "1.0.0",
        "capabilities": {"pushNotifications": push_notifications},
        "supportedInterfaces": interfaces
        if interfaces is not None
        else [
            {
                "url": _A2A_ENDPOINT,
                "protocolBinding": "JSONRPC",
                "protocolVersion": "1.0",
            }
        ],
        "skills": skills
        if skills is not None
        else [
            {
                "id": "wf-1",
                "name": "tool-builder",
                "description": "Builds learned tools",
                "tags": ["tool-builder"],
            }
        ],
    }


def _a2a_task(state: str, *, artifacts: list[dict] | None = None) -> dict:
    task: dict = {"id": "task-1", "contextId": "task-1", "status": {"state": state}}
    if artifacts is not None:
        task["artifacts"] = artifacts
    return task


def _rpc_result(result: dict) -> HttpResponse:
    return HttpResponse(200, {"jsonrpc": "2.0", "id": "1", "result": result})


def _a2a_backend(client: _FakeHttpClient, **kwargs) -> A2AToolBuildBackend:
    return A2AToolBuildBackend(
        client=client,
        card_url="https://ting.example/.well-known/agent-card.json",
        poll_interval_seconds=0,
        sleep=_no_sleep,
        **kwargs,
    )


async def test_a2a_backend_builds_from_inline_canonical_artifact() -> None:
    activities: list[dict[str, object]] = []

    async def _capture(activity: dict[str, object]) -> None:
        activities.append(activity)

    artifacts = [
        {
            "artifactId": "research/campaigns/task-1/learned_tool.json",
            "parts": [{"filename": "learned_tool.json", "text": _BUILT_CONTRACT}],
        }
    ]
    client = _FakeHttpClient(
        {
            ("GET", "/.well-known/agent-card.json"): [HttpResponse(200, _a2a_card())],
            ("POST", "/api/v1/ting/a2a"): [
                _rpc_result({"task": _a2a_task("TASK_STATE_SUBMITTED")}),
                _rpc_result(_a2a_task("TASK_STATE_WORKING")),
                _rpc_result(_a2a_task("TASK_STATE_COMPLETED", artifacts=artifacts)),
            ],
        }
    )
    backend = _a2a_backend(client, workflow_id="wf-1", activity_emitter=_capture)

    result = await backend.build(_request())

    assert result.manifest["name"] == "mimir_metric_window"
    assert result.tool_code.startswith("def run")
    assert result.test_code.startswith("def test_run")
    assert result.requirements == ["httpx>=0.27"]
    assert result.build_evidence == {"retrieval": "canonical_file"}
    assert result.provenance["backend"] == "a2a"
    assert result.provenance["a2a_task_id"] == "task-1"
    assert result.provenance["workflow_id"] == "wf-1"

    send_body = client.post_bodies[0]
    assert send_body["method"] == "SendMessage"
    message = send_body["params"]["message"]
    assert message["metadata"]["skillId"] == "wf-1"
    assert message["metadata"]["sessionName"] == "tool-build-mimir_metric_window"
    assert message["parts"][0]["text"]
    assert client.post_bodies[1]["method"] == "GetTask"
    assert all(headers.get("A2A-Version") == "1.0" for headers in client.headers_seen)
    assert [activity["state"] for activity in activities] == [
        "TASK_STATE_WORKING",
        "TASK_STATE_COMPLETED",
    ]
    assert activities[0] == {
        "agent_id": "https://ting.example/.well-known/agent-card.json",
        "skill_id": "wf-1",
        "task_id": "task-1",
        "state": "TASK_STATE_WORKING",
        "operation": "build",
        "input_required": False,
        "question": "",
        "prompt": "Build a tool that queries a bounded metric window and summarizes it.",
        "status_message": "",
        "source_tool": "build_tool",
    }


async def test_a2a_backend_suspends_for_push_and_resumes_with_one_get() -> None:
    artifacts = [
        {
            "artifactId": "research/campaigns/task-1/learned_tool.json",
            "parts": [{"filename": "learned_tool.json", "text": _BUILT_CONTRACT}],
        }
    ]
    client = _FakeHttpClient(
        {
            ("GET", "/.well-known/agent-card.json"): [
                HttpResponse(200, _a2a_card(push_notifications=True))
            ],
            ("POST", "/api/v1/ting/a2a"): [
                _rpc_result({"task": _a2a_task("TASK_STATE_SUBMITTED")}),
                _rpc_result({"taskId": "task-1", "id": "push-1"}),
                _rpc_result(_a2a_task("TASK_STATE_COMPLETED", artifacts=artifacts)),
            ],
        }
    )
    backend = _a2a_backend(
        client,
        workflow_id="wf-1",
        push_callback_url="https://ivaldi.example/a2a/push",
        push_notification_token="callback-secret",
    )

    with pytest.raises(ToolBuildPendingError) as raised:
        await backend.build(_request())

    assert raised.value.push_registered is True
    assert raised.value.continuation == {
        "task_id": "task-1",
        "input_kind": "pending",
        "round": 0,
        "exchanges": [],
        "push_registered": True,
    }
    assert [body["method"] for body in client.post_bodies] == [
        "SendMessage",
        "CreateTaskPushNotificationConfig",
    ]
    registration = client.post_bodies[1]["params"]
    assert registration["taskId"] == "task-1"
    assert registration["url"] == "https://ivaldi.example/a2a/push"
    assert registration["token"] == "callback-secret"

    result = await backend.build(replace(_request(), continuation=raised.value.continuation))

    assert result.manifest["name"] == "mimir_metric_window"
    assert [body["method"] for body in client.post_bodies] == [
        "SendMessage",
        "CreateTaskPushNotificationConfig",
        "GetTask",
    ]


async def test_a2a_backend_closes_local_tracking_when_polling_is_exhausted() -> None:
    activities: list[dict[str, object]] = []

    async def _capture(activity: dict[str, object]) -> None:
        activities.append(activity)

    client = _FakeHttpClient(
        {
            ("GET", "/.well-known/agent-card.json"): [HttpResponse(200, _a2a_card())],
            ("POST", "/api/v1/ting/a2a"): [
                _rpc_result({"task": _a2a_task("TASK_STATE_SUBMITTED")}),
                _rpc_result(_a2a_task("TASK_STATE_WORKING")),
            ],
        }
    )
    backend = _a2a_backend(
        client,
        workflow_id="wf-1",
        activity_emitter=_capture,
        max_poll_attempts=1,
    )

    with pytest.raises(ToolBuildError, match="did not finish within 1 polls"):
        await backend.build(_request())

    assert activities[-1]["task_id"] == "task-1"
    assert activities[-1]["state"] == "TASK_STATE_WORKING"
    assert activities[-1]["tracking_state"] == "poll_exhausted"


async def test_a2a_backend_uses_durable_operation_id_for_message_correlation() -> None:
    artifacts = [
        {
            "artifactId": "x/learned_tool.json",
            "parts": [{"filename": "learned_tool.json", "text": _BUILT_CONTRACT}],
        }
    ]
    client = _FakeHttpClient(
        {
            ("GET", "/.well-known/agent-card.json"): [HttpResponse(200, _a2a_card())],
            ("POST", "/api/v1/ting/a2a"): [
                _rpc_result({"tasks": []}),
                _rpc_result({"task": _a2a_task("TASK_STATE_SUBMITTED")}),
                _rpc_result(_a2a_task("TASK_STATE_COMPLETED", artifacts=artifacts)),
            ],
        }
    )
    backend = _a2a_backend(client, workflow_id="wf-1")

    await backend.build(replace(_request(), operation_id="operation-resident-1"))

    assert [body["method"] for body in client.post_bodies] == [
        "ListTasks",
        "SendMessage",
        "GetTask",
    ]
    message = client.post_bodies[1]["params"]["message"]
    assert message["messageId"] == "operation-resident-1"
    assert message["contextId"] == "operation-resident-1"


async def test_a2a_backend_recovers_existing_task_by_context_id() -> None:
    artifacts = [
        {
            "artifactId": "x/learned_tool.json",
            "parts": [{"filename": "learned_tool.json", "text": _BUILT_CONTRACT}],
        }
    ]
    recovered = _a2a_task("TASK_STATE_WORKING")
    recovered["contextId"] = "operation-resident-1"
    client = _FakeHttpClient(
        {
            ("GET", "/.well-known/agent-card.json"): [HttpResponse(200, _a2a_card())],
            ("POST", "/api/v1/ting/a2a"): [
                _rpc_result({"tasks": [recovered]}),
                _rpc_result(_a2a_task("TASK_STATE_COMPLETED", artifacts=artifacts)),
            ],
        }
    )
    backend = _a2a_backend(client, workflow_id="wf-1")

    result = await backend.build(replace(_request(), operation_id="operation-resident-1"))

    assert result.provenance["a2a_task_id"] == "task-1"
    assert [body["method"] for body in client.post_bodies] == ["ListTasks", "GetTask"]


async def test_a2a_backend_propagates_active_trace_in_message_metadata(
    monkeypatch,
) -> None:
    artifacts = [
        {
            "artifactId": "x/learned_tool.json",
            "parts": [{"filename": "learned_tool.json", "text": _BUILT_CONTRACT}],
        }
    ]
    client = _FakeHttpClient(
        {
            ("GET", "/.well-known/agent-card.json"): [HttpResponse(200, _a2a_card())],
            ("POST", "/api/v1/ting/a2a"): [
                _rpc_result({"task": _a2a_task("TASK_STATE_SUBMITTED")}),
                _rpc_result(_a2a_task("TASK_STATE_COMPLETED", artifacts=artifacts)),
            ],
        }
    )
    telemetry = MagicMock()
    telemetry.inject.return_value = {
        "traceparent": "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"
    }
    telemetry.span.side_effect = lambda *_args, **_kwargs: nullcontext(MagicMock())
    monkeypatch.setattr(
        "ravn.adapters.tool_build.a2a.get_observability",
        lambda: telemetry,
    )
    backend = _a2a_backend(client, workflow_id="wf-1")

    await backend.build(_request())

    metadata = client.post_bodies[0]["params"]["message"]["metadata"]
    assert metadata["traceContext"] == telemetry.inject.return_value


async def test_a2a_backend_passes_connection_id_when_configured() -> None:
    artifacts = [
        {
            "artifactId": "x/learned_tool.json",
            "parts": [{"filename": "learned_tool.json", "text": _BUILT_CONTRACT}],
        }
    ]
    client = _FakeHttpClient(
        {
            ("GET", "/.well-known/agent-card.json"): [HttpResponse(200, _a2a_card())],
            ("POST", "/api/v1/ting/a2a"): [
                _rpc_result({"task": _a2a_task("TASK_STATE_SUBMITTED")}),
                _rpc_result(_a2a_task("TASK_STATE_COMPLETED", artifacts=artifacts)),
            ],
        }
    )
    backend = _a2a_backend(client, workflow_id="wf-1", connection_id="conn-valhalla")

    await backend.build(_request())

    metadata = client.post_bodies[0]["params"]["message"]["metadata"]
    assert metadata["connectionId"] == "conn-valhalla"


async def test_a2a_backend_omits_connection_id_by_default() -> None:
    artifacts = [
        {
            "artifactId": "x/learned_tool.json",
            "parts": [{"filename": "learned_tool.json", "text": _BUILT_CONTRACT}],
        }
    ]
    client = _FakeHttpClient(
        {
            ("GET", "/.well-known/agent-card.json"): [HttpResponse(200, _a2a_card())],
            ("POST", "/api/v1/ting/a2a"): [
                _rpc_result({"task": _a2a_task("TASK_STATE_SUBMITTED")}),
                _rpc_result(_a2a_task("TASK_STATE_COMPLETED", artifacts=artifacts)),
            ],
        }
    )
    backend = _a2a_backend(client, workflow_id="wf-1")

    await backend.build(_request())

    metadata = client.post_bodies[0]["params"]["message"]["metadata"]
    assert "connectionId" not in metadata


async def test_a2a_backend_selects_skill_by_tag() -> None:
    artifacts = [
        {
            "artifactId": "x/learned_tool.json",
            "parts": [{"filename": "learned_tool.json", "text": _BUILT_CONTRACT}],
        }
    ]
    card = _a2a_card(
        skills=[
            {"id": "wf-other", "name": "deploy", "tags": ["deploy"]},
            {"id": "wf-9", "name": "tool-builder", "tags": ["tool-builder"]},
        ]
    )
    client = _FakeHttpClient(
        {
            ("GET", "/.well-known/agent-card.json"): [HttpResponse(200, card)],
            ("POST", "/api/v1/ting/a2a"): [
                _rpc_result({"task": _a2a_task("TASK_STATE_SUBMITTED")}),
                _rpc_result(_a2a_task("TASK_STATE_COMPLETED", artifacts=artifacts)),
            ],
        }
    )
    backend = _a2a_backend(client, workflow_selector={"tags": ["tool-builder"]})

    result = await backend.build(_request())

    assert result.provenance["workflow_id"] == "wf-9"
    assert client.post_bodies[0]["params"]["message"]["metadata"]["skillId"] == "wf-9"


async def test_a2a_backend_fetches_url_part_artifact() -> None:
    artifacts = [
        {
            "artifactId": "research/campaigns/task-1/learned_tool.json",
            "parts": [
                {
                    "filename": "learned_tool.json",
                    "url": "https://ting.example/api/v1/ting/research/campaigns/task-1/artifact?path=research%2Fcampaigns%2Ftask-1%2Flearned_tool.json",
                }
            ],
        }
    ]
    client = _FakeHttpClient(
        {
            ("GET", "/.well-known/agent-card.json"): [HttpResponse(200, _a2a_card())],
            ("POST", "/api/v1/ting/a2a"): [
                _rpc_result({"task": _a2a_task("TASK_STATE_SUBMITTED")}),
                _rpc_result(_a2a_task("TASK_STATE_COMPLETED", artifacts=artifacts)),
            ],
            ("GET", "learned_tool.json"): [
                HttpResponse(200, {"path": "learned_tool.json", "content": _BUILT_CONTRACT})
            ],
        }
    )
    backend = _a2a_backend(client, workflow_id="wf-1")

    result = await backend.build(_request())

    assert result.tool_code.startswith("def run")
    assert result.build_evidence == {"retrieval": "canonical_file"}


async def test_a2a_backend_rejects_cross_origin_jsonrpc_endpoint() -> None:
    client = _FakeHttpClient(
        {
            ("GET", "/.well-known/agent-card.json"): [
                HttpResponse(
                    200,
                    _a2a_card(
                        interfaces=[
                            {
                                "url": "https://attacker.example/a2a",
                                "protocolBinding": "JSONRPC",
                            }
                        ]
                    ),
                )
            ],
        }
    )
    backend = _a2a_backend(client, workflow_id="wf-1")

    with pytest.raises(ToolBuildError, match="share the configured card origin"):
        await backend.build(_request())

    assert not any("attacker.example" in url for _method, url in client.calls)


async def test_a2a_backend_does_not_fetch_cross_origin_artifact() -> None:
    artifacts = [
        {
            "artifactId": "x/learned_tool.json",
            "parts": [
                {
                    "filename": "learned_tool.json",
                    "url": "https://attacker.example/learned_tool.json",
                }
            ],
        }
    ]
    client = _FakeHttpClient(
        {
            ("GET", "/.well-known/agent-card.json"): [HttpResponse(200, _a2a_card())],
            ("POST", "/api/v1/ting/a2a"): [
                _rpc_result({"task": _a2a_task("TASK_STATE_SUBMITTED")}),
                _rpc_result(_a2a_task("TASK_STATE_COMPLETED", artifacts=artifacts)),
            ],
        }
    )
    backend = _a2a_backend(client, workflow_id="wf-1")

    with pytest.raises(ToolBuildError, match="no retrievable"):
        await backend.build(_request())

    assert not any("attacker.example" in url for _method, url in client.calls)


async def test_a2a_backend_scrapes_inline_text_when_no_canonical() -> None:
    artifacts = [
        {
            "artifactId": "research/campaigns/task-1/final.md",
            "parts": [
                {
                    "filename": "final.md",
                    "text": f"the result\n```json\n{_BUILT_CONTRACT}\n```\n",
                }
            ],
        }
    ]
    client = _FakeHttpClient(
        {
            ("GET", "/.well-known/agent-card.json"): [HttpResponse(200, _a2a_card())],
            ("POST", "/api/v1/ting/a2a"): [
                _rpc_result({"task": _a2a_task("TASK_STATE_SUBMITTED")}),
                _rpc_result(_a2a_task("TASK_STATE_COMPLETED", artifacts=artifacts)),
            ],
        }
    )
    backend = _a2a_backend(client, workflow_id="wf-1")

    result = await backend.build(_request())

    assert result.tool_code.startswith("def run")
    assert result.build_evidence == {"retrieval": "inline_scrape"}


async def test_a2a_backend_raises_on_failed_task() -> None:
    client = _FakeHttpClient(
        {
            ("GET", "/.well-known/agent-card.json"): [HttpResponse(200, _a2a_card())],
            ("POST", "/api/v1/ting/a2a"): [
                _rpc_result({"task": _a2a_task("TASK_STATE_SUBMITTED")}),
                _rpc_result(_a2a_task("TASK_STATE_FAILED")),
            ],
        }
    )
    backend = _a2a_backend(client, workflow_id="wf-1")

    with pytest.raises(ToolBuildError, match="TASK_STATE_FAILED"):
        await backend.build(_request())


async def test_a2a_backend_raises_on_rpc_error() -> None:
    client = _FakeHttpClient(
        {
            ("GET", "/.well-known/agent-card.json"): [HttpResponse(200, _a2a_card())],
            ("POST", "/api/v1/ting/a2a"): [
                HttpResponse(
                    200,
                    {
                        "jsonrpc": "2.0",
                        "id": "1",
                        "error": {"code": -32602, "message": "unknown workflow: wf-1"},
                    },
                )
            ],
        }
    )
    backend = _a2a_backend(client, workflow_id="wf-1")

    with pytest.raises(ToolBuildError, match="unknown workflow"):
        await backend.build(_request())


async def test_a2a_backend_requires_jsonrpc_interface() -> None:
    client = _FakeHttpClient(
        {
            ("GET", "/.well-known/agent-card.json"): [HttpResponse(200, _a2a_card(interfaces=[]))],
        }
    )
    backend = _a2a_backend(client, workflow_id="wf-1")

    with pytest.raises(ToolBuildError, match="no JSONRPC interface"):
        await backend.build(_request())


async def test_a2a_backend_requires_workflow_id_or_selector() -> None:
    client = _FakeHttpClient(
        {
            ("GET", "/.well-known/agent-card.json"): [HttpResponse(200, _a2a_card())],
        }
    )
    backend = _a2a_backend(client)

    with pytest.raises(ToolBuildError, match="workflow_id or workflow_selector"):
        await backend.build(_request())


async def test_a2a_backend_matches_ting_backend_result_shape() -> None:
    """Parity: the same canned contract yields identical results on both backends."""
    a2a_client = _FakeHttpClient(
        {
            ("GET", "/.well-known/agent-card.json"): [HttpResponse(200, _a2a_card())],
            ("POST", "/api/v1/ting/a2a"): [
                _rpc_result({"task": _a2a_task("TASK_STATE_SUBMITTED")}),
                _rpc_result(
                    _a2a_task(
                        "TASK_STATE_COMPLETED",
                        artifacts=[
                            {
                                "artifactId": "learned_tool.json",
                                "parts": [
                                    {"filename": "learned_tool.json", "text": _BUILT_CONTRACT}
                                ],
                            }
                        ],
                    )
                ),
            ],
        }
    )
    ting_client = _FakeHttpClient(
        {
            ("POST", "/api/v1/ting/workflows/wf-1/launch"): [
                HttpResponse(200, {"campaign_id": "camp-1"})
            ],
            ("GET", "/api/v1/ting/research/campaigns/camp-1"): [
                HttpResponse(200, {"status": "COMPLETED"})
            ],
            ("GET", "/artifact?path=learned_tool.json"): [
                HttpResponse(200, {"path": "learned_tool.json", "content": _BUILT_CONTRACT})
            ],
        }
    )

    a2a_result = await _a2a_backend(a2a_client, workflow_id="wf-1").build(_request())
    ting_result = await TingWorkflowToolBuildBackend(
        client=ting_client,
        base_url="http://ting",
        workflow_id="wf-1",
        poll_interval_seconds=0,
        sleep=_no_sleep,
    ).build(_request())

    assert a2a_result.manifest == ting_result.manifest
    assert a2a_result.tool_code == ting_result.tool_code
    assert a2a_result.test_code == ting_result.test_code
    assert a2a_result.requirements == ting_result.requirements
    assert a2a_result.build_evidence == ting_result.build_evidence


# ---------------------------------------------------------------------------
# A2A backend — workflow gate handling (INPUT_REQUIRED back-and-forth)
# ---------------------------------------------------------------------------


_SPEC_GATE = {
    "gateId": "gate-1",
    "nodeId": "capability-spec-gate",
    "label": "Confirm capability specification",
    "condition": "The framed spec must be confirmed before implementation.",
    "instructions": "Approve when the spec captures the intended tool.",
    "summary": "",
}


def _a2a_gated_task(gate: dict | None = None) -> dict:
    task = _a2a_task("TASK_STATE_INPUT_REQUIRED")
    task["metadata"] = {"pendingGates": [gate or _SPEC_GATE]}
    return task


def _approving_reviewer(record: list | None = None):
    async def _review(request, gate):
        if record is not None:
            record.append((request.name, gate))
        return "approve", "Spec matches the commissioned capability."

    return _review


_A2A_DONE_ARTIFACTS = [
    {
        "artifactId": "research/campaigns/task-1/learned_tool.json",
        "parts": [{"filename": "learned_tool.json", "text": _BUILT_CONTRACT}],
    }
]


async def test_a2a_backend_answers_gate_and_completes() -> None:
    seen_gates: list = []
    client = _FakeHttpClient(
        {
            ("GET", "/.well-known/agent-card.json"): [HttpResponse(200, _a2a_card())],
            ("POST", "/api/v1/ting/a2a"): [
                _rpc_result({"task": _a2a_task("TASK_STATE_SUBMITTED")}),
                _rpc_result(_a2a_task("TASK_STATE_WORKING")),
                _rpc_result(_a2a_gated_task()),
                _rpc_result({"task": _a2a_task("TASK_STATE_WORKING")}),
                _rpc_result(_a2a_task("TASK_STATE_WORKING")),
                _rpc_result(_a2a_task("TASK_STATE_COMPLETED", artifacts=_A2A_DONE_ARTIFACTS)),
            ],
        }
    )
    backend = _a2a_backend(
        client,
        workflow_id="wf-1",
        gate_reviewer=_approving_reviewer(seen_gates),
    )

    result = await backend.build(_request())

    # The reviewer saw the gate question exposed by GetTask.
    assert seen_gates == [("mimir_metric_window", _SPEC_GATE)]
    # The exchange is recorded verbatim in the build evidence.
    exchanges = result.build_evidence["gate_exchanges"]
    assert len(exchanges) == 1
    assert exchanges[0]["decision"] == "approve"
    assert exchanges[0]["delivery"] == "delivered"
    assert exchanges[0]["gate"]["label"] == "Confirm capability specification"
    assert result.provenance["gate_exchanges"] == exchanges
    # The gate reply went over A2A with the documented contract.
    reply = next(
        body
        for body in client.post_bodies
        if body["method"] == "SendMessage" and body["params"]["message"].get("taskId") == "task-1"
    )
    assert reply["params"]["message"]["metadata"]["gateDecision"] == "approve"
    assert reply["params"]["message"]["metadata"]["gateId"] == "gate-1"
    assert reply["params"]["message"]["parts"][0]["text"].startswith("Spec matches")


async def test_a2a_backend_suspends_gate_without_reviewer() -> None:
    client = _FakeHttpClient(
        {
            ("GET", "/.well-known/agent-card.json"): [HttpResponse(200, _a2a_card())],
            ("POST", "/api/v1/ting/a2a"): [
                _rpc_result({"task": _a2a_task("TASK_STATE_SUBMITTED")}),
                _rpc_result(_a2a_gated_task()),
            ],
        }
    )
    backend = _a2a_backend(client, workflow_id="wf-1")

    with pytest.raises(ToolBuildInputRequiredError) as raised:
        await backend.build(_request())
    assert raised.value.task_id == "task-1"
    assert raised.value.input_kind == "gate"
    assert raised.value.prompt.startswith("Confirm capability specification")
    assert raised.value.continuation["reply_metadata"] == {"gateId": "gate-1"}


async def test_a2a_backend_bounds_gate_rounds() -> None:
    client = _FakeHttpClient(
        {
            ("GET", "/.well-known/agent-card.json"): [HttpResponse(200, _a2a_card())],
            ("POST", "/api/v1/ting/a2a"): [
                _rpc_result({"task": _a2a_task("TASK_STATE_SUBMITTED")}),
                _rpc_result(_a2a_gated_task()),
            ],
        }
    )
    backend = _a2a_backend(
        client,
        workflow_id="wf-1",
        gate_reviewer=_approving_reviewer(),
        max_gate_rounds=1,
    )

    with pytest.raises(ToolBuildError, match="exceeded 1 input rounds"):
        await backend.build(_request())


async def test_a2a_backend_request_changes_requires_notes() -> None:
    async def _changes_no_notes(request, gate):
        return "request_changes", ""

    client = _FakeHttpClient(
        {
            ("GET", "/.well-known/agent-card.json"): [HttpResponse(200, _a2a_card())],
            ("POST", "/api/v1/ting/a2a"): [
                _rpc_result({"task": _a2a_task("TASK_STATE_SUBMITTED")}),
                _rpc_result(_a2a_gated_task()),
            ],
        }
    )
    backend = _a2a_backend(client, workflow_id="wf-1", gate_reviewer=_changes_no_notes)

    with pytest.raises(ToolBuildError, match="without notes"):
        await backend.build(_request())


async def test_a2a_backend_stale_gate_reply_keeps_polling() -> None:
    stale_error = HttpResponse(
        200,
        {
            "jsonrpc": "2.0",
            "id": "1",
            "error": {
                "code": -32602,
                "message": "task task-1 has no pending gate matching the reply",
            },
        },
    )
    client = _FakeHttpClient(
        {
            ("GET", "/.well-known/agent-card.json"): [HttpResponse(200, _a2a_card())],
            ("POST", "/api/v1/ting/a2a"): [
                _rpc_result({"task": _a2a_task("TASK_STATE_SUBMITTED")}),
                _rpc_result(_a2a_gated_task()),
                stale_error,
                _rpc_result(_a2a_task("TASK_STATE_COMPLETED", artifacts=_A2A_DONE_ARTIFACTS)),
            ],
        }
    )
    backend = _a2a_backend(client, workflow_id="wf-1", gate_reviewer=_approving_reviewer())

    result = await backend.build(_request())

    exchanges = result.build_evidence["gate_exchanges"]
    assert exchanges[0]["delivery"] == "stale"


def _a2a_questioned_task() -> dict:
    task = _a2a_task("TASK_STATE_INPUT_REQUIRED")
    task["metadata"] = {
        "pendingQuestions": [
            {
                "requestId": "help-1",
                "persona": "specification-framer",
                "question": "Which namespaces are in scope?",
                "reason": "needs_context",
                "recommendation": "All namespaces.",
                "attempted": [],
            }
        ]
    }
    return task


async def test_a2a_backend_answers_peer_question_and_completes() -> None:
    seen: list = []

    async def _answerer(request, question):
        seen.append((request.name, question["question"]))
        return "All namespaces, read-only; totals per namespace and storage class."

    client = _FakeHttpClient(
        {
            ("GET", "/.well-known/agent-card.json"): [HttpResponse(200, _a2a_card())],
            ("POST", "/api/v1/ting/a2a"): [
                _rpc_result({"task": _a2a_task("TASK_STATE_SUBMITTED")}),
                _rpc_result(_a2a_questioned_task()),
                _rpc_result({"task": _a2a_task("TASK_STATE_WORKING")}),
                _rpc_result(_a2a_task("TASK_STATE_COMPLETED", artifacts=_A2A_DONE_ARTIFACTS)),
            ],
        }
    )
    backend = _a2a_backend(client, workflow_id="wf-1", question_answerer=_answerer)

    result = await backend.build(_request())

    assert seen == [("mimir_metric_window", "Which namespaces are in scope?")]
    exchanges = result.build_evidence["gate_exchanges"]
    assert exchanges[0]["kind"] == "question"
    assert exchanges[0]["delivery"] == "delivered"
    assert exchanges[0]["question"]["persona"] == "specification-framer"
    assert exchanges[0]["answer"].startswith("All namespaces")
    reply = next(
        body
        for body in client.post_bodies
        if body["method"] == "SendMessage" and body["params"]["message"].get("taskId") == "task-1"
    )
    # A question reply is a plain informative message — no gateDecision.
    assert "gateDecision" not in reply["params"]["message"]["metadata"]
    assert reply["params"]["message"]["metadata"]["requestId"] == "help-1"
    assert reply["params"]["message"]["parts"][0]["text"].startswith("All namespaces")


async def test_a2a_backend_suspends_question_without_answerer() -> None:
    client = _FakeHttpClient(
        {
            ("GET", "/.well-known/agent-card.json"): [HttpResponse(200, _a2a_card())],
            ("POST", "/api/v1/ting/a2a"): [
                _rpc_result({"task": _a2a_task("TASK_STATE_SUBMITTED")}),
                _rpc_result(_a2a_questioned_task()),
            ],
        }
    )
    backend = _a2a_backend(client, workflow_id="wf-1", gate_reviewer=_approving_reviewer())

    with pytest.raises(ToolBuildInputRequiredError) as raised:
        await backend.build(_request())
    assert raised.value.task_id == "task-1"
    assert raised.value.input_kind == "question"
    assert raised.value.prompt == "Which namespaces are in scope?"
    assert raised.value.continuation["reply_metadata"] == {"requestId": "help-1"}


async def test_a2a_backend_resumes_same_task_after_external_question_answer() -> None:
    client = _FakeHttpClient(
        {
            ("GET", "/.well-known/agent-card.json"): [HttpResponse(200, _a2a_card())],
            ("POST", "/api/v1/ting/a2a"): [
                _rpc_result({"task": _a2a_task("TASK_STATE_SUBMITTED")}),
                _rpc_result(_a2a_questioned_task()),
                _rpc_result({"task": _a2a_task("TASK_STATE_WORKING")}),
                _rpc_result(
                    _a2a_task(
                        "TASK_STATE_COMPLETED",
                        artifacts=_A2A_DONE_ARTIFACTS,
                    )
                ),
            ],
        }
    )
    backend = _a2a_backend(client, workflow_id="wf-1")

    with pytest.raises(ToolBuildInputRequiredError) as raised:
        await backend.build(_request())

    continuation = {
        **raised.value.continuation,
        "answer": "All namespaces, read-only.",
    }
    result = await backend.build(replace(_request(), continuation=continuation))

    replies = [
        body
        for body in client.post_bodies
        if body["method"] == "SendMessage" and body["params"]["message"].get("taskId") == "task-1"
    ]
    assert len(replies) == 1
    assert replies[0]["params"]["message"]["metadata"] == {"requestId": "help-1"}
    assert replies[0]["params"]["message"]["parts"] == [{"text": "All namespaces, read-only."}]
    exchanges = result.build_evidence["gate_exchanges"]
    assert exchanges == [
        {
            "round": 1,
            "kind": "question",
            "question": _a2a_questioned_task()["metadata"]["pendingQuestions"][0],
            "answer": "All namespaces, read-only.",
            "delivery": "delivered",
        }
    ]
