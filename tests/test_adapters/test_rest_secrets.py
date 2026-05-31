"""Tests for the REST adapter for MCP servers and secrets."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from volundr.adapters.inbound.rest_secrets import (
    MCPServerResponse,
    SecretResponse,
    _build_secrets_router,
    create_secrets_router,
)
from volundr.adapters.outbound.config_mcp_servers import ConfigMCPServerProvider
from volundr.adapters.outbound.memory_secrets import InMemorySecretManager
from volundr.config import MCPServerEntry
from volundr.domain.models import MCPServerConfig, SecretInfo


@pytest.fixture
def sample_mcp_entries() -> list[MCPServerEntry]:
    return [
        MCPServerEntry(
            name="linear",
            type="stdio",
            command="npx",
            args=["-y", "@linear/mcp-server"],
            description="Linear issue tracking",
        ),
        MCPServerEntry(
            name="filesystem",
            type="stdio",
            command="mcp-fs",
            description="Local filesystem access",
        ),
    ]


@pytest.fixture
def sample_secrets() -> list[SecretInfo]:
    return [
        SecretInfo(name="github-token", keys=["GITHUB_TOKEN"]),
        SecretInfo(name="anthropic-api-key", keys=["ANTHROPIC_API_KEY"]),
    ]


@pytest.fixture
def mcp_provider(sample_mcp_entries) -> ConfigMCPServerProvider:
    return ConfigMCPServerProvider(sample_mcp_entries)


@pytest.fixture
def secret_manager(sample_secrets) -> InMemorySecretManager:
    return InMemorySecretManager(sample_secrets)


@pytest.fixture
def app(mcp_provider, secret_manager) -> FastAPI:
    app = FastAPI()
    app.include_router(create_secrets_router(mcp_provider, secret_manager))
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture
def deprecated_generic_client(mcp_provider, secret_manager) -> TestClient:
    app = FastAPI()
    app.include_router(
        _build_secrets_router(
            mcp_provider,
            secret_manager,
            prefix="/api/v1/legacy-credentials",
            deprecated=True,
            canonical_prefix="/api/v1/credentials",
        )
    )
    return TestClient(app)


class TestMCPServerResponse:
    def test_from_config(self):
        cfg = MCPServerConfig(
            name="linear",
            type="stdio",
            command="npx",
            args=["-y", "@linear/mcp-server"],
            description="Linear",
        )
        response = MCPServerResponse.from_config(cfg)

        assert response.name == "linear"
        assert response.type == "stdio"
        assert response.command == "npx"
        assert response.args == ["-y", "@linear/mcp-server"]
        assert response.description == "Linear"


class TestSecretResponse:
    def test_from_info(self):
        info = SecretInfo(name="my-secret", keys=["KEY1", "KEY2"])
        response = SecretResponse.from_info(info)

        assert response.name == "my-secret"
        assert response.keys == ["KEY1", "KEY2"]


class TestListMCPServers:
    def test_list_mcp_servers(self, client: TestClient):
        response = client.get("/api/v1/credentials/mcp-servers")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        names = {s["name"] for s in data}
        assert names == {"linear", "filesystem"}

    def test_list_mcp_servers_empty(self):
        app = FastAPI()
        provider = ConfigMCPServerProvider([])
        manager = InMemorySecretManager()
        app.include_router(create_secrets_router(provider, manager))
        client = TestClient(app)

        response = client.get("/api/v1/credentials/mcp-servers")
        assert response.status_code == 200
        assert response.json() == []


class TestGetMCPServer:
    def test_get_mcp_server_success(self, client: TestClient):
        response = client.get("/api/v1/credentials/mcp-servers/linear")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "linear"
        assert data["type"] == "stdio"
        assert data["command"] == "npx"
        assert data["args"] == ["-y", "@linear/mcp-server"]

    def test_get_mcp_server_not_found(self, client: TestClient):
        response = client.get("/api/v1/credentials/mcp-servers/nonexistent")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_deprecated_shared_router_mcp_server_not_found(
        self,
        deprecated_generic_client: TestClient,
    ):
        response = deprecated_generic_client.get(
            "/api/v1/legacy-credentials/mcp-servers/nonexistent"
        )
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestListSecrets:
    def test_list_secrets(self, client: TestClient):
        response = client.get("/api/v1/credentials/secrets")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert {item["name"] for item in data} == {"github-token", "anthropic-api-key"}
        github = next(s for s in data if s["name"] == "github-token")
        assert github["keys"] == ["GITHUB_TOKEN"]

    def test_deprecated_shared_router_list_secrets_warns(
        self,
        deprecated_generic_client: TestClient,
    ):
        response = deprecated_generic_client.get("/api/v1/legacy-credentials/secrets")
        assert response.status_code == 200
        assert response.headers["Deprecation"] == "true"
        assert response.headers["X-Niuu-Canonical-Route"] == "/api/v1/credentials/secrets"
        names = {item["name"] for item in response.json()}
        assert names == {"github-token", "anthropic-api-key"}

    def test_list_secrets_empty(self):
        app = FastAPI()
        provider = ConfigMCPServerProvider([])
        manager = InMemorySecretManager()
        app.include_router(create_secrets_router(provider, manager))
        client = TestClient(app)

        response = client.get("/api/v1/credentials/secrets")
        assert response.status_code == 200
        assert response.json() == []


class TestGetSecret:
    def test_get_secret_success(self, client: TestClient):
        response = client.get("/api/v1/credentials/secrets/github-token")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "github-token"
        assert data["keys"] == ["GITHUB_TOKEN"]

    def test_get_secret_not_found(self, client: TestClient):
        response = client.get("/api/v1/credentials/secrets/nonexistent")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_deprecated_shared_router_secret_not_found(
        self,
        deprecated_generic_client: TestClient,
    ):
        response = deprecated_generic_client.get("/api/v1/legacy-credentials/secrets/nonexistent")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestCreateSecret:
    def test_create_secret_success(self, client: TestClient):
        response = client.post(
            "/api/v1/credentials/secrets",
            json={"name": "my-api-key", "data": {"API_KEY": "sk-test"}},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "my-api-key"
        assert data["keys"] == ["API_KEY"]

    def test_create_secret_multiple_keys(self, client: TestClient):
        response = client.post(
            "/api/v1/credentials/secrets",
            json={
                "name": "multi-key",
                "data": {"KEY1": "val1", "KEY2": "val2"},
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "multi-key"
        assert sorted(data["keys"]) == ["KEY1", "KEY2"]

    def test_create_secret_does_not_return_values(self, client: TestClient):
        response = client.post(
            "/api/v1/credentials/secrets",
            json={"name": "no-leak", "data": {"SECRET": "super-secret-value"}},
        )
        assert response.status_code == 201
        data = response.json()
        assert "data" not in data
        assert "super-secret-value" not in str(data)

    def test_create_secret_conflict(self, client: TestClient):
        response = client.post(
            "/api/v1/credentials/secrets",
            json={"name": "github-token", "data": {"KEY": "val"}},
        )
        assert response.status_code == 409
        assert "already exists" in response.json()["detail"].lower()

    def test_create_secret_invalid_name(self, client: TestClient):
        response = client.post(
            "/api/v1/credentials/secrets",
            json={"name": "INVALID_NAME!", "data": {"KEY": "val"}},
        )
        assert response.status_code == 400

    def test_create_secret_empty_data(self, client: TestClient):
        response = client.post(
            "/api/v1/credentials/secrets",
            json={"name": "empty-data", "data": {}},
        )
        assert response.status_code == 422

    def test_create_secret_visible_in_list(self, client: TestClient):
        client.post(
            "/api/v1/credentials/secrets",
            json={"name": "new-secret", "data": {"KEY": "val"}},
        )
        response = client.get("/api/v1/credentials/secrets")
        names = {item["name"] for item in response.json()}
        assert "new-secret" in names

    def test_deprecated_shared_router_create_secret_validation_error(
        self,
        deprecated_generic_client: TestClient,
    ):
        response = deprecated_generic_client.post(
            "/api/v1/legacy-credentials/secrets",
            json={"name": "INVALID_NAME!", "data": {"KEY": "val"}},
        )
        assert response.status_code == 400
        assert "invalid" in response.json()["detail"].lower()
