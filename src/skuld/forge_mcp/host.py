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
from skuld.peer_inbox import AnswerWait, TakenAnswer
from skuld.session_runtime import RuntimeDirError

if TYPE_CHECKING:
    from skuld.broker import Broker

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


@dataclass(frozen=True)
class _RecipientState:
    """Forge's view of an ask's recipient: ``ended`` when it can never answer for now, else the
    messages its Skuld reports as accepted and not yet read."""

    ended: _Ended | None
    unread: frozenset[str] = frozenset()


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
                    state = await self._recipient_state(recipient_id, instance_id)
                    if state.ended is not None:
                        return self._ask_ended(recipient_id, state.ended)
                    next_check = time.monotonic() + poll_s
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return await self._ask_deadline(
                        wait, recipient_id, instance_id, message_id, request_id,
                        delivery_confirmed, deadline_s,
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
    ) -> dict[str, Any]:
        """The deadline passed: one last look at the inbox and at Forge, then say which kind."""
        taken = await self._broker.take_peer_answer(wait)
        if taken is not None:
            return self._ask_answered(recipient_id, taken)
        state = await self._recipient_state(recipient_id, instance_id)
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
            outcome, detail = AskOutcome.DELIVERY_UNKNOWN, (
                "Forge did not confirm the message as delivered (the send is still pending or "
                f"unconfirmed), and no answer came within {deadline_s:g}s. Whether the "
                f"recipient has it is unknown, not failed. {again}"
            )  # fmt: skip
        else:
            outcome, detail = AskOutcome.DEADLINE, (
                f"the recipient had not answered within deadline_s={deadline_s:g} "
                f"(ceiling ask.wait_max_s={self._broker._settings.ask.wait_max_s:g}, the longest "
                f"one MCP tool call may run). {again}"
            )  # fmt: skip
        record_cap_hit("ask.wait_max_s", f"ask {request_id} ended {outcome.value}: {detail}")
        return self._ask_ended(recipient_id, _Ended(outcome, detail))

    async def _recipient_state(self, recipient_id: str, instance_id: str | None) -> _RecipientState:
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
            controls = metadata.get("kind")
            return _RecipientState(
                _Ended(
                    AskOutcome.BLOCKED,
                    f"session {recipient_id} is waiting on a person"
                    + (f" ({controls})" if isinstance(controls, str) and controls else "")
                    + " (an approval or a question) and cannot read or answer until they respond. "
                    "Tell the user, or call ask again with the same request_id later.",
                )
            )
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

    async def send_mesh_message(
        self, peer: str, content: str, *, expect_reply: bool, request_id: str, in_reply_to: str
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
        failure = answer.get("error") or (
            answer.get("status") if answer.get("status") == "error" else None
        )
        if failure:
            raise ForgeApiError(f"mesh peer {peer!r} refused the message: {failure}", status=502)
        return {"message_id": message.message_id, "status": answer.get("status") or "sent"}

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
