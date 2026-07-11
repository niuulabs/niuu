"""Hermes API-server adapter for resident sessions and shared chat."""

from __future__ import annotations

import asyncio
import json
import secrets
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx

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

HERMES_CREDENTIAL_NAME = "hermes-api-server"
HERMES_LEGACY_CREDENTIAL_NAME = "hermes-dashboard"
_TOKEN_FIELD = "api_key"
_LEGACY_TOKEN_FIELD = "session_token"
HERMES_REQUEST_TIMEOUT_SECONDS = 30
HERMES_SESSION_PAGE_SIZE = 200
NIUU_MODEL_PREFIX = "niuu/"
VOLUNDR_DELETED_END_REASON = "volundr_deleted"


class HermesGatewayError(RuntimeError):
    """Hermes API or transport failure."""


async def ensure_hermes_api_key(
    store: CredentialStorePort,
    runtime: ResidentRuntime,
) -> str:
    """Return the machine API key shared by Volundr and one Hermes resident."""
    owner_id = str(runtime.id)
    existing = await store.get_value("resident", owner_id, HERMES_CREDENTIAL_NAME)
    if existing:
        token = existing.get(_TOKEN_FIELD)
        if token:
            return token
        raise RuntimeError("Hermes resident API credential is incomplete")

    legacy = await store.get_value("resident", owner_id, HERMES_LEGACY_CREDENTIAL_NAME)
    if legacy and legacy.get(_LEGACY_TOKEN_FIELD):
        token = legacy[_LEGACY_TOKEN_FIELD]
        await store.store(
            "resident",
            owner_id,
            HERMES_CREDENTIAL_NAME,
            SecretType.API_KEY,
            {_TOKEN_FIELD: token},
            metadata={"purpose": "hermes-api-server"},
        )
        await store.delete("resident", owner_id, HERMES_LEGACY_CREDENTIAL_NAME)
        return token

    token = secrets.token_urlsafe(32)
    await store.store(
        "resident",
        owner_id,
        HERMES_CREDENTIAL_NAME,
        SecretType.API_KEY,
        {_TOKEN_FIELD: token},
        metadata={"purpose": "hermes-api-server"},
    )
    return token


def _api_url(target: SessionProxyTarget, path: str) -> str:
    parsed = urlsplit(target.service_url)
    scheme = "https" if target.connect_secure else "http"
    connect_host = target.connect_host
    if ":" in connect_host and not connect_host.startswith("["):
        connect_host = f"[{connect_host}]"
    connect_netloc = f"{connect_host}:{target.connect_port}"
    base_path = parsed.path.rstrip("/")
    full_path = f"{base_path}/{path.lstrip('/')}"
    return urlunsplit((scheme, connect_netloc, full_path, "", ""))


class _HermesAPI:
    """Authenticated client for NVIDIA Hermes' API-server platform."""

    def __init__(self, target: SessionProxyTarget, api_key: str) -> None:
        parsed = urlsplit(target.service_url)
        self._target = target
        self._client = httpx.AsyncClient(
            timeout=HERMES_REQUEST_TIMEOUT_SECONDS,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Host": parsed.netloc,
            },
        )

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = await self._client.request(
            method,
            _api_url(self._target, path),
            params=params,
            json=json_body,
        )
        if response.is_success:
            payload = response.json()
            return payload if isinstance(payload, dict) else {}
        self._raise_response_error(response)

    async def stream_run(self, run_id: str):
        path = f"/v1/runs/{quote(run_id, safe='')}/events"
        async with self._client.stream("GET", _api_url(self._target, path)) as response:
            if not response.is_success:
                await response.aread()
                self._raise_response_error(response)
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    event = json.loads(line.removeprefix("data:").strip())
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    yield event

    @staticmethod
    def _raise_response_error(response: httpx.Response) -> None:
        try:
            payload = response.json()
        except ValueError:
            payload = None
        detail: Any = response.text
        if isinstance(payload, dict):
            error = payload.get("error")
            detail = error.get("message") if isinstance(error, dict) else payload.get("detail")
        raise HermesGatewayError(
            "Hermes API request failed "
            f"({response.status_code}): {detail or response.reason_phrase}"
        )

    async def close(self) -> None:
        await self._client.aclose()


def _session_uuid(key: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"hermes-resident-session:{key}")


def normalize_hermes_model_id(model: str) -> str:
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


def _approval_request_id(run_id: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return str(uuid5(NAMESPACE_URL, f"hermes-approval:{run_id}:{canonical}"))


class HermesChatConnection(ResidentChatConnection):
    """Translate Hermes API-server runs and SSE events to shared chat frames."""

    def __init__(
        self,
        api: _HermesAPI,
        session_key: str,
        history: list[dict[str, Any]],
        model: str,
    ) -> None:
        self._api = api
        self._session_key = session_key
        self._model = model
        self._events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._events.put_nowait(
            {
                "type": "capabilities",
                "interrupt": True,
                "steer": False,
                "set_model": False,
                "set_thinking_tokens": False,
            }
        )
        self._events.put_nowait({"type": "conversation_history", "turns": history})
        self._message_started = False
        self._active_run_id: str | None = None
        self._run_stream: asyncio.Task[None] | None = None
        self._approval_requests: dict[str, str] = {}

    async def receive(self) -> dict[str, Any]:
        return await self._events.get()

    async def _consume_run(self, run_id: str) -> None:
        terminal = False
        try:
            async for event in self._api.stream_run(run_id):
                terminal = await self._enqueue_event(run_id, event) or terminal
            if terminal:
                return
            status = await self._api.request("GET", f"/v1/runs/{quote(run_id, safe='')}")
            await self._enqueue_run_status(run_id, status)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._events.put({"type": "error", "error": str(exc)})
        finally:
            if self._active_run_id == run_id:
                self._active_run_id = None

    async def _enqueue_run_status(self, run_id: str, status: dict[str, Any]) -> None:
        state = str(status.get("status") or "")
        if state == "completed":
            await self._enqueue_event(
                run_id,
                {
                    "event": "run.completed",
                    "output": status.get("output", ""),
                    "usage": status.get("usage", {}),
                },
            )
            return
        if state == "cancelled":
            await self._enqueue_event(run_id, {"event": "run.cancelled"})
            return
        await self._events.put(
            {
                "type": "error",
                "error": str(status.get("error") or f"Hermes run ended in state {state}"),
            }
        )

    async def _enqueue_event(self, run_id: str, event: dict[str, Any]) -> bool:
        event_type = str(event.get("event") or "")
        if event_type == "message.delta" and not self._message_started:
            self._message_started = True
            await self._events.put(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "model": self._model,
                        "content": [],
                    },
                }
            )
            await self._events.put(
                {
                    "type": "content_block_delta",
                    "delta": {
                        "type": "text_delta",
                        "text": str(event.get("delta") or ""),
                    },
                }
            )
            return False
        if event_type == "run.completed" and not self._message_started:
            text = str(event.get("output") or "")
            await self._events.put(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "model": self._model,
                        "content": [],
                    },
                }
            )
            if text:
                await self._events.put(
                    {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": text},
                    }
                )
            await self._events.put(
                {"type": "result", "result": text, "usage": event.get("usage", {})}
            )
            return True
        normalized = self._normalize_event(run_id, event_type, event)
        if normalized is not None:
            await self._events.put(normalized)
        return event_type in {"run.completed", "run.failed", "run.cancelled"}

    def _normalize_event(
        self,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        if event_type == "message.delta":
            return {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": str(payload.get("delta") or "")},
            }
        if event_type == "run.completed":
            text = str(payload.get("output") or "")
            self._message_started = False
            return {"type": "result", "result": text, "usage": payload.get("usage", {})}
        if event_type == "tool.started":
            name = str(payload.get("tool") or "tool")
            return {
                "type": "tool_start",
                "data": name,
                "metadata": {
                    "tool_name": name,
                    "tool_id": run_id,
                    "input": {"preview": payload.get("preview")},
                },
            }
        if event_type == "tool.completed":
            return {
                "type": "tool_result",
                "data": "",
                "metadata": {
                    "tool_name": str(payload.get("tool") or "tool"),
                    "tool_id": run_id,
                    "is_error": bool(payload.get("error", False)),
                },
            }
        if event_type == "approval.request":
            request_id = _approval_request_id(run_id, payload)
            self._approval_requests[request_id] = run_id
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
        if event_type == "run.failed":
            self._message_started = False
            return {"type": "error", "error": str(payload.get("error") or "Hermes run failed")}
        if event_type == "run.cancelled":
            self._message_started = False
            return {"type": "result", "result": "", "interrupted": True}
        return None

    async def send(self, frame: dict[str, Any]) -> None:
        frame_type = frame.get("type")
        if frame_type == "interrupt":
            if self._active_run_id:
                await self._api.request(
                    "POST", f"/v1/runs/{quote(self._active_run_id, safe='')}/stop"
                )
            return
        if frame_type in {"steer", "steer_active_turn"}:
            raise HermesGatewayError("Hermes API server does not support steering active runs")
        if frame_type == "permission_response":
            await self._send_permission_response(frame)
            return
        if frame_type != "user":
            raise HermesGatewayError(f"Unsupported shared chat command: {frame_type}")
        if self._active_run_id:
            raise HermesGatewayError("Hermes already has an active run for this chat")

        content = frame.get("content")
        text = _frame_text(content)
        if _image_attachments(content):
            raise HermesGatewayError("Hermes run API does not support image attachments")
        request_id = str(frame.get("request_id") or uuid4())
        payload = await self._api.request(
            "POST",
            "/v1/runs",
            json_body={
                "input": text,
                "session_id": self._session_key,
                "model": normalize_hermes_model_id(self._model),
            },
        )
        run_id = str(payload.get("run_id") or "")
        if not run_id:
            raise HermesGatewayError("Hermes run API returned no run id")
        self._active_run_id = run_id
        self._run_stream = asyncio.create_task(self._consume_run(run_id))
        self._events.put_nowait(
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
        run_id = self._approval_requests.get(request_id)
        if not run_id:
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
        await self._api.request(
            "POST",
            f"/v1/runs/{quote(run_id, safe='')}/approval",
            json_body={"choice": choice},
        )
        self._approval_requests.pop(request_id, None)
        self._events.put_nowait(
            {
                "type": "permission_resolved",
                "request_id": request_id,
                "behavior": behavior,
                "auto_approved": False,
            }
        )

    async def close(self) -> None:
        if self._run_stream is not None:
            self._run_stream.cancel()
            with suppress(asyncio.CancelledError):
                await self._run_stream
        await self._api.close()


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


class HermesResidentSessionController(ResidentSessionController):
    """Hermes API-server implementation of resident sessions and shared chat."""

    def __init__(
        self,
        runtime_controller: Any,
        credential_store: CredentialStorePort,
    ) -> None:
        self._routes = runtime_controller
        self._credentials = credential_store
        self._session_keys: dict[UUID, str] = {}

    @property
    def engine(self) -> ResidentEngine:
        return ResidentEngine.HERMES

    async def _connect(self, runtime: ResidentRuntime) -> _HermesAPI:
        target = self._routes.resident_proxy_target(runtime)
        if target is None:
            raise HermesGatewayError("Hermes resident has no API service route")
        api_key = await ensure_hermes_api_key(self._credentials, runtime)
        return _HermesAPI(target, api_key)

    async def _list_rows(self, api: _HermesAPI) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            payload = await api.request(
                "GET",
                "/api/sessions",
                params={"limit": HERMES_SESSION_PAGE_SIZE, "offset": offset},
            )
            page = payload.get("data")
            if not isinstance(page, list):
                return rows
            rows.extend(row for row in page if isinstance(row, dict))
            if not payload.get("has_more") or not page:
                return rows
            offset += len(page)

    async def _resolve_key(
        self,
        api: _HermesAPI,
        session_id: UUID,
    ) -> str:
        known = self._session_keys.get(session_id)
        if known:
            return known
        for row in await self._list_rows(api):
            key = str(row.get("id") or "")
            if key:
                self._session_keys[_session_uuid(key)] = key
        known = self._session_keys.get(session_id)
        if known:
            return known
        raise HermesGatewayError(f"Hermes session {session_id} was not found")

    async def list_sessions(self, runtime: ResidentRuntime) -> list[ResidentSession]:
        api = await self._connect(runtime)
        try:
            rows = await self._list_rows(api)
        finally:
            await api.close()
        sessions: list[ResidentSession] = []
        for row in rows:
            if row.get("end_reason") == VOLUNDR_DELETED_END_REASON:
                continue
            key = str(row.get("id") or "")
            if not key:
                continue
            session_id = _session_uuid(key)
            self._session_keys[session_id] = key
            created_at = _timestamp(row.get("started_at"))
            stored_model = str(row.get("model") or runtime.model)
            model = (
                runtime.model
                if normalize_hermes_model_id(runtime.model) == stored_model
                else stored_model
            )
            sessions.append(
                ResidentSession(
                    id=session_id,
                    resident_id=runtime.id,
                    title=str(row.get("title") or row.get("preview") or runtime.name),
                    model=model,
                    status=ResidentSessionStatus.IDLE,
                    created_at=created_at,
                    updated_at=created_at,
                    message_count=int(row.get("message_count") or 0),
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
        if model and model != runtime.model:
            raise HermesGatewayError(
                "Hermes API sessions use the resident model; "
                f"requested {model!r}, resident uses {runtime.model!r}"
            )
        api = await self._connect(runtime)
        try:
            payload = await api.request(
                "POST",
                "/api/sessions",
                json_body={
                    "title": title or runtime.name,
                    "model": normalize_hermes_model_id(runtime.model),
                },
            )
        finally:
            await api.close()
        session_payload = payload.get("session")
        key = str(session_payload.get("id") or "") if isinstance(session_payload, dict) else ""
        if not key:
            raise HermesGatewayError("Hermes session API returned no persistent session id")
        session_id = _session_uuid(key)
        self._session_keys[session_id] = key
        now = datetime.now(UTC)
        return ResidentSession(
            id=session_id,
            resident_id=runtime.id,
            title=title or runtime.name,
            model=model or runtime.model,
            created_at=now,
            updated_at=now,
            chat_endpoint=f"/s/{runtime.id}/sessions/{session_id}/session",
        )

    async def delete_session(self, runtime: ResidentRuntime, session_id: UUID) -> None:
        api = await self._connect(runtime)
        try:
            key = await self._resolve_key(api, session_id)
            await api.request(
                "PATCH",
                f"/api/sessions/{quote(key, safe='')}",
                json_body={"end_reason": VOLUNDR_DELETED_END_REASON},
            )
        finally:
            await api.close()
        self._session_keys.pop(session_id, None)

    async def connect_chat(
        self,
        runtime: ResidentRuntime,
        session_id: UUID,
    ) -> ResidentChatConnection:
        api = await self._connect(runtime)
        try:
            key = await self._resolve_key(api, session_id)
            encoded_key = quote(key, safe="")
            detail = await api.request("GET", f"/api/sessions/{encoded_key}")
            history = await api.request("GET", f"/api/sessions/{encoded_key}/messages")
        except Exception:
            await api.close()
            raise
        session_payload = detail.get("session")
        stored_model = (
            str(session_payload.get("model") or runtime.model)
            if isinstance(session_payload, dict)
            else runtime.model
        )
        resolved_model = (
            runtime.model
            if normalize_hermes_model_id(runtime.model) == stored_model
            else stored_model
        )
        return HermesChatConnection(
            api,
            key,
            _history_turns(history.get("data")),
            resolved_model,
        )
