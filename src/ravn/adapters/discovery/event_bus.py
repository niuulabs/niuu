"""Transport-neutral Ravn peer discovery over a Sleipnir event bus."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from ravn.domain.models import RavnCandidate, RavnPeer
from ravn.ports.discovery import PeerCallback
from sleipnir.domain.events import SleipnirEvent
from sleipnir.ports.events import SleipnirPublisher, SleipnirSubscriber, Subscription

_ANNOUNCE_EVENT_TYPE = "ravn.mesh.announce"


class EventBusDiscoveryAdapter:
    """Discover flock peers using whichever transport backs Sleipnir."""

    requires_sleipnir_transport = True

    def __init__(
        self,
        own_identity: Any,
        publisher: SleipnirPublisher,
        subscriber: SleipnirSubscriber,
        *,
        heartbeat_interval_s: float = 30.0,
        peer_ttl_s: float = 90.0,
        manage_transport_lifecycle: bool = True,
    ) -> None:
        self._identity = own_identity
        self._publisher = publisher
        self._subscriber = subscriber
        self._heartbeat_interval_s = heartbeat_interval_s
        self._peer_ttl_s = peer_ttl_s
        self._manage_transport_lifecycle = manage_transport_lifecycle
        self._peers: dict[str, RavnPeer] = {}
        self._on_join: list[PeerCallback] = []
        self._on_leave: list[PeerCallback] = []
        self._subscription: Subscription | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._manage_transport_lifecycle and hasattr(self._publisher, "start"):
            await self._publisher.start()  # type: ignore[attr-defined]
        if (
            self._manage_transport_lifecycle
            and self._subscriber is not self._publisher
            and hasattr(self._subscriber, "start")
        ):
            await self._subscriber.start()  # type: ignore[attr-defined]
        self._subscription = await self._subscriber.subscribe(
            [_ANNOUNCE_EVENT_TYPE], self._handle_announce
        )
        await self.announce()
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(),
            name=f"flock-discovery-{self._identity.peer_id}",
        )

    async def stop(self) -> None:
        await self._publish("leave")
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._heartbeat_task
            self._heartbeat_task = None
        if self._subscription is not None:
            await self._subscription.unsubscribe()
            self._subscription = None
        if self._manage_transport_lifecycle and hasattr(self._publisher, "stop"):
            await self._publisher.stop()  # type: ignore[attr-defined]
        if (
            self._manage_transport_lifecycle
            and self._subscriber is not self._publisher
            and hasattr(self._subscriber, "stop")
        ):
            await self._subscriber.stop()  # type: ignore[attr-defined]

    async def announce(self) -> None:
        await self._publish("join")

    async def scan(self) -> list[RavnCandidate]:
        return []

    async def watch(self, on_join: PeerCallback, on_leave: PeerCallback) -> None:
        self._on_join.append(on_join)
        self._on_leave.append(on_leave)

    async def handshake(self, candidate: RavnCandidate) -> RavnPeer | None:
        return self._peers.get(candidate.peer_id)

    def peers(self) -> dict[str, RavnPeer]:
        self._evict_stale()
        return dict(self._peers)

    async def own_identity(self) -> Any:
        return self._identity

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_interval_s)
            await self._publish("heartbeat")
            self._evict_stale()

    async def _publish(self, action: str) -> None:
        identity = {
            key: getattr(self._identity, key, None)
            for key in (
                "peer_id",
                "realm_id",
                "persona",
                "capabilities",
                "permission_mode",
                "version",
                "rep_address",
                "pub_address",
                "spiffe_id",
                "sleipnir_routing_key",
            )
        }
        await self._publisher.publish(
            SleipnirEvent(
                event_type=_ANNOUNCE_EVENT_TYPE,
                source=f"ravn:{self._identity.peer_id}",
                payload={"identity": identity, "action": action, "status": "idle", "task_count": 0},
                summary=f"Flock peer {action}: {self._identity.peer_id}",
                domain="code",
                urgency=0.3,
                timestamp=datetime.now(UTC),
            )
        )

    async def _handle_announce(self, event: SleipnirEvent) -> None:
        payload = event.payload
        identity = payload.get("identity")
        if not isinstance(identity, dict):
            return
        peer_id = str(identity.get("peer_id") or "")
        if not peer_id or peer_id == self._identity.peer_id:
            return
        if str(identity.get("realm_id") or "") != self._identity.realm_id:
            return
        if payload.get("action") == "leave":
            peer = self._peers.pop(peer_id, None)
            if peer is not None:
                for callback in self._on_leave:
                    with suppress(Exception):
                        callback(peer)
            return
        now = datetime.now(UTC)
        peer = self._peers.get(peer_id)
        if peer is not None:
            peer.last_seen = now
            peer.last_heartbeat = now
            peer.status = str(payload.get("status") or "idle")  # type: ignore[assignment]
            peer.task_count = int(payload.get("task_count") or 0)
            if payload.get("action") == "join":
                await self._publish("heartbeat")
            return
        peer = RavnPeer(
            peer_id=peer_id,
            realm_id=self._identity.realm_id,
            persona=str(identity.get("persona") or ""),
            capabilities=list(identity.get("capabilities") or []),
            permission_mode=str(identity.get("permission_mode") or ""),
            version=str(identity.get("version") or ""),
            rep_address=identity.get("rep_address"),
            pub_address=identity.get("pub_address"),
            spiffe_id=identity.get("spiffe_id"),
            sleipnir_routing_key=identity.get("sleipnir_routing_key"),
            trust_level="verified",
            first_seen=now,
            last_seen=now,
            last_heartbeat=now,
            status=str(payload.get("status") or "idle"),  # type: ignore[arg-type]
            task_count=int(payload.get("task_count") or 0),
        )
        self._peers[peer_id] = peer
        for callback in self._on_join:
            with suppress(Exception):
                callback(peer)
        if payload.get("action") == "join":
            await self._publish("heartbeat")

    def _evict_stale(self) -> None:
        now = datetime.now(UTC)
        stale = [
            peer_id
            for peer_id, peer in self._peers.items()
            if (now - peer.last_seen).total_seconds() > self._peer_ttl_s
        ]
        for peer_id in stale:
            peer = self._peers.pop(peer_id)
            for callback in self._on_leave:
                with suppress(Exception):
                    callback(peer)
