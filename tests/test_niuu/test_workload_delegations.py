"""Workload delegations: the exchange's owner_from: delegation mode and its API."""

from __future__ import annotations

import base64
import time
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from niuu.adapters.inbound.rest_workload_delegations import create_workload_delegations_router
from niuu.adapters.memory_workload_delegations import InMemoryWorkloadDelegationRepository
from niuu.domain.models import Principal
from niuu.domain.services.workload_identity import WorkloadIdentityError
from niuu.service_runtime import create_workload_identity_service


def _jwk_from_key(key: rsa.RSAPrivateKey, *, kid: str) -> dict[str, Any]:
    numbers = key.public_key().public_numbers()

    def b64(value: int) -> str:
        raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    return {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": b64(numbers.n),
        "e": b64(numbers.e),
    }


ISSUER = "https://k8s.valhalla.test"
VERIFIER = "k8s-valhalla"
RESIDENT_SA = "system:serviceaccount:skuld:resident-0f9e3c1a-6c1d-4f4e-9d3a-2b1c0a9e8d7f"


def _proof(key: rsa.RSAPrivateKey, subject: str = RESIDENT_SA) -> str:
    now = int(time.time())
    return jwt.encode(
        {"iss": ISSUER, "sub": subject, "aud": "volundr-api", "iat": now, "exp": now + 600},
        key,
        algorithm="RS256",
        headers={"kid": "k8s-proof"},
    )


def _service(key: rsa.RSAPrivateKey, delegations: InMemoryWorkloadDelegationRepository):
    return create_workload_identity_service(
        SimpleNamespace(
            enabled=True,
            issuer="https://exchange.test",
            audiences=["volundr-api"],
            token_ttl_seconds=900,
            key_id="test",
            signing_key_pem="",
            signing_key_env="",
            verifiers=[
                SimpleNamespace(
                    name=VERIFIER,
                    adapter="niuu.adapters.workload_identity.jwt.JwtWorkloadIdentityVerifier",
                    kwargs={
                        "issuer": ISSUER,
                        "audiences": ["volundr-api"],
                        "static_jwks": {"keys": [_jwk_from_key(key, kid="k8s-proof")]},
                    },
                    secret_kwargs_env={},
                )
            ],
            mappings=[
                SimpleNamespace(
                    name="delegated-residents",
                    verifier=VERIFIER,
                    subject="",
                    subject_prefix="system:serviceaccount:skuld:resident-",
                    issuer=ISSUER,
                    claims={},
                    owner_from="delegation",
                    owner_id="",
                    owner_id_claim="",
                    owner_id_claim_pattern="",
                    tenant_id="ignored",
                    email="",
                    roles=["volundr:developer"],
                    metadata={"cluster": "valhalla", "kind": "resident"},
                )
            ],
        ),
        delegations=delegations,
    )


async def _delegate(repo: InMemoryWorkloadDelegationRepository, **overrides: Any):
    return await repo.create(
        **{
            "verifier": VERIFIER,
            "subject": RESIDENT_SA,
            "owner_id": "owner-1",
            "tenant_id": "default",
            "created_by": "owner-1",
            "name": "Muninn",
            "resident_id": "0f9e3c1a",
            **overrides,
        }
    )


@pytest.mark.asyncio
async def test_delegated_workload_speaks_for_its_owner_with_itself_as_actor() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    repo = InMemoryWorkloadDelegationRepository()
    record = await _delegate(repo)
    service = _service(key, repo)

    result = await service.exchange(_proof(key))

    claims = service.verify_token(result.token)
    assert claims["sub"] == "owner-1"
    assert claims["tenant_id"] == "default"
    assert claims["act"] == {"sub": RESIDENT_SA}
    assert claims["workload_delegation_id"] == str(record.id)
    assert claims["workload_resident_id"] == "0f9e3c1a"
    assert claims["workload_owner_scoped"] is False
    assert claims["resource_access"]["volundr-api"]["roles"] == ["volundr:developer"]
    assert result.workload_name == "Muninn"


@pytest.mark.asyncio
async def test_workload_without_a_delegation_is_refused() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    service = _service(key, InMemoryWorkloadDelegationRepository())

    with pytest.raises(WorkloadIdentityError, match="No active delegation"):
        await service.exchange(_proof(key))


@pytest.mark.asyncio
async def test_revoked_delegation_admits_no_one() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    repo = InMemoryWorkloadDelegationRepository()
    record = await _delegate(repo)
    await repo.revoke(record.id)

    with pytest.raises(WorkloadIdentityError, match="No active delegation"):
        await _service(key, repo).exchange(_proof(key))


@pytest.mark.asyncio
async def test_delegation_for_another_cluster_does_not_admit_the_workload() -> None:
    """The verifier keys the record: the same subject on another cluster is another workload."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    repo = InMemoryWorkloadDelegationRepository()
    await _delegate(repo, verifier="k8s-noatun")

    with pytest.raises(WorkloadIdentityError, match="No active delegation"):
        await _service(key, repo).exchange(_proof(key))


@pytest.mark.asyncio
async def test_host_without_a_delegation_store_admits_no_delegated_workload() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    service = _service(key, InMemoryWorkloadDelegationRepository())
    service._delegations = None

    with pytest.raises(WorkloadIdentityError, match="No active delegation"):
        await service.exchange(_proof(key))


@pytest.mark.asyncio
async def test_moving_a_workload_to_another_owner_needs_a_revoke_first() -> None:
    from niuu.ports.workload_delegations import WorkloadDelegationExistsError

    repo = InMemoryWorkloadDelegationRepository()
    first = await _delegate(repo)

    assert (await _delegate(repo)).id == first.id
    with pytest.raises(WorkloadDelegationExistsError):
        await _delegate(repo, owner_id="owner-2")


# --- API -------------------------------------------------------------------------


def _client(principal: Principal, repo: InMemoryWorkloadDelegationRepository) -> TestClient:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    app = FastAPI()
    app.state.workload_delegations = repo
    app.state.workload_identity_service = _service(key, repo)

    async def as_principal() -> Principal:
        return principal

    app.include_router(create_workload_delegations_router(as_principal))
    return TestClient(app)


def _owner(**overrides: Any) -> Principal:
    return Principal(
        **{
            "user_id": "owner-1",
            "email": "",
            "tenant_id": "default",
            "roles": ["volundr:developer"],
            **overrides,
        }
    )


BODY = {"verifier": VERIFIER, "subject": RESIDENT_SA, "name": "Muninn", "residentId": "0f9e3c1a"}


def test_owner_delegates_a_workload_to_themselves() -> None:
    repo = InMemoryWorkloadDelegationRepository()
    response = _client(_owner(), repo).post("/api/v1/tokens/workload/delegations", json=BODY)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["ownerId"] == "owner-1"
    assert body["createdBy"] == "owner-1"
    assert body["residentId"] == "0f9e3c1a"


def test_delegated_caller_cannot_manage_delegations() -> None:
    """A resident's token (it carries an actor) must not grant itself or others more reach."""
    client = _client(_owner(actor=RESIDENT_SA), InMemoryWorkloadDelegationRepository())

    assert client.post("/api/v1/tokens/workload/delegations", json=BODY).status_code == 403
    assert client.get("/api/v1/tokens/workload/delegations").status_code == 403


def test_only_an_admin_delegates_to_another_owner() -> None:
    repo = InMemoryWorkloadDelegationRepository()
    body = {**BODY, "ownerId": "owner-2"}

    denied = _client(_owner(), repo).post("/api/v1/tokens/workload/delegations", json=body)
    allowed = _client(_owner(roles=["volundr:admin"]), repo).post(
        "/api/v1/tokens/workload/delegations", json=body
    )

    assert denied.status_code == 403
    assert allowed.status_code == 201
    assert allowed.json()["ownerId"] == "owner-2"


def test_delegation_no_mapping_could_use_is_refused() -> None:
    body = {**BODY, "subject": "system:serviceaccount:nats:valkyrie-valhalla-k8s"}
    response = _client(_owner(), InMemoryWorkloadDelegationRepository()).post(
        "/api/v1/tokens/workload/delegations", json=body
    )

    assert response.status_code == 422


def test_workload_already_delegated_to_another_owner_conflicts() -> None:
    repo = InMemoryWorkloadDelegationRepository()
    _client(_owner(), repo).post("/api/v1/tokens/workload/delegations", json=BODY)

    response = _client(_owner(roles=["volundr:admin"]), repo).post(
        "/api/v1/tokens/workload/delegations", json={**BODY, "ownerId": "owner-2"}
    )

    assert response.status_code == 409


def test_owner_lists_and_revokes_only_their_own_delegations() -> None:
    repo = InMemoryWorkloadDelegationRepository()
    created = _client(_owner(), repo).post("/api/v1/tokens/workload/delegations", json=BODY)
    delegation_id = created.json()["id"]
    stranger = _client(_owner(user_id="owner-2"), repo)

    assert stranger.get("/api/v1/tokens/workload/delegations").json() == []
    assert (
        stranger.delete(f"/api/v1/tokens/workload/delegations/{delegation_id}").status_code == 404
    )

    owner = _client(_owner(), repo)
    assert [d["id"] for d in owner.get("/api/v1/tokens/workload/delegations").json()] == [
        delegation_id
    ]
    assert owner.delete(f"/api/v1/tokens/workload/delegations/{delegation_id}").status_code == 204
    assert owner.get("/api/v1/tokens/workload/delegations").json() == []


# --- Postgres adapter --------------------------------------------------------------


def _row(**overrides: Any) -> dict[str, Any]:
    from datetime import UTC, datetime
    from uuid import uuid4

    return {
        "id": uuid4(),
        "verifier": VERIFIER,
        "subject": RESIDENT_SA,
        "owner_id": "owner-1",
        "tenant_id": "default",
        "created_by": "owner-1",
        "created_at": datetime.now(UTC),
        "name": "Muninn",
        "resident_id": "",
        "revoked_at": None,
        **overrides,
    }


@pytest.mark.asyncio
async def test_postgres_create_keeps_the_existing_record_for_the_same_owner() -> None:
    from unittest.mock import AsyncMock

    from niuu.adapters.postgres_workload_delegations import PostgresWorkloadDelegationRepository

    existing = _row()
    pool = AsyncMock()
    pool.fetchrow.side_effect = [None, existing]  # insert conflicts, then the active record
    repo = PostgresWorkloadDelegationRepository(pool)

    record = await repo.create(
        verifier=VERIFIER,
        subject=RESIDENT_SA,
        owner_id="owner-1",
        tenant_id="default",
        created_by="owner-1",
    )

    assert record.id == existing["id"]
    assert (
        "ON CONFLICT (verifier, subject) WHERE revoked_at IS NULL DO NOTHING"
        in (pool.fetchrow.await_args_list[0].args[0])
    )


@pytest.mark.asyncio
async def test_postgres_create_refuses_a_workload_another_owner_holds() -> None:
    from unittest.mock import AsyncMock

    from niuu.adapters.postgres_workload_delegations import PostgresWorkloadDelegationRepository
    from niuu.ports.workload_delegations import WorkloadDelegationExistsError

    pool = AsyncMock()
    pool.fetchrow.side_effect = [None, _row(owner_id="owner-2")]

    with pytest.raises(WorkloadDelegationExistsError):
        await PostgresWorkloadDelegationRepository(pool).create(
            verifier=VERIFIER,
            subject=RESIDENT_SA,
            owner_id="owner-1",
            tenant_id="default",
            created_by="owner-1",
        )
