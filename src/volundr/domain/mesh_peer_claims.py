"""Which holder owns which mesh peer id, and the port that records it.

A mesh certificate (:mod:`niuu.mesh.attestation`) binds a peer id to a key. Forge must not
certify two live holders under one peer id, or one of them could pass for the other. A
**claim** is that exclusivity: one row per peer id, taken by one atomic upsert and released
when the holder's session stops.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

#: Prefix of the holder of a session's claims.
HOLDER_SESSION = "session:"
#: Prefix of the holder of a Forge-hosted resident peer's claims.
HOLDER_RESIDENT = "resident:"


def session_holder(session_id: object) -> str:
    return f"{HOLDER_SESSION}{session_id}"


def resident_holder(runtime_id: object) -> str:
    return f"{HOLDER_RESIDENT}{runtime_id}"


class PeerIdClaimedError(Exception):
    """The peer id is held by another live holder. The message names the peer id."""

    def __init__(self, peer_id: str) -> None:
        self.peer_id = peer_id
        super().__init__(
            f"Mesh peer id {peer_id!r} is held by another live session or resident; pick a "
            "different peer id, or stop the holder (its claim also lapses when its "
            "certificate expires)"
        )


@dataclass(frozen=True)
class PeerClaim:
    """A claim that was released: enough to tell the mesh the launch ended."""

    peer_id: str
    holder: str
    launch_id: str
    mesh_realm: str


class MeshPeerClaims(ABC):
    """Durable peer-id claims. ``claim`` is one atomic write."""

    @abstractmethod
    async def claim(
        self,
        *,
        peer_id: str,
        holder: str,
        launch_id: str,
        mesh_realm: str,
        expires_at: datetime,
    ) -> None:
        """Take (or renew) ``peer_id`` for ``holder``.

        Succeeds when no row exists, the row is held by the same ``holder`` (on any
        launch), or the row has expired. Raises :class:`PeerIdClaimedError` otherwise.
        """

    @abstractmethod
    async def release(self, *, holder: str, launch_id: str) -> list[PeerClaim]:
        """Delete the holder's claims made on this launch; return what was deleted.

        A holder that already moved on to a newer launch has no claim on the old one, so
        releasing a stale launch deletes nothing.
        """
