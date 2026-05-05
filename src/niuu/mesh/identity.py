"""Shared mesh peer identity model (NIU-631).

``MeshIdentity`` mirrors ``RavnIdentity`` but lives in ``niuu`` so that both
Ravn and Skuld can construct peer identities without cross-importing between
the two modules.

Discovery adapters accept ``own_identity: Any`` and read fields by name, so
``MeshIdentity`` is a drop-in replacement for ``RavnIdentity`` at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MeshIdentity:
    """Own identity advertised to the flock during discovery.

    Fields mirror ``ravn.domain.models.RavnIdentity`` so this type is
    accepted by all discovery adapters that take ``own_identity``.
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
    sleipnir_routing_key: str | None = None
