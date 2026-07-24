from __future__ import annotations

import base64
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
import yaml
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jwt import PyJWKSet

from niuu.adapters.inbound.rest_pats import create_pats_router
from niuu.domain.models import Principal
from niuu.domain.services.workload_identity import (
    WorkloadIdentityError,
    WorkloadIdentityService,
)
from niuu.service_runtime import create_workload_identity_service

OWNER_ID = "76475334-b685-4299-b91d-1ec37f57e10f"
WORKLOAD_SUBJECT = "system:serviceaccount:valkyrie:ravn"
WORKLOAD_ISSUER = "https://kubernetes.default.svc"
EXCHANGE_ISSUER = "https://yggdrasil.niuu.world/api/v1/tokens/workload"


def _b64url_uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _jwk_from_key(key: rsa.RSAPrivateKey, *, kid: str) -> dict[str, Any]:
    public_numbers = key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": _b64url_uint(public_numbers.n),
        "e": _b64url_uint(public_numbers.e),
    }


def _workload_token(key: rsa.RSAPrivateKey, *, subject: str = WORKLOAD_SUBJECT) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": WORKLOAD_ISSUER,
            "sub": subject,
            "aud": "volundr-api",
            "iat": now,
            "nbf": now - 5,
            "exp": now + 600,
            "kubernetes.io": {
                "namespace": "valkyrie",
                "serviceaccount": {"name": "ravn"},
            },
        },
        key,
        algorithm="RS256",
        headers={"kid": "k8s-proof"},
    )


def _service(proof_key: rsa.RSAPrivateKey) -> WorkloadIdentityService:
    return create_workload_identity_service(
        SimpleNamespace(
            enabled=True,
            issuer=EXCHANGE_ISSUER,
            audiences=["volundr-api", "forge", "ting", "mimir", "guild"],
            token_ttl_seconds=900,
            key_id="niuu-workload-test",
            signing_key_pem="",
            signing_key_env="",
            verifiers=[
                SimpleNamespace(
                    name="kubernetes",
                    adapter="niuu.adapters.workload_identity.jwt.JwtWorkloadIdentityVerifier",
                    kwargs={
                        "issuer": WORKLOAD_ISSUER,
                        "audiences": ["volundr-api"],
                        "static_jwks": {"keys": [_jwk_from_key(proof_key, kid="k8s-proof")]},
                    },
                    secret_kwargs_env={},
                )
            ],
            mappings=[
                SimpleNamespace(
                    name="ravn-valkyrie",
                    verifier="kubernetes",
                    subject=WORKLOAD_SUBJECT,
                    subject_prefix="",
                    issuer=WORKLOAD_ISSUER,
                    claims={"kubernetes.io.namespace": "valkyrie"},
                    owner_id=OWNER_ID,
                    tenant_id="default",
                    email="jozef@niuu.world",
                    roles=["admin", "volundr:developer"],
                    metadata={"cluster": "ymir"},
                )
            ],
        )
    )


@pytest.mark.asyncio
async def test_workload_identity_exchange_mints_owner_scoped_token() -> None:
    proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    service = _service(proof_key)

    result = await service.exchange(_workload_token(proof_key))

    jwk = PyJWKSet.from_dict(service.jwks()).keys[0]
    claims = jwt.decode(
        result.token,
        key=jwk.key,
        algorithms=["RS256"],
        audience="volundr-api",
        issuer=EXCHANGE_ISSUER,
    )
    assert claims["sub"] == OWNER_ID
    assert claims["tenant_id"] == "default"
    assert claims["workload_sub"] == WORKLOAD_SUBJECT
    assert claims["workload_cluster"] == "ymir"
    assert claims["resource_access"]["volundr"]["roles"] == ["admin", "volundr:developer"]
    assert result.principal.user_id == OWNER_ID
    assert result.workload_name == "ravn-valkyrie"


def test_workload_identity_issues_session_bound_token_for_verified_adapter() -> None:
    proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    service = _service(proof_key)

    issued = service.issue_token(
        principal=Principal(
            user_id=OWNER_ID,
            email="jozef@niuu.world",
            tenant_id="default",
            roles=["volundr:developer"],
        ),
        workload_subject="spiffe://niuu.world/openshell/sandbox/sandbox-1",
        workload_name="openshell-session-session-1",
        audiences=["volundr-api"],
        token_use="openshell_session",
        claims={"session_id": "session-1", "sandbox_id": "sandbox-1"},
    )

    jwk = PyJWKSet.from_dict(service.jwks()).keys[0]
    claims = jwt.decode(
        issued.token,
        key=jwk.key,
        algorithms=["RS256"],
        audience="volundr-api",
        issuer=EXCHANGE_ISSUER,
    )
    assert claims["sub"] == OWNER_ID
    assert claims["token_use"] == "openshell_session"
    assert claims["workload_session_id"] == "session-1"
    assert claims["workload_sandbox_id"] == "sandbox-1"


@pytest.mark.asyncio
async def test_workload_identity_exchange_mints_requested_service_audiences() -> None:
    proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    service = _service(proof_key)

    result = await service.exchange(
        _workload_token(proof_key),
        audiences=["ting", "mimir"],
    )

    jwk = PyJWKSet.from_dict(service.jwks()).keys[0]
    claims = jwt.decode(
        result.token,
        key=jwk.key,
        algorithms=["RS256"],
        audience="mimir",
        issuer=EXCHANGE_ISSUER,
    )
    assert claims["aud"] == ["ting", "mimir"]
    assert claims["resource_access"]["ting"]["roles"] == ["admin", "volundr:developer"]
    assert claims["resource_access"]["mimir"]["roles"] == ["admin", "volundr:developer"]
    assert claims["resource_access"]["volundr-api"]["roles"] == [
        "admin",
        "volundr:developer",
    ]


@pytest.mark.asyncio
async def test_workload_identity_exchange_mints_scoped_build_token() -> None:
    proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    service = _service(proof_key)

    result = await service.exchange(
        _workload_token(proof_key),
        scopes=["forge:session:create", "ting:workflow:launch"],
    )

    jwk = PyJWKSet.from_dict(service.jwks()).keys[0]
    claims = jwt.decode(
        result.token,
        key=jwk.key,
        algorithms=["RS256"],
        audience="volundr-api",
        issuer=EXCHANGE_ISSUER,
    )
    assert claims["token_use"] == "valkyrie_build"
    assert claims["scopes"] == ["forge:session:create", "ting:workflow:launch"]


@pytest.mark.asyncio
async def test_workload_identity_exchange_drops_unknown_build_scopes() -> None:
    proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    service = _service(proof_key)

    result = await service.exchange(
        _workload_token(proof_key),
        scopes=["forge:session:create", "forge:session:delete", "*"],
    )

    jwk = PyJWKSet.from_dict(service.jwks()).keys[0]
    claims = jwt.decode(
        result.token,
        key=jwk.key,
        algorithms=["RS256"],
        audience="volundr-api",
        issuer=EXCHANGE_ISSUER,
    )
    assert claims["token_use"] == "valkyrie_build"
    assert claims["scopes"] == ["forge:session:create"]


@pytest.mark.asyncio
async def test_workload_identity_exchange_without_scopes_is_not_a_build_token() -> None:
    proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    service = _service(proof_key)

    result = await service.exchange(_workload_token(proof_key))

    jwk = PyJWKSet.from_dict(service.jwks()).keys[0]
    claims = jwt.decode(
        result.token,
        key=jwk.key,
        algorithms=["RS256"],
        audience="volundr-api",
        issuer=EXCHANGE_ISSUER,
    )
    assert "token_use" not in claims
    assert "scopes" not in claims


@pytest.mark.asyncio
async def test_workload_identity_exchange_all_unknown_scopes_yield_plain_token() -> None:
    proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    service = _service(proof_key)

    result = await service.exchange(
        _workload_token(proof_key),
        scopes=["nope", "also-nope"],
    )

    jwk = PyJWKSet.from_dict(service.jwks()).keys[0]
    claims = jwt.decode(
        result.token,
        key=jwk.key,
        algorithms=["RS256"],
        audience="volundr-api",
        issuer=EXCHANGE_ISSUER,
    )
    assert "token_use" not in claims
    assert "scopes" not in claims


def test_workload_exchange_route_mints_scoped_build_token() -> None:
    proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    app = FastAPI()
    service = _service(proof_key)
    app.state.workload_identity_service = service

    async def forbidden_principal() -> Principal:
        raise AssertionError("workload exchange must not call the user principal dependency")

    app.include_router(create_pats_router(forbidden_principal))

    response = TestClient(app).post(
        "/api/v1/tokens/workload/exchange",
        json={
            "token": _workload_token(proof_key),
            "scopes": ["forge:session:create", "forge:session:delete"],
        },
    )

    assert response.status_code == 201
    jwk = PyJWKSet.from_dict(service.jwks()).keys[0]
    claims = jwt.decode(
        response.json()["token"],
        key=jwk.key,
        algorithms=["RS256"],
        audience="volundr-api",
        issuer=EXCHANGE_ISSUER,
    )
    assert claims["token_use"] == "valkyrie_build"
    assert claims["scopes"] == ["forge:session:create"]


@pytest.mark.asyncio
async def test_workload_identity_exchange_rejects_unconfigured_audience() -> None:
    proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    service = _service(proof_key)

    with pytest.raises(WorkloadIdentityError, match="not allowed: unknown"):
        await service.exchange(_workload_token(proof_key), audiences=["unknown"])


@pytest.mark.asyncio
async def test_workload_identity_exchange_rejects_unmapped_subject() -> None:
    proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    service = _service(proof_key)

    with pytest.raises(WorkloadIdentityError, match="No workload identity mapping matched"):
        await service.exchange(
            _workload_token(proof_key, subject="system:serviceaccount:other:ravn")
        )


@pytest.mark.asyncio
async def test_workload_identity_exchange_matches_subject_prefix() -> None:
    proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    service = _service(proof_key)
    service._config.mappings[0].subject = ""
    service._config.mappings[0].subject_prefix = "system:serviceaccount:skuld:openbao-session-"
    service._config.mappings[0].claims = {}
    session_subject = "system:serviceaccount:skuld:openbao-session-abc123"

    result = await service.exchange(_workload_token(proof_key, subject=session_subject))

    assert result.workload_subject == session_subject


def test_workload_exchange_route_does_not_require_user_principal() -> None:
    proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    app = FastAPI()
    app.state.workload_identity_service = _service(proof_key)

    async def forbidden_principal() -> Principal:
        raise AssertionError("workload exchange must not call the user principal dependency")

    app.include_router(create_pats_router(forbidden_principal))

    response = TestClient(app).post(
        "/api/v1/tokens/workload/exchange",
        json={"token": _workload_token(proof_key), "audiences": ["ting", "mimir"]},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["tokenType"] == "Bearer"
    assert body["principal"]["userId"] == OWNER_ID
    assert body["workloadSubject"] == WORKLOAD_SUBJECT


@pytest.mark.parametrize(
    ("chart", "target_port"),
    [
        ("volundr", 8080),
        ("ting", 8081),
        ("guild", 8084),
        ("niuu-shared", 8082),
    ],
)
def test_workload_jwt_provider_renders_as_additional_envoy_auth_provider(
    chart: str,
    target_port: int,
) -> None:
    rendered = _render_chart(chart)
    assert "workload:" in rendered
    assert "requires_any:" in rendered
    assert "- provider_name: keycloak" in rendered
    assert "- provider_name: workload" in rendered
    assert f"http://127.0.0.1:{target_port}/api/v1/tokens/workload/jwks" in rendered
    assert "NIUU_WORKLOAD_IDENTITY_SIGNING_KEY" in rendered

    documents = list(yaml.safe_load_all(rendered))
    envoy = next(
        doc
        for doc in documents
        if doc and doc.get("metadata", {}).get("name", "").endswith("-envoy")
    )
    envoy_yaml = envoy["data"]["envoy.yaml"]
    assert 'prefix: "/api/v1/tokens/workload/jwks"' in envoy_yaml
    if chart != "guild":
        assert 'prefix: "/api/v1/tokens/workload/exchange"' in envoy_yaml


def test_skuld_accepts_workload_jwt_at_gateway_and_sidecar() -> None:
    chart_dir = Path(__file__).parent.parent / "charts" / "skuld"
    result = subprocess.run(
        [
            "helm",
            "template",
            "test",
            str(chart_dir),
            "--set",
            "envoy.enabled=true",
            "--set",
            "envoy.jwt.enabled=true",
            "--set",
            "envoy.jwt.issuer=https://keycloak.example/realms/volundr",
            "--set",
            "envoy.jwt.jwksUri=https://keycloak.example/certs",
            "--set",
            "envoy.jwt.keycloakHost=keycloak.example",
            "--set",
            "envoy.jwt.workload.enabled=true",
            "--set",
            f"envoy.jwt.workload.issuer={EXCHANGE_ISSUER}",
            "--set",
            "envoy.jwt.workload.audiences[0]=volundr-api",
            "--set",
            f"envoy.jwt.workload.jwksUri={EXCHANGE_ISSUER}/jwks",
            "--set",
            "envoy.jwt.workload.jwksHost=yggdrasil.niuu.world",
            "--set",
            "gateway.enabled=true",
            "--set",
            "gateway.jwt.enabled=true",
            "--set",
            "gateway.jwt.issuer=https://keycloak.example/realms/volundr",
            "--set",
            "gateway.jwt.audiences[0]=volundr-api",
            "--set",
            "gateway.jwt.jwksUri=https://keycloak.example/certs",
            "--set",
            "gateway.jwt.workload.enabled=true",
            "--set",
            f"gateway.jwt.workload.issuer={EXCHANGE_ISSUER}",
            "--set",
            "gateway.jwt.workload.audiences[0]=volundr-api",
            "--set",
            f"gateway.jwt.workload.jwksUri={EXCHANGE_ISSUER}/jwks",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"helm template failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")

    assert "workload:" in result.stdout
    assert "requires_any:" in result.stdout
    assert "- provider_name: keycloak" in result.stdout
    assert "- provider_name: workload" in result.stdout
    assert "cluster: workload_jwks" in result.stdout
    assert 'address: "yggdrasil.niuu.world"' in result.stdout

    documents = list(yaml.safe_load_all(result.stdout))
    security_policy = next(doc for doc in documents if doc and doc.get("kind") == "SecurityPolicy")
    providers = security_policy["spec"]["jwt"]["providers"]
    assert [provider["name"] for provider in providers] == ["volundr-idp", "workload"]


def test_bifrost_accepts_workload_jwt_provider() -> None:
    chart_dir = Path(__file__).parent.parent / "charts" / "bifrost"
    result = subprocess.run(
        [
            "helm",
            "template",
            "test",
            str(chart_dir),
            "--set",
            "envoy.enabled=true",
            "--set",
            "envoy.jwt.enabled=true",
            "--set",
            "envoy.jwt.issuer=https://keycloak.example/realms/volundr",
            "--set",
            "envoy.jwt.jwksUri=https://keycloak.example/certs",
            "--set",
            "envoy.jwt.keycloakHost=keycloak.example",
            "--set",
            "envoy.jwt.workload.enabled=true",
            "--set",
            f"envoy.jwt.workload.issuer={EXCHANGE_ISSUER}",
            "--set",
            "envoy.jwt.workload.audiences[0]=volundr-api",
            "--set",
            f"envoy.jwt.workload.jwksUri={EXCHANGE_ISSUER}/jwks",
            "--set",
            "envoy.jwt.workload.jwksHost=yggdrasil.niuu.world",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"helm template failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")

    assert "workload:" in result.stdout
    assert "requires_any:" in result.stdout
    assert "- provider_name: keycloak" in result.stdout
    assert "- provider_name: workload" in result.stdout
    assert "cluster: workload_jwks" in result.stdout
    assert 'address: "yggdrasil.niuu.world"' in result.stdout


def test_ting_accepts_external_workload_jwks_provider() -> None:
    chart_dir = Path(__file__).parent.parent / "charts" / "ting"
    result = subprocess.run(
        [
            "helm",
            "template",
            "test",
            str(chart_dir),
            "--set",
            "envoy.enabled=true",
            "--set",
            "envoy.jwt.enabled=true",
            "--set",
            "envoy.jwt.issuer=https://keycloak.example/realms/volundr",
            "--set",
            "envoy.jwt.jwksUri=https://keycloak.example/certs",
            "--set",
            "envoy.jwt.keycloakHost=keycloak.example",
            "--set",
            "envoy.jwt.workload.enabled=true",
            "--set",
            f"envoy.jwt.workload.issuer={EXCHANGE_ISSUER}",
            "--set",
            "envoy.jwt.workload.audiences[0]=volundr-api",
            "--set",
            f"envoy.jwt.workload.jwksUri={EXCHANGE_ISSUER}/jwks",
            "--set",
            "envoy.jwt.workload.jwksHost=yggdrasil.niuu.world",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"helm template failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")

    assert "workload:" in result.stdout
    assert "requires_any:" in result.stdout
    assert "- provider_name: keycloak" in result.stdout
    assert "- provider_name: workload" in result.stdout
    assert "cluster: workload_jwks" in result.stdout
    assert 'address: "yggdrasil.niuu.world"' in result.stdout


def test_ravn_accepts_external_workload_jwks_provider() -> None:
    chart_dir = Path(__file__).parent.parent / "charts" / "ravn"
    result = subprocess.run(
        [
            "helm",
            "template",
            "test",
            str(chart_dir),
            "--set",
            "envoy.enabled=true",
            "--set",
            "envoy.jwt.enabled=true",
            "--set",
            "envoy.jwt.issuer=https://keycloak.example/realms/volundr",
            "--set",
            "envoy.jwt.jwksUri=https://keycloak.example/certs",
            "--set",
            "envoy.jwt.keycloakHost=keycloak.example",
            "--set",
            "envoy.jwt.workload.enabled=true",
            "--set",
            f"envoy.jwt.workload.issuer={EXCHANGE_ISSUER}",
            "--set",
            "envoy.jwt.workload.audiences[0]=volundr-api",
            "--set",
            f"envoy.jwt.workload.jwksUri={EXCHANGE_ISSUER}/jwks",
            "--set",
            "envoy.jwt.workload.jwksHost=yggdrasil.niuu.world",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"helm template failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")

    assert "workload:" in result.stdout
    assert "requires_any:" in result.stdout
    assert "- provider_name: keycloak" in result.stdout
    assert "- provider_name: workload" in result.stdout
    assert "cluster: workload_jwks" in result.stdout
    assert 'address: "yggdrasil.niuu.world"' in result.stdout


def test_observatory_accepts_forwarded_workload_identity() -> None:
    chart_dir = Path(__file__).parent.parent / "charts" / "observatory"
    result = subprocess.run(
        [
            "helm",
            "template",
            "test",
            str(chart_dir),
            "--set",
            "envoy.enabled=true",
            "--set",
            "envoy.jwt.enabled=true",
            "--set",
            "envoy.jwt.issuer=https://keycloak.example/realms/volundr",
            "--set",
            "envoy.jwt.jwksUri=https://keycloak.example/certs",
            "--set",
            "envoy.jwt.keycloakHost=keycloak.example",
            "--set",
            "envoy.jwt.workload.enabled=true",
            "--set",
            f"envoy.jwt.workload.issuer={EXCHANGE_ISSUER}",
            "--set",
            "envoy.jwt.workload.audiences[0]=volundr-api",
            "--set",
            f"envoy.jwt.workload.jwksUri={EXCHANGE_ISSUER}/jwks",
            "--set",
            "envoy.jwt.workload.jwksHost=yggdrasil.niuu.world",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"helm template failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")

    assert "workload:" in result.stdout
    assert "requires_any:" in result.stdout
    assert "- provider_name: keycloak" in result.stdout
    assert "- provider_name: workload" in result.stdout
    assert "cluster: workload_jwks" in result.stdout
    assert 'address: "yggdrasil.niuu.world"' in result.stdout


def _render_chart(chart: str) -> str:
    chart_dir = Path(__file__).parent.parent / "charts" / chart
    result = subprocess.run(
        [
            "helm",
            "template",
            "test",
            str(chart_dir),
            "--set",
            "envoy.enabled=true",
            "--set",
            "envoy.jwt.enabled=true",
            "--set",
            "envoy.jwt.issuer=https://keycloak.example/realms/volundr",
            "--set",
            "envoy.jwt.jwksUri=https://keycloak.example/certs",
            "--set",
            "envoy.jwt.keycloakHost=keycloak.example",
            "--set",
            "envoy.jwt.workload.enabled=true",
            "--set",
            f"envoy.jwt.workload.issuer={EXCHANGE_ISSUER}",
            "--set",
            "envoy.jwt.workload.audiences[0]=volundr-api",
            "--set",
            "workloadIdentity.enabled=true",
            "--set",
            f"workloadIdentity.issuer={EXCHANGE_ISSUER}",
            "--set",
            "workloadIdentity.signingKey.existingSecret=workload-key",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"helm template failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return result.stdout
