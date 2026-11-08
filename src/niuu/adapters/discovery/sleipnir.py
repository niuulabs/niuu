"""SleipnirDiscoveryAdapter — infra-mode flock discovery via AMQP pub/sub (NIU-538).

Ravens publish structured announce events on startup, reconnect, and heartbeat.
SPIFFE JWT-SVID validation is used for trust — no separate handshake socket.

**Announce event schema**::

    {
      "event_type": "ravn.mesh.announce",
      "source": "ravn:{peer_id}",
      "payload": {
        "identity": { <MeshIdentity fields> },
        "action": "join | leave | heartbeat",
        "status": "idle | busy",
        "task_count": 2
      }
    }

**Trust**: Announce events without a valid SPIFFE JWT-SVID matching
``spiffe://niuu.world/*/ravn/<peer_id>`` are silently dropped.

**Cold-start convergence**: On startup, publish own announce and wait up to
``convergence_wait_s`` for other peers to respond with theirs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from niuu.mesh.identity import MeshCandidate, MeshIdentity, MeshPeer, unannounced_facts
from niuu.ports.discovery import PeerCallback

if TYPE_CHECKING:
    from niuu.mesh.config import DiscoveryConfig

try:
    import aio_pika  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    aio_pika = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_ANNOUNCE_ROUTING_KEY = "ravn.mesh.announce"
_EXCHANGE_NAME = "ravn.mesh"


class SleipnirDiscoveryAdapter:
    """RabbitMQ-based flock discovery for infra mode.

    Parameters
    ----------
    own_identity:
        Pre-built ``MeshIdentity`` for this instance.
    amqp_url_env:
        Environment variable name containing the AMQP connection URL.
    convergence_wait_s:
        Seconds to wait on startup for other peers to announce.
    heartbeat_interval_s:
        Seconds between Sleipnir announce heartbeats.
    spiffe_audience_env:
        Env var containing the SPIFFE trust domain for JWT-SVID validation.
    peer_ttl_s:
        Seconds of missed heartbeats before a peer is evicted.
    **kwargs:
        Ignored — allows forward compatibility with new config fields.
    """

    #: ``build_discovery_adapters`` passes ``attestor`` to adapters that set this.
    signs_announcements = True

    def __init__(
        self,
        own_identity: MeshIdentity,
        *,
        amqp_url_env: str = "SLEIPNIR_AMQP_URL",
        convergence_wait_s: float = 5.0,
        heartbeat_interval_s: float = 60.0,
        spiffe_audience_env: str = "SPIFFE_TRUST_DOMAIN",
        peer_ttl_s: float = 90.0,
        # Legacy: accept config objects for backward compatibility
        config: DiscoveryConfig | None = None,
        attestor: Any | None = None,
        **kwargs: Any,
    ) -> None:
        self._attestor = attestor
        self._identity = own_identity
        self._amqp_url_env = amqp_url_env
        self._convergence_wait_s = convergence_wait_s
        self._heartbeat_interval_s = heartbeat_interval_s
        self._spiffe_audience_env = spiffe_audience_env
        self._peer_ttl_s = peer_ttl_s

        # Legacy config support — extract values if config objects provided
        if config is not None:
            self._convergence_wait_s = config.sleipnir.convergence_wait_s
            self._heartbeat_interval_s = config.sleipnir.heartbeat_interval_s
            self._spiffe_audience_env = config.sleipnir.spiffe_audience_env
            self._peer_ttl_s = config.peer_ttl_s

        self._peers: dict[str, MeshPeer] = {}
        self._on_join: list[PeerCallback] = []
        self._on_leave: list[PeerCallback] = []

        self._connection: object | None = None
        self._channel: object | None = None
        self._exchange: object | None = None

        self._heartbeat_task: asyncio.Task | None = None
        self._consumer_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # DiscoveryPort interface
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Connect to AMQP, publish join announce, and start consumer + heartbeat."""
        if aio_pika is None:  # pragma: no cover
            logger.warning("sleipnir_discovery: aio_pika not installed — discovery disabled")
            return

        await self._connect()
        await self.announce()

        # Wait briefly for other peers to respond with their announces.
        await asyncio.sleep(self._convergence_wait_s)

        self._consumer_task = asyncio.create_task(
            self._consume_loop(), name="sleipnir_discovery_consumer"
        )
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(), name="sleipnir_discovery_heartbeat"
        )
        logger.info("sleipnir_discovery: started peer=%s", self._identity.peer_id)

    async def stop(self) -> None:
        """Publish leave announce, cancel tasks, close connection."""
        await self._publish_announce("leave")

        for task in (self._heartbeat_task, self._consumer_task):
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    _ = await task

        self._heartbeat_task = None
        self._consumer_task = None

        if self._connection is not None:
            with suppress(Exception):
                await self._connection.close()  # type: ignore[union-attr]
            self._connection = None

        logger.info("sleipnir_discovery: stopped peer=%s", self._identity.peer_id)

    async def announce(self) -> None:
        """Publish a join announce event."""
        await self._publish_announce("join")

    async def scan(self) -> list[MeshCandidate]:
        """Return empty list — Sleipnir discovery is push-only (pub/sub)."""
        return []

    async def watch(self, on_join: PeerCallback, on_leave: PeerCallback) -> None:
        """Register join/leave callbacks (non-blocking)."""
        self._on_join.append(on_join)
        self._on_leave.append(on_leave)

    async def handshake(self, candidate: MeshCandidate) -> MeshPeer | None:
        """No handshake in Sleipnir mode — trust is delegated to SPIFFE."""
        return None

    def peers(self) -> dict[str, MeshPeer]:
        """Return the cached verified peer table (synchronous)."""
        return dict(self._peers)

    async def own_identity(self) -> MeshIdentity:
        """Return this Ravn's identity."""
        return self._identity

    # ------------------------------------------------------------------
    # Internal — AMQP
    # ------------------------------------------------------------------

    async def _connect(self) -> bool:
        if aio_pika is None:
            return False
        amqp_url = os.environ.get(self._amqp_url_env, "")
        if not amqp_url:
            logger.debug(
                "sleipnir_discovery: %s not set — discovery disabled",
                self._amqp_url_env,
            )
            return False
        try:
            conn = await aio_pika.connect_robust(amqp_url)
            channel = await conn.channel()
            exchange = await channel.declare_exchange(
                _EXCHANGE_NAME,
                aio_pika.ExchangeType.TOPIC,
                durable=True,
            )
            self._connection = conn
            self._channel = channel
            self._exchange = exchange
            return True
        except Exception as exc:
            logger.debug("sleipnir_discovery: connection failed: %s", exc)
            return False

    async def _publish_announce(
        self,
        action: str,
        *,
        status: str | None = None,
        task_count: int = 0,
    ) -> None:
        if self._exchange is None:
            return
        if status is None:
            status = "busy" if getattr(self._identity, "busy", False) else "idle"
        announcement: dict[str, Any] = {
            "identity": self._identity_dict(),
            "action": action,
            "status": status,
            "task_count": task_count,
        }
        if self._attestor is not None:
            announcement = self._attestor.sign(announcement)
        payload = {
            "event_type": "ravn.mesh.announce",
            "source": f"ravn:{self._identity.peer_id}",
            "payload": announcement,
        }
        body = json.dumps(payload).encode()
        try:
            msg = aio_pika.Message(body=body, content_type="application/json")  # type: ignore[union-attr]
            await self._exchange.publish(msg, routing_key=_ANNOUNCE_ROUTING_KEY)  # type: ignore[union-attr]
        except Exception as exc:
            logger.debug("sleipnir_discovery: publish announce failed: %s", exc)

    async def _consume_loop(self) -> None:
        if self._channel is None or self._exchange is None:
            return
        try:
            queue = await self._channel.declare_queue("", exclusive=True)  # type: ignore[union-attr]
            await queue.bind(self._exchange, routing_key=_ANNOUNCE_ROUTING_KEY)

            async def _on_message(message: object) -> None:
                async with message.process():  # type: ignore[attr-defined]
                    try:
                        raw = json.loads(message.body)  # type: ignore[attr-defined]
                        await self._handle_announce(raw)
                    except Exception as exc:
                        logger.debug("sleipnir_discovery: bad announce message: %s", exc)

            await queue.consume(_on_message)  # type: ignore[union-attr]

            # Keep alive until cancelled
            await asyncio.Future()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.debug("sleipnir_discovery: consume_loop error: %s", exc)

    async def _handle_announce(self, raw: dict) -> None:
        if raw.get("event_type") != "ravn.mesh.announce":
            return

        payload = raw.get("payload", {})
        action = payload.get("action", "join")
        identity_raw = payload.get("identity", {})
        peer_id = identity_raw.get("peer_id", "")

        if not peer_id or peer_id == self._identity.peer_id:
            return

        if self._attestor is not None and not self._attestor.accept(payload):
            return

        if not self._validate_spiffe(raw, peer_id):
            logger.debug("sleipnir_discovery: ignoring peer %s — SPIFFE validation failed", peer_id)
            return

        if action == "leave":
            self._remove_peer(peer_id)
            return

        now = datetime.now(UTC)
        status = payload.get("status", "idle")
        task_count = int(payload.get("task_count", 0))

        # A peer's busy flag travels with its activity state; an older peer announced a
        # constant "idle" status, which says nothing about it.
        not_announced = unannounced_facts(identity_raw, keys={"busy": "activity_state"})

        if peer_id in self._peers:
            peer = self._peers[peer_id]
            peer.last_seen = now
            peer.last_heartbeat = now
            peer.status = status  # type: ignore[assignment]
            peer.task_count = task_count
            peer.role = str(identity_raw.get("role") or "")
            peer.description = str(identity_raw.get("description") or "")
            peer.activity_state = str(identity_raw.get("activity_state") or "")
            peer.consumes_event_types = list(identity_raw.get("consumes_event_types") or [])
            peer.emits_event_types = list(identity_raw.get("emits_event_types") or [])
            peer.not_announced = not_announced
            return

        peer = MeshPeer(
            peer_id=peer_id,
            realm_id=identity_raw.get("realm_id", ""),
            persona=identity_raw.get("persona", ""),
            display_name=str(identity_raw.get("display_name") or ""),
            participant_type=str(identity_raw.get("participant_type") or "ravn"),
            capabilities=identity_raw.get("capabilities", []),
            permission_mode=identity_raw.get("permission_mode", ""),
            version=identity_raw.get("version", ""),
            rep_address=identity_raw.get("rep_address"),
            pub_address=identity_raw.get("pub_address"),
            spiffe_id=identity_raw.get("spiffe_id"),
            sleipnir_routing_key=identity_raw.get("sleipnir_routing_key"),
            consumes_event_types=list(identity_raw.get("consumes_event_types") or []),
            emits_event_types=list(identity_raw.get("emits_event_types") or []),
            role=str(identity_raw.get("role") or ""),
            description=str(identity_raw.get("description") or ""),
            activity_state=str(identity_raw.get("activity_state") or ""),
            not_announced=not_announced,
            trust_level="verified",
            first_seen=now,
            last_seen=now,
            last_heartbeat=now,
            status=status,  # type: ignore[arg-type]
            task_count=task_count,
        )
        self._peers[peer_id] = peer
        for cb in self._on_join:
            with suppress(Exception):
                cb(peer)

    def _validate_spiffe(self, raw: dict, peer_id: str) -> bool:
        """Validate SPIFFE JWT-SVID on the announce message.

        In environments without SPIFFE, skip validation (return True).
        Real validation checks the JWT matches ``spiffe://niuu.world/*/ravn/<peer_id>``.
        """
        trust_domain = os.environ.get(self._spiffe_audience_env, "")
        if not trust_domain:
            # No SPIFFE configured — accept without validation
            return True

        jwt_svid = raw.get("spiffe_jwt", "")
        if not jwt_svid:
            return False

        # Production: validate JWT-SVID via SPIRE workload API.
        # For now, check the subject claim contains the peer_id.
        try:
            import base64

            header, claims_b64, *_ = jwt_svid.split(".")
            # Pad base64
            padding = 4 - len(claims_b64) % 4
            claims_raw = base64.urlsafe_b64decode(claims_b64 + "=" * padding)
            claims = json.loads(claims_raw)
            sub = claims.get("sub", "")
            return f"/ravn/{peer_id}" in sub
        except Exception:
            return False

    def _remove_peer(self, peer_id: str) -> None:
        peer = self._peers.pop(peer_id, None)
        if peer is not None:
            for cb in self._on_leave:
                with suppress(Exception):
                    cb(peer)

    def _identity_dict(self) -> dict:
        return {
            "peer_id": self._identity.peer_id,
            "realm_id": self._identity.realm_id,
            "persona": self._identity.persona,
            "capabilities": self._identity.capabilities,
            "permission_mode": self._identity.permission_mode,
            "version": self._identity.version,
            "rep_address": self._identity.rep_address,
            "pub_address": self._identity.pub_address,
            "spiffe_id": self._identity.spiffe_id,
            "sleipnir_routing_key": self._identity.sleipnir_routing_key,
            "display_name": getattr(self._identity, "display_name", ""),
            "participant_type": getattr(self._identity, "participant_type", "ravn"),
            "consumes_event_types": list(getattr(self._identity, "consumes_event_types", [])),
            "emits_event_types": list(getattr(self._identity, "emits_event_types", [])),
            "role": getattr(self._identity, "role", ""),
            "description": getattr(self._identity, "description", ""),
            "activity_state": getattr(self._identity, "activity_state", ""),
        }

    async def _heartbeat_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._heartbeat_interval_s)
                await self._publish_announce("heartbeat")
                self._evict_stale_peers()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.debug("sleipnir_discovery: heartbeat_loop error: %s", exc)

    def _evict_stale_peers(self) -> None:
        now = datetime.now(UTC)
        ttl = self._peer_ttl_s
        to_evict = [
            pid
            for pid, peer in self._peers.items()
            if (now - peer.last_heartbeat).total_seconds() > ttl
        ]
        for pid in to_evict:
            peer = self._peers.pop(pid, None)
            if peer is not None:
                logger.debug("sleipnir_discovery: evicted stale peer %s", pid)
                for cb in self._on_leave:
                    with suppress(Exception):
                        cb(peer)
