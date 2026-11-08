"""Shared mesh peer models (NIU-631, NIU-538).

``MeshIdentity`` is the identity a participant announces to its flock;
``MeshPeer`` is a discovered flock member and ``MeshCandidate`` an unverified
one.  They live in ``niuu`` so that every mesh participant (Ravn, Skuld, future
agents) shares one definition and none has to import another.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal


@dataclass
class MeshIdentity:
    """Own identity advertised to the flock during discovery.

    ``rep_address`` and ``pub_address`` are set by the active mesh adapter
    on startup so that peers know where to connect.  ``realm_id`` is the raw
    realm secret: it is never transmitted, only hashed before announcing.
    """

    peer_id: str
    realm_id: str
    persona: str
    capabilities: list[str]
    permission_mode: str
    version: str
    consumes_event_types: list[str] = field(default_factory=list)
    emits_event_types: list[str] = field(default_factory=list)
    rep_address: str | None = None
    pub_address: str | None = None
    spiffe_id: str | None = None
    display_name: str = ""
    participant_type: str = "ravn"
    sleipnir_routing_key: str | None = None
    #: What the participant is for, so a peer can choose whom to talk to. Both are facts the
    #: platform takes from the participant's persona and config, never from the agent.
    role: str = ""
    description: str = ""
    #: The participant's own activity state ("idle", "active", "awaiting_input", ...), kept
    #: current by the participant and carried on every heartbeat. Empty when unknown.
    activity_state: str = ""
    #: Whether the participant is working right now; announced as the peer's ``status``.
    busy: bool = False


@dataclass
class MeshCandidate:
    """Pre-handshake peer candidate discovered via mDNS or K8s (unverified).

    Carries enough information to attempt a handshake but has not yet proven
    realm membership.  ``realm_id_hash`` is SHA-256(realm_key)[:16] — the raw
    secret is never transmitted.
    """

    peer_id: str
    realm_id_hash: str  # SHA-256(realm_key)[:16] — not the raw secret
    host: str
    rep_address: str | None  # nng REP address
    pub_address: str | None  # nng PUB address
    handshake_port: int | None  # temp nng PAIR port for HMAC exchange
    metadata: dict = field(default_factory=dict)  # raw TXT records / pod annotations


@dataclass
class MeshPeer(MeshIdentity):
    """A verified (or pending/rejected) flock member.

    Extends ``MeshIdentity`` with trust metadata and liveness state.
    ``status`` and ``task_count`` are updated via heartbeat so that the
    cascade coordinator can pick idle peers.
    """

    trust_level: Literal["verified", "unverified", "rejected"] = "unverified"
    first_seen: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_seen: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_heartbeat: datetime = field(default_factory=lambda: datetime.now(UTC))
    latency_ms: float | None = None
    status: Literal["idle", "busy"] = "idle"
    task_count: int = 0
