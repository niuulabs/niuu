"""SkuldMeshAdapter — mesh peer bridge for Skuld sessions (NIU-612).

Gives Skuld a mesh identity so it can participate in the mesh as a peer. What other peers
send this session lands in the session's peer inbox (``skuld.peer_inbox``) and reaches the
harness at the next turn boundary like any other peer message:

* an RPC of type ``peer_message`` carries a :class:`~niuu.domain.peer_message.PeerMessage`;
* a ``work_request`` RPC and an outcome event on a consumed topic become a peer message from
  the mesh peer, whose reply route is the mesh back to it.

Nothing here waits for the harness. A sender is told its message is held; the answer comes
later, from the harness, through the ``reply`` tool.

NIU-634: wired to use niuu.mesh.MeshParticipant for lifecycle management.
"""

from __future__ import annotations

import logging
import socket
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

from niuu.domain.peer_message import (
    PEER_MESSAGE_FRAME,
    PeerAddress,
    PeerKind,
    PeerMessage,
    PeerMessageError,
    ReplyKind,
    ReplyRoute,
)
from niuu.mesh.attestation import VERIFIED_SENDER_KEY
from niuu.mesh.events import MeshEvent, MeshEventType
from niuu.mesh.identity import MeshPeer
from niuu.mesh.participant import MeshParticipant
from niuu.ports.mesh import MeshPort
from skuld.config import MeshConfig
from skuld.mesh_topics import IN_REPLY_TO_KEY, inbox_topic
from skuld.peer_inbox import (
    CHANNEL_MESH,
    PEER_STATE_RPC_TYPE,
    PeerAcceptance,
    PeerMessageRefusedError,
    SenderIdentity,
)
from sleipnir.ports.events import SleipnirSubscriber

logger = logging.getLogger("skuld.mesh_adapter")

#: A broker-side answer to one mesh RPC ``type`` a mesh peer may send.
RpcHandler = Callable[[dict], Awaitable[dict]]


class PeerMessageReceiver(Protocol):
    """The session's peer inbox, as the mesh sees it."""

    async def accept_peer_message(
        self,
        envelope: PeerMessage,
        request_id: str | None = None,
        *,
        channel: str | None = None,
        verified: SenderIdentity | None = None,
    ) -> PeerAcceptance:
        """Hold ``envelope`` for the next turn boundary; raises when it is refused."""


class SkuldMeshAdapter:
    """Bridge between the mesh and the session's peer inbox.

    Lifecycle:
        start() — start participant (mesh + discovery), subscribe to task topics
        stop()  — unsubscribe, stop participant (mesh + discovery)
    """

    def __init__(
        self,
        participant: MeshParticipant,
        config: MeshConfig,
        session_id: str,
        peer_inbox: PeerMessageReceiver,
        rpc_handlers: Mapping[str, RpcHandler] | None = None,
        consumes_event_types: Sequence[str] | None = None,
    ) -> None:
        self._participant = participant
        self._rpc_handlers = dict(rpc_handlers or {})
        self._mesh: MeshPort | None = participant.mesh
        self._peer_inbox = peer_inbox
        self._config = config
        self._session_id = session_id
        # The topics subscribed to: the session's declaration when the composition root
        # resolved one (its persona's `consumes` on top of the config), else the config's.
        self._consumes = tuple(
            config.consumes_event_types if consumes_event_types is None else consumes_event_types
        )
        self._peer_id = participant.peer_id or config.peer_id or socket.gethostname()
        self._running = False
        # Topics followed at runtime (``mesh_subscribe``), on top of the standing ones.
        self._followed: set[str] = set()

    @property
    def peer_id(self) -> str:
        return self._peer_id

    def peers(self) -> dict[str, MeshPeer]:
        """The verified peer table, keyed by peer id; empty when no discovery is configured."""
        discovery = self._participant.discovery
        if discovery is None:
            return {}
        return dict(discovery.peers())

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def sleipnir_subscriber(self) -> SleipnirSubscriber | None:
        """Expose the Sleipnir subscriber from the underlying mesh transport.

        Returns None when the mesh is not a SleipnirMeshAdapter (e.g. in-process
        test mesh that has no dedicated subscriber port).
        """
        return getattr(self._mesh, "subscriber", None)

    async def start(self) -> None:
        """Start participant (mesh + discovery) and subscribe to task topics."""
        if self._running:
            return

        await self._participant.start()
        logger.info("mesh adapter: participant started (peer_id=%s)", self._peer_id)

        # Set up RPC handler for work_request messages
        if self._mesh is not None and hasattr(self._mesh, "set_rpc_handler"):
            self._mesh.set_rpc_handler(self._handle_rpc)
            logger.info("mesh adapter: RPC handler registered")

        # Subscribe to consumed event types, and to the topic answers to this peer's own
        # events arrive on (nobody has to subscribe to anything in advance to be answered).
        if self._mesh is not None:
            for event_type in (*self._consumes, self.inbox_topic):
                await self._mesh.subscribe(event_type, self._handle_outcome_event)
                logger.info("mesh adapter: subscribed to topic %r", event_type)

        self._running = True
        logger.info(
            "mesh adapter: started (peer_id=%s, capabilities=%s, consumes=%s)",
            self._peer_id,
            self._config.capabilities,
            list(self._consumes),
        )

    async def stop(self) -> None:
        """Unsubscribe from topics and stop participant (mesh + discovery)."""
        if not self._running:
            return

        self._running = False

        if self._mesh is not None:
            for event_type in (*self._consumes, self.inbox_topic, *sorted(self._followed)):
                await self._mesh.unsubscribe(event_type)

        await self._participant.stop()

        logger.info("mesh adapter: stopped (peer_id=%s)", self._peer_id)

    async def publish(self, event: MeshEvent, topic: str) -> None:
        """Publish an event onto the mesh when the adapter is active."""
        if self._mesh is None:
            return
        await self._mesh.publish(event, topic=topic)

    @property
    def inbox_topic(self) -> str:
        """Where answers addressed to this peer arrive; it always listens here."""
        return inbox_topic(self._peer_id)

    @property
    def standing_topics(self) -> tuple[str, ...]:
        """The topics subscribed to from the start: declared, never changed at runtime."""
        return self._consumes

    @property
    def followed_topics(self) -> tuple[str, ...]:
        """The topics followed at runtime, in name order."""
        return tuple(sorted(self._followed))

    async def follow(self, topic: str) -> bool:
        """Start listening to ``topic`` now. False when this peer already listened to it."""
        if topic in self._consumes or topic in self._followed:
            return False
        if not self._running or self._mesh is None:
            raise RuntimeError(
                "the mesh is not running, so the topic was not followed; check the broker log "
                "for why the mesh adapter did not start"
            )
        await self._mesh.subscribe(topic, self._handle_outcome_event)
        self._followed.add(topic)
        logger.info("mesh adapter: following topic %r", topic)
        return True

    async def unfollow(self, topic: str) -> bool:
        """Stop listening to a topic followed at runtime. False when it was not followed."""
        if topic not in self._followed:
            return False
        if self._mesh is None:
            raise RuntimeError("the mesh is not running, so the topic was not unfollowed")
        await self._mesh.unsubscribe(topic)
        self._followed.discard(topic)
        logger.info("mesh adapter: stopped following topic %r", topic)
        return True

    async def publish_event(self, event: MeshEvent, topic: str) -> None:
        """Publish an event the caller needs to know left this peer.

        Unlike :meth:`publish`, a mesh that is not running raises instead of being skipped,
        and so does a transport that refuses the event. Nothing waits for a consumer.
        """
        if not self._running or self._mesh is None:
            raise RuntimeError(
                "the mesh is not running, so the event was not published; check the broker "
                "log for why the mesh adapter did not start"
            )
        await self._mesh.publish(event, topic=topic)

    async def send_directed_message(
        self,
        target_peer_id: str,
        content: str,
        *,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        """Deliver operator input without awaiting the recipient's model turn."""
        if self._mesh is None:
            return {"status": "error", "error": "Mesh is unavailable"}
        return await self._mesh.send(
            target_peer_id,
            {
                "type": "directed_message",
                "content": content,
                "metadata": metadata,
            },
            timeout_s=self._config.rpc_timeout_s,
        )

    async def send_peer_message(self, target_peer_id: str, message: PeerMessage) -> dict[str, Any]:
        """Send a stamped peer message envelope to a mesh peer and return its answer.

        Raises ``RuntimeError`` when the mesh is unavailable; a transport failure or a peer
        that does not know the peer, or the type, surfaces from the mesh or in the answer.
        """
        if self._mesh is None:
            raise RuntimeError("Mesh is unavailable")
        return await self._mesh.send(
            target_peer_id,
            {"type": PEER_MESSAGE_FRAME, "envelope": message.to_wire()},
            timeout_s=self._config.rpc_timeout_s,
        )

    async def request_peer_state(self, target_peer_id: str) -> dict[str, Any]:
        """Ask a peer's Skuld what it is doing (``peer_state`` RPC); raises like ``send``."""
        if self._mesh is None:
            raise RuntimeError("Mesh is unavailable")
        return await self._mesh.send(
            target_peer_id, {"type": PEER_STATE_RPC_TYPE}, timeout_s=self._config.rpc_timeout_s
        )

    async def _handle_rpc(self, message: dict) -> dict:
        """Handle incoming RPC messages (peer_message, work_request, and the broker's own)."""
        msg_type = message.get("type", "")

        if msg_type == PEER_MESSAGE_FRAME:
            return await self._handle_peer_message(message)

        if msg_type == "work_request":
            return await self._handle_work_request(message)

        handler = self._rpc_handlers.get(msg_type)
        if handler is not None:
            return await handler(message)

        return {"error": "unknown_message_type", "type": msg_type}

    async def _handle_peer_message(self, message: dict) -> dict:
        """A peer message RPC: ``{"envelope": <wire>}`` goes into the inbox."""
        try:
            envelope = PeerMessage.from_wire(message.get("envelope"))
        except PeerMessageError as exc:
            logger.error("mesh adapter: malformed peer message refused: %s", exc)
            return {"status": "refused", "error": str(exc)}
        return await self._accept(envelope, verified=self._verified_sender(message))

    def _verified_sender(self, carrier: Any) -> SenderIdentity | None:
        """Who the mesh verified sent this message, when this deployment verifies senders.

        The mesh layer sets the key after checking the certificate and signature, over
        whatever the sender wrote. With mesh signing off nothing checked, so the key is the
        sender's own and is ignored: the sender is then only claimed.
        """
        if not self._config.attestation.required:
            return None
        raw = carrier.get(VERIFIED_SENDER_KEY) if isinstance(carrier, Mapping) else None
        if raw is None:
            return None
        return SenderIdentity.from_wire(raw)

    async def _accept(
        self, envelope: PeerMessage, *, verified: SenderIdentity | None = None
    ) -> dict:
        extra = {} if verified is None else {"verified": verified}
        try:
            acceptance = await self._peer_inbox.accept_peer_message(
                envelope, channel=CHANNEL_MESH, **extra
            )
        except PeerMessageRefusedError as exc:
            return {"status": "refused", "message_id": envelope.message_id, "error": str(exc)}
        answer: dict[str, Any] = {"status": "accepted", "message_id": acceptance.message_id}
        if acceptance.duplicate:
            answer["duplicate"] = True
        return answer

    async def _handle_work_request(self, message: dict) -> dict:
        """A work_request becomes a message from the requesting peer; its answer comes by reply.

        The requester must name itself (``source_peer_id``): the answer is routed back to it
        over the mesh, and the harness is told who asked.
        """
        prompt = message.get("prompt", "")
        request_id = message.get("request_id") or str(uuid.uuid4())

        if not prompt:
            return {"status": "error", "request_id": request_id, "error": "empty prompt"}
        source_peer_id = message.get("source_peer_id")
        if not isinstance(source_peer_id, str) or not source_peer_id.strip():
            return {
                "status": "error",
                "request_id": request_id,
                "error": (
                    "work_request needs source_peer_id: the answer is sent back to that peer "
                    "over the mesh"
                ),
            }

        logger.info(
            "mesh adapter: work_request %s from %s (event_type=%s)",
            request_id,
            source_peer_id,
            message.get("event_type", ""),
        )
        try:
            envelope = self._mesh_message(
                message_id=str(request_id),
                source_peer_id=source_peer_id,
                prompt=prompt,
                event_type=str(message.get("event_type", "")),
            )
        except PeerMessageError as exc:
            return {"status": "error", "request_id": request_id, "error": str(exc)}
        answer = await self._accept(envelope, verified=self._verified_sender(message))
        return {**answer, "request_id": request_id}

    async def _handle_outcome_event(self, event: MeshEvent) -> None:
        """An outcome event on a consumed topic becomes a message from the emitting peer.

        A message this session refuses raises: the event is not silently dropped.
        """
        if event.type != MeshEventType.OUTCOME:
            return
        if event.source == self._peer_id:
            # A thread copy or an inbox copy of this peer's own publication.
            return

        payload = event.payload
        prompt = payload.get("prompt", "")
        if not prompt:
            logger.debug("mesh adapter: ignoring outcome event without prompt")
            return

        event_type = payload.get("event_type", "")
        logger.info(
            "mesh adapter: handling outcome event (event_type=%s, source=%s)",
            event_type,
            event.source,
        )
        envelope = self._mesh_message(
            message_id=event.event_id,
            source_peer_id=event.source,
            prompt=prompt,
            event_type=event_type,
            in_reply_to=str(payload.get(IN_REPLY_TO_KEY) or ""),
            thread=event.correlation_id,
        )
        answer = await self._accept(envelope, verified=self._verified_sender(event.attestation))
        if answer["status"] != "accepted":
            raise PeerMessageRefusedError(
                f"outcome event {event.event_id} from {event.source} was refused: {answer['error']}"
            )

    def _mesh_message(
        self,
        *,
        message_id: str,
        source_peer_id: str,
        prompt: str,
        event_type: str,
        in_reply_to: str = "",
        thread: str = "",
    ) -> PeerMessage:
        content = f"[Request for event type {event_type}]\n{prompt}" if event_type else prompt
        return PeerMessage(
            message_id=message_id,
            sender=PeerAddress(PeerKind.MESH_PEER, source_peer_id, name=source_peer_id),
            recipient=PeerAddress(PeerKind.SESSION, str(self._session_id)),
            sent_at=datetime.now(UTC),
            content=content,
            reply=ReplyRoute(ReplyKind.MESH, source_peer_id),
            in_reply_to=in_reply_to,
            thread=thread,
        )
