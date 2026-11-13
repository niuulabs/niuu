"""The people routes (contract C9): proposals, their approval and rejection, promotions.

The app runs in mini mode without auth, so Mímir's identity adapter is the
allow-all adapter, configured per test as the person calling. Forge (C1, C5, C6
and GET /sessions/{id}) and the org's gbrain are faked over respx.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from mimir.app import create_app
from mimir.config import MimirServiceConfig
from niuu.cap_telemetry import cap_hit_counts, degraded_counts, reset_cap_hits
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
NOTIFY_URL = f"{FORGE}/api/v1/forge/sessions/{SESSION_ID}/memory/proposals"
DECIDED_URL = rf"{FORGE}/api/v1/forge/projects/{PROJECT_ID}/memory/proposals/p-[0-9a-f]+/decided"


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
        router.post(NOTIFY_URL, name="notify").mock(return_value=httpx.Response(201, json={}))
        router.post(url__regex=DECIDED_URL, name="decided").mock(
            return_value=httpx.Response(200, json={})
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
        with _client(tmp_path) as client:
            pack = client.get("/mimir/memory/context-pack", headers=_session())
            person = client.get(
                "/mimir/memory/context-pack", headers={"Authorization": "Bearer not-a-session"}
            )
            anonymous = client.get("/mimir/memory/context-pack")
        assert pack.status_code == 200, pack.text
        body = pack.json()
        assert set(body) == {"text"}
        assert body["text"].startswith("Memory context for this session")
        assert person.status_code == 401 and anonymous.status_code == 401


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


PROJECTS_URL = f"{FORGE}/api/v1/forge/projects"
MOUNTS_URL = f"{FORGE}/api/v1/forge/projects/{PROJECT_ID}/memory"


def _projects(router, *, mounts: list[dict] | None = None) -> None:
    router.get(PROJECTS_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": PROJECT_ID, "name": "Booking platform", "status": "active"},
            ],
        )
    )
    router.get(MOUNTS_URL).mock(return_value=httpx.Response(200, json=mounts or []))


def _bifrost(router, answer: dict) -> respx.Route:
    return router.post(f"{BIFROST}/v1/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": json.dumps(answer)}],
                "stop_reason": "end_turn",
            },
        )
    )


def _write_project_page(client) -> None:
    result = _tool(
        client,
        "memory_write_page",
        ref="decisions/booking-cutover",
        title="Booking cut-over",
        compiled_truth="The cut-over is the weekend of 12 December. Ines Moreau decides.",
        evidence=[URL],
        layer="project",
    )
    assert result["written"] is True, result


class TestPersonAsk:
    def test_a_person_asks_over_their_projects_and_the_org_charged_to_them(
        self, tmp_path, forge
    ) -> None:
        _, router = forge
        _role(router, "contributor")
        _projects(router)
        call = _bifrost(
            router,
            {
                "answer": "The cut-over is the weekend of 12 December. Ines Moreau decides.",
                "citations": [{"layer": PROJECT, "ref": "decisions/booking-cutover"}],
                "gaps": ["Nobody has confirmed the date yet."],
            },
        )
        with _client(tmp_path) as client:
            _write_project_page(client)
            response = client.post(
                "/mimir/memory/ask",
                json={"question": "When is the booking cut-over?"},
                headers={"Authorization": "Bearer person-login"},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["answer"].startswith("The cut-over is the weekend of 12 December.")
        assert body["gaps"] == ["Nobody has confirmed the date yet."]
        assert body["sources"] == [
            {
                "layer": PROJECT,
                "layer_name": "Booking platform",
                "ref": "decisions/booking-cutover",
                "kind": "page",
                "title": "Booking cut-over",
                "url": None,
            }
        ]
        sent = call.calls.last.request
        assert sent.headers["authorization"] == "Bearer person-login"
        assert sent.headers["x-agent-id"] == "ines" and sent.headers["x-tenant-id"] == "acme"

    def test_a_project_ask_reads_that_project_and_its_attached_memories(
        self, tmp_path, forge
    ) -> None:
        _, router = forge
        role = _role(router, "viewer")
        _projects(router)
        mounts = router.get(MOUNTS_URL).mock(return_value=httpx.Response(200, json=[]))
        _bifrost(router, {"answer": "", "citations": [], "gaps": ["Not known."]})
        with _client(tmp_path) as client:
            _write_project_page(client)
            response = client.post(
                "/mimir/memory/ask",
                json={"question": "booking cut-over", "project_id": PROJECT_ID},
            )
        assert response.status_code == 200, response.text
        assert role.called and mounts.called
        assert response.json()["sources"] == []

    def test_a_project_the_person_is_not_on_is_not_found(self, tmp_path, forge) -> None:
        _, router = forge
        _role(router, None)
        _projects(router)
        with _client(tmp_path) as client:
            response = client.post(
                "/mimir/memory/ask", json={"question": "x", "project_id": PROJECT_ID}
            )
        assert response.status_code == 404

    def test_nothing_found_makes_no_model_call(self, tmp_path, forge) -> None:
        _, router = forge
        _projects(router)
        call = router.post(f"{BIFROST}/v1/messages")
        with _client(tmp_path) as client:
            response = client.post("/mimir/memory/ask", json={"question": "zebra"})
        assert response.status_code == 200, response.text
        assert response.json()["answer"] == ""
        assert "Nothing in your layers" in response.json()["gaps"][0]
        assert not call.called

    @pytest.mark.parametrize(
        ("body", "status"), [({}, 422), ({"question": "x", "project_id": 3}, 422)]
    )
    def test_a_malformed_question_is_refused(self, tmp_path, forge, body, status) -> None:
        with _client(tmp_path) as client:
            response = client.post("/mimir/memory/ask", json=body)
        assert response.status_code == status

    def test_a_session_credential_is_refused(self, tmp_path, forge) -> None:
        with _client(tmp_path) as client:
            response = client.post("/mimir/memory/ask", json={"question": "x"}, headers=_session())
        assert response.status_code == 403

    def test_a_failed_answer_call_is_a_bad_gateway(self, tmp_path, forge) -> None:
        _, router = forge
        _role(router, "owner")
        _projects(router)
        router.post(f"{BIFROST}/v1/messages").mock(return_value=httpx.Response(500, text="down"))
        with _client(tmp_path) as client:
            _write_project_page(client)
            response = client.post("/mimir/memory/ask", json={"question": "booking cut-over"})
        assert response.status_code == 502
        assert "down" in response.json()["detail"]


class TestReview:
    def _propose(self, client, why: str = "The payments freeze moved a week.") -> str:
        result = _tool(
            client,
            "memory_propose",
            layer="project",
            change={
                "action": "write_page",
                "ref": "services/booking-cutover",
                "title": "The booking cut-over date",
                "compiled_truth": "The cut-over is the weekend of 12 December.",
            },
            evidence=[URL],
            why=why,
        )
        return result["proposal"]["id"]

    def test_an_approver_reviews_what_it_says_now_and_would_say(self, tmp_path, forge) -> None:
        _, router = forge
        _role(router, "approver")
        _projects(router)
        with _client(tmp_path) as client:
            # What the project says now: an earlier proposal, approved.
            first = _propose_page(client)
            client.post(f"/mimir/memory/proposals/{first}/approve", json={"layer": PROJECT})
            write = _tool(
                client,
                "memory_write_page",
                ref="services/booking-api",
                title="Booking API budget",
                compiled_truth="The p99 budget is 250 ms.",
                evidence=[URL],
                layer="project",
                why="The SLO review lowered it, says LIN-482.",
            )
            response = client.get("/mimir/memory/review")
        assert write["written"] is False, write
        assert response.status_code == 200, response.text
        [item] = response.json()["proposals"]
        assert item["layer"] == PROJECT and item["layer_name"] == "Booking platform"
        assert item["title"] == "Booking API budget"
        assert item["now"] == "The p99 budget is 300 ms."
        assert item["would"] == "The p99 budget is 250 ms."
        assert item["why"] == "The SLO review lowered it, says LIN-482."
        assert item["by"] == "agent"
        assert item["sources"] == [
            {
                "ref": URL,
                "kind": "url",
                "title": None,
                "url": "https://linear.app/acme/LIN-482",
                "kept": True,
            }
        ]
        assert response.json()["more"] == []

    def test_a_cited_source_is_resolved_to_its_title_and_origin(self, tmp_path, forge) -> None:
        _, router = forge
        _role(router, "owner")
        _projects(router)
        with _client(tmp_path) as client:
            source = _tool(
                client,
                "memory_add_source",
                title="LIN-482 moved",
                content="The freeze moved a week.",
                kind="ticket",
                url="https://linear.app/acme/LIN-482",
            )
            result = _tool(
                client,
                "memory_propose",
                layer="project",
                change={"action": "remember", "fact": "x", "about": "y", "kind": "fact"},
                evidence=[f"source:{source['source_id']}", "tool_call:abc"],
                why="It was decided.",
            )
            response = client.get("/mimir/memory/review", params={"layer": PROJECT})
        [item] = response.json()["proposals"]
        assert item["id"] == result["proposal"]["id"]
        assert item["now"] is None and item["would"] == "x" and item["title"] == "Y"
        assert item["sources"][0]["title"] == "LIN-482 moved"
        assert item["sources"][0]["url"] == "https://linear.app/acme/LIN-482"
        assert item["sources"][1] == {
            "ref": "tool_call:abc",
            "kind": "tool_call",
            "title": None,
            "url": None,
            "kept": True,
        }

    def test_only_the_layers_the_person_decides_are_reviewed(self, tmp_path, forge) -> None:
        _, router = forge
        _role(router, "contributor")
        _projects(router)
        with _client(tmp_path) as client:
            self._propose(client)
            response = client.get("/mimir/memory/review")
            one = client.get("/mimir/memory/review", params={"layer": PROJECT})
        assert response.status_code == 200 and response.json()["proposals"] == []
        assert one.status_code == 403

    def test_an_org_admin_reviews_the_org(self, tmp_path, forge) -> None:
        _, router = forge
        _role(router, "viewer")
        _projects(router)
        with _client(tmp_path, roles=["volundr:admin"]) as client:
            _tool(
                client,
                "memory_propose",
                layer="org",
                change={"action": "remember", "fact": "x", "about": "y", "kind": "fact"},
                evidence=[URL],
                why="w",
            )
            response = client.get("/mimir/memory/review")
        [item] = response.json()["proposals"]
        assert item["layer"] == "org" and item["layer_name"] == "the org"

    def test_a_full_layer_is_named_and_counted(self, tmp_path, forge) -> None:
        _, router = forge
        _role(router, "owner")
        _projects(router)
        with _client(tmp_path) as client:
            self._propose(client)
            self._propose(client)
            response = client.get("/mimir/memory/review", params={"limit": 1})
            bad = client.get("/mimir/memory/review", params={"limit": 0})
        assert response.json()["more"] == [PROJECT]
        assert len(response.json()["proposals"]) == 1
        assert cap_hit_counts()["memory.default_result_count"] >= 1
        assert bad.status_code == 422


class TestProposalNotifications:
    def test_a_project_proposal_tells_its_deciders_through_forge(self, tmp_path, forge) -> None:
        _, router = forge
        with _client(tmp_path) as client:
            result = _tool(
                client,
                "memory_propose",
                layer="project",
                change={"action": "remember", "fact": "x", "about": "booking-api", "kind": "fact"},
                evidence=[URL],
                why="The budget changed.",
            )
        assert result["notified"] is True
        sent = router.routes["notify"].calls.last.request
        assert sent.headers["authorization"] == f"Bearer {session_token()}"
        assert json.loads(sent.content) == {
            "proposal_id": result["proposal"]["id"],
            "project_id": PROJECT_ID,
            "title": "Booking api",
            "why": "The budget changed.",
        }

    def test_an_org_proposal_is_not_announced(self, tmp_path, forge) -> None:
        _, router = forge
        with _client(tmp_path) as client:
            result = _tool(
                client,
                "memory_propose",
                layer="org",
                change={"action": "remember", "fact": "x", "about": "y", "kind": "fact"},
                evidence=[URL],
                why="w",
            )
        assert "notified" not in result
        assert not router.routes["notify"].called

    def test_a_failed_announcement_keeps_the_proposal_and_is_degraded(
        self, tmp_path, forge
    ) -> None:
        _, router = forge
        router.routes["notify"].mock(return_value=httpx.Response(503, text="feed down"))
        _role(router, "owner")
        with _client(tmp_path) as client:
            response = client.post(
                "/mimir/memory/tools/memory_propose",
                headers=_session(),
                json={
                    "layer": "project",
                    "change": {"action": "remember", "fact": "x", "about": "y", "kind": "fact"},
                    "evidence": [URL],
                    "why": "w",
                },
            )
            pending = client.get("/mimir/memory/proposals", params={"layer": PROJECT}).json()
        result = response.json()
        assert result["isError"] is False
        assert result["structuredContent"]["notified"] is False
        assert "approvers were not told" in result["content"][0]["text"]
        assert degraded_counts()["memory.proposal_notify"] == 1
        assert len(pending["proposals"]) == 1

    @pytest.mark.parametrize(("verb", "outcome"), [("approve", "kept"), ("reject", "discarded")])
    def test_deciding_settles_the_notifications(self, tmp_path, forge, verb, outcome) -> None:
        _, router = forge
        _role(router, "owner")
        with _client(tmp_path) as client:
            proposal_id = _propose_page(client)
            response = client.post(
                f"/mimir/memory/proposals/{proposal_id}/{verb}",
                json={"layer": PROJECT, "reason": "Discarded on the Review page"},
                headers={"Authorization": "Bearer person-login"},
            )
        assert response.status_code == 200, response.text
        assert "notice" not in response.json()
        sent = router.routes["decided"].calls.last.request
        assert sent.url.path.endswith(f"/{proposal_id}/decided")
        assert sent.headers["authorization"] == "Bearer person-login"
        assert json.loads(sent.content) == {"outcome": outcome}

    def test_a_failed_settlement_keeps_the_decision_and_is_degraded(self, tmp_path, forge) -> None:
        _, router = forge
        router.routes["decided"].mock(return_value=httpx.Response(500, text="no"))
        _role(router, "owner")
        with _client(tmp_path) as client:
            proposal_id = _propose_page(client)
            response = client.post(
                f"/mimir/memory/proposals/{proposal_id}/approve", json={"layer": PROJECT}
            )
        assert response.status_code == 200, response.text
        assert response.json()["proposal"]["status"] == "approved"
        assert "were not settled" in response.json()["notice"]
        assert degraded_counts()["memory.proposal_notify"] == 1
