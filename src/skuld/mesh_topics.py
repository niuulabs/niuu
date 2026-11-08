"""The mesh topics a session subscribes to and may publish, and the event it publishes.

Who listens is declared, never named by the publisher. A session's pinned persona states
the topics it ``consumes`` and ``produces``; the operator's mesh config may add to both.
This module turns that declaration into:

* the topics the session subscribes to (the persona's ``consumes.event_types`` on top of
  ``mesh.consumes_event_types``);
* the topics its ``mesh_publish`` tool may publish (the persona's ``produces`` on top of
  ``mesh.publish_event_types``). A topic in neither is refused, loudly, naming what is
  declared: pub/sub cannot tell a topic nobody listens to from a typo, so the declaration
  is the only guard against publishing into the void with a success-looking answer; and
* the :class:`~niuu.mesh.events.MeshEvent` a publication becomes, stamped by Skuld with
  this session's mesh identity and nothing taken from the content.

It decides nothing about what a publication means. Which consumers act on it, and whether
they answer, is theirs and the workflow's.
"""

from __future__ import annotations

import hashlib
import re
import socket
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from niuu.mesh.events import MeshEvent, MeshEventType
from niuu.mesh.identity import CHOOSER_FACTS
from personas.domain.persona_document import (
    PortablePersonaDefinition,
    consumed_event_types,
    produced_event_types,
)
from skuld.config import SkuldSettings

#: Namespace of derived event ids (a protocol constant, not a tunable).
_PUBLICATION_ID_NAMESPACE = uuid.UUID("5d1c8f7a-2b3e-4f6a-9c0d-7e4a1b2c3d4e")

#: The payload key a publisher's later answer uses to say which event it answers.
IN_REPLY_TO_KEY = "in_reply_to"
#: The payload key that carries an explicit thread tag the publisher chose.
CORRELATION_KEY = "correlation_id"


#: Topics the platform uses for threads and for answers addressed to one peer. Nobody
#: publishes or subscribes to them by name.
THREAD_TOPIC_PREFIX = "thread"
INBOX_TOPIC_PREFIX = "inbox"
_RESERVED_PREFIXES = (f"{THREAD_TOPIC_PREFIX}.", f"{INBOX_TOPIC_PREFIX}.")
_SEGMENT = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")


class InvalidTopicError(ValueError):
    """A topic name the mesh cannot carry, or one reserved for the platform."""


def thread_topic(correlation_id: str) -> str:
    """The topic that carries one thread: every publication that belongs to it.

    The correlation id is hashed so ids that differ only in punctuation or case never share
    a topic and the result is always a valid topic segment.
    """
    digest = hashlib.sha256(correlation_id.encode()).hexdigest()[:32]
    return f"{THREAD_TOPIC_PREFIX}.t{digest}"


def inbox_topic(peer_id: str) -> str:
    """The topic a peer always listens on: answers published to it by name of its own event."""
    digest = hashlib.sha256(peer_id.encode()).hexdigest()[:32]
    return f"{INBOX_TOPIC_PREFIX}.p{digest}"


def validate_subscribable_topic(topic: str) -> str:
    """The topic, or :class:`InvalidTopicError` naming what is wrong and the remedy."""
    cleaned = topic.strip()
    if not cleaned or any(not _SEGMENT.fullmatch(segment) for segment in cleaned.split(".")):
        raise InvalidTopicError(
            f"{topic!r} is not a topic: it is dot-separated segments that each start with a "
            "letter and hold only letters, digits, '_' and '-' (for example review.requested)"
        )
    if cleaned.startswith(_RESERVED_PREFIXES):
        raise InvalidTopicError(
            f"{cleaned!r} is a platform topic. Follow a thread with mesh_subscribe "
            "correlation_id; a peer's inbox topic carries answers addressed to that peer and "
            "is not subscribable"
        )
    return cleaned


class UndeclaredTopicError(ValueError):
    """The session did not declare this topic as one it publishes; the message names the fix."""


@dataclass(frozen=True)
class MeshTopics:
    """What a session declared about its mesh traffic."""

    consumes: tuple[str, ...]
    produces: tuple[str, ...]
    persona_id: str = ""
    #: What this session announces about itself, from its persona and its mesh config.
    role: str = ""
    description: str = ""

    def check_publishable(self, topic: str) -> None:
        """Raise :class:`UndeclaredTopicError` unless ``topic`` is declared as published."""
        if topic in self.produces:
            return
        declared = ", ".join(repr(item) for item in self.produces) or "none"
        source = f"persona {self.persona_id!r}" if self.persona_id else "this session"
        raise UndeclaredTopicError(
            f"{topic!r} is not a topic {source} declares it publishes (declared: {declared}). "
            "Nothing was published. Publish one of the declared topics, add the topic to the "
            "persona's `produces` (event_type or event_type_map), or list it in "
            "mesh.publish_event_types for a session without that persona."
        )


def _merged(*groups: Iterable[str]) -> tuple[str, ...]:
    """The union of the groups, each topic once, in the order first seen."""
    seen: dict[str, None] = {}
    for group in groups:
        for item in group:
            topic = item.strip()
            if topic:
                seen.setdefault(topic)
    return tuple(seen)


def mesh_topics(settings: SkuldSettings) -> MeshTopics:
    """The session's declared topics, from its pinned persona and its mesh config.

    A persona the session carries that is not a valid portable document raises: the session
    could not have started its harness with it either.
    """
    mesh = settings.mesh
    raw = settings.session.persona
    if raw is None:
        return MeshTopics(
            consumes=_merged(mesh.consumes_event_types),
            produces=_merged(mesh.publish_event_types),
            role=mesh.role.strip(),
            description=mesh.description.strip(),
        )
    persona = PortablePersonaDefinition.from_dict(raw)
    return MeshTopics(
        consumes=_merged(
            mesh.consumes_event_types, sorted(consumed_event_types(persona.definition))
        ),
        produces=_merged(
            mesh.publish_event_types, sorted(produced_event_types(persona.definition))
        ),
        persona_id=persona.id,
        role=mesh.role.strip() or persona.id,
        description=str(persona.definition.get("description") or "").strip()
        or mesh.description.strip(),
    )


def publication_event_id(peer_id: str, topic: str, request_id: str) -> str:
    """The same id for the same ``(peer, topic, request_id)``, so a retry deduplicates."""
    return str(uuid.uuid5(_PUBLICATION_ID_NAMESPACE, f"{peer_id}\n{topic}\n{request_id}"))


def build_publication(
    *,
    peer_id: str,
    session_id: str,
    session_name: str,
    persona_id: str,
    topic: str,
    content: str,
    request_id: str,
    urgency: float,
    correlation_id: str = "",
    in_reply_to: str = "",
    trace_context: dict[str, str] | None = None,
    now: datetime | None = None,
) -> MeshEvent:
    """The mesh event for one publication, stamped with this session's mesh identity.

    ``source`` is this session's mesh peer id, which is the address a consumer's ``reply``
    goes to. The ``publisher`` block names the session for the audit trail. The event's
    ``correlation_id`` is the thread it belongs to: the one the publisher named, else the
    event it answers (``in_reply_to``), else its own id.
    """
    event_id = publication_event_id(peer_id, topic, request_id)
    payload: dict[str, Any] = {
        "event_type": topic,
        "prompt": content,
        "session_id": session_id,
        "persona": persona_id,
        "publisher": {
            "session_id": session_id,
            "name": session_name,
            "node": socket.gethostname(),
            "peer_id": peer_id,
        },
        "trigger_source": "mesh_publish",
    }
    # The thread an event belongs to: the one the publisher named, else the one it answers,
    # else its own (a new conversation).
    thread = correlation_id or in_reply_to or event_id
    if in_reply_to:
        payload[IN_REPLY_TO_KEY] = in_reply_to
    if thread != event_id:
        payload[CORRELATION_KEY] = thread
    return MeshEvent(
        event_id=event_id,
        type=MeshEventType.OUTCOME,
        source=peer_id,
        payload=payload,
        timestamp=now or datetime.now(UTC),
        urgency=urgency,
        correlation_id=thread,
        session_id=session_id,
        root_correlation_id=session_id,
        trace_context=dict(trace_context or {}),
    )


def publication_topics(event: MeshEvent, topic: str, reply_peer_id: str = "") -> list[str]:
    """Every topic one publication goes to, the declared topic first.

    * its declared topic, for whoever consumes it;
    * its thread's topic when it belongs to a thread that is not just itself (an answer, or
      an event that names a thread), so everyone following the thread sees it;
    * the inbox topic of the peer it answers, so that peer receives the answer without having
      subscribed to anything in advance.
    """
    topics = [topic]
    if event.correlation_id != event.event_id:
        topics.append(thread_topic(event.correlation_id))
    if reply_peer_id:
        topics.append(inbox_topic(reply_peer_id))
    return list(dict.fromkeys(topics))


def describe_peer(peer: Any) -> dict[str, Any]:
    """The facts a discovered peer announced, as a chooser reads them.

    Nothing here is ranked or scored. A fact the peer's announcement did not carry (an older
    Skuld, a Kubernetes or static discovery that cannot say it) is ``null`` and named in
    ``not_announced``: that is not the same as the peer announcing an empty role or no
    topics, and it is never rendered as an empty string.
    """
    missing = set(getattr(peer, "not_announced", ()))
    facts: dict[str, Any] = {
        "peer_id": peer.peer_id,
        "name": getattr(peer, "display_name", ""),
        "kind": getattr(peer, "participant_type", ""),
        "persona": peer.persona,
        "role": getattr(peer, "role", ""),
        "description": getattr(peer, "description", ""),
        "capabilities": list(peer.capabilities),
        "consumes": list(getattr(peer, "consumes_event_types", [])),
        "produces": list(getattr(peer, "emits_event_types", [])),
        "activity_state": getattr(peer, "activity_state", ""),
        "status": peer.status,
        "task_count": peer.task_count,
        "trust_level": peer.trust_level,
        "last_heartbeat": peer.last_heartbeat.isoformat(),
    }
    for fact, field in _FACT_FIELDS.items():
        if fact in missing:
            facts[field] = None
    facts["not_announced"] = [fact for fact in CHOOSER_FACTS if fact in missing]
    return facts


#: The tool-output field each chooser fact fills when the peer announced it.
_FACT_FIELDS = {
    "role": "role",
    "description": "description",
    "activity_state": "activity_state",
    "busy": "status",
    "consumes_event_types": "consumes",
    "emits_event_types": "produces",
}


def producers_of(peers: Iterable[Any], topic: str) -> tuple[list[Any], int]:
    """The peers that announce they produce ``topic``, in peer id order, and how many peers
    did not announce what they produce at all.

    That is a fact lookup in the peer table and nothing more: whether a peer is the right one
    to ask is the asker's judgment. A peer that did not announce its topics is not matched
    (it is unknown, not "no"), and is counted so the asker knows the match may be incomplete.
    """
    matched: list[Any] = []
    unknown = 0
    for peer in sorted(peers, key=lambda candidate: candidate.peer_id):
        if "emits_event_types" in getattr(peer, "not_announced", ()):
            unknown += 1
            continue
        if topic in peer.emits_event_types:
            matched.append(peer)
    return matched, unknown


def peer_table(
    peers: Iterable[Any],
    *,
    role: str = "",
    capability: str = "",
    consumes: str = "",
    produces: str = "",
) -> list[dict[str, Any]]:
    """The peers that match every given filter exactly, in peer id order.

    A filter is an exact match on one announced fact (the role is the role, the capability
    is among the capabilities). The order is the peer id's, not a preference.
    """
    table = []
    for peer in sorted(peers, key=lambda candidate: candidate.peer_id):
        facts = describe_peer(peer)
        if role and facts["role"] != role:
            continue
        if capability and capability not in facts["capabilities"]:
            continue
        if consumes and consumes not in facts["consumes"]:
            continue
        if produces and produces not in facts["produces"]:
            continue
        table.append(facts)
    return table
