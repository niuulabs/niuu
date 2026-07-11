"""Native OpenClaw Gateway adapter for resident sessions and shared chat."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import time
from contextlib import suppress
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import websockets
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from niuu.domain.models import SecretType
from niuu.ports.credentials import CredentialStorePort
from niuu.ports.session_proxy import SessionProxyTarget
from volundr.domain.models import (
    ResidentEngine,
    ResidentRuntime,
    ResidentSession,
    ResidentSessionStatus,
)
from volundr.domain.ports import (
    ResidentChatConnection,
    ResidentSessionController,
)

_CREDENTIAL_NAME = "openclaw-gateway"
_AGENT_ID = "main"
_SESSION_KEY_PREFIX = f"agent:{_AGENT_ID}:niuu-"
_SCOPES = ["operator.read", "operator.write", "operator.admin"]
_MIN_PROTOCOL_VERSION = 3
_MAX_PROTOCOL_VERSION = 4


class OpenClawGatewayError(RuntimeError):
    """Gateway RPC failure with structured OpenClaw details."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _new_machine_credential() -> dict[str, str]:
    private_key = Ed25519PrivateKey.generate()
    public_raw = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    return {
        "gateway_token": secrets.token_urlsafe(48),
        "device_id": hashlib.sha256(public_raw).hexdigest(),
        "public_key": _b64url(public_raw),
        "private_key_pem": private_pem,
        "device_token": "",
    }


async def ensure_openclaw_machine_credential(
    store: CredentialStorePort,
    runtime: ResidentRuntime,
) -> dict[str, str]:
    """Return the OpenBao-backed machine identity for one OpenClaw resident."""
    owner_id = str(runtime.id)
    existing = await store.get_value("resident", owner_id, _CREDENTIAL_NAME)
    if existing:
        required = {"gateway_token", "device_id", "public_key", "private_key_pem"}
        if required.issubset(existing):
            return existing
        raise RuntimeError("OpenClaw resident machine credential is incomplete")
    data = _new_machine_credential()
    await store.store(
        "resident",
        owner_id,
        _CREDENTIAL_NAME,
        SecretType.API_KEY,
        data,
        metadata={"purpose": "openclaw-gateway-device"},
    )
    return data


class _GatewayConnection:
    def __init__(
        self,
        websocket: Any,
        credentials: dict[str, str],
    ) -> None:
        self._websocket = websocket
        self._credentials = credentials
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self.events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._reader: asyncio.Task[None] | None = None

    @classmethod
    async def connect(
        cls,
        target: SessionProxyTarget,
        credentials: dict[str, str],
    ) -> tuple[_GatewayConnection, str]:
        parsed = urlsplit(target.service_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        uri = urlunsplit((scheme, parsed.netloc, parsed.path or "/", "", ""))
        connect_kwargs: dict[str, Any] = {
            "host": target.connect_host,
            "port": target.connect_port,
            "open_timeout": 15,
            "close_timeout": 5,
            "max_size": 4 * 1024 * 1024,
        }
        if target.connect_secure:
            connect_kwargs["ssl"] = True
        websocket = await websockets.connect(uri, **connect_kwargs)
        connection = cls(websocket, credentials)
        try:
            raw_challenge = json.loads(await asyncio.wait_for(websocket.recv(), timeout=10))
            if raw_challenge.get("event") != "connect.challenge":
                raise OpenClawGatewayError("OpenClaw Gateway did not send a connect challenge")
            nonce = str((raw_challenge.get("payload") or {}).get("nonce") or "")
            if not nonce:
                raise OpenClawGatewayError("OpenClaw Gateway challenge has no nonce")
            hello = await connection._connect_request(nonce)
        except Exception:
            await websocket.close()
            raise
        connection._reader = asyncio.create_task(connection._read_loop())
        device_token = str((hello.get("auth") or {}).get("deviceToken") or "")
        return connection, device_token

    async def _connect_request(self, nonce: str) -> dict[str, Any]:
        request_id = str(uuid4())
        signed_at = int(time.time() * 1000)
        auth_token = self._credentials.get("device_token") or self._credentials["gateway_token"]
        payload = "|".join(
            (
                "v3",
                self._credentials["device_id"],
                "gateway-client",
                "backend",
                "operator",
                ",".join(_SCOPES),
                str(signed_at),
                auth_token,
                nonce,
                "linux",
                "server",
            )
        )
        private_key = serialization.load_pem_private_key(
            self._credentials["private_key_pem"].encode("ascii"),
            password=None,
        )
        if not isinstance(private_key, Ed25519PrivateKey):
            raise OpenClawGatewayError("OpenClaw device identity is not Ed25519")
        frame = {
            "type": "req",
            "id": request_id,
            "method": "connect",
            "params": {
                "minProtocol": _MIN_PROTOCOL_VERSION,
                "maxProtocol": _MAX_PROTOCOL_VERSION,
                "client": {
                    "id": "gateway-client",
                    "displayName": "Volundr",
                    "version": "1",
                    "platform": "linux",
                    "deviceFamily": "server",
                    "mode": "backend",
                },
                "caps": [],
                "role": "operator",
                "scopes": _SCOPES,
                "auth": {"token": auth_token},
                "device": {
                    "id": self._credentials["device_id"],
                    "publicKey": self._credentials["public_key"],
                    "signature": _b64url(private_key.sign(payload.encode("utf-8"))),
                    "signedAt": signed_at,
                    "nonce": nonce,
                },
            },
        }
        await self._websocket.send(json.dumps(frame))
        response = json.loads(await asyncio.wait_for(self._websocket.recv(), timeout=15))
        if response.get("type") != "res" or response.get("id") != request_id:
            raise OpenClawGatewayError("OpenClaw Gateway returned an invalid connect response")
        if not response.get("ok"):
            error = response.get("error") or {}
            raise OpenClawGatewayError(
                str(error.get("message") or "OpenClaw Gateway authentication failed"),
                error.get("details") if isinstance(error.get("details"), dict) else {},
            )
        return response.get("payload") or {}

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        if self._reader is None:
            raise OpenClawGatewayError("OpenClaw Gateway connection is not ready")
        request_id = str(uuid4())
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self._websocket.send(
            json.dumps({"type": "req", "id": request_id, "method": method, "params": params or {}})
        )
        try:
            return await asyncio.wait_for(future, timeout=30)
        finally:
            self._pending.pop(request_id, None)

    async def _read_loop(self) -> None:
        try:
            async for raw in self._websocket:
                frame = json.loads(raw)
                if frame.get("type") == "res":
                    future = self._pending.get(str(frame.get("id") or ""))
                    if future and not future.done():
                        if frame.get("ok"):
                            future.set_result(frame.get("payload"))
                        else:
                            error = frame.get("error") or {}
                            future.set_exception(
                                OpenClawGatewayError(
                                    str(error.get("message") or "OpenClaw Gateway request failed"),
                                    error.get("details")
                                    if isinstance(error.get("details"), dict)
                                    else {},
                                )
                            )
                elif frame.get("type") == "event":
                    await self.events.put(frame)
        finally:
            error = OpenClawGatewayError("OpenClaw Gateway connection closed")
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(error)
            await self.events.put({"type": "event", "event": "connection.closed", "payload": {}})

    async def close(self) -> None:
        if self._reader:
            self._reader.cancel()
            with suppress(asyncio.CancelledError):
                await self._reader
        await self._websocket.close()


def _session_uuid(key: str) -> UUID | None:
    if not key.startswith(_SESSION_KEY_PREFIX):
        return None
    try:
        return UUID(key.removeprefix(_SESSION_KEY_PREFIX))
    except ValueError:
        return None


def _session_key(session_id: UUID) -> str:
    return f"{_SESSION_KEY_PREFIX}{session_id}"


def _message_text(message: Any) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        str(part.get("text") or "")
        for part in content
        if isinstance(part, dict) and part.get("type") in {"text", "output_text"}
    )


def _history_turns(payload: Any) -> list[dict[str, Any]]:
    messages = payload.get("messages") if isinstance(payload, dict) else []
    if not isinstance(messages, list):
        return []
    turns: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict) or message.get("role") not in {"user", "assistant"}:
            continue
        timestamp = message.get("timestamp") or message.get("createdAt")
        created_at = datetime.now(UTC)
        if isinstance(timestamp, (int, float)):
            created_at = datetime.fromtimestamp(timestamp / 1000, UTC)
        turns.append(
            {
                "id": str(message.get("id") or f"history-{index}"),
                "role": message["role"],
                "content": _message_text(message),
                "created_at": created_at.isoformat(),
            }
        )
    return turns


class OpenClawChatConnection(ResidentChatConnection):
    """Translate OpenClaw Gateway events to the existing shared chat contract."""

    def __init__(
        self,
        gateway: _GatewayConnection,
        session_key: str,
        history: list[dict[str, Any]],
        model: str,
    ) -> None:
        self._gateway = gateway
        self._session_key = session_key
        self._model = model
        self._initial: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._initial.put_nowait(
            {
                "type": "capabilities",
                "interrupt": True,
                "set_model": False,
                "set_thinking_tokens": False,
            }
        )
        self._initial.put_nowait({"type": "conversation_history", "turns": history})
        self._active_runs: set[str] = set()

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
            frame = gateway_frame.result()
            event = str(frame.get("event") or "")
            if event == "connection.closed":
                raise OpenClawGatewayError("OpenClaw Gateway connection closed")
            payload = frame.get("payload") or {}
            if not isinstance(payload, dict) or payload.get("sessionKey") != self._session_key:
                continue
            if event == "chat":
                run_id = str(payload.get("runId") or "")
                state = payload.get("state")
                if state == "delta":
                    if run_id not in self._active_runs:
                        self._active_runs.add(run_id)
                        self._initial.put_nowait(
                            {
                                "type": "content_block_delta",
                                "delta": {
                                    "type": "text_delta",
                                    "text": payload.get("deltaText") or "",
                                },
                            }
                        )
                        return {
                            "type": "assistant",
                            "message": {"role": "assistant", "model": self._model, "content": []},
                        }
                    return {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": payload.get("deltaText") or ""},
                    }
                if state == "final":
                    if run_id not in self._active_runs:
                        text = _message_text(payload.get("message"))
                        self._active_runs.add(run_id)
                        if text:
                            self._initial.put_nowait(
                                {
                                    "type": "content_block_delta",
                                    "delta": {"type": "text_delta", "text": text},
                                }
                            )
                        self._initial.put_nowait({"type": "result", "result": ""})
                        return {
                            "type": "assistant",
                            "message": {"role": "assistant", "model": self._model, "content": []},
                        }
                    self._active_runs.discard(run_id)
                    return {"type": "result", "result": ""}
                if state in {"error", "aborted"}:
                    self._active_runs.discard(run_id)
                    return {
                        "type": "error",
                        "error": payload.get("errorMessage") or state,
                    }

    async def send(self, frame: dict[str, Any]) -> None:
        frame_type = frame.get("type")
        if frame_type == "interrupt":
            await self._gateway.request("sessions.abort", {"key": self._session_key})
            return
        if frame_type != "user":
            raise OpenClawGatewayError(f"Unsupported shared chat command: {frame_type}")
        content = frame.get("content")
        attachments: list[dict[str, str]] = []
        if isinstance(content, list):
            text = "".join(
                str(part.get("text") or "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
            for index, part in enumerate(content):
                if not isinstance(part, dict) or part.get("type") != "image":
                    continue
                source = part.get("source")
                if not isinstance(source, dict):
                    continue
                attachments.append(
                    {
                        "mimeType": str(source.get("media_type") or "image/png"),
                        "fileName": f"attachment-{index + 1}",
                        "content": str(source.get("data") or ""),
                    }
                )
        else:
            text = str(content or "")
        request_id = str(frame.get("request_id") or uuid4())
        await self._gateway.request(
            "sessions.steer" if self._active_runs else "sessions.send",
            {
                "key": self._session_key,
                "message": text,
                "idempotencyKey": request_id,
                **({"attachments": attachments} if attachments else {}),
            },
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

    async def close(self) -> None:
        with suppress(Exception):
            await self._gateway.request("sessions.messages.unsubscribe", {"key": self._session_key})
        await self._gateway.close()


class OpenClawResidentSessionController(ResidentSessionController):
    """OpenClaw implementation of resident-native session lifecycle and chat."""

    def __init__(self, openshell_controller: Any, credential_store: CredentialStorePort) -> None:
        self._openshell = openshell_controller
        self._credentials = credential_store

    @property
    def engine(self) -> ResidentEngine:
        return ResidentEngine.OPENCLAW

    async def _connect(self, runtime: ResidentRuntime) -> _GatewayConnection:
        target = self._openshell.resident_proxy_target(runtime)
        if target is None:
            raise OpenClawGatewayError("OpenClaw resident has no Gateway service route")
        credentials = await ensure_openclaw_machine_credential(self._credentials, runtime)
        try:
            connection, device_token = await _GatewayConnection.connect(target, credentials)
        except OpenClawGatewayError as exc:
            if exc.details.get("code") != "PAIRING_REQUIRED":
                raise
            request_id = str(exc.details.get("requestId") or exc.details.get("request_id") or "")
            if not request_id:
                raise
            await self._openshell.approve_resident_device(
                runtime,
                request_id=request_id,
                gateway_token=credentials["gateway_token"],
            )
            connection, device_token = await _GatewayConnection.connect(target, credentials)
        if device_token and device_token != credentials.get("device_token"):
            credentials = {**credentials, "device_token": device_token}
            await self._credentials.store(
                "resident",
                str(runtime.id),
                _CREDENTIAL_NAME,
                SecretType.API_KEY,
                credentials,
                metadata={"purpose": "openclaw-gateway-device"},
            )
        return connection

    async def list_sessions(self, runtime: ResidentRuntime) -> list[ResidentSession]:
        connection = await self._connect(runtime)
        try:
            payload = await connection.request(
                "sessions.list",
                {"limit": 500, "includeDerivedTitles": True},
            )
        finally:
            await connection.close()
        rows = payload.get("sessions") if isinstance(payload, dict) else []
        sessions: list[ResidentSession] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            session_id = _session_uuid(str(row.get("key") or ""))
            if session_id is None:
                continue
            updated_ms = row.get("updatedAt")
            updated_at = (
                datetime.fromtimestamp(updated_ms / 1000, UTC)
                if isinstance(updated_ms, (int, float))
                else datetime.now(UTC)
            )
            sessions.append(
                ResidentSession(
                    id=session_id,
                    resident_id=runtime.id,
                    title=str(row.get("label") or row.get("derivedTitle") or runtime.name),
                    model=str(row.get("model") or runtime.model),
                    status=(
                        ResidentSessionStatus.RUNNING
                        if row.get("hasActiveRun")
                        else ResidentSessionStatus.FAILED
                        if row.get("status") == "failed"
                        else ResidentSessionStatus.IDLE
                    ),
                    created_at=updated_at,
                    updated_at=updated_at,
                    tokens_used=int(row.get("totalTokens") or 0),
                    cost=Decimal(str(row.get("estimatedCostUsd") or 0)),
                    chat_endpoint=f"/s/{runtime.id}/sessions/{session_id}/session",
                )
            )
        return sessions

    async def create_session(
        self,
        runtime: ResidentRuntime,
        *,
        title: str,
        model: str,
    ) -> ResidentSession:
        session_id = uuid4()
        connection = await self._connect(runtime)
        try:
            agents_payload = await connection.request("agents.list", {})
            agents = agents_payload.get("agents") if isinstance(agents_payload, dict) else []
            if not isinstance(agents, list) or not any(
                isinstance(agent, dict) and str(agent.get("id") or "") == _AGENT_ID
                for agent in agents
            ):
                raise OpenClawGatewayError(
                    f"OpenClaw Gateway does not advertise the {_AGENT_ID!r} resident agent"
                )
            await connection.request(
                "sessions.create",
                {
                    "key": _session_key(session_id),
                    "agentId": _AGENT_ID,
                    "label": title or runtime.name,
                    **({"model": model} if model else {}),
                },
            )
        finally:
            await connection.close()
        now = datetime.now(UTC)
        return ResidentSession(
            id=session_id,
            resident_id=runtime.id,
            title=title or runtime.name,
            model=model,
            created_at=now,
            updated_at=now,
            chat_endpoint=f"/s/{runtime.id}/sessions/{session_id}/session",
        )

    async def delete_session(self, runtime: ResidentRuntime, session_id: UUID) -> None:
        connection = await self._connect(runtime)
        try:
            await connection.request(
                "sessions.delete",
                {"key": _session_key(session_id), "deleteTranscript": True},
            )
        finally:
            await connection.close()

    async def connect_chat(
        self,
        runtime: ResidentRuntime,
        session_id: UUID,
    ) -> ResidentChatConnection:
        connection = await self._connect(runtime)
        key = _session_key(session_id)
        try:
            history = await connection.request(
                "chat.history", {"sessionKey": key, "limit": 1000, "maxChars": 500_000}
            )
            await connection.request("sessions.messages.subscribe", {"key": key})
        except Exception:
            await connection.close()
            raise
        return OpenClawChatConnection(connection, key, _history_turns(history), runtime.model)
