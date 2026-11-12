"""The C2 routes on the Mímir app: Forge authenticates them, nothing else is loosened."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from mimir.adapters.memory_rest import is_memory_tool_route
from mimir.app import create_app
from mimir.config import MimirServiceConfig
from niuu.cap_telemetry import cap_hit_counts, reset_cap_hits
from tests.test_mimir.unit.memory_fakes import (
    MCP,
    PROJECT_ID,
    SESSION_ID,
    SIGNING_KEY,
    FakeGBrain,
    session_token,
)
from tests.test_mimir.unit.test_auth_mode import _OIDC_ISSUER_KWARGS, JWKS_URI, PUBLIC_JWK

FORGE = "http://forge.test"
CONTEXT_URL = f"{FORGE}/api/v1/forge/sessions/{SESSION_ID}/memory-context"
_BACKEND = {"adapter": "tests.test_mimir.unit.fakes.InMemoryMimirAdapter"}
TOOLS = [
    "memory_search",
    "memory_read",
    "memory_entity",
    "memory_recall",
    "memory_add_source",
    "memory_write_page",
    "memory_add_timeline",
    "memory_remember",
    "memory_retire",
    "memory_propose",
]


def _memory(**overrides) -> dict:
    return {
        "enabled": True,
        "forge_base_url": FORGE,
        "systems": [
            {
                "name": "acme-brain",
                "adapter": "mimir.backends.gbrain.GBrainBackend",
                "mcp_url": MCP,
                "api_token": "tok",
                "signing_key": SIGNING_KEY,
            }
        ],
        "tenants": {"acme": "acme-brain"},
        **overrides,
    }


def _forge_answer(tenant: str = "acme") -> dict:
    return {
        "session_id": SESSION_ID,
        "launch_id": "launch-1",
        "owner_id": "ines",
        "tenant_id": tenant,
        "project_id": PROJECT_ID,
        "workflow_run_id": None,
        "project_memory": {"write_prefixes": ["decisions/"]},
    }


@pytest.fixture(autouse=True)
def _reset_caps():
    reset_cap_hits()


@pytest.fixture
def services():
    brain = FakeGBrain()
    with respx.mock(assert_all_called=False) as router:
        router.post(MCP).mock(side_effect=brain.handle)
        forge = router.get(CONTEXT_URL).mock(return_value=httpx.Response(200, json=_forge_answer()))
        router.get(JWKS_URI).mock(return_value=httpx.Response(200, json={"keys": [PUBLIC_JWK]}))
        yield brain, forge


def _client(tmp_path: Path, **config) -> TestClient:
    settings = MimirServiceConfig(
        path=str(tmp_path / "mimir"), backend=_BACKEND, memory=_memory(), **config
    )
    return TestClient(create_app(settings))


def _auth(token: str | None = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token or session_token()}"}


class TestAuthentication:
    @pytest.mark.parametrize("prefix", ["/mimir", "/api/v1/mimir"])
    def test_the_tools_are_listed_for_a_session_forge_confirms(
        self, tmp_path, services, prefix
    ) -> None:
        brain, forge = services
        with _client(tmp_path) as client:
            response = client.get(f"{prefix}/memory/tools", headers=_auth())
        assert response.status_code == 200, response.text
        body = response.json()
        assert [t["name"] for t in body["tools"]] == TOOLS
        assert f"session:{SESSION_ID}" in body["instructions"]
        assert f"project:{PROJECT_ID}" in body["instructions"]
        assert "decisions/" in body["instructions"]
        assert forge.calls.last.request.headers["authorization"] == _auth()["Authorization"]

    def test_no_token_is_401(self, tmp_path, services) -> None:
        with _client(tmp_path) as client:
            assert client.get("/mimir/memory/tools").status_code == 401
            assert client.post("/mimir/memory/tools/memory_search", json={}).status_code == 401

    def test_a_token_that_is_not_a_jwt_is_401(self, tmp_path, services) -> None:
        with _client(tmp_path) as client:
            assert client.get("/mimir/memory/tools", headers=_auth("abc")).status_code == 401

    @pytest.mark.parametrize("claim", [None, "", "../../admin", "a/b", 7])
    def test_a_token_naming_no_session_is_401_and_forge_is_not_asked(
        self, tmp_path, services, claim
    ) -> None:
        _, forge = services
        token = session_token(workload_session_id=claim)
        with _client(tmp_path) as client:
            response = client.get("/mimir/memory/tools", headers=_auth(token))
        assert response.status_code == 401
        assert "workload_session_id" in response.json()["detail"]
        assert not forge.called

    def test_an_enabled_gateway_without_forge_does_not_start(self, tmp_path) -> None:
        settings = MimirServiceConfig(
            path=str(tmp_path / "mimir"), backend=_BACKEND, memory=_memory(forge_base_url="")
        )
        with pytest.raises(ValueError, match="memory.forge_base_url"):
            create_app(settings)

    @pytest.mark.parametrize("status", [401, 403, 404, 500])
    def test_a_forge_refusal_is_401(self, tmp_path, services, status) -> None:
        _, forge = services
        forge.mock(return_value=httpx.Response(status))
        with _client(tmp_path) as client:
            response = client.post(
                "/mimir/memory/tools/memory_search", headers=_auth(), json={"query": "x"}
            )
        assert response.status_code == 401
        assert "Forge did not confirm" in response.json()["detail"]

    def test_an_unreachable_forge_is_401(self, tmp_path, services) -> None:
        _, forge = services
        forge.mock(side_effect=httpx.ConnectError("down"))
        with _client(tmp_path) as client:
            assert client.get("/mimir/memory/tools", headers=_auth()).status_code == 401

    def test_a_session_of_another_tenant_is_403_on_a_tenant_instance(
        self, tmp_path, services
    ) -> None:
        _, forge = services
        forge.mock(return_value=httpx.Response(200, json=_forge_answer("globex")))
        with _client(tmp_path, tenant_id="acme") as client:
            assert client.get("/mimir/memory/tools", headers=_auth()).status_code == 403

    def test_an_org_without_a_memory_system_is_503_naming_the_setting(
        self, tmp_path, services
    ) -> None:
        _, forge = services
        forge.mock(return_value=httpx.Response(200, json=_forge_answer("initech")))
        with _client(tmp_path) as client:
            response = client.get("/mimir/memory/tools", headers=_auth())
        assert response.status_code == 503
        assert "memory.tenants" in response.json()["detail"]


class TestOtherRoutesStayClosed:
    def test_under_oidc_a_session_credential_still_cannot_reach_other_routes(
        self, tmp_path, services
    ) -> None:
        with _client(
            tmp_path,
            auth_mode="oidc",
            identity_adapter="identity.adapters.jwks.JwksBearerAuthenticationAdapter",
            identity_kwargs=_OIDC_ISSUER_KWARGS,
        ) as client:
            assert client.get("/mimir/memory/tools", headers=_auth()).status_code == 200
            assert client.get("/mimir/stats", headers=_auth()).status_code == 401
            assert client.get("/mimir/stats").status_code == 401
            assert client.delete("/mimir/memory/tools", headers=_auth()).status_code == 401

    def test_a_tenant_instance_still_refuses_other_routes(self, tmp_path, services) -> None:
        with _client(tmp_path, tenant_id="acme") as client:
            assert client.get("/mimir/stats", headers=_auth()).status_code == 403

    def test_disabled_memory_serves_no_tools(self, tmp_path, services) -> None:
        settings = MimirServiceConfig(path=str(tmp_path / "mimir"), backend=_BACKEND)
        with TestClient(create_app(settings)) as client:
            assert client.get("/mimir/memory/tools", headers=_auth()).status_code == 404

    @pytest.mark.parametrize(
        ("method", "path", "exempt"),
        [
            ("GET", "/mimir/memory/tools", True),
            ("GET", "/api/v1/mimir/memory/tools", True),
            ("POST", "/mimir/memory/tools/memory_search", True),
            ("POST", "/api/v1/mimir/memory/tools/memory_search", True),
            ("POST", "/mimir/memory/tools", False),
            ("GET", "/mimir/memory/tools/memory_search", False),
            ("GET", "/mimir/memory/tools/../stats", False),
            ("POST", "/mimir/memory/tools/a/b", False),
            ("GET", "/mimir/memory/toolsx", False),
            ("GET", "/mimir/stats", False),
        ],
    )
    def test_only_the_two_routes_are_exempt(self, method, path, exempt) -> None:
        assert is_memory_tool_route(method, path) is exempt


class TestCalls:
    def test_an_unknown_tool_is_404(self, tmp_path, services) -> None:
        with _client(tmp_path) as client:
            response = client.post("/mimir/memory/tools/memory_ask", headers=_auth(), json={})
        assert response.status_code == 404

    def test_a_body_that_is_not_json_is_400(self, tmp_path, services) -> None:
        with _client(tmp_path) as client:
            response = client.post(
                "/mimir/memory/tools/memory_search", headers=_auth(), content=b"{nope"
            )
        assert response.status_code == 400

    def test_a_write_names_the_layer_it_landed_in(self, tmp_path, services) -> None:
        with _client(tmp_path) as client:
            response = client.post(
                "/api/v1/mimir/memory/tools/memory_write_page",
                headers=_auth(),
                json={
                    "ref": "decisions/drop-queue",
                    "compiled_truth": "We dropped the queue.",
                    "layer": "project",
                    "evidence": ["url:https://linear.app/acme/LIN-482"],
                },
            )
        result = response.json()
        assert response.status_code == 200
        assert result["isError"] is False
        assert result["structuredContent"]["layer"] == f"project:{PROJECT_ID}"
        assert result["structuredContent"]["written"] is True

    def test_a_write_outside_the_prefixes_names_the_proposal_and_approver(
        self, tmp_path, services
    ) -> None:
        with _client(tmp_path) as client:
            result = client.post(
                "/mimir/memory/tools/memory_write_page",
                headers=_auth(),
                json={"ref": "services/booking-api", "compiled_truth": "x", "layer": "project"},
            ).json()
        assert result["isError"] is False
        proposal = result["structuredContent"]["proposal"]
        assert proposal["approver"] == f"the owners of project {PROJECT_ID}"
        assert proposal["id"] in result["content"][0]["text"]

    def test_a_refusal_is_an_error_naming_the_rule_and_counted(self, tmp_path, services) -> None:
        with _client(tmp_path) as client:
            result = client.post(
                "/mimir/memory/tools/memory_write_page",
                headers=_auth(),
                json={"ref": "x", "compiled_truth": "x", "layer": "project:other-project"},
            ).json()
        assert result["isError"] is True
        assert result["structuredContent"]["rule"] == "layer_not_in_session"
        assert "layer_not_in_session" in result["content"][0]["text"]
        assert cap_hit_counts()["memory.write_rules.layer_not_in_session"] == 1
