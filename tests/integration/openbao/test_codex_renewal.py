"""Real OpenBao/oauthapp rotation test with an isolated mock OAuth provider.

OPENBAO_OAUTH_PLUGIN must point to the official Linux amd64 oauthapp executable.
Requires Docker Desktop and openssl. No real user credentials are used here.
"""

import asyncio
import hashlib
import json
import os
import ssl
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs
from uuid import uuid4

import httpx
import jwt
import pytest

from niuu.adapters.openbao_credential_store import OpenBaoCredentialStore
from niuu.adapters.openbao_oauth_credential_store import OpenBaoOAuthCredentialStore
from niuu.domain.models import SecretType
from niuu.domain.oauth_credentials import oauth_credential_name
from volundr.adapters.outbound.codex_credential_broker import (
    CodexCredentialBrokerError,
    OpenBaoCodexCredentialBroker,
)

pytestmark = pytest.mark.integration


def run(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT).strip()


@pytest.fixture(params=[True, False], ids=["provider-expiry", "bounded-expiry"])
def engine(tmp_path, request):
    plugin = os.environ.get("OPENBAO_OAUTH_PLUGIN")
    if not plugin:
        pytest.skip("Set OPENBAO_OAUTH_PLUGIN to run the real engine test")
    plugin = str(Path(plugin).resolve())
    run(
        "openssl",
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        str(tmp_path / "key.pem"),
        "-out",
        str(tmp_path / "cert.pem"),
        "-days",
        "1",
        "-subj",
        "/CN=host.docker.internal",
        "-addext",
        "subjectAltName=DNS:host.docker.internal",
        "-addext",
        "basicConstraints=critical,CA:TRUE",
    )
    state = {"refresh": "seed-refresh", "calls": 0, "revoked": False}

    def token():
        return jwt.encode(
            {
                "exp": int(time.time()) + 14,
                "iat": int(time.time()),
                "jti": str(state["calls"]),
                "https://api.openai.com/auth": {
                    "chatgpt_account_id": "test-account",
                    "chatgpt_plan_type": "pro",
                },
            },
            key="",
            algorithm="none",
        )

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            form = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
            good = (
                not state["revoked"]
                and form.get("refresh_token") == [state["refresh"]]
                and form.get("client_id") == ["test-public-client"]
                and (not state.get("resource") or form.get("resource") == [state["resource"]])
            )
            if good:
                state["calls"] += 1
                state["refresh"] = "rotated-" + str(state["calls"])
                payload = {
                    "access_token": token(),
                    "refresh_token": state["refresh"],
                    "token_type": "Bearer",
                }
                if request.param:
                    payload["expires_in"] = 14
            else:
                payload = {"error": "invalid_grant"}
            self.send_response(200 if good else 400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

    server = ThreadingHTTPServer(("0.0.0.0", 0), Provider)
    tls = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain(tmp_path / "cert.pem", tmp_path / "key.pem")
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    (tmp_path / "bao.hcl").write_text('plugin_directory = "/plugins"\ndisable_mlock = true\n')
    container = "niuu-oauth-test-" + uuid4().hex[:10]
    try:
        run(
            "docker",
            "run",
            "--rm",
            "-d",
            "--platform",
            "linux/amd64",
            "--name",
            container,
            "-p",
            "127.0.0.1::8200",
            "-v",
            f"{plugin}:/plugins/oauthapp:ro",
            "-v",
            f"{tmp_path}:/config:ro",
            "-e",
            "SSL_CERT_FILE=/config/cert.pem",
            "ghcr.io/openbao/openbao:2.5.3",
            "server",
            "-dev",
            "-dev-root-token-id=test-root",
            "-dev-listen-address=0.0.0.0:8200",
            "-config=/config/bao.hcl",
        )
        port = run("docker", "port", container, "8200/tcp").rsplit(":", 1)[1]
        base = f"http://127.0.0.1:{port}"
        with httpx.Client(base_url=base, headers={"X-Vault-Token": "test-root"}) as client:
            for _ in range(100):
                try:
                    if client.get("/v1/sys/health").status_code == 200:
                        break
                except httpx.RequestError:
                    pass
                time.sleep(0.1)
            else:
                pytest.fail("OpenBao did not become healthy: " + run("docker", "logs", container))
            for path, data in [
                (
                    "sys/plugins/catalog/secret/oauthapp",
                    {
                        "sha256": hashlib.sha256(Path(plugin).read_bytes()).hexdigest(),
                        "command": "oauthapp",
                    },
                ),
                ("sys/mounts/oauthapp", {"type": "oauthapp"}),
                ("sys/mounts/volundr", {"type": "kv", "options": {"version": "2"}}),
                (
                    "oauthapp/config",
                    {
                        "tune_refresh_check_interval_seconds": 0,
                        "tune_reap_check_interval_seconds": 0,
                    },
                ),
                (
                    "oauthapp/servers/niuu-codex-subscription",
                    {
                        "provider": "custom",
                        "client_id": "test-public-client",
                        "provider_options": {
                            "token_url": f"https://host.docker.internal:{server.server_port}/token",
                            "auth_style": "in_params",
                        },
                    },
                ),
            ]:
                response = client.post("/v1/" + path, json=data)
                assert response.is_success, (path, response.status_code, response.text)
        yield base, state, token
    finally:
        subprocess.run(
            ["docker", "rm", "-f", container],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        server.shutdown()
        server.server_close()
        worker.join()


async def test_import_rotation_parallel_readers_and_revocation(engine):
    base, state, token = engine
    store = OpenBaoOAuthCredentialStore(
        url=base,
        token="test-root",
        oauth_mount_path="oauthapp",
        codex_oauth_server="niuu-codex-subscription",
        minimum_seconds=1,
        codex_maximum_expiry_seconds=14,
    )
    try:
        # Seed the legacy nested document exactly as the old broker stored it.
        auth = json.dumps(
            {
                "tokens": {
                    "access_token": token(),
                    "refresh_token": "seed-refresh",
                    "account_id": "test-account",
                }
            }
        )
        await OpenBaoCredentialStore.store(
            store,
            "user",
            "alice",
            "codex",
            SecretType.OAUTH_TOKEN,
            {"auth.json": auth},
            {"integration": "codex"},
        )
        assert await store.migrate_codex_credential(
            owner_id="alice", tenant_id="tenant-a", name="codex"
        )
        assert state["calls"] == 1
        raw = await OpenBaoCredentialStore.get_value(store, "user", "alice", "codex")
        assert raw == {}
        broker = OpenBaoCodexCredentialBroker(credential_store=store)
        kwargs = dict(
            owner_id="alice",
            tenant_id="tenant-a",
            credential_name="codex",
            credential_field="auth.json",
        )
        first = await broker.get_tokens(**kwargs)
        assert first.account_id == "test-account"
        assert first.plan_type == "pro"
        assert not await store.migrate_codex_credential(
            owner_id="alice", tenant_id="tenant-a", name="codex"
        )
        assert state["calls"] == 1
        await asyncio.sleep(5)  # Enter oauthapp's native ten-second renewal window.
        results = await asyncio.gather(*(broker.get_tokens(**kwargs) for _ in range(8)))
        assert state["calls"] == 2
        assert all(result.access_token != first.access_token for result in results)
        assert len({result.access_token for result in results}) == 1
        with pytest.raises(CodexCredentialBrokerError):
            await broker.get_tokens(**{**kwargs, "tenant_id": "tenant-b"})
        path = "oauthapp/creds/" + oauth_credential_name("tenant-a", "alice", "codex")
        await store._request(
            "put",
            "/v1/sys/policies/acl/test-session",
            json={"policy": f'path "{path}" {{ capabilities = ["read"] }}'},
        )
        issued = await store._request(
            "post",
            "/v1/auth/token/create",
            json={"policies": ["test-session"], "no_default_policy": True},
        )
        session_token = issued.json()["auth"]["client_token"]
        async with httpx.AsyncClient(
            base_url=base, headers={"X-Vault-Token": session_token}
        ) as client:
            allowed = await client.get("/v1/" + path)
            assert allowed.status_code == 200
            assert "refresh_token" not in allowed.text
            assert (await client.get("/v1/oauthapp/creds/another-user")).status_code == 403
            assert (
                await client.get("/v1/oauthapp/servers/niuu-codex-subscription")
            ).status_code == 403
        state["revoked"] = True
        await asyncio.sleep(5)
        with pytest.raises(CodexCredentialBrokerError) as exc:
            await broker.get_tokens(**kwargs)
        assert exc.value.reconnect
    finally:
        await store.close()


async def test_mcp_engine_rejects_private_discovered_token_endpoint(engine):
    from niuu.domain.oauth_credentials import OAuthCredentialUnavailableError

    base, state, _ = engine
    store = OpenBaoOAuthCredentialStore(
        url=base,
        token="test-root",
        oauth_mount_path="oauthapp",
        manage_oauth_applications=True,
        mcp_resource_indicators=True,
    )
    try:
        await store.configure_oauth_application(
            slug="mcp",
            app="private",
            client_id="test-public-client",
            client_secret="",
            authorize_url="https://127.0.0.1/authorize",
            token_url="https://127.0.0.1/token",
            token_endpoint_auth_method="none",
            public_endpoints_only=True,
        )
        with pytest.raises(OAuthCredentialUnavailableError):
            await store.store(
                "user",
                "alice",
                "private-mcp",
                SecretType.OAUTH_TOKEN,
                {"refresh_token": "seed-refresh"},
                {
                    "integration": "mcp",
                    "tenant_id": "tenant-a",
                    "oauth_app": "private",
                    "mcp_resource": "https://mcp.example.test/tools",
                },
            )
        assert state["calls"] == 0
    finally:
        await store.close()


async def test_mcp_resource_survives_engine_import_and_parallel_refresh(engine):
    base, state, _ = engine
    state["resource"] = "https://mcp.example.test/tools"
    store = OpenBaoOAuthCredentialStore(
        url=base,
        token="test-root",
        oauth_mount_path="oauthapp",
        oauth_servers={"mcp/default": "niuu-codex-subscription"},
        mcp_resource_indicators=True,
        minimum_seconds=1,
        maximum_expiry_seconds=14,
    )
    try:
        await store.store(
            "user",
            "alice",
            "mcp-tools",
            SecretType.OAUTH_TOKEN,
            {"access_token": "first", "refresh_token": "seed-refresh"},
            {
                "integration": "mcp",
                "tenant_id": "tenant-a",
                "oauth_app": "default",
                "oauth_token_field": "access_token",
                "mcp_resource": state["resource"],
            },
        )
        assert state["calls"] == 1
        await asyncio.sleep(5)
        values = await asyncio.gather(
            *(store.get_value("user", "alice", "mcp-tools") for _ in range(8))
        )
        assert state["calls"] == 2
        assert len({value["access_token"] for value in values}) == 1
        assert all("refresh_token" not in value for value in values)
    finally:
        await store.close()


async def test_flat_integration_migration_rotation_and_revocation(engine):
    from niuu.domain.oauth_credentials import OAuthCredentialUnavailableError

    base, state, _ = engine
    store = OpenBaoOAuthCredentialStore(
        url=base,
        token="test-root",
        oauth_mount_path="oauthapp",
        oauth_servers={"integration/default": "niuu-codex-subscription"},
        minimum_seconds=1,
        maximum_expiry_seconds=14,
    )
    try:
        await OpenBaoCredentialStore.store(
            store,
            "user",
            "alice",
            "integration",
            SecretType.OAUTH_TOKEN,
            {"token": "old-access", "refresh_token": "seed-refresh", "cloud_id": "site"},
            {"integration": "integration"},
        )
        args = dict(
            owner_id="alice",
            tenant_id="tenant-a",
            name="integration",
            integration="integration",
            oauth_app="default",
            token_field="token",
        )
        assert await store.migrate_oauth_credential(**args)
        first = await store.get_value("user", "alice", "integration")
        assert not await store.migrate_oauth_credential(**args)
        assert state["calls"] == 1
        assert await OpenBaoCredentialStore.get_value(store, "user", "alice", "integration") == {
            "cloud_id": "site"
        }
        await asyncio.sleep(5)
        values = await asyncio.gather(
            *(store.get_value("user", "alice", "integration") for _ in range(8))
        )
        assert state["calls"] == 2
        assert len({value["token"] for value in values}) == 1
        assert values[0]["token"] != first["token"]
        assert all(value["cloud_id"] == "site" and "refresh_token" not in value for value in values)
        state["revoked"] = True
        await asyncio.sleep(5)
        with pytest.raises(OAuthCredentialUnavailableError) as exc:
            await store.get_value("user", "alice", "integration")
        assert exc.value.reconnect
    finally:
        await store.close()
