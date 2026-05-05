"""StaticDiscoveryAdapter — file-based peer discovery for cross-network setups.

Reads peer definitions from a YAML file (cluster.yaml). Useful for environments
where mDNS multicast doesn't work (cross-VPC, cross-cluster, cloud functions).

**File format**::

    peers:
      - peer_id: ravn-eu-west-1
        host: 10.0.1.50
        persona: coding-agent
        capabilities:
          - bash
          - file
          - git
        permission_mode: workspace_write
        rep_address: tcp://10.0.1.50:7481
        pub_address: tcp://10.0.1.50:7480
        webhook_url: http://10.0.1.50:7490/ravn/mesh
        consumes_event_types:
          - code.changed

**Trust**: Peers in the file are implicitly trusted — no handshake is performed.
The file author is responsible for ensuring only trusted peers are listed.

**Hot-reload**: When ``poll_interval_s > 0``, the adapter periodically re-reads
the file and updates the peer table (join/leave callbacks fire as needed).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from ravn.domain.models import RavnCandidate, RavnIdentity, RavnPeer
from ravn.ports.discovery import PeerCallback

if TYPE_CHECKING:
    from ravn.config import DiscoveryConfig

logger = logging.getLogger(__name__)


class StaticDiscoveryAdapter:
    """File-based peer discovery for cross-network deployments.

    Parameters
    ----------
    own_identity:
        Pre-built ``RavnIdentity`` for this instance.
    cluster_file:
        Path to the cluster.yaml peer definition file.
    poll_interval_s:
        Seconds between file re-reads for hot-reload. Set to 0 to disable.
    heartbeat_interval_s:
        Ignored — static discovery doesn't send heartbeats.
    peer_ttl_s:
        Ignored — static peers don't expire.
    **kwargs:
        Ignored — allows forward compatibility with new config fields.
    """

    def __init__(
        self,
        own_identity: RavnIdentity,
        *,
        cluster_file: str = "~/.ravn/cluster.yaml",
        poll_interval_s: float = 30.0,
        heartbeat_interval_s: float = 30.0,  # noqa: ARG002 — ignored
        peer_ttl_s: float = 90.0,  # noqa: ARG002 — ignored
        # Legacy: accept config object for backward compatibility
        config: DiscoveryConfig | None = None,  # noqa: ARG002 — ignored
        **kwargs: Any,  # noqa: ARG002 — ignored
    ) -> None:
        self._identity = own_identity
        self._cluster_file = Path(cluster_file).expanduser()
        self._poll_interval_s = poll_interval_s

        self._peers: dict[str, RavnPeer] = {}
        self._on_join: list[PeerCallback] = []
        self._on_leave: list[PeerCallback] = []

        self._poll_task: asyncio.Task | None = None
        self._last_mtime: float = 0.0

    # ------------------------------------------------------------------
    # DiscoveryPort interface
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Load cluster.json and optionally start the poll loop."""
        await self._load_peers()

        if self._poll_interval_s > 0:
            self._poll_task = asyncio.create_task(self._poll_loop(), name="static_discovery_poll")

        logger.info(
            "static_discovery: started peer=%s cluster_file=%s peers=%d",
            self._identity.peer_id,
            self._cluster_file,
            len(self._peers),
        )

    async def stop(self) -> None:
        """Cancel the poll loop if running."""
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

        logger.info("static_discovery: stopped peer=%s", self._identity.peer_id)

    async def announce(self) -> None:
        """No-op — static discovery doesn't announce."""

    async def scan(self) -> list[RavnCandidate]:
        """Return all peers as candidates (they're pre-verified by being in the file)."""
        return [
            RavnCandidate(
                peer_id=peer.peer_id,
                realm_id_hash="",  # Static peers don't have realm hash
                host=peer.rep_address.split("://")[1].split(":")[0] if peer.rep_address else "",
                rep_address=peer.rep_address,
                pub_address=peer.pub_address,
                handshake_port=None,
                metadata={},
            )
            for peer in self._peers.values()
        ]

    async def watch(self, on_join: PeerCallback, on_leave: PeerCallback) -> None:
        """Register join/leave callbacks (non-blocking)."""
        self._on_join.append(on_join)
        self._on_leave.append(on_leave)

    async def handshake(self, candidate: RavnCandidate) -> RavnPeer | None:
        """Return the pre-verified peer if it exists in our table."""
        return self._peers.get(candidate.peer_id)

    def peers(self) -> dict[str, RavnPeer]:
        """Return the current peer table (synchronous)."""
        return dict(self._peers)

    async def own_identity(self) -> RavnIdentity:
        """Return this Ravn's identity."""
        return self._identity

    # ------------------------------------------------------------------
    # Internal — file loading
    # ------------------------------------------------------------------

    async def _load_peers(self) -> None:
        """Load peers from cluster.yaml and fire join/leave callbacks."""
        if not self._cluster_file.exists():
            logger.debug("static_discovery: cluster file not found: %s", self._cluster_file)
            return

        try:
            content = self._cluster_file.read_text(encoding="utf-8")
            data = yaml.safe_load(content) or {}
            self._last_mtime = self._cluster_file.stat().st_mtime
        except Exception as exc:
            logger.warning("static_discovery: failed to read cluster file: %s", exc)
            return

        if not isinstance(data, dict) or "peers" not in data:
            logger.warning("static_discovery: invalid cluster file format (missing 'peers' key)")
            return

        new_peers: dict[str, RavnPeer] = {}
        now = datetime.now(UTC)

        for entry in data.get("peers", []):
            if not isinstance(entry, dict):
                continue

            peer_id = entry.get("peer_id", "")
            if not peer_id or peer_id == self._identity.peer_id:
                continue  # Skip self and invalid entries

            peer = RavnPeer(
                peer_id=peer_id,
                realm_id=self._identity.realm_id,  # Assume same realm
                persona=entry.get("persona", "unknown"),
                capabilities=entry.get("capabilities", []),
                permission_mode=entry.get("permission_mode", "read_only"),
                version=entry.get("version", "0.0.0"),
                consumes_event_types=entry.get("consumes_event_types", []),
                emits_event_types=entry.get("emits_event_types", []),
                rep_address=entry.get("rep_address"),
                pub_address=entry.get("pub_address"),
                spiffe_id=entry.get("spiffe_id"),
                sleipnir_routing_key=entry.get("sleipnir_routing_key"),
                trust_level="verified",  # File = implicit trust
                first_seen=now,
                last_seen=now,
                last_heartbeat=now,
            )
            new_peers[peer_id] = peer

        # Compute diff and fire callbacks
        old_ids = set(self._peers.keys())
        new_ids = set(new_peers.keys())

        # Peers that left
        for peer_id in old_ids - new_ids:
            peer = self._peers[peer_id]
            for cb in self._on_leave:
                try:
                    cb(peer)
                except Exception:
                    pass
            logger.debug("static_discovery: peer left: %s", peer_id)

        # Peers that joined
        for peer_id in new_ids - old_ids:
            peer = new_peers[peer_id]
            for cb in self._on_join:
                try:
                    cb(peer)
                except Exception:
                    pass
            logger.debug("static_discovery: peer joined: %s", peer_id)

        self._peers = new_peers

    async def _poll_loop(self) -> None:
        """Periodically check for file changes and reload."""
        while True:
            try:
                await asyncio.sleep(self._poll_interval_s)

                if not self._cluster_file.exists():
                    continue

                try:
                    current_mtime = self._cluster_file.stat().st_mtime
                except Exception:
                    continue

                if current_mtime > self._last_mtime:
                    logger.debug("static_discovery: cluster file changed, reloading")
                    await self._load_peers()

            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.debug("static_discovery: poll_loop error: %s", exc)
