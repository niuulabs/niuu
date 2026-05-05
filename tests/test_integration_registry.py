"""Tests for integration registry, MCP injection, and catalog endpoint."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.helpers.http_contracts import RouteCallSpec, assert_route_equivalence
from volundr.adapters.inbound.rest_integrations import (
    create_canonical_integrations_router,
    create_integrations_router,
)
from volundr.adapters.outbound.memory_integrations import InMemoryIntegrationRepository
from volundr.domain.models import (
    IntegrationConnection,
    IntegrationDefinition,
    MCPServerSpec,
    Principal,
)
from volundr.domain.ports import SecretRepository
from volundr.domain.services.integration_registry import (
    IntegrationRegistry,
    definitions_from_config,
)
from volundr.domain.services.mcp_injection import MCPInjectionService
from volundr.domain.services.tracker_factory import TrackerFactory

# --- Fixtures ---


@pytest.fixture
def linear_mcp_spec() -> MCPServerSpec:
    return MCPServerSpec(
        name="linear-mcp",
        command="npx",
        args=("-y", "@anthropic-ai/linear-mcp-server"),
        env_from_credentials={"LINEAR_API_KEY": "api_key"},
    )


@pytest.fixture
def linear_definition(linear_mcp_spec: MCPServerSpec) -> IntegrationDefinition:
    return IntegrationDefinition(
        slug="linear",
        name="Linear",
        description="Linear issue tracker",
        integration_type="issue_tracker",
        adapter="volundr.adapters.outbound.linear.LinearAdapter",
        icon="linear",
        credential_schema={
            "required": ["api_key"],
            "properties": {"api_key": {"type": "string"}},
        },
        config_schema={},
        mcp_server=linear_mcp_spec,
    )


@pytest.fixture
def github_definition() -> IntegrationDefinition:
    return IntegrationDefinition(
        slug="github",
        name="GitHub",
        description="GitHub source control",
        integration_type="source_control",
        adapter="volundr.adapters.outbound.github.GitHubProvider",
        icon="github",
        credential_schema={
            "required": ["personal_access_token"],
            "properties": {"personal_access_token": {"type": "string"}},
        },
        config_schema={},
        mcp_server=MCPServerSpec(
            name="github-mcp",
            command="npx",
            args=("-y", "@modelcontextprotocol/server-github"),
            env_from_credentials={
                "GITHUB_PERSONAL_ACCESS_TOKEN": "personal_access_token",
            },
        ),
    )


@pytest.fixture
def telegram_definition() -> IntegrationDefinition:
    return IntegrationDefinition(
        slug="telegram",
        name="Telegram",
        description="Telegram messaging",
        integration_type="messaging",
        adapter="volundr.adapters.outbound.telegram.TelegramIntegration",
        mcp_server=None,
    )


@pytest.fixture
def registry(
    linear_definition: IntegrationDefinition,
    github_definition: IntegrationDefinition,
    telegram_definition: IntegrationDefinition,
) -> IntegrationRegistry:
    return IntegrationRegistry([linear_definition, github_definition, telegram_definition])


@pytest.fixture
def sample_connection() -> IntegrationConnection:
    now = datetime.now(UTC)
    return IntegrationConnection(
        id="conn-1",
        owner_id="user-1",
        integration_type="issue_tracker",
        adapter="volundr.adapters.outbound.linear.LinearAdapter",
        credential_name="linear-key",
        config={},
        enabled=True,
        created_at=now,
        updated_at=now,
        slug="linear",
    )


# --- IntegrationRegistry tests ---


class TestIntegrationRegistry:
    """Tests for the IntegrationRegistry."""

    def test_get_definition(self, registry: IntegrationRegistry):
        defn = registry.get_definition("linear")
        assert defn is not None
        assert defn.slug == "linear"
        assert defn.name == "Linear"

    def test_get_definition_not_found(self, registry: IntegrationRegistry):
        assert registry.get_definition("nonexistent") is None

    def test_list_definitions(self, registry: IntegrationRegistry):
        definitions = registry.list_definitions()
        slugs = [d.slug for d in definitions]
        assert "github" in slugs
        assert "linear" in slugs
        assert "telegram" in slugs
        assert len(definitions) == 3  # fixture provides 3 definitions

    def test_list_definitions_no_user_defs(self):
        registry = IntegrationRegistry([])
        # Registry is a simple container — empty when no definitions passed
        assert len(registry.list_definitions()) == 0

    def test_get_definitions_by_type(self, registry: IntegrationRegistry):
        trackers = registry.get_definitions_by_type("issue_tracker")
        assert any(d.slug == "linear" for d in trackers)

        source_control = registry.get_definitions_by_type("source_control")
        assert any(d.slug == "github" for d in source_control)

    def test_get_definitions_by_type_none(self, registry: IntegrationRegistry):
        assert registry.get_definitions_by_type("nonexistent") == []

    def test_build_mcp_env(
        self,
        registry: IntegrationRegistry,
        sample_connection: IntegrationConnection,
    ):
        creds = {"api_key": "lin_test_key123"}
        env = registry.build_mcp_env(sample_connection, creds)
        assert env is not None
        assert env["LINEAR_API_KEY"] == "lin_test_key123"

    def test_build_mcp_env_no_mcp_server(
        self,
        registry: IntegrationRegistry,
    ):
        now = datetime.now(UTC)
        conn = IntegrationConnection(
            id="conn-t",
            owner_id="user-1",
            integration_type="messaging",
            adapter="volundr.adapters.outbound.telegram.TelegramIntegration",
            credential_name="tg-key",
            config={},
            enabled=True,
            created_at=now,
            updated_at=now,
            slug="telegram",
        )
        assert registry.build_mcp_env(conn, {}) is None

    def test_build_mcp_env_unknown_slug(self, registry: IntegrationRegistry):
        now = datetime.now(UTC)
        conn = IntegrationConnection(
            id="conn-x",
            owner_id="user-1",
            integration_type="unknown",
            adapter="some.Adapter",
            credential_name="key",
            config={},
            enabled=True,
            created_at=now,
            updated_at=now,
            slug="nonexistent",
        )
        assert registry.build_mcp_env(conn, {}) is None

    def test_build_mcp_server_config(
        self,
        registry: IntegrationRegistry,
        sample_connection: IntegrationConnection,
    ):
        creds = {"api_key": "lin_test_key123"}
        cfg = registry.build_mcp_server_config(sample_connection, creds)
        assert cfg is not None
        assert cfg["name"] == "linear-mcp"
        assert cfg["type"] == "stdio"
        assert cfg["command"] == "npx"
        assert cfg["args"] == ["-y", "@anthropic-ai/linear-mcp-server"]
        assert cfg["env"]["LINEAR_API_KEY"] == "lin_test_key123"

    def test_build_mcp_server_config_no_mcp(
        self,
        registry: IntegrationRegistry,
    ):
        now = datetime.now(UTC)
        conn = IntegrationConnection(
            id="conn-t",
            owner_id="user-1",
            integration_type="messaging",
            adapter="volundr.adapters.outbound.telegram.TelegramIntegration",
            credential_name="tg-key",
            config={},
            enabled=True,
            created_at=now,
            updated_at=now,
            slug="telegram",
        )
        assert registry.build_mcp_server_config(conn, {}) is None

    def test_build_mcp_env_falls_back_to_config(
        self,
        registry: IntegrationRegistry,
    ):
        """When credential field is missing, falls back to connection config."""
        now = datetime.now(UTC)
        conn = IntegrationConnection(
            id="conn-gh",
            owner_id="user-1",
            integration_type="source_control",
            adapter="volundr.adapters.outbound.github.GitHubProvider",
            credential_name="gh-key",
            config={"personal_access_token": "from-config"},
            enabled=True,
            created_at=now,
            updated_at=now,
            slug="github",
        )
        env = registry.build_mcp_env(conn, {})
        assert env is not None
        assert env["GITHUB_PERSONAL_ACCESS_TOKEN"] == "from-config"


# --- definitions_from_config tests ---


class TestDefinitionsFromConfig:
    """Tests for definitions_from_config parser."""

    def test_parse_with_mcp(self):
        raw = [
            {
                "slug": "linear",
                "name": "Linear",
                "description": "Tracker",
                "integration_type": "issue_tracker",
                "adapter": "volundr.adapters.outbound.linear.LinearAdapter",
                "icon": "linear",
                "credential_schema": {
                    "required": ["api_key"],
                    "properties": {"api_key": {"type": "string"}},
                },
                "mcp_server": {
                    "name": "linear-mcp",
                    "command": "npx",
                    "args": ["-y", "@anthropic-ai/linear-mcp-server"],
                    "env_from_credentials": {"LINEAR_API_KEY": "api_key"},
                },
            }
        ]
        defs = definitions_from_config(raw)
        assert len(defs) == 1
        d = defs[0]
        assert d.slug == "linear"
        assert d.mcp_server is not None
        assert d.mcp_server.name == "linear-mcp"
        assert d.mcp_server.args == ("-y", "@anthropic-ai/linear-mcp-server")

    def test_parse_without_mcp(self):
        raw = [
            {
                "slug": "telegram",
                "name": "Telegram",
                "integration_type": "messaging",
                "adapter": "some.Adapter",
            }
        ]
        defs = definitions_from_config(raw)
        assert len(defs) == 1
        assert defs[0].mcp_server is None
        assert defs[0].description == ""

    def test_parse_empty(self):
        assert definitions_from_config([]) == []

    def test_parse_null_mcp(self):
        raw = [
            {
                "slug": "test",
                "name": "Test",
                "integration_type": "messaging",
                "adapter": "some.Adapter",
                "mcp_server": None,
            }
        ]
        defs = definitions_from_config(raw)
        assert defs[0].mcp_server is None


# --- MCPInjectionService tests ---


class TestMCPInjectionService:
    """Tests for MCP auto-injection logic."""

    async def test_collect_for_enabled_integration(
        self,
        registry: IntegrationRegistry,
    ):
        integration_repo = InMemoryIntegrationRepository()
        now = datetime.now(UTC)
        conn = IntegrationConnection(
            id="conn-1",
            owner_id="user-1",
            integration_type="issue_tracker",
            adapter="volundr.adapters.outbound.linear.LinearAdapter",
            credential_name="linear-key",
            config={},
            enabled=True,
            created_at=now,
            updated_at=now,
            slug="linear",
        )
        await integration_repo.save_connection(conn)

        cred_store = AsyncMock()
        cred_store.get_value = AsyncMock(return_value={"api_key": "lin_key"})

        service = MCPInjectionService(registry, integration_repo, cred_store)
        servers = await service.collect_mcp_servers("user-1")

        assert len(servers) == 1
        assert servers[0]["name"] == "linear-mcp"
        assert servers[0]["env"]["LINEAR_API_KEY"] == "lin_key"

    async def test_skip_disabled_integration(
        self,
        registry: IntegrationRegistry,
    ):
        integration_repo = InMemoryIntegrationRepository()
        now = datetime.now(UTC)
        conn = IntegrationConnection(
            id="conn-1",
            owner_id="user-1",
            integration_type="issue_tracker",
            adapter="volundr.adapters.outbound.linear.LinearAdapter",
            credential_name="linear-key",
            config={},
            enabled=False,
            created_at=now,
            updated_at=now,
            slug="linear",
        )
        await integration_repo.save_connection(conn)

        cred_store = AsyncMock()
        service = MCPInjectionService(registry, integration_repo, cred_store)
        servers = await service.collect_mcp_servers("user-1")
        assert servers == []

    async def test_skip_no_slug(self, registry: IntegrationRegistry):
        integration_repo = InMemoryIntegrationRepository()
        now = datetime.now(UTC)
        conn = IntegrationConnection(
            id="conn-1",
            owner_id="user-1",
            integration_type="issue_tracker",
            adapter="volundr.adapters.outbound.linear.LinearAdapter",
            credential_name="linear-key",
            config={},
            enabled=True,
            created_at=now,
            updated_at=now,
            slug="",
        )
        await integration_repo.save_connection(conn)

        cred_store = AsyncMock()
        service = MCPInjectionService(registry, integration_repo, cred_store)
        servers = await service.collect_mcp_servers("user-1")
        assert servers == []

    async def test_skip_missing_credential(
        self,
        registry: IntegrationRegistry,
    ):
        integration_repo = InMemoryIntegrationRepository()
        now = datetime.now(UTC)
        conn = IntegrationConnection(
            id="conn-1",
            owner_id="user-1",
            integration_type="issue_tracker",
            adapter="volundr.adapters.outbound.linear.LinearAdapter",
            credential_name="missing-key",
            config={},
            enabled=True,
            created_at=now,
            updated_at=now,
            slug="linear",
        )
        await integration_repo.save_connection(conn)

        cred_store = AsyncMock()
        cred_store.get_value = AsyncMock(return_value=None)

        service = MCPInjectionService(registry, integration_repo, cred_store)
        servers = await service.collect_mcp_servers("user-1")
        assert servers == []

    async def test_skip_no_mcp_server(
        self,
        registry: IntegrationRegistry,
    ):
        """Integration with no MCP server spec (telegram) is skipped."""
        integration_repo = InMemoryIntegrationRepository()
        now = datetime.now(UTC)
        conn = IntegrationConnection(
            id="conn-t",
            owner_id="user-1",
            integration_type="messaging",
            adapter="volundr.adapters.outbound.telegram.TelegramIntegration",
            credential_name="tg-key",
            config={},
            enabled=True,
            created_at=now,
            updated_at=now,
            slug="telegram",
        )
        await integration_repo.save_connection(conn)

        cred_store = AsyncMock()
        cred_store.get_value = AsyncMock(return_value={"bot_token": "tok"})

        service = MCPInjectionService(registry, integration_repo, cred_store)
        servers = await service.collect_mcp_servers("user-1")
        assert servers == []

    async def test_multiple_integrations(
        self,
        registry: IntegrationRegistry,
    ):
        integration_repo = InMemoryIntegrationRepository()
        now = datetime.now(UTC)

        linear_conn = IntegrationConnection(
            id="conn-1",
            owner_id="user-1",
            integration_type="issue_tracker",
            adapter="volundr.adapters.outbound.linear.LinearAdapter",
            credential_name="linear-key",
            config={},
            enabled=True,
            created_at=now,
            updated_at=now,
            slug="linear",
        )
        github_conn = IntegrationConnection(
            id="conn-2",
            owner_id="user-1",
            integration_type="source_control",
            adapter="volundr.adapters.outbound.github.GitHubProvider",
            credential_name="gh-key",
            config={},
            enabled=True,
            created_at=now,
            updated_at=now,
            slug="github",
        )
        await integration_repo.save_connection(linear_conn)
        await integration_repo.save_connection(github_conn)

        cred_store = AsyncMock()

        async def get_value(owner_type, owner_id, name):
            if name == "linear-key":
                return {"api_key": "lin_key"}
            if name == "gh-key":
                return {"personal_access_token": "ghp_tok"}
            return None

        cred_store.get_value = AsyncMock(side_effect=get_value)

        service = MCPInjectionService(registry, integration_repo, cred_store)
        servers = await service.collect_mcp_servers("user-1")

        assert len(servers) == 2
        names = {s["name"] for s in servers}
        assert "linear-mcp" in names
        assert "github-mcp" in names

    async def test_no_integrations(self, registry: IntegrationRegistry):
        integration_repo = InMemoryIntegrationRepository()
        cred_store = AsyncMock()
        service = MCPInjectionService(registry, integration_repo, cred_store)
        servers = await service.collect_mcp_servers("user-1")
        assert servers == []


# --- Catalog endpoint tests ---


@pytest.fixture
def mock_principal():
    return Principal(
        user_id="user-1",
        email="test@test.com",
        tenant_id="default",
        roles=["volundr:admin"],
    )


@pytest.fixture
def catalog_client(
    registry: IntegrationRegistry,
    mock_principal: Principal,
) -> TestClient:
    app = FastAPI()
    integration_repo = InMemoryIntegrationRepository()
    mock_secret_repo = AsyncMock(spec=SecretRepository)
    mock_secret_repo.get_credential = AsyncMock(return_value={"api_key": "k"})
    tracker_factory = TrackerFactory(mock_secret_repo)

    async def mock_extract_principal():
        return mock_principal

    canonical_router = create_canonical_integrations_router(
        integration_repo,
        tracker_factory,
        registry=registry,
    )
    app.include_router(canonical_router)
    router = create_integrations_router(
        integration_repo,
        tracker_factory,
        registry=registry,
    )
    app.include_router(router)

    from volundr.adapters.inbound.auth import extract_principal

    app.dependency_overrides[extract_principal] = mock_extract_principal
    return TestClient(app)


class TestCatalogEndpoint:
    """Tests for the GET /catalog endpoint."""

    def test_list_catalog(self, catalog_client: TestClient):
        response = catalog_client.get("/api/v1/volundr/integrations/catalog")
        assert response.status_code == 200
        data = response.json()
        ids = [d["id"] for d in data]
        slugs = [d["slug"] for d in data]
        # Built-ins + test telegram
        assert "github" in ids
        assert "github" in slugs
        assert "linear" in ids
        assert "linear" in slugs
        assert "telegram" in ids
        assert "telegram" in slugs
        assert len(data) == 3  # fixture provides 3 definitions
        assert response.headers["Deprecation"] == "true"
        assert response.headers["X-Niuu-Canonical-Route"] == "/api/v1/integrations/catalog"

    def test_canonical_catalog_matches_legacy(self, catalog_client: TestClient):
        assert_route_equivalence(
            catalog_client,
            legacy=RouteCallSpec(path="/api/v1/volundr/integrations/catalog"),
            canonical=RouteCallSpec(path="/api/v1/integrations/catalog"),
        )

    def test_catalog_entry_has_mcp(self, catalog_client: TestClient):
        response = catalog_client.get("/api/v1/volundr/integrations/catalog")
        data = response.json()
        linear = next(d for d in data if d["slug"] == "linear")
        assert linear["id"] == "linear"
        assert linear["mcp_server"] is not None
        assert linear["mcp_server"]["name"] == "linear-mcp"
        assert linear["mcp_server"]["command"] == "npx"

    def test_catalog_entry_no_mcp(self, catalog_client: TestClient):
        response = catalog_client.get("/api/v1/volundr/integrations/catalog")
        data = response.json()
        telegram = next(d for d in data if d["slug"] == "telegram")
        assert telegram["mcp_server"] is None

    def test_catalog_empty_registry(self, mock_principal: Principal):
        app = FastAPI()
        integration_repo = InMemoryIntegrationRepository()
        mock_secret_repo = AsyncMock(spec=SecretRepository)
        tracker_factory = TrackerFactory(mock_secret_repo)
        router = create_integrations_router(
            integration_repo,
            tracker_factory,
            registry=IntegrationRegistry([]),
        )
        app.include_router(router)

        async def mock_extract():
            return mock_principal

        from volundr.adapters.inbound.auth import extract_principal

        app.dependency_overrides[extract_principal] = mock_extract
        client = TestClient(app)

        response = client.get("/api/v1/volundr/integrations/catalog")
        assert response.status_code == 200
        # Empty registry returns no definitions
        assert len(response.json()) == 0

    def test_catalog_no_registry(self, mock_principal: Principal):
        """When no registry is provided, catalog returns empty list."""
        app = FastAPI()
        integration_repo = InMemoryIntegrationRepository()
        mock_secret_repo = AsyncMock(spec=SecretRepository)
        tracker_factory = TrackerFactory(mock_secret_repo)
        router = create_integrations_router(
            integration_repo,
            tracker_factory,
            registry=None,
        )
        app.include_router(router)

        async def mock_extract():
            return mock_principal

        from volundr.adapters.inbound.auth import extract_principal

        app.dependency_overrides[extract_principal] = mock_extract
        client = TestClient(app)

        response = client.get("/api/v1/volundr/integrations/catalog")
        assert response.status_code == 200
        assert response.json() == []


# --- MCPServerSpec model tests ---


class TestMCPServerSpec:
    """Tests for MCPServerSpec dataclass."""

    def test_args_coerced_to_tuple(self):
        spec = MCPServerSpec(
            name="test",
            command="cmd",
            args=["a", "b"],
        )
        assert isinstance(spec.args, tuple)
        assert spec.args == ("a", "b")

    def test_env_coerced_to_dict(self):
        spec = MCPServerSpec(
            name="test",
            command="cmd",
        )
        assert isinstance(spec.env_from_credentials, dict)


# --- IntegrationDefinition model tests ---


class TestIntegrationDefinition:
    """Tests for IntegrationDefinition dataclass."""

    def test_schemas_default_to_dict(self):
        defn = IntegrationDefinition(
            slug="test",
            name="Test",
            description="test",
            integration_type="test",
            adapter="test.Adapter",
        )
        assert isinstance(defn.credential_schema, dict)
        assert isinstance(defn.config_schema, dict)

    def test_with_all_fields(self, linear_definition: IntegrationDefinition):
        assert linear_definition.slug == "linear"
        assert linear_definition.mcp_server is not None
        assert linear_definition.credential_schema["required"] == ["api_key"]


# --- IntegrationConnection slug field tests ---


class TestIntegrationConnectionSlug:
    """Tests for the slug field on IntegrationConnection."""

    def test_default_empty_slug(self):
        now = datetime.now(UTC)
        conn = IntegrationConnection(
            id="c1",
            owner_id="u1",
            integration_type="issue_tracker",
            adapter="some.Adapter",
            credential_name="cred",
            config={},
            enabled=True,
            created_at=now,
            updated_at=now,
        )
        assert conn.slug == ""

    def test_explicit_slug(self):
        now = datetime.now(UTC)
        conn = IntegrationConnection(
            id="c1",
            owner_id="u1",
            integration_type="issue_tracker",
            adapter="some.Adapter",
            credential_name="cred",
            config={},
            enabled=True,
            created_at=now,
            updated_at=now,
            slug="linear",
        )
        assert conn.slug == "linear"
