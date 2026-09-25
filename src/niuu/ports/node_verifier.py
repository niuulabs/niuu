"""Port for verifying node-originated, Ed25519-signed Guild requests."""

from __future__ import annotations

from abc import ABC, abstractmethod

from niuu.domain.models import RegisteredNode


class NodeSignatureError(Exception):
    """Raised when a node-signed request cannot be admitted.

    Covers every rejection reason (unknown node, expired clock skew, replayed
    timestamp, bad signature) — callers map this to a single fail-closed 401,
    never a degraded "probably fine" path (see ``.claude/rules/no-fallbacks.md``).
    """


class RegisteredNodeVerifier(ABC):
    """Verify a signature a joined node computed over one of its own requests.

    The signed message is ``f"{method}\\n{path}\\n{timestamp}\\n{sha256(body).hexdigest()}"``,
    signed with the node's Ed25519 private key. ``timestamp`` is Unix seconds
    and must both fall inside a bounded clock-skew window and strictly
    increase call over call for that node — the only replay protection this
    scheme has.
    """

    @abstractmethod
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
        """Return the verified node, or raise :class:`NodeSignatureError`."""
