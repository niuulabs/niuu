"""Tests for the REST adapter for MCP servers and secrets."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.helpers.http_contracts import RouteCallSpec, assert_route_equivalence
from volundr.adapters.inbound.rest_secrets import (
    MCPServerResponse,
    SecretResponse,
    _build_secrets_router,
    create_canonical_secrets_router,
    create_secrets_router,
)
from volundr.adapters.outbound.config_mcp_servers import ConfigMCPServerProvider
from volundr.adapters.outbound.memory_secrets import InMemorySecretManager
from volundr.config import MCPServerEntry
from volundr.domain.models import MCPServerConfig, SecretInfo

# ---- Fixtures ----


@pytest.fixture
def sample_mcp_entries() -> list[MCPServerEntry]:
    """Create sample MCP server config entries."""
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
    """Create sample secret info objects."""
    return [
        SecretInfo(name="github-token", keys=["GITHUB_TOKEN"]),
        SecretInfo(name="anthropic-api-key", keys=["ANTHROPIC_API_KEY"]),
    ]


@pytest.fixture
def mcp_provider(sample_mcp_entries) -> ConfigMCPServerProvider:
    """Create config-driven MCP server provider."""
    return ConfigMCPServerProvider(sample_mcp_entries)


@pytest.fixture
def secret_manager(sample_secrets) -> InMemorySecretManager:
    """Create in-memory secret manager with sample data."""
    return InMemorySecretManager(sample_secrets)


@pytest.fixture
def app(mcp_provider, secret_manager) -> FastAPI:
    """Create test FastAPI app with secrets routes."""
    app = FastAPI()
    app.include_router(create_canonical_secrets_router(mcp_provider, secret_manager))
    router = create_secrets_router(mcp_provider, secret_manager)
    app.include_router(router)
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """Create a test client."""
    return TestClient(app)


@pytest.fixture
def deprecated_generic_client(mcp_provider, secret_manager) -> TestClient:
    """Create a client for exercising deprecated shared-router branches."""
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


# ---- MCPServerResponse model tests ----


class TestMCPServerResponse:
    """Tests for MCPServerResponse model."""

    def test_from_config(self):
        """MCPServerResponse.from_config converts domain model."""
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
    """Tests for SecretResponse model."""

    def test_from_info(self):
        """SecretResponse.from_info converts domain model."""
        info = SecretInfo(name="my-secret", keys=["KEY1", "KEY2"])
        response = SecretResponse.from_info(info)

        assert response.name == "my-secret"
        assert response.keys == ["KEY1", "KEY2"]


# ---- MCP server endpoint tests ----


class TestListMCPServers:
    """Tests for GET /api/v1/volundr/mcp-servers."""

    def test_list_mcp_servers(self, client: TestClient):
        """Returns all MCP server configs."""
        response = client.get("/api/v1/volundr/mcp-servers")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        names = {s["name"] for s in data}
        assert names == {"linear", "filesystem"}
        assert response.headers["Deprecation"] == "true"
        assert response.headers["X-Niuu-Canonical-Route"] == "/api/v1/credentials/mcp-servers"

    def test_canonical_mcp_servers_match_legacy(self, client: TestClient):
        assert_route_equivalence(
            client,
            legacy=RouteCallSpec(path="/api/v1/volundr/mcp-servers"),
            canonical=RouteCallSpec(path="/api/v1/credentials/mcp-servers"),
        )

    def test_list_mcp_servers_empty(self):
        """Returns empty list when no servers configured."""
        app = FastAPI()
        provider = ConfigMCPServerProvider([])
        manager = InMemorySecretManager()
        router = create_secrets_router(provider, manager)
        app.include_router(router)
        client = TestClient(app)

        response = client.get("/api/v1/volundr/mcp-servers")
        assert response.status_code == 200
        assert response.json() == []


class TestGetMCPServer:
    """Tests for GET /api/v1/volundr/mcp-servers/{server_name}."""

    def test_get_mcp_server_success(self, client: TestClient):
        """Returns MCP server config by name."""
        response = client.get("/api/v1/volundr/mcp-servers/linear")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "linear"
        assert data["type"] == "stdio"
        assert data["command"] == "npx"
        assert data["args"] == ["-y", "@linear/mcp-server"]

    def test_get_mcp_server_not_found(self, client: TestClient):
        """Returns 404 for non-existent MCP server."""
        response = client.get("/api/v1/volundr/mcp-servers/nonexistent")
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


# ---- Secret endpoint tests (GET) ----


class TestListSecrets:
    """Tests for GET /api/v1/volundr/secrets."""

    def test_list_secrets(self, client: TestClient):
        """Legacy Volundr surface returns only secret names."""
        response = client.get("/api/v1/volundr/secrets")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert set(data) == {"github-token", "anthropic-api-key"}
        assert response.headers["Deprecation"] == "true"
        assert response.headers["X-Niuu-Canonical-Route"] == "/api/v1/credentials/secrets"

    def test_canonical_secrets_return_rich_metadata(self, client: TestClient):
        response = client.get("/api/v1/credentials/secrets")
        assert response.status_code == 200
        data = response.json()
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
        """Returns empty list when no secrets exist."""
        app = FastAPI()
        provider = ConfigMCPServerProvider([])
        manager = InMemorySecretManager()
        router = create_secrets_router(provider, manager)
        app.include_router(router)
        client = TestClient(app)

        response = client.get("/api/v1/volundr/secrets")
        assert response.status_code == 200
        assert response.json() == []


class TestGetSecret:
    """Tests for GET /api/v1/volundr/secrets/{secret_name}."""

    def test_get_secret_success(self, client: TestClient):
        """Returns secret metadata by name."""
        response = client.get("/api/v1/volundr/secrets/github-token")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "github-token"
        assert data["keys"] == ["GITHUB_TOKEN"]

    def test_get_secret_not_found(self, client: TestClient):
        """Returns 404 for non-existent secret."""
        response = client.get("/api/v1/volundr/secrets/nonexistent")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_deprecated_shared_router_secret_not_found(
        self,
        deprecated_generic_client: TestClient,
    ):
        response = deprecated_generic_client.get("/api/v1/legacy-credentials/secrets/nonexistent")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


# ---- Secret creation endpoint tests (POST) ----


class TestCreateSecret:
    """Tests for POST /api/v1/volundr/secrets."""

    def test_create_secret_success(self, client: TestClient):
        """Creates a new secret and returns metadata."""
        response = client.post(
            "/api/v1/volundr/secrets",
            json={"name": "my-api-key", "data": {"API_KEY": "sk-test"}},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "my-api-key"
        assert data["keys"] == ["API_KEY"]

    def test_create_secret_multiple_keys(self, client: TestClient):
        """Creates a secret with multiple key-value pairs."""
        response = client.post(
            "/api/v1/volundr/secrets",
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
        """Response contains keys but not secret values."""
        response = client.post(
            "/api/v1/volundr/secrets",
            json={"name": "no-leak", "data": {"SECRET": "super-secret-value"}},
        )
        assert response.status_code == 201
        data = response.json()
        assert "data" not in data
        assert "super-secret-value" not in str(data)

    def test_create_secret_conflict(self, client: TestClient):
        """Returns 409 when secret already exists."""
        response = client.post(
            "/api/v1/volundr/secrets",
            json={"name": "github-token", "data": {"KEY": "val"}},
        )
        assert response.status_code == 409
        assert "already exists" in response.json()["detail"].lower()

    def test_create_secret_invalid_name(self, client: TestClient):
        """Returns 400 for invalid Kubernetes secret name."""
        response = client.post(
            "/api/v1/volundr/secrets",
            json={"name": "INVALID_NAME!", "data": {"KEY": "val"}},
        )
        assert response.status_code == 400

    def test_create_secret_empty_data(self, client: TestClient):
        """Returns 422 for empty data dict."""
        response = client.post(
            "/api/v1/volundr/secrets",
            json={"name": "empty-data", "data": {}},
        )
        assert response.status_code == 422

    def test_create_secret_visible_in_list(self, client: TestClient):
        """Created secret appears in subsequent list."""
        client.post(
            "/api/v1/volundr/secrets",
            json={"name": "new-secret", "data": {"KEY": "val"}},
        )
        response = client.get("/api/v1/volundr/secrets")
        names = set(response.json())
        assert "new-secret" in names

    def test_canonical_create_secret_conflict(self, client: TestClient):
        response = client.post(
            "/api/v1/credentials/secrets",
            json={"name": "github-token", "data": {"KEY": "val"}},
        )
        assert response.status_code == 409
        assert "already exists" in response.json()["detail"].lower()

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
