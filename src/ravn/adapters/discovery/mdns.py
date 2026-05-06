"""MdnsDiscoveryAdapter — Pi-mode flock discovery via mDNS + HMAC handshake (NIU-538).

Uses ``zeroconf`` for mDNS service registration and browsing.

**Announcement**

Registers ``{peer_id}._ravn._tcp.local`` with TXT records:
- ``realm_id``       — SHA-256(realm_key)[:16]  (hash, not raw secret)
- ``peer_id``        — stable UUID
- ``persona``        — active persona name
- ``ver``            — ravn version
- ``rep_addr``       — nng REP address for mesh.send()
- ``pub_addr``       — nng PUB address for mesh.subscribe()
- ``handshake_port`` — temporary nng PAIR port for HMAC exchange

**Handshake (HMAC-SHA256 challenge-response)**

::

    A → B:  HELLO peer_id_A nonce_A
    B → A:  CHALLENGE HMAC(realm_key, "ravn-handshake|nonce_A|peer_id_A|peer_id_B") nonce_B
    A → B:  VERIFY HMAC(realm_key, "ravn-handshake|nonce_B|peer_id_B|peer_id_A")
            + full RavnIdentity JSON
    B → A:  ACCEPT + full RavnIdentity JSON   OR   REJECT

**Liveness**

Heartbeat fires every ``heartbeat_interval_s`` seconds (default 30s).
Peers are evicted when ``last_heartbeat`` is older than ``peer_ttl_s`` (default 90s).
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import hmac
import json
import logging
import secrets
import socket
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ravn.adapters.discovery._identity import (
    load_or_create_realm_key,
    realm_id_hash,
)
from ravn.domain.models import RavnCandidate, RavnIdentity, RavnPeer
from ravn.ports.discovery import PeerCallback

if TYPE_CHECKING:
    from ravn.config import DiscoveryConfig

try:
    from zeroconf import ServiceBrowser, ServiceInfo, Zeroconf
    from zeroconf.asyncio import AsyncServiceBrowser, AsyncZeroconf
except ImportError:  # pragma: no cover
    Zeroconf = None  # type: ignore[assignment,misc]
    AsyncZeroconf = None  # type: ignore[assignment]
    ServiceInfo = None  # type: ignore[assignment,misc]
    ServiceBrowser = None  # type: ignore[assignment,misc]
    AsyncServiceBrowser = None  # type: ignore[assignment,misc]

try:
    import pynng  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    pynng = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_HANDSHAKE_PREFIX = "ravn-handshake"


def _hmac_hex(key: bytes, *parts: str) -> str:
    """Compute HMAC-SHA256 of ``"|".join(parts)`` with *key*, return hex digest."""
    msg = "|".join(parts).encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def _local_ip() -> str:
    """Best-effort LAN IP address detection (falls back to 127.0.0.1)."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except Exception:
        return "127.0.0.1"


class MdnsDiscoveryAdapter:
    """mDNS-based flock discovery for Pi mode (zeroconf + nng HMAC handshake).

    Parameters
    ----------
    own_identity:
        Pre-built ``RavnIdentity`` for this instance (peer_id, realm_id,
        persona, capabilities, etc.).  ``rep_address`` and ``pub_address``
        should be populated by the mesh adapter before ``start()`` is called.
    handshake_port:
        Port for the nng PAIR socket used during handshake negotiation.
    service_type:
        mDNS service type for flock discovery (e.g. "_ravn._tcp.local.").
    handshake_timeout_s:
        Seconds to wait for HMAC handshake completion.
    heartbeat_interval_s:
        Seconds between liveness heartbeats.
    peer_ttl_s:
        Seconds of missed heartbeats before a peer is evicted.
    **kwargs:
        Ignored — allows forward compatibility with new config fields.
    """

    def __init__(
        self,
        own_identity: RavnIdentity,
        *,
        handshake_port: int = 7482,
        service_type: str = "_ravn._tcp.local.",
        handshake_timeout_s: float = 5.0,
        heartbeat_interval_s: float = 30.0,
        peer_ttl_s: float = 90.0,
        # Legacy: accept config object for backward compatibility
        config: DiscoveryConfig | None = None,
        **kwargs: Any,
    ) -> None:
        self._identity = own_identity
        self._handshake_port = handshake_port
        self._service_type = service_type
        self._handshake_timeout_s = handshake_timeout_s
        self._heartbeat_interval_s = heartbeat_interval_s
        self._peer_ttl_s = peer_ttl_s

        # Legacy config support — extract values if config object provided
        if config is not None:
            self._service_type = config.mdns.service_type
            self._handshake_timeout_s = config.mdns.handshake_timeout_s
            self._heartbeat_interval_s = config.heartbeat_interval_s
            self._peer_ttl_s = config.peer_ttl_s

        # Derive realm key — used only for HMAC, never transmitted raw.
        self._realm_key: bytes = load_or_create_realm_key()

        self._peers: dict[str, RavnPeer] = {}
        self._candidates: dict[str, RavnCandidate] = {}
        self._on_join: list[PeerCallback] = []
        self._on_leave: list[PeerCallback] = []

        self._zc: AsyncZeroconf | None = None
        self._browser: AsyncServiceBrowser | None = None
        self._service_info: ServiceInfo | None = None

        self._heartbeat_task: asyncio.Task | None = None
        self._handshake_listener_task: asyncio.Task | None = None
        self._pending_handshakes: set[str] = set()

    # ------------------------------------------------------------------
    # DiscoveryPort interface
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Register mDNS service and start background loops."""
        if AsyncZeroconf is None:  # pragma: no cover
            logger.warning("mdns_discovery: zeroconf not installed — discovery disabled")
            return

        self._zc = AsyncZeroconf()
        await self._register_service()
        await self._start_browser()

        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(), name="mdns_discovery_heartbeat"
        )
        self._handshake_listener_task = asyncio.create_task(
            self._handshake_listener(), name="mdns_discovery_hs_listener"
        )
        logger.info(
            "mdns_discovery: started peer=%s handshake_port=%d",
            self._identity.peer_id,
            self._handshake_port,
        )

    async def stop(self) -> None:
        """Unregister mDNS service and cancel background tasks."""
        for task in (self._heartbeat_task, self._handshake_listener_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        self._heartbeat_task = None
        self._handshake_listener_task = None

        if self._zc is not None:
            if self._service_info is not None:
                try:
                    await self._zc.async_unregister_service(self._service_info)
                except Exception:
                    pass
            try:
                await self._zc.async_close()
            except Exception:
                pass
            self._zc = None

        logger.info("mdns_discovery: stopped peer=%s", self._identity.peer_id)

    async def announce(self) -> None:
        """Re-register the mDNS service (e.g. after address change)."""
        if self._zc is None:
            return
        await self._register_service()

    async def scan(self) -> list[RavnCandidate]:
        """Return the current set of discovered candidates (snapshot)."""
        return list(self._candidates.values())

    async def watch(self, on_join: PeerCallback, on_leave: PeerCallback) -> None:
        """Register join/leave callbacks (non-blocking)."""
        self._on_join.append(on_join)
        self._on_leave.append(on_leave)

    async def handshake(self, candidate: RavnCandidate) -> RavnPeer | None:
        """Run HMAC handshake with *candidate* as the initiating side (A).

        Returns a verified ``RavnPeer`` on success, ``None`` on realm mismatch.
        """
        if pynng is None:  # pragma: no cover
            logger.debug("mdns_discovery: pynng not installed — handshake skipped")
            return None

        if candidate.handshake_port is None:
            logger.debug("mdns_discovery: candidate %s has no handshake_port", candidate.peer_id)
            return None

        addr = f"tcp://{candidate.host}:{candidate.handshake_port}"
        timeout_ms = int(self._handshake_timeout_s * 1000)

        try:
            return await asyncio.get_running_loop().run_in_executor(
                None,
                self._run_handshake_initiator,
                candidate,
                addr,
                timeout_ms,
            )
        except Exception as exc:
            logger.debug("mdns_discovery: handshake with %s failed: %s", candidate.peer_id, exc)
            return None

    def peers(self) -> dict[str, RavnPeer]:
        """Return the cached verified peer table (synchronous)."""
        return dict(self._peers)

    def find_peer_for_event_type(self, event_type: str) -> RavnPeer | None:
        """Find the first idle peer that consumes the given event type.

        Returns None if no matching peer is found or all matching peers are busy.
        Prefers idle peers over busy ones.
        """
        idle_match: RavnPeer | None = None
        busy_match: RavnPeer | None = None

        for peer in self._peers.values():
            if event_type in peer.consumes_event_types:
                if peer.status == "idle":
                    idle_match = peer
                    break  # Prefer first idle match
                if busy_match is None:
                    busy_match = peer

        return idle_match or busy_match

    async def own_identity(self) -> RavnIdentity:
        """Return this Ravn's identity."""
        return self._identity

    # ------------------------------------------------------------------
    # Internal — mDNS registration
    # ------------------------------------------------------------------

    def _build_txt_records(self) -> dict[str, str]:
        ip = _local_ip()
        records: dict[str, str] = {
            "realm_id": realm_id_hash(self._realm_key),
            "peer_id": self._identity.peer_id,
            "persona": self._identity.persona,
            "ver": self._identity.version,
            "handshake_port": str(self._handshake_port),
        }
        if self._identity.rep_address:
            records["rep_addr"] = self._identity.rep_address
        if self._identity.pub_address:
            records["pub_addr"] = self._identity.pub_address
        if not self._identity.rep_address and not self._identity.pub_address:
            records["host"] = ip
        # Include event types this persona consumes (for mesh routing)
        if self._identity.consumes_event_types:
            records["consumes"] = ",".join(self._identity.consumes_event_types)
        return records

    async def _register_service(self) -> None:
        if self._zc is None:
            return
        ip = _local_ip()
        txt = self._build_txt_records()
        service_name = f"{self._identity.peer_id}.{self._service_type}"
        info = ServiceInfo(  # type: ignore[call-arg]
            type_=self._service_type,
            name=service_name,
            addresses=[socket.inet_aton(ip)],
            port=self._handshake_port,
            properties={k: v.encode() for k, v in txt.items()},
        )
        try:
            await self._zc.async_register_service(info, ttl=60)
            self._service_info = info
            logger.debug(
                "mdns_discovery: registered %s at %s:%d", service_name, ip, self._handshake_port
            )
        except Exception as exc:
            logger.debug("mdns_discovery: service registration failed: %s", exc)

    async def _start_browser(self) -> None:
        if self._zc is None:
            return

        handlers = [self._on_service_state_change]
        self._browser = AsyncServiceBrowser(  # type: ignore[call-arg]
            self._zc.zeroconf,
            self._service_type,
            handlers=handlers,
        )

    def _on_service_state_change(
        self,
        zeroconf: object,
        service_type: str,
        name: str,
        state_change: object,
    ) -> None:
        """mDNS browse callback — fires on add/remove/update."""
        asyncio.get_running_loop().call_soon_threadsafe(
            asyncio.ensure_future,
            self._handle_service_event(zeroconf, service_type, name, state_change),
        )

    async def _handle_service_event(
        self,
        zeroconf: object,
        service_type: str,
        name: str,
        state_change: object,
    ) -> None:
        from zeroconf import ServiceStateChange

        if state_change == ServiceStateChange.Removed:
            peer_id = name.split(".")[0]
            self._remove_candidate(peer_id)
            return

        if state_change not in (ServiceStateChange.Added, ServiceStateChange.Updated):
            return

        info = ServiceInfo(type_=service_type, name=name)  # type: ignore[call-arg]
        try:
            await info.async_request(zeroconf, timeout=2000)  # type: ignore[attr-defined]
        except Exception:
            return

        props = info.decoded_properties  # type: ignore[attr-defined]
        peer_id = (props.get("peer_id") or "").strip()
        if not peer_id or peer_id == self._identity.peer_id:
            return

        rid_hash = (props.get("realm_id") or "").strip()
        own_hash = realm_id_hash(self._realm_key)
        if rid_hash != own_hash:
            logger.debug(
                "mdns_discovery: ignoring peer %s — realm hash mismatch (%s != %s)",
                peer_id,
                rid_hash,
                own_hash,
            )
            return

        addresses = info.parsed_addresses()  # type: ignore[attr-defined]
        host = addresses[0] if addresses else ""
        port = info.port  # type: ignore[attr-defined]
        candidate = RavnCandidate(
            peer_id=peer_id,
            realm_id_hash=rid_hash,
            host=host,
            rep_address=(props.get("rep_addr") or "").strip() or None,
            pub_address=(props.get("pub_addr") or "").strip() or None,
            handshake_port=port,
            metadata=dict(props),
        )
        self._candidates[peer_id] = candidate

        if peer_id not in self._peers and peer_id not in self._pending_handshakes:
            asyncio.ensure_future(self._initiate_handshake(candidate))

    def _remove_candidate(self, peer_id: str) -> None:
        self._candidates.pop(peer_id, None)
        peer = self._peers.pop(peer_id, None)
        if peer is not None:
            for cb in self._on_leave:
                try:
                    cb(peer)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Internal — handshake initiator (side A)
    # ------------------------------------------------------------------

    async def _initiate_handshake(self, candidate: RavnCandidate) -> None:
        """Initiate handshake with retry and jitter to handle PAIR socket contention.

        PAIR sockets are 1-to-1, so when multiple peers discover each other
        simultaneously, only one connection succeeds. Jitter + retries resolve this.
        """
        self._pending_handshakes.add(candidate.peer_id)
        try:
            # Random jitter (0-500ms) to stagger simultaneous discovery
            jitter = secrets.randbelow(500) / 1000.0
            await asyncio.sleep(jitter)

            max_retries = 3
            for attempt in range(max_retries):
                if candidate.peer_id in self._peers:
                    # Already verified (responder side completed first)
                    return

                peer = await self.handshake(candidate)
                if peer is not None:
                    self._add_peer(peer)
                    return

                if attempt < max_retries - 1:
                    # Exponential backoff with jitter: 0.5s, 1s, 2s base
                    backoff = (0.5 * (2**attempt)) + (secrets.randbelow(300) / 1000.0)
                    logger.debug(
                        "mdns_discovery: handshake attempt %d/%d failed for %s, retrying in %.2fs",
                        attempt + 1,
                        max_retries,
                        candidate.peer_id,
                        backoff,
                    )
                    await asyncio.sleep(backoff)

            logger.debug(
                "mdns_discovery: handshake with %s failed after %d attempts",
                candidate.peer_id,
                max_retries,
            )
        finally:
            self._pending_handshakes.discard(candidate.peer_id)

    def _run_handshake_initiator(
        self,
        candidate: RavnCandidate,
        addr: str,
        timeout_ms: int,
    ) -> RavnPeer | None:
        """Blocking HMAC handshake — runs in executor."""
        sock = pynng.Pair0(recv_timeout=timeout_ms, send_timeout=timeout_ms)  # type: ignore[attr-defined]
        try:
            sock.dial(addr)
            nonce_a = secrets.token_hex(16)
            own_id = self._identity.peer_id
            peer_id = candidate.peer_id

            # A → B: HELLO peer_id_A nonce_A
            sock.send(f"HELLO {own_id} {nonce_a}".encode())

            # B → A: CHALLENGE <hmac_b> <nonce_B>
            reply = sock.recv().decode()
            parts = reply.split(" ", 2)
            if len(parts) != 3 or parts[0] != "CHALLENGE":
                return None
            hmac_b, nonce_b = parts[1], parts[2]

            expected = _hmac_hex(
                self._realm_key,
                _HANDSHAKE_PREFIX,
                nonce_a,
                own_id,
                peer_id,
            )
            if not hmac.compare_digest(hmac_b, expected):
                logger.debug("mdns_discovery: CHALLENGE HMAC mismatch from %s", peer_id)
                return None

            # A → B: VERIFY <hmac_a> + identity JSON
            hmac_a = _hmac_hex(
                self._realm_key,
                _HANDSHAKE_PREFIX,
                nonce_b,
                peer_id,
                own_id,
            )
            identity_json = json.dumps(dataclasses.asdict(self._identity))
            sock.send(f"VERIFY {hmac_a} {identity_json}".encode())

            # B → A: ACCEPT <identity_json>  OR  REJECT
            final = sock.recv().decode()
            if final.startswith("REJECT"):
                return None

            if not final.startswith("ACCEPT "):
                return None

            peer_identity_raw = json.loads(final[len("ACCEPT ") :])
            return self._peer_from_identity_dict(peer_identity_raw, candidate)
        finally:
            sock.close()

    # ------------------------------------------------------------------
    # Internal — handshake listener (side B)
    # ------------------------------------------------------------------

    async def _handshake_listener(self) -> None:
        """Listen for incoming HMAC handshake requests from other Ravens."""
        if pynng is None:  # pragma: no cover
            return

        loop = asyncio.get_running_loop()
        sock = pynng.Pair0()  # type: ignore[attr-defined]
        try:
            sock.listen(f"tcp://*:{self._handshake_port}")
            while True:
                try:
                    data = await loop.run_in_executor(None, sock.recv)
                    await loop.run_in_executor(None, self._handle_handshake_responder, sock, data)
                except asyncio.CancelledError:
                    return
                except Exception as exc:
                    logger.debug("mdns_discovery: handshake_listener error: %s", exc)
                    await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            pass
        finally:
            sock.close()

    def _handle_handshake_responder(self, sock: object, data: bytes) -> None:
        """Blocking HMAC handshake — responder side (B)."""
        try:
            msg = data.decode()
            parts = msg.split(" ", 2)
            if len(parts) != 3 or parts[0] != "HELLO":
                return

            peer_id_a, nonce_a = parts[1], parts[2]
            own_id = self._identity.peer_id
            nonce_b = secrets.token_hex(16)

            hmac_b = _hmac_hex(
                self._realm_key,
                _HANDSHAKE_PREFIX,
                nonce_a,
                peer_id_a,
                own_id,
            )
            sock.send(f"CHALLENGE {hmac_b} {nonce_b}".encode())  # type: ignore[attr-defined]

            reply = sock.recv().decode()  # type: ignore[attr-defined]
            reply_parts = reply.split(" ", 2)
            if len(reply_parts) < 3 or reply_parts[0] != "VERIFY":
                sock.send(b"REJECT")  # type: ignore[attr-defined]
                return

            hmac_a_recv = reply_parts[1]
            identity_json = reply_parts[2]

            expected_a = _hmac_hex(
                self._realm_key,
                _HANDSHAKE_PREFIX,
                nonce_b,
                own_id,
                peer_id_a,
            )
            if not hmac.compare_digest(hmac_a_recv, expected_a):
                logger.debug("mdns_discovery: VERIFY HMAC mismatch from %s", peer_id_a)
                sock.send(b"REJECT")  # type: ignore[attr-defined]
                return

            try:
                peer_identity_raw = json.loads(identity_json)
            except Exception:
                sock.send(b"REJECT")  # type: ignore[attr-defined]
                return

            own_identity_json = json.dumps(dataclasses.asdict(self._identity))
            sock.send(f"ACCEPT {own_identity_json}".encode())  # type: ignore[attr-defined]

            candidate = self._candidates.get(peer_id_a)
            peer = self._peer_from_identity_dict(peer_identity_raw, candidate)
            asyncio.get_running_loop().call_soon_threadsafe(self._add_peer, peer)
        except Exception as exc:
            logger.debug("mdns_discovery: responder error: %s", exc)

    # ------------------------------------------------------------------
    # Internal — peer table management
    # ------------------------------------------------------------------

    def _add_peer(self, peer: RavnPeer) -> None:
        is_new = peer.peer_id not in self._peers
        self._peers[peer.peer_id] = peer
        if is_new:
            for cb in self._on_join:
                try:
                    cb(peer)
                except Exception:
                    pass

    def _peer_from_identity_dict(
        self,
        raw: dict,
        candidate: RavnCandidate | None,
    ) -> RavnPeer:
        now = datetime.now(UTC)
        peer = RavnPeer(
            peer_id=raw.get("peer_id", ""),
            realm_id=raw.get("realm_id", ""),
            persona=raw.get("persona", ""),
            capabilities=raw.get("capabilities", []),
            permission_mode=raw.get("permission_mode", ""),
            version=raw.get("version", ""),
            consumes_event_types=raw.get("consumes_event_types", []),
            rep_address=raw.get("rep_address") or (candidate.rep_address if candidate else None),
            pub_address=raw.get("pub_address") or (candidate.pub_address if candidate else None),
            spiffe_id=raw.get("spiffe_id"),
            sleipnir_routing_key=raw.get("sleipnir_routing_key"),
            trust_level="verified",
            first_seen=now,
            last_seen=now,
            last_heartbeat=now,
        )
        return peer

    # ------------------------------------------------------------------
    # Internal — heartbeat + eviction
    # ------------------------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._heartbeat_interval_s)
                await self.announce()
                self._evict_stale_peers()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.debug("mdns_discovery: heartbeat_loop error: %s", exc)

    def _evict_stale_peers(self) -> None:
        now = datetime.now(UTC)
        ttl = self._peer_ttl_s
        to_evict = [
            peer_id
            for peer_id, peer in self._peers.items()
            if (now - peer.last_heartbeat).total_seconds() > ttl
        ]
        for peer_id in to_evict:
            peer = self._peers.pop(peer_id, None)
            if peer is not None:
                logger.debug("mdns_discovery: evicted stale peer %s", peer_id)
                for cb in self._on_leave:
                    try:
                        cb(peer)
                    except Exception:
                        pass

    def update_peer_heartbeat(
        self,
        peer_id: str,
        *,
        status: str = "idle",
        task_count: int = 0,
    ) -> None:
        """Update liveness state for *peer_id* (called on heartbeat receipt)."""
        peer = self._peers.get(peer_id)
        if peer is None:
            return
        now = datetime.now(UTC)
        # RavnPeer is a dataclass, reassign fields
        peer.last_seen = now
        peer.last_heartbeat = now
        peer.status = status  # type: ignore[assignment]
        peer.task_count = task_count
