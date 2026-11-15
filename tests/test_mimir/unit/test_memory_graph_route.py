"""``GET {base}/memory/graph`` (contract G1): who may ask, which layers they get, the answer.

The app runs in mini mode (the allow-all identity adapter, a configured dev principal) or
under OIDC (a verified JWT). Forge (the person's projects) and the org's gbrain are faked
over respx; pages are written with the real ``GBrainBackend`` so their attribution is sealed
and read back the way the gateway reads it.
"""

from __future__ import annotations

import asyncio
import shutil
import time
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
import respx
from fastapi.testclient import TestClient

from mimir.app import create_app
from mimir.backends.gbrain import GBrainBackend
from mimir.config import MimirServiceConfig
from mimir.memory.models import (
    Attribution,
    Evidence,
    EvidenceKind,
    NewSource,
    NewTimelineRow,
    PageWrite,
    SourceKind,
)
from niuu.cap_telemetry import cap_hit_counts, reset_cap_hits
from niuu.domain.mimir_facts import TrustLevel
from tests.test_mimir.unit.memory_fakes import (
    ADMIN_TOKEN,
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
PROJECTS = f"{FORGE}/api/v1/forge/projects"
OTHER_PROJECT = "9a0e0000-0000-4000-8000-0000000000bb"
PROJECT = f"project:{PROJECT_ID}"
OTHER = f"project:{OTHER_PROJECT}"
PATH = "/mimir/memory/graph"
_BACKEND = {"adapter": "tests.test_mimir.unit.fakes.InMemoryMimirAdapter"}
_OIDC = {
    "auth_mode": "oidc",
    "identity_adapter": "identity.adapters.jwks.JwksBearerAuthenticationAdapter",
    "identity_kwargs": _OIDC_ISSUER_KWARGS,
}
_MINI = {
    "auth_mode": "none",
    "identity_adapter": "identity.adapters.identity.AllowAllHeaderAuthenticationAdapter",
    "identity_kwargs": {"user_id": "ines", "tenant_id": "acme", "roles": []},
}
_AUTHOR = Attribution(
    "agent:test", TrustLevel.AGENT, (Evidence(EvidenceKind.STATEMENT, "s1"),), datetime.now(UTC)
)


@pytest.fixture(autouse=True)
def _reset_caps():
    reset_cap_hits()


@pytest.fixture
def world():
    """Forge, the identity provider's keys and the org's brain, over one respx router."""
    brain = FakeGBrain()
    with respx.mock(assert_all_called=False) as router:
        brain.mount(router)
        router.get(JWKS_URI).mock(return_value=httpx.Response(200, json={"keys": [PUBLIC_JWK]}))
        router.get(PROJECTS).mock(
            return_value=httpx.Response(200, json=[{"id": PROJECT_ID, "name": "Booking platform"}])
        )
        yield brain, router


def _memory(**overrides: Any) -> dict:
    return {
        "enabled": True,
        "forge_base_url": FORGE,
        "systems": [
            {
                "name": "acme-brain",
                "adapter": "mimir.backends.gbrain.GBrainBackend",
                "mcp_url": MCP,
                "admin_token": ADMIN_TOKEN,
                "signing_key": SIGNING_KEY,
            }
        ],
        "tenants": {"acme": "acme-brain"},
        "service_auth": {"adapter": "niuu.adapters.outbound.http_auth.NoAuthHeaderAdapter"},
        "connector_service_subjects": ["ting"],
        **overrides,
    }


def _client(tmp_path: Path, mode: dict = _MINI, **memory: Any) -> TestClient:
    settings = MimirServiceConfig(
        path=str(tmp_path / "mimir"), backend=_BACKEND, memory=_memory(**memory), **mode
    )
    return TestClient(create_app(settings))


def _jwt(**claims: Any) -> str:
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


def _write(namespace: str, slug: str, truth: str, *, page_type: str = "", rows=()) -> None:
    """A page, and its timeline rows, written through the real backend."""

    async def go() -> None:
        backend = GBrainBackend(MCP, admin_token=ADMIN_TOKEN, signing_key=SIGNING_KEY)
        try:
            await backend.create_namespace(namespace)
            await backend.put_page(
                namespace,
                slug,
                PageWrite(compiled_truth=truth, title=slug, page_type=page_type),
                _AUTHOR,
                expected_revision=None,
            )
            for on, summary, refs in rows:
                cited = tuple(Evidence.parse(r) for r in refs)
                await backend.append_timeline(
                    namespace,
                    slug,
                    NewTimelineRow(on=date.fromisoformat(on), summary=summary, evidence=cited),
                    replace(_AUTHOR, evidence=cited),
                )
        finally:
            await backend.close()

    asyncio.run(go())


def _write_source(namespace: str, title: str, content: str, kind: SourceKind) -> str:
    async def go() -> str:
        backend = GBrainBackend(MCP, admin_token=ADMIN_TOKEN, signing_key=SIGNING_KEY)
        try:
            await backend.create_namespace(namespace)
            record, _ = await backend.put_source(
                namespace, NewSource(title=title, content=content, kind=kind), _AUTHOR
            )
            return record.id
        finally:
            await backend.close()

    return asyncio.run(go())


def _seed() -> str:
    """An org page, a project page linking it, and a ticket the project page cites."""
    ticket = _write_source(
        f"project-{PROJECT_ID}", "LIN-482 Booking cut-over plan", "Cut over.", SourceKind.TICKET
    )
    _write("org", "standards/security", "All services follow the security standard.")
    _write(
        f"project-{PROJECT_ID}",
        "decisions/booking-cutover",
        "Cut over booking-api on Friday.\n\n- [[standards/security]] — rel: follows — the rule\n"
        "- [[people/ines]]\n",
        page_type="decision",
        rows=[("2026-11-03", "Decided", [f"source:{ticket}"])],
    )
    return ticket


class TestTheAnswer:
    @pytest.mark.parametrize("prefix", ["/mimir", "/api/v1/mimir"])
    def test_a_person_gets_their_layers_nodes_edges_and_unresolved_links(
        self, tmp_path, world, prefix
    ) -> None:
        ticket = _seed()
        with _client(tmp_path) as client:
            response = client.get(f"{prefix}/memory/graph")
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {"layers", "nodes", "edges", "unresolved"}
        assert body["layers"] == [
            {"id": "org", "name": "Org"},
            {"id": PROJECT, "name": "Booking platform"},
        ]
        page_id = f"{PROJECT}/decisions/booking-cutover"
        by_id = {n["id"]: n for n in body["nodes"]}
        assert set(by_id) == {
            "org/standards/security",
            page_id,
            f"{PROJECT}/_sources/{ticket}",
        }
        decision = by_id[page_id]
        assert (decision["kind"], decision["layer"], decision["path"]) == (
            "decision",
            PROJECT,
            "decisions/booking-cutover",
        )
        assert decision["summary"] == "Cut over booking-api on Friday."
        assert (decision["first_seen"], decision["changed_at"]) == ("2026-11-03", "2026-11-03")
        assert by_id[f"{PROJECT}/_sources/{ticket}"]["kind"] == "ticket"
        assert {(e["source"], e["target"], e["relation"], e["since"]) for e in body["edges"]} == {
            (page_id, "org/standards/security", "follows", None),
            (page_id, f"{PROJECT}/_sources/{ticket}", "cites", "2026-11-03"),
        }
        assert body["unresolved"] == [{"source": page_id, "target_slug": "people/ines"}]

    def test_the_forge_call_carries_the_persons_own_login(self, tmp_path, world) -> None:
        _, router = world
        with _client(tmp_path, _OIDC) as client:
            token = _jwt()
            assert client.get(PATH, headers=_bearer(token)).status_code == 200
        sent = [c.request for c in router.calls if str(c.request.url) == PROJECTS]
        assert sent[-1].headers["authorization"] == f"Bearer {token}"


class TestWhichLayers:
    def test_a_project_the_person_is_not_on_is_absent(self, tmp_path, world) -> None:
        _write("org", "o", "Org page.")
        _write(f"project-{PROJECT_ID}", "p", "Member page.")
        _write(f"project-{OTHER_PROJECT}", "secret", "Not theirs.")
        with _client(tmp_path) as client:
            body = client.get(PATH).json()
        assert [layer["id"] for layer in body["layers"]] == ["org", PROJECT]
        assert {n["id"] for n in body["nodes"]} == {"org/o", f"{PROJECT}/p"}
        assert OTHER_PROJECT not in str(body)

    def test_layers_narrows_to_a_subset(self, tmp_path, world) -> None:
        _write("org", "o", "Org page.")
        _write(f"project-{PROJECT_ID}", "p", "Member page.")
        with _client(tmp_path) as client:
            org_only = client.get(PATH, params={"layers": "org"}).json()
            project_only = client.get(PATH, params={"layers": PROJECT}).json()
            both = client.get(PATH, params={"layers": f"{PROJECT},org"}).json()
        assert [layer["id"] for layer in org_only["layers"]] == ["org"]
        assert [n["id"] for n in org_only["nodes"]] == ["org/o"]
        assert [n["id"] for n in project_only["nodes"]] == [f"{PROJECT}/p"]
        assert [layer["id"] for layer in both["layers"]] == ["org", PROJECT]

    def test_layers_never_widens_to_a_project_the_person_is_not_on(self, tmp_path, world) -> None:
        _write(f"project-{OTHER_PROJECT}", "secret", "Not theirs.")
        with _client(tmp_path) as client:
            response = client.get(PATH, params={"layers": f"org,{OTHER}"})
        assert response.status_code == 404
        assert OTHER in response.json()["detail"]
        assert "secret" not in response.text

    @pytest.mark.parametrize("layers", ["", "org,", "nonsense", "org,,project:x"])
    def test_a_malformed_layers_list_is_a_422(self, tmp_path, world, layers) -> None:
        with _client(tmp_path) as client:
            assert client.get(PATH, params={"layers": layers}).status_code == 422

    def test_a_layer_kind_a_person_never_reads_here_is_a_404(self, tmp_path, world) -> None:
        with _client(tmp_path) as client:
            assert client.get(PATH, params={"layers": "run:5e55"}).status_code == 404

    def test_a_person_with_no_projects_gets_the_org(self, tmp_path, world) -> None:
        _, router = world
        router.get(PROJECTS).mock(return_value=httpx.Response(200, json=[]))
        _write("org", "o", "Org page.")
        with _client(tmp_path) as client:
            body = client.get(PATH).json()
        assert [layer["id"] for layer in body["layers"]] == ["org"]


class TestWhoMayAsk:
    def test_a_person_under_oidc_is_admitted(self, tmp_path, world) -> None:
        with _client(tmp_path, _OIDC) as client:
            assert client.get(PATH, headers=_bearer(_jwt())).status_code == 200

    def test_no_token_under_oidc_is_401(self, tmp_path, world) -> None:
        with _client(tmp_path, _OIDC) as client:
            assert client.get(PATH).status_code == 401

    def test_a_session_credential_is_refused_under_oidc(self, tmp_path, world) -> None:
        token = _jwt(token_use="forge_session", workload_session_id=SESSION_ID, scopes=["x"])
        with _client(tmp_path, _OIDC) as client:
            assert client.get(PATH, headers=_bearer(token)).status_code == 403

    def test_a_session_credential_is_refused_in_mini_mode(self, tmp_path, world) -> None:
        with _client(tmp_path) as client:
            response = client.get(PATH, headers=_bearer(session_token()))
        assert response.status_code == 403
        assert "person" in response.json()["detail"]

    def test_a_scoped_workload_token_is_refused(self, tmp_path, world) -> None:
        token = jwt.encode(
            {"sub": "x", "token_use": "valkyrie_build", "scopes": ["compute_host_join"]},
            "k" * 40,
            algorithm="HS256",
        )
        with _client(tmp_path) as client:
            assert client.get(PATH, headers=_bearer(token)).status_code == 403

    def test_a_connector_credential_is_refused(self, tmp_path, world) -> None:
        with _client(tmp_path, _OIDC) as client:
            response = client.get(PATH, headers=_bearer(_jwt(act={"sub": "ting"})))
        assert response.status_code == 403
        assert "connector" in response.json()["detail"]

    def test_a_scoped_connector_credential_is_refused(self, tmp_path, world) -> None:
        token = _jwt(act={"sub": "ting"}, token_use="valkyrie_build", scopes=["compute_host_join"])
        with _client(tmp_path, _OIDC) as client:
            assert client.get(PATH, headers=_bearer(token)).status_code == 403

    def test_another_orgs_person_is_refused_by_a_tenant_instance(self, tmp_path, world) -> None:
        with _client(tmp_path, {**_OIDC, "tenant_id": "globex"}) as client:
            assert client.get(PATH, headers=_bearer(_jwt())).status_code == 403

    def test_the_route_is_not_a_session_route(self) -> None:
        from mimir.adapters.memory_rest import is_session_route

        assert not is_session_route("GET", PATH)
        assert not is_session_route("GET", "/api/v1/mimir/memory/graph")


class TestFailures:
    def test_a_layer_that_cannot_be_read_fails_the_response_naming_a_layer(
        self, tmp_path, world
    ) -> None:
        brain, _ = world
        _write("org", "o", "Org page.")
        brain.fail_tools.add("list_pages")
        with _client(tmp_path) as client:
            response = client.get(PATH)
        assert response.status_code == 502
        detail = response.json()["detail"]
        assert "layer org" in detail or f"layer {PROJECT}" in detail
        assert "nodes" not in response.json()

    def test_a_large_layer_is_returned_whole(self, tmp_path, world) -> None:
        for index in range(25):
            _write("org", f"page-{index}", "X.")
        with _client(tmp_path) as client:
            response = client.get(PATH)
        assert response.status_code == 200, response.text
        assert len(response.json()["nodes"]) == 25
        assert cap_hit_counts() == {}

    def test_forge_refusing_the_login_is_passed_on(self, tmp_path, world) -> None:
        _, router = world
        router.get(PROJECTS).mock(return_value=httpx.Response(401))
        with _client(tmp_path) as client:
            assert client.get(PATH).status_code == 401

    def test_forge_being_down_is_a_502(self, tmp_path, world) -> None:
        _, router = world
        router.get(PROJECTS).mock(return_value=httpx.Response(500))
        with _client(tmp_path) as client:
            response = client.get(PATH)
        assert response.status_code == 502
        assert "Forge" in response.json()["detail"]

    def test_an_org_without_a_memory_system_is_a_503_naming_the_setting(
        self, tmp_path, world
    ) -> None:
        with _client(tmp_path, tenants={"globex": "acme-brain"}) as client:
            response = client.get(PATH)
        assert response.status_code == 503
        assert "memory.tenants" in response.json()["detail"]


class TestCaching:
    def _list_calls(self, brain: FakeGBrain) -> int:
        return sum(1 for name, _ in brain.calls if name == "list_pages")

    def test_a_second_request_inside_the_window_is_served_without_reading(
        self, tmp_path, world
    ) -> None:
        brain, _ = world
        _write("org", "o", "Org page.")
        with _client(tmp_path, graph_cache_seconds=60) as client:
            first = client.get(PATH).json()
            reads = self._list_calls(brain)
            second = client.get(PATH).json()
        assert reads > 0
        assert second == first
        assert self._list_calls(brain) == reads

    def test_a_narrowed_request_is_a_different_graph(self, tmp_path, world) -> None:
        _write("org", "o", "Org page.")
        _write(f"project-{PROJECT_ID}", "p", "Member page.")
        with _client(tmp_path) as client:
            whole = client.get(PATH).json()
            org = client.get(PATH, params={"layers": "org"}).json()
        assert len(whole["nodes"]) == 2
        assert len(org["nodes"]) == 1

    def test_zero_seconds_reads_every_time(self, tmp_path, world) -> None:
        brain, _ = world
        _write("org", "o", "Org page.")
        with _client(tmp_path, graph_cache_seconds=0) as client:
            before = self._list_calls(brain)
            client.get(PATH)
            one_build = self._list_calls(brain) - before
            client.get(PATH)
        assert one_build > 0
        assert self._list_calls(brain) - before == one_build * 2


def test_the_settings_are_documented_with_defaults() -> None:
    fields = MimirServiceConfig.model_fields["memory"].annotation.model_fields
    assert (
        fields["graph_cache_seconds"].default,
        fields["graph_concurrency"].default,
    ) == (30.0, 8)
    assert "graph_max_records_per_layer" not in fields
    for name in ("graph_cache_seconds", "graph_concurrency"):
        assert fields[name].description


@pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is required to render charts")
class TestChart:
    def _memory(self, tmp_path: Path, **values: Any) -> dict:
        from tests.test_mimir.unit.test_memory_chart import _STEP2, _SYSTEM, _config, _docs, _helm

        memory = {
            "enabled": True,
            "forgeBaseUrl": "http://volundr:8080",
            "systems": [_SYSTEM],
            "tenants": {"acme": "acme-brain"},
            **_STEP2,
            **values,
        }
        return _config(_docs(_helm(tmp_path, memory)))["memory"]

    def test_the_graph_settings_render_from_named_values(self, tmp_path: Path) -> None:
        memory = self._memory(tmp_path, graphCacheSeconds=0, graphConcurrency=4)
        assert memory["graph_cache_seconds"] == 0
        assert memory["graph_concurrency"] == 4

    def test_unset_graph_settings_leave_the_service_defaults(self, tmp_path: Path) -> None:
        memory = self._memory(tmp_path)
        assert not [key for key in memory if key.startswith("graph_")]
