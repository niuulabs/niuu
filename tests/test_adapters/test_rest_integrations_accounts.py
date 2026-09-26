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


def test_managed_oauth_status_distinguishes_reconnect_from_vault_failure(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from niuu.domain.oauth_credentials import OAUTH_ENGINE, OAuthCredentialUnavailableError

    client, store = _client(tmp_path)
    assert _connect(client, "gitlab", "initial").status_code == 201
    metadata = {
        "renewal_owner": OAUTH_ENGINE,
        "auth_state": "active",
        # An enrollment's old expiry is not the current engine token expiry.
        "auth_expires_at": "2000-01-01T00:00:00Z",
    }
    monkeypatch.setattr(store, "get", AsyncMock(return_value=SimpleNamespace(metadata=metadata)))
    value = AsyncMock(
        return_value={"token": "private-access", "expires_at": "2099-01-01T00:00:00Z"}
    )
    monkeypatch.setattr(store, "get_value", value)
    response = client.get("/api/v1/integrations")
    assert response.json()[0]["credential_status"] == "active"
    assert "private-access" not in response.text
    for reconnect, expected in [(True, "auth_required"), (False, "unavailable")]:
        value.side_effect = OAuthCredentialUnavailableError(reconnect=reconnect)
        response = client.get("/api/v1/integrations")
        assert response.status_code == 200
        assert response.json()[0]["credential_status"] == expected


def test_credential_identity_comes_from_authenticated_context(tmp_path):
    import asyncio

    client, store = _client(tmp_path)
    response = client.post(
        "/api/v1/integrations",
        json={
            "slug": "github",
            "config": {},
            "credential": {
                "name": "github",
                "data": {"token": "test"},
                "metadata": {
                    "tenant_id": "forged",
                    "integration": "forged",
                    "oauth_app": "forged",
                },
            },
        },
    )
    assert response.status_code == 201
    stored = asyncio.run(store.get("user", "owner-1", "github"))
    assert stored.metadata["tenant_id"] == "t"
    assert stored.metadata["integration"] == "github"
    assert stored.metadata["oauth_app"] == "default"


def test_replace_static_credential_keeps_connection_and_other_accounts(tmp_path):
    import asyncio

    client, store = _client(tmp_path)
    connection = _connect(client, "github-work", "old-work").json()
    _connect(client, "github-personal", "personal")
    response = client.put(
        f"/api/v1/integrations/{connection['id']}",
        json={"credential": {"token": "new-work"}},
    )
    assert response.status_code == 200
    assert response.json()["id"] == connection["id"]
    assert "new-work" not in response.text
    assert len(client.get("/api/v1/integrations").json()) == 2
    assert asyncio.run(store.get_value("user", "owner-1", "github-work"))["token"] == "new-work"
    assert asyncio.run(store.get_value("user", "owner-1", "github-personal"))["token"] == "personal"


def test_replace_credential_validates_and_enforces_owner(tmp_path):
    import asyncio

    client, store = _client(tmp_path)
    connection = _connect(client, "github-work", "old-work").json()
    endpoint = f"/api/v1/integrations/{connection['id']}"
    assert client.put(endpoint, json={"credential": {"token": ""}}).status_code == 422
    assert (
        client.put(endpoint, json={"credential": {}, "credential_name": "other"}).status_code == 422
    )
    client.app.dependency_overrides[extract_principal] = lambda: Principal(
        user_id="other", tenant_id="t", email="other@example.test", roles=["volundr:developer"]
    )
    assert client.put(endpoint, json={"credential": {"token": "stolen"}}).status_code == 404
    assert asyncio.run(store.get_value("user", "owner-1", "github-work"))["token"] == "old-work"


def test_mcp_connection_test_uses_internal_host_allowlist(tmp_path, monkeypatch) -> None:
    import asyncio
    from datetime import UTC, datetime

    from volundr.adapters.outbound.mcp_oauth import MCPOAuthDiscovery
    from volundr.domain.models import IntegrationConnection, IntegrationType, SecretType

    url = "https://tools.asgard.niuu.world/mcp"
    store = FileCredentialStore(base_dir=str(tmp_path))
    repository = InMemoryIntegrationRepository()
    now = datetime.now(UTC)
    asyncio.run(
        repository.save_connection(
            IntegrationConnection(
                id="mcp-1",
                owner_id="owner-1",
                integration_type=IntegrationType.MCP,
                adapter="",
                credential_name="mcp-mcp-1",
                config={"mcp_url": url, "name": "Tools", "tenant_id": "t"},
                enabled=True,
                created_at=now,
                updated_at=now,
                slug="mcp",
            )
        )
    )
    asyncio.run(
        store.store(
            "user",
            "owner-1",
            "mcp-mcp-1",
            SecretType.OAUTH_TOKEN,
            {"access_token": "test-access"},
            {"mcp_url": url, "tenant_id": "t"},
        )
    )
    initialized = []

    async def initialize(self, server_url, headers):
        initialized.append((server_url, self._internal_hosts, headers))

    monkeypatch.setattr(MCPOAuthDiscovery, "initialize", initialize)
    integrations = IntegrationRegistry(
        definitions_from_config([d.model_dump() for d in _default_integration_definitions()])
    )
    app = FastAPI()
    app.include_router(
        create_integrations_router(
            repository,
            TrackerFactory(store),
            registry=integrations,
            credential_store=store,
            mcp_internal_hosts=["*.asgard.niuu.world"],
        )
    )
    app.dependency_overrides[extract_principal] = lambda: Principal(
        user_id="owner-1", tenant_id="t", email="o@example.test", roles=["volundr:developer"]
    )

    result = TestClient(app).post("/api/v1/integrations/mcp-1/test").json()

    assert result["success"] is True, result
    assert initialized == [(url, ("*.asgard.niuu.world",), {"Authorization": "Bearer test-access"})]
