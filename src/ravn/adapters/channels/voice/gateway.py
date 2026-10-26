"""Make the resident the agent service of a realtime voice client.

Rather than teaching a voice client about Ravn, this teaches the resident to
speak the agent-service protocol such clients already implement: a
``{type,id,payload}`` WebSocket at the root and REST under ``/api``. The
client's realtime model runs on the device and talks to the provider directly;
the resident supplies who is speaking (its persona), short-lived provider
credentials, its own tools, and the record of the call.

The server binds loopback. The pod's nginx forwards ``/voice/`` here, behind the
gateway's JWT check, which projects the verified caller into ``user_id_header``.
Only the resident's owner is served (``owner_id``): a call mints the owner's
provider credentials and runs the resident's tools. ``auth`` is answered ``auth.ok``.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import Body, FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from ravn.adapters.channels.voice import protocol as wire
from ravn.adapters.channels.voice.prompt import (
    UnsupportedProviderError,
    VoicePersona,
    VoicePromptComposer,
)
from ravn.adapters.channels.voice.room import VoiceRoomCopier
from ravn.adapters.channels.voice.store import UnknownCallError, VoiceCallStore
from ravn.adapters.channels.voice.tools import (
    UnknownToolError,
    VoiceToolBridge,
    VoiceToolTimeoutError,
)
from ravn.ports.realtime_voice import RealtimeCredentialError, RealtimeCredentialMinterPort

logger = logging.getLogger(__name__)

#: Mint provider (what ``voice.ephemeral`` names) → prompt provider.
MINT_PROVIDERS = {"xai": "grok", "openai": "gpt-realtime"}

#: The one tool profile the app asks for.
VOICE_TOOL_PROFILE = "voice"
#: WebSocket close code for a caller who is not the resident's owner.
_CLOSE_FORBIDDEN = 4403


@dataclass
class _Connection:
    """Per-socket state: the tool allowlist the app may set with ``tools.enable``."""

    allowlist: set[str] | None = None
    started: float = field(default_factory=time.monotonic)


class VoiceGateway:
    """The resident's voice server."""

    def __init__(
        self,
        config: Any,
        *,
        persona: VoicePersona,
        composer: VoicePromptComposer,
        tools: VoiceToolBridge,
        store: VoiceCallStore,
        minters: dict[str, RealtimeCredentialMinterPort],
        room: VoiceRoomCopier | None,
    ) -> None:
        if not getattr(config, "owner_id", ""):
            raise ValueError(
                "The voice channel needs owner_id: only the resident's owner may call it"
            )
        unknown = set(minters) - set(MINT_PROVIDERS)
        if unknown:
            raise ValueError(
                f"Voice minters {sorted(unknown)} name no supported provider; "
                f"use {sorted(MINT_PROVIDERS)}"
            )
        self._config = config
        self._persona = persona
        self._composer = composer
        self._tools = tools
        self._store = store
        self._minters = minters
        self._room = room
        self._connections: set[int] = set()
        self._started = time.monotonic()
        self._app = self._build_app()

    @property
    def app(self) -> FastAPI:
        return self._app

    # -- lifecycle -------------------------------------------------------

    async def run(self) -> None:
        import uvicorn

        from niuu.observability import install_uvicorn_log_redaction

        install_uvicorn_log_redaction()
        server = uvicorn.Server(
            uvicorn.Config(
                self._app,
                host=self._config.host,
                port=int(self._config.port),
                log_level="info",
                access_log=False,
                ws_ping_interval=None,  # the app owns the heartbeat (ping / pong frames)
            )
        )
        logger.info(
            "Voice channel listening on %s:%s (persona=%s, providers=%s)",
            self._config.host,
            self._config.port,
            self._persona.name,
            sorted(self._minters),
        )
        try:
            await server.serve()
        finally:
            await self.aclose()

    async def aclose(self) -> None:
        """Release the room connection and the call store."""
        if self._room is not None:
            await self._room.aclose()
        self._store.close()

    # -- app ---------------------------------------------------------------

    def _is_owner(self, headers: Any) -> bool:
        return headers.get(self._config.user_id_header, "").strip() == self._config.owner_id

    def _build_app(self) -> FastAPI:
        app = FastAPI(title="Ravn voice channel", docs_url=None, redoc_url=None)

        @app.middleware("http")
        async def owner_only(request: Request, call_next: Any) -> Any:
            if not self._is_owner(request.headers):
                return JSONResponse(
                    {"success": False, "error": "Only this resident's owner may call it"},
                    status_code=403,
                )
            return await call_next(request)

        @app.get("/health")
        async def health() -> dict[str, Any]:
            return {
                "status": "ok",
                "uptime": int(time.monotonic() - self._started),
                "connections": len(self._connections),
                "sessions": sum(
                    1
                    for call in self._store.calls(self._config.history_max_limit)
                    if call.state == "active"
                ),
                "tools": len(self._tools.names),
            }

        @app.websocket("/")
        async def ws_root(websocket: WebSocket) -> None:
            await self._serve(websocket)

        @app.get("/api/tools")
        async def list_tools(profile: str | None = Query(default=None)) -> JSONResponse:
            definitions = self._tools.definitions()
            if profile is None:
                return JSONResponse({"success": True, "tools": definitions})
            if profile != VOICE_TOOL_PROFILE:
                return JSONResponse(
                    {"success": False, "error": f"Unknown tool profile: {profile}"},
                    status_code=400,
                )
            return JSONResponse(
                {
                    "success": True,
                    "tools": definitions,
                    "profile": VOICE_TOOL_PROFILE,
                    "sha": _catalog_sha(definitions),
                }
            )

        @app.post("/api/tools/{name}/call")
        async def call_tool(
            name: str, body: dict[str, Any] = Body(default_factory=dict)
        ) -> JSONResponse:
            args = body.get("args") if isinstance(body.get("args"), dict) else {}
            conversation_id = str(body.get("conversationId") or body.get("sessionId") or "")
            call_id = str(body.get("callId") or "")
            try:
                outcome = await self._tools.call(
                    name, args, call_id=call_id, conversation_id=conversation_id
                )
            except UnknownToolError:
                return JSONResponse(
                    {"success": False, "error": f"Voice tool '{name}' is not registered"},
                    status_code=400,
                )
            except VoiceToolTimeoutError:
                return JSONResponse(
                    {"success": False, "error": f"Voice tool '{name}' ran past its time limit"},
                    status_code=400,
                )
            if outcome.is_error:
                return JSONResponse(
                    {"success": True, "result": {"error": outcome.content, "toolName": name}}
                )
            return JSONResponse({"success": True, "result": outcome.content})

        @app.get("/api/voice/prompt")
        async def voice_prompt(
            provider: str = Query(...),
            surface: str = Query(default="ios"),
            conversationId: str = Query(default=""),  # noqa: N803 — the app's query name
        ) -> JSONResponse:
            if not conversationId:
                return JSONResponse(
                    {"success": False, "error": "conversationId is required"}, status_code=400
                )
            try:
                prompt = self._composer.prompt(
                    provider, surface=surface, conversation_id=conversationId
                )
            except UnsupportedProviderError:
                logger.info("No voice prompt for provider %r", provider.replace("\n", " "))
                return JSONResponse(
                    {
                        "success": False,
                        "error": "This resident composes no prompt for that provider",
                    },
                    status_code=404,
                )
            return JSONResponse({"success": True, "prompt": prompt})

        @app.get("/api/call-history")
        async def call_history(limit: int | None = Query(default=None, ge=1)) -> dict[str, Any]:
            count = min(limit or self._config.history_default_limit, self._config.history_max_limit)
            calls = [self._store.history_json(call) for call in self._store.calls(count)]
            return {"calls": calls, "count": len(calls)}

        @app.get("/api/logs")
        async def list_logs() -> dict[str, Any]:
            calls = self._store.calls(self._config.history_max_limit)
            return {"sessions": [self._store.session_json(call) for call in calls]}

        @app.get("/api/logs/{call_id}")
        async def get_log(call_id: str) -> JSONResponse:
            try:
                call = self._store.get(call_id)
            except UnknownCallError:
                return JSONResponse({"error": "Unknown voice call"}, status_code=404)
            entries = [self._store.entry_json(entry) for entry in self._store.entries(call_id)]
            return JSONResponse({"session": self._store.session_json(call), "entries": entries})

        @app.patch("/api/logs/{call_id}")
        async def patch_log(
            call_id: str, body: dict[str, Any] = Body(default_factory=dict)
        ) -> JSONResponse:
            title = body.get("title")
            pinned = body.get("pinned")
            if title is not None and not isinstance(title, str):
                return JSONResponse({"error": "title must be a string"}, status_code=400)
            if pinned is not None and not isinstance(pinned, bool):
                return JSONResponse({"error": "pinned must be a boolean"}, status_code=400)
            try:
                call = self._store.update(call_id, title=title, pinned=pinned)
            except UnknownCallError:
                return JSONResponse({"error": "Unknown voice call"}, status_code=404)
            return JSONResponse(
                {"success": True, "sessionId": call_id, "title": call.title, "pinned": call.pinned}
            )

        @app.delete("/api/logs/{call_id}")
        async def delete_log(call_id: str) -> JSONResponse:
            try:
                self._store.delete(call_id)
            except UnknownCallError:
                return JSONResponse({"error": "Unknown voice call"}, status_code=404)
            return JSONResponse({"deleted": True, "sessionId": call_id})

        return app

    # -- WebSocket -------------------------------------------------------

    async def _serve(self, websocket: WebSocket) -> None:
        if not self._is_owner(websocket.headers):
            await websocket.close(code=_CLOSE_FORBIDDEN)
            return
        await websocket.accept()
        connection = _Connection()
        key = id(connection)
        self._connections.add(key)
        try:
            while True:
                raw = await websocket.receive_text()
                response = await self._answer(connection, raw)
                if response is not None:
                    await websocket.send_json(response)
        except WebSocketDisconnect:
            # The app closing its socket is the normal end of a connection.
            pass
        finally:
            self._connections.discard(key)
            with contextlib.suppress(RuntimeError):
                await websocket.close()

    async def _answer(self, connection: _Connection, raw: str) -> dict | None:
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
        except Exception as exc:
            logger.exception("Voice channel failed to answer a message")
            return wire.error_frame(message_id, str(exc), wire.ErrorCodes.INTERNAL_ERROR)

    async def _dispatch(
        self, connection: _Connection, message_type: str, message_id: str, payload: dict[str, Any]
    ) -> dict:
        match message_type:
            case wire.AUTH:
                return wire.frame(wire.AUTH_OK, message_id)
            case wire.PING:
                return wire.frame(wire.PONG, message_id)
            case wire.PERSONA_LIST:
                return wire.frame(
                    wire.PERSONA_LIST_RESULT,
                    message_id,
                    {"defaultPersona": self._persona.name, "personas": [self._persona.name]},
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
                return await self._call(message_id, payload)
            case wire.SESSION_START:
                return self._session_start(message_id, payload)
            case wire.SESSION_LOG:
                return await self._session_log(message_id, payload)
            case wire.SESSION_END:
                return self._session_end(message_id, payload)
            case wire.VOICE_EPHEMERAL:
                return await self._mint(message_id, payload)
            case wire.GATEWAY_CREDENTIALS:
                return wire.frame(
                    wire.GATEWAY_CREDENTIALS_RESULT,
                    message_id,
                    {"gateways": [], "reason": "A resident hands out no gateway credentials."},
                )
            case wire.DICTATION_CREDENTIAL | wire.MEMORY_ENROLLMENT:
                raise wire.ProtocolError(
                    f"{message_type} is not served by a resident's voice channel",
                    wire.ErrorCodes.NOT_CONFIGURED,
                )
            case wire.TOOLS_GROUP_TOGGLE:
                raise wire.ProtocolError(
                    "This resident's voice tools have no groups", wire.ErrorCodes.INVALID_PARAMS
                )
        raise wire.ProtocolError(f"Unknown message: {message_type}", wire.ErrorCodes.UNKNOWN_TYPE)

    # -- handlers --------------------------------------------------------

    def _context(self) -> dict[str, Any]:
        persona = self._persona
        prompt = persona.context_prompt()
        voices = {key: name for key, name in persona.voices.items()}
        return {
            "name": persona.name,
            "displayName": persona.display_name,
            "contextPrompt": prompt,
            "voice": voices.get("openai-realtime") or next(iter(voices.values())),
            "voices": voices,
            "sources": [f"persona:{persona.name}"],
            "errors": [],
        }

    def _allowed_definitions(self, connection: _Connection) -> list[dict[str, Any]]:
        definitions = self._tools.definitions()
        if connection.allowlist is None:
            return definitions
        return [d for d in definitions if d["name"] in connection.allowlist]

    def _enable(self, connection: _Connection, message_id: str, payload: dict[str, Any]) -> dict:
        names = payload.get("tools", payload.get("allowlist"))
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            raise wire.ProtocolError(
                "tools must be a list of names", wire.ErrorCodes.INVALID_PARAMS
            )
        connection.allowlist = {name for name in names if name in self._tools.names}
        return wire.frame(
            wire.TOOLS_ENABLE_RESULT, message_id, {"enabled": sorted(connection.allowlist)}
        )

    async def _call(self, message_id: str, payload: dict[str, Any]) -> dict:
        name = str(payload.get("name") or "")
        if not name:
            raise wire.ProtocolError("name is required", wire.ErrorCodes.INVALID_PARAMS)
        args = payload.get("args", payload.get("arguments")) or {}
        if not isinstance(args, dict):
            raise wire.ProtocolError("args must be an object", wire.ErrorCodes.INVALID_PARAMS)
        try:
            outcome = await self._tools.call(
                name,
                args,
                call_id=str(payload.get("callId") or ""),
                conversation_id=str(payload.get("sessionId") or ""),
            )
        except (UnknownToolError, VoiceToolTimeoutError) as exc:
            message = (
                str(exc) if isinstance(exc, VoiceToolTimeoutError) else f"Unknown tool: {name}"
            )
            return wire.frame(wire.TOOLS_CALL_ERROR, message_id, {"name": name, "error": message})
        result: Any = (
            {"error": outcome.content, "toolName": name} if outcome.is_error else outcome.content
        )
        return wire.frame(wire.TOOLS_CALL_RESULT, message_id, {"name": name, "result": result})

    def _session_start(self, message_id: str, payload: dict[str, Any]) -> dict:
        call_id = _required(payload, "sessionId")
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        call = self._store.start(
            call_id,
            persona=str(payload.get("persona") or self._persona.name),
            provider=str(payload.get("provider") or ""),
            metadata=metadata,
        )
        return wire.frame(
            wire.SESSION_STARTED, message_id, {"session": self._store.session_json(call)}
        )

    async def _session_log(self, message_id: str, payload: dict[str, Any]) -> dict:
        call_id = _required(payload, "sessionId")
        entry_type = _required(payload, "type")
        entry_payload = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        try:
            entry = self._store.log(call_id, entry_type, entry_payload)
        except UnknownCallError as exc:
            raise wire.ProtocolError(str(exc), wire.ErrorCodes.SESSION_ERROR) from exc
        except ValueError as exc:
            raise wire.ProtocolError(str(exc), wire.ErrorCodes.INVALID_PARAMS) from exc
        await self._copy_to_room(call_id, entry_type, entry_payload)
        return wire.frame(
            wire.SESSION_LOG_ACK, message_id, {"entry": self._store.entry_json(entry)}
        )

    def _session_end(self, message_id: str, payload: dict[str, Any]) -> dict:
        call_id = _required(payload, "sessionId")
        duration = payload.get("durationMs")
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        summary = payload.get("summary") if isinstance(payload.get("summary"), str) else None
        try:
            call = self._store.end(
                call_id,
                reason=str(payload.get("reason") or "ended"),
                duration_ms=int(duration) if isinstance(duration, int | float) else None,
                summary=summary,
                metadata=metadata,
            )
        except UnknownCallError as exc:
            raise wire.ProtocolError(str(exc), wire.ErrorCodes.SESSION_ERROR) from exc
        return wire.frame(
            wire.SESSION_ENDED, message_id, {"summary": self._store.session_json(call)}
        )

    async def _copy_to_room(self, call_id: str, entry_type: str, payload: dict[str, Any]) -> None:
        if self._room is None or entry_type not in ("user_speech", "agent_speech"):
            return
        text = str(payload.get("text") or "").strip()
        if not text or payload.get("isLive") is True:
            return
        speaker = "user" if entry_type == "user_speech" else "agent"
        await self._room.copy(call_id=call_id, speaker=speaker, text=text)

    async def _mint(self, message_id: str, payload: dict[str, Any]) -> dict:
        provider = str(payload.get("provider") or "")
        if not provider:
            raise wire.ProtocolError("provider is required", wire.ErrorCodes.INVALID_PARAMS)
        minter = self._minters.get(provider)
        if minter is None:
            raise wire.ProtocolError(
                f"This resident mints no {provider} voice credentials",
                wire.ErrorCodes.UNSUPPORTED_PROVIDER,
            )
        prompt_provider = MINT_PROVIDERS[provider]
        model = str(payload.get("model") or self._composer.model_for(prompt_provider))
        voice = self._persona.voice_for(prompt_provider)
        instructions = self._composer.standing_instructions()
        try:
            credential = await minter.mint(
                model=model,
                voice=voice,
                instructions=instructions,
                reasoning=str(payload.get("reasoning") or ""),
            )
        except RealtimeCredentialError as exc:
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
                "persona": self._persona.name,
                "voice": voice,
                "instructions": instructions,
                "personaApplied": credential.session_bound,
            },
        )


def _required(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise wire.ProtocolError(f"{key} is required", wire.ErrorCodes.INVALID_PARAMS)
    return value


def _catalog_sha(definitions: list[dict[str, Any]]) -> str:
    canonical = json.dumps(definitions, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
