"""AttestedMesh — a MeshPort decorator that proves who sent every mesh message.

Wraps any mesh transport (Sleipnir over nng/NATS/RabbitMQ, webhook, composite,
in-process) and adds, in one place:

* **Outgoing.** Every published event and every RPC request is signed with this peer's
  key, and every RPC reply it gives is signed and bound to the request it answers.
* **Incoming.** Every event, request and reply is verified before any handler sees it
  (:mod:`niuu.mesh.attestation`): certificate, signature, freshness, that what the message
  says about its sender (session id, peer id, reply route, ``source``) is what the
  certificate says, that the sender shares a flock/realm/tenant with this peer, and that
  its launch was not revoked.

It fails closed. A request that cannot be verified gets a signed ``refused`` reply that
names why; an event that cannot be verified is not handled, is logged at error and counted
(``niuu.mesh.attestation.refusals``, by ``reason``, ``direction`` and the sender it
claimed); a reply that cannot be verified raises to the caller. Nothing unverified
reaches a handler.

The signature, not the transport, is what proves the sender. NATS credentials or subject
permissions, where a deployment adds them, are an extra layer, not a substitute.
"""

from __future__ import annotations

import logging
import threading
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from typing import Any

from niuu.mesh.attestation import (
    COUNTER_REFUSALS,
    PURPOSE_ANNOUNCE,
    PURPOSE_EVENT,
    PURPOSE_RPC,
    PURPOSE_RPC_REPLY,
    VERIFIED_SENDER_KEY,
    AttestationReason,
    AttestationRefusedError,
    MeshAttestationVerifier,
    MeshSigner,
    VerifiedSender,
    digest,
    sanitize_topic,
    topic_matches,
)
from niuu.mesh.events import MeshEvent
from niuu.mesh.projection import project_mesh_event
from niuu.observability import get_observability

logger = logging.getLogger(__name__)

#: The mesh topic on which Forge announces that a session stopped (signed by Forge).
REVOCATION_TOPIC = "mesh.revocation"

DIRECTION_REQUEST = "request"
DIRECTION_REPLY = "reply"
DIRECTION_EVENT = "event"
DIRECTION_REVOCATION = "revocation"
DIRECTION_ANNOUNCEMENT = "announcement"

#: Called for every refusal: ``(error, direction, topic_or_type)``.
RefusalListener = Callable[[AttestationRefusedError, str, str], None]

_lock = threading.Lock()
_refusals: Counter[str] = Counter()


def refusal_counts() -> dict[str, int]:
    """Refusals since process start, keyed ``<direction>.<reason>``, for health snapshots."""
    with _lock:
        return dict(_refusals)


def reset_refusal_counts() -> None:
    """Forget the process counts (tests only)."""
    with _lock:
        _refusals.clear()


def event_subject(event: MeshEvent, topic: str) -> dict[str, Any]:
    """What an event signature covers: everything a receiver acts on, and the topic."""
    return {
        "topic": sanitize_topic(topic),
        "event_id": event.event_id,
        "type": str(event.type.value) if hasattr(event.type, "value") else str(event.type),
        "source": event.source,
        "payload": event.payload,
        "urgency": event.urgency,
        "correlation_id": event.correlation_id,
        "session_id": event.session_id,
        "task_id": event.task_id,
    }


def _peer_of_source(source: str) -> str:
    """The peer id in an event ``source``: ``skuld:<peer>`` and a bare ``<peer>`` both occur."""
    return source.split(":", 1)[1] if ":" in source else source


def bind_event(event: MeshEvent, sender: VerifiedSender) -> None:
    """Refuse an event that names a sender other than the one that signed it."""
    if _peer_of_source(event.source) != sender.peer_id:
        raise AttestationRefusedError(
            AttestationReason.BINDING_MISMATCH,
            f"event source {event.source!r} is not the signing peer {sender.peer_id!r}",
            claimed=event.source,
        )
    if sender.session_id and event.session_id and event.session_id != sender.session_id:
        raise AttestationRefusedError(
            AttestationReason.BINDING_MISMATCH,
            f"event names session {event.session_id!r} but {sender.peer_id!r} signs for "
            f"session {sender.session_id!r}",
            claimed=event.source,
        )
    publisher = event.payload.get("publisher") if isinstance(event.payload, Mapping) else None
    if not isinstance(publisher, Mapping):
        return
    for key, certified in (("session_id", sender.session_id), ("peer_id", sender.peer_id)):
        named = publisher.get(key)
        if named and certified and str(named) != certified:
            raise AttestationRefusedError(
                AttestationReason.BINDING_MISMATCH,
                f"publisher.{key} {named!r} is not the certified {certified!r}",
                claimed=event.source,
            )


def _mismatch(message: str, claimed: str) -> AttestationRefusedError:
    return AttestationRefusedError(AttestationReason.BINDING_MISMATCH, message, claimed=claimed)


def bind_rpc(body: Mapping[str, Any], sender: VerifiedSender) -> None:
    """Refuse a request whose claims about its sender are not the certified ones."""
    source = body.get("source_peer_id")
    if source is not None and source != sender.peer_id:
        raise _mismatch(
            f"source_peer_id {source!r} is not the signing peer {sender.peer_id!r}",
            str(source),
        )
    envelope = body.get("envelope")
    if envelope is None:
        return
    if not isinstance(envelope, Mapping):
        raise _mismatch("envelope is not an object", sender.peer_id)
    relayed_by = envelope.get("relayed_by")
    if isinstance(relayed_by, Mapping):
        vouching = str(relayed_by.get("id") or "")
        if vouching not in {sender.peer_id, sender.session_id}:
            raise _mismatch(
                f"relayed_by {vouching!r} is not the signing peer {sender.peer_id!r}",
                vouching,
            )
    else:
        _bind_envelope_sender(envelope.get("sender"), sender)
    _bind_envelope_reply(envelope.get("reply"), sender, relayed=isinstance(relayed_by, Mapping))


def _bind_envelope_sender(raw: Any, sender: VerifiedSender) -> None:
    if not isinstance(raw, Mapping):
        raise _mismatch("envelope has no sender", sender.peer_id)
    kind = raw.get("kind")
    named = str(raw.get("id") or "")
    if kind == "session" and sender.session_id and named == sender.session_id:
        return
    if kind == "mesh_peer" and named == sender.peer_id:
        return
    raise _mismatch(
        f"envelope sender {kind} {named!r} is not what {sender.peer_id!r} is certified as "
        f"(session {sender.session_id or '-'}, peer {sender.peer_id})",
        named,
    )


def _bind_envelope_reply(raw: Any, sender: VerifiedSender, *, relayed: bool) -> None:
    if raw is None:
        return
    if not isinstance(raw, Mapping):
        raise _mismatch("envelope reply is not an object", sender.peer_id)
    kind = raw.get("kind")
    target = str(raw.get("target") or "")
    own = {"mesh": sender.peer_id, "session": sender.session_id}
    if kind in own and target == own[kind]:
        return
    if kind == "room" and relayed:
        return
    raise _mismatch(
        f"reply route {kind} {target!r} does not lead back to {sender.peer_id!r}", target
    )


class MeshAttestor:
    """Signs and verifies what rides the bus beside the mesh: discovery announcements.

    A peer table decides whom agents ask, so a forged announcement steers questions and a
    forged ``leave`` silences a real peer. An announcement is signed like any mesh message,
    and one is accepted only if its certificate checks out, the peer it announces is the
    one that signed it, and it is not older than the last one accepted from that peer (a
    captured ``leave`` cannot be replayed after a newer heartbeat).
    """

    def __init__(
        self,
        signer: MeshSigner,
        verifier: MeshAttestationVerifier,
        refuse: Callable[[AttestationRefusedError, str, str], None],
    ) -> None:
        self._signer = signer
        self._verifier = verifier
        self._refuse = refuse
        self._last_signed: dict[str, int] = {}

    def sign(self, announcement: Mapping[str, Any]) -> dict[str, Any]:
        """The announcement with its attestation attached."""
        return {**announcement, "attestation": self._signer.attest(PURPOSE_ANNOUNCE, announcement)}

    def accept(self, announcement: Mapping[str, Any]) -> bool:
        """Whether *announcement* is genuine; a refusal is logged, counted and returns False."""
        body = {key: value for key, value in announcement.items() if key != "attestation"}
        identity = body.get("identity")
        claimed = str(identity.get("peer_id") or "") if isinstance(identity, Mapping) else ""
        try:
            sender = self._verifier.verify(
                announcement.get("attestation"), PURPOSE_ANNOUNCE, body, claimed=claimed
            )
            if sender.peer_id != claimed:
                raise _mismatch(
                    f"announcement for {claimed!r} is signed by {sender.peer_id!r}", claimed
                )
            if sender.signed_at_ms < self._last_signed.get(sender.peer_id, 0):
                raise AttestationRefusedError(
                    AttestationReason.STALE,
                    f"announcement from {sender.peer_id!r} is older than one already accepted",
                    claimed=claimed,
                )
        except AttestationRefusedError as error:
            self._refuse(error, DIRECTION_ANNOUNCEMENT, claimed or "announce")
            return False
        self._last_signed[sender.peer_id] = sender.signed_at_ms
        return True


class AttestedMesh:
    """A MeshPort that signs what it sends and verifies what it receives."""

    def __init__(
        self,
        inner: Any,
        signer: MeshSigner,
        verifier: MeshAttestationVerifier,
        *,
        own_peer_id: str,
        event_max_age_s: float = 3600.0,
        on_refusal: RefusalListener | None = None,
    ) -> None:
        self._inner = inner
        self._signer = signer
        self._verifier = verifier
        self._own_peer_id = own_peer_id
        self._event_max_age_ms = int(event_max_age_s * 1000)
        self._on_refusal = on_refusal
        self._revocation_subscribed = False
        self._attestor = MeshAttestor(signer, verifier, self._refused)

    def __getattr__(self, name: str) -> Any:
        # Everything that is not part of the signed surface (``subscriber``, ...) is the
        # wrapped mesh's own.
        return getattr(self._inner, name)

    @property
    def inner(self) -> Any:
        return self._inner

    @property
    def attestor(self) -> MeshAttestor:
        """Signs and verifies discovery announcements with this peer's identity."""
        return self._attestor

    @property
    def signer(self) -> MeshSigner:
        return self._signer

    @property
    def verifier(self) -> MeshAttestationVerifier:
        return self._verifier

    # ------------------------------------------------------------------
    # Counting
    # ------------------------------------------------------------------

    def _refused(self, error: AttestationRefusedError, direction: str, what: str) -> None:
        with _lock:
            _refusals[f"{direction}.{error.reason.value}"] += 1
        logger.error(
            "mesh attestation refused a %s (%s) from %r: %s",
            direction,
            what,
            error.claimed or "?",
            error,
        )
        get_observability().count(
            COUNTER_REFUSALS,
            attributes={
                "direction": direction,
                "reason": error.reason.value,
                "sender": error.claimed or "unknown",
            },
            description="Mesh messages refused because their sender could not be verified.",
        )
        if self._on_refusal is not None:
            self._on_refusal(error, direction, what)

    # ------------------------------------------------------------------
    # MeshPort
    # ------------------------------------------------------------------

    async def publish(self, event: MeshEvent, topic: str) -> None:
        subject = event_subject(event, topic)
        attestation = self._signer.attest(PURPOSE_EVENT, subject, extra={"topic": subject["topic"]})
        await self._inner.publish(replace(event, attestation=attestation), topic)

    async def subscribe(self, topic: str, handler: Callable[[MeshEvent], Awaitable[None]]) -> None:
        async def verified(event: MeshEvent) -> None:
            try:
                sender = self._verify_event(event, topic)
            except AttestationRefusedError as error:
                # Acknowledged, not redelivered: a refusal is deterministic, so a retry
                # would fail identically. It is counted and logged at error.
                self._refused(error, DIRECTION_EVENT, topic)
                return
            # The proof has done its work; the handler is told who it proved, in place of it.
            await handler(replace(event, attestation={VERIFIED_SENDER_KEY: sender.summary()}))

        await self._inner.subscribe(topic, verified)

    async def unsubscribe(self, topic: str) -> None:
        await self._inner.unsubscribe(topic)

    def verify_event_for_topic(self, event: MeshEvent, topic: str) -> VerifiedSender:
        """Verify an event that was published on exactly *topic* (for direct consumers)."""
        signed = event.attestation.get("topic") if isinstance(event.attestation, Mapping) else None
        if isinstance(signed, str) and sanitize_topic(signed) != sanitize_topic(topic):
            raise _mismatch(
                f"event was signed for topic {signed!r} but arrived on {topic!r}", event.source
            )
        return self._verify_event(event, topic)

    def verified_subscriber(self, subscriber: Any, *, event_prefix: str) -> Any:
        """Wrap a raw Sleipnir subscriber so its consumer only sees verified mesh events."""
        return VerifiedSleipnirSubscriber(self, subscriber, event_prefix=event_prefix)

    def _verify_event(self, event: MeshEvent, subscribed: str) -> VerifiedSender:
        attestation = event.attestation
        signed_topic = attestation.get("topic") if isinstance(attestation, Mapping) else None
        if attestation and not isinstance(signed_topic, str):
            raise AttestationRefusedError(
                AttestationReason.MALFORMED,
                "event attestation names no topic",
                claimed=event.source,
            )
        if attestation and not topic_matches(subscribed, signed_topic):
            raise _mismatch(
                f"event was signed for topic {signed_topic!r}, not {subscribed!r}", event.source
            )
        subject = event_subject(event, signed_topic or subscribed)
        sender = self._verifier.verify(
            attestation,
            PURPOSE_EVENT,
            subject,
            claimed=event.source,
            max_age_ms=self._event_max_age_ms,
        )
        bind_event(event, sender)
        return sender

    async def send(
        self,
        target_peer_id: str,
        message: dict,
        *,
        timeout_s: float | None = None,
    ) -> dict:
        subject = {"to": target_peer_id, "message": message}
        attestation = self._signer.attest(PURPOSE_RPC, subject)
        reply = await self._inner.send(
            target_peer_id, {**message, "attestation": attestation}, timeout_s=timeout_s
        )
        return self._verify_reply(reply, target_peer_id, digest(subject))

    def _verify_reply(self, reply: Any, target_peer_id: str, request_digest: str) -> dict:
        try:
            if not isinstance(reply, Mapping):
                raise AttestationRefusedError(
                    AttestationReason.MALFORMED,
                    f"reply is not an object: {reply!r}",
                    claimed=target_peer_id,
                )
            body = {key: value for key, value in reply.items() if key != "attestation"}
            sender = self._verifier.verify(
                reply.get("attestation"),
                PURPOSE_RPC_REPLY,
                {"request": request_digest, "response": body},
                claimed=target_peer_id,
            )
            if sender.peer_id != target_peer_id:
                raise _mismatch(
                    f"the reply to a message for {target_peer_id!r} is signed by "
                    f"{sender.peer_id!r}",
                    sender.peer_id,
                )
        except AttestationRefusedError as error:
            self._refused(error, DIRECTION_REPLY, target_peer_id)
            if error.reason is AttestationReason.MISSING and isinstance(reply, Mapping):
                raise AttestationRefusedError(
                    error.reason,
                    f"{error.detail} (unsigned reply from {target_peer_id!r}: {dict(reply)!r})",
                    claimed=error.claimed,
                ) from error
            raise
        return body

    def set_rpc_handler(self, handler: Callable[[dict], Awaitable[dict]]) -> None:
        async def verified(request: dict) -> dict:
            body = {key: value for key, value in request.items() if key != "attestation"}
            subject = {"to": self._own_peer_id, "message": body}
            try:
                sender = self._verifier.verify(
                    request.get("attestation"),
                    PURPOSE_RPC,
                    subject,
                    claimed=str(body.get("source_peer_id") or ""),
                )
                bind_rpc(body, sender)
            except AttestationRefusedError as error:
                self._refused(error, DIRECTION_REQUEST, str(body.get("type") or ""))
                response: dict[str, Any] = {
                    "status": "refused",
                    "error": str(error),
                    "reason": error.reason.value,
                }
                return self._sign_reply(subject, response)
            try:
                response = await handler({**body, VERIFIED_SENDER_KEY: sender.summary()})
            except Exception as exc:
                logger.warning("mesh rpc handler failed: %s", exc)
                response = {"error": str(exc)}
            return self._sign_reply(subject, response)

        self._inner.set_rpc_handler(verified)

    def _sign_reply(self, request_subject: Mapping[str, Any], response: dict) -> dict:
        attestation = self._signer.attest(
            PURPOSE_RPC_REPLY, {"request": digest(request_subject), "response": response}
        )
        return {**response, "attestation": attestation}

    async def start(self) -> None:
        await self._inner.start()
        await self._inner.subscribe(REVOCATION_TOPIC, self._on_revocation)
        self._revocation_subscribed = True

    async def stop(self) -> None:
        try:
            if self._revocation_subscribed:
                self._revocation_subscribed = False
                await self._inner.unsubscribe(REVOCATION_TOPIC)
            await self._inner.stop()
        finally:
            await self._signer.stop()

    async def _on_revocation(self, event: MeshEvent) -> None:
        """Forge says a session stopped: drop that launch now. Only Forge's word counts."""
        try:
            self._verifier.verify(
                event.attestation,
                PURPOSE_EVENT,
                event_subject(event, str(event.attestation.get("topic") or REVOCATION_TOPIC))
                if event.attestation
                else {},
                claimed=event.source,
                authority=True,
                max_age_ms=self._event_max_age_ms,
            )
            session_id = str(event.payload["session_id"])
            launch_id = str(event.payload["launch_id"])
        except AttestationRefusedError as error:
            self._refused(error, DIRECTION_REVOCATION, REVOCATION_TOPIC)
            return
        except KeyError as exc:
            self._refused(
                AttestationRefusedError(
                    AttestationReason.MALFORMED,
                    f"revocation lacks {exc.args[0]}",
                    claimed=event.source,
                ),
                DIRECTION_REVOCATION,
                REVOCATION_TOPIC,
            )
            return
        self._verifier.revoke(session_id, launch_id)


class VerifiedSleipnirSubscriber:
    """A Sleipnir subscriber that hands on only verified mesh events, rebuilt from the proof.

    For consumers that read Sleipnir events directly instead of through the mesh port
    (the room's collaboration bridge). It drops anything that is not a signed mesh event,
    and for those it sets what the consumer trusts from the verified certificate and the
    signed event, never from the outer envelope, which the sender chose: the ``source``
    is the signing peer, and the collaboration projection is recomputed here from the
    signed event rather than taken from the payload.
    """

    def __init__(self, mesh: AttestedMesh, subscriber: Any, *, event_prefix: str) -> None:
        self._mesh = mesh
        self._subscriber = subscriber
        self._event_prefix = event_prefix

    def __getattr__(self, name: str) -> Any:
        return getattr(self._subscriber, name)

    async def subscribe(
        self, patterns: list[str], handler: Callable[[Any], Awaitable[None]]
    ) -> Any:
        async def verified(sleipnir_event: Any) -> None:
            payload = sleipnir_event.payload
            if "ravn_type" not in payload:
                # Not a mesh event (an RPC leg, a discovery announcement): not for this consumer.
                return
            from niuu.adapters.mesh.sleipnir_mesh import sleipnir_to_mesh_event  # noqa: PLC0415

            mesh_event = sleipnir_to_mesh_event(sleipnir_event)
            topic = str(sleipnir_event.event_type)[len(self._event_prefix) + 1 :]
            try:
                sender = self._mesh.verify_event_for_topic(mesh_event, topic)
            except AttestationRefusedError as error:
                self._mesh._refused(error, DIRECTION_EVENT, topic)
                return
            trusted = replace(
                sleipnir_event,
                source=f"ravn:{sender.peer_id}",
                payload={
                    **payload,
                    "collaboration_events": project_mesh_event(mesh_event),
                },
            )
            await handler(trusted)

        return await self._subscriber.subscribe(patterns, verified)


__all__ = [
    "REVOCATION_TOPIC",
    "AttestedMesh",
    "MeshAttestor",
    "VerifiedSleipnirSubscriber",
    "bind_event",
    "bind_rpc",
    "event_subject",
    "refusal_counts",
    "reset_refusal_counts",
]
