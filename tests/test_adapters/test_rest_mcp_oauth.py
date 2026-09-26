"""Browser MCP authorization survives replicas and stays bound to the owner."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from niuu.adapters.openbao_oauth_credential_store import OpenBaoOAuthCredentialStore
from volundr.adapters.inbound.auth import extract_principal
from volundr.adapters.inbound.rest_mcp_oauth import create_mcp_oauth_router
from volundr.adapters.outbound.mcp_oauth import MCPOAuthDiscovery
from volundr.adapters.outbound.memory_integrations import InMemoryIntegrationRepository
from volundr.config import OAuthConfig
from volundr.domain.models import Principal


@pytest.fixture
def setup(monkeypatch):
    secrets = {}
    store = Mock(spec=OpenBaoOAuthCredentialStore)
    store.manages_oauth_applications = True
    store.supports_resource_indicators = True

    async def write(owner_type, owner_id, name, secret_type, values, metadata=None):
        secrets[owner_type, owner_id, name] = values

    store.store = AsyncMock(side_effect=write)
    store.get_value = AsyncMock(side_effect=lambda *key: secrets.get(key))
    store.delete = AsyncMock(side_effect=lambda *key: secrets.pop(key))
    store.configure_oauth_application = AsyncMock()
    repository = InMemoryIntegrationRepository()
    locks = {}

    @asynccontextmanager
    async def hold(*key):
        async with locks.setdefault(key, asyncio.Lock()):
            yield

    lock = Mock(hold=hold)
    requests = []

    def respond(request):
        requests.append(request)
        match request.url.path:
            case "/mcp":
                return httpx.Response(401)
            case "/.well-known/oauth-protected-resource/mcp":
                return httpx.Response(
                    200,
                    json={
                        "resource": "https://tools.example/mcp",
                        "authorization_servers": ["https://auth.example"],
                    },
                )
            case "/.well-known/oauth-authorization-server":
                return httpx.Response(
                    200,
                    json={
                        "issuer": "https://auth.example",
                        "authorization_endpoint": "https://auth.example/authorize",
                        "token_endpoint": "https://auth.example/token",
                        "registration_endpoint": "https://auth.example/register",
                        "code_challenge_methods_supported": ["S256"],
                        "token_endpoint_auth_methods_supported": ["none"],
                        "client_id_metadata_document_supported": True,
                    },
                )
            case "/register":
                return httpx.Response(
                    201,
                    json={
                        "client_id": "registered-client",
                        "redirect_uris": [
                            "https://niuu.example/api/v1/integrations/oauth/mcp/callback"
                        ],
                        "token_endpoint_auth_method": "none",
                    },
                )
            case "/token":
                return httpx.Response(
                    200,
                    json={
                        "access_token": "test-access",
                        "refresh_token": "test-refresh",
                        "token_type": "Bearer",
                        "expires_in": 3600,
                    },
                )
        return httpx.Response(404)

    monkeypatch.setattr(
        MCPOAuthDiscovery,
        "client",
        lambda _: httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )

    def replica(user="alice", tenant="team", internal_hosts=()):
        app = FastAPI()
        app.dependency_overrides[extract_principal] = lambda: Principal(
            user_id=user, tenant_id=tenant, email="person@example.test", roles=[]
        )
        app.include_router(
            create_mcp_oauth_router(
                OAuthConfig(
                    redirect_base_url="https://niuu.example",
                    mcp_internal_hosts=list(internal_hosts),
                ),
                store,
                repository,
                lock,
            )
        )
        return TestClient(app)

    return replica, store, requests


def test_browser_flow_cross_replica_pkce_resource_and_replay(setup):
    replica, store, requests = setup
    response = replica().post("/mcp/connect", json={"server_url": "https://tools.example/mcp"})
    assert response.status_code == 200, response.text
    authorization = parse_qs(urlsplit(response.json()["url"]).query)
    assert authorization["code_challenge_method"] == ["S256"]
    assert authorization["resource"] == ["https://tools.example/mcp"]
    params = {"state": authorization["state"][0], "code": "provider-code"}
    callback = replica().get("/mcp/callback", params=params)
    assert callback.status_code == 200
    assert "niuu:mcp-connected" in callback.text
    assert response.json()["connection_id"] in callback.text
    exchange = parse_qs(next(r for r in requests if r.url.path == "/token").content.decode())
    assert len(exchange["code_verifier"][0]) >= 43
    assert exchange["resource"] == ["https://tools.example/mcp"]
    saved = store.store.call_args
    assert saved.args[0:2] == ("user", "alice")
    assert saved.args[5]["tenant_id"] == "team"
    assert saved.args[5]["mcp_url"] == "https://tools.example/mcp"
    assert replica().get("/mcp/callback", params=params).status_code == 400
    assert len([r for r in requests if r.url.path == "/token"]) == 1


def test_client_registration_reused_and_owner_isolated(setup):
    replica, _, requests = setup
    for user in ("alice", "alice", "bob"):
        result = replica(user).post(
            "/mcp/connect", json={"server_url": "https://tools.example/mcp"}
        )
        assert result.status_code == 200, result.text
    assert len([r for r in requests if r.url.path == "/register"]) == 2


def test_api_token_connection_cannot_be_reconnected_by_another_owner(setup):
    replica, _, requests = setup
    body = {"server_url": "https://tools.example/mcp", "api_token": "test-key"}
    response = replica().post("/mcp/connect", json=body)
    assert response.status_code == 200
    body["connection_id"] = response.json()["connection_id"]
    assert replica("bob").post("/mcp/connect", json=body).status_code == 404
    assert replica(tenant="another").post("/mcp/connect", json=body).status_code == 404
    body["server_url"] = "https://other.example/mcp"
    assert replica().post("/mcp/connect", json=body).status_code == 409
    assert requests == []


@pytest.mark.asyncio
async def test_concurrent_callbacks_exchange_the_code_only_once(setup):
    replica, store, requests = setup
    get_value = store.get_value.side_effect

    async def delayed_get(*key):
        await asyncio.sleep(0)
        return get_value(*key)

    store.get_value.side_effect = delayed_get
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=replica().app), base_url="https://niuu.example"
    ) as client:
        started = await client.post(
            "/mcp/connect", json={"server_url": "https://tools.example/mcp"}
        )
        state = parse_qs(urlsplit(started.json()["url"]).query)["state"][0]
        results = await asyncio.gather(
            *(
                client.get("/mcp/callback", params={"state": state, "code": "provider-code"})
                for _ in range(2)
            )
        )
    assert sorted(response.status_code for response in results) == [200, 400]
    assert len([r for r in requests if r.url.path == "/token"]) == 1


def test_wrong_callback_issuer_never_exchanges_the_code(setup):
    replica, _, requests = setup
    started = replica().post("/mcp/connect", json={"server_url": "https://tools.example/mcp"})
    state = parse_qs(urlsplit(started.json()["url"]).query)["state"][0]
    result = replica().get(
        "/mcp/callback",
        params={"state": state, "code": "provider-code", "iss": "https://another.example"},
    )
    assert result.status_code == 400
    assert not any(r.url.path == "/token" for r in requests)


def test_unpatched_engine_cannot_accept_mcp_oauth(setup):
    replica, store, requests = setup
    store.supports_resource_indicators = False
    result = replica().post("/mcp/connect", json={"server_url": "https://tools.example/mcp"})
    assert result.status_code == 503
    assert not any(r.url.path in {"/register", "/token"} for r in requests)


def test_callback_access_logs_keep_status_but_drop_codes():
    import logging

    from volundr.adapters.inbound.rest_oauth import _OAuthAccessFilter

    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        "%s %s %s %s %s",
        (
            "client",
            "GET",
            "/api/v1/integrations/oauth/mcp/callback?code=secret&state=secret",
            "1.1",
            200,
        ),
        None,
    )
    assert _OAuthAccessFilter().filter(record)
    assert "secret" not in record.getMessage()
    assert "200" in record.getMessage()


@pytest.mark.parametrize(
    ("internal_hosts", "public_only"), [((), True), (("auth.example",), False)]
)
def test_engine_public_restriction_follows_internal_allowlist(setup, internal_hosts, public_only):
    replica, store, _ = setup
    response = replica(internal_hosts=internal_hosts).post(
        "/mcp/connect", json={"server_url": "https://tools.example/mcp"}
    )
    assert response.status_code == 200, response.text
    configured = store.configure_oauth_application.call_args.kwargs
    assert configured["public_endpoints_only"] is public_only


def test_client_metadata_self_check_ignores_internal_allowlist(setup, monkeypatch):
    replica, _, _ = setup
    patched = MCPOAuthDiscovery.client
    seen = []

    def client(self):
        http = patched(self)

        async def record(request):
            seen.append((request.url.path, self._internal_hosts))

        http.event_hooks["request"].append(record)
        return http

    monkeypatch.setattr(MCPOAuthDiscovery, "client", client)
    response = replica(internal_hosts=("niuu.example", "auth.example")).post(
        "/mcp/connect", json={"server_url": "https://tools.example/mcp"}
    )
    assert response.status_code == 200, response.text
    metadata_path = "/api/v1/integrations/oauth/mcp/client-metadata"
    assert [hosts for path, hosts in seen if path == metadata_path] == [()]
    assert [hosts for path, hosts in seen if path == "/register"] == [
        ("niuu.example", "auth.example")
    ]
