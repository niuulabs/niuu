"""Tests for flock flows REST API — full CRUD + validation failures."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tyr.adapters.flows.config import ConfigFlockFlowProvider
from tyr.api.flock_flows import (
    create_flock_flows_router,
    resolve_flow_provider,
    resolve_persona_names,
)
from tyr.domain.flock_flow import FlockFlowConfig, FlockPersonaOverride
from tyr.ports.flock_flow import FlockFlowProvider


def _auth_headers(user_id: str = "test-user") -> dict[str, str]:
    return {"x-auth-user-id": user_id}


def _make_app(
    provider: FlockFlowProvider | None = None,
    known_personas: set[str] | None = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(create_flock_flows_router())

    p = provider or ConfigFlockFlowProvider()

    async def _resolve_provider() -> FlockFlowProvider:
        return p

    async def _resolve_personas() -> set[str]:
        return known_personas or set()

    app.dependency_overrides[resolve_flow_provider] = _resolve_provider
    app.dependency_overrides[resolve_persona_names] = _resolve_personas
    return app


def _flow_body(
    name: str = "test-flow",
    personas: list[dict] | None = None,
) -> dict:
    return {
        "name": name,
        "description": "A test flow",
        "personas": personas or [{"name": "coordinator"}, {"name": "reviewer"}],
        "mesh_transport": "nng",
        "max_concurrent_tasks": 3,
    }


class TestListFlows:
    def test_empty_list(self) -> None:
        client = TestClient(_make_app())
        resp = client.get("/api/v1/tyr/flock_flows", headers=_auth_headers())
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_with_flows(self) -> None:
        provider = ConfigFlockFlowProvider()
        provider.save(FlockFlowConfig(name="flow-a"))
        provider.save(FlockFlowConfig(name="flow-b"))

        client = TestClient(_make_app(provider=provider))
        resp = client.get("/api/v1/tyr/flock_flows", headers=_auth_headers())
        assert resp.status_code == 200
        names = {f["name"] for f in resp.json()}
        assert names == {"flow-a", "flow-b"}


class TestGetFlow:
    def test_get_existing(self) -> None:
        provider = ConfigFlockFlowProvider()
        provider.save(
            FlockFlowConfig(
                name="my-flow",
                description="Test",
                personas=[FlockPersonaOverride(name="coordinator")],
            )
        )

        client = TestClient(_make_app(provider=provider))
        resp = client.get("/api/v1/tyr/flock_flows/my-flow", headers=_auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "my-flow"
        assert data["description"] == "Test"
        assert len(data["personas"]) == 1

    def test_get_nonexistent(self) -> None:
        client = TestClient(_make_app())
        resp = client.get("/api/v1/tyr/flock_flows/nonexistent", headers=_auth_headers())
        assert resp.status_code == 404


class TestCreateFlow:
    def test_create_success(self) -> None:
        client = TestClient(_make_app())
        resp = client.post(
            "/api/v1/tyr/flock_flows",
            json=_flow_body(),
            headers=_auth_headers(),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "test-flow"
        assert len(data["personas"]) == 2

    def test_create_duplicate_returns_409(self) -> None:
        provider = ConfigFlockFlowProvider()
        provider.save(FlockFlowConfig(name="existing"))

        client = TestClient(_make_app(provider=provider))
        resp = client.post(
            "/api/v1/tyr/flock_flows",
            json=_flow_body("existing"),
            headers=_auth_headers(),
        )
        assert resp.status_code == 409

    def test_create_with_unknown_persona_returns_422(self) -> None:
        client = TestClient(_make_app(known_personas={"coordinator", "reviewer"}))
        resp = client.post(
            "/api/v1/tyr/flock_flows",
            json=_flow_body(personas=[{"name": "unknown-persona"}]),
            headers=_auth_headers(),
        )
        assert resp.status_code == 422
        assert "unknown-persona" in resp.json()["detail"]

    def test_create_skips_validation_when_no_persona_source(self) -> None:
        client = TestClient(_make_app(known_personas=set()))
        resp = client.post(
            "/api/v1/tyr/flock_flows",
            json=_flow_body(personas=[{"name": "any-persona"}]),
            headers=_auth_headers(),
        )
        assert resp.status_code == 201

    def test_create_empty_name_returns_422(self) -> None:
        client = TestClient(_make_app())
        body = _flow_body()
        body["name"] = ""
        resp = client.post(
            "/api/v1/tyr/flock_flows",
            json=body,
            headers=_auth_headers(),
        )
        assert resp.status_code == 422


class TestUpdateFlow:
    def test_update_success(self) -> None:
        provider = ConfigFlockFlowProvider()
        provider.save(FlockFlowConfig(name="update-me"))

        client = TestClient(_make_app(provider=provider))
        resp = client.put(
            "/api/v1/tyr/flock_flows/update-me",
            json=_flow_body("update-me"),
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        assert resp.json()["description"] == "A test flow"

    def test_update_nonexistent_returns_404(self) -> None:
        client = TestClient(_make_app())
        resp = client.put(
            "/api/v1/tyr/flock_flows/nonexistent",
            json=_flow_body("nonexistent"),
            headers=_auth_headers(),
        )
        assert resp.status_code == 404

    def test_update_with_unknown_persona_returns_422(self) -> None:
        provider = ConfigFlockFlowProvider()
        provider.save(FlockFlowConfig(name="update-me"))

        client = TestClient(_make_app(provider=provider, known_personas={"coordinator"}))
        resp = client.put(
            "/api/v1/tyr/flock_flows/update-me",
            json=_flow_body("update-me", personas=[{"name": "bad-persona"}]),
            headers=_auth_headers(),
        )
        assert resp.status_code == 422


class TestDeleteFlow:
    def test_delete_success(self) -> None:
        provider = ConfigFlockFlowProvider()
        provider.save(FlockFlowConfig(name="delete-me"))

        client = TestClient(_make_app(provider=provider))
        resp = client.delete(
            "/api/v1/tyr/flock_flows/delete-me",
            headers=_auth_headers(),
        )
        assert resp.status_code == 204

    def test_delete_nonexistent_returns_404(self) -> None:
        client = TestClient(_make_app())
        resp = client.delete(
            "/api/v1/tyr/flock_flows/nonexistent",
            headers=_auth_headers(),
        )
        assert resp.status_code == 404


class TestFullCRUDCycle:
    def test_create_read_update_delete(self) -> None:
        client = TestClient(_make_app())
        headers = _auth_headers()

        # Create
        resp = client.post(
            "/api/v1/tyr/flock_flows",
            json=_flow_body("lifecycle"),
            headers=headers,
        )
        assert resp.status_code == 201

        # Read
        resp = client.get("/api/v1/tyr/flock_flows/lifecycle", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["name"] == "lifecycle"

        # Update
        updated_body = _flow_body("lifecycle")
        updated_body["description"] = "Updated description"
        resp = client.put(
            "/api/v1/tyr/flock_flows/lifecycle",
            json=updated_body,
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["description"] == "Updated description"

        # Delete
        resp = client.delete("/api/v1/tyr/flock_flows/lifecycle", headers=headers)
        assert resp.status_code == 204

        # Verify gone
        resp = client.get("/api/v1/tyr/flock_flows/lifecycle", headers=headers)
        assert resp.status_code == 404
