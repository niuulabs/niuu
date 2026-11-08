"""The inbox through which other agents' messages reach this session's harness.

Every message from another agent (a Forge session, a collaboration room, a mesh peer) arrives
as a :class:`~niuu.domain.peer_message.PeerMessage`. This module holds it durably, hands it to
the harness at a turn boundary, and answers the ``reply`` tool's lookup of it.

* **Accepting** a message means it is in the session's event log and Forge confirmed it. That is
  all "delivered" means to the sender: not that the harness has read it.
* **Delivery** is at the turn boundary, the same gate a workflow step's instructions wait for.
  Messages that wait go in as one turn, oldest first. A pending workflow step goes in before
  them. ``urgent`` skips the wait and takes the path a person's message takes.
* **A restart** rebuilds the inbox from the log. A message already handed to the harness is
  never handed over again (``injected``), and a message delivered twice is handled once.

Skuld decides nothing about a message. It does not read the content, does not choose when a
conversation has had enough, and has no limit on how many messages wait: a loop between agents
is made visible (per-pair counts in the session status, the ``skuld.peer.messages`` counter),
never capped.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from niuu.cap_telemetry import record_degraded
from niuu.domain.peer_message import (
    PEER_MESSAGE_FRAME,
    PeerKind,
    PeerMessage,
    PeerMessageError,
    render_for_harness,
)
from niuu.observability import get_observability

logger = logging.getLogger("skuld.broker")

#: Where the turn that carries peer messages came from, on the turn and its trace span.
PEER_TURN_SOURCE = "peer_message"

#: Namespace of the deterministic id of the user turn that carries a batch of peer messages.
_PEER_TURN_NAMESPACE = "peer-messages"

#: The OTel counter every message increments, once per outcome.
PEER_MESSAGES_COUNTER = "skuld.peer.messages"

OUTCOME_ACCEPTED = "accepted"
OUTCOME_DUPLICATE = "duplicate"
OUTCOME_REFUSED = "refused"
OUTCOME_DELIVERED = "delivered"
_OUTCOMES = (OUTCOME_ACCEPTED, OUTCOME_DUPLICATE, OUTCOME_REFUSED, OUTCOME_DELIVERED)

CHANNEL_SESSION = "session"
CHANNEL_ROOM = "room"
CHANNEL_MESH = "mesh"

#: Between messages of one batch, so the harness can tell where one ends.
_BATCH_SEPARATOR = "\n\n---\n\n"


class PeerMessageRefusedError(PeerMessageError):
    """This session will not take the message; the reason says why and what to do."""


@dataclass(frozen=True)
class PeerAcceptance:
    """What accepting a message came to: it is durably held, new or already there."""

    message_id: str
    duplicate: bool


@dataclass
class PeerInboxEntry:
    """One accepted message as this session holds it. This is what the durable frame carries."""

    envelope: PeerMessage
    accepted_at: str
    #: The message has been handed to the harness (so a restart must not hand it over again).
    injected: bool = False

    def frame(self) -> dict[str, Any]:
        """The durable broker frame for the entry's current state."""
        return {
            "type": PEER_MESSAGE_FRAME,
            "envelope": self.envelope.to_wire(),
            "accepted_at": self.accepted_at,
            "injected": self.injected,
        }

    @classmethod
    def from_frame(cls, payload: dict[str, Any]) -> PeerInboxEntry:
        injected = payload.get("injected")
        accepted_at = payload.get("accepted_at")
        if not isinstance(injected, bool) or not isinstance(accepted_at, str):
            raise PeerMessageError(
                "a durable peer message frame needs 'accepted_at' (text) and 'injected' (bool)"
            )
        return cls(
            envelope=PeerMessage.from_wire(payload.get("envelope")),
            accepted_at=accepted_at,
            injected=injected,
        )


def peer_channel(envelope: PeerMessage) -> str:
    """How the message reached this session: said in a room, from a mesh peer, or a session."""
    if envelope.room_id:
        return CHANNEL_ROOM
    if envelope.sender.kind is PeerKind.MESH_PEER:
        return CHANNEL_MESH
    return CHANNEL_SESSION


def peer_turn_id(session_id: str, message_ids: list[str]) -> str:
    """The user turn that carries a batch of messages has one id per batch."""
    batch = "\n".join(message_ids)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{session_id}:{_PEER_TURN_NAMESPACE}:{batch}"))


def render_peer_turn(entries: list[PeerInboxEntry]) -> str:
    """The text of one turn: each message as the contract renders it, oldest first."""
    if len(entries) == 1:
        return render_for_harness(entries[0].envelope)
    header = f"[{len(entries)} messages arrived while you were working, oldest first]"
    body = _BATCH_SEPARATOR.join(render_for_harness(entry.envelope) for entry in entries)
    return f"{header}\n\n{body}"


class PeerInboxMixin:
    """Broker behavior for receiving messages from other agents."""

    _peer_inbox: dict[str, PeerInboxEntry]
    _peer_inbox_lock: asyncio.Lock
    _boundary_injection_lock: asyncio.Lock
    _peer_inbox_tasks: set[asyncio.Task[None]]
    _peer_pair_counts: dict[tuple[str, str], dict[str, int]]

    def _init_peer_inbox(self) -> None:
        self._peer_inbox = {}
        self._peer_inbox_lock = asyncio.Lock()
        self._boundary_injection_lock = asyncio.Lock()
        self._peer_inbox_tasks = set()
        self._peer_pair_counts = {}
        self._peer_inbox_restore_failure: str | None = None

    # -- accepting

    async def accept_peer_message(
        self,
        envelope: PeerMessage,
        request_id: str | None = None,
        *,
        channel: str | None = None,
    ) -> PeerAcceptance:
        """Hold the message durably, then trigger its delivery. Raises when it is refused.

        Returns once Forge has confirmed the message in the session's event log, not when the
        harness has read it. A message already held (same ``message_id``) changes nothing and
        is reported as a duplicate. ``channel`` names how it arrived when the path knows
        better than the envelope (the mesh does).
        """
        via = channel or peer_channel(envelope)
        try:
            self._check_peer_message_acceptable(envelope)
        except PeerMessageRefusedError as exc:
            self._count_peer_message(envelope, via, OUTCOME_REFUSED)
            logger.error(
                "peer message %s from %s refused (request_id=%s): %s",
                envelope.message_id,
                envelope.sender.label(),
                request_id,
                exc,
            )
            raise
        async with self._peer_inbox_lock:
            if envelope.message_id in self._peer_inbox:
                self._count_peer_message(envelope, via, OUTCOME_DUPLICATE)
                return PeerAcceptance(envelope.message_id, duplicate=True)
            entry = PeerInboxEntry(
                envelope=envelope, accepted_at=datetime.now(UTC).isoformat(), injected=False
            )
            try:
                await self._record_peer_entries([entry])
            except PeerMessageRefusedError:
                self._count_peer_message(envelope, via, OUTCOME_REFUSED)
                raise
            self._peer_inbox[envelope.message_id] = entry
        self._count_peer_message(envelope, via, OUTCOME_ACCEPTED)
        self._spawn_boundary_injection(f"peer-inbox-accept-{envelope.message_id}")
        return PeerAcceptance(envelope.message_id, duplicate=False)

    def _check_peer_message_acceptable(self, envelope: PeerMessage) -> None:
        if not self._settings.peer_inbox.enabled:
            raise PeerMessageRefusedError(
                "this session does not take peer messages (peer_inbox.enabled is off)"
            )
        if envelope.recipient.id != str(self.session_id):
            raise PeerMessageRefusedError(
                f"the message is addressed to {envelope.recipient.label()}, but this is "
                f"session {self.session_id}"
            )
        if self._peer_inbox_restore_failure is not None:
            raise PeerMessageRefusedError(
                "this session could not restore its inbox from the durable log "
                f"({self._peer_inbox_restore_failure}), so it cannot tell which messages it "
                "already holds; restart the session once Forge's log is reachable"
            )
        if self._startup_state == "failed":
            raise PeerMessageRefusedError(
                f"this session's broker failed to start: {self._startup_failure}"
            )
        if self._is_room_routed_session():
            raise PeerMessageRefusedError(
                "this session is routed to a room participant and has no harness of its own "
                "to read the message; address the participant in the room"
            )
        if not (self._settings.event_log_enabled and self.volundr_api_url):
            raise PeerMessageRefusedError(
                "this session has no durable Forge log (volundr_api_url unset or "
                "event_log_enabled=false), so a peer message could not survive a restart"
            )

    async def _record_peer_entries(self, entries: list[PeerInboxEntry]) -> None:
        """Write the entries' state to the session event log; wait until Forge stored them.

        A message that is not durable would be lost by a restart, so a frame Forge did not
        store is a refusal, never a message that exists only in memory. The sender retries; the
        message id makes the retry safe.
        """
        receipts = []
        for entry in entries:
            logged = self._enqueue_event_log(entry.frame())
            if logged is None:
                raise PeerMessageRefusedError(
                    "the durable event log refused the peer message frame; the message would "
                    "not survive a restart"
                )
            receipts.append((entry, self._watch_event_log_entry(logged)))
        timeout_s = self._settings.forge_mcp.notify_confirm_timeout_s
        for entry, receipt in receipts:
            stored = await self._await_event_log_receipt(receipt, timeout_s)
            if stored is False:
                raise PeerMessageRefusedError(
                    f"the peer message frame for {entry.envelope.message_id} was not stored "
                    "(it was dropped while Forge was unreachable, or its sequence conflicted); "
                    "send it again"
                )
            if stored is None:
                raise PeerMessageRefusedError(
                    f"Forge did not confirm the peer message {entry.envelope.message_id} "
                    f"within forge_mcp.notify_confirm_timeout_s ({timeout_s}s); send it again "
                    "(the message id makes that safe)"
                )

    # -- delivery at a turn boundary

    def _spawn_boundary_injection(self, name: str) -> None:
        """Hand over whatever is waiting if the harness is between turns, in the background."""
        task = asyncio.create_task(self._inject_at_turn_boundary(), name=name)
        self._peer_inbox_tasks.add(task)
        task.add_done_callback(self._peer_inbox_task_done)

    def _spawn_peer_inbox_task(self, coro: Coroutine[Any, Any, None], name: str) -> None:
        task = asyncio.create_task(coro, name=name)
        self._peer_inbox_tasks.add(task)
        task.add_done_callback(self._peer_inbox_task_done)

    def _peer_inbox_task_done(self, task: asyncio.Task[None]) -> None:
        self._peer_inbox_tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("peer inbox task %s failed", task.get_name(), exc_info=task.exception())

    def _peer_messages_waiting(self) -> bool:
        return any(not entry.injected for entry in self._peer_inbox.values())

    async def _inject_at_turn_boundary(self) -> None:
        """Hand the harness what is waiting, if it is between turns.

        A pending workflow step goes first; the peer messages wait for the next boundary. One
        lock covers both so two triggers never decide at once.
        """
        async with self._boundary_injection_lock:
            if await self._maybe_inject_workflow_step():
                return
            await self._maybe_inject_peer_messages()

    async def _observe_turn_boundary(self, state: str) -> None:
        """A turn started or ended: track the open step, and deliver at the next idle."""
        await self._observe_workflow_step_activity(state)
        if state == "idle":
            self._spawn_boundary_injection("peer-inbox-idle")

    async def _maybe_inject_peer_messages(self) -> bool:
        """Hand every waiting message to the harness as one turn. True when it did."""
        async with self._peer_inbox_lock:
            waiting = [entry for entry in self._peer_inbox.values() if not entry.injected]
            if not waiting:
                return False
            if self._transport is None or self._activity_state in ("stopped", "error"):
                return False
            urgent = any(entry.envelope.urgent for entry in waiting)
            if not urgent and not self._turn_boundary_open():
                return False
            message_ids = [entry.envelope.message_id for entry in waiting]
            turn_id = peer_turn_id(str(self.session_id), message_ids)
            text = render_peer_turn(waiting)
            for entry in waiting:
                entry.injected = True
            try:
                await self._record_peer_entries(waiting)
                await self._ensure_transport_started()
                task = await self._submit_user_turn(
                    text,
                    msg_id=turn_id,
                    request_id=None,
                    source=PEER_TURN_SOURCE,
                    extra_metadata={"peer_messages": message_ids},
                )
            except Exception:
                await self._return_to_waiting(waiting)
                raise
            for entry in waiting:
                self._count_peer_message(
                    entry.envelope, peer_channel(entry.envelope), OUTCOME_DELIVERED
                )
        self._spawn_peer_inbox_task(
            self._watch_peer_delivery(task, turn_id, message_ids), f"peer-inbox-delivered-{turn_id}"
        )
        return True

    async def _return_to_waiting(self, entries: list[PeerInboxEntry]) -> None:
        """The harness did not get these: put them back, durably, so a boundary retries."""
        for entry in entries:
            entry.injected = False
        try:
            await self._record_peer_entries(entries)
        except PeerMessageError as exc:
            ids = ", ".join(entry.envelope.message_id for entry in entries)
            record_degraded(
                "skuld.peer_inbox.inject",
                f"peer messages {ids} were not handed to the harness, and putting them back "
                f"in the inbox could not be stored either ({exc}); a restart will not deliver "
                "them",
            )

    async def _watch_peer_delivery(
        self, task: asyncio.Task[None], turn_id: str, message_ids: list[str]
    ) -> None:
        """Messages whose turn never reached the harness are reported, never quietly lost."""
        await task
        turn = next((t for t in self._conversation_turns if t.id == turn_id), None)
        if turn is None or turn.metadata.get("steering_state") != "failed":
            return
        record_degraded(
            "skuld.peer_inbox.delivery",
            f"the harness did not accept the turn carrying peer messages {', '.join(message_ids)} "
            "after the delivery retries (delivery.max_attempts); see the session's "
            "user_delivery_failed frame. They go back to waiting for the next turn boundary.",
        )
        async with self._peer_inbox_lock:
            entries = [
                self._peer_inbox[message_id]
                for message_id in message_ids
                if message_id in self._peer_inbox
            ]
            await self._return_to_waiting(entries)

    # -- restore

    def _refuse_peer_messages_after_failed_restore(self, exc: BaseException) -> None:
        """The log could not be read back: refuse peer messages loudly, never take them blind.

        Without the restored inbox this session cannot dedup or hand over what it already
        accepted, so taking new messages would lose or repeat them. The session keeps running
        for the person using it; only peers are refused, each with this reason.
        """
        self._peer_inbox_restore_failure = str(exc) or type(exc).__name__
        record_degraded(
            "skuld.peer_inbox.restore",
            f"session {self.session_id} could not restore its peer inbox from the durable log "
            f"({self._peer_inbox_restore_failure}); it refuses peer messages until restarted",
        )

    def _restore_durable_peer_inbox(self, frames: list) -> None:
        """Rebuild the inbox from the log: each message at its latest logged state."""
        for frame in frames:
            if getattr(frame, "kind", "") != PEER_MESSAGE_FRAME:
                continue
            entry = PeerInboxEntry.from_frame(frame.payload)
            self._peer_inbox[entry.envelope.message_id] = entry
        for entry in self._peer_inbox.values():
            counts = self._pair_counts(entry.envelope)
            counts[OUTCOME_ACCEPTED] += 1
            if entry.injected:
                counts[OUTCOME_DELIVERED] += 1

    # -- lookup for replies

    def received_peer_message(self, message_id: str) -> PeerMessage:
        """A message this session received, by id, for the ``reply`` tool to answer."""
        entry = self._peer_inbox.get(message_id)
        if entry is None:
            raise PeerMessageError(
                f"this session received no peer message {message_id!r}; reply with the "
                "message_id printed in the message header"
            )
        return entry.envelope

    # -- counting

    def _pair_counts(self, envelope: PeerMessage) -> dict[str, int]:
        key = (envelope.sender.id, envelope.recipient.id)
        counts = self._peer_pair_counts.get(key)
        if counts is None:
            counts = dict.fromkeys(_OUTCOMES, 0)
            self._peer_pair_counts[key] = counts
        return counts

    def _count_peer_message(self, envelope: PeerMessage, channel: str, outcome: str) -> None:
        self._pair_counts(envelope)[outcome] += 1
        get_observability().count(
            PEER_MESSAGES_COUNTER,
            attributes={
                "sender": envelope.sender.id,
                "recipient": envelope.recipient.id,
                "channel": channel,
                "outcome": outcome,
            },
            description="Peer messages this session received, by sender, recipient and outcome.",
        )

    def peer_message_status(self) -> dict[str, Any]:
        """Per sender/recipient pair counts, for the session status. No cap, only visibility."""
        pairs = [
            {"sender": sender, "recipient": recipient, **counts}
            for (sender, recipient), counts in sorted(self._peer_pair_counts.items())
        ]
        return {"pairs": pairs}
