"""The broker as the Forge MCP host: its own session's environment and notifications."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import socket
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from niuu.build_identity import build_identity
from niuu.cap_telemetry import record_cap_hit
from niuu.domain.artifact_reports import ArtifactReport, build_report_turn
from niuu.domain.notifications import NotificationDraft, build_notification_turn
from niuu.domain.peer_envelope import derive_message_id
from niuu.domain.peer_message import (
    PeerAddress,
    PeerKind,
    PeerMessage,
    ReplyKind,
    ReplyRoute,
)
from niuu.domain.services.forge_session_token import SESSION_ID_CLAIM
from niuu.domain.workflow_step import WorkflowStepCloseArgs
from niuu.forge_mcp.credentials import is_forge_session_claims, token_scopes, unverified_claims
from niuu.forge_mcp.models import (
    SECRET_NAME_MAX_CHARS,
    SECRET_NAME_PATTERN,
    AskOutcome,
    ForgeApiError,
    SecretRequestError,
)
from niuu.forge_mcp.ports import ForgeClient, ForgeMcpHost
from niuu.observability import get_observability
from niuu.ports.mesh import PeerNotFoundError
from skuld.conversation_models import ConversationTurn
from skuld.mesh_follows import MeshFollowError, follow_for
from skuld.mesh_topics import (
    InvalidTopicError,
    UndeclaredTopicError,
    build_publication,
    peer_table,
    publication_event_id,
    publication_topics,
    validate_subscribable_topic,
)
from skuld.mesh_topics import (
    producers_of as producer_peers,
)
from skuld.peer_inbox import (
    CHANNEL_MESH,
    OUTCOME_ASKED,
    OUTCOME_FAILED,
    OUTCOME_PUBLISHED,
    OUTCOME_REFUSED,
    PEER_MESSAGES_COUNTER,
    AnswerWait,
    TakenAnswer,
)
from skuld.session_runtime import RuntimeDirError

if TYPE_CHECKING:
    from skuld.broker import Broker

#: Counts of the topics and threads a session followed or stopped following.
MESH_FOLLOWS_COUNTER = "skuld.mesh.follows"

logger = logging.getLogger("skuld.broker")

NOTIFICATION_TURN_PREFIX = "nt_"
REPORT_TURN_PREFIX = "ar_"
SECRET_REQUEST_TURN_PREFIX = "sr_"
#: The tool_use part the chat renders as a masked card; its input never holds a value.
SECRET_REQUEST_TOOL_NAME = "secret_request"
SECRET_PENDING = "pending"
SECRET_FULFILLED = "fulfilled"
SECRET_DELIVERED = "delivered"
SECRET_DECLINED = "declined"
SECRET_CANCELLED = "cancelled"
#: The OTel counter every ``ask`` increments once, by the outcome it ended with.
ASK_OUTCOMES_COUNTER = "skuld.ask.outcomes"
#: Lifecycle statuses after which a session will never answer.
_GONE_STATUSES = frozenset({"stopped", "failed", "archived"})
_ACTIVITY_AWAITING_INPUT = "awaiting_input"
_ACTIVITY_STOPPED = "stopped"
STATE_COMMITTED = "committed"
STATE_PENDING = "pending"

# Transport adapter module → engine name reported to the model.
_ENGINE_BY_MODULE = {
    "skuld.transports.tmux_interactive": "claude",
    "skuld.transports.sdk": "claude",
    "skuld.transports.sdk_websocket": "claude",
    "skuld.transports.subprocess": "claude",
    "skuld.transports.persistent_subprocess": "claude",
    "skuld.transports.remote_control": "claude",
    "skuld.transports.codex": "codex",
    "skuld.transports.codex_ws": "codex",
    "skuld.transports.grok": "grok",
    "skuld.transports.opencode": "opencode",
    "skuld.transports.muse": "muse",
    "skuld.transports.pi": "pi",
    "skuld.transports.dsh": "dsh",
}


def forge_credential(token: str) -> dict[str, Any]:
    """What credential the MCP's Forge calls use; never the token itself."""
    claims = unverified_claims(token)
    if not is_forge_session_claims(claims):
        return {"kind": "broker", "note": "no session token; the broker's own credential"}
    expires = claims.get("exp")
    return {
        "kind": "forge_session",
        "session_id": claims.get(SESSION_ID_CLAIM),
        "scopes": list(token_scopes(claims)),
        "expires_at": (
            datetime.fromtimestamp(int(expires), UTC).isoformat()
            if isinstance(expires, int | float)
            else None
        ),
    }


def engine_for_adapter(adapter_path: str) -> str:
    module = adapter_path.rsplit(".", 1)[0]
    return _ENGINE_BY_MODULE.get(module, module.rsplit(".", 1)[-1])


def new_notification_turn_id() -> str:
    return f"{NOTIFICATION_TURN_PREFIX}{uuid.uuid4().hex}"


@dataclass(frozen=True)
class _Ended:
    """A wait that Forge's view of the recipient settles: the outcome and why."""

    outcome: AskOutcome
    detail: str


def _blocked(who: str, kind: object) -> _Ended:
    return _Ended(
        AskOutcome.BLOCKED,
        f"{who} is waiting on a person"
        + (f" ({kind})" if isinstance(kind, str) and kind else "")
        + " (an approval or a question) and cannot read or answer until they respond. "
        "Tell the user, or call ask again with the same request_id later.",
    )


@dataclass(frozen=True)
class _RecipientState:
    """Forge's view of an ask's recipient: ``ended`` when it can never answer for now, else the
    messages its Skuld reports as accepted and not yet read."""

    ended: _Ended | None
    unread: frozenset[str] = frozenset()
    #: Why the state could not be read this time (a peer that did not answer); empty otherwise.
    error: str = ""


class BrokerForgeMcpHost(ForgeMcpHost):
    """Environment and notify for the broker's own session."""

    def __init__(self, broker: Broker, client: ForgeClient) -> None:
        self._broker = broker
        self._client = client

    @property
    def session_id(self) -> str | None:
        return str(self._broker.session_id)

    async def environment(self) -> dict[str, Any]:
        broker = self._broker
        settings = broker._settings
        environment: dict[str, Any] = {
            "session": {"id": str(broker.session_id), "name": settings.session.name},
            "node": {"hostname": socket.gethostname()},
            "workspace": broker.workspace_dir,
            "engine": engine_for_adapter(settings.transport_adapter),
            "transport": settings.transport_adapter.rsplit(".", 1)[-1],
            "model": broker.model,
            "reasoning_effort": settings.session.reasoning_effort or None,
            "forge_url": broker.volundr_api_url or None,
            "runtime": build_identity(),
            "mcp_servers": broker._effective_mcp_server_names(),
            "skills": broker._materialized_skill_names(),
            "present_file": True,
            "durable_log": bool(settings.event_log_enabled and broker.volundr_api_url),
            "forge_credential": forge_credential(settings.forge_mcp.token),
        }
        environment.update(await self._forge_view())
        return environment

    async def _forge_view(self) -> dict[str, Any]:
        """Project and Guild instance come from Forge's session row, not local config."""
        if not self._broker.volundr_api_url:
            return {"project_id": None, "instance_id": None, "forge_reachable": False}
        try:
            session = await self._client.get_session(str(self._broker.session_id), instance_id=None)
        except ForgeApiError as exc:
            return {
                "project_id": None,
                "instance_id": None,
                "forge_reachable": exc.status is not None,
                "forge_error": exc.detail,
            }
        coordination = session.get("coordination")
        coordination = coordination if isinstance(coordination, dict) else {}
        return {
            "project_id": coordination.get("project_id"),
            "project_role": coordination.get("role"),
            "parent": coordination.get("parent") or None,
            "instance_id": session.get("instance_id"),
            "forge_reachable": True,
        }

    async def notify(self, draft: NotificationDraft) -> dict[str, Any]:
        broker = self._broker
        if not (broker._settings.event_log_enabled and broker.volundr_api_url):
            raise ForgeApiError(
                "this session has no durable Forge log (volundr_api_url unset or "
                "event_log_enabled=false), so a notification could never reach Forge",
                status=503,
            )
        turn_id = new_notification_turn_id()
        created_at = datetime.now(UTC).isoformat()
        turn = ConversationTurn(
            **build_notification_turn(
                draft,
                turn_id=turn_id,
                session_id=str(broker.session_id),
                created_at=created_at,
            )
        )
        # Same durable path as present-file: history + durable log first, then live.
        entry = broker._append_turn(turn)
        if entry is None:
            raise ForgeApiError("the durable log refused the notification turn", status=503)
        receipt = broker._watch_event_log_entry(entry)
        await broker._channels.broadcast({"type": "conversation.turn", "turn": asdict(turn)})
        stored = await broker._await_event_log_receipt(
            receipt, broker._settings.forge_mcp.notify_confirm_timeout_s
        )
        if stored is False:
            raise ForgeApiError(
                "the notification's log entry was not stored (dropped while Forge was "
                "unreachable, or its sequence conflicted); it is visible in this session only",
                status=409,
            )
        notification = turn.parts[0]["input"]
        return {
            "notification_id": notification["notification_id"],
            "turn_id": turn_id,
            "session_seq": entry["seq"],
            "state": STATE_COMMITTED if stored else STATE_PENDING,
        }

    async def report_artifact(self, report: ArtifactReport) -> dict[str, Any]:
        """Append the report's durable turn; Forge's artifact projector records it (F1)."""
        broker = self._broker
        if not (broker._settings.event_log_enabled and broker.volundr_api_url):
            raise ForgeApiError(
                "this session has no durable Forge log, so a report could never reach Forge",
                status=503,
            )
        turn_id = f"{REPORT_TURN_PREFIX}{uuid.uuid4().hex}"
        turn = ConversationTurn(
            **build_report_turn(report, turn_id=turn_id, created_at=datetime.now(UTC).isoformat())
        )
        entry = broker._append_turn(turn)
        if entry is None:
            raise ForgeApiError("the durable log refused the report turn", status=503)
        receipt = broker._watch_event_log_entry(entry)
        await broker._channels.broadcast({"type": "conversation.turn", "turn": asdict(turn)})
        stored = await broker._await_event_log_receipt(
            receipt, broker._settings.forge_mcp.notify_confirm_timeout_s
        )
        if stored is False:
            raise ForgeApiError(
                "the report's log entry was not stored (dropped while Forge was unreachable, "
                "or its sequence conflicted)",
                status=409,
            )
        return {
            "turn_id": turn_id,
            "session_seq": entry["seq"],
            "url": report.url,
            "state": STATE_COMMITTED if stored else STATE_PENDING,
        }

    async def received_peer_message(self, message_id: str) -> PeerMessage:
        """A message this session's inbox holds, by id; an unknown id raises PeerMessageError."""
        return self._broker.received_peer_message(message_id)

    def ask_wait_max_s(self) -> float:
        return self._broker._settings.ask.wait_max_s

    async def ask_for_answer(
        self,
        *,
        recipient_id: str,
        instance_id: str | None,
        message_id: str,
        request_id: str,
        delivery_confirmed: bool,
        deadline_s: float,
        mesh_peer: str | None = None,
    ) -> dict[str, Any]:
        """Wait for the recipient's reply to ``message_id`` and say how the wait ended.

        The reply arrives in this session's own inbox and wakes the wait at once; Forge is
        asked only for what only it knows: whether the recipient is blocked on a person, gone,
        or has not yet read the message. Exactly one :class:`AskOutcome` comes back, counted
        on ``skuld.ask.outcomes``. A reply taken here is not handed to the harness again.
        """
        broker = self._broker
        poll_s = broker._settings.ask.poll_interval_s
        deadline = time.monotonic() + deadline_s
        async with broker.awaiting_peer_answer(message_id, recipient_id) as wait:
            next_check = time.monotonic()
            while True:
                wait.arrived.clear()
                taken = await broker.take_peer_answer(wait)
                if taken is not None:
                    return self._ask_answered(recipient_id, taken)
                now = time.monotonic()
                if now >= next_check:
                    state = await self._recipient_state(recipient_id, instance_id, mesh_peer)
                    if state.ended is not None:
                        return self._ask_ended(recipient_id, state.ended)
                    next_check = time.monotonic() + poll_s
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return await self._ask_deadline(
                        wait, recipient_id, instance_id, message_id, request_id,
                        delivery_confirmed, deadline_s, mesh_peer,
                    )  # fmt: skip
                pause = min(remaining, max(next_check - time.monotonic(), 0.0))
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(wait.arrived.wait(), timeout=pause)

    async def _ask_deadline(
        self,
        wait: AnswerWait,
        recipient_id: str,
        instance_id: str | None,
        message_id: str,
        request_id: str,
        delivery_confirmed: bool,
        deadline_s: float,
        mesh_peer: str | None,
    ) -> dict[str, Any]:
        """The deadline passed: one last look at the inbox and at Forge, then say which kind."""
        taken = await self._broker.take_peer_answer(wait)
        if taken is not None:
            return self._ask_answered(recipient_id, taken)
        state = await self._recipient_state(recipient_id, instance_id, mesh_peer)
        if state.ended is not None:
            return self._ask_ended(recipient_id, state.ended)
        again = (
            f"Call ask again with request_id {request_id!r} to keep waiting (the message is not "
            "sent twice); an answer that arrives after this call is delivered to you at your "
            "next turn boundary."
        )
        if message_id in state.unread:
            outcome, detail = AskOutcome.NOT_STARTED, (
                "the message reached the recipient's inbox, but it has taken no turn that read "
                f"it within {deadline_s:g}s (it is busy, or between turns with its harness not "
                f"yet picking the message up). {again}"
            )  # fmt: skip
        elif not delivery_confirmed:
            unconfirmed = (
                "the recipient's Skuld did not answer the send within mesh.rpc_timeout_s"
                if mesh_peer
                else "Forge did not confirm the send as delivered (it is pending or unconfirmed)"
            )
            outcome, detail = AskOutcome.DELIVERY_UNKNOWN, (
                f"{unconfirmed}, and no answer came within {deadline_s:g}s. Whether the "
                f"recipient has the message is unknown, not failed. {again}"
            )  # fmt: skip
        else:
            outcome, detail = AskOutcome.DEADLINE, (
                f"the recipient had not answered within deadline_s={deadline_s:g} "
                f"(ceiling ask.wait_max_s={self._broker._settings.ask.wait_max_s:g}, the longest "
                f"one MCP tool call may run). {again}"
                + (f" The peer's state could not be read: {state.error}." if state.error else "")
            )  # fmt: skip
        if deadline_s >= self._broker._settings.ask.wait_max_s:
            # Only the ceiling is a cap; a shorter deadline the caller chose is its own decision,
            # still named and counted in skuld.ask.outcomes.
            record_cap_hit("ask.wait_max_s", f"ask {request_id} ended {outcome.value}: {detail}")
        return self._ask_ended(recipient_id, _Ended(outcome, detail))

    async def _recipient_state(
        self, recipient_id: str, instance_id: str | None, mesh_peer: str | None
    ) -> _RecipientState:
        """The recipient's state: from its own Skuld over the mesh for a peer, else from Forge."""
        if mesh_peer is not None:
            return await self._mesh_peer_state(mesh_peer)
        return await self._forge_recipient_state(recipient_id, instance_id)

    async def _mesh_peer_state(self, peer_id: str) -> _RecipientState:
        """Gone when the peer left the table, else what its Skuld says about itself (RPC)."""
        adapter = self._mesh_adapter_or_raise()
        if peer_id not in adapter.peers():
            return _RecipientState(
                _Ended(
                    AskOutcome.GONE,
                    f"mesh peer {peer_id!r} is no longer in this session's peer table (it left "
                    "the flock or stopped announcing), so it cannot answer.",
                )
            )
        try:
            answer = await adapter.request_peer_state(peer_id)
        except PeerNotFoundError:
            return _RecipientState(
                _Ended(
                    AskOutcome.GONE,
                    f"mesh peer {peer_id!r} dropped from the peer table while its state was "
                    "being read, so it cannot answer.",
                )
            )
        except TimeoutError:
            return _RecipientState(None, error="its Skuld did not answer within mesh.rpc_timeout_s")
        if answer.get("status") != "ok":
            return _RecipientState(None, error=str(answer.get("error") or answer))
        activity = str(answer.get("activity_state") or "")
        if activity == _ACTIVITY_STOPPED:
            return _RecipientState(
                _Ended(AskOutcome.GONE, f"mesh peer {peer_id!r} reports its transport stopped.")
            )
        if activity == _ACTIVITY_AWAITING_INPUT:
            return _RecipientState(_blocked(peer_id, answer.get("kind")))
        waiting = answer.get("waiting")
        return _RecipientState(
            None, frozenset(waiting) if isinstance(waiting, list) else frozenset()
        )

    async def _forge_recipient_state(
        self, recipient_id: str, instance_id: str | None
    ) -> _RecipientState:
        """Ask Forge about the recipient: gone, blocked on a person, and what it has not read."""
        try:
            session = await self._client.get_session(recipient_id, instance_id=instance_id)
        except ForgeApiError as exc:
            if exc.status != 404:
                raise
            return _RecipientState(
                _Ended(
                    AskOutcome.GONE,
                    f"session {recipient_id} no longer exists (it was deleted), so it can "
                    "never answer.",
                )
            )
        status = str(session.get("status") or "")
        activity = str(session.get("activity_state") or "")
        if status in _GONE_STATUSES or activity == _ACTIVITY_STOPPED:
            return _RecipientState(
                _Ended(
                    AskOutcome.GONE,
                    f"session {recipient_id} is {status or activity} and will not answer. Start "
                    "it again, or ask another session.",
                )
            )
        metadata = session.get("activity_metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        if activity == _ACTIVITY_AWAITING_INPUT:
            return _RecipientState(_blocked(recipient_id, metadata.get("kind")))
        peers = metadata.get("peer_messages")
        waiting = peers.get("waiting") if isinstance(peers, dict) else None
        unread = frozenset(waiting) if isinstance(waiting, list) else frozenset()
        return _RecipientState(None, unread)

    def _ask_answered(self, recipient_id: str, taken: TakenAnswer) -> dict[str, Any]:
        envelope = taken.envelope
        result: dict[str, Any] = {
            "outcome": AskOutcome.ANSWERED.value,
            "answer": envelope.content,
            "from": envelope.sender.label(),
            "answer_message_id": envelope.message_id,
            "answered_at": envelope.sent_at.isoformat(),
        }
        if taken.already_delivered:
            result["note"] = (
                "this answer had already been delivered to you as a message at an earlier turn "
                "boundary, so it is in your context twice"
            )
        self._count_ask(AskOutcome.ANSWERED, recipient_id)
        return result

    def _ask_ended(self, recipient_id: str, ended: _Ended) -> dict[str, Any]:
        self._count_ask(ended.outcome, recipient_id)
        return {"outcome": ended.outcome.value, "detail": ended.detail}

    def _count_ask(self, outcome: AskOutcome, recipient_id: str) -> None:
        get_observability().count(
            ASK_OUTCOMES_COUNTER,
            attributes={"outcome": outcome.value, "recipient": recipient_id},
            description="How this session's ask calls ended, by outcome and recipient.",
        )

    def workflow_step_host(self) -> bool:
        return self._broker._settings.workflow_steps.enabled

    def mesh_host(self) -> bool:
        return self._broker._settings.mesh.enabled

    def _mesh_adapter_or_raise(self) -> Any:
        adapter = self._broker._mesh_adapter
        if adapter is None:
            raise ForgeApiError(
                "this session's mesh is not running (mesh.enabled is set but the adapter did "
                "not start); check the broker log, or set mesh.enabled to false. Messages to "
                "other sessions are not rerouted through Forge while the mesh is down",
                status=503,
            )
        return adapter

    async def mesh_peer_for(self, *, session_id: str | None, name: str | None) -> str | None:
        """The mesh peer id a recipient is, or ``None`` when it is not in the peer table.

        Looked up by session id or by name against the verified peer table: a peer's id, or
        for a session its display name (the session name it announces). A name two peers
        carry is an error that lists them. With the mesh enabled but not running, this raises:
        whether the recipient is a peer cannot be known, and the answer is never "use Forge".
        """
        if not self._broker._settings.mesh.enabled:
            return None
        peers = self._mesh_adapter_or_raise().peers()
        if session_id is not None:
            return session_id if session_id in peers else None
        if name in peers:
            return name
        matches = [peer.peer_id for peer in peers.values() if peer.display_name == name]
        if len(matches) > 1:
            raise ForgeApiError(
                f"{len(matches)} mesh peers are named {name!r}: {', '.join(sorted(matches))}. "
                "Address one by its peer id (send again with `to` set to the peer id).",
                status=409,
            )
        return matches[0] if matches else None

    async def send_mesh_message(
        self,
        peer: str,
        content: str,
        *,
        expect_reply: bool,
        request_id: str,
        in_reply_to: str,
        urgent: bool = False,
        thread: str = "",
    ) -> dict[str, Any]:
        """Send a peer message envelope to a mesh peer, stamped with this session's identity.

        The sender is this session (id, name, node) and, when a reply is expected, the reply
        route is this session's mesh peer id. Nothing about the sender comes from ``content``.
        A mesh that is down, a peer that is unknown or unreachable, and a peer that refuses
        the message are errors, never a quiet success.
        """
        broker = self._broker
        adapter = broker._mesh_adapter
        if adapter is None:
            raise ForgeApiError(
                "this session's mesh is not running (mesh.enabled is set but the adapter did "
                "not start); check the broker log, or set mesh.enabled to false",
                status=503,
            )
        sender = PeerAddress(
            kind=PeerKind.SESSION,
            id=str(broker.session_id),
            name=broker._settings.session.name,
            node=socket.gethostname(),
        )
        recipient = PeerAddress(kind=PeerKind.MESH_PEER, id=peer)
        message = PeerMessage(
            message_id=derive_message_id(sender, recipient, request_id),
            sender=sender,
            recipient=recipient,
            sent_at=datetime.now(UTC),
            content=content,
            reply=ReplyRoute(kind=ReplyKind.MESH, target=adapter.peer_id) if expect_reply else None,
            in_reply_to=in_reply_to,
            urgent=urgent,
            thread=thread,
        )
        try:
            answer = await adapter.send_peer_message(peer, message)
        except PeerNotFoundError as exc:
            raise ForgeApiError(
                f"mesh peer {peer!r} is not in this session's peer table; "
                "check the peer id with the flock's member list",
                status=404,
            ) from exc
        except TimeoutError as exc:
            raise ForgeApiError(
                f"mesh peer {peer!r} did not answer within mesh.rpc_timeout_s; the message may "
                "or may not have arrived: retry with the same request_id",
                status=504,
            ) from exc
        if answer.get("status") == "unconfirmed":
            # The recipient's Forge did not confirm in time: it may have stored the message.
            raise ForgeApiError(
                f"mesh peer {peer!r} did not confirm the message: {answer.get('error')}",
                status=504,
            )
        failure = answer.get("error") or (
            answer.get("status") if answer.get("status") == "error" else None
        )
        if failure:
            raise ForgeApiError(f"mesh peer {peer!r} refused the message: {failure}", status=502)
        return {
            "message_id": message.message_id,
            "status": answer.get("status") or "sent",
            "path": "mesh",
            "peer": peer,
        }

    def _peer_table(self) -> dict[str, Any]:
        """The peers this session's discovery knows, or an error when it has none.

        A session with no discovery cannot see any peer: that is an error and not an empty
        table, which would read as "nobody is there".
        """
        broker = self._broker
        discovery = broker._mesh_discovery
        if broker._mesh_adapter is None or discovery is None:
            raise ForgeApiError(
                "this session has no peer discovery (mesh.enabled is set but mesh.adapters or "
                "mesh.discovery_adapters is empty, or the mesh did not start), so it cannot list "
                "peers. Configure a discovery adapter, or use mesh_send with a peer id you know",
                status=503,
            )
        return discovery.peers()

    async def mesh_ask(
        self,
        content: str,
        *,
        producers_of: str,
        peers: list[str],
        request_id: str,
        wait_s: float,
    ) -> dict[str, Any]:
        """Put a question to every peer that produces a topic, or to peers the asker chose.

        The recipients are resolved from the peer table as facts: the peers that announce
        they produce the topic (not its consumers; the asker's own ``produces`` does not
        limit a question), or exactly the peers named. A question nobody can receive is an
        error, never silence. Each recipient gets it directly with the same message id and
        thread; answers come back as events into the inbox, any number or none. With
        ``wait_s`` the call also waits that long and returns the answers so far.
        """
        broker = self._broker
        adapter = broker._mesh_adapter
        table = self._peer_table()
        unknown = 0
        if producers_of:
            recipients, unknown = producer_peers(table.values(), producers_of)
            if not recipients:
                self._count_question(producers_of, OUTCOME_REFUSED)
                raise ForgeApiError(
                    f"no peer announces that it produces {producers_of!r}, so the question "
                    f"reached nobody ({len(table)} peers known; {unknown} of them did not "
                    "announce what they produce). Nothing was sent. Choose peers yourself from "
                    "mesh_peers by their description, or publish an event if you mean to "
                    "announce something rather than ask",
                    status=409,
                )
            audience = f"the peers that produce {producers_of!r}"
            reason = f"you announce that you produce {producers_of!r}"
        else:
            absent = [peer for peer in peers if peer not in table]
            if absent:
                self._count_question(",".join(absent), OUTCOME_REFUSED)
                raise ForgeApiError(
                    f"not in this session's peer table: {', '.join(absent)}. Nothing was sent. "
                    "Choose peer ids from mesh_peers",
                    status=404,
                )
            recipients = [table[peer] for peer in peers]
            audience = "the peers chosen"
            reason = "you were chosen to answer"
        sender = PeerAddress(
            kind=PeerKind.SESSION,
            id=str(broker.session_id),
            name=broker._settings.session.name,
            node=socket.gethostname(),
        )
        question_id = publication_event_id(adapter.peer_id, "question", request_id)
        text = f"[This question is asked of you because {reason}.]\n{content}"
        outcomes = await asyncio.gather(
            *(self._ask_one(adapter, sender, peer, question_id, text) for peer in recipients)
        )
        delivered = [outcome for outcome in outcomes if "error" not in outcome]
        failed = [outcome for outcome in outcomes if "error" in outcome]
        if not delivered:
            raise ForgeApiError(
                f"the question reached none of {audience}: "
                + "; ".join(f"{o['peer']}: {o['error']}" for o in failed)
                + ". Retry with the same request_id once they are reachable",
                status=502,
            )
        answer: dict[str, Any] = {
            "question_id": question_id,
            "thread": question_id,
            "asked": audience,
            "delivered": delivered,
            "failed": failed,
            "peers_not_announcing_produces": unknown,
        }
        if wait_s > 0:
            asked = {outcome["peer"] for outcome in delivered}
            answer.update(await self._wait_for_answers(question_id, wait_s, asked))
        return answer

    async def _ask_one(
        self,
        adapter: Any,
        sender: PeerAddress,
        peer: Any,
        question_id: str,
        text: str,
    ) -> dict[str, Any]:
        message = PeerMessage(
            message_id=question_id,
            sender=sender,
            recipient=PeerAddress(kind=PeerKind.MESH_PEER, id=peer.peer_id),
            sent_at=datetime.now(UTC),
            content=text,
            reply=ReplyRoute(kind=ReplyKind.MESH, target=adapter.peer_id),
            thread=question_id,
        )
        who = {"peer": peer.peer_id, "name": getattr(peer, "display_name", "")}
        try:
            answer = await adapter.send_peer_message(peer.peer_id, message)
            failure = answer.get("error") or (
                answer.get("status") if answer.get("status") == "error" else None
            )
        except PeerNotFoundError:
            failure = "it left the peer table before the question reached it"
        except TimeoutError:
            failure = "it did not answer within mesh.rpc_timeout_s (it may still have the question)"
        except Exception as exc:  # the one send; any transport failure is this peer's, named
            failure = f"{type(exc).__name__}: {exc}"
        if failure:
            logger.error("mesh_ask %s to %s failed: %s", question_id, peer.peer_id, failure)
            self._count_question(peer.peer_id, OUTCOME_FAILED)
            return {**who, "error": str(failure)}
        self._count_question(peer.peer_id, OUTCOME_ASKED)
        return who

    async def _wait_for_answers(
        self, question_id: str, wait_s: float, asked: set[str]
    ) -> dict[str, Any]:
        """Wait up to ``wait_s`` for the answers to the question and return them, taken.

        It returns as soon as every peer the question reached has answered (``asked`` is the
        asker's own choice, not a guessed count), else at the deadline with the answers so
        far; ``ended`` says which. Answers from peers that were not asked are returned too and
        never end the wait. A sender is one id: the mesh-verified peer however it answered, or
        the claimed sender id when the mesh verified nothing.
        """
        broker = self._broker
        deadline = time.monotonic() + wait_s
        ended = "deadline"
        async with broker.awaiting_question_answers(question_id) as wait:
            while True:
                wait.arrived.clear()
                if asked <= broker.question_answerers(wait):
                    ended = "all_asked_answered"
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(wait.arrived.wait(), timeout=remaining)
            taken = await broker.take_question_answers(wait)
        answers = []
        for item in taken:
            envelope = item.envelope
            identity, kind = item.identity
            entry = {
                "from": envelope.sender.label(),
                "identity": identity,
                "identity_kind": kind,
                "message_id": envelope.message_id,
                "content": envelope.content,
                "answered_at": envelope.sent_at.isoformat(),
            }
            if item.already_delivered:
                entry["note"] = "already handed to you as a message at an earlier turn boundary"
            answers.append(entry)
        answered = {answer["identity"] for answer in answers}
        return {
            "waited_s": wait_s,
            "ended": ended,
            "answers": answers,
            "answered": len(answered),
            "not_yet_answered": sorted(asked - answered),
        }

    def _count_question(self, recipient: str, outcome: str) -> None:
        get_observability().count(
            PEER_MESSAGES_COUNTER,
            attributes={
                "sender": str(self._broker.session_id),
                "recipient": recipient,
                "channel": CHANNEL_MESH,
                "outcome": outcome,
            },
            description="Peer messages this session received, by sender, recipient and outcome.",
        )

    async def mesh_peers(
        self, *, role: str, capability: str, consumes: str, produces: str
    ) -> dict[str, Any]:
        """The peers this session's discovery knows, as they announced themselves.

        Facts only: the filters are exact matches on announced fields and nothing is ranked.
        A session with no discovery configured cannot see any peer, which is an error and not
        an empty table: an empty table would read as "nobody is there".
        """
        table = peer_table(
            self._peer_table().values(),
            role=role,
            capability=capability,
            consumes=consumes,
            produces=produces,
        )
        return {"peers": table, "count": len(table), "as_of": datetime.now(UTC).isoformat()}

    async def publish_mesh_event(
        self,
        topic: str,
        content: str,
        *,
        request_id: str,
        correlation_id: str,
        in_reply_to: str,
    ) -> dict[str, Any]:
        """Publish ``content`` on a declared mesh topic, straight onto this session's mesh.

        No Forge round trip. The sender is this session's mesh identity, stamped here; the
        content cannot name one. The answer is the transport accepting the event: no consumer
        is waited for and none is promised. A topic the session did not declare, a mesh that
        is down and a transport that refuses the event are errors, each counted.
        """
        broker = self._broker
        adapter = broker._mesh_adapter
        topics = broker._mesh_topics
        if adapter is None or topics is None:
            self._count_publication(topic, OUTCOME_FAILED)
            raise ForgeApiError(
                "this session's mesh is not running (mesh.enabled is set but the adapter did "
                "not start); check the broker log, or set mesh.enabled to false",
                status=503,
            )
        try:
            topics.check_publishable(topic)
        except UndeclaredTopicError as exc:
            self._count_publication(topic, OUTCOME_REFUSED)
            raise ForgeApiError(str(exc), status=403) from exc
        correlation_id = correlation_id or self._thread_of(in_reply_to)
        event = build_publication(
            peer_id=adapter.peer_id,
            session_id=str(broker.session_id),
            session_name=broker._settings.session.name,
            persona_id=topics.persona_id,
            topic=topic,
            content=content,
            request_id=request_id,
            urgency=broker._settings.mesh.published_event_urgency,
            correlation_id=correlation_id,
            in_reply_to=in_reply_to,
            trace_context=get_observability().inject(),
        )
        # An answer also goes to the peer it answers, so that peer needs no subscription.
        answered = self._answered_peer(in_reply_to)
        copies = publication_topics(event, topic, answered)
        for index, target in enumerate(copies):
            try:
                await adapter.publish_event(event, target)
            except Exception as exc:
                self._count_publication(topic, OUTCOME_FAILED)
                logger.error(
                    "mesh_publish of %s (event %s) failed on %s: %s",
                    topic,
                    event.event_id,
                    target,
                    exc,
                )
                done = (
                    f" It had already gone to {index} of its {len(copies)} destinations."
                    if index
                    else " Nothing is known to have been published."
                )
                raise ForgeApiError(
                    f"the mesh did not accept the event on {topic!r}: {exc}.{done} Retry with "
                    "the same request_id once the mesh is back; consumers drop the repeat",
                    status=502,
                ) from exc
        self._count_publication(topic, OUTCOME_PUBLISHED)
        answer = {
            "event_id": event.event_id,
            "topic": topic,
            "correlation_id": event.correlation_id,
            "status": "published",
        }
        if answered:
            answer["answered_peer"] = answered
        return answer

    def _thread_of(self, message_id: str) -> str:
        """The thread of a message this session received, else empty (use the id itself)."""
        entry = self._broker._peer_inbox.get(message_id) if message_id else None
        return entry.envelope.thread if entry is not None else ""

    def _answered_peer(self, in_reply_to: str) -> str:
        """The mesh peer whose message this publication answers, if it came over the mesh.

        An id this session never received (such as its own earlier event) names no peer: the
        publication is still part of that thread, it just has nobody to be sent to directly.
        """
        if not in_reply_to:
            return ""
        entry = self._broker._peer_inbox.get(in_reply_to)
        if entry is None or entry.envelope.reply is None:
            return ""
        if entry.envelope.reply.kind is not ReplyKind.MESH:
            return ""
        return entry.envelope.reply.target

    async def mesh_subscribe(self, *, topic: str, correlation_id: str) -> dict[str, Any]:
        """Start listening to a topic or follow a thread, durably. Any valid topic is allowed.

        The persona's ``consumes`` stays the standing subscription; this adds to it and
        survives a restart. Nothing waits for an event.
        """
        return await self._change_follow(topic, correlation_id, follow=True)

    async def mesh_unsubscribe(self, *, topic: str, correlation_id: str) -> dict[str, Any]:
        """Stop following a topic or thread that was followed at runtime."""
        return await self._change_follow(topic, correlation_id, follow=False)

    async def _change_follow(
        self, topic: str, correlation_id: str, *, follow: bool
    ) -> dict[str, Any]:
        broker = self._broker
        action = "followed" if follow else "unfollowed"
        try:
            target = follow_for(
                topic=validate_subscribable_topic(topic) if topic else "",
                correlation_id=correlation_id,
            )
            if follow:
                new = await broker.follow_mesh(target)
            else:
                await broker.unfollow_mesh(target)
                new = True
        except (MeshFollowError, InvalidTopicError) as exc:
            self._count_follow("refused", topic or correlation_id)
            raise ForgeApiError(str(exc), status=409) from exc
        except RuntimeError as exc:
            self._count_follow("failed", topic or correlation_id)
            raise ForgeApiError(
                f"the mesh did not change the subscription: {exc}", status=503
            ) from exc
        self._count_follow(action if new else "already", target.topic)
        return {
            "status": action if new else "already",
            "topic": target.topic,
            **({"correlation_id": target.thread} if target.thread else {}),
            **broker.mesh_subscriptions(),
        }

    def _count_follow(self, outcome: str, subject: str) -> None:
        get_observability().count(
            MESH_FOLLOWS_COUNTER,
            attributes={"outcome": outcome, "topic": subject},
            description="Topics and threads this session followed or stopped following.",
        )

    def _count_publication(self, topic: str, outcome: str) -> None:
        """Count a publication on ``skuld.peer.messages``, the recipient being the topic."""
        get_observability().count(
            PEER_MESSAGES_COUNTER,
            attributes={
                "sender": str(self._broker.session_id),
                "recipient": topic,
                "channel": CHANNEL_MESH,
                "outcome": outcome,
            },
            description="Peer messages this session received, by sender, recipient and outcome.",
        )

    async def close_workflow_step(self, args: WorkflowStepCloseArgs) -> dict[str, Any]:
        """Close the session's open workflow step; a close the step does not allow raises."""
        return await self._broker.close_workflow_step(args)

    async def request_secret(self, name: str, purpose: str, target: str | None) -> dict[str, Any]:
        """Ask the user for a secret in the chat and hand the agent a file path, never the value.

        Forge holds the request and the value (the user types it into a card that posts
        straight to Forge). This waits for the answer, claims the value once and writes it
        0600 into the session's runtime dir. The value is never logged, returned, put in a
        turn or put in an exception message.
        """
        broker = self._broker
        if not (broker._settings.event_log_enabled and broker.volundr_api_url):
            raise ForgeApiError(
                "this session has no durable Forge log (volundr_api_url unset or "
                "event_log_enabled=false), so the user could never see the secret request",
                status=503,
            )
        if not re.fullmatch(SECRET_NAME_PATTERN, name) or len(name) > SECRET_NAME_MAX_CHARS:
            raise ForgeApiError(
                f"a secret's name must match {SECRET_NAME_PATTERN} and be at most "
                f"{SECRET_NAME_MAX_CHARS} characters (it becomes a file name)",
                status=422,
            )
        session_id = str(broker.session_id)
        body: dict[str, Any] = {"name": name, "purpose": purpose}
        if target:
            body["target"] = target
        request = await self._client.create_secret_request(session_id, body)
        request_id = _secret_request_id(request)
        try:
            await self._emit_secret_request_turn(request_id, name, purpose, target)
        except ForgeApiError:
            await self._client.cancel_secret_request(session_id, request_id)
            raise
        request = await self._await_secret_answer(session_id, request_id, name, request)
        status = request.get("status")
        if status == SECRET_DELIVERED:
            return self._already_delivered(name, request_id)
        return await self._claim_secret(session_id, request_id, name)

    async def _emit_secret_request_turn(
        self, request_id: str, name: str, purpose: str, target: str | None
    ) -> None:
        """Append the card's durable turn, once per request id (a re-ask reuses the card)."""
        broker = self._broker
        turn_id = f"{SECRET_REQUEST_TURN_PREFIX}{request_id}"
        if any(existing.id == turn_id for existing in broker._conversation_turns):
            return
        part = {
            "type": "tool_use",
            "id": request_id,
            "name": SECRET_REQUEST_TOOL_NAME,
            "input": {
                "request_id": request_id,
                "name": name,
                "purpose": purpose,
                "target": target,
            },
        }
        turn = ConversationTurn(
            id=turn_id,
            role="assistant",
            content=f"Secret requested: {name}",
            parts=[part],
            metadata={"cost": None, "model": None, "usage": {}, "secret_request": True},
        )
        # Same durable path as present-file: history + durable log first, then live.
        if broker._append_turn(turn) is None:
            raise ForgeApiError("the durable log refused the secret request turn", status=503)
        await broker._channels.broadcast({"type": "conversation.turn", "turn": asdict(turn)})

    async def _await_secret_answer(
        self, session_id: str, request_id: str, name: str, request: dict[str, Any]
    ) -> dict[str, Any]:
        """Poll until the user fulfilled the request; raise when declined, cancelled or late."""
        cfg = self._broker._settings.secret_requests
        deadline = time.monotonic() + cfg.wait_max_s
        while True:
            status = request.get("status")
            if status in (SECRET_FULFILLED, SECRET_DELIVERED):
                return request
            if status == SECRET_DECLINED:
                raise SecretRequestError(
                    f"the user declined to provide {name}. Do not ask again unless they "
                    "change their mind; tell them what you could not do without it."
                )
            if status == SECRET_CANCELLED:
                raise SecretRequestError(
                    f"the request for {name} was cancelled before the user answered. Call "
                    "request_secret again if you still need it."
                )
            if status != SECRET_PENDING:
                raise ForgeApiError(
                    f"Forge reported secret request {request_id} in unknown status {status!r}",
                    status=502,
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.error(
                    "request_secret: %s not answered within secret_requests.wait_max_s=%s",
                    name,
                    cfg.wait_max_s,
                )
                raise SecretRequestError(
                    f"the user has not provided {name} within {cfg.wait_max_s:g}s "
                    "(secret_requests.wait_max_s). The request is still open in the chat: "
                    "call request_secret again with the same name to keep waiting, or raise "
                    "secret_requests.wait_max_s."
                )
            await asyncio.sleep(min(cfg.poll_interval_s, remaining))
            request = await self._client.get_secret_request(session_id, request_id)

    async def _claim_secret(self, session_id: str, request_id: str, name: str) -> dict[str, Any]:
        claimed = await self._client.claim_secret_request(session_id, request_id)
        value = claimed.get("value")
        if claimed.get("name") != name or not isinstance(value, str) or not value:
            # Say what was wrong with the shape, never what was in it.
            raise ForgeApiError(
                f"Forge's claim of secret request {request_id} did not return a non-empty "
                f"value for {name}",
                status=502,
            )
        try:
            path = self._broker._session_runtime.write_secret(name, value)
        except (OSError, RuntimeDirError) as exc:
            reason = exc.strerror if isinstance(exc, OSError) and exc.strerror else str(exc)
            raise SecretRequestError(
                f"{name} was provided but could not be written to the session's secrets "
                f"directory ({reason}). The value was handed over once and is gone; call "
                "request_secret again to ask for it again."
            ) from exc
        return {"status": "ready", "name": name, "path": str(path)}

    def _already_delivered(self, name: str, request_id: str) -> dict[str, Any]:
        """The request was claimed before this call; its file is the answer if it is there."""
        path = self._broker._session_runtime.secrets_dir / name
        if not path.is_file():
            raise SecretRequestError(
                f"the secret request {request_id} for {name} was already delivered, but its "
                "file is gone. Call request_secret again to ask for it again."
            )
        return {"status": "ready", "name": name, "path": str(path)}


def _secret_request_id(request: dict[str, Any]) -> str:
    request_id = request.get("id")
    if not isinstance(request_id, str) or not request_id:
        raise ForgeApiError("Forge's secret request answer has no id", status=502)
    return request_id
