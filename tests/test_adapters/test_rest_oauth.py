"""Tests for REST OAuth endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from volundr.adapters.inbound.rest_oauth import create_canonical_oauth_router, create_oauth_router
from volundr.config import OAuthClientConfig, OAuthConfig
from volundr.domain.models import (
    IntegrationConnection,
    IntegrationDefinition,
    IntegrationType,
    OAuthSpec,
    Principal,
)
from volundr.domain.services.integration_registry import IntegrationRegistry


def _mock_identity(principal: Principal | None = None):
    identity = AsyncMock()
    if principal is None:
        principal = Principal(
            user_id="u1",
            email="user@test.com",
            tenant_id="t1",
            roles=["volundr:admin"],
        )
    identity.validate_token.return_value = principal
    return identity


def _make_definition(slug: str = "linear", oauth: OAuthSpec | None = None) -> IntegrationDefinition:
    if oauth is None:
        oauth = OAuthSpec(
            authorize_url="https://auth.example.com/authorize",
            token_url="https://auth.example.com/token",
            revoke_url="https://auth.example.com/revoke",
            scopes=("read", "write"),
            token_field_mapping={"api_key": "access_token"},
        )
    return IntegrationDefinition(
        slug=slug,
        name="Linear",
        description="Issue tracker",
        integration_type=IntegrationType.ISSUE_TRACKER,
        adapter="volundr.adapters.linear.LinearProvider",
        oauth=oauth,
    )


def _make_app(
    definitions: list[IntegrationDefinition] | None = None,
    clients: dict[str, OAuthClientConfig] | None = None,
    identity=None,
) -> tuple[FastAPI, AsyncMock, AsyncMock]:
    registry = IntegrationRegistry(definitions or [])
    credential_store = AsyncMock()
    integration_repo = AsyncMock()

    oauth_config = OAuthConfig(
        redirect_base_url="https://app.test",
        clients=clients or {},
    )

    app = FastAPI()
    app.state.identity = identity or _mock_identity()
    canonical_router = create_canonical_oauth_router(
        oauth_config=oauth_config,
        integration_registry=registry,
        credential_store=credential_store,
        integration_repo=integration_repo,
    )
    app.include_router(canonical_router)
    router = create_oauth_router(
        oauth_config=oauth_config,
        integration_registry=registry,
        credential_store=credential_store,
        integration_repo=integration_repo,
    )
    app.include_router(router)
    return app, credential_store, integration_repo


AUTH = {"Authorization": "Bearer tok"}
PREFIX = "/api/v1/volundr/integrations/oauth"
CANONICAL_PREFIX = "/api/v1/integrations/oauth"


class TestAuthorize:
    def test_returns_url_for_valid_integration(self):
        defn = _make_definition(slug="linear")
        clients = {"linear": OAuthClientConfig(client_id="cid", client_secret="csec")}
        app, _, _ = _make_app(definitions=[defn], clients=clients)
        client = TestClient(app)

        resp = client.get(f"{PREFIX}/linear/authorize", headers=AUTH)

        assert resp.status_code == 200
        body = resp.json()
        assert "url" in body
        assert "https://auth.example.com/authorize" in body["url"]
        assert "client_id=cid" in body["url"]
        assert "state=" in body["url"]

    def test_legacy_authorize_sets_deprecation_headers(self):
        defn = _make_definition(slug="linear")
        clients = {"linear": OAuthClientConfig(client_id="cid", client_secret="csec")}
        app, _, _ = _make_app(definitions=[defn], clients=clients)
        client = TestClient(app)

        resp = client.get(f"{PREFIX}/linear/authorize", headers=AUTH)

        assert resp.status_code == 200
        assert resp.headers["Deprecation"] == "true"
        assert resp.headers["X-Niuu-Canonical-Route"] == f"{CANONICAL_PREFIX}/linear/authorize"

    def test_canonical_authorize_route_exists(self):
        defn = _make_definition(slug="linear")
        clients = {"linear": OAuthClientConfig(client_id="cid", client_secret="csec")}
        app, _, _ = _make_app(definitions=[defn], clients=clients)
        client = TestClient(app)

        resp = client.get(f"{CANONICAL_PREFIX}/linear/authorize", headers=AUTH)

        assert resp.status_code == 200
        assert "url" in resp.json()

    def test_404_for_missing_integration(self):
        app, _, _ = _make_app(definitions=[], clients={})
        client = TestClient(app)

        resp = client.get(f"{PREFIX}/nonexistent/authorize", headers=AUTH)

        assert resp.status_code == 404

    def test_400_for_missing_client_config(self):
        defn = _make_definition(slug="linear")
        app, _, _ = _make_app(definitions=[defn], clients={})
        client = TestClient(app)

        resp = client.get(f"{PREFIX}/linear/authorize", headers=AUTH)

        assert resp.status_code == 400
        assert "not configured" in resp.json()["detail"]

    def test_404_for_integration_without_oauth(self):
        defn = IntegrationDefinition(
            slug="manual",
            name="Manual",
            description="No OAuth",
            integration_type=IntegrationType.AI_PROVIDER,
            adapter="some.Adapter",
            oauth=None,
        )
        app, _, _ = _make_app(definitions=[defn], clients={})
        client = TestClient(app)

        resp = client.get(f"{PREFIX}/manual/authorize", headers=AUTH)

        assert resp.status_code == 404


class TestCallback:
    def test_400_for_invalid_state(self):
        app, _, _ = _make_app()
        client = TestClient(app)

        resp = client.get(
            f"{PREFIX}/callback?code=abc&state=invalid-state",
            headers=AUTH,
        )

        assert resp.status_code == 400
        assert "Invalid or expired" in resp.json()["detail"]

    def test_successful_callback_stores_credential_and_connection(self):
        from unittest.mock import patch as _patch

        defn = _make_definition(slug="linear")
        clients = {
            "linear": OAuthClientConfig(
                client_id="cid",
                client_secret="csec",
            ),
        }
        app, credential_store, integration_repo = _make_app(
            definitions=[defn],
            clients=clients,
        )
        client = TestClient(app, raise_server_exceptions=False)

        # Step 1: call authorize to create pending state
        auth_resp = client.get(
            f"{PREFIX}/linear/authorize",
            headers=AUTH,
        )
        assert auth_resp.status_code == 200
        url = auth_resp.json()["url"]

        # Extract state from the URL
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(url)
        state = parse_qs(parsed.query)["state"][0]

        # Step 2: mock the token exchange and call callback
        mock_exchange = AsyncMock(
            return_value={"api_key": "oauth-tok-123"},
        )
        with _patch.object(
            __import__(
                "volundr.adapters.outbound.oauth2_provider",
                fromlist=["OAuth2Provider"],
            ).OAuth2Provider,
            "exchange_code",
            mock_exchange,
        ):
            cb_resp = client.get(
                f"{PREFIX}/callback?code=auth-code&state={state}",
            )

        assert cb_resp.status_code == 200
        assert "Connected to Linear" in cb_resp.text
        credential_store.store.assert_called_once()
        store_kwargs = credential_store.store.call_args
        assert store_kwargs[1]["name"] == "linear-oauth-token"
        assert store_kwargs[1]["data"] == {"api_key": "oauth-tok-123"}
        integration_repo.save_connection.assert_called_once()


class TestDisconnect:
    def test_404_for_missing_connection(self):
        app, _, integration_repo = _make_app()
        integration_repo.list_connections.return_value = []
        client = TestClient(app)

        resp = client.post(f"{PREFIX}/linear/disconnect", headers=AUTH)

        assert resp.status_code == 404
        assert "No connection found" in resp.json()["detail"]

    def test_success_deletes_connection_and_credential(self):
        now = datetime.now(UTC)
        connection = IntegrationConnection(
            id="conn-1",
            owner_id="u1",
            integration_type=IntegrationType.ISSUE_TRACKER,
            adapter="volundr.adapters.linear.LinearProvider",
            credential_name="linear-oauth-token",
            config={},
            enabled=True,
            created_at=now,
            updated_at=now,
            slug="linear",
        )

        defn = _make_definition(slug="linear")
        clients = {"linear": OAuthClientConfig(client_id="cid", client_secret="csec")}
        app, credential_store, integration_repo = _make_app(
            definitions=[defn],
            clients=clients,
        )
        integration_repo.list_connections.return_value = [connection]
        credential_store.get_value.return_value = {"access_token": "tok-123"}
        client = TestClient(app)

        resp = client.post(f"{PREFIX}/linear/disconnect", headers=AUTH)

        assert resp.status_code == 204
        credential_store.delete.assert_called_once_with("user", "u1", "linear-oauth-token")
        integration_repo.delete_connection.assert_called_once_with("conn-1")

    def test_disconnect_without_oauth_config_still_deletes(self):
        now = datetime.now(UTC)
        connection = IntegrationConnection(
            id="conn-2",
            owner_id="u1",
            integration_type=IntegrationType.ISSUE_TRACKER,
            adapter="some.Adapter",
            credential_name="my-cred",
            config={},
            enabled=True,
            created_at=now,
            updated_at=now,
            slug="nooauth",
        )

        app, credential_store, integration_repo = _make_app(
            definitions=[],
            clients={},
        )
        integration_repo.list_connections.return_value = [connection]
        client = TestClient(app)

        resp = client.post(f"{PREFIX}/nooauth/disconnect", headers=AUTH)

        assert resp.status_code == 204
        credential_store.delete.assert_called_once()
        integration_repo.delete_connection.assert_called_once_with("conn-2")
