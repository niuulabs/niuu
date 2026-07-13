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
from collections.abc import AsyncIterator

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
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
    ) -> None:
        self._config = config
        self._gateway = gateway
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
