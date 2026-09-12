"""Opt-in live JWT -> Envoy -> Cedar -> upstream proof.

Run RUN_ENVOY_CEDAR=1 pytest tests/test_charts/test_cedar_envoy_live.py.
Requires Docker Desktop (host.docker.internal), Helm, and the chart's Envoy image.
All identities, keys, and upstream responses here are test fixtures.
"""

import asyncio
import json
import os
import ssl
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import grpc
import httpx
import jwt
import pytest
import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from envoy.service.auth.v3.external_auth_pb2_grpc import add_AuthorizationServicer_to_server

from identity.adapters.cedar import CedarAuthorizationAdapter
from identity.adapters.envoy_authz import EnvoyAuthorizationService
from identity.authz_config import AuthorizationGatewayConfig
from tests.test_charts.test_cedar_authz import envoy_config, render_cedar

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_ENVOY_CEDAR") != "1", reason="Opt-in Docker Desktop Envoy proof"
)


@pytest.mark.parametrize(
    ("chart_name", "tls_case"),
    [
        ("volundr", "trusted"),
        ("volundr", "wrong-host"),
        ("volundr", "untrusted-ca"),
        ("niuu-shared", "trusted"),
        ("ting", "trusted"),
        ("skuld", "trusted"),
    ],
)
async def test_real_envoy_enforces_jwt_and_cedar_before_upstream(tmp_path, chart_name, tls_case):
    documents = render_cedar(
        chart=Path(__file__).parents[2] / "charts" / chart_name,
        **{
            "session.id": "one",
            "session.ownerId": "alice",
            "session.tenantId": "acme",
            "envoy.port": "8443",
            "envoy.jwt.workload.jwksTls": "true",
            "envoy.jwt.rolesClaim": "resource_access.volundr.roles",
            "envoy.jwt.workload.enabled": "true",
            "envoy.jwt.workload.issuer": "https://workload.test",
            "envoy.jwt.workload.audiences[0]": "forge",
            "envoy.jwt.workload.rolesClaim": "resource_access.volundr.roles",
            "envoy.jwt.workload.jwksUri": "https://issuer.test/workload/jwks",
            "envoy.jwt.workload.jwksHost": "issuer.test",
        },
    )
    config = envoy_config(documents)
    settings = AuthorizationGatewayConfig.model_validate(
        next(
            yaml.safe_load(d["data"]["authz.yaml"])
            for d in documents
            if d["kind"] == "ConfigMap" and "authz.yaml" in d["data"]
        )
    )
    calls = []

    class Upstream(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            calls.append(dict(self.headers))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        do_OPTIONS = do_GET  # noqa: N815
        do_POST = do_GET  # noqa: N815 — standard HTTP handler name

        def log_message(self, *args):
            pass

    upstream = ThreadingHTTPServer(("0.0.0.0", 0), Upstream)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    server = grpc.aio.server()
    add_AuthorizationServicer_to_server(
        EnvoyAuthorizationService(CedarAuthorizationAdapter(), settings),
        server,
    )
    # Test-only externally reachable binding lets Docker Desktop reach the fixture.
    port = server.add_insecure_port("0.0.0.0:0")
    await server.start()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="test", use="sig", alg="RS256")
    tls_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-ca")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(tls_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("attacker.test" if tls_case == "wrong-host" else "issuer.test")]
            ),
            critical=False,
        )
        .sign(tls_key, hashes.SHA256())
    )
    cert_path = tmp_path / "ca.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path = tmp_path / "tls-key.pem"
    key_path.write_bytes(
        tls_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )

    class JWKS(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"keys": [jwk]}).encode())

        def log_message(self, *args):
            pass

    jwks_server = ThreadingHTTPServer(("0.0.0.0", 0), JWKS)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert_path, key_path)
    jwks_server.socket = tls.wrap_socket(jwks_server.socket, server_side=True)
    jwks_thread = threading.Thread(target=jwks_server.serve_forever, daemon=True)
    jwks_thread.start()
    for cluster in config["static_resources"]["clusters"]:
        cluster["type"] = "STRICT_DNS"
        address = cluster["load_assignment"]["endpoints"][0]["lb_endpoints"][0]["endpoint"][
            "address"
        ]["socket_address"]
        address.update(
            address="host.docker.internal",
            port_value={
                "cedar_authz": port,
                "local_service": upstream.server_port,
                "local_nginx": upstream.server_port,
                "keycloak": jwks_server.server_port,
                "workload_jwks": jwks_server.server_port,
            }[cluster["name"]],
        )
        if cluster["name"] in ("keycloak", "workload_jwks") and tls_case != "untrusted-ca":
            cluster["transport_socket"]["typed_config"]["common_tls_context"]["validation_context"][
                "trusted_ca"
            ]["filename"] = "/etc/test-ca.pem"
    path = tmp_path / "envoy.yaml"
    path.write_text(yaml.safe_dump(config))
    image = next(
        c["image"]
        for d in documents
        if d["kind"] == "Deployment"
        for c in d["spec"]["template"]["spec"]["containers"]
        if c["name"] == "envoy"
    )
    container = None
    try:
        container = subprocess.check_output(
            [
                "docker",
                "run",
                "--rm",
                "-d",
                "-p",
                "127.0.0.1::8443",
                "-v",
                f"{path}:/etc/envoy/envoy.yaml:ro",
                "-v",
                f"{cert_path}:/etc/test-ca.pem:ro",
                image,
                "-c",
                "/etc/envoy/envoy.yaml",
                "--concurrency",
                "1",
            ],
            text=True,
        ).strip()
        exposed = subprocess.check_output(["docker", "port", container, "8443"], text=True).strip()
        async with httpx.AsyncClient(base_url=f"http://{exposed}", timeout=5) as client:
            for _ in range(100):
                try:
                    if (await client.get("/health")).status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                await asyncio.sleep(0.1)
            else:
                pytest.fail(subprocess.check_output(["docker", "logs", container], text=True))

            def token(**overrides):
                return jwt.encode(
                    {
                        "iss": "https://issuer.test",
                        "aud": "forge",
                        "sub": "alice",
                        "tenant_id": "acme",
                        "exp": int(time.time()) + 60,
                        "resource_access": {"volundr": {"roles": ["volundr:developer"]}},
                        **overrides,
                    },
                    key,
                    algorithm="RS256",
                    headers={"kid": "test"},
                )

            route = {
                "skuld": "/api/message",
                "volundr": "/api/v1/forge/sessions",
                "niuu-shared": "/api/v1/credentials",
                "ting": "/api/v1/ting/workflows/00000000-0000-0000-0000-000000000000/launch",
            }[chart_name]
            calls.clear()
            if tls_case != "trusted":
                response = await client.get(route, headers={"Authorization": f"Bearer {token()}"})
                assert response.status_code in (401, 403, 503)
                response = await client.get(
                    route, headers={"Authorization": f"Bearer {token(iss='https://workload.test')}"}
                )
                assert response.status_code in (401, 403, 503)
                assert not calls
                return
            assert (await client.get(route, headers={"x-auth-user-id": "admin"})).status_code == 401
            assert not calls
            for invalid in [token(exp=1), token(aud="other"), token(iss="https://evil.test")]:
                assert (
                    await client.get(route, headers={"Authorization": f"Bearer {invalid}"})
                ).status_code in (401, 403)
            assert not calls
            viewer = token(resource_access={"volundr": {"roles": ["volundr:viewer"]}})
            assert (
                await client.post(route, headers={"Authorization": f"Bearer {viewer}"})
            ).status_code == 403
            assert not calls
            scoped = token(
                token_use="valkyrie_build",
                scopes=["ting:workflow:launch" if chart_name == "ting" else "forge:session:create"],
            )
            assert (
                await client.get(route, headers={"Authorization": f"Bearer {scoped}"})
            ).status_code == 403
            assert not calls
            headers = {"Authorization": f"Bearer {token()}", "x-auth-user-id": "forged"}
            assert (await client.get(route, headers=headers)).status_code == 200
            assert calls[-1]["x-auth-user-id"] == "alice"
            if chart_name == "skuld":
                assert (await client.get(route, params={"token": token()})).status_code == 200
                assert (await client.options(route)).status_code == 200
            assert (
                await client.post(route, headers={"Authorization": f"Bearer {scoped}"})
            ).status_code == (200 if chart_name in ("volundr", "ting") else 403)
            assert (
                await client.get(
                    route, headers={"Authorization": f"Bearer {token(iss='https://workload.test')}"}
                )
            ).status_code == 200
            count = len(calls)
            if chart_name == "skuld":
                for denied in [token(sub="bob"), token(tenant_id="other"), viewer]:
                    for target in [
                        "/api/message",
                        "/terminal",
                        "/session",
                        "/api/files/presented/one",
                    ]:
                        assert (
                            await client.get(target, headers={"Authorization": f"Bearer {denied}"})
                        ).status_code == 403
            else:
                assert (await client.get("/unknown", headers=headers)).status_code == 403
            assert len(calls) == count
            await server.stop(0)
            assert (await client.get(route, headers=headers)).status_code == 503
            assert len(calls) == count
    finally:
        if container:
            subprocess.run(["docker", "rm", "-f", container], check=False, capture_output=True)
        await server.stop(0)
        upstream.shutdown()
        upstream.server_close()
        thread.join()
        jwks_server.shutdown()
        jwks_server.server_close()
        jwks_thread.join()
