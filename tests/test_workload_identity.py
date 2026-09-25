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


def _service_with_owner_id_claim(
    proof_key: rsa.RSAPrivateKey,
    *,
    owner_id_claim: str = "sub",
    owner_id_claim_pattern: str = "",
    extra_mappings: list[SimpleNamespace] | None = None,
) -> WorkloadIdentityService:
    return create_workload_identity_service(
        SimpleNamespace(
            enabled=True,
            issuer=EXCHANGE_ISSUER,
            audiences=["volundr-api"],
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
                    name="ravn-resident",
                    verifier="kubernetes",
                    subject="",
                    subject_prefix="system:serviceaccount:valkyrie:resident-",
                    issuer=WORKLOAD_ISSUER,
                    claims={},
                    owner_id="",
                    owner_id_claim=owner_id_claim,
                    owner_id_claim_pattern=owner_id_claim_pattern,
                    tenant_id="default",
                    email="",
                    roles=["volundr:developer"],
                    metadata={},
                ),
                *(extra_mappings or []),
            ],
        )
    )


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
        owner_id_claim="",
        owner_id_claim_pattern="",
        tenant_id="default",
        email="",
        roles=["volundr:developer"],
        metadata={},
    )


@pytest.mark.asyncio
async def test_owner_id_claim_gives_each_distinct_subject_its_own_principal() -> None:
    """Two callers sharing one mapping's subject_prefix must not be conflated."""
    proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    service = _service_with_owner_id_claim(proof_key)

    result_a = await service.exchange(
        _workload_token(proof_key, subject="system:serviceaccount:valkyrie:resident-aaa")
    )
    result_b = await service.exchange(
        _workload_token(proof_key, subject="system:serviceaccount:valkyrie:resident-bbb")
    )

    assert result_a.principal.user_id == "system:serviceaccount:valkyrie:resident-aaa"
    assert result_b.principal.user_id == "system:serviceaccount:valkyrie:resident-bbb"
    assert result_a.principal.user_id != result_b.principal.user_id


class _FakeTenantResolver:
    """A fake OwnerTenantResolverPort standing in for
    volundr.adapters.outbound.resident_tenant_resolver's real, asyncpg-backed
    one — a real Postgres connection is out of reach here (database.md:
    mock/patch asyncpg, no Docker for tests)."""

    def __init__(self, tenants: dict[str, str]) -> None:
        self._tenants = tenants

    async def tenant_id_for_owner(self, owner_id: str) -> str | None:
        return self._tenants.get(owner_id)


def _service_with_tenant_resolver(
    proof_key: rsa.RSAPrivateKey, *, tenant_resolver: _FakeTenantResolver
) -> WorkloadIdentityService:
    """Same shape as _service_with_owner_id_claim, but wired with a fake
    tenant resolver directly — bypassing create_workload_identity_service's
    dynamic import, which needs a real importable dotted path."""
    from niuu.adapters.workload_identity.jwt import JwtWorkloadIdentityVerifier
    from niuu.domain.services.workload_identity import WorkloadIdentityService

    config = SimpleNamespace(
        enabled=True,
        issuer=EXCHANGE_ISSUER,
        audiences=["volundr-api"],
        token_ttl_seconds=900,
        key_id="niuu-workload-test",
        signing_key_pem="",
        mappings=[
            SimpleNamespace(
                name="ravn-resident",
                verifier="kubernetes",
                subject="",
                subject_prefix="system:serviceaccount:valkyrie:resident-",
                issuer=WORKLOAD_ISSUER,
                claims={},
                owner_id="",
                owner_id_claim="sub",
                owner_id_claim_pattern="^system:serviceaccount:[^:]+:resident-(.+)$",
                tenant_id="default",
                email="",
                roles=["volundr:developer"],
                metadata={},
            )
        ],
    )
    verifier = JwtWorkloadIdentityVerifier(
        issuer=WORKLOAD_ISSUER,
        audiences=["volundr-api"],
        static_jwks={"keys": [_jwk_from_key(proof_key, kid="k8s-proof")]},
    )
    return WorkloadIdentityService(
        config,
        verifiers={"kubernetes": verifier},
        tenant_resolver=tenant_resolver,
    )


@pytest.mark.asyncio
async def test_tenant_resolver_derives_tenant_per_caller_not_the_mapping_default() -> None:
    """The MUST-FIX this closes: residentMapping's own tenant_id is one
    fixed value shared by every resident it admits, wrong for every tenant
    but one. With a resolver configured, each caller's REAL tenant (from
    resident_runtimes.tenant_id in production) is used instead."""
    proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    resolver = _FakeTenantResolver({"aaa": "tenant-a", "bbb": "tenant-b"})
    service = _service_with_tenant_resolver(proof_key, tenant_resolver=resolver)

    result_a = await service.exchange(
        _workload_token(proof_key, subject="system:serviceaccount:valkyrie:resident-aaa")
    )
    result_b = await service.exchange(
        _workload_token(proof_key, subject="system:serviceaccount:valkyrie:resident-bbb")
    )

    assert result_a.principal.tenant_id == "tenant-a"
    assert result_b.principal.tenant_id == "tenant-b"


@pytest.mark.asyncio
async def test_tenant_resolver_finding_nothing_raises_not_falls_back() -> None:
    """A caller whose owner_id the resolver cannot find must be rejected
    outright — silently placing it in the mapping's default tenant would put
    its triggers/spend where the real owner can never see them."""
    proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    resolver = _FakeTenantResolver({})  # no records at all
    service = _service_with_tenant_resolver(proof_key, tenant_resolver=resolver)

    with pytest.raises(WorkloadIdentityError, match="no durable record"):
        await service.exchange(
            _workload_token(proof_key, subject="system:serviceaccount:valkyrie:resident-ccc")
        )


@pytest.mark.asyncio
async def test_no_tenant_resolver_configured_falls_back_to_mapping_tenant_id() -> None:
    """The sanctioned minimum: without a resolver, the mapping's own static
    tenant_id is used verbatim — single-tenant-only, but explicit, not a
    silent guess."""
    proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    service = _service_with_owner_id_claim(proof_key)  # no tenant_resolver at all

    result = await service.exchange(
        _workload_token(proof_key, subject="system:serviceaccount:valkyrie:resident-aaa")
    )

    assert result.principal.tenant_id == "default"


@pytest.mark.asyncio
async def test_owner_id_claim_mapping_marks_the_issued_token_owner_scoped() -> None:
    """A caller a resident-scoping mapping matched must be distinguishable
    from one that shares a fixed owner_id — resident_budget's startup check
    (ravn.adapters.resident_budget.platform.PlatformBudgetReporter) relies on
    this claim to refuse to run under a possibly-shared identity."""
    proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    service = _service_with_owner_id_claim(proof_key)

    result = await service.exchange(
        _workload_token(proof_key, subject="system:serviceaccount:valkyrie:resident-aaa")
    )

    jwk = PyJWKSet.from_dict(service.jwks()).keys[0]
    claims = jwt.decode(
        result.token,
        key=jwk.key,
        algorithms=["RS256"],
        audience="volundr-api",
        issuer=EXCHANGE_ISSUER,
    )
    assert claims["workload_owner_scoped"] is True


@pytest.mark.asyncio
async def test_fixed_owner_id_mapping_marks_the_issued_token_not_owner_scoped() -> None:
    """The original, still-supported mapping shape (fixed owner_id shared by
    every caller that matches) must report itself as NOT owner-scoped."""
    proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    service = _service(proof_key)  # the shared fixture: fixed owner_id, no owner_id_claim

    result = await service.exchange(_workload_token(proof_key))

    jwk = PyJWKSet.from_dict(service.jwks()).keys[0]
    claims = jwt.decode(
        result.token,
        key=jwk.key,
        algorithms=["RS256"],
        audience="volundr-api",
        issuer=EXCHANGE_ISSUER,
    )
    assert claims["workload_owner_scoped"] is False


@pytest.mark.asyncio
async def test_owner_id_claim_pattern_extracts_the_resident_id() -> None:
    proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    service = _service_with_owner_id_claim(
        proof_key,
        owner_id_claim_pattern=r"^system:serviceaccount:[^:]+:resident-(.+)$",
    )

    result = await service.exchange(
        _workload_token(proof_key, subject="system:serviceaccount:valkyrie:resident-aaa-111")
    )

    assert result.principal.user_id == "aaa-111"


@pytest.mark.asyncio
async def test_owner_id_claim_pattern_mismatch_falls_through_not_raises() -> None:
    """A pattern non-match means 'this mapping does not apply' — it must not
    itself raise (that would abandon every mapping tried after it). With no
    other mapping configured, the overall exchange still fails, but via the
    generic 'no mapping matched' path, not this mapping's own error."""
    proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    service = _service_with_owner_id_claim(
        proof_key,
        owner_id_claim_pattern=r"^never-matches$",
    )

    with pytest.raises(WorkloadIdentityError, match="No workload identity mapping matched"):
        await service.exchange(
            _workload_token(proof_key, subject="system:serviceaccount:valkyrie:resident-aaa")
        )


@pytest.mark.asyncio
async def test_owner_id_claim_pattern_mismatch_falls_through_to_a_specific_mapping() -> None:
    """The MUST-FIX this closes: residentMapping's broad
    "...resident-" subject_prefix must not shadow a specific, non-UUID-named
    ServiceAccount (e.g. valhalla's Fleet-managed resident-muninn) a
    different mapping owns exactly — the pattern rejects the non-UUID
    caller, _matches falls through, and the specific mapping wins."""
    proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    service = _service_with_owner_id_claim(
        proof_key,
        owner_id_claim_pattern=r"^system:serviceaccount:[^:]+:resident-([0-9a-f-]{36})$",
        extra_mappings=[
            _fixed_mapping(
                name="resident-muninn",
                subject="system:serviceaccount:valkyrie:resident-muninn",
                owner_id="muninn-owner",
            )
        ],
    )

    result = await service.exchange(
        _workload_token(proof_key, subject="system:serviceaccount:valkyrie:resident-muninn")
    )

    assert result.principal.user_id == "muninn-owner"


@pytest.mark.asyncio
async def test_owner_id_claim_pattern_without_a_trailing_anchor_falls_through() -> None:
    """re.fullmatch, not re.match: a pattern missing a trailing "$" (an easy
    authoring mistake) must not silently accept a claim value with unmatched
    trailing content — the unmatched suffix could be anything. That
    incompatibility is a non-match, not a raise (see
    test_owner_id_claim_pattern_mismatch_falls_through_not_raises)."""
    proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    # No trailing "$" — matches a PREFIX under re.match, but the subject the
    # verified proof actually carries has trailing content this pattern
    # never accounts for.
    service = _service_with_owner_id_claim(
        proof_key,
        owner_id_claim_pattern=r"^system:serviceaccount:valkyrie:resident-(aaa)",
    )

    with pytest.raises(WorkloadIdentityError, match="No workload identity mapping matched"):
        await service.exchange(
            _workload_token(
                proof_key, subject="system:serviceaccount:valkyrie:resident-aaa-and-then-more"
            )
        )


@pytest.mark.asyncio
async def test_owner_id_claim_missing_raises_not_empty_owner() -> None:
    proof_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    service = _service_with_owner_id_claim(proof_key, owner_id_claim="nickname")

    with pytest.raises(WorkloadIdentityError, match="requires claim 'nickname'"):
        await service.exchange(
            _workload_token(proof_key, subject="system:serviceaccount:valkyrie:resident-aaa")
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
