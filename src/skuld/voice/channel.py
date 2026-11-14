"""The session voice channel: the session as the agent service of a realtime voice call.

Rather than teaching the Lexi app about sessions, the session speaks the agent-service
protocol the app already implements (``lexi-agent-service-v1``): a ``{type,id,payload}``
WebSocket at ``/voice`` and REST under ``/voice/api``, on the broker's own authenticated
surface beside the chat WebSocket. The app's realtime model runs on the phone and talks
to the provider directly; the session supplies the voice persona, the call's
instructions grounded in this session, a short-lived provider credential Forge mints
from the owner's key, its tools (which act on this session) and the call record.

Only the session's owner is served: every request must carry the owner in the
proxy-verified user id header (``ws_auth.user_id_header``). ``auth`` is answered
``auth.ok``: the transport already authenticated the caller.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from fastapi import (
    Body,
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse

from niuu.domain.message_author import MessageAuthor
from niuu.domain.session_voice import SESSION_VOICE_PATH, SESSION_VOICE_PROTOCOL
from skuld.voice import protocol as wire
from skuld.voice.binding import UnknownToolError, VoiceCallBinding
from skuld.voice.calls import UnknownCallError, VoiceCallBook
from skuld.voice.minting import ForgeVoiceMinter, VoiceMintError
from skuld.voice.persona import MINT_PROVIDERS, UnsupportedProviderError, VoicePromptComposer
from skuld.websocket_auth import author_from_headers

logger = logging.getLogger("skuld.voice")

#: The one tool profile the app asks for.
VOICE_TOOL_PROFILE = "voice"
#: WebSocket close code for a caller who is not the session's owner.
CLOSE_FORBIDDEN = 4403
_NOT_OWNER = "Only this session's owner may call it"


@dataclass
class _Connection:
    """Per-socket state: the socket, its send lock, who is on it, and its tool allowlist."""

    websocket: WebSocket
    author: MessageAuthor | None
    allowlist: set[str] | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send(self, message: dict[str, Any]) -> bool:
        """Send one frame under the socket's lock; ``False`` when the socket is gone."""
        async with self.lock:
            try:
                await self.websocket.send_json(message)
            except (RuntimeError, WebSocketDisconnect, OSError):
                return False
        return True


class SessionVoiceChannel:
    """The voice channel of one session."""

    def __init__(
        self,
        *,
        binding: VoiceCallBinding,
        composer: VoicePromptComposer,
        calls: VoiceCallBook,
        minter: ForgeVoiceMinter,
        owner_id: str,
        user_id_header: str,
        display_name_header: str,
    ) -> None:
        if not owner_id.strip():
            raise ValueError("The voice channel needs the session's owner: only they are served")
        self._binding = binding
        self._composer = composer
        self._calls = calls
        self._minter = minter
        self._owner_id = owner_id.strip()
        self._user_id_header = user_id_header
        self._display_name_header = display_name_header
        self._connections: set[int] = set()
        self._started = time.monotonic()
        #: Set when the call list could not be restored from the durable log.
        self.history_unavailable: str | None = None

    @property
    def calls(self) -> VoiceCallBook:
        return self._calls

    # -- identity ----------------------------------------------------------

    def caller(self, headers: Mapping[str, str]) -> MessageAuthor | None:
        """The owner, when the verified caller is the owner; ``None`` otherwise."""
        author = author_from_headers(
            headers,
            user_id_header=self._user_id_header,
            display_name_header=self._display_name_header,
        )
        if author is None or author.user_id != self._owner_id:
            return None
        return author

    # -- REST --------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        return {
            "status": "degraded" if self.history_unavailable else "ok",
            "protocol": SESSION_VOICE_PROTOCOL,
            "uptime": int(time.monotonic() - self._started),
            "connections": len(self._connections),
            "sessions": self._calls.active_count(),
            "tools": len(self._binding.tool_names),
            **(
                {"historyUnavailable": self.history_unavailable} if self.history_unavailable else {}
            ),
        }

    def tool_catalog(self, profile: str | None) -> JSONResponse:
        definitions = self._binding.tool_definitions()
        if profile is None:
            return JSONResponse({"success": True, "tools": definitions})
        if profile != VOICE_TOOL_PROFILE:
            return JSONResponse(
                {"success": False, "error": f"Unknown tool profile: {profile}"}, status_code=400
            )
        return JSONResponse(
            {
                "success": True,
                "tools": definitions,
                "profile": VOICE_TOOL_PROFILE,
                "sha": _catalog_sha(definitions),
            }
        )

    async def call_tool_rest(
        self, name: str, body: dict[str, Any], author: MessageAuthor
    ) -> JSONResponse:
        args = body.get("args") if isinstance(body.get("args"), dict) else {}
        call_id = str(body.get("conversationId") or body.get("sessionId") or "")
        try:
            outcome = await self._binding.call_tool(name, args, author=author, call_id=call_id)
        except UnknownToolError:
            return JSONResponse(
                {"success": False, "error": f"Voice tool '{name}' is not registered"},
                status_code=400,
            )
        if outcome.is_error:
            return JSONResponse(
                {"success": True, "result": {"error": outcome.content, "toolName": name}}
            )
        return JSONResponse({"success": True, "result": outcome.content})

    def prompt(self, provider: str, surface: str, conversation_id: str) -> JSONResponse:
        if not conversation_id:
            return JSONResponse(
                {"success": False, "error": "conversationId is required"}, status_code=400
            )
        try:
            prompt = self._composer.prompt(
                provider,
                self._binding,
                surface=surface,
                conversation_id=conversation_id,
            )
        except UnsupportedProviderError as exc:
            logger.info("No voice prompt: %s", str(exc).replace("\n", " "))
            return JSONResponse(
                {
                    "success": False,
                    "error": "This session composes no voice prompt for that provider: it is "
                    "not grok or gpt-realtime, or the persona has no voice for it",
                },
                status_code=404,
            )
        return JSONResponse({"success": True, "prompt": prompt})

    def _require_history(self) -> None:
        if self.history_unavailable:
            raise HTTPException(
                503,
                "This session's call history could not be restored from its event log: "
                f"{self.history_unavailable}. Restart the session to read it again.",
            )

    def call_history(self, limit: int | None) -> dict[str, Any]:
        self._require_history()
        rows = [self._calls.history_json(call) for call in self._calls.calls(limit)]
        return {"calls": rows, "count": len(rows)}

    def logs(self) -> dict[str, Any]:
        self._require_history()
        return {"sessions": [self._calls.session_json(call) for call in self._calls.calls()]}

    def log(self, call_id: str) -> JSONResponse:
        self._require_history()
        try:
            call = self._calls.get(call_id)
        except UnknownCallError:
            return JSONResponse({"error": "Unknown voice call"}, status_code=404)
        entries = [self._calls.entry_json(entry) for entry in call.entries]
        return JSONResponse({"session": self._calls.session_json(call), "entries": entries})

    def patch_log(self, call_id: str, body: dict[str, Any]) -> JSONResponse:
        title = body.get("title")
        pinned = body.get("pinned")
        if title is not None and not isinstance(title, str):
            return JSONResponse({"error": "title must be a string"}, status_code=400)
        if pinned is not None and not isinstance(pinned, bool):
            return JSONResponse({"error": "pinned must be a boolean"}, status_code=400)
        try:
            call = self._calls.update(call_id, title=title, pinned=pinned)
        except UnknownCallError:
            return JSONResponse({"error": "Unknown voice call"}, status_code=404)
        return JSONResponse(
            {"success": True, "sessionId": call_id, "title": call.title, "pinned": call.pinned}
        )

    def delete_log(self, call_id: str) -> JSONResponse:
        try:
            self._calls.delete(call_id)
        except UnknownCallError:
            return JSONResponse({"error": "Unknown voice call"}, status_code=404)
        return JSONResponse({"deleted": True, "sessionId": call_id})

    # -- WebSocket ---------------------------------------------------------

    async def serve(self, websocket: WebSocket) -> None:
        author = self.caller(websocket.headers)
        if author is None:
            await websocket.close(code=CLOSE_FORBIDDEN, reason=_NOT_OWNER)
            return
        await websocket.accept()
        connection = _Connection(websocket=websocket, author=author)
        key = id(connection)
        self._connections.add(key)
        try:
            while True:
                raw = await websocket.receive_text()
                response = await self.answer(connection, raw)
                if response is not None and not await connection.send(response):
                    break
        except WebSocketDisconnect:
            # The app closing its socket is the normal end of a connection.
            pass
        finally:
            self._connections.discard(key)
            with contextlib.suppress(RuntimeError):
                await websocket.close()

    async def answer(self, connection: _Connection, raw: str) -> dict | None:
        try:
            data = json.loads(raw)
        except ValueError:
            return wire.error_frame("", "message is not JSON", wire.ErrorCodes.INVALID_MESSAGE)
        message_id = str(data.get("id") or "") if isinstance(data, dict) else ""
        try:
            message_type, message_id, payload = wire.parse_frame(data)
            return await self._dispatch(connection, message_type, message_id, payload)
        except wire.ProtocolError as exc:
            return wire.error_frame(message_id, str(exc), exc.code)
        except Exception:
            logger.exception("Voice channel failed to answer a message")
            return wire.error_frame(
                message_id,
                "The session's voice channel failed on this message; see the session's log",
                wire.ErrorCodes.INTERNAL_ERROR,
            )

    async def _dispatch(
        self, connection: _Connection, message_type: str, message_id: str, payload: dict[str, Any]
    ) -> dict | None:
        match message_type:
            case wire.AUTH:
                return wire.frame(wire.AUTH_OK, message_id)
            case wire.PING:
                return wire.frame(wire.PONG, message_id)
            case wire.PERSONA_LIST:
                name = self._composer.persona.name
                return wire.frame(
                    wire.PERSONA_LIST_RESULT,
                    message_id,
                    {"defaultPersona": name, "personas": [name]},
                )
            case wire.PERSONA_GET:
                return wire.frame(wire.PERSONA_DATA, message_id, {"context": self._context()})
            case wire.TOOLS_LIST:
                return wire.frame(
                    wire.TOOLS_LIST_RESULT,
                    message_id,
                    {"tools": self._allowed_definitions(connection), "groups": []},
                )
            case wire.TOOLS_ENABLE:
                return self._enable(connection, message_id, payload)
            case wire.TOOLS_CALL:
                return await self._call(connection, message_id, payload)
            case wire.SESSION_START:
                return self._session_start(message_id, payload)
            case wire.SESSION_LOG:
                return self._session_log(message_id, payload)
            case wire.SESSION_END:
                return self._session_end(message_id, payload)
            case wire.VOICE_EPHEMERAL:
                return await self._mint(message_id, payload)
            case wire.GATEWAY_CREDENTIALS:
                return wire.frame(
                    wire.GATEWAY_CREDENTIALS_RESULT,
                    message_id,
                    {"gateways": [], "reason": "A session hands out no gateway credentials."},
                )
            case wire.DICTATION_CREDENTIAL | wire.MEMORY_ENROLLMENT:
                raise wire.ProtocolError(
                    f"{message_type} is not served by a session's voice channel",
                    wire.ErrorCodes.NOT_CONFIGURED,
                )
            case wire.TOOLS_GROUP_TOGGLE:
                raise wire.ProtocolError(
                    "This session's voice tools have no groups", wire.ErrorCodes.INVALID_PARAMS
                )
            case wire.CALL_PRESENCE:
                return wire.frame(
                    wire.CALL_PRESENCE_RESULT,
                    message_id,
                    {"registered": False, "reason": "a session does not place calls"},
                )
            case wire.PUSH_REGISTER:
                # The tokens are dropped unread and never logged: the phone registers them
                # with Forge's push gateway.
                return wire.frame(
                    wire.PUSH_REGISTER_RESULT,
                    message_id,
                    {"registered": False, "reason": "push tokens are registered with Forge"},
                )
        raise wire.ProtocolError(f"Unknown message: {message_type}", wire.ErrorCodes.UNKNOWN_TYPE)

    # -- handlers ----------------------------------------------------------

    def _context(self) -> dict[str, Any]:
        persona = self._composer.persona
        voices = dict(persona.voices)
        return {
            "name": persona.name,
            "displayName": persona.display_name,
            "contextPrompt": self._composer.standing_instructions(self._binding),
            "voice": persona.default_voice(),
            "voices": voices,
            "sources": [f"persona:{persona.name}", *self._binding.sources()],
            "errors": [],
        }

    def _allowed_definitions(self, connection: _Connection) -> list[dict[str, Any]]:
        definitions = self._binding.tool_definitions()
        if connection.allowlist is None:
            return definitions
        return [d for d in definitions if d["name"] in connection.allowlist]

    def _enable(self, connection: _Connection, message_id: str, payload: dict[str, Any]) -> dict:
        names = payload.get("tools", payload.get("allowlist"))
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            raise wire.ProtocolError(
                "tools must be a list of names", wire.ErrorCodes.INVALID_PARAMS
            )
        connection.allowlist = {name for name in names if name in self._binding.tool_names}
        return wire.frame(
            wire.TOOLS_ENABLE_RESULT, message_id, {"enabled": sorted(connection.allowlist)}
        )

    async def _call(
        self, connection: _Connection, message_id: str, payload: dict[str, Any]
    ) -> dict:
        name = str(payload.get("name") or "")
        if not name:
            raise wire.ProtocolError("name is required", wire.ErrorCodes.INVALID_PARAMS)
        args = payload.get("args", payload.get("arguments")) or {}
        if not isinstance(args, dict):
            raise wire.ProtocolError("args must be an object", wire.ErrorCodes.INVALID_PARAMS)
        if connection.allowlist is not None and name not in connection.allowlist:
            return wire.frame(
                wire.TOOLS_CALL_ERROR,
                message_id,
                {"name": name, "error": f"Tool {name} is not enabled on this call"},
            )
        try:
            outcome = await self._binding.call_tool(
                name,
                args,
                author=connection.author,
                call_id=str(payload.get("sessionId") or ""),
            )
        except UnknownToolError:
            return wire.frame(
                wire.TOOLS_CALL_ERROR, message_id, {"name": name, "error": f"Unknown tool: {name}"}
            )
        result: Any = (
            {"error": outcome.content, "toolName": name} if outcome.is_error else outcome.content
        )
        return wire.frame(wire.TOOLS_CALL_RESULT, message_id, {"name": name, "result": result})

    def _session_start(self, message_id: str, payload: dict[str, Any]) -> dict:
        call_id = _required(payload, "sessionId")
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        call = self._calls.start(
            call_id,
            persona=str(payload.get("persona") or self._composer.persona.name),
            provider=str(payload.get("provider") or ""),
            metadata=metadata,
        )
        return wire.frame(
            wire.SESSION_STARTED, message_id, {"session": self._calls.session_json(call)}
        )

    def _session_log(self, message_id: str, payload: dict[str, Any]) -> dict:
        call_id = _required(payload, "sessionId")
        entry_type = _required(payload, "type")
        entry_payload = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        try:
            entry = self._calls.log(call_id, entry_type, entry_payload)
        except UnknownCallError as exc:
            raise wire.ProtocolError(str(exc), wire.ErrorCodes.SESSION_ERROR) from exc
        except ValueError as exc:
            raise wire.ProtocolError(str(exc), wire.ErrorCodes.INVALID_PARAMS) from exc
        return wire.frame(
            wire.SESSION_LOG_ACK, message_id, {"entry": self._calls.entry_json(entry)}
        )

    def _session_end(self, message_id: str, payload: dict[str, Any]) -> dict:
        call_id = _required(payload, "sessionId")
        duration = payload.get("durationMs")
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        summary = payload.get("summary") if isinstance(payload.get("summary"), str) else None
        try:
            call = self._calls.end(
                call_id,
                reason=str(payload.get("reason") or "ended"),
                duration_ms=int(duration) if isinstance(duration, int | float) else None,
                summary=summary,
                metadata=metadata,
            )
        except UnknownCallError as exc:
            raise wire.ProtocolError(str(exc), wire.ErrorCodes.SESSION_ERROR) from exc
        return wire.frame(
            wire.SESSION_ENDED, message_id, {"summary": self._calls.session_json(call)}
        )

    async def _mint(self, message_id: str, payload: dict[str, Any]) -> dict:
        provider = str(payload.get("provider") or "")
        if not provider:
            raise wire.ProtocolError("provider is required", wire.ErrorCodes.INVALID_PARAMS)
        prompt_provider = MINT_PROVIDERS.get(provider)
        if prompt_provider is None:
            raise wire.ProtocolError(
                f"A session's voice channel mints no {provider} credentials; "
                f"use one of {sorted(MINT_PROVIDERS)}",
                wire.ErrorCodes.UNSUPPORTED_PROVIDER,
            )
        persona = self._composer.persona
        try:
            voice = persona.voice_for(prompt_provider)
        except UnsupportedProviderError as exc:
            raise wire.ProtocolError(str(exc), wire.ErrorCodes.UNSUPPORTED_PROVIDER) from exc
        model = str(payload.get("model") or self._composer.model_for(prompt_provider))
        instructions = self._composer.standing_instructions(self._binding)
        try:
            credential = await self._minter.mint(
                provider=provider,
                model=model,
                voice=voice,
                instructions=instructions,
                reasoning=str(payload.get("reasoning") or ""),
            )
        except VoiceMintError as exc:
            logger.error("Voice credential mint failed: %s", exc)
            raise wire.ProtocolError(str(exc), wire.ErrorCodes.PROVIDER_ERROR) from exc
        return wire.frame(
            wire.VOICE_EPHEMERAL_RESULT,
            message_id,
            {
                "success": True,
                "provider": provider,
                "token": credential.token,
                "expiresAt": credential.expires_at_ms,
                "model": credential.model,
                "persona": persona.name,
                "voice": voice,
                "instructions": instructions,
                "personaApplied": credential.session_bound,
            },
        )


def register_voice_routes(app: FastAPI, channel: Callable[[], SessionVoiceChannel | None]) -> None:
    """Mount the channel on the broker app at ``/voice`` (404 while it is off)."""

    def _channel() -> SessionVoiceChannel:
        current = channel()
        if current is None:
            raise HTTPException(404, "This session runs no voice channel")
        return current

    def _owner(request: Request) -> tuple[SessionVoiceChannel, MessageAuthor]:
        current = _channel()
        author = current.caller(request.headers)
        if author is None:
            raise HTTPException(403, _NOT_OWNER)
        return current, author

    base = SESSION_VOICE_PATH

    @app.websocket(base)
    @app.websocket(base + "/")
    async def voice_socket(websocket: WebSocket) -> None:
        current = channel()
        if current is None:
            await websocket.close(code=CLOSE_FORBIDDEN, reason="This session runs no voice channel")
            return
        await current.serve(websocket)

    @app.get(base + "/health")
    async def voice_health(owner: Any = Depends(_owner)) -> dict[str, Any]:
        return owner[0].health()

    @app.get(base + "/api/tools")
    async def voice_tools(
        profile: str | None = Query(default=None), owner: Any = Depends(_owner)
    ) -> JSONResponse:
        return owner[0].tool_catalog(profile)

    @app.post(base + "/api/tools/{name}/call")
    async def voice_tool_call(
        name: str,
        body: dict[str, Any] = Body(default_factory=dict),
        owner: Any = Depends(_owner),
    ) -> JSONResponse:
        current, author = owner
        return await current.call_tool_rest(name, body, author)

    @app.get(base + "/api/voice/prompt")
    async def voice_prompt(
        provider: str = Query(...),
        surface: str = Query(default="ios"),
        conversationId: str = Query(default=""),  # noqa: N803 — the app's query name
        owner: Any = Depends(_owner),
    ) -> JSONResponse:
        return owner[0].prompt(provider, surface, conversationId)

    @app.get(base + "/api/call-history")
    async def voice_call_history(
        limit: int | None = Query(default=None, ge=1), owner: Any = Depends(_owner)
    ) -> dict[str, Any]:
        return owner[0].call_history(limit)

    @app.get(base + "/api/logs")
    async def voice_logs(owner: Any = Depends(_owner)) -> dict[str, Any]:
        return owner[0].logs()

    @app.get(base + "/api/logs/{call_id}")
    async def voice_log(call_id: str, owner: Any = Depends(_owner)) -> JSONResponse:
        return owner[0].log(call_id)

    @app.patch(base + "/api/logs/{call_id}")
    async def voice_patch_log(
        call_id: str,
        body: dict[str, Any] = Body(default_factory=dict),
        owner: Any = Depends(_owner),
    ) -> JSONResponse:
        return owner[0].patch_log(call_id, body)

    @app.delete(base + "/api/logs/{call_id}")
    async def voice_delete_log(call_id: str, owner: Any = Depends(_owner)) -> JSONResponse:
        return owner[0].delete_log(call_id)


def _required(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise wire.ProtocolError(f"{key} is required", wire.ErrorCodes.INVALID_PARAMS)
    return value


def _catalog_sha(definitions: list[dict[str, Any]]) -> str:
    canonical = json.dumps(definitions, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
