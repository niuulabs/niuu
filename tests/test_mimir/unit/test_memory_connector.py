"""The connector route (contract C11): ``POST {base}/memory/sources``.

A platform service credential whose actor is in ``memory.connector_service_subjects``
writes a source at ``tool`` trust into a project or org layer. Mini mode without auth
admits the local caller; nothing admits a session, a person or a scoped credential.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import httpx
import jwt
import pytest
import respx
from fastapi.testclient import TestClient

from mimir.adapters.memory_rest import is_memory_tool_route
from mimir.app import create_app
from mimir.backends.gbrain import GBrainBackend
from mimir.config import MimirServiceConfig
from niuu.cap_telemetry import reset_cap_hits
from niuu.domain.mimir_facts import TrustLevel
from tests.test_mimir.unit.memory_fakes import (
    MCP,
    PROJECT_ID,
    SESSION_ID,
    SIGNING_KEY,
    FakeGBrain,
    session_token,
)
from tests.test_mimir.unit.test_auth_mode import (
    _OIDC_ISSUER_KWARGS,
    AUDIENCE,
    ISSUER,
    JWKS_URI,
    KID,
    PRIVATE_KEY,
    PUBLIC_JWK,
)

FORGE = "http://forge.test"
BIFROST = "http://bifrost.test"
_BACKEND = {"adapter": "tests.test_mimir.unit.fakes.InMemoryMimirAdapter"}
PROJECT = f"project:{PROJECT_ID}"
NAMESPACE = f"project-{PROJECT_ID}"
TICKET_URL = "https://linear.app/acme/issue/LIN-482"
PATH = "/mimir/memory/sources"
_OIDC = {
    "auth_mode": "oidc",
    "identity_adapter": "identity.adapters.jwks.JwksBearerAuthenticationAdapter",
    "identity_kwargs": _OIDC_ISSUER_KWARGS,
}
_MINI = {
    "auth_mode": "none",
    "identity_adapter": "identity.adapters.identity.AllowAllHeaderAuthenticationAdapter",
    "identity_kwargs": {"user_id": "dev-user", "tenant_id": "acme", "roles": []},
}


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
        "service_auth": {"adapter": "niuu.adapters.outbound.http_auth.NoAuthHeaderAdapter"},
        "bifrost_url": BIFROST,
        "answer_model": "answer-model-1",
        **overrides,
    }


def _body(**overrides) -> dict:
    return {
        "tenant_id": "acme",
        "layer": PROJECT,
        "connector": "linear",
        "external_id": "LIN-482",
        "title": "LIN-482 Booking cut-over plan",
        "content": "Cut over booking-api on Friday.\n\nines: agreed.",
        "kind": "ticket",
        "origin_url": TICKET_URL,
        "fetched_at": "2026-11-12T09:00:00+00:00",
        "upstream_updated_at": "2026-11-12T08:30:00+00:00",
        **overrides,
    }


def _jwt(**claims) -> str:
    now = int(time.time())
    body = {
        "sub": "ines",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "exp": now + 300,
        "iat": now,
        "tenant_id": "acme",
        **claims,
    }
    return jwt.encode(body, PRIVATE_KEY, algorithm="RS256", headers={"kid": KID})


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _service(actor: str = "ting", **claims) -> dict[str, str]:
    """A workload-identity exchange token: unscoped, acting for its mapped owner."""
    return _bearer(_jwt(act={"sub": actor}, **claims))


@pytest.fixture(autouse=True)
def _reset_caps():
    reset_cap_hits()


@pytest.fixture
def forge():
    """The respx router that fakes Forge, the identity provider's keys and the org's gbrain."""
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
def brain(forge):
    brain = FakeGBrain()
    forge.post(MCP).mock(side_effect=brain.handle)
    forge.get(JWKS_URI).mock(return_value=httpx.Response(200, json={"keys": [PUBLIC_JWK]}))
    return brain


def _client(tmp_path: Path, mode: dict, *, subjects=("ting",), **config) -> TestClient:
    settings = MimirServiceConfig(
        path=str(tmp_path / "mimir"),
        backend=_BACKEND,
        memory=_memory(connector_service_subjects=list(subjects)),
        **mode,
        **config,
    )
    return TestClient(create_app(settings))


def _stored(namespace: str, source_id: str):
    """Read a source back from the fake brain the way the gateway does."""

    async def read():
        backend = GBrainBackend(MCP, "tok", signing_key=SIGNING_KEY)
        try:
            return await backend.get_source(namespace, source_id)
        finally:
            await backend.close()

    return asyncio.run(read())


class TestWhoMayWrite:
    @pytest.mark.parametrize("prefix", ["/mimir", "/api/v1/mimir"])
    def test_a_configured_service_writes_a_source(self, tmp_path, brain, prefix) -> None:
        with _client(tmp_path, _OIDC) as client:
            response = client.post(f"{prefix}/memory/sources", headers=_service(), json=_body())
        assert response.status_code == 200, response.text
        answer = response.json()
        assert answer["layer"] == PROJECT and answer["new"] is True
        assert answer["source_id"].startswith("src_")

    def test_the_source_is_tool_trust_by_the_connector_citing_the_ticket(
        self, tmp_path, brain
    ) -> None:
        with _client(tmp_path, _OIDC) as client:
            answer = client.post(PATH, headers=_service(), json=_body()).json()
        record = _stored(NAMESPACE, answer["source_id"])
        assert record.attribution.author == "tool:linear"
        assert record.attribution.trust is TrustLevel.TOOL
        assert [e.ref for e in record.attribution.evidence] == [f"url:{TICKET_URL}"]
        assert record.attribution.attested is True
        assert (record.title, record.kind, record.origin_url) == (
            "LIN-482 Booking cut-over plan",
            "ticket",
            TICKET_URL,
        )
        assert record.content == _body()["content"]

    def test_a_person_is_refused(self, tmp_path, brain) -> None:
        with _client(tmp_path, _OIDC) as client:
            response = client.post(PATH, headers=_bearer(_jwt()), json=_body())
        assert response.status_code == 403
        assert "connector" in response.json()["detail"]
        assert not brain.pages

    def test_another_service_is_refused(self, tmp_path, brain) -> None:
        with _client(tmp_path, _OIDC) as client:
            response = client.post(PATH, headers=_service("regin"), json=_body())
        assert response.status_code == 403
        assert not brain.pages

    def test_no_configured_subject_admits_nobody_and_names_the_setting(
        self, tmp_path, brain
    ) -> None:
        with _client(tmp_path, _OIDC, subjects=()) as client:
            response = client.post(PATH, headers=_service(), json=_body())
        assert response.status_code == 403
        assert "memory.connector_service_subjects" in response.json()["detail"]
        assert not brain.pages

    def test_a_session_credential_is_refused_even_from_a_connector_subject(
        self, tmp_path, brain
    ) -> None:
        token = _jwt(
            act={"sub": "ting"},
            token_use="forge_session",
            workload_session_id=SESSION_ID,
            scopes=["forge:memory"],
        )
        with _client(tmp_path, _OIDC) as client:
            response = client.post(PATH, headers=_bearer(token), json=_body())
        assert response.status_code == 403
        assert not brain.pages

    def test_a_scoped_workload_token_is_refused(self, tmp_path, brain) -> None:
        token = _jwt(act={"sub": "ting"}, token_use="valkyrie_build", scopes=["compute_host_join"])
        with _client(tmp_path, _OIDC) as client:
            response = client.post(PATH, headers=_bearer(token), json=_body())
        assert response.status_code == 403
        assert not brain.pages

    def test_no_token_under_oidc_is_401(self, tmp_path, brain) -> None:
        with _client(tmp_path, _OIDC) as client:
            assert client.post(PATH, json=_body()).status_code == 401

    def test_a_token_that_does_not_verify_is_401(self, tmp_path, brain) -> None:
        forged = jwt.encode({"sub": "x", "act": {"sub": "ting"}}, "k" * 40, algorithm="HS256")
        with _client(tmp_path, _OIDC) as client:
            response = client.post(PATH, headers=_bearer(forged), json=_body())
        assert response.status_code == 401
        assert not brain.pages

    def test_a_tenant_instance_refuses_another_orgs_sources(self, tmp_path, brain) -> None:
        with _client(tmp_path, _OIDC, tenant_id="acme") as client:
            other = client.post(PATH, headers=_service(), json=_body(tenant_id="globex"))
            own = client.post(PATH, headers=_service(), json=_body())
        assert other.status_code == 403
        assert own.status_code == 200

    def test_the_local_caller_passes_in_mini_mode_without_a_token(self, tmp_path, brain) -> None:
        with _client(tmp_path, _MINI, subjects=()) as client:
            response = client.post(PATH, json=_body())
        assert response.status_code == 200, response.text
        assert response.json()["new"] is True

    def test_a_session_credential_is_refused_in_mini_mode(self, tmp_path, brain) -> None:
        with _client(tmp_path, _MINI) as client:
            response = client.post(PATH, headers=_bearer(session_token()), json=_body())
        assert response.status_code == 403
        assert not brain.pages

    def test_a_scoped_token_is_refused_in_mini_mode(self, tmp_path, brain) -> None:
        token = jwt.encode(
            {"sub": "x", "token_use": "valkyrie_build", "scopes": ["compute_host_join"]},
            "k" * 40,
            algorithm="HS256",
        )
        with _client(tmp_path, _MINI) as client:
            response = client.post(PATH, headers=_bearer(token), json=_body())
        assert response.status_code == 403
        assert not brain.pages

    def test_the_connector_route_is_not_exempt_from_identity(self) -> None:
        for path in (PATH, "/api/v1/mimir/memory/sources"):
            assert is_memory_tool_route("POST", path) is False
            assert is_memory_tool_route("GET", path) is False


class TestOtherRoutesBehaveAsBefore:
    def test_a_connector_credential_is_refused_on_the_people_routes(self, tmp_path, brain) -> None:
        with _client(tmp_path, _OIDC) as client:
            proposals = client.get(f"/mimir/memory/proposals?layer={PROJECT}", headers=_service())
            promotion = client.post(
                "/mimir/memory/promotions", headers=_service(), json={"refs": []}
            )
        assert proposals.status_code == 403
        assert promotion.status_code == 403
        assert "connector" in proposals.json()["detail"]

    def test_a_connector_credential_is_refused_on_the_session_routes(self, tmp_path, brain) -> None:
        with _client(tmp_path, _OIDC) as client:
            tools = client.get("/mimir/memory/tools", headers=_service())
            call = client.post("/mimir/memory/tools/memory_search", headers=_service(), json={})
        assert tools.status_code == 401
        assert call.status_code == 401

    def test_a_person_still_reaches_the_people_routes(self, tmp_path, brain, forge) -> None:
        forge.get(f"{FORGE}/api/v1/forge/projects/{PROJECT_ID}/role").mock(
            return_value=httpx.Response(404, json={"detail": "no"})
        )
        with _client(tmp_path, _OIDC) as client:
            response = client.get(
                f"/mimir/memory/proposals?layer={PROJECT}", headers=_bearer(_jwt())
            )
        assert response.status_code == 404


class TestTheWrite:
    def test_the_same_content_again_is_not_a_new_source(self, tmp_path, brain) -> None:
        with _client(tmp_path, _OIDC) as client:
            first = client.post(PATH, headers=_service(), json=_body()).json()
            again = client.post(
                PATH,
                headers=_service(),
                json=_body(title="LIN-482 renamed", fetched_at="2026-11-12T10:00:00+00:00"),
            ).json()
        assert first["new"] is True and again["new"] is False
        assert again["source_id"] == first["source_id"]
        assert [slug for slug in brain.pages if first["source_id"] in slug] == [
            f"l/{NAMESPACE}/_sources/{first['source_id']}"
        ]

    def test_a_changed_ticket_is_a_new_source_and_the_old_one_stays(self, tmp_path, brain) -> None:
        changed = _body()["content"] + "\n\nines: moved to Monday."
        with _client(tmp_path, _OIDC) as client:
            first = client.post(PATH, headers=_service(), json=_body()).json()
            second = client.post(PATH, headers=_service(), json=_body(content=changed)).json()
            first_again = client.post(PATH, headers=_service(), json=_body()).json()
        assert second["new"] is True
        assert second["source_id"] != first["source_id"]
        assert first_again == first | {"new": False}
        assert _stored(NAMESPACE, first["source_id"]).content == _body()["content"]
        assert _stored(NAMESPACE, second["source_id"]).content == changed

    def test_the_same_content_in_the_org_layer_is_a_new_source_there(self, tmp_path, brain) -> None:
        with _client(tmp_path, _OIDC) as client:
            project = client.post(PATH, headers=_service(), json=_body()).json()
            org = client.post(PATH, headers=_service(), json=_body(layer="org")).json()
        assert org["layer"] == "org" and org["new"] is True
        assert org["source_id"] == project["source_id"]
        assert _stored("org", org["source_id"]).attribution.author == "tool:linear"

    @pytest.mark.parametrize(
        "layer",
        [
            f"session:{SESSION_ID}",
            "run:a1b2c3d4-0000-4000-8000-000000000123",
            "attached:team-notes",
            "workspace:x",
            "project:",
            "org:acme",
        ],
    )
    def test_only_project_and_org_layers_take_connector_sources(
        self, tmp_path, brain, layer
    ) -> None:
        with _client(tmp_path, _OIDC) as client:
            response = client.post(PATH, headers=_service(), json=_body(layer=layer))
        assert response.status_code == 400, response.text
        assert not brain.pages

    def test_an_org_without_a_memory_system_is_503_naming_the_setting(
        self, tmp_path, brain
    ) -> None:
        with _client(tmp_path, _OIDC) as client:
            response = client.post(PATH, headers=_service(), json=_body(tenant_id="initech"))
        assert response.status_code == 503
        assert "memory.tenants" in response.json()["detail"]
        assert not brain.pages

    @pytest.mark.parametrize(
        "change",
        [
            {"tenant_id": ""},
            {"layer": None},
            {"connector": "Linear Inc"},
            {"connector": "tool:linear"},
            {"external_id": " "},
            {"title": ""},
            {"content": "   "},
            {"kind": "tool_output"},
            {"kind": "diary"},
            {"origin_url": "LIN-482"},
            {"origin_url": "ftp://x/y"},
            {"fetched_at": "yesterday"},
            {"upstream_updated_at": None},
        ],
    )
    def test_a_malformed_source_is_400_and_stores_nothing(self, tmp_path, brain, change) -> None:
        with _client(tmp_path, _OIDC) as client:
            response = client.post(PATH, headers=_service(), json=_body(**change))
        assert response.status_code == 400, response.text
        assert not brain.pages

    def test_a_body_that_is_not_an_object_is_400(self, tmp_path, brain) -> None:
        with _client(tmp_path, _OIDC) as client:
            assert client.post(PATH, headers=_service(), json=["x"]).status_code == 400
            assert client.post(PATH, headers=_service(), content=b"{nope").status_code == 400

    def test_a_memory_system_that_fails_is_502_not_a_stored_source(self, tmp_path, brain) -> None:
        brain.fail_tools = {"put_page"}
        with _client(tmp_path, _OIDC) as client:
            response = client.post(PATH, headers=_service(), json=_body())
        assert response.status_code == 502
        assert PROJECT_ID in response.json()["detail"]

    def test_an_unreachable_memory_system_is_502(self, tmp_path, brain) -> None:
        brain.fail_with = 500
        with _client(tmp_path, _OIDC) as client:
            assert client.post(PATH, headers=_service(), json=_body()).status_code == 502


class TestCountedAndLogged:
    def test_every_write_is_counted_by_connector_and_whether_it_was_new(
        self, tmp_path, brain, monkeypatch
    ) -> None:
        counted: list[tuple[str, dict]] = []

        class Recorder:
            def count(self, name, value=1, *, attributes=None, description=""):
                counted.append((name, attributes))

        monkeypatch.setattr("mimir.memory.gateway.get_observability", lambda: Recorder())
        with _client(tmp_path, _OIDC) as client:
            client.post(PATH, headers=_service(), json=_body())
            client.post(PATH, headers=_service(), json=_body())
            client.post(PATH, headers=_service(), json=_body(layer=f"session:{SESSION_ID}"))
        assert counted == [
            ("memory.connector.sources", {"connector": "linear", "new": True}),
            ("memory.connector.sources", {"connector": "linear", "new": False}),
        ]

    def test_a_write_is_logged_with_its_ticket_and_times(self, tmp_path, brain, caplog) -> None:
        with caplog.at_level(logging.INFO, logger="mimir.memory.gateway"):
            with _client(tmp_path, _OIDC) as client:
                answer = client.post(PATH, headers=_service(), json=_body()).json()
        line = next(r.getMessage() for r in caplog.records if "connector linear" in r.getMessage())
        assert answer["source_id"] in line
        assert "LIN-482" in line and PROJECT in line
        assert "2026-11-12T08:30:00+00:00" in line and "2026-11-12T09:00:00+00:00" in line
        assert "stored" in line


class TestConfig:
    def test_a_blank_subject_is_refused_at_startup(self) -> None:
        with pytest.raises(ValueError, match="connector_service_subjects"):
            MimirServiceConfig(memory=_memory(connector_service_subjects=["ting", " "]))

    def test_subjects_default_to_none(self) -> None:
        assert MimirServiceConfig(memory=_memory()).memory.connector_service_subjects == []
