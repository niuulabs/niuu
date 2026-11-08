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
    assert claims["act"] == {"sub": WORKLOAD_SUBJECT}
    assert claims["workload_cluster"] == "ymir"
    assert claims["resource_access"]["volundr"]["roles"] == ["admin", "volundr:developer"]
    assert result.principal.user_id == OWNER_ID
    assert result.workload_name == "ravn-valkyrie"


def _fixed_mapping(*, name: str, subject: str, owner_id: str) -> SimpleNamespace:
    """An exact-subject mapping with a fixed owner_id — the shape a
    Fleet-managed, non-UUID-named ServiceAccount (e.g. valhalla's
    resident-muninn) would use, distinct from residentMapping's broad,
    per-runtime-UUID prefix mapping."""
    return SimpleNamespace(
        name=name,
        verifier="kubernetes",
        subject=subject,
        subject_prefix="",
        issuer=WORKLOAD_ISSUER,
        claims={},
        owner_id=owner_id,
        tenant_id="default",
        email="",
        roles=["volundr:developer"],
        metadata={},
    )


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
async def test_workload_identity_exchange_never_grants_the_issuer_only_preview_scope() -> None:
    proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    service = _service(proof_key)

    result = await service.exchange(
        _workload_token(proof_key),
        scopes=["forge:session:create", "forge:session:preview"],
    )

    jwk = PyJWKSet.from_dict(service.jwks()).keys[0]
    claims = jwt.decode(
        result.token,
        key=jwk.key,
        algorithms=["RS256"],
        audience="volundr-api",
        issuer=EXCHANGE_ISSUER,
    )
    assert claims["scopes"] == ["forge:session:create"]


@pytest.mark.asyncio
async def test_workload_identity_exchange_asking_only_for_preview_yields_a_plain_token() -> None:
    proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    service = _service(proof_key)

    result = await service.exchange(
        _workload_token(proof_key),
        scopes=["forge:session:preview"],
    )

    jwk = PyJWKSet.from_dict(service.jwks()).keys[0]
    claims = jwt.decode(
        result.token,
        key=jwk.key,
        algorithms=["RS256"],
        audience="volundr-api",
        issuer=EXCHANGE_ISSUER,
    )
    assert "scopes" not in claims
    assert "token_use" not in claims


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
            "--set",
            "bifrost.url=http://bifrost.test",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"helm template failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return result.stdout


def _prefix_mapping(*, name: str, subject_prefix: str, owner_id: str) -> SimpleNamespace:
    mapping = _fixed_mapping(name=name, subject="", owner_id=owner_id)
    mapping.subject_prefix = subject_prefix
    return mapping


@pytest.mark.asyncio
async def test_exact_subject_mapping_beats_an_earlier_prefix_mapping() -> None:
    """The most specific mapping wins, not the first one listed."""
    proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    service = _service(proof_key)
    service._config.mappings[:] = [
        _prefix_mapping(
            name="namespace-wide",
            subject_prefix="system:serviceaccount:valkyrie:",
            owner_id="shared",
        ),
        _fixed_mapping(name="ravn", subject=WORKLOAD_SUBJECT, owner_id="ravn-owner"),
    ]

    result = await service.exchange(_workload_token(proof_key))

    assert result.principal.user_id == "ravn-owner"
    assert result.workload_name == "ravn"


@pytest.mark.asyncio
async def test_longer_prefix_beats_a_shorter_one_listed_first() -> None:
    proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    service = _service(proof_key)
    service._config.mappings[:] = [
        _prefix_mapping(
            name="namespace-wide", subject_prefix="system:serviceaccount:skuld:", owner_id="shared"
        ),
        _prefix_mapping(
            name="sessions",
            subject_prefix="system:serviceaccount:skuld:openbao-session-",
            owner_id="sessions-owner",
        ),
    ]

    result = await service.exchange(
        _workload_token(proof_key, subject="system:serviceaccount:skuld:openbao-session-abc")
    )

    assert result.principal.user_id == "sessions-owner"


@pytest.mark.asyncio
async def test_equally_specific_matches_are_refused() -> None:
    """Two mappings admitting the same caller at the same rank is a misconfiguration."""
    proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    service = _service(proof_key)
    service._config.mappings[:] = [
        _fixed_mapping(name="first", subject=WORKLOAD_SUBJECT, owner_id="a"),
        _fixed_mapping(name="second", subject=WORKLOAD_SUBJECT, owner_id="b"),
    ]

    with pytest.raises(WorkloadIdentityError, match="several equally specific.*first, second"):
        await service.exchange(_workload_token(proof_key))


@pytest.mark.asyncio
async def test_mapping_without_issuer_admits_no_one() -> None:
    proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    service = _service(proof_key)
    service._config.mappings[0].issuer = ""

    with pytest.raises(WorkloadIdentityError, match="'ravn-valkyrie' has no issuer"):
        await service.exchange(_workload_token(proof_key))


@pytest.mark.asyncio
async def test_mapping_without_subject_or_prefix_admits_no_one() -> None:
    """A claims-only mapping would admit every ServiceAccount the verifier accepts."""
    proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    service = _service(proof_key)
    service._config.mappings[0].subject = ""

    with pytest.raises(WorkloadIdentityError, match="neither subject nor subject_prefix"):
        await service.exchange(_workload_token(proof_key))


@pytest.mark.asyncio
async def test_prefix_must_end_on_a_separator() -> None:
    """A bare "...:muninn" prefix would also admit "...:muninn-anything"."""
    proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    service = _service(proof_key)
    service._config.mappings[0].subject = ""
    service._config.mappings[0].subject_prefix = "system:serviceaccount:valkyrie:rav"
    service._config.mappings[0].claims = {}

    with pytest.raises(WorkloadIdentityError, match="must end on one of"):
        await service.exchange(_workload_token(proof_key))


def test_token_that_speaks_for_its_own_subject_carries_no_actor() -> None:
    """``act`` names a different party acting for ``sub``; a self-issued token has none."""
    proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    service = _service(proof_key)

    issued = service.issue_token(
        principal=Principal(user_id="node-owner", email="", tenant_id="default", roles=[]),
        workload_subject="node-owner",
        workload_name="guild-node-pairing",
        audiences=["guild"],
    )

    assert "act" not in service.verify_token(issued.token)


@pytest.mark.asyncio
async def test_exchange_logs_which_workload_it_spoke_for(caplog: pytest.LogCaptureFixture) -> None:
    proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    service = _service(proof_key)

    with caplog.at_level("INFO", logger="niuu.domain.services.workload_identity"):
        result = await service.exchange(_workload_token(proof_key))

    line = next(r.getMessage() for r in caplog.records if "Workload exchange" in r.getMessage())
    assert "mapping=ravn-valkyrie" in line
    assert f"workload={WORKLOAD_SUBJECT}" in line
    assert f"owner={OWNER_ID}" in line
    assert result.token not in line


class _RecordingVerifier:
    """A verifier for one cluster that records being asked and answers with *outcome*."""

    def __init__(self, outcome: dict[str, Any] | Exception) -> None:
        self.outcome = outcome
        self.asked = 0

    async def verify(self, token: str) -> dict[str, Any]:
        self.asked += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _cluster_mapping(*, name: str, verifier: str, issuer: str, subject: str) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        verifier=verifier,
        subject=subject,
        subject_prefix="",
        issuer=issuer,
        claims={},
        owner_id=OWNER_ID,
        tenant_id="default",
        email="",
        roles=["volundr:developer"],
        metadata={},
    )


def _two_cluster_service(
    jarnvidr: _RecordingVerifier, ymir: _RecordingVerifier
) -> WorkloadIdentityService:
    """ymir's mapping listed last, as on the live config: its refusal used to be
    the only reason a jarnvidr caller was given."""
    return WorkloadIdentityService(
        SimpleNamespace(
            enabled=True,
            issuer=EXCHANGE_ISSUER,
            audiences=["volundr-api"],
            mappings=[
                _cluster_mapping(
                    name="valkyrie-jarnvidr",
                    verifier="k8s-jarnvidr",
                    issuer=WORKLOAD_ISSUER,
                    subject=WORKLOAD_SUBJECT,
                ),
                _cluster_mapping(
                    name="forge-ymir",
                    verifier="k8s-ymir",
                    issuer="https://k8s-issuer.ymir.example",
                    subject="system:serviceaccount:volundr:niuu-volundr",
                ),
            ],
        ),
        verifiers={"k8s-jarnvidr": jarnvidr, "k8s-ymir": ymir},
    )


@pytest.mark.asyncio
async def test_a_refusal_names_the_callers_own_verifier_failure() -> None:
    """jarnvidr's JWKS answered 502, yet its valkyrie was told its issuer was not ymir's."""
    proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jarnvidr = _RecordingVerifier(OSError("HTTP Error 502: Bad Gateway"))
    ymir = _RecordingVerifier(jwt.InvalidIssuerError("proof issuer is not this verifier's"))
    service = _two_cluster_service(jarnvidr, ymir)

    with pytest.raises(WorkloadIdentityError) as refused:
        await service.exchange(_workload_token(proof_key))

    assert "'k8s-jarnvidr' refused the proof: HTTP Error 502" in str(refused.value)
    assert "not this verifier" not in str(refused.value)
    # A mapping for another cluster's issuer cannot admit this proof; never ask its verifier.
    assert ymir.asked == 0


@pytest.mark.asyncio
async def test_a_refusal_is_logged_with_the_callers_issuer_and_subject(
    caplog: pytest.LogCaptureFixture,
) -> None:
    proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = _workload_token(proof_key)
    jarnvidr = _RecordingVerifier(OSError("HTTP Error 502: Bad Gateway"))
    service = _two_cluster_service(jarnvidr, _RecordingVerifier({}))

    with caplog.at_level("WARNING", logger="niuu.domain.services.workload_identity"):
        with pytest.raises(WorkloadIdentityError):
            await service.exchange(token)

    [record] = [r for r in caplog.records if "Workload exchange refused" in r.getMessage()]
    line = record.getMessage()
    assert record.levelname == "WARNING"
    assert f"issuer={WORKLOAD_ISSUER}" in line
    assert f"subject={WORKLOAD_SUBJECT}" in line
    assert "HTTP Error 502" in line
    assert token not in line


@pytest.mark.asyncio
async def test_a_refusal_after_matching_is_logged_too(caplog: pytest.LogCaptureFixture) -> None:
    """Every refusal reaches the operator, not only "no mapping matched"."""
    proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    service = _service(proof_key)

    with caplog.at_level("WARNING", logger="niuu.domain.services.workload_identity"):
        with pytest.raises(WorkloadIdentityError, match="not allowed: unknown"):
            await service.exchange(_workload_token(proof_key), audiences=["unknown"])

    line = next(r.getMessage() for r in caplog.records if "refused" in r.getMessage())
    assert f"subject={WORKLOAD_SUBJECT}" in line
    assert "not allowed: unknown" in line


@pytest.mark.asyncio
async def test_an_unmapped_subject_is_named_in_the_refusal() -> None:
    proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    service = _service(proof_key)
    stranger = "system:serviceaccount:other:ravn"

    with pytest.raises(WorkloadIdentityError) as refused:
        await service.exchange(_workload_token(proof_key, subject=stranger))

    assert f"no mapping admits subject {stranger!r} from issuer {WORKLOAD_ISSUER!r}" in str(
        refused.value
    )


@pytest.mark.asyncio
async def test_a_proof_that_is_not_a_jwt_is_still_offered_to_every_verifier(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pre-filtering reads the claimed issuer; a proof without one is not dropped unseen."""
    jarnvidr = _RecordingVerifier(ValueError("not a JWT"))
    ymir = _RecordingVerifier(ValueError("not a JWT"))
    service = _two_cluster_service(jarnvidr, ymir)

    with caplog.at_level("WARNING", logger="niuu.domain.services.workload_identity"):
        with pytest.raises(WorkloadIdentityError, match="'k8s-ymir' refused the proof"):
            await service.exchange("opaque-proof")

    assert (jarnvidr.asked, ymir.asked) == (1, 1)
    line = next(r.getMessage() for r in caplog.records if "refused" in r.getMessage())
    assert "issuer=? subject=?" in line
    assert "opaque-proof" not in line


@pytest.mark.asyncio
async def test_a_verifier_for_another_issuer_refuses_without_fetching_its_keys() -> None:
    """eitri's issuer timing out froze every exchange: each proof was offered to
    every cluster's verifier, and each fetched its keys before checking issuer."""
    from niuu.adapters.workload_identity.jwt import JwtWorkloadIdentityVerifier

    proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier = JwtWorkloadIdentityVerifier(
        issuer="https://k8s-issuer.eitri.example",
        audiences=["volundr-api"],
        jwks_uri="https://k8s-issuer.eitri.example/openid/v1/jwks",
    )

    def unreachable(_token: str) -> Any:
        raise AssertionError("fetched the keys of an issuer the proof does not name")

    verifier._resolve_key = unreachable  # type: ignore[method-assign]

    with pytest.raises(jwt.InvalidIssuerError, match="eitri"):
        await verifier.verify(_workload_token(proof_key))


@pytest.mark.asyncio
async def test_a_verifier_fetches_keys_off_the_event_loop() -> None:
    """PyJWKClient blocks; on the loop, one slow issuer stops the whole host."""
    import threading

    from niuu.adapters.workload_identity.jwt import JwtWorkloadIdentityVerifier

    proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier = JwtWorkloadIdentityVerifier(
        issuer=WORKLOAD_ISSUER,
        audiences=["volundr-api"],
        static_jwks={"keys": [_jwk_from_key(proof_key, kid="k8s-proof")]},
    )
    resolve = verifier._resolve_key
    threads: list[threading.Thread] = []

    def recording(token: str) -> Any:
        threads.append(threading.current_thread())
        return resolve(token)

    verifier._resolve_key = recording  # type: ignore[method-assign]

    claims = await verifier.verify(_workload_token(proof_key))

    assert claims["sub"] == WORKLOAD_SUBJECT
    assert threads and threads[0] is not threading.main_thread()


def _granting(service: WorkloadIdentityService, scopes: list[str]) -> WorkloadIdentityService:
    """The mapping lists ``scopes`` in its privileged_scopes."""
    service._config.mappings[0].privileged_scopes = scopes
    return service


def _claims(service: WorkloadIdentityService, token: str) -> dict[str, Any]:
    jwk = PyJWKSet.from_dict(service.jwks()).keys[0]
    return jwt.decode(
        token, key=jwk.key, algorithms=["RS256"], audience="volundr-api", issuer=EXCHANGE_ISSUER
    )


class TestNotificationDeliveryScope:
    """The push gateway's cross-tenant reader: granted only where a mapping lists it."""

    async def test_a_mapping_that_lists_it_mints_a_delivery_token(self) -> None:
        proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        service = _granting(_service(proof_key), ["forge:notifications:deliver"])

        result = await service.exchange(
            _workload_token(proof_key), scopes=["forge:notifications:deliver"]
        )

        claims = _claims(service, result.token)
        assert claims["token_use"] == "notification_delivery"
        assert claims["scopes"] == ["forge:notifications:deliver"]

    async def test_a_mapping_that_does_not_list_it_is_refused_loudly(self) -> None:
        proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        service = _service(proof_key)

        with pytest.raises(WorkloadIdentityError, match="privileged_scopes"):
            await service.exchange(
                _workload_token(proof_key), scopes=["forge:notifications:deliver"]
            )

    async def test_it_never_shares_a_token_with_build_scopes(self) -> None:
        proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        service = _granting(_service(proof_key), ["forge:notifications:deliver"])

        with pytest.raises(WorkloadIdentityError, match="separate exchanges"):
            await service.exchange(
                _workload_token(proof_key),
                scopes=["forge:notifications:deliver", "forge:session:create"],
            )

    async def test_listing_it_grants_nothing_unasked(self) -> None:
        proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        service = _granting(_service(proof_key), ["forge:notifications:deliver"])

        result = await service.exchange(_workload_token(proof_key))

        assert "token_use" not in _claims(service, result.token)


class TestPrivilegedScopesConfig:
    def _mapping(self, **overrides):
        from niuu.config_models import WorkloadIdentityMappingConfig

        values = {
            "name": "forge",
            "subject": "system:serviceaccount:volundr:niuu-volundr",
            "issuer": WORKLOAD_ISSUER,
            "owner_id": OWNER_ID,
            "privileged_scopes": ["forge:notifications:deliver"],
        }
        values.update(overrides)
        return WorkloadIdentityMappingConfig(**values)

    def test_an_exact_subject_may_carry_it(self) -> None:
        assert self._mapping().privileged_scopes == ["forge:notifications:deliver"]

    def test_a_subject_prefix_may_not(self) -> None:
        with pytest.raises(ValueError, match="exact subject"):
            self._mapping(subject="", subject_prefix="system:serviceaccount:volundr:")

    def test_a_delegated_identity_may_not(self) -> None:
        with pytest.raises(ValueError, match="delegated"):
            self._mapping(owner_from="delegation")

    def test_only_privileged_scopes_may_be_listed(self) -> None:
        with pytest.raises(ValueError, match="not privileged scopes"):
            self._mapping(privileged_scopes=["forge:session:create"])
