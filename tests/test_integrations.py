"""Tests for integration management (NIU-107)."""

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
from volundr.adapters.outbound.jira import JiraAdapter
from volundr.adapters.outbound.memory_integrations import InMemoryIntegrationRepository
from volundr.domain.models import (
    IntegrationConnection,
    Principal,
    TrackerIssue,
)
from volundr.domain.ports import CredentialStorePort
from volundr.domain.services.integration_registry import (
    IntegrationRegistry,
    definitions_from_config,
)
from volundr.domain.services.tracker import TrackerService
from volundr.domain.services.tracker_factory import TrackerFactory

# --- Fixtures ---


@pytest.fixture
def integration_repo() -> InMemoryIntegrationRepository:
    return InMemoryIntegrationRepository()


@pytest.fixture
def mock_credential_store() -> AsyncMock:
    store = AsyncMock(spec=CredentialStorePort)
    store.get_value = AsyncMock(return_value={"api_key": "test-key"})
    return store


@pytest.fixture
def tracker_factory(mock_credential_store: AsyncMock) -> TrackerFactory:
    return TrackerFactory(mock_credential_store)


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
    )


@pytest.fixture
def sample_connection_disabled() -> IntegrationConnection:
    now = datetime.now(UTC)
    return IntegrationConnection(
        id="conn-2",
        owner_id="user-1",
        integration_type="issue_tracker",
        adapter="volundr.adapters.outbound.jira.JiraAdapter",
        credential_name="jira-key",
        config={"site_url": "https://test.atlassian.net"},
        enabled=False,
        created_at=now,
        updated_at=now,
    )


# --- InMemoryIntegrationRepository tests ---


class TestInMemoryIntegrationRepository:
    """Tests for the in-memory IntegrationRepository."""

    async def test_save_and_get(
        self,
        integration_repo: InMemoryIntegrationRepository,
        sample_connection: IntegrationConnection,
    ):
        saved = await integration_repo.save_connection(sample_connection)
        assert saved.id == "conn-1"

        retrieved = await integration_repo.get_connection("conn-1")
        assert retrieved is not None
        assert retrieved.adapter == sample_connection.adapter

    async def test_get_not_found(
        self,
        integration_repo: InMemoryIntegrationRepository,
    ):
        result = await integration_repo.get_connection("nonexistent")
        assert result is None

    async def test_list_connections(
        self,
        integration_repo: InMemoryIntegrationRepository,
        sample_connection: IntegrationConnection,
        sample_connection_disabled: IntegrationConnection,
    ):
        await integration_repo.save_connection(sample_connection)
        await integration_repo.save_connection(sample_connection_disabled)

        connections = await integration_repo.list_connections("user-1")
        assert len(connections) == 2

    async def test_list_connections_by_type(
        self,
        integration_repo: InMemoryIntegrationRepository,
        sample_connection: IntegrationConnection,
    ):
        await integration_repo.save_connection(sample_connection)

        connections = await integration_repo.list_connections(
            "user-1",
            "issue_tracker",
        )
        assert len(connections) == 1

    async def test_list_connections_wrong_user(
        self,
        integration_repo: InMemoryIntegrationRepository,
        sample_connection: IntegrationConnection,
    ):
        await integration_repo.save_connection(sample_connection)

        connections = await integration_repo.list_connections("other-user")
        assert len(connections) == 0

    async def test_delete_connection(
        self,
        integration_repo: InMemoryIntegrationRepository,
        sample_connection: IntegrationConnection,
    ):
        await integration_repo.save_connection(sample_connection)
        await integration_repo.delete_connection("conn-1")

        result = await integration_repo.get_connection("conn-1")
        assert result is None

    async def test_delete_nonexistent(
        self,
        integration_repo: InMemoryIntegrationRepository,
    ):
        # Should not raise
        await integration_repo.delete_connection("nonexistent")


# --- TrackerFactory tests ---


class TestTrackerFactory:
    """Tests for the TrackerFactory."""

    async def test_create_adapter(
        self,
        tracker_factory: TrackerFactory,
        sample_connection: IntegrationConnection,
    ):
        adapter = await tracker_factory.create(sample_connection)
        assert adapter.provider_name == "linear"

    async def test_create_credential_not_found(
        self,
        mock_credential_store: AsyncMock,
    ):
        mock_credential_store.get_value = AsyncMock(return_value=None)
        factory = TrackerFactory(mock_credential_store)

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
        )

        with pytest.raises(ValueError, match="not found"):
            await factory.create(conn)


# --- TrackerService with factory tests ---


class TestTrackerServiceWithFactory:
    """Tests for TrackerService integration repo support."""

    async def test_fallback_to_default_provider(self):
        """When no user connections exist, fall back to default."""
        from tests.test_tracker import (
            InMemoryIssueTracker,
            InMemoryMappingRepository,
        )

        tracker = InMemoryIssueTracker()
        tracker.add_issue(
            TrackerIssue(
                id="i1",
                identifier="T-1",
                title="Test",
                status="Open",
                url="https://example.com",
            )
        )
        mapping_repo = InMemoryMappingRepository()
        integration_repo = InMemoryIntegrationRepository()
        mock_factory = AsyncMock(spec=TrackerFactory)

        service = TrackerService(
            tracker,
            mapping_repo,
            integration_repo=integration_repo,
            tracker_factory=mock_factory,
        )

        # No user connections, should use default
        result = await service.search_issues("Test", user_id="user-1")
        assert len(result) == 1
        assert result[0].identifier == "T-1"
        mock_factory.create.assert_not_called()

    async def test_uses_user_connection_when_available(self):
        """When user has an active connection, use it."""
        from tests.test_tracker import InMemoryMappingRepository

        mapping_repo = InMemoryMappingRepository()
        integration_repo = InMemoryIntegrationRepository()

        now = datetime.now(UTC)
        conn = IntegrationConnection(
            id="conn-1",
            owner_id="user-1",
            integration_type="issue_tracker",
            adapter="volundr.adapters.outbound.linear.LinearAdapter",
            credential_name="key",
            config={},
            enabled=True,
            created_at=now,
            updated_at=now,
        )
        await integration_repo.save_connection(conn)

        mock_tracker = AsyncMock()
        mock_tracker.search_issues = AsyncMock(
            return_value=[
                TrackerIssue(
                    id="i2",
                    identifier="U-1",
                    title="User Issue",
                    status="Open",
                    url="https://example.com",
                ),
            ]
        )

        mock_factory = AsyncMock(spec=TrackerFactory)
        mock_factory.create = AsyncMock(return_value=mock_tracker)

        service = TrackerService(
            None,
            mapping_repo,
            integration_repo=integration_repo,
            tracker_factory=mock_factory,
        )

        result = await service.search_issues("User", user_id="user-1")
        assert len(result) == 1
        assert result[0].identifier == "U-1"
        mock_factory.create.assert_called_once()


# --- JiraAdapter unit tests (no network) ---


class TestJiraAdapterUnit:
    """Unit tests for JiraAdapter (no network calls)."""

    def test_provider_name(self):
        adapter = JiraAdapter(
            api_token="token",
            email="test@test.com",
            site_url="https://test.atlassian.net",
        )
        assert adapter.provider_name == "jira"

    def test_issue_to_tracker(self):
        issue = {
            "id": "10001",
            "key": "PROJ-42",
            "self": "https://test.atlassian.net/rest/api/3/issue/10001",
            "fields": {
                "summary": "Fix login bug",
                "status": {"name": "In Progress"},
                "assignee": {"displayName": "Alice"},
                "labels": ["bug", "urgent"],
                "priority": {"name": "High"},
            },
        }
        result = JiraAdapter._issue_to_tracker(issue)
        assert result.id == "10001"
        assert result.identifier == "PROJ-42"
        assert result.title == "Fix login bug"
        assert result.status == "In Progress"
        assert result.assignee == "Alice"
        assert result.labels == ["bug", "urgent"]
        assert result.priority == 2

    def test_issue_to_tracker_missing_fields(self):
        issue = {
            "id": "10002",
            "key": "PROJ-43",
            "self": "https://test.atlassian.net/rest/api/3/issue/10002",
            "fields": {
                "summary": "Something",
                "status": None,
                "assignee": None,
                "labels": [],
                "priority": None,
            },
        }
        result = JiraAdapter._issue_to_tracker(issue)
        assert result.status == "Unknown"
        assert result.assignee is None
        assert result.labels == []
        assert result.priority == 0


# --- Integration REST endpoint tests ---


@pytest.fixture
def mock_principal():
    return Principal(
        user_id="user-1",
        email="test@test.com",
        tenant_id="default",
        roles=["volundr:admin"],
    )


@pytest.fixture
def integration_client(
    integration_repo: InMemoryIntegrationRepository,
    tracker_factory: TrackerFactory,
    mock_principal: Principal,
) -> TestClient:
    app = FastAPI()

    # Mock auth dependency
    async def mock_extract_principal():
        return mock_principal

    canonical_router = create_canonical_integrations_router(integration_repo, tracker_factory)
    app.include_router(canonical_router)
    router = create_integrations_router(integration_repo, tracker_factory)
    app.include_router(router)

    # Override the dependency
    from volundr.adapters.inbound.auth import extract_principal

    app.dependency_overrides[extract_principal] = mock_extract_principal

    return TestClient(app)


class TestIntegrationEndpoints:
    """Tests for integration REST endpoints."""

    def test_list_empty(self, integration_client: TestClient):
        response = integration_client.get("/api/v1/volundr/integrations")
        assert response.status_code == 200
        assert response.json() == []
        assert response.headers["Deprecation"] == "true"
        assert response.headers["X-Niuu-Canonical-Route"] == "/api/v1/integrations"

    def test_list_preserves_boolean_config_values(
        self,
        integration_client: TestClient,
        integration_repo: InMemoryIntegrationRepository,
    ):
        now = datetime.now(UTC)
        connection = IntegrationConnection(
            id="conn-bool",
            owner_id="user-1",
            integration_type="messaging",
            adapter="tyr.adapters.telegram_notification.TelegramNotificationAdapter",
            credential_name="telegram-main",
            config={"notify_only": True},
            enabled=True,
            created_at=now,
            updated_at=now,
            slug="telegram",
        )
        import asyncio

        asyncio.run(integration_repo.save_connection(connection))

        response = integration_client.get("/api/v1/volundr/integrations")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["config"]["notify_only"] is True

    def test_create_integration(self, integration_client: TestClient):
        response = integration_client.post(
            "/api/v1/volundr/integrations",
            json={
                "integration_type": "issue_tracker",
                "adapter": "volundr.adapters.outbound.linear.LinearAdapter",
                "credential_name": "linear-key",
                "config": {},
                "enabled": True,
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["integration_type"] == "issue_tracker"
        assert data["adapter"] == "volundr.adapters.outbound.linear.LinearAdapter"
        assert data["credential_name"] == "linear-key"
        assert data["integrationType"] == "issue_tracker"
        assert data["credentialName"] == "linear-key"
        assert data["enabled"] is True
        assert "id" in data
        assert "created_at" in data
        assert data["createdAt"] == data["created_at"]
        assert data["updatedAt"] == data["updated_at"]

    def test_create_integration_accepts_camel_case_legacy_payload(
        self,
        integration_client: TestClient,
    ):
        response = integration_client.post(
            "/api/v1/volundr/integrations",
            json={
                "integrationType": "issue_tracker",
                "type": "issue_tracker",
                "adapter": "volundr.adapters.outbound.linear.LinearAdapter",
                "credentialName": "linear-key",
                "config": {},
                "enabled": True,
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["integration_type"] == "issue_tracker"
        assert data["credential_name"] == "linear-key"

    def test_create_and_list(self, integration_client: TestClient):
        integration_client.post(
            "/api/v1/volundr/integrations",
            json={
                "integration_type": "issue_tracker",
                "adapter": "volundr.adapters.outbound.linear.LinearAdapter",
                "credential_name": "key",
            },
        )
        response = integration_client.get("/api/v1/volundr/integrations")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["integrationType"] == data[0]["integration_type"]
        assert data[0]["credentialName"] == data[0]["credential_name"]

    def test_delete_integration(self, integration_client: TestClient):
        create_resp = integration_client.post(
            "/api/v1/volundr/integrations",
            json={
                "integration_type": "issue_tracker",
                "adapter": "volundr.adapters.outbound.linear.LinearAdapter",
                "credential_name": "key",
            },
        )
        conn_id = create_resp.json()["id"]

        delete_resp = integration_client.delete(
            f"/api/v1/volundr/integrations/{conn_id}",
        )
        assert delete_resp.status_code == 204

        list_resp = integration_client.get("/api/v1/volundr/integrations")
        assert len(list_resp.json()) == 0

    def test_delete_not_found(self, integration_client: TestClient):
        response = integration_client.delete(
            "/api/v1/volundr/integrations/nonexistent",
        )
        assert response.status_code == 404

    def test_update_integration(self, integration_client: TestClient):
        create_resp = integration_client.post(
            "/api/v1/volundr/integrations",
            json={
                "integration_type": "issue_tracker",
                "adapter": "volundr.adapters.outbound.linear.LinearAdapter",
                "credential_name": "key",
                "enabled": True,
            },
        )
        conn_id = create_resp.json()["id"]

        update_resp = integration_client.put(
            f"/api/v1/volundr/integrations/{conn_id}",
            json={"enabled": False},
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["enabled"] is False

    def test_update_integration_accepts_camel_case_legacy_payload(
        self,
        integration_client: TestClient,
    ):
        create_resp = integration_client.post(
            "/api/v1/volundr/integrations",
            json={
                "integration_type": "issue_tracker",
                "adapter": "volundr.adapters.outbound.linear.LinearAdapter",
                "credential_name": "key",
                "enabled": True,
            },
        )
        conn_id = create_resp.json()["id"]

        update_resp = integration_client.put(
            f"/api/v1/volundr/integrations/{conn_id}",
            json={"credentialName": "renamed-key"},
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["credential_name"] == "renamed-key"

    def test_update_not_found(self, integration_client: TestClient):
        response = integration_client.put(
            "/api/v1/volundr/integrations/nonexistent",
            json={"enabled": False},
        )
        assert response.status_code == 404

    def test_test_integration(self, integration_client: TestClient):
        create_resp = integration_client.post(
            "/api/v1/volundr/integrations",
            json={
                "integration_type": "issue_tracker",
                "adapter": "volundr.adapters.outbound.linear.LinearAdapter",
                "credential_name": "key",
            },
        )
        conn_id = create_resp.json()["id"]

        # Test will fail since we're not actually connecting
        response = integration_client.post(
            f"/api/v1/volundr/integrations/{conn_id}/test",
        )
        assert response.status_code == 200
        data = response.json()
        # It returns a result (success or failure depends on network)
        assert "success" in data
        assert "provider" in data

    def test_test_not_found(self, integration_client: TestClient):
        response = integration_client.post(
            "/api/v1/volundr/integrations/nonexistent/test",
        )
        assert response.status_code == 404

    def test_canonical_list_matches_legacy(
        self,
        integration_client: TestClient,
        integration_repo: InMemoryIntegrationRepository,
        sample_connection: IntegrationConnection,
    ):
        import asyncio

        asyncio.run(integration_repo.save_connection(sample_connection))

        assert_route_equivalence(
            integration_client,
            legacy=RouteCallSpec(path="/api/v1/volundr/integrations"),
            canonical=RouteCallSpec(path="/api/v1/integrations"),
        )

    def test_canonical_test_matches_legacy(
        self,
        integration_client: TestClient,
        integration_repo: InMemoryIntegrationRepository,
        sample_connection: IntegrationConnection,
    ):
        import asyncio

        asyncio.run(integration_repo.save_connection(sample_connection))

        assert_route_equivalence(
            integration_client,
            legacy=RouteCallSpec(
                path=f"/api/v1/volundr/integrations/{sample_connection.id}/test",
                method="POST",
            ),
            canonical=RouteCallSpec(
                path=f"/api/v1/integrations/{sample_connection.id}/test",
                method="POST",
            ),
        )

    def test_legacy_catalog_returns_catalog_entries_with_mcp_and_oauth_metadata(
        self,
        integration_repo: InMemoryIntegrationRepository,
        tracker_factory: TrackerFactory,
        mock_principal: Principal,
    ):
        registry = IntegrationRegistry(
            definitions_from_config(
                [
                    {
                        "slug": "linear",
                        "name": "Linear",
                        "description": "Issue tracking with Linear",
                        "integration_type": "issue_tracker",
                        "adapter": "volundr.adapters.outbound.linear.LinearAdapter",
                        "icon": "linear",
                        "credential_schema": {"type": "object"},
                        "config_schema": {"type": "object"},
                        "auth_type": "oauth2_authorization_code",
                        "mcp_server": {
                            "name": "linear-mcp",
                            "command": "npx",
                            "args": ["@linear/mcp-server"],
                            "env_from_credentials": {"LINEAR_API_KEY": "token"},
                        },
                        "oauth": {
                            "authorize_url": "https://linear.app/oauth/authorize",
                            "token_url": "https://api.linear.app/oauth/token",
                            "scopes": ["read", "write"],
                        },
                    }
                ]
            )
        )
        app = FastAPI()

        async def mock_extract_principal():
            return mock_principal

        app.include_router(
            create_integrations_router(
                integration_repo,
                tracker_factory,
                registry=registry,
            )
        )

        from volundr.adapters.inbound.auth import extract_principal

        app.dependency_overrides[extract_principal] = mock_extract_principal
        client = TestClient(app)

        response = client.get("/api/v1/volundr/integrations/catalog")
        assert response.status_code == 200
        assert response.headers["Deprecation"] == "true"
        assert response.headers["X-Niuu-Canonical-Route"] == "/api/v1/integrations/catalog"
        data = response.json()
        assert data == [
            {
                "id": "linear",
                "slug": "linear",
                "name": "Linear",
                "description": "Issue tracking with Linear",
                "integration_type": "issue_tracker",
                "adapter": "volundr.adapters.outbound.linear.LinearAdapter",
                "icon": "linear",
                "credential_schema": {"type": "object"},
                "config_schema": {"type": "object"},
                "mcp_server": {
                    "name": "linear-mcp",
                    "command": "npx",
                    "args": ["@linear/mcp-server"],
                    "env_from_credentials": {"LINEAR_API_KEY": "token"},
                },
                "auth_type": "oauth2_authorization_code",
                "oauth_scopes": ["read", "write"],
            }
        ]

    def test_legacy_catalog_returns_empty_without_registry(
        self,
        integration_client: TestClient,
    ):
        response = integration_client.get("/api/v1/volundr/integrations/catalog")
        assert response.status_code == 200
        assert response.json() == []


class TestIntegrationTestEndpointBranches:
    """Tests for non-tracker integration test endpoint branches."""

    async def test_source_control_with_valid_credential(
        self,
        integration_repo: InMemoryIntegrationRepository,
        tracker_factory: TrackerFactory,
        mock_principal: Principal,
    ):
        credential_store = AsyncMock()
        credential_store.get_value.return_value = {"token": "ghp_xxx"}

        app = FastAPI()

        async def mock_extract_principal():
            return mock_principal

        router = create_integrations_router(
            integration_repo,
            tracker_factory,
            credential_store=credential_store,
        )
        app.include_router(router)

        from volundr.adapters.inbound.auth import extract_principal

        app.dependency_overrides[extract_principal] = mock_extract_principal
        client = TestClient(app)

        now = datetime.now(UTC)
        conn = IntegrationConnection(
            id="sc-1",
            owner_id="user-1",
            integration_type="source_control",
            adapter="volundr.adapters.outbound.github.GitHubProvider",
            credential_name="gh-token",
            config={},
            enabled=True,
            created_at=now,
            updated_at=now,
        )
        await integration_repo.save_connection(conn)

        resp = client.post("/api/v1/volundr/integrations/sc-1/test")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    async def test_ai_provider_with_missing_credential(
        self,
        integration_repo: InMemoryIntegrationRepository,
        tracker_factory: TrackerFactory,
        mock_principal: Principal,
    ):
        credential_store = AsyncMock()
        credential_store.get_value.return_value = None

        app = FastAPI()

        async def mock_extract_principal():
            return mock_principal

        router = create_integrations_router(
            integration_repo,
            tracker_factory,
            credential_store=credential_store,
        )
        app.include_router(router)

        from volundr.adapters.inbound.auth import extract_principal

        app.dependency_overrides[extract_principal] = mock_extract_principal
        client = TestClient(app)

        now = datetime.now(UTC)
        conn = IntegrationConnection(
            id="ai-1",
            owner_id="user-1",
            integration_type="ai_provider",
            adapter="volundr.adapters.outbound.anthropic.AnthropicProvider",
            credential_name="anthropic-key",
            config={},
            enabled=True,
            created_at=now,
            updated_at=now,
        )
        await integration_repo.save_connection(conn)

        resp = client.post("/api/v1/volundr/integrations/ai-1/test")
        assert resp.status_code == 200
        assert resp.json()["success"] is False
        assert resp.json()["error"] == "Credential not found"

    async def test_source_control_without_credential_store(
        self,
        integration_repo: InMemoryIntegrationRepository,
        tracker_factory: TrackerFactory,
        mock_principal: Principal,
    ):
        app = FastAPI()

        async def mock_extract_principal():
            return mock_principal

        router = create_integrations_router(
            integration_repo,
            tracker_factory,
        )
        app.include_router(router)

        from volundr.adapters.inbound.auth import extract_principal

        app.dependency_overrides[extract_principal] = mock_extract_principal
        client = TestClient(app)

        now = datetime.now(UTC)
        conn = IntegrationConnection(
            id="sc-2",
            owner_id="user-1",
            integration_type="source_control",
            adapter="volundr.adapters.outbound.github.GitHubProvider",
            credential_name="gh-token",
            config={},
            enabled=True,
            created_at=now,
            updated_at=now,
        )
        await integration_repo.save_connection(conn)

        resp = client.post("/api/v1/volundr/integrations/sc-2/test")
        assert resp.status_code == 200
        assert resp.json()["success"] is False
        assert resp.json()["error"] == "Credential store not configured"

    async def test_messaging_type_not_supported(
        self,
        integration_repo: InMemoryIntegrationRepository,
        tracker_factory: TrackerFactory,
        mock_principal: Principal,
    ):
        app = FastAPI()

        async def mock_extract_principal():
            return mock_principal

        router = create_integrations_router(
            integration_repo,
            tracker_factory,
        )
        app.include_router(router)

        from volundr.adapters.inbound.auth import extract_principal

        app.dependency_overrides[extract_principal] = mock_extract_principal
        client = TestClient(app)

        now = datetime.now(UTC)
        conn = IntegrationConnection(
            id="msg-1",
            owner_id="user-1",
            integration_type="messaging",
            adapter="some.MessagingAdapter",
            credential_name="slack-key",
            config={},
            enabled=True,
            created_at=now,
            updated_at=now,
        )
        await integration_repo.save_connection(conn)

        resp = client.post("/api/v1/volundr/integrations/msg-1/test")
        assert resp.status_code == 200
        assert resp.json()["success"] is False
        assert "not supported" in resp.json()["error"]

    async def test_issue_tracker_test_returns_serialized_exception(
        self,
        integration_repo: InMemoryIntegrationRepository,
        mock_principal: Principal,
    ):
        app = FastAPI()
        tracker_factory = AsyncMock(spec=TrackerFactory)
        tracker_factory.create.side_effect = RuntimeError("tracker exploded")

        async def mock_extract_principal():
            return mock_principal

        router = create_integrations_router(
            integration_repo,
            tracker_factory,
        )
        app.include_router(router)

        from volundr.adapters.inbound.auth import extract_principal

        app.dependency_overrides[extract_principal] = mock_extract_principal
        client = TestClient(app)

        now = datetime.now(UTC)
        conn = IntegrationConnection(
            id="it-1",
            owner_id="user-1",
            integration_type="issue_tracker",
            adapter="volundr.adapters.outbound.linear.LinearAdapter",
            credential_name="linear-key",
            config={},
            enabled=True,
            created_at=now,
            updated_at=now,
        )
        await integration_repo.save_connection(conn)

        resp = client.post("/api/v1/volundr/integrations/it-1/test")
        assert resp.status_code == 200
        assert resp.json()["success"] is False
        assert resp.json()["error"] == "tracker exploded"


# --- IntegrationConnection model tests ---


class TestIntegrationConnectionModel:
    """Tests for IntegrationConnection dataclass."""

    def test_frozen(self):
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
        assert conn.id == "c1"
        assert conn.owner_id == "u1"
        assert conn.enabled is True

    def test_config_dict(self):
        now = datetime.now(UTC)
        conn = IntegrationConnection(
            id="c1",
            owner_id="u1",
            integration_type="issue_tracker",
            adapter="some.Adapter",
            credential_name="cred",
            config={"site_url": "https://test.com"},
            enabled=True,
            created_at=now,
            updated_at=now,
        )
        assert conn.config["site_url"] == "https://test.com"
