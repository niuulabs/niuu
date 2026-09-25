"""Ed25519 adapter for verifying node-originated Guild requests."""

from __future__ import annotations

import base64
import hashlib
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from niuu.domain.models import RegisteredNode
from niuu.ports.node_verifier import NodeSignatureError, RegisteredNodeVerifier
from niuu.ports.nodes import NodeRepository


def signing_message(method: str, path: str, timestamp: int, body: bytes) -> bytes:
    """The exact bytes a node signs, and Guild re-derives, for one request.

    Shared between the adapter and the CLI's node-signing code so the two
    sides can never silently drift apart.
    """
    body_hash = hashlib.sha256(body).hexdigest()
    return f"{method.upper()}\n{path}\n{timestamp}\n{body_hash}".encode()


class Ed25519NodeVerifier(RegisteredNodeVerifier):
    """Verifies node signatures against the joined node's stored public key.

    Fails closed on every rejection path (unknown node, malformed key,
    invalid signature, clock skew, replay) — never a degraded "probably the
    same node" acceptance. See ``.claude/rules/no-fallbacks.md``.
    """

    def __init__(self, node_repository: NodeRepository, *, clock_skew_seconds: float) -> None:
        if clock_skew_seconds <= 0:
            raise ValueError("clock_skew_seconds must be positive")
        self._nodes = node_repository
        self._clock_skew_seconds = clock_skew_seconds

    async def verify(
        self,
        *,
        node_id: str,
        method: str,
        path: str,
        timestamp: int,
        body: bytes,
        signature: str,
    ) -> RegisteredNode:
        node = await self._nodes.get(node_id)
        if node is None:
            raise NodeSignatureError(f"Unknown node: {node_id}")

        now = time.time()
        if abs(now - timestamp) > self._clock_skew_seconds:
            raise NodeSignatureError(
                f"Request timestamp outside the allowed {self._clock_skew_seconds}s clock-skew "
                "window; check the node's clock"
            )
        if node.last_request_at is not None and timestamp <= node.last_request_at:
            raise NodeSignatureError(
                "Request timestamp does not strictly increase over the node's last "
                "accepted request; rejected as a possible replay"
            )

        try:
            public_key_bytes = base64.b64decode(node.public_key, validate=True)
            signature_bytes = base64.b64decode(signature, validate=True)
            Ed25519PublicKey.from_public_bytes(public_key_bytes).verify(
                signature_bytes, signing_message(method, path, timestamp, body)
            )
        except (InvalidSignature, ValueError) as exc:
            raise NodeSignatureError(f"Signature verification failed for node {node_id}") from exc

        await self._nodes.record_request(node_id, timestamp=timestamp)
        return node
