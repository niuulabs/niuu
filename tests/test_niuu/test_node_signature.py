"""Tests for the Ed25519 node-signature verifier adapter."""

from __future__ import annotations

import base64
import time
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from niuu.adapters.node_signature import Ed25519NodeVerifier, signing_message
from niuu.domain.models import RegisteredNode
from niuu.ports.node_verifier import NodeSignatureError


def _keypair() -> tuple[Ed25519PrivateKey, str]:
    private_key = Ed25519PrivateKey.generate()
    raw_public = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return private_key, base64.b64encode(raw_public).decode("ascii")


class FakeNodeRepository:
    def __init__(self, nodes: dict[str, RegisteredNode]) -> None:
        self._nodes = nodes
        self.recorded: list[tuple[str, int]] = []

    async def get(self, node_id: str) -> RegisteredNode | None:
        return self._nodes.get(node_id)

    async def record_request(self, node_id: str, *, timestamp: int) -> None:
        self.recorded.append((node_id, timestamp))
        node = self._nodes[node_id]
        self._nodes[node_id] = RegisteredNode(
            id=node.id,
            name=node.name,
            public_key=node.public_key,
            tenant_id=node.tenant_id,
            created_by=node.created_by,
            created_at=node.created_at,
            last_seen_at=node.last_seen_at,
            last_request_at=timestamp,
        )

    # Unused by the verifier, present only to satisfy the port shape if needed.
    async def create(self, **kwargs):  # noqa: ANN003, ANN201
        raise NotImplementedError

    async def touch_heartbeat(self, node_id: str):  # noqa: ANN201
        raise NotImplementedError

    async def delete(self, node_id: str) -> None:
        raise NotImplementedError


def _node(node_id: str, public_key: str, *, last_request_at: int | None = None) -> RegisteredNode:
    return RegisteredNode(
        id=node_id,
        name="spark-1",
        public_key=public_key,
        tenant_id="tenant-a",
        created_by="admin-1",
        created_at=datetime.now(UTC),
        last_request_at=last_request_at,
    )


@pytest.mark.asyncio
async def test_verify_accepts_a_correctly_signed_request() -> None:
    private_key, public_key = _keypair()
    node = _node("node-1", public_key)
    repo = FakeNodeRepository({"node-1": node})
    verifier = Ed25519NodeVerifier(repo, clock_skew_seconds=30.0)

    timestamp = int(time.time())
    body = b'{"instances":[]}'
    message = signing_message("POST", "/api/v1/niuu/guild/nodes/node-1/heartbeat", timestamp, body)
    signature = base64.b64encode(private_key.sign(message)).decode("ascii")

    verified = await verifier.verify(
        node_id="node-1",
        method="POST",
        path="/api/v1/niuu/guild/nodes/node-1/heartbeat",
        timestamp=timestamp,
        body=body,
        signature=signature,
    )

    assert verified.id == "node-1"
    assert repo.recorded == [("node-1", timestamp)]


@pytest.mark.asyncio
async def test_verify_rejects_an_unknown_node() -> None:
    repo = FakeNodeRepository({})
    verifier = Ed25519NodeVerifier(repo, clock_skew_seconds=30.0)

    with pytest.raises(NodeSignatureError, match="Unknown node"):
        await verifier.verify(
            node_id="ghost",
            method="POST",
            path="/x",
            timestamp=int(time.time()),
            body=b"",
            signature="AA==",
        )


@pytest.mark.asyncio
async def test_verify_rejects_a_timestamp_outside_the_clock_skew_window() -> None:
    private_key, public_key = _keypair()
    node = _node("node-1", public_key)
    repo = FakeNodeRepository({"node-1": node})
    verifier = Ed25519NodeVerifier(repo, clock_skew_seconds=5.0)

    stale_timestamp = int(time.time()) - 3600
    message = signing_message("POST", "/x", stale_timestamp, b"")
    signature = base64.b64encode(private_key.sign(message)).decode("ascii")

    with pytest.raises(NodeSignatureError, match="clock-skew"):
        await verifier.verify(
            node_id="node-1",
            method="POST",
            path="/x",
            timestamp=stale_timestamp,
            body=b"",
            signature=signature,
        )


@pytest.mark.asyncio
async def test_verify_rejects_a_replayed_non_increasing_timestamp() -> None:
    private_key, public_key = _keypair()
    timestamp = int(time.time())
    node = _node("node-1", public_key, last_request_at=timestamp)
    repo = FakeNodeRepository({"node-1": node})
    verifier = Ed25519NodeVerifier(repo, clock_skew_seconds=30.0)

    message = signing_message("POST", "/x", timestamp, b"")
    signature = base64.b64encode(private_key.sign(message)).decode("ascii")

    with pytest.raises(NodeSignatureError, match="replay"):
        await verifier.verify(
            node_id="node-1",
            method="POST",
            path="/x",
            timestamp=timestamp,
            body=b"",
            signature=signature,
        )


@pytest.mark.asyncio
async def test_verify_rejects_a_tampered_body() -> None:
    private_key, public_key = _keypair()
    node = _node("node-1", public_key)
    repo = FakeNodeRepository({"node-1": node})
    verifier = Ed25519NodeVerifier(repo, clock_skew_seconds=30.0)

    timestamp = int(time.time())
    message = signing_message("POST", "/x", timestamp, b"original body")
    signature = base64.b64encode(private_key.sign(message)).decode("ascii")

    with pytest.raises(NodeSignatureError, match="Signature verification failed"):
        await verifier.verify(
            node_id="node-1",
            method="POST",
            path="/x",
            timestamp=timestamp,
            body=b"tampered body",
            signature=signature,
        )


@pytest.mark.asyncio
async def test_verify_rejects_a_signature_from_the_wrong_key() -> None:
    _, public_key = _keypair()
    other_private_key, _ = _keypair()
    node = _node("node-1", public_key)
    repo = FakeNodeRepository({"node-1": node})
    verifier = Ed25519NodeVerifier(repo, clock_skew_seconds=30.0)

    timestamp = int(time.time())
    message = signing_message("POST", "/x", timestamp, b"")
    signature = base64.b64encode(other_private_key.sign(message)).decode("ascii")

    with pytest.raises(NodeSignatureError, match="Signature verification failed"):
        await verifier.verify(
            node_id="node-1",
            method="POST",
            path="/x",
            timestamp=timestamp,
            body=b"",
            signature=signature,
        )


def test_clock_skew_must_be_positive() -> None:
    with pytest.raises(ValueError, match="clock_skew_seconds must be positive"):
        Ed25519NodeVerifier(FakeNodeRepository({}), clock_skew_seconds=0)
