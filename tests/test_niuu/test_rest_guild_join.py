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
    NodeRegistrationConflictError,
    PairingCodeInvalidError,
    PairingCodeMintingUnavailableError,
    UntrustedNodeError,
)
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
        slug="node-abc-volundr",
        name="spark-1 (volundr)",
        base_url="http://127.0.0.1:8080",
        visibility=InstanceVisibility.TENANT,
        owner_id=None,
        tenant_id="tenant-a",
        enabled=True,
        is_default=False,
        config={},
        created_at=now,
        updated_at=now,
        node_id="node-1",
    )


class StubGuildJoinService:
    def __init__(self) -> None:
        self.mint_result: MintedPairingCode | Exception = MintedPairingCode(
            code="minted-code", expires_at=datetime.now(UTC)
        )
        self.mint_calls: list[dict] = []
        self.join_result: JoinResult | Exception = JoinResult(
            node=_node(),
            instances=[_instance()],
            identity=IdentityTrustConfig(mode="oidc", issuers=[]),
        )
        self.join_calls: list[dict] = []
        self.heartbeat_result: tuple | Exception = (_node(), [_instance()])
        self.leave_error: Exception | None = None
        self.list_nodes_result: list[RegisteredNode] | Exception = [_node()]
        self.revoke_result: bool | Exception = True
        self.revoke_calls: list[str] = []

    async def mint_pairing_code(self, principal, *, allow_plaintext, allow_untrusted_node_auth):
        self.mint_calls.append(
            {
                "allow_plaintext": allow_plaintext,
                "allow_untrusted_node_auth": allow_untrusted_node_auth,
            }
        )
        if isinstance(self.mint_result, Exception):
            raise self.mint_result
        return self.mint_result

    async def join(self, *, raw_code, node_name, public_key, node_auth_mode, instances):
        self.join_calls.append(
            {
                "raw_code": raw_code,
                "node_name": node_name,
                "public_key": public_key,
                "node_auth_mode": node_auth_mode,
            }
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

    async def list_nodes(self, principal):
        if isinstance(self.list_nodes_result, Exception):
            raise self.list_nodes_result
        return self.list_nodes_result

    async def revoke_node(self, principal, node_id):
        self.revoke_calls.append(node_id)
        if isinstance(self.revoke_result, Exception):
            raise self.revoke_result
        return self.revoke_result


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


VALID_NODE_ID = "00000000-0000-0000-0000-000000000001"


def test_mint_pairing_code_returns_the_code_and_expiry() -> None:
    service, verifier = StubGuildJoinService(), StubNodeVerifier()
    client = _client(service, verifier)

    response = client.post(
        "/api/v1/niuu/guild/pairing-codes",
        json={"allowPlaintext": True, "allowUntrustedNodeAuth": False},
        headers=_auth_headers(),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["code"] == "minted-code"
    assert service.mint_calls == [{"allow_plaintext": True, "allow_untrusted_node_auth": False}]


def test_mint_pairing_code_defaults_both_consents_to_false() -> None:
    service, verifier = StubGuildJoinService(), StubNodeVerifier()
    client = _client(service, verifier)

    response = client.post("/api/v1/niuu/guild/pairing-codes", headers=_auth_headers())

    assert response.status_code == 201
    assert service.mint_calls == [{"allow_plaintext": False, "allow_untrusted_node_auth": False}]


def test_mint_pairing_code_maps_access_error_to_403() -> None:
    service, verifier = StubGuildJoinService(), StubNodeVerifier()
    service.mint_result = GuildJoinAccessError("nope")
    client = _client(service, verifier)

    response = client.post("/api/v1/niuu/guild/pairing-codes", headers=_auth_headers())

    assert response.status_code == 403


def test_mint_pairing_code_maps_minting_unavailable_to_503() -> None:
    service, verifier = StubGuildJoinService(), StubNodeVerifier()
    service.mint_result = PairingCodeMintingUnavailableError("workload identity is disabled")
    client = _client(service, verifier)

    response = client.post("/api/v1/niuu/guild/pairing-codes", headers=_auth_headers())

    assert response.status_code == 503


def test_list_nodes_requires_auth_and_returns_nodes() -> None:
    service, verifier = StubGuildJoinService(), StubNodeVerifier()
    client = _client(service, verifier)

    response = client.get("/api/v1/niuu/guild/nodes", headers=_auth_headers())

    assert response.status_code == 200
    assert response.json()[0]["id"] == "node-1"


def test_list_nodes_maps_access_error_to_403() -> None:
    service, verifier = StubGuildJoinService(), StubNodeVerifier()
    service.list_nodes_result = GuildJoinAccessError("nope")
    client = _client(service, verifier)

    response = client.get("/api/v1/niuu/guild/nodes", headers=_auth_headers())

    assert response.status_code == 403


def test_revoke_node_succeeds() -> None:
    service, verifier = StubGuildJoinService(), StubNodeVerifier()
    client = _client(service, verifier)

    response = client.delete(f"/api/v1/niuu/guild/nodes/{VALID_NODE_ID}", headers=_auth_headers())

    assert response.status_code == 204
    assert service.revoke_calls == [VALID_NODE_ID]


def test_revoke_node_returns_404_when_not_found() -> None:
    service, verifier = StubGuildJoinService(), StubNodeVerifier()
    service.revoke_result = False
    client = _client(service, verifier)

    response = client.delete(f"/api/v1/niuu/guild/nodes/{VALID_NODE_ID}", headers=_auth_headers())

    assert response.status_code == 404


def test_revoke_node_maps_access_error_to_403() -> None:
    service, verifier = StubGuildJoinService(), StubNodeVerifier()
    service.revoke_result = GuildJoinAccessError("nope")
    client = _client(service, verifier)

    response = client.delete(f"/api/v1/niuu/guild/nodes/{VALID_NODE_ID}", headers=_auth_headers())

    assert response.status_code == 403


def test_revoke_node_with_a_malformed_id_returns_404_not_500() -> None:
    """A non-UUID path param must never reach the repository's `::uuid`
    cast (a DB-level error surfacing as a bare 500)."""
    service, verifier = StubGuildJoinService(), StubNodeVerifier()
    client = _client(service, verifier)

    response = client.delete("/api/v1/niuu/guild/nodes/not-a-uuid", headers=_auth_headers())

    assert response.status_code == 404
    assert service.revoke_calls == []


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
            "nodeAuthMode": "oidc",
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
        {
            "raw_code": "the-code",
            "node_name": "spark-1",
            "public_key": "k",
            "node_auth_mode": "oidc",
        }
    ]


def test_join_defaults_node_auth_mode_to_none() -> None:
    service, verifier = StubGuildJoinService(), StubNodeVerifier()
    client = _client(service, verifier)

    client.post(
        "/api/v1/niuu/guild/join",
        json={"code": "c", "nodeName": "spark-1", "publicKey": "k", "instances": []},
        headers={"authorization": f"Bearer {_scoped_token(['node_join'])}"},
    )

    assert service.join_calls[0]["node_auth_mode"] == "none"


@pytest.mark.parametrize(
    ("exc", "status_code"),
    [
        (PairingCodeInvalidError("bad code"), 401),
        (UntrustedNodeError("untrusted"), 403),
        (NodeRegistrationConflictError("taken"), 409),
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

    response = client.post(
        f"/api/v1/niuu/guild/nodes/{VALID_NODE_ID}/heartbeat", json={"instances": []}
    )

    assert response.status_code == 401
    assert verifier.calls == []


def test_heartbeat_verifies_signature_and_returns_instances() -> None:
    service, verifier = StubGuildJoinService(), StubNodeVerifier()
    client = _client(service, verifier)

    response = client.post(
        f"/api/v1/niuu/guild/nodes/{VALID_NODE_ID}/heartbeat",
        json={"instances": []},
        headers={
            "x-niuu-node-id": VALID_NODE_ID,
            "x-niuu-timestamp": "1700000000123",
            "x-niuu-signature": "sig",
        },
    )

    assert response.status_code == 200
    assert response.json()["instances"][0]["id"] == "instance-1"
    assert verifier.calls[0]["node_id"] == VALID_NODE_ID
    assert verifier.calls[0]["method"] == "POST"
    assert verifier.calls[0]["timestamp"] == 1700000000123


def test_heartbeat_maps_signature_error_to_401() -> None:
    service, verifier = StubGuildJoinService(), StubNodeVerifier()
    verifier.error = NodeSignatureError("bad signature")
    client = _client(service, verifier)

    response = client.post(
        f"/api/v1/niuu/guild/nodes/{VALID_NODE_ID}/heartbeat",
        json={"instances": []},
        headers={
            "x-niuu-node-id": VALID_NODE_ID,
            "x-niuu-timestamp": "1700000000123",
            "x-niuu-signature": "sig",
        },
    )

    assert response.status_code == 401


def test_leave_verifies_signature_and_calls_the_service() -> None:
    service, verifier = StubGuildJoinService(), StubNodeVerifier()
    client = _client(service, verifier)

    response = client.post(
        f"/api/v1/niuu/guild/nodes/{VALID_NODE_ID}/leave",
        headers={
            "x-niuu-node-id": VALID_NODE_ID,
            "x-niuu-timestamp": "1700000000123",
            "x-niuu-signature": "sig",
        },
    )

    assert response.status_code == 204
    assert verifier.calls[0]["node_id"] == VALID_NODE_ID


def test_node_id_header_mismatch_is_rejected() -> None:
    service, verifier = StubGuildJoinService(), StubNodeVerifier()
    client = _client(service, verifier)

    response = client.post(
        f"/api/v1/niuu/guild/nodes/{VALID_NODE_ID}/leave",
        headers={
            "x-niuu-node-id": "00000000-0000-0000-0000-000000000099",
            "x-niuu-timestamp": "1700000000123",
            "x-niuu-signature": "sig",
        },
    )

    assert response.status_code == 401
    assert verifier.calls == []


def test_malformed_node_id_path_param_is_a_uniform_401() -> None:
    """A malformed id must fail exactly like an unknown one — see the
    _AUTH_FAILED constant — never a distinguishable 400/422."""
    service, verifier = StubGuildJoinService(), StubNodeVerifier()
    client = _client(service, verifier)

    response = client.post(
        "/api/v1/niuu/guild/nodes/not-a-uuid/leave",
        headers={
            "x-niuu-node-id": "not-a-uuid",
            "x-niuu-timestamp": "1700000000123",
            "x-niuu-signature": "sig",
        },
    )

    assert response.status_code == 401
    assert verifier.calls == []
