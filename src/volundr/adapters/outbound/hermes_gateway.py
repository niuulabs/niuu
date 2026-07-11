"""Hermes TUI Gateway adapter for resident sessions and shared chat."""

from __future__ import annotations

import asyncio
import json
import secrets
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import websockets

from niuu.domain.models import SecretType
from niuu.ports.credentials import CredentialStorePort
from niuu.ports.session_proxy import SessionProxyTarget
from volundr.domain.models import (
    ResidentEngine,
    ResidentRuntime,
    ResidentSession,
    ResidentSessionStatus,
)
from volundr.domain.ports import ResidentChatConnection, ResidentSessionController

HERMES_CREDENTIAL_NAME = "hermes-dashboard"
_TOKEN_FIELD = "session_token"
PENDING_SESSION_TTL_SECONDS = 300
NIUU_MODEL_PREFIX = "niuu/"


class HermesGatewayError(RuntimeError):
    """Hermes JSON-RPC or transport failure."""

    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


async def ensure_hermes_dashboard_token(
    store: CredentialStorePort,
    runtime: ResidentRuntime,
) -> str:
    """Return the dashboard token shared by Volundr and one Hermes resident."""
    owner_id = str(runtime.id)
    existing = await store.get_value("resident", owner_id, HERMES_CREDENTIAL_NAME)
    if existing:
        token = existing.get(_TOKEN_FIELD)
        if token:
            return token
        raise RuntimeError("Hermes resident dashboard credential is incomplete")

    token = secrets.token_urlsafe(32)
    await store.store(
        "resident",
        owner_id,
        HERMES_CREDENTIAL_NAME,
        SecretType.API_KEY,
        {_TOKEN_FIELD: token},
        metadata={"purpose": "hermes-dashboard-session"},
    )
    return token


def _gateway_uri(target: SessionProxyTarget, token: str) -> str:
    parsed = urlsplit(target.service_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    base_path = parsed.path.rstrip("/")
    path = f"{base_path}/api/ws" if base_path else "/api/ws"
    return urlunsplit((scheme, parsed.netloc, path, urlencode({"token": token}), ""))


class _HermesConnection:
    def __init__(self, websocket: Any) -> None:
        self._websocket = websocket
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self.events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._reader = asyncio.create_task(self._read_loop())

    @classmethod
    async def connect(
        cls,
        target: SessionProxyTarget,
        token: str,
    ) -> _HermesConnection:
        kwargs: dict[str, Any] = {
            "host": target.connect_host,
            "port": target.connect_port,
            "open_timeout": 15,
            "close_timeout": 5,
            "max_size": 4 * 1024 * 1024,
        }
        if target.connect_secure:
            kwargs["ssl"] = True
        websocket = await websockets.connect(_gateway_uri(target, token), **kwargs)
        return cls(websocket)

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        request_id = str(uuid4())
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._websocket.send(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "method": method,
                        "params": params or {},
                    }
                )
            )
            return await asyncio.wait_for(future, timeout=30)
        finally:
            self._pending.pop(request_id, None)

    async def _read_loop(self) -> None:
        try:
            async for raw in self._websocket:
                frame = json.loads(raw)
                if frame.get("method") == "event":
                    params = frame.get("params")
                    if isinstance(params, dict):
                        await self.events.put(params)
                    continue
                request_id = str(frame.get("id") or "")
                future = self._pending.get(request_id)
                if future is None or future.done():
                    continue
                error = frame.get("error")
                if isinstance(error, dict):
                    future.set_exception(
                        HermesGatewayError(
                            str(error.get("message") or "Hermes Gateway request failed"),
                            code=error.get("code") if isinstance(error.get("code"), int) else None,
                        )
                    )
                    continue
                future.set_result(frame.get("result"))
        except Exception as exc:
            if not self._websocket.close_code:
                await self.events.put({"type": "connection.closed", "payload": {"error": str(exc)}})
        finally:
            error = HermesGatewayError("Hermes Gateway connection closed")
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(error)
            await self.events.put({"type": "connection.closed", "payload": {}})

    async def close(self) -> None:
        self._reader.cancel()
        with suppress(asyncio.CancelledError):
            await self._reader
        await self._websocket.close()


def _session_uuid(key: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"hermes-resident-session:{key}")


def _hermes_model_id(model: str) -> str:
    """Translate Niuu's virtual provider namespace to the Bifrost model ID."""
    return model.removeprefix(NIUU_MODEL_PREFIX)


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, (int, float)):
        return None
    return datetime.fromtimestamp(value, UTC)


def _history_turns(messages: Any) -> list[dict[str, Any]]:
    if not isinstance(messages, list):
        return []
    turns: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict) or message.get("role") not in {"user", "assistant"}:
            continue
        text = message.get("text", message.get("content", ""))
        if not isinstance(text, str):
            text = json.dumps(text, ensure_ascii=False)
        created_at = _timestamp(message.get("timestamp")) or datetime.now(UTC)
        turns.append(
            {
                "id": str(message.get("id") or f"history-{index}"),
                "role": message["role"],
                "content": text,
                "created_at": created_at.isoformat(),
            }
        )
    return turns


def _approval_request_id(live_session_id: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return str(uuid5(NAMESPACE_URL, f"hermes-approval:{live_session_id}:{canonical}"))


@dataclass
class _PendingHermesSession:
    connection: _HermesConnection
    live_session_id: str
    session: ResidentSession
    expiry: asyncio.Task[None]


class HermesChatConnection(ResidentChatConnection):
    """Translate one resumed Hermes live session to shared chat frames."""

    def __init__(
        self,
        gateway: _HermesConnection,
        live_session_id: str,
        history: list[dict[str, Any]],
        model: str,
    ) -> None:
        self._gateway = gateway
        self._live_session_id = live_session_id
        self._model = model
        self._initial: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._initial.put_nowait(
            {
                "type": "capabilities",
                "interrupt": True,
                "steer": True,
                "set_model": False,
                "set_thinking_tokens": False,
            }
        )
        self._initial.put_nowait({"type": "conversation_history", "turns": history})
        self._message_started = False
        self._approval_requests: set[str] = set()
        self._input_requests: set[str] = set()

    async def receive(self) -> dict[str, Any]:
        while True:
            if not self._initial.empty():
                return await self._initial.get()
            local_frame = asyncio.create_task(self._initial.get())
            gateway_frame = asyncio.create_task(self._gateway.events.get())
            done, pending = await asyncio.wait(
                (local_frame, gateway_frame), return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            for task in pending:
                with suppress(asyncio.CancelledError):
                    await task
            if local_frame in done:
                return local_frame.result()

            event = gateway_frame.result()
            event_type = str(event.get("type") or "")
            if event_type == "connection.closed":
                raise HermesGatewayError("Hermes Gateway connection closed")
            if event.get("session_id") != self._live_session_id:
                continue
            payload = event.get("payload")
            if not isinstance(payload, dict):
                payload = {}
            normalized = self._normalize_event(event_type, payload)
            if normalized is not None:
                return normalized

    def _normalize_event(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        if event_type == "message.start":
            self._message_started = True
            return {
                "type": "assistant",
                "message": {"role": "assistant", "model": self._model, "content": []},
            }
        if event_type == "message.delta":
            if not self._message_started:
                self._message_started = True
                self._initial.put_nowait(
                    {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": str(payload.get("text") or "")},
                    }
                )
                return {
                    "type": "assistant",
                    "message": {"role": "assistant", "model": self._model, "content": []},
                }
            return {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": str(payload.get("text") or "")},
            }
        if event_type == "message.complete":
            text = str(payload.get("text") or "")
            if not self._message_started:
                if text:
                    self._initial.put_nowait(
                        {
                            "type": "content_block_delta",
                            "delta": {"type": "text_delta", "text": text},
                        }
                    )
                self._initial.put_nowait({"type": "result", "result": text})
                return {
                    "type": "assistant",
                    "message": {"role": "assistant", "model": self._model, "content": []},
                }
            self._message_started = False
            if payload.get("status") == "error":
                return {"type": "error", "error": text or "Hermes turn failed"}
            return {"type": "result", "result": text}
        if event_type == "tool.start":
            name = str(payload.get("name") or "tool")
            tool_input = payload.get("args") or payload.get("context") or {}
            return {
                "type": "tool_start",
                "data": name,
                "metadata": {
                    "tool_name": name,
                    "tool_id": str(payload.get("tool_id") or ""),
                    "input": tool_input,
                },
            }
        if event_type == "tool.complete":
            return {
                "type": "tool_result",
                "data": payload.get("result", payload.get("result_text", "")),
                "metadata": {
                    "tool_name": str(payload.get("name") or "tool"),
                    "tool_id": str(payload.get("tool_id") or ""),
                    "is_error": bool(payload.get("is_error", False)),
                },
            }
        if event_type == "clarify.request":
            request_id = str(payload.get("request_id") or "")
            if not request_id:
                return None
            self._input_requests.add(request_id)
            return {
                "type": "ask_user_question",
                "request_id": request_id,
                "questions": [
                    {
                        "header": "Clarification",
                        "question": str(payload.get("question") or ""),
                        "options": [
                            {"label": str(choice)}
                            for choice in payload.get("choices", [])
                            if isinstance(choice, str) and choice
                        ],
                        "multiSelect": False,
                    }
                ],
            }
        if event_type == "approval.request":
            request_id = _approval_request_id(self._live_session_id, payload)
            self._approval_requests.add(request_id)
            return {
                "type": "control_request",
                "request_id": request_id,
                "tool": "terminal",
                "input": {
                    "command": str(payload.get("command") or ""),
                    "description": str(payload.get("description") or ""),
                    "allow_permanent": bool(payload.get("allow_permanent", False)),
                },
            }
        if event_type == "error":
            return {"type": "error", "error": str(payload.get("message") or "Hermes error")}
        return None

    async def send(self, frame: dict[str, Any]) -> None:
        frame_type = frame.get("type")
        if frame_type == "interrupt":
            await self._gateway.request("session.interrupt", {"session_id": self._live_session_id})
            return
        if frame_type in {"steer", "steer_active_turn"}:
            await self._gateway.request(
                "session.steer",
                {
                    "session_id": self._live_session_id,
                    "text": str(frame.get("content") or ""),
                },
            )
            return
        if frame_type == "permission_response":
            await self._send_permission_response(frame)
            return
        if frame_type == "ask_user_answer":
            await self._send_input_response(frame)
            return
        if frame_type != "user":
            raise HermesGatewayError(f"Unsupported shared chat command: {frame_type}")

        content = frame.get("content")
        text = _frame_text(content)
        for attachment in _image_attachments(content):
            await self._gateway.request(
                "image.attach_bytes",
                {"session_id": self._live_session_id, **attachment},
            )
        request_id = str(frame.get("request_id") or uuid4())
        await self._gateway.request(
            "prompt.submit",
            {"session_id": self._live_session_id, "text": text},
        )
        self._initial.put_nowait(
            {
                "type": "user_confirmed",
                "id": request_id,
                "request_id": request_id,
                "content": text,
                "created_at": datetime.now(UTC).isoformat(),
            }
        )

    async def _send_permission_response(self, frame: dict[str, Any]) -> None:
        request_id = str(frame.get("request_id") or "")
        if request_id not in self._approval_requests:
            raise HermesGatewayError("Unknown Hermes approval request")
        choices = {
            "allow": "once",
            "allowOnce": "once",
            "allowForever": "always",
            "deny": "deny",
        }
        behavior = str(frame.get("behavior") or "deny")
        choice = choices.get(behavior)
        if choice is None:
            raise HermesGatewayError(f"Unsupported permission behavior: {behavior}")
        await self._gateway.request(
            "approval.respond",
            {"session_id": self._live_session_id, "choice": choice},
        )
        self._approval_requests.discard(request_id)
        self._initial.put_nowait(
            {
                "type": "permission_resolved",
                "request_id": request_id,
                "behavior": behavior,
                "auto_approved": False,
            }
        )

    async def _send_input_response(self, frame: dict[str, Any]) -> None:
        request_id = str(frame.get("request_id") or "")
        if request_id not in self._input_requests:
            raise HermesGatewayError("Unknown Hermes input request")
        await self._gateway.request(
            "clarify.respond",
            {
                "session_id": self._live_session_id,
                "request_id": request_id,
                "answer": _answer_text(frame.get("answers")),
            },
        )
        self._input_requests.discard(request_id)
        self._initial.put_nowait({"type": "ask_user_resolved", "request_id": request_id})

    async def close(self) -> None:
        await self._gateway.close()


def _frame_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content or "")
    return "".join(
        str(part.get("text") or "")
        for part in content
        if isinstance(part, dict) and part.get("type") == "text"
    )


def _image_attachments(content: Any) -> list[dict[str, str]]:
    if not isinstance(content, list):
        return []
    attachments: list[dict[str, str]] = []
    for index, part in enumerate(content):
        if not isinstance(part, dict) or part.get("type") != "image":
            continue
        source = part.get("source")
        if not isinstance(source, dict) or source.get("type") != "base64":
            continue
        data = source.get("data")
        if not isinstance(data, str) or not data:
            continue
        media_type = str(source.get("media_type") or "image/png")
        extension = media_type.removeprefix("image/").replace("jpeg", "jpg")
        attachments.append(
            {
                "content_base64": data,
                "filename": f"attachment-{index + 1}.{extension}",
            }
        )
    return attachments


def _answer_text(answers: Any) -> str:
    if not isinstance(answers, list) or not answers:
        return ""
    answer = answers[0]
    if isinstance(answer, dict):
        answer = answer.get("answer", "")
    if isinstance(answer, list):
        return ", ".join(str(value) for value in answer)
    return str(answer or "")


class HermesResidentSessionController(ResidentSessionController):
    """Hermes implementation of resident-native lifecycle and shared chat."""

    def __init__(
        self,
        runtime_controller: Any,
        credential_store: CredentialStorePort,
    ) -> None:
        self._routes = runtime_controller
        self._credentials = credential_store
        self._session_keys: dict[UUID, str] = {}
        self._pending_sessions: dict[UUID, _PendingHermesSession] = {}

    @property
    def engine(self) -> ResidentEngine:
        return ResidentEngine.HERMES

    async def _connect(self, runtime: ResidentRuntime) -> _HermesConnection:
        target = self._routes.resident_proxy_target(runtime)
        if target is None:
            raise HermesGatewayError("Hermes resident has no dashboard service route")
        token = await ensure_hermes_dashboard_token(self._credentials, runtime)
        return await _HermesConnection.connect(target, token)

    async def _list_rows(self, connection: _HermesConnection) -> list[dict[str, Any]]:
        payload = await connection.request("session.list", {"limit": 500})
        rows = payload.get("sessions") if isinstance(payload, dict) else []
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    async def _resolve_key(
        self,
        connection: _HermesConnection,
        session_id: UUID,
    ) -> str:
        known = self._session_keys.get(session_id)
        if known:
            return known
        for row in await self._list_rows(connection):
            key = str(row.get("id") or "")
            if key:
                self._session_keys[_session_uuid(key)] = key
        known = self._session_keys.get(session_id)
        if known:
            return known
        raise HermesGatewayError(f"Hermes session {session_id} was not found")

    async def list_sessions(self, runtime: ResidentRuntime) -> list[ResidentSession]:
        connection = await self._connect(runtime)
        try:
            rows = await self._list_rows(connection)
        finally:
            await connection.close()
        sessions: list[ResidentSession] = []
        for row in rows:
            key = str(row.get("id") or "")
            if not key:
                continue
            session_id = _session_uuid(key)
            self._session_keys[session_id] = key
            created_at = _timestamp(row.get("started_at"))
            sessions.append(
                ResidentSession(
                    id=session_id,
                    resident_id=runtime.id,
                    title=str(row.get("title") or row.get("preview") or runtime.name),
                    model=str(row.get("model") or runtime.model),
                    status=ResidentSessionStatus.IDLE,
                    created_at=created_at,
                    updated_at=created_at,
                    message_count=int(row.get("message_count") or 0),
                    chat_endpoint=f"/s/{runtime.id}/sessions/{session_id}/session",
                )
            )
        known_ids = {session.id for session in sessions}
        sessions.extend(
            pending.session
            for pending in self._pending_sessions.values()
            if pending.session.id not in known_ids
        )
        return sessions

    async def create_session(
        self,
        runtime: ResidentRuntime,
        *,
        title: str,
        model: str,
    ) -> ResidentSession:
        connection = await self._connect(runtime)
        try:
            payload = await connection.request(
                "session.create",
                {
                    "title": title or runtime.name,
                    "model": _hermes_model_id(model or runtime.model),
                    "source": "desktop",
                    "close_on_disconnect": False,
                },
            )
            live_session_id = (
                str(payload.get("session_id") or "") if isinstance(payload, dict) else ""
            )
            if not live_session_id:
                raise HermesGatewayError("Hermes session.create returned no live session id")
            await connection.request(
                "session.title",
                {"session_id": live_session_id, "title": title or runtime.name},
            )
        except Exception:
            await connection.close()
            raise
        key = str(payload.get("stored_session_id") or "") if isinstance(payload, dict) else ""
        if not key:
            await connection.close()
            raise HermesGatewayError("Hermes session.create returned no persistent session key")
        session_id = _session_uuid(key)
        self._session_keys[session_id] = key
        now = datetime.now(UTC)
        session = ResidentSession(
            id=session_id,
            resident_id=runtime.id,
            title=title or runtime.name,
            model=model or runtime.model,
            created_at=now,
            updated_at=now,
            chat_endpoint=f"/s/{runtime.id}/sessions/{session_id}/session",
        )
        expiry = asyncio.create_task(self._expire_pending_session(session_id))
        self._pending_sessions[session_id] = _PendingHermesSession(
            connection=connection,
            live_session_id=live_session_id,
            session=session,
            expiry=expiry,
        )
        return session

    async def _expire_pending_session(self, session_id: UUID) -> None:
        try:
            await asyncio.sleep(PENDING_SESSION_TTL_SECONDS)
            pending = self._pending_sessions.pop(session_id, None)
            if pending is not None:
                with suppress(Exception):
                    await pending.connection.request(
                        "session.close", {"session_id": pending.live_session_id}
                    )
                await pending.connection.close()
        except asyncio.CancelledError:
            return

    async def delete_session(self, runtime: ResidentRuntime, session_id: UUID) -> None:
        pending = self._pending_sessions.pop(session_id, None)
        if pending is not None:
            pending.expiry.cancel()
            try:
                await pending.connection.request(
                    "session.close", {"session_id": pending.live_session_id}
                )
            finally:
                await pending.connection.close()
            self._session_keys.pop(session_id, None)
            return
        connection = await self._connect(runtime)
        try:
            key = await self._resolve_key(connection, session_id)
            await connection.request("session.delete", {"session_id": key})
        finally:
            await connection.close()
        self._session_keys.pop(session_id, None)

    async def connect_chat(
        self,
        runtime: ResidentRuntime,
        session_id: UUID,
    ) -> ResidentChatConnection:
        pending = self._pending_sessions.pop(session_id, None)
        if pending is not None:
            pending.expiry.cancel()
            connection = pending.connection
            live_session_id = pending.live_session_id
            payload: dict[str, Any] = {"info": {"model": pending.session.model}}
        else:
            connection = await self._connect(runtime)
        try:
            if pending is None:
                key = await self._resolve_key(connection, session_id)
                payload = await connection.request(
                    "session.resume",
                    {
                        "session_id": key,
                        "source": "desktop",
                        "close_on_disconnect": False,
                    },
                )
                if not isinstance(payload, dict) or not payload.get("session_id"):
                    raise HermesGatewayError("Hermes session.resume returned no live session id")
                live_session_id = str(payload["session_id"])
            history = await connection.request("session.history", {"session_id": live_session_id})
        except Exception:
            await connection.close()
            raise
        return HermesChatConnection(
            connection,
            live_session_id,
            _history_turns(history.get("messages") if isinstance(history, dict) else None),
            str((payload.get("info") or {}).get("model") or runtime.model),
        )
