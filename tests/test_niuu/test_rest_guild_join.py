"""Tests for the Guild node-join REST endpoints."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.adapters.identity import AllowAllIdentityAdapter
from niuu.adapters.inbound.rest_guild_join import create_guild_join_router
from niuu.domain.models import InstanceKind, InstanceVisibility, RegisteredInstance, RegisteredNode
from niuu.domain.services.guild_join import (
    GuildJoinAccessError,
    GuildJoinError,
    IdentityTrustConfig,
    JoinResult,
    MintedPairingCode,
    PairingCodeInvalidError,
)
from niuu.domain.services.instances import InstanceTransportSecurityError
from niuu.domain.services.token_scope import VALKYRIE_BUILD_TOKEN_USE
from niuu.ports.node_verifier import NodeSignatureError

_SIGNING_KEY = "test-only-signing-key-32-bytes-long!"


def _scoped_token(scopes: list[str]) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "sub": "node-join-caller",
            "iat": now,
            "exp": now + 600,
            "token_use": VALKYRIE_BUILD_TOKEN_USE,
            "scopes": scopes,
        },
        _SIGNING_KEY,
        algorithm="HS256",
    )


def _node(node_id: str = "node-1") -> RegisteredNode:
    return RegisteredNode(
        id=node_id,
        name="spark-1",
        public_key="k",
        tenant_id="tenant-a",
        created_by="admin-1",
        created_at=datetime.now(UTC),
    )


def _instance() -> RegisteredInstance:
    now = datetime.now(UTC)
    return RegisteredInstance(
        id="instance-1",
        kind=InstanceKind.VOLUNDR,
        slug="spark-1-volundr",
        name="spark-1 (volundr)",
        base_url="http://127.0.0.1:8080",
        visibility=InstanceVisibility.TENANT,
        owner_id=None,
        tenant_id="tenant-a",
        enabled=True,
        is_default=False,
        config={"node_id": "node-1"},
        created_at=now,
        updated_at=now,
    )


class StubGuildJoinService:
    def __init__(self) -> None:
        self.mint_result: MintedPairingCode | Exception = MintedPairingCode(
            code="minted-code", expires_at=datetime.now(UTC)
        )
        self.join_result: JoinResult | Exception = JoinResult(
            node=_node(),
            instances=[_instance()],
            identity=IdentityTrustConfig(mode="oidc", issuers=[]),
        )
        self.heartbeat_result: tuple | Exception = (_node(), [_instance()])
        self.leave_error: Exception | None = None
        self.join_calls: list[dict] = []

    async def mint_pairing_code(self, principal):
        if isinstance(self.mint_result, Exception):
            raise self.mint_result
        return self.mint_result

    async def join(self, *, raw_code, node_name, public_key, instances):
        self.join_calls.append(
            {"raw_code": raw_code, "node_name": node_name, "public_key": public_key}
        )
        if isinstance(self.join_result, Exception):
            raise self.join_result
        return self.join_result

    async def heartbeat(self, node, instances):
        if isinstance(self.heartbeat_result, Exception):
            raise self.heartbeat_result
        return self.heartbeat_result

    async def leave(self, node):
        if self.leave_error is not None:
            raise self.leave_error


class StubNodeVerifier:
    def __init__(self) -> None:
        self.error: NodeSignatureError | None = None
        self.calls: list[dict] = []

    async def verify(self, *, node_id, method, path, timestamp, body, signature):
        self.calls.append(
            {
                "node_id": node_id,
                "method": method,
                "path": path,
                "timestamp": timestamp,
                "body": body,
                "signature": signature,
            }
        )
        if self.error is not None:
            raise self.error
        return _node(node_id)


def _client(service: StubGuildJoinService, verifier: StubNodeVerifier) -> TestClient:
    app = FastAPI()
    app.state.identity = AllowAllIdentityAdapter(user_repository=AsyncMock())
    app.include_router(create_guild_join_router(service, node_verifier=verifier))  # type: ignore[arg-type]
    return TestClient(app)


def _auth_headers() -> dict[str, str]:
    return {
        "authorization": "Bearer test-token",
        "x-auth-user-id": "admin-1",
        "x-auth-email": "admin@example.com",
        "x-auth-tenant": "tenant-a",
        "x-auth-roles": "admin",
    }


def test_mint_pairing_code_returns_the_code_and_expiry() -> None:
    service, verifier = StubGuildJoinService(), StubNodeVerifier()
    client = _client(service, verifier)

    response = client.post("/api/v1/niuu/guild/pairing-codes", headers=_auth_headers())

    assert response.status_code == 201
    body = response.json()
    assert body["code"] == "minted-code"
    assert body["expiresAt"].startswith(
        service.mint_result.expires_at.isoformat(timespec="seconds")[:19]
    )


def test_mint_pairing_code_maps_access_error_to_403() -> None:
    service, verifier = StubGuildJoinService(), StubNodeVerifier()
    service.mint_result = GuildJoinAccessError("nope")
    client = _client(service, verifier)

    response = client.post("/api/v1/niuu/guild/pairing-codes", headers=_auth_headers())

    assert response.status_code == 403


def test_join_is_denied_without_the_node_join_scope() -> None:
    service, verifier = StubGuildJoinService(), StubNodeVerifier()
    client = _client(service, verifier)

    response = client.post(
        "/api/v1/niuu/guild/join",
        json={"code": "c", "nodeName": "spark-1", "publicKey": "k", "instances": []},
        headers={"authorization": f"Bearer {_scoped_token(['forge:session:create'])}"},
    )

    assert response.status_code == 403
    assert service.join_calls == []


def test_join_succeeds_with_a_node_join_scoped_token() -> None:
    service, verifier = StubGuildJoinService(), StubNodeVerifier()
    client = _client(service, verifier)

    response = client.post(
        "/api/v1/niuu/guild/join",
        json={
            "code": "the-code",
            "nodeName": "spark-1",
            "publicKey": "k",
            "instances": [{"kind": "volundr", "baseUrl": "http://127.0.0.1:8080"}],
        },
        headers={"authorization": f"Bearer {_scoped_token(['node_join'])}"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["nodeId"] == "node-1"
    assert body["instances"][0]["baseUrl"] == "http://127.0.0.1:8080"
    assert body["identity"] == {"mode": "oidc", "issuers": []}
    assert service.join_calls == [
        {"raw_code": "the-code", "node_name": "spark-1", "public_key": "k"}
    ]


@pytest.mark.parametrize(
    ("exc", "status_code"),
    [
        (PairingCodeInvalidError("bad code"), 401),
        (InstanceTransportSecurityError("insecure"), 422),
        (GuildJoinError("bad request"), 400),
    ],
)
def test_join_error_mapping(exc: Exception, status_code: int) -> None:
    service, verifier = StubGuildJoinService(), StubNodeVerifier()
    service.join_result = exc
    client = _client(service, verifier)

    response = client.post(
        "/api/v1/niuu/guild/join",
        json={"code": "c", "nodeName": "spark-1", "publicKey": "k", "instances": []},
        headers={"authorization": f"Bearer {_scoped_token(['node_join'])}"},
    )

    assert response.status_code == status_code


def test_heartbeat_requires_signature_headers() -> None:
    service, verifier = StubGuildJoinService(), StubNodeVerifier()
    client = _client(service, verifier)

    response = client.post("/api/v1/niuu/guild/nodes/node-1/heartbeat", json={"instances": []})

    assert response.status_code == 401
    assert verifier.calls == []


def test_heartbeat_verifies_signature_and_returns_instances() -> None:
    service, verifier = StubGuildJoinService(), StubNodeVerifier()
    client = _client(service, verifier)

    response = client.post(
        "/api/v1/niuu/guild/nodes/node-1/heartbeat",
        json={"instances": []},
        headers={
            "x-niuu-node-id": "node-1",
            "x-niuu-timestamp": "1700000000",
            "x-niuu-signature": "sig",
        },
    )

    assert response.status_code == 200
    assert response.json()["instances"][0]["id"] == "instance-1"
    assert verifier.calls[0]["node_id"] == "node-1"
    assert verifier.calls[0]["method"] == "POST"
    assert verifier.calls[0]["timestamp"] == 1700000000


def test_heartbeat_maps_signature_error_to_401() -> None:
    service, verifier = StubGuildJoinService(), StubNodeVerifier()
    verifier.error = NodeSignatureError("bad signature")
    client = _client(service, verifier)

    response = client.post(
        "/api/v1/niuu/guild/nodes/node-1/heartbeat",
        json={"instances": []},
        headers={
            "x-niuu-node-id": "node-1",
            "x-niuu-timestamp": "1700000000",
            "x-niuu-signature": "sig",
        },
    )

    assert response.status_code == 401


def test_leave_verifies_signature_and_calls_the_service() -> None:
    service, verifier = StubGuildJoinService(), StubNodeVerifier()
    client = _client(service, verifier)

    response = client.post(
        "/api/v1/niuu/guild/nodes/node-1/leave",
        headers={
            "x-niuu-node-id": "node-1",
            "x-niuu-timestamp": "1700000000",
            "x-niuu-signature": "sig",
        },
    )

    assert response.status_code == 204
    assert verifier.calls[0]["node_id"] == "node-1"


def test_node_id_header_mismatch_is_rejected() -> None:
    service, verifier = StubGuildJoinService(), StubNodeVerifier()
    client = _client(service, verifier)

    response = client.post(
        "/api/v1/niuu/guild/nodes/node-1/leave",
        headers={
            "x-niuu-node-id": "someone-else",
            "x-niuu-timestamp": "1700000000",
            "x-niuu-signature": "sig",
        },
    )

    assert response.status_code == 400
    assert verifier.calls == []
