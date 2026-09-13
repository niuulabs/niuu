"""Several accounts of one provider are several connections, each with its own credential name."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from volundr.adapters.inbound.auth import extract_principal
from volundr.adapters.inbound.rest_integrations import create_integrations_router
from volundr.adapters.outbound.file_credential_store import FileCredentialStore
from volundr.adapters.outbound.memory_integrations import InMemoryIntegrationRepository
from volundr.config import _default_integration_definitions
from volundr.domain.models import Principal
from volundr.domain.services.integration_registry import (
    IntegrationRegistry,
    definitions_from_config,
)
from volundr.domain.services.tracker_factory import TrackerFactory


def _client(tmp_path) -> tuple[TestClient, FileCredentialStore]:
    store = FileCredentialStore(base_dir=str(tmp_path))
    integrations = IntegrationRegistry(
        definitions_from_config([d.model_dump() for d in _default_integration_definitions()])
    )
    app = FastAPI()
    app.include_router(
        create_integrations_router(
            InMemoryIntegrationRepository(),
            TrackerFactory(store),
            registry=integrations,
            credential_store=store,
        )
    )
    app.dependency_overrides[extract_principal] = lambda: Principal(
        user_id="owner-1", tenant_id="t", email="o@example.test", roles=["volundr:developer"]
    )
    return TestClient(app), store


def _connect(client: TestClient, name: str, token: str):
    return client.post(
        "/api/v1/integrations",
        json={
            "slug": "github",
            "config": {},
            "credential": {"name": name, "data": {"token": token}},
        },
    )


def test_a_second_account_is_a_second_connection(tmp_path) -> None:
    client, _ = _client(tmp_path)

    assert _connect(client, "github-setup", "ghp_personal").status_code == 201
    assert _connect(client, "github-work", "ghp_work").status_code == 201

    rows = client.get("/api/v1/integrations").json()
    assert sorted(row["credential_name"] for row in rows) == ["github-setup", "github-work"]
    assert {row["slug"] for row in rows} == {"github"}


def test_reusing_an_accounts_name_is_refused_not_overwritten(tmp_path) -> None:
    client, _ = _client(tmp_path)
    assert _connect(client, "github-setup", "ghp_personal").status_code == 201

    response = _connect(client, "github-setup", "ghp_other")

    assert response.status_code == 409
    assert "already connected as 'github-setup'" in response.json()["detail"]
    rows = client.get("/api/v1/integrations").json()
    assert len(rows) == 1
