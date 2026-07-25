"""HTTP gateway — FastAPI server for local/LAN access to Ravn.

Endpoints:
  POST /chat    — send a message; response is an SSE stream of RavnEvents.
  GET  /status  — JSON: active session IDs and count.
  GET  /events  — SSE broadcast of *all* events across all sessions.
  WS   /ws      — WebSocket chat with CLI-format translation.

Runs via uvicorn inside an asyncio task (no subprocess).
Suitable for Home Assistant automations, local scripts, and cron jobs.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import secrets
from collections.abc import AsyncIterator
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ravn.adapters.channels.gateway import RavnGateway
from ravn.config import HttpChannelConfig
from ravn.domain.events import RavnEvent
from ravn.ports.event_translator import EventTranslatorPort

logger = logging.getLogger(__name__)


def _import_class(dotted_path: str) -> type:
    """Import a class from a fully-qualified dotted path."""
    module_path, class_name = dotted_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


class ChatRequest(BaseModel):
    """Body schema for ``POST /chat``."""

    message: str
    session_id: str = "http:default"


class ResidentAnswerRequest(BaseModel):
    """Free-text answer for one persisted resident continuation."""

    case_id: str
    answer: str


class HttpGateway:
    """FastAPI-based HTTP gateway for Ravn.

    Each call to ``POST /chat`` streams :class:`~ravn.domain.events.RavnEvent`
    objects as Server-Sent Events so callers can display streaming output.

    ``WS /ws`` accepts WebSocket connections and translates events using the
    configured :class:`~ravn.ports.event_translator.EventTranslatorPort`
    (default: CLI stream-json format for ``useSkuldChat`` compatibility).

    ``GET /events`` broadcasts *all* events from *all* active sessions to the
    subscriber — useful for dashboards or Home Assistant integrations.
    """

    def __init__(
        self,
        config: HttpChannelConfig,
        gateway: RavnGateway,
        resident_runtime: Any | None = None,
    ) -> None:
        self._config = config
        self._gateway = gateway
        self._resident_runtime = resident_runtime
        self._translator_cls: type[EventTranslatorPort] = _import_class(config.translator)
        self._app = self._build_app()

    @property
    def app(self) -> FastAPI:
        """The underlying FastAPI application (useful for testing)."""
        return self._app

    # ------------------------------------------------------------------
    # FastAPI application
    # ------------------------------------------------------------------

    def _build_app(self) -> FastAPI:
        app = FastAPI(title="Ravn Gateway", docs_url=None, redoc_url=None)

        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )

        def require_operator(authorization: str | None = Header(default=None)) -> None:
            token = os.environ.get(self._config.operator_token_env, "").strip()
            if not token:
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "Resident operator endpoints are disabled because "
                        f"{self._config.operator_token_env} is not configured"
                    ),
                )
            scheme, _, presented = (authorization or "").partition(" ")
            if scheme.casefold() != "bearer" or not secrets.compare_digest(
                presented.strip(), token
            ):
                raise HTTPException(
                    status_code=401,
                    detail="Invalid resident operator bearer token",
                    headers={"WWW-Authenticate": "Bearer"},
                )

        @app.post("/chat")
        async def chat(request: ChatRequest) -> StreamingResponse:
            """Send a message to Ravn and receive a streaming SSE response."""
            return StreamingResponse(
                self._chat_stream(request.session_id, request.message),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )

        @app.get("/status")
        async def status() -> dict:
            """Return active session IDs, session count, and profile identity."""
            return self._gateway.get_status()

        @app.get("/events")
        async def events() -> StreamingResponse:
            """SSE broadcast stream — receive all events from all sessions."""
            return StreamingResponse(
                self._broadcast_stream(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )

        if self._resident_runtime is not None:

            @app.get("/resident/operator-needed")
            async def resident_operator_needed(
                _: None = Depends(require_operator),
            ) -> dict[str, Any]:
                """List pending resident questions on the authenticated daemon surface."""
                return {"items": await self._resident_runtime.pending_questions()}

            @app.get("/resident/timeline")
            async def resident_timeline(
                _: None = Depends(require_operator),
                prefix: str = "",
            ) -> dict[str, Any]:
                """Serve the resident's working-state history from its own state store.

                Ravn owns resident state, so it serves it. Reading the durable
                records out of band cannot see residents whose state lives behind
                a non-filesystem adapter, and cannot be trusted to be current.
                """
                from ravn.resident_timeline import build_resident_timeline  # noqa: PLC0415

                timeline = await build_resident_timeline(
                    self._resident_runtime.state,
                    resident_id=self._resident_runtime.resident_id,
                    charter=self._resident_runtime.charter,
                    prefix=prefix,
                )
                return timeline.as_dict()

            @app.post("/resident/operator-answer")
            async def resident_operator_answer(
                request: ResidentAnswerRequest,
                _: None = Depends(require_operator),
            ) -> dict[str, Any]:
                """Persist an answer and enqueue the same resident case."""
                try:
                    return await self._resident_runtime.submit_operator_answer(
                        case_id=request.case_id,
                        answer=request.answer,
                    )
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
                except LookupError as exc:
                    raise HTTPException(status_code=404, detail=str(exc)) from exc

        @app.websocket("/ws")
        async def websocket_chat(ws: WebSocket) -> None:
            """WebSocket chat — translates RavnEvents to CLI stream-json."""
            await ws.accept()
            session_id = f"ws:{id(ws)}"
            translator = self._translator_cls()
            try:
                while True:
                    raw = await ws.receive_text()
                    msg = json.loads(raw)
                    if msg.get("type") != "user":
                        continue
                    content = msg.get("content", "")
                    if not content:
                        continue
                    translator.reset()
                    async for event in self._gateway.handle_message_stream(session_id, content):
                        for wire_event in translator.translate(event):
                            await ws.send_text(json.dumps(wire_event))
            except WebSocketDisconnect:
                logger.debug("WebSocket client disconnected (session=%s).", session_id)
            except Exception:
                logger.exception("WebSocket error (session=%s).", session_id)

        return app

    # ------------------------------------------------------------------
    # Stream generators
    # ------------------------------------------------------------------

    @staticmethod
    def _serialise_event(event: RavnEvent) -> str:
        """Serialise a :class:`RavnEvent` as a JSON string for SSE delivery."""
        return json.dumps(
            {
                "type": str(event.type),
                "payload": event.payload,
                "source": event.source,
                "session_id": str(event.session_id),
                "timestamp": event.timestamp.isoformat(),
            }
        )

    async def _chat_stream(self, session_id: str, message: str) -> AsyncIterator[str]:
        """Yield SSE-formatted lines for each event from a chat turn."""
        async for event in self._gateway.handle_message_stream(session_id, message):
            yield f"data: {self._serialise_event(event)}\n\n"

    async def _broadcast_stream(self) -> AsyncIterator[str]:
        """Yield SSE-formatted lines for every event across all sessions."""
        q = self._gateway.subscribe()
        try:
            while True:
                event = await q.get()
                if event is None:
                    break
                yield f"data: {self._serialise_event(event)}\n\n"
        finally:
            self._gateway.unsubscribe(q)

    # ------------------------------------------------------------------
    # Server lifecycle
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Start the uvicorn server and block until cancelled."""
        import uvicorn

        uv_config = uvicorn.Config(
            app=self._app,
            host=self._config.host,
            port=self._config.port,
            log_level="warning",
            access_log=False,
        )
        server = uvicorn.Server(uv_config)
        logger.info(
            "HTTP gateway listening on %s:%s.",
            self._config.host,
            self._config.port,
        )
        try:
            await server.serve()
        except asyncio.CancelledError:
            logger.info("HTTP gateway stopped.")
            raise
