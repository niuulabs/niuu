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
import ipaddress
import json
import logging
from collections.abc import AsyncIterator, Callable, Sequence

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ravn.adapters.channels.gateway import RavnGateway
from ravn.config import HttpChannelConfig
from ravn.domain.events import RavnEvent
from ravn.ports.event_translator import EventTranslatorPort
from ravn.ports.tool import ToolPort

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


class ToolExecuteRequest(BaseModel):
    """Body schema for a resident-owned tool invocation."""

    input: dict


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
        *,
        resident_tool_provider: Callable[[], Sequence[ToolPort]] | None = None,
    ) -> None:
        self._config = config
        self._gateway = gateway
        self._resident_tool_provider = resident_tool_provider
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

        @app.get("/internal/tools")
        async def resident_tools(request: Request) -> dict:
            """Describe tools owned by this resident daemon."""
            self._require_loopback(request)
            return {
                "tools": [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "input_schema": tool.input_schema,
                        "required_permission": tool.required_permission,
                        "parallelisable": tool.parallelisable,
                    }
                    for tool in self._resident_tools()
                ]
            }

        @app.post("/internal/tools/{tool_name}")
        async def execute_resident_tool(
            tool_name: str,
            body: ToolExecuteRequest,
            request: Request,
        ) -> dict:
            """Execute a tool through the daemon that owns its runtime state."""
            self._require_loopback(request)
            tool = next(
                (candidate for candidate in self._resident_tools() if candidate.name == tool_name),
                None,
            )
            if tool is None:
                raise HTTPException(status_code=404, detail="Unknown resident tool")
            result = await tool.execute(body.input)
            return {
                "tool_call_id": result.tool_call_id,
                "content": result.content,
                "is_error": result.is_error,
            }

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

    def _resident_tools(self) -> Sequence[ToolPort]:
        if self._resident_tool_provider is None:
            return ()
        return self._resident_tool_provider()

    @staticmethod
    def _require_loopback(request: Request) -> None:
        host = request.client.host if request.client is not None else ""
        try:
            is_loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            is_loopback = False
        if not is_loopback:
            raise HTTPException(status_code=403, detail="Resident tools are local-only")

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
