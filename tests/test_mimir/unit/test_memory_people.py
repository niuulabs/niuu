"""The people routes (contract C9): proposals, their approval and rejection, promotions.

The app runs in mini mode without auth, so Mímir's identity adapter is the
allow-all adapter, configured per test as the person calling. Forge (C1, C5, C6
and GET /sessions/{id}) and the org's gbrain are faked over respx.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from mimir.app import create_app
from mimir.config import MimirServiceConfig
from niuu.cap_telemetry import reset_cap_hits
from tests.test_mimir.unit.memory_fakes import (
    MCP,
    PROJECT_ID,
    SESSION_ID,
    SIGNING_KEY,
    FakeGBrain,
    session_token,
)

FORGE = "http://forge.test"
BIFROST = "http://bifrost.test"
RUN_ID = "a1b2c3d4-0000-4000-8000-000000000123"
PROJECT = f"project:{PROJECT_ID}"
URL = "url:https://linear.app/acme/LIN-482"
_BACKEND = {"adapter": "tests.test_mimir.unit.fakes.InMemoryMimirAdapter"}
ROLE_URL = f"{FORGE}/api/v1/forge/projects/{PROJECT_ID}/role"


@pytest.fixture(autouse=True)
def _reset_caps():
    reset_cap_hits()


def _memory() -> dict:
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
    }


def _context(run_id: str | None = None) -> dict:
    return {
        "session_id": SESSION_ID,
        "launch_id": "launch-1",
        "owner_id": "ines",
        "tenant_id": "acme",
        "project_id": PROJECT_ID,
        "workflow_run_id": run_id,
        "project_memory": {"write_prefixes": ["decisions/"]},
        "model": "claude-fable-5-1",
        "objective": None,
        "project_brief": None,
        "since": None,
        "attached": [],
    }


@pytest.fixture
def forge():
    brain = FakeGBrain()
    with respx.mock(assert_all_called=False) as router:
        router.post(MCP).mock(side_effect=brain.handle)
        router.get(f"{FORGE}/api/v1/forge/sessions/{SESSION_ID}/memory-context").mock(
            return_value=httpx.Response(200, json=_context(RUN_ID))
        )
        router.get(f"{FORGE}/api/v1/forge/sessions/{SESSION_ID}").mock(
            return_value=httpx.Response(
                200, json={"id": SESSION_ID, "owner_id": "ines", "tenant_id": "acme"}
            )
        )
        router.get(f"{FORGE}/api/v1/forge/memory/runs/{RUN_ID}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "run_id": RUN_ID,
                    "owner_id": "ines",
                    "tenant_id": "acme",
                    "session_ids": [SESSION_ID],
                },
            )
        )
        router.post(f"{FORGE}/api/v1/forge/memory/ended").mock(
            return_value=httpx.Response(200, json={"sessions": {}, "runs": {}})
        )
        yield brain, router


def _client(tmp_path: Path, *, user: str = "ines", roles: list[str] | None = None) -> TestClient:
    settings = MimirServiceConfig(
        path=str(tmp_path / "mimir"),
        backend=_BACKEND,
        memory=_memory(),
        auth_mode="none",
        identity_adapter="identity.adapters.identity.AllowAllHeaderAuthenticationAdapter",
        identity_kwargs={"user_id": user, "tenant_id": "acme", "roles": roles or []},
    )
    return TestClient(create_app(settings))


def _session() -> dict[str, str]:
    return {"Authorization": f"Bearer {session_token()}"}


def _role(router, role: str | None) -> respx.Route:
    if role is None:
        return router.get(ROLE_URL).mock(return_value=httpx.Response(404, json={"detail": "no"}))
    return router.get(ROLE_URL).mock(
        return_value=httpx.Response(200, json={"project_id": PROJECT_ID, "role": role})
    )


def _tool(client, name: str, **arguments) -> dict:
    response = client.post(f"/mimir/memory/tools/{name}", headers=_session(), json=arguments)
    assert response.status_code == 200, response.text
    return response.json()["structuredContent"]


def _propose_page(client) -> str:
    result = _tool(
        client,
        "memory_write_page",
        ref="services/booking-api",
        compiled_truth="The p99 budget is 300 ms.",
        evidence=[URL],
        layer="project",
    )
    assert result["written"] is False
    return result["proposal"]["id"]


def _page(brain: FakeGBrain, slug: str) -> dict:
    return brain.pages[slug]


class TestProposals:
    def test_an_approver_lists_the_pending_proposals_of_the_project(self, tmp_path, forge) -> None:
        _, router = forge
        _role(router, "approver")
        with _client(tmp_path) as client:
            proposal_id = _propose_page(client)
            response = client.get("/mimir/memory/proposals", params={"layer": PROJECT})
        assert response.status_code == 200, response.text
        listed = response.json()["proposals"]
        assert [p["id"] for p in listed] == [proposal_id]
        assert listed[0]["status"] == "pending" and listed[0]["evidence"] == [URL]
        assert listed[0]["proposer"] == f"agent:session:{SESSION_ID}"

    @pytest.mark.parametrize(
        ("role", "status"), [("contributor", 403), ("viewer", 403), (None, 404)]
    )
    def test_others_are_refused_and_a_non_member_learns_nothing(
        self, tmp_path, forge, role, status
    ) -> None:
        _, router = forge
        _role(router, role)
        with _client(tmp_path) as client:
            proposal_id = _propose_page(client)
            listed = client.get("/mimir/memory/proposals", params={"layer": PROJECT})
            approved = client.post(
                f"/mimir/memory/proposals/{proposal_id}/approve", json={"layer": PROJECT}
            )
        assert listed.status_code == status
        assert approved.status_code == status

    def test_forge_is_asked_with_the_persons_own_login(self, tmp_path, forge) -> None:
        _, router = forge
        role = _role(router, "owner")
        with _client(tmp_path) as client:
            client.get(
                "/mimir/memory/proposals",
                params={"layer": PROJECT},
                headers={"Authorization": "Bearer person-login"},
            )
        assert role.calls.last.request.headers["authorization"] == "Bearer person-login"

    def test_a_session_credential_is_refused_on_the_people_routes(self, tmp_path, forge) -> None:
        _, router = forge
        _role(router, "owner")
        with _client(tmp_path) as client:
            response = client.get(
                "/mimir/memory/proposals", params={"layer": PROJECT}, headers=_session()
            )
        assert response.status_code == 403

    def test_approval_applies_the_change_at_operator_trust_with_its_evidence(
        self, tmp_path, forge
    ) -> None:
        brain, router = forge
        _role(router, "approver")
        with _client(tmp_path, user="marc") as client:
            proposal_id = _propose_page(client)
            response = client.post(
                f"/mimir/memory/proposals/{proposal_id}/approve", json={"layer": PROJECT}
            )
            assert response.status_code == 200, response.text
            decided = response.json()["proposal"]
            assert decided["status"] == "approved" and decided["decided_by"] == "person:marc"
            assert "wrote page services/booking-api" in decided["applied"]
            read = _tool(client, "memory_read", ref="services/booking-api")
            pending = client.get("/mimir/memory/proposals", params={"layer": PROJECT}).json()
            again = client.post(
                f"/mimir/memory/proposals/{proposal_id}/approve", json={"layer": PROJECT}
            )
        page = read["pages"][0]
        assert page["layer"] == PROJECT
        assert page["trust"] == "operator" and page["written_by"] == "operator:marc"
        assert page["evidence"] == [URL, f"proposal:{PROJECT}/{proposal_id}"]
        assert pending["proposals"] == []
        assert again.status_code == 409
        decided_page = f"l/_decided/project-{PROJECT_ID}/{proposal_id}"
        assert f"agent:session:{SESSION_ID}" in _page(brain, decided_page)["compiled_truth"]

    def test_an_approved_fact_lands_at_operator_trust(self, tmp_path, forge) -> None:
        _, router = forge
        _role(router, "owner")
        with _client(tmp_path) as client:
            proposed = _tool(
                client,
                "memory_remember",
                fact="The p99 budget is 300 ms.",
                about="services/booking-api",
                kind="fact",
                evidence=[URL],
                layer="project",
            )
            proposal_id = proposed["proposal"]["id"]
            client.post(f"/mimir/memory/proposals/{proposal_id}/approve", json={"layer": PROJECT})
            recalled = _tool(client, "memory_recall", about="services/booking-api")
        fact = recalled["facts"][0]
        assert fact["trust"] == "operator" and fact["author"] == "operator:ines"
        assert f"proposal:{PROJECT}/{proposal_id}" in fact["evidence"]

    def test_an_approval_that_cannot_apply_stays_pending(self, tmp_path, forge) -> None:
        brain, router = forge
        _role(router, "owner")
        with _client(tmp_path) as client:
            proposal_id = _propose_page(client)
            brain.seed_page(f"l/project-{PROJECT_ID}/services/booking-api", "written meanwhile")
            response = client.post(
                f"/mimir/memory/proposals/{proposal_id}/approve", json={"layer": PROJECT}
            )
            pending = client.get("/mimir/memory/proposals", params={"layer": PROJECT}).json()
        assert response.status_code == 409
        assert [p["id"] for p in pending["proposals"]] == [proposal_id]

    def test_rejection_records_the_reason(self, tmp_path, forge) -> None:
        brain, router = forge
        _role(router, "approver")
        with _client(tmp_path) as client:
            proposal_id = _propose_page(client)
            without = client.post(
                f"/mimir/memory/proposals/{proposal_id}/reject", json={"layer": PROJECT}
            )
            response = client.post(
                f"/mimir/memory/proposals/{proposal_id}/reject",
                json={"layer": PROJECT, "reason": "the budget is 250 ms"},
            )
            pending = client.get("/mimir/memory/proposals", params={"layer": PROJECT}).json()
        assert without.status_code == 422
        assert response.status_code == 200, response.text
        assert response.json()["proposal"]["decision"] == "the budget is 250 ms"
        assert pending["proposals"] == []
        assert f"l/project-{PROJECT_ID}/services/booking-api" not in brain.pages
        decided = _page(brain, f"l/_decided/project-{PROJECT_ID}/{proposal_id}")
        assert "the budget is 250 ms" in decided["compiled_truth"]

    @pytest.mark.parametrize(
        ("roles", "status"), [(["volundr:admin"], 200), (["volundr:developer"], 403)]
    )
    def test_org_proposals_are_decided_by_an_org_admin(
        self, tmp_path, forge, roles, status
    ) -> None:
        with _client(tmp_path, roles=roles) as client:
            proposed = _tool(
                client,
                "memory_write_page",
                ref="glossary/sla",
                compiled_truth="SLA: service level agreement.",
                evidence=[URL],
                layer="org",
            )
            response = client.post(
                f"/mimir/memory/proposals/{proposed['proposal']['id']}/approve",
                json={"layer": "org"},
            )
        assert response.status_code == status, response.text

    def test_proposals_are_only_kept_for_project_and_org(self, tmp_path, forge) -> None:
        with _client(tmp_path, roles=["volundr:admin"]) as client:
            response = client.get(
                "/mimir/memory/proposals", params={"layer": f"session:{SESSION_ID}"}
            )
        assert response.status_code == 422


class TestPromotions:
    def _write_finding(self, client, layer: str = "session") -> None:
        _tool(
            client,
            "memory_write_page",
            ref="findings/staging-db",
            compiled_truth="Staging is pinned to Postgres 15 by the infra chart.",
            evidence=["commit:niuulabs/infra@1f4f9622b"],
            layer=layer,
        )

    def test_the_session_owner_promotes_a_finding_and_an_owner_approves_it(
        self, tmp_path, forge
    ) -> None:
        _, router = forge
        _role(router, "owner")
        with _client(tmp_path) as client:
            self._write_finding(client)
            response = client.post(
                "/mimir/memory/promotions",
                json={
                    "from": f"session:{SESSION_ID}",
                    "refs": ["findings/staging-db"],
                    "to": PROJECT,
                },
            )
            assert response.status_code == 200, response.text
            proposal = response.json()["proposals"][0]
            assert proposal["promoted_from"] == f"session:{SESSION_ID}/findings/staging-db"
            assert proposal["proposer"] == "person:ines"
            assert proposal["trust"] == "agent"
            client.post(
                f"/mimir/memory/proposals/{proposal['id']}/approve", json={"layer": PROJECT}
            )
            page = _tool(client, "memory_read", ref="findings/staging-db", layer="project")
        written = page["pages"][0]
        assert written["trust"] == "operator"
        assert written["evidence"] == [
            "commit:niuulabs/infra@1f4f9622b",
            f"proposal:{PROJECT}/{proposal['id']}",
        ]

    def test_only_the_session_owner_promotes(self, tmp_path, forge) -> None:
        _, router = forge
        _role(router, "owner")
        with _client(tmp_path, user="marc", roles=["volundr:admin"]) as client:
            self._write_finding(client)
            response = client.post(
                "/mimir/memory/promotions",
                json={
                    "from": f"session:{SESSION_ID}",
                    "refs": ["findings/staging-db"],
                    "to": "org",
                },
            )
        assert response.status_code == 403
        assert "only the owner" in response.json()["detail"]

    def test_the_run_owner_promotes_from_the_run_layer(self, tmp_path, forge) -> None:
        _, router = forge
        _role(router, "contributor")
        with _client(tmp_path) as client:
            self._write_finding(client, layer="run")
            response = client.post(
                "/mimir/memory/promotions",
                json={"from": f"run:{RUN_ID}", "refs": ["findings/staging-db"], "to": PROJECT},
            )
        assert response.status_code == 200, response.text
        assert response.json()["proposals"][0]["proposer_session"] is None

    def test_a_project_the_person_is_not_on_is_not_found(self, tmp_path, forge) -> None:
        _, router = forge
        _role(router, None)
        with _client(tmp_path) as client:
            self._write_finding(client)
            response = client.post(
                "/mimir/memory/promotions",
                json={
                    "from": f"session:{SESSION_ID}",
                    "refs": ["findings/staging-db"],
                    "to": PROJECT,
                },
            )
        assert response.status_code == 404

    def test_a_record_that_is_not_there_creates_nothing(self, tmp_path, forge) -> None:
        brain, router = forge
        _role(router, "owner")
        with _client(tmp_path) as client:
            self._write_finding(client)
            response = client.post(
                "/mimir/memory/promotions",
                json={
                    "from": f"session:{SESSION_ID}",
                    "refs": ["findings/staging-db", "findings/missing"],
                    "to": PROJECT,
                },
            )
        assert response.status_code == 404
        assert not any(slug.startswith("l/_proposals/") and "/p-" in slug for slug in brain.pages)

    def test_promotion_refuses_a_long_lived_source(self, tmp_path, forge) -> None:
        with _client(tmp_path) as client:
            response = client.post(
                "/mimir/memory/promotions",
                json={"from": PROJECT, "refs": ["x"], "to": "org"},
            )
        assert response.status_code == 422


class TestContextPackRoute:
    def test_a_session_gets_its_pack_and_a_persons_login_is_refused(self, tmp_path, forge) -> None:
        _, router = forge
        router.get(f"{BIFROST}/models/claude-fable-5-1").mock(
            return_value=httpx.Response(
                200, json={"id": "claude-fable-5-1", "context_window": 4000}
            )
        )
        with _client(tmp_path) as client:
            pack = client.get("/mimir/memory/context-pack", headers=_session())
            person = client.get(
                "/mimir/memory/context-pack", headers={"Authorization": "Bearer not-a-session"}
            )
            anonymous = client.get("/mimir/memory/context-pack")
        assert pack.status_code == 200, pack.text
        body = pack.json()
        assert body["budget_tokens"] == 200 and body["used_tokens"] <= 200
        assert body["text"].startswith("Memory context for this session")
        assert person.status_code == 401 and anonymous.status_code == 401

    def test_a_catalog_without_a_window_is_a_502_naming_it(self, tmp_path, forge) -> None:
        _, router = forge
        router.get(f"{BIFROST}/models/claude-fable-5-1").mock(
            return_value=httpx.Response(
                200, json={"id": "claude-fable-5-1", "context_window": None}
            )
        )
        with _client(tmp_path) as client:
            response = client.get("/mimir/memory/context-pack", headers=_session())
        assert response.status_code == 502
        assert "context_window" in response.json()["detail"]


class TestStartup:
    @pytest.mark.parametrize(
        ("overrides", "match"),
        [
            ({"service_auth": None}, "memory.service_auth"),
            ({"bifrost_url": ""}, "memory.bifrost_url"),
            ({"answer_model": ""}, "memory.answer_model"),
        ],
    )
    def test_an_enabled_gateway_missing_a_part_does_not_start(
        self, tmp_path, overrides, match
    ) -> None:
        settings = MimirServiceConfig(
            path=str(tmp_path / "mimir"), backend=_BACKEND, memory={**_memory(), **overrides}
        )
        with pytest.raises(ValueError, match=match):
            create_app(settings)

    def test_ask_can_be_turned_off(self, tmp_path, forge) -> None:
        settings = MimirServiceConfig(
            path=str(tmp_path / "mimir"),
            backend=_BACKEND,
            memory={**_memory(), "answer_model": "", "ask_enabled": False},
            auth_mode="none",
            identity_adapter="identity.adapters.identity.AllowAllHeaderAuthenticationAdapter",
        )
        with TestClient(create_app(settings)) as client:
            tools = client.get("/mimir/memory/tools", headers=_session()).json()["tools"]
        assert "memory_ask" not in [t["name"] for t in tools]
