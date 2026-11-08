"""The broker as the Forge MCP host: its own session's environment and notifications."""

from __future__ import annotations

import asyncio
import logging
import re
import socket
import time
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from niuu.build_identity import build_identity
from niuu.domain.artifact_reports import ArtifactReport, build_report_turn
from niuu.domain.notifications import NotificationDraft, build_notification_turn
from niuu.domain.peer_message import PeerMessage
from niuu.domain.services.forge_session_token import SESSION_ID_CLAIM
from niuu.domain.workflow_step import WorkflowStepCloseArgs
from niuu.forge_mcp.credentials import is_forge_session_claims, token_scopes, unverified_claims
from niuu.forge_mcp.models import (
    SECRET_NAME_MAX_CHARS,
    SECRET_NAME_PATTERN,
    ForgeApiError,
    SecretRequestError,
)
from niuu.forge_mcp.ports import ForgeClient, ForgeMcpHost
from skuld.conversation_models import ConversationTurn
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

    def workflow_step_host(self) -> bool:
        return self._broker._settings.workflow_steps.enabled

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
