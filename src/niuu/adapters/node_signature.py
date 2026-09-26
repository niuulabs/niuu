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

#: Generic, uniform message for every rejection reason — an unknown node id
#: and a bad signature must be indistinguishable to the caller, or a probe
#: of node ids becomes an oracle for which ones exist.
_AUTH_FAILED = "Node authentication failed"


def signing_message(method: str, path: str, timestamp_ms: int, body: bytes) -> bytes:
    """The exact bytes a node signs, and Guild re-derives, for one request.

    ``timestamp_ms`` is Unix time in MILLISECONDS (not seconds) — a heartbeat
    and a leave issued within the same second must both be able to advance
    the strictly-increasing replay watermark. Shared between the adapter and
    the CLI's node-signing code so the two sides can never silently drift
    apart.
    """
    body_hash = hashlib.sha256(body).hexdigest()
    return f"{method.upper()}\n{path}\n{timestamp_ms}\n{body_hash}".encode()


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
        self._clock_skew_ms = clock_skew_seconds * 1000

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
            raise NodeSignatureError(_AUTH_FAILED)

        now_ms = time.time() * 1000
        if abs(now_ms - timestamp) > self._clock_skew_ms:
            raise NodeSignatureError(_AUTH_FAILED)

        try:
            public_key_bytes = base64.b64decode(node.public_key, validate=True)
            signature_bytes = base64.b64decode(signature, validate=True)
            Ed25519PublicKey.from_public_bytes(public_key_bytes).verify(
                signature_bytes, signing_message(method, path, timestamp, body)
            )
        except (InvalidSignature, ValueError) as exc:
            raise NodeSignatureError(_AUTH_FAILED) from exc

        # The watermark advance is the actual replay-protection boundary —
        # it re-checks under the database row lock at write time, so a
        # request that raced past the clock-skew check above still cannot
        # replay or reorder against a concurrent request for the same node.
        advanced = await self._nodes.try_advance_watermark(node_id, timestamp_ms=timestamp)
        if not advanced:
            raise NodeSignatureError(_AUTH_FAILED)

        return node
