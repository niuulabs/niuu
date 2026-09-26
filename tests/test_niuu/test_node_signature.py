"""Tests for the Ed25519 node-signature verifier adapter.

Covers blocker 2 from the security review: the replay watermark must be
advanced by a single atomic conditional UPDATE, never a check-then-write
that races under concurrent requests, and must use millisecond precision so
two signed requests in the same second can both succeed.
"""

from __future__ import annotations

import asyncio
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
    """Mirrors PostgresNodeRepository.try_advance_watermark's atomicity: the
    conditional check-and-set happens under one lock acquisition, so two
    concurrent calls for a non-increasing pair can never both succeed —
    exactly what a real ``UPDATE ... WHERE ... RETURNING`` guarantees."""

    def __init__(self, nodes: dict[str, RegisteredNode]) -> None:
        self._nodes = nodes
        self._lock = asyncio.Lock()
        self.advance_calls: list[tuple[str, int]] = []

    async def get(self, node_id: str) -> RegisteredNode | None:
        return self._nodes.get(node_id)

    async def try_advance_watermark(self, node_id: str, *, timestamp_ms: int) -> bool:
        self.advance_calls.append((node_id, timestamp_ms))
        async with self._lock:
            node = self._nodes.get(node_id)
            if node is None:
                return False
            if node.last_request_at is not None and node.last_request_at >= timestamp_ms:
                return False
            from dataclasses import replace

            self._nodes[node_id] = replace(node, last_request_at=timestamp_ms)
            return True


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


def _now_ms() -> int:
    return int(time.time() * 1000)


@pytest.mark.asyncio
async def test_verify_accepts_a_correctly_signed_request() -> None:
    private_key, public_key = _keypair()
    node = _node("node-1", public_key)
    repo = FakeNodeRepository({"node-1": node})
    verifier = Ed25519NodeVerifier(repo, clock_skew_seconds=30.0)

    timestamp_ms = _now_ms()
    body = b'{"instances":[]}'
    message = signing_message(
        "POST", "/api/v1/niuu/guild/nodes/node-1/heartbeat", timestamp_ms, body
    )
    signature = base64.b64encode(private_key.sign(message)).decode("ascii")

    verified = await verifier.verify(
        node_id="node-1",
        method="POST",
        path="/api/v1/niuu/guild/nodes/node-1/heartbeat",
        timestamp=timestamp_ms,
        body=body,
        signature=signature,
    )

    assert verified.id == "node-1"
    assert repo.advance_calls == [("node-1", timestamp_ms)]


@pytest.mark.asyncio
async def test_a_heartbeat_and_a_leave_in_the_same_second_both_succeed() -> None:
    """Millisecond precision is the point: two distinct signed requests
    issued within the same wall-clock second must both advance the
    watermark, not collide as if they were replays of each other."""
    private_key, public_key = _keypair()
    node = _node("node-1", public_key)
    repo = FakeNodeRepository({"node-1": node})
    verifier = Ed25519NodeVerifier(repo, clock_skew_seconds=30.0)

    base_ms = _now_ms()

    async def _send(offset_ms: int, path: str) -> None:
        timestamp_ms = base_ms + offset_ms
        message = signing_message("POST", path, timestamp_ms, b"")
        signature = base64.b64encode(private_key.sign(message)).decode("ascii")
        await verifier.verify(
            node_id="node-1",
            method="POST",
            path=path,
            timestamp=timestamp_ms,
            body=b"",
            signature=signature,
        )

    await _send(0, "/api/v1/niuu/guild/nodes/node-1/heartbeat")
    await _send(5, "/api/v1/niuu/guild/nodes/node-1/leave")  # same second, 5ms later


@pytest.mark.asyncio
async def test_unknown_node_and_bad_signature_raise_the_identical_error() -> None:
    """Blocker-adjacent hardening: the two rejection reasons must be
    indistinguishable, or probing node ids becomes an existence oracle."""
    private_key, public_key = _keypair()
    node = _node("node-1", public_key)
    repo = FakeNodeRepository({"node-1": node})
    verifier = Ed25519NodeVerifier(repo, clock_skew_seconds=30.0)
    timestamp_ms = _now_ms()

    with pytest.raises(NodeSignatureError) as unknown_exc:
        await verifier.verify(
            node_id="ghost",
            method="POST",
            path="/x",
            timestamp=timestamp_ms,
            body=b"",
            signature="AA==",
        )

    message = signing_message("POST", "/x", timestamp_ms, b"")
    bad_signature = base64.b64encode(private_key.sign(message + b"tamper")).decode("ascii")
    with pytest.raises(NodeSignatureError) as bad_sig_exc:
        await verifier.verify(
            node_id="node-1",
            method="POST",
            path="/x",
            timestamp=timestamp_ms,
            body=b"",
            signature=bad_signature,
        )

    assert str(unknown_exc.value) == str(bad_sig_exc.value)


@pytest.mark.asyncio
async def test_verify_rejects_a_timestamp_outside_the_clock_skew_window() -> None:
    private_key, public_key = _keypair()
    node = _node("node-1", public_key)
    repo = FakeNodeRepository({"node-1": node})
    verifier = Ed25519NodeVerifier(repo, clock_skew_seconds=5.0)

    stale_timestamp_ms = _now_ms() - 3600_000
    message = signing_message("POST", "/x", stale_timestamp_ms, b"")
    signature = base64.b64encode(private_key.sign(message)).decode("ascii")

    with pytest.raises(NodeSignatureError):
        await verifier.verify(
            node_id="node-1",
            method="POST",
            path="/x",
            timestamp=stale_timestamp_ms,
            body=b"",
            signature=signature,
        )
    assert repo.advance_calls == []


@pytest.mark.asyncio
async def test_verify_rejects_a_replayed_non_increasing_timestamp() -> None:
    private_key, public_key = _keypair()
    timestamp_ms = _now_ms()
    node = _node("node-1", public_key, last_request_at=timestamp_ms)
    repo = FakeNodeRepository({"node-1": node})
    verifier = Ed25519NodeVerifier(repo, clock_skew_seconds=30.0)

    message = signing_message("POST", "/x", timestamp_ms, b"")
    signature = base64.b64encode(private_key.sign(message)).decode("ascii")

    with pytest.raises(NodeSignatureError):
        await verifier.verify(
            node_id="node-1",
            method="POST",
            path="/x",
            timestamp=timestamp_ms,
            body=b"",
            signature=signature,
        )


@pytest.mark.asyncio
async def test_concurrent_requests_with_the_same_timestamp_only_one_succeeds() -> None:
    """The core of blocker 2: verify signature is not the race boundary —
    the atomic watermark advance is, and it must actually serialize two
    concurrent callers instead of both reading a stale watermark."""
    private_key, public_key = _keypair()
    node = _node("node-1", public_key)
    repo = FakeNodeRepository({"node-1": node})
    verifier = Ed25519NodeVerifier(repo, clock_skew_seconds=30.0)

    timestamp_ms = _now_ms()
    message = signing_message("POST", "/x", timestamp_ms, b"")
    signature = base64.b64encode(private_key.sign(message)).decode("ascii")

    async def _attempt():
        return await verifier.verify(
            node_id="node-1",
            method="POST",
            path="/x",
            timestamp=timestamp_ms,
            body=b"",
            signature=signature,
        )

    results = await asyncio.gather(_attempt(), _attempt(), return_exceptions=True)
    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, NodeSignatureError)]
    assert len(successes) == 1
    assert len(failures) == 1


@pytest.mark.asyncio
async def test_verify_rejects_a_tampered_body() -> None:
    private_key, public_key = _keypair()
    node = _node("node-1", public_key)
    repo = FakeNodeRepository({"node-1": node})
    verifier = Ed25519NodeVerifier(repo, clock_skew_seconds=30.0)

    timestamp_ms = _now_ms()
    message = signing_message("POST", "/x", timestamp_ms, b"original body")
    signature = base64.b64encode(private_key.sign(message)).decode("ascii")

    with pytest.raises(NodeSignatureError):
        await verifier.verify(
            node_id="node-1",
            method="POST",
            path="/x",
            timestamp=timestamp_ms,
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

    timestamp_ms = _now_ms()
    message = signing_message("POST", "/x", timestamp_ms, b"")
    signature = base64.b64encode(other_private_key.sign(message)).decode("ascii")

    with pytest.raises(NodeSignatureError):
        await verifier.verify(
            node_id="node-1",
            method="POST",
            path="/x",
            timestamp=timestamp_ms,
            body=b"",
            signature=signature,
        )


def test_clock_skew_must_be_positive() -> None:
    with pytest.raises(ValueError, match="clock_skew_seconds must be positive"):
        Ed25519NodeVerifier(FakeNodeRepository({}), clock_skew_seconds=0)
