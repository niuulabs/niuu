"""RegistryDiscoveryAdapter: the peers on this machine, found through one shared file.

Every session that runs on the same host registers itself in a JSON file
(:class:`sleipnir.adapters.discovery.ServiceRegistry`, advisory-locked, dead processes
pruned by pid) with its nng PUB address and the facts a peer chooses by (name, role,
topics, activity). Each session reads the same file, so the peer table has the sessions on
the host, each with the address to dial, and nothing is needed but the path.

This is the discovery for processes that share a machine and its file system: the mini-mode
platform, a registered host or VM that runs several sessions. Machines that cannot share a
file use ``k8s`` (or another adapter that carries ``pub_address``).

Failures are loud (``niuu.adapters.discovery._failures``): a registry file that cannot be
written, or a session whose PUB address is not known when discovery starts, raises
``DiscoveryError`` from ``start()``; a poll that fails is reported on every pass and the peer
table stays as it was.

Trust: the file is writable only by the user the sessions run as (mode 0600), and what proves
a sender is the Forge-signed certificate on each message, never this file.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from niuu.adapters.discovery._failures import DiscoveryError, notify, report_failure
from niuu.mesh.identity import MeshCandidate, MeshIdentity, MeshPeer
from niuu.ports.discovery import PeerCallback
from sleipnir.adapters.discovery import ServiceEntry, ServiceRegistry

logger = logging.getLogger(__name__)

#: The identity facts a peer registers (the raw realm is never written, only its hash).
_IDENTITY_FIELDS = (
    "persona",
    "display_name",
    "participant_type",
    "capabilities",
    "permission_mode",
    "version",
    "rep_address",
    "spiffe_id",
    "sleipnir_routing_key",
    "consumes_event_types",
    "emits_event_types",
    "role",
    "description",
    "activity_state",
    "busy",
)


def realm_hash(realm_id: str) -> str:
    """The realm as it is written to the file: a prefix of its SHA-256."""
    return hashlib.sha256(realm_id.encode("utf-8")).hexdigest()[:16]


class RegistryDiscoveryAdapter:
    """Peer discovery through a registry file shared by the sessions of one host.

    Parameters
    ----------
    own_identity:
        This session's identity. ``pub_address`` must be set before ``start()``: it is the
        address peers dial.
    path:
        The registry file, shared by every session on the host. Required: the pod manager
        that starts the session decides it.
    poll_interval_s:
        Seconds between reads of the file (and re-registration when the identity changed).
        A new peer is dialed at the next pass, so this bounds how long a new session is
        unheard.
    **kwargs:
        Ignored (``heartbeat_interval_s``, ``peer_ttl_s`` and the like the builder passes).
    """

    def __init__(
        self,
        own_identity: MeshIdentity,
        *,
        path: str,
        poll_interval_s: float = 1.0,
        **kwargs: Any,  # noqa: ARG002 - the builder passes every adapter the same defaults
    ) -> None:
        if not path:
            raise DiscoveryError(
                "registry discovery needs `path`, the file the sessions of this host share "
                "(mesh.discovery_adapters: [{adapter: registry, path: ...}])"
            )
        if poll_interval_s <= 0:
            raise DiscoveryError("registry discovery poll_interval_s must be positive")
        self._identity = own_identity
        self._path = Path(path).expanduser()
        self._poll_interval_s = poll_interval_s
        self._registry = ServiceRegistry(self._path)
        self._peers: dict[str, MeshPeer] = {}
        self._on_join: list[PeerCallback] = []
        self._on_leave: list[PeerCallback] = []
        self._registered: dict[str, Any] | None = None
        self._poll_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # DiscoveryPort
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Register this session, read the peers on the host and keep following them."""
        if not self._identity.pub_address:
            raise DiscoveryError(
                "registry discovery has no nng PUB address to register for "
                f"{self._identity.peer_id}: the mesh transport must bind before discovery "
                "starts. Use `transport: nng`, or a discovery adapter that needs no address"
            )
        await self._register()
        await self._read_peers()
        self._poll_task = asyncio.create_task(self._poll_loop(), name="registry_discovery_poll")
        logger.info(
            "registry_discovery: started peer=%s registry=%s peers=%d",
            self._identity.peer_id,
            self._path,
            len(self._peers),
        )

    async def stop(self) -> None:
        """Stop following the file and take this session out of it."""
        if self._poll_task is not None:
            self._poll_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._poll_task
            self._poll_task = None
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self._registry.deregister, self._identity.peer_id)
        except OSError as exc:
            raise DiscoveryError(
                f"registry discovery could not remove {self._identity.peer_id} from "
                f"{self._path}: {exc}; peers drop it when its process is gone"
            ) from exc
        self._registered = None
        logger.info("registry_discovery: stopped peer=%s", self._identity.peer_id)

    async def announce(self) -> None:
        """Write this session's current identity to the file."""
        await self._register(force=True)

    async def scan(self) -> list[MeshCandidate]:
        """The peers on the host, as candidates (they are pre-verified by being in the file)."""
        return [
            MeshCandidate(
                peer_id=peer.peer_id,
                realm_id_hash=realm_hash(peer.realm_id),
                host="",
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

    async def handshake(self, candidate: MeshCandidate) -> MeshPeer | None:
        """No handshake: a session on the same host and user, in the same realm, is trusted."""
        return self._peers.get(candidate.peer_id)

    def peers(self) -> dict[str, MeshPeer]:
        """The peers on this host (synchronous snapshot)."""
        return dict(self._peers)

    async def own_identity(self) -> MeshIdentity:
        return self._identity

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _info(self) -> dict[str, Any]:
        info = {name: getattr(self._identity, name, None) for name in _IDENTITY_FIELDS}
        info["realm_hash"] = realm_hash(self._identity.realm_id)
        return info

    async def _register(self, *, force: bool = False) -> None:
        info = self._info()
        if not force and info == self._registered:
            return
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None,
                self._registry.register,
                self._identity.peer_id,
                self._identity.pub_address,
                info,
            )
        except OSError as exc:
            raise DiscoveryError(
                f"registry discovery cannot write {self._path}: {exc}. Make the directory "
                "writable for the user the sessions run as, or set `path` elsewhere"
            ) from exc
        self._registered = info

    async def _read_peers(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            entries = await loop.run_in_executor(None, self._registry.list_services)
        except OSError as exc:
            raise DiscoveryError(f"registry discovery cannot read {self._path}: {exc}") from exc
        own_realm = realm_hash(self._identity.realm_id)
        now = datetime.now(UTC)
        found: dict[str, MeshPeer] = {}
        for entry in entries:
            if entry.id == self._identity.peer_id:
                continue
            if entry.info.get("realm_hash") != own_realm:
                continue  # another flock on this host: the expected steady state
            found[entry.id] = self._peer(entry, now)

        for peer_id in set(self._peers) - set(found):
            notify("registry", "leave", self._on_leave, self._peers[peer_id])
        joined = [found[peer_id] for peer_id in found if peer_id not in self._peers]
        self._peers = found
        for peer in joined:
            notify("registry", "join", self._on_join, peer)

    def _peer(self, entry: ServiceEntry, now: datetime) -> MeshPeer:
        info = entry.info
        previous = self._peers.get(entry.id)
        return MeshPeer(
            peer_id=entry.id,
            realm_id=self._identity.realm_id,
            persona=str(info.get("persona") or ""),
            display_name=str(info.get("display_name") or ""),
            participant_type=str(info.get("participant_type") or "skuld"),
            capabilities=list(info.get("capabilities") or []),
            permission_mode=str(info.get("permission_mode") or ""),
            version=str(info.get("version") or ""),
            rep_address=info.get("rep_address"),
            pub_address=entry.socket,
            spiffe_id=info.get("spiffe_id"),
            sleipnir_routing_key=info.get("sleipnir_routing_key"),
            consumes_event_types=list(info.get("consumes_event_types") or []),
            emits_event_types=list(info.get("emits_event_types") or []),
            role=str(info.get("role") or ""),
            description=str(info.get("description") or ""),
            activity_state=str(info.get("activity_state") or ""),
            status="busy" if info.get("busy") else "idle",
            trust_level="verified",
            first_seen=previous.first_seen if previous else now,
            last_seen=now,
            last_heartbeat=now,
        )

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self._poll_interval_s)
            try:
                await self._register()
                await self._read_peers()
            except asyncio.CancelledError:
                raise
            except DiscoveryError as exc:
                report_failure("registry", "poll", str(exc))
