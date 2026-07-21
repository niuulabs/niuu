"""Transport-neutral room membership, presence, routing, and replay."""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import Any

from niuu.collaboration.models import Participant, RoomMessage, RoomState

logger = logging.getLogger(__name__)

EventSink = Callable[[dict[str, Any]], Awaitable[None]]
PresenceSink = Callable[["CollaborationEvent"], Awaitable[None]]
DeliverySink = Callable[[str, dict[str, Any]], Awaitable[bool]]
ConnectionCheck = Callable[[str], bool]
Clock = Callable[[], float]
RecordedAt = Callable[[], datetime]

HUMAN_ENVIRONMENT_ROLES: frozenset[str] = frozenset(
    {"observer", "teacher", "approver", "debugger", "owner"}
)

HUMAN_ROLE_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "observer": ("view", "reply"),
    "teacher": ("view", "reply", "teach", "correct"),
    "approver": ("view", "reply", "approve", "authorize_action"),
    "debugger": ("view", "reply", "debug", "teach", "correct"),
    "owner": (
        "view",
        "reply",
        "approve",
        "teach",
        "correct",
        "debug",
        "change_autonomy",
        "authorize_action",
    ),
}

_APPROVAL_CAPABILITIES = frozenset({"approve", "authorize_action"})
_REVIEWABLE_AUTHORITIES = frozenset({"court_required", "human_review_required"})


@dataclass(frozen=True)
class CollaborationEvent:
    """Canonical collaboration event before transport adaptation."""

    event_type: str
    payload: dict[str, Any]
    correlation_id: str = ""


def matches_subscription(event_type: str, subscriptions: Iterable[str]) -> bool:
    """Return whether an event type matches exact or prefix subscriptions."""
    for pattern in subscriptions:
        if pattern == event_type:
            return True
        if pattern.endswith(".*") and event_type.startswith(pattern[:-1]):
            return True
        if pattern.endswith("*") and event_type.startswith(pattern[:-1]):
            return True
    return False


def effective_human_capabilities(
    role: str,
    environment_action_authorities: Iterable[str] | None,
) -> tuple[str, ...]:
    """Intersect a human role with actions the environment can review."""
    grants = HUMAN_ROLE_CAPABILITIES[role]
    if environment_action_authorities is None:
        return grants
    if _REVIEWABLE_AUTHORITIES & set(environment_action_authorities):
        return grants
    return tuple(grant for grant in grants if grant not in _APPROVAL_CAPABILITIES)


async def _discard_event(_event: dict[str, Any]) -> None:
    return None


async def _discard_presence(_event: CollaborationEvent) -> None:
    return None


async def _unavailable_delivery(_peer_id: str, _payload: dict[str, Any]) -> bool:
    return False


class CollaborationRoom:
    """Reusable room state with all external effects supplied as callbacks.

    The object owns collaboration mechanics only. It knows nothing about
    FastAPI, WebSockets, Telegram, model runtimes, Ravn events, or Sleipnir.
    """

    def __init__(
        self,
        *,
        participant_colors: Iterable[str],
        environment_id: str,
        broadcast: EventSink | None = None,
        publish: PresenceSink | None = None,
        deliver: DeliverySink | None = None,
        is_connected: ConnectionCheck | None = None,
        clock: Clock | None = None,
        recorded_at: RecordedAt | None = None,
        presence_sweep_interval_s: float = 0.0,
    ) -> None:
        colors = tuple(participant_colors)
        if not colors:
            raise ValueError("participant_colors must not be empty")
        self._environment_id = environment_id
        self._broadcast = broadcast or _discard_event
        self._publish = publish or _discard_presence
        self._deliver = deliver or _unavailable_delivery
        self._is_connected = is_connected or (lambda _peer_id: False)
        self._clock = clock or time.time
        self._recorded_at = recorded_at or (lambda: datetime.now(UTC))
        self._presence_sweep_interval_s = presence_sweep_interval_s
        self._participants: dict[str, Participant] = {}
        self._reply_context: dict[str, dict[str, Any]] = {}
        self._room_event_log: dict[str, list[dict[str, Any]]] = {}
        self._room_context_snapshots: dict[str, dict[str, Any]] = {}
        self._room_sequence = 0
        self._color_cycle = itertools.cycle(colors)
        self._presence_sweep_task: asyncio.Task[None] | None = None

    def start_presence_sweep(self) -> None:
        """Start periodic eviction when configured."""
        if self._presence_sweep_interval_s <= 0 or self._presence_sweep_task is not None:
            return
        self._presence_sweep_task = asyncio.create_task(
            self._presence_sweep_loop(), name="collaboration_presence_sweep"
        )

    async def stop_presence_sweep(self) -> None:
        """Stop periodic presence eviction."""
        if self._presence_sweep_task is None:
            return
        self._presence_sweep_task.cancel()
        await asyncio.gather(self._presence_sweep_task, return_exceptions=True)
        self._presence_sweep_task = None

    async def _presence_sweep_loop(self) -> None:
        while True:
            await asyncio.sleep(self._presence_sweep_interval_s)
            try:
                await self.sweep_expired_participants()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("collaboration presence sweep failed")

    async def sweep_expired_participants(self) -> list[str]:
        """Remove heartbeat-expired participants without a live connection."""
        now = self._clock()
        expired = [
            peer_id
            for peer_id, participant in self._participants.items()
            if not self._is_connected(peer_id) and self._heartbeat_expired(participant, now)
        ]
        for peer_id in expired:
            participant = self._participants.pop(peer_id)
            self._reply_context.pop(peer_id, None)
            await self._broadcast(
                {
                    "type": "participant_left",
                    "participantId": peer_id,
                    "reason": "heartbeat_timeout",
                }
            )
            await self._publish_participant_left(participant, reason="heartbeat_timeout")
        return expired

    @staticmethod
    def _heartbeat_expired(participant: Participant, now: float) -> bool:
        if participant.last_heartbeat_at is None or participant.heartbeat_ttl_s <= 0:
            return False
        return now - participant.last_heartbeat_at > participant.heartbeat_ttl_s

    async def register_agent(
        self,
        peer_id: str,
        persona: str,
        *,
        display_name: str = "",
        subscribes_to: Iterable[str] | None = None,
        emits: Iterable[str] | None = None,
        tools: Iterable[str] | None = None,
        environment_id: str | None = None,
        participant_kind: str = "",
        capabilities: Iterable[str] | None = None,
        surfaces: Iterable[str] | None = None,
        wakefulness: str = "unknown",
        attention_state: str = "available",
        heartbeat_ttl_s: float = 90.0,
        authority_role: str = "",
        room_ids: Iterable[str] | None = None,
    ) -> Participant:
        """Register or reconnect an agent participant."""
        subscriptions = tuple(subscribes_to or ())
        emitted = tuple(emits or ())
        tool_names = tuple(tools or ())
        capability_names = tuple(capabilities or tool_names)
        surface_names = tuple(surfaces or ())
        memberships = tuple(room_ids or ())
        effective_environment = environment_id or self._environment_id
        existing = self._participants.get(peer_id)

        if existing is None:
            participant = Participant(
                peer_id=peer_id,
                persona=persona,
                color=next(self._color_cycle),
                participant_type="ravn",
                display_name=display_name,
                subscribes_to=subscriptions,
                emits=emitted,
                tools=tool_names,
                environment_id=effective_environment,
                participant_kind=participant_kind or "ravn",
                capabilities=capability_names,
                surfaces=surface_names,
                wakefulness=wakefulness,
                attention_state=attention_state,
                heartbeat_ttl_s=heartbeat_ttl_s,
                last_heartbeat_at=self._clock(),
                authority_role=authority_role,
                room_ids=memberships,
            )
        else:
            participant = replace(
                existing,
                persona=persona,
                display_name=display_name or existing.display_name,
                subscribes_to=subscriptions or existing.subscribes_to,
                emits=emitted or existing.emits,
                tools=tool_names or existing.tools,
                environment_id=effective_environment or existing.environment_id,
                participant_kind=participant_kind or existing.participant_kind,
                capabilities=capability_names or existing.capabilities,
                surfaces=surface_names or existing.surfaces,
                wakefulness=(wakefulness if wakefulness != "unknown" else existing.wakefulness),
                attention_state=attention_state or existing.attention_state,
                heartbeat_ttl_s=heartbeat_ttl_s or existing.heartbeat_ttl_s,
                last_heartbeat_at=self._clock(),
                authority_role=authority_role or existing.authority_role,
                room_ids=memberships or existing.room_ids,
            )
        return await self._store_joined(participant)

    async def register_peer(
        self,
        peer_id: str,
        persona: str,
        *,
        display_name: str = "",
        subscribes_to: Iterable[str] | None = None,
        emits: Iterable[str] | None = None,
        tools: Iterable[str] | None = None,
        participant_type: str = "ravn",
        environment_id: str | None = None,
        participant_kind: str = "",
        capabilities: Iterable[str] | None = None,
        surfaces: Iterable[str] | None = None,
        wakefulness: str = "watching",
        attention_state: str = "available",
        heartbeat_ttl_s: float = 90.0,
        authority_role: str = "",
        room_ids: Iterable[str] | None = None,
    ) -> Participant:
        """Register a discovered participant without assuming its transport."""
        existing = self._participants.get(peer_id)
        if existing is not None:
            return existing
        participant = Participant(
            peer_id=peer_id,
            persona=persona,
            color=next(self._color_cycle),
            participant_type=participant_type,
            display_name=display_name or persona,
            subscribes_to=tuple(subscribes_to or ()),
            emits=tuple(emits or ()),
            tools=tuple(tools or ()),
            environment_id=environment_id or self._environment_id,
            participant_kind=participant_kind or participant_type,
            capabilities=tuple(capabilities or tools or ()),
            surfaces=tuple(surfaces or ()),
            wakefulness=wakefulness,
            attention_state=attention_state,
            heartbeat_ttl_s=heartbeat_ttl_s,
            last_heartbeat_at=self._clock(),
            authority_role=authority_role,
            room_ids=tuple(room_ids or ()),
        )
        return await self._store_joined(participant)

    async def _store_joined(self, participant: Participant) -> Participant:
        self._participants[participant.peer_id] = participant
        await self._broadcast({"type": "participant_joined", "participant": asdict(participant)})
        await self._publish_participant_joined(participant)
        return participant

    async def join_human_environment(
        self,
        participant_id: str,
        *,
        display_name: str,
        environment_id: str,
        role: str = "observer",
        room_id: str = "",
        capabilities: Iterable[str] | None = None,
        surfaces: Iterable[str] | None = None,
        heartbeat_ttl_s: float = 300.0,
        environment_action_authorities: Iterable[str] | None = None,
    ) -> Participant:
        """Register or update a human participant in an environment."""
        participant_id = participant_id.strip()
        if not participant_id:
            raise ValueError("participant_id is required")
        if not environment_id.strip():
            raise ValueError("environment_id is required")
        normalized_role = role.strip() or "observer"
        if normalized_role not in HUMAN_ENVIRONMENT_ROLES:
            raise PermissionError(f"Unknown Environment role: {normalized_role}")

        grants = effective_human_capabilities(normalized_role, environment_action_authorities)
        requested = tuple(capabilities or grants)
        disallowed = sorted(set(requested) - set(grants))
        if disallowed:
            raise PermissionError(
                f"Role {normalized_role} cannot claim capabilities: {', '.join(disallowed)}"
            )

        membership = tuple([room_id] if room_id else [])
        existing = self._participants.get(participant_id)
        color = existing.color if existing is not None else next(self._color_cycle)
        existing_rooms = existing.room_ids if existing is not None else ()
        participant = Participant(
            peer_id=participant_id,
            persona=display_name or participant_id,
            color=color,
            participant_type="human",
            display_name=display_name or participant_id,
            subscribes_to=("room.message", "room.direct", "participant.*"),
            emits=("room.message", "room.direct", "feedback.recorded"),
            environment_id=environment_id,
            participant_kind="human",
            capabilities=requested,
            surfaces=tuple(surfaces or ("skuld.room",)),
            wakefulness="wakeful",
            attention_state="available",
            heartbeat_ttl_s=heartbeat_ttl_s,
            last_heartbeat_at=self._clock(),
            authority_role=normalized_role,
            room_ids=tuple(dict.fromkeys([*existing_rooms, *membership])),
        )
        return await self._store_joined(participant)

    async def leave_human_environment(self, participant_id: str, *, reason: str = "left") -> None:
        """Remove a human participant from an environment."""
        participant = self._participants.get(participant_id)
        if participant is None:
            raise LookupError(f"Unknown room participant: {participant_id}")
        if participant.participant_type != "human":
            raise PermissionError(f"Participant is not human: {participant_id}")
        self._participants.pop(participant_id)
        await self._broadcast({"type": "participant_left", "participantId": participant_id})
        await self._publish_participant_left(participant, reason=reason)

    async def unregister(self, peer_id: str, *, reason: str = "left") -> None:
        """Remove any participant."""
        participant = self._participants.pop(peer_id, None)
        self._reply_context.pop(peer_id, None)
        await self._broadcast({"type": "participant_left", "participantId": peer_id})
        if participant is not None:
            await self._publish_participant_left(participant, reason=reason)

    async def heartbeat(
        self,
        peer_id: str,
        *,
        status: str | None = None,
        wakefulness: str | None = None,
        attention_state: str | None = None,
    ) -> Participant | None:
        """Record a participant heartbeat."""
        participant = self._participants.get(peer_id)
        if participant is None:
            return None
        updated = replace(
            participant,
            status=status or participant.status,
            wakefulness=wakefulness or participant.wakefulness,
            attention_state=attention_state or participant.attention_state,
            last_heartbeat_at=self._clock(),
        )
        self._participants[peer_id] = updated
        await self._broadcast(
            {
                "type": "participant_heartbeat",
                "participantId": peer_id,
                "participant": asdict(updated),
            }
        )
        await self._publish_event("participant.heartbeat", updated, status=updated.status)
        return updated

    def require_participant_capability(self, participant_id: str, capability: str) -> None:
        """Require a declared participant capability."""
        participant = self._participants.get(participant_id)
        if participant is None:
            raise LookupError(f"Unknown room participant: {participant_id}")
        if capability not in participant.capabilities:
            raise PermissionError(f"Participant {participant_id} lacks capability: {capability}")

    async def update_activity(self, peer_id: str, status: str) -> Participant | None:
        """Update transient participant activity without interpreting its meaning."""
        participant = self._participants.get(peer_id)
        if participant is None:
            return None
        updated = replace(participant, status=status, last_heartbeat_at=self._clock())
        self._participants[peer_id] = updated
        return updated

    async def open_environment_huddle(
        self,
        *,
        room_id: str,
        purpose: str,
        environment_id: str | None = None,
        root_correlation_id: str = "",
        active_state: dict[str, Any] | None = None,
        signal_refs: Iterable[str] | None = None,
        judgment_refs: Iterable[str] | None = None,
        action_refs: Iterable[str] | None = None,
        transcript_targets: Iterable[str] | None = None,
        participants: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        """Open a replayable room scoped to an environment."""
        environment = environment_id or self._environment_id
        participant_ids = list(participants or ()) or [
            participant.peer_id
            for participant in self._participants.values()
            if (participant.environment_id or self._environment_id) == environment
        ]
        opened = {
            "type": "room_opened",
            "roomId": room_id,
            "environmentId": environment,
            "purpose": purpose,
            "participants": participant_ids,
        }
        await self._broadcast(opened)
        await self._publish(
            CollaborationEvent(
                "room.opened",
                {
                    "environment_id": environment,
                    "room_id": room_id,
                    "purpose": purpose,
                    "participants": participant_ids,
                },
                room_id,
            )
        )
        self._append_huddle_event(room_id, opened)
        await self.record_huddle_context_snapshot(
            room_id=room_id,
            environment_id=environment,
            root_correlation_id=root_correlation_id or room_id,
            active_state=active_state or {},
            signal_refs=list(signal_refs or ()),
            judgment_refs=list(judgment_refs or ()),
            action_refs=list(action_refs or ()),
            transcript_targets=list(transcript_targets or ("mimir",)),
            participant_ids=participant_ids,
        )
        return opened

    async def record_huddle_context_snapshot(
        self,
        *,
        room_id: str,
        environment_id: str,
        root_correlation_id: str,
        active_state: dict[str, Any],
        signal_refs: list[str],
        judgment_refs: list[str],
        action_refs: list[str],
        participant_ids: list[str],
        transcript_targets: list[str],
    ) -> dict[str, Any]:
        """Record context needed by late room joiners."""
        snapshot = {
            "type": "room_context_snapshot",
            "roomId": room_id,
            "environmentId": environment_id,
            "rootCorrelationId": root_correlation_id,
            "activeState": dict(active_state),
            "signalRefs": list(signal_refs),
            "judgmentRefs": list(judgment_refs),
            "actionRefs": list(action_refs),
            "participantIds": list(participant_ids),
            "transcriptTargets": list(transcript_targets),
        }
        self._room_context_snapshots[room_id] = snapshot
        self._append_huddle_event(room_id, snapshot)
        await self._publish(
            CollaborationEvent(
                "room.context_snapshot.recorded",
                {
                    "environment_id": environment_id,
                    "room_id": room_id,
                    "root_correlation_id": root_correlation_id,
                    "active_state": dict(active_state),
                    "signal_refs": list(signal_refs),
                    "judgment_refs": list(judgment_refs),
                    "action_refs": list(action_refs),
                    "participant_ids": list(participant_ids),
                    "transcript_targets": list(transcript_targets),
                },
                root_correlation_id,
            )
        )
        return snapshot

    async def record_huddle_message(
        self,
        *,
        room_id: str,
        environment_id: str,
        message_id: str,
        participant_id: str,
        role: str,
        content: str,
        visibility: str = "public",
        thread_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record a canonical room message."""
        message = RoomMessage(
            message_id=message_id,
            room_id=room_id,
            environment_id=environment_id,
            participant_id=participant_id,
            role=role,
            content=content,
            visibility=visibility,
            thread_id=thread_id,
            metadata=dict(metadata or {}),
        ).to_event()
        self._append_huddle_event(room_id, message)
        await self._publish(
            CollaborationEvent(
                "room.message.recorded",
                {
                    "environment_id": environment_id,
                    "room_id": room_id,
                    "message_id": message_id,
                    "participant_id": participant_id,
                    "role": role,
                    "content": content,
                    "visibility": visibility,
                    "thread_id": thread_id,
                    "metadata": dict(metadata or {}),
                },
                room_id,
            )
        )
        return message

    def replay_huddle(self, room_id: str, *, from_sequence: int = 0) -> list[dict[str, Any]]:
        """Return ordered room events after a sequence for late joiners."""
        return [
            dict(event)
            for event in self._room_event_log.get(room_id, [])
            if int(event.get("sequence", 0)) > from_sequence
        ]

    def _append_huddle_event(self, room_id: str, event: dict[str, Any]) -> dict[str, Any]:
        self._room_sequence += 1
        stored = {
            "sequence": self._room_sequence,
            "recordedAt": self._recorded_at().isoformat(),
            **event,
        }
        self._room_event_log.setdefault(room_id, []).append(stored)
        return stored

    def build_huddle_transcript(self, room_id: str) -> str:
        """Render the recorded public room messages as Markdown."""
        lines = [f"# Huddle Transcript: {room_id}", ""]
        snapshot = self._room_context_snapshots.get(room_id)
        if snapshot:
            lines.extend(
                [
                    f"- Environment: {snapshot.get('environmentId', '')}",
                    f"- Root correlation: {snapshot.get('rootCorrelationId', '')}",
                    "",
                ]
            )
        for event in self._room_event_log.get(room_id, []):
            if event.get("type") != "room_message":
                continue
            participant = event.get("participantId", "")
            content = str(event.get("content", "")).strip()
            if not content:
                continue
            lines.extend([f"## {participant}", content, ""])
        return "\n".join(lines).strip() + "\n"

    async def close_environment_huddle(
        self,
        *,
        room_id: str,
        reason: str = "closed",
        summary: str = "",
    ) -> dict[str, Any]:
        """Close a room and publish its replayable transcript."""
        snapshot = self._room_context_snapshots.get(room_id, {})
        environment_id = str(snapshot.get("environmentId") or self._environment_id)
        transcript_ref = f"huddles/{environment_id}/{room_id}.md"
        content = self.build_huddle_transcript(room_id)
        message_refs = [
            str(event["id"])
            for event in self._room_event_log.get(room_id, [])
            if event.get("type") == "room_message" and event.get("id")
        ]
        await self._publish(
            CollaborationEvent(
                "room.transcript.recorded",
                {
                    "environment_id": environment_id,
                    "room_id": room_id,
                    "transcript_ref": transcript_ref,
                    "message_refs": message_refs,
                    "summary": summary or f"{len(message_refs)} huddle messages recorded",
                    "transcript_content": content,
                },
                room_id,
            )
        )
        closed = {
            "type": "room_closed",
            "roomId": room_id,
            "environmentId": environment_id,
            "reason": reason,
            "transcriptRef": transcript_ref,
        }
        self._append_huddle_event(room_id, closed)
        await self._broadcast(closed)
        await self._publish(
            CollaborationEvent(
                "room.closed",
                {
                    "environment_id": environment_id,
                    "room_id": room_id,
                    "reason": reason,
                    "transcript_ref": transcript_ref,
                },
                room_id,
            )
        )
        return closed

    def set_reply_context(self, peer_id: str, context: dict[str, Any]) -> None:
        """Attach opaque continuation metadata to the next directed reply."""
        self._reply_context[peer_id] = dict(context)

    async def route_directed_message(
        self,
        target_peer_id: str,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Deliver a directed message through the configured transport adapter."""
        merged_metadata = dict(self._reply_context.get(target_peer_id, {}))
        if metadata:
            merged_metadata.update(metadata)
        payload: dict[str, Any] = {"type": "directed_message", "content": content}
        if merged_metadata:
            payload["metadata"] = merged_metadata
        delivered = await self._deliver(target_peer_id, payload)
        if delivered:
            self._reply_context.pop(target_peer_id, None)
        return delivered

    def pending_reply_peer_ids(self) -> tuple[str, ...]:
        """Return peers with continuation metadata awaiting a reply."""
        return tuple(self._reply_context)

    def get_room_state_event(self, *, environment_id: str | None = None) -> dict[str, Any]:
        """Return the established room-state event."""
        participants = {
            participant.peer_id: participant
            for participant in self._participants.values()
            if environment_id is None
            or (participant.environment_id or self._environment_id) == environment_id
        }
        return RoomState(participants).to_event()

    def environment_roster(self, *, environment_id: str | None = None) -> list[dict[str, Any]]:
        """Return participants, optionally filtered by environment."""
        return self.get_room_state_event(environment_id=environment_id)["participants"]

    @property
    def participants(self) -> dict[str, Participant]:
        return dict(self._participants)

    def has_participant(self, peer_id: str) -> bool:
        return peer_id in self._participants

    def participant(self, peer_id: str) -> Participant | None:
        return self._participants.get(peer_id)

    async def _publish_participant_joined(self, participant: Participant) -> None:
        await self._publish_event("participant.joined", participant)

    async def _publish_participant_left(self, participant: Participant, *, reason: str) -> None:
        await self._publish_event("participant.left", participant, reason=reason)

    async def _publish_event(self, event_type: str, participant: Participant, **extra: Any) -> None:
        payload = {
            "environment_id": participant.environment_id or self._environment_id,
            "participant_id": participant.peer_id,
            "participant_type": participant.participant_kind or participant.participant_type,
            "display_name": participant.display_name or participant.persona,
            "capabilities": list(participant.capabilities or participant.tools),
            "surfaces": list(participant.surfaces),
            "tools": list(participant.tools),
            "wakefulness": participant.wakefulness,
            "attention_state": participant.attention_state,
            "heartbeat_ttl_s": participant.heartbeat_ttl_s,
            **extra,
        }
        await self._publish(
            CollaborationEvent(
                event_type,
                payload,
                participant.environment_id or self._environment_id,
            )
        )
