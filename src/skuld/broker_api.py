"""FastAPI composition and HTTP route handlers for the Skuld broker."""

import json
import logging
import mimetypes
import os
import re
import shutil
import time
import uuid
from collections import deque
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Response, WebSocket, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from niuu.build_identity import build_identity
from niuu.domain.history_paging import InvalidHistoryCursorError
from niuu.domain.json_text import json_text_safe
from niuu.domain.text_projection import projection_revision
from skuld.conversation_models import ConversationTurn
from skuld.conversation_read import conversation_rows, wait_history_quiet
from skuld.conversation_shallow import SHALLOW_DETAIL, elide_turn, is_elided_input
from skuld.conversation_snapshot import ConversationSnapshotTooLargeError, prepare_history_page
from skuld.event_log import FORGE_SESSIONS_PATH
from skuld.file_routes import register_file_routes
from skuld.path_security import (
    UnsafePathError,
    resolve_contained_path,
    resolve_path_in_roots,
)
from skuld.service_manager import ServiceCreateRequest, ServiceStatus
from volundr.log_aggregate import aggregate_workspace_logs

WORKFLOW_GATE_INTENT_HEADER = "x-niuu-workflow-gate-intent"
WORKFLOW_GATE_INTENT_RESOLVE = "resolve"

logger = logging.getLogger("skuld.broker")
_BUILD_IDENTITY = build_identity()
_broker_getter: Callable[[], Any] | None = None
_log_buffer: deque[dict] = deque()


class _BrokerProxy:
    """Resolve the replaceable broker singleton at each route access."""

    def __getattr__(self, name: str) -> Any:
        if _broker_getter is None:
            raise RuntimeError("Skuld broker API is not bound")
        return getattr(_broker_getter(), name)


broker = _BrokerProxy()


def bind_broker(getter: Callable[[], Any], log_buffer: deque[dict]) -> None:
    """Bind route handlers to the broker singleton and its live log buffer."""
    global _broker_getter, _log_buffer
    _broker_getter = getter
    _log_buffer = log_buffer

    cfg = getter()._settings.ws_auth
    if cfg.enforce_ownership:
        app.state.identity = getter()._ws_identity
        from niuu.adapters.pat_revocation_middleware import PATRevocationMiddleware

        app.add_middleware(
            PATRevocationMiddleware, websocket_check_interval=cfg.websocket_check_interval
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    from niuu.observability import configure_observability, shutdown_observability

    # Telegram embeds the bot credential in every Bot API URL. Suppress httpx's
    # request-level INFO records so the credential never enters a log record;
    # retain value redaction for Skuld/uvicorn-owned access-token messages.
    _redact_filter = _TokenRedactFilter()
    filtered_loggers = [
        logging.getLogger(name) for name in ("uvicorn", "uvicorn.access", "uvicorn.error")
    ]
    httpx_logger = logging.getLogger("httpx")
    filtered_loggers.append(httpx_logger)
    for filtered_logger in filtered_loggers:
        filtered_logger.addFilter(_redact_filter)
    previous_httpx_level = httpx_logger.level
    httpx_logger.setLevel(logging.WARNING)

    configure_observability(
        broker._settings.observability,
        resource_attributes={
            "service.namespace": "skuld",
            "service.instance.id": broker.session_id,
            "niuu.session.id": broker.session_id,
        },
    )
    try:
        await broker.startup()
        yield
    finally:
        try:
            await broker.shutdown()
        finally:
            shutdown_observability()
            httpx_logger.setLevel(previous_httpx_level)
            for filtered_logger in filtered_loggers:
                filtered_logger.removeFilter(_redact_filter)


app = FastAPI(
    title="Skuld Broker",
    description="WebSocket broker for Claude Code CLI",
    version="0.3.0",
    lifespan=lifespan,
)

# Add CORS middleware — browser UI (hlidskjalf) is served from a different
# origin than the per-session Skuld pod, so cross-origin requests to /api/*
# need explicit CORS headers.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    """Health check endpoint."""
    startup = broker.startup_status()
    return {
        "status": "unhealthy" if startup["startup_state"] == "failed" else "healthy",
        "session_id": broker.session_id,
        **startup,
        **_BUILD_IDENTITY,
    }


@app.get("/ready")
async def ready(response: Response) -> dict:
    """Readiness check endpoint."""
    startup = broker.startup_status()
    if not startup["ready"]:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"session_id": broker.session_id, **startup}


@app.websocket("/session")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint for browser chat."""
    await broker.handle_websocket(websocket)


@app.websocket("/ws/cli/{session_id}")
async def cli_websocket_endpoint(websocket: WebSocket, session_id: str) -> None:
    """WebSocket endpoint for Claude Code CLI (via --sdk-url)."""
    await broker.handle_cli_websocket(websocket, session_id)


@app.websocket("/ws/ravn/{peer_id}")
async def ravn_websocket_endpoint(websocket: WebSocket, peer_id: str) -> None:
    """WebSocket endpoint for Ravn daemon connections (room mode)."""
    await broker.handle_ravn_websocket(websocket, peer_id)


# --- Broker Log API ---


@app.get("/api/logs")
async def get_broker_logs(
    lines: int = Query(default=100, ge=1, le=2000),
    level: str = Query(default="DEBUG"),
) -> dict:
    """Return recent broker log entries from the in-memory ring buffer."""
    min_level = getattr(logging, level.upper(), logging.DEBUG)
    filtered = [entry for entry in _log_buffer if logging.getLevelName(entry["level"]) >= min_level]
    tail = list(filtered)[-lines:]
    return {
        "session_id": broker.session_id,
        "total": len(_log_buffer),
        "returned": len(tail),
        "lines": tail,
    }


@app.get("/api/logs/aggregate")
async def get_aggregate_logs(
    lines: int = Query(default=100, ge=1, le=2000),
    level: str = Query(default="DEBUG"),
    participants: str | None = Query(default=None),
    query: str = Query(default=""),
) -> dict:
    """Return interleaved broker, flock, and service logs from the session workspace."""
    requested_participants = (
        {item.strip() for item in participants.split(",") if item.strip()} if participants else None
    )
    payload = aggregate_workspace_logs(
        broker.workspace_dir,
        lines=lines,
        level=level,
        participants=requested_participants,
        query=query,
    )
    if not any(item.get("id") == "skuld" for item in payload.get("available_participants", [])):
        min_level = getattr(logging, level.upper(), logging.DEBUG)
        query_lower = query.casefold()
        broker_lines = []
        for index, entry in enumerate(_log_buffer):
            entry_level = str(entry.get("level") or "INFO").upper()
            entry_level_no = getattr(logging, entry_level, logging.INFO)
            message = str(entry.get("message") or "")
            source = str(entry.get("logger") or "skuld")
            if entry_level_no < min_level:
                continue
            if requested_participants and "skuld" not in requested_participants:
                continue
            if query_lower and not (
                query_lower in "skuld"
                or query_lower in source.casefold()
                or query_lower in message.casefold()
            ):
                continue
            created = entry.get("timestamp")
            timestamp = (
                datetime.fromtimestamp(float(created), tz=UTC)
                if isinstance(created, (int, float))
                else datetime.now(UTC)
            )
            broker_lines.append(
                {
                    "id": f"skuld-buffer-{index}",
                    "timestamp": timestamp.isoformat(),
                    "level": entry_level,
                    "participant": "skuld",
                    "participant_label": "Skuld",
                    "participant_kind": "broker",
                    "source": source,
                    "message": message,
                    "sequence": index,
                    "stream": "memory",
                }
            )

        if _log_buffer:
            payload.setdefault("available_participants", []).insert(
                0,
                {"id": "skuld", "label": "Skuld", "kind": "broker"},
            )
        if broker_lines:
            all_lines = [*payload.get("lines", []), *broker_lines]
            all_lines.sort(key=lambda item: (str(item.get("timestamp") or ""), item.get("id", "")))
            payload["lines"] = all_lines[-lines:]
            payload["returned"] = len(payload["lines"])
            payload["filtered"] = int(payload.get("filtered") or 0) + len(broker_lines)
        payload["total"] = int(payload.get("total") or 0) + len(_log_buffer)
    payload["session_id"] = broker.session_id
    return payload


# --- Conversation History API ---


@app.get("/api/conversation/history")
async def get_conversation_history(
    detail: str = "full",
    history_protocol: int = 0,
    limit: int = 0,
    max_bytes: int = 0,
    cursor: str | None = None,
    turn_id: str | None = None,
) -> dict:
    """Return the conversation history with activity state.

    ``detail=shallow`` elides heavy ``tool_result`` content to lazy-load
    placeholders (see ``conversation_shallow``); the client fetches an
    individual result on demand via ``/api/conversation/tool-result/{id}``.
    """
    if history_protocol == 2:
        try:
            await wait_history_quiet(broker)
        except TimeoutError as exc:
            raise HTTPException(
                503, {"code": "history_busy", "recovery": "retry"}, headers={"Retry-After": "1"}
            ) from exc
    shallow = detail == SHALLOW_DETAIL
    is_active = (
        broker._transport is not None
        and broker._transport.is_alive
        and bool(broker._pending_assistant_content or broker._pending_reasoning_text)
    )
    # Build a short activity description for the UI
    last_activity = ""
    if is_active:
        if broker._pending_assistant_content:
            # Last ~100 chars of what the assistant is writing
            last_activity = broker._pending_assistant_content[-100:]
        elif broker._pending_reasoning_text:
            last_activity = "Thinking..."

    t_build = time.perf_counter()
    turns = conversation_rows(broker, omit_empty_participants=True)
    revision = projection_revision(turns)
    settled = [
        t
        for t in turns
        if not t.get("in_progress") and (t.get("metadata") or {}).get("status") != "in_progress"
    ]
    history_state = {
        "history_has_in_progress": len(settled) != len(turns),
        "history_settled_tail_id": settled[-1].get("id") if settled else None,
    }
    if turn_id is not None:
        turns = [turn for turn in turns if turn.get("id") == turn_id]
        if not turns:
            raise HTTPException(404, "History item no longer exists in this projection")
    last_activity = json_text_safe(last_activity)
    build_ms = (time.perf_counter() - t_build) * 1000.0

    elide_ms = 0.0
    if shallow and history_protocol != 2:
        t_elide = time.perf_counter()
        turns = [elide_turn(d) for d in turns]
        elide_ms = (time.perf_counter() - t_elide) * 1000.0

    prep = {
        "layer": "skuld",
        "shallow": shallow,
        "turns": len(turns),
        "build_ms": round(build_ms, 1),
        "elide_ms": round(elide_ms, 1),
    }
    logger.info(
        "[perf] conversation prep shallow=%s turns=%d build=%.1fms elide=%.1fms",
        shallow,
        len(turns),
        build_ms,
        elide_ms,
    )
    result = {
        "turns": turns,
        "projection_revision": revision,
        "head_seq": broker._event_log_seq,
        "is_active": is_active,
        "last_activity": last_activity,
        "_prep": prep,
        # Seed the first paint after a reconnect with the authoritative activity
        # state + when it was entered, so a client doesn't have to wait for the
        # next SSE/activity report to know whether the session is active/idle/etc.
        "activity_state": broker._activity_state,
        "activity_state_since": broker._state_since_iso(broker._activity_state_since),
        # The stable turn anchor for the running elapsed (null ⇔ no turn in
        # flight). Mirrors the /activity report so a reconnect's first paint can
        # anchor "running Ns" to the prompt instant, not the coarse _since.
        "turn_started_at": (
            broker._state_since_iso(broker._turn_started_at)
            if broker._turn_started_at is not None
            else None
        ),
    }
    if history_protocol != 2:
        if cursor:
            raise HTTPException(400, "cursor requires history_protocol=2")
        return json_text_safe(result)
    if turn_id is not None or limit < 0 or max_bytes < 0:
        raise HTTPException(400, "Invalid history page request")
    try:
        return prepare_history_page(
            {**result, **history_state, "history_source": "gateway"},
            session_id=broker.session_id,
            max_turns=min(
                limit or broker._settings.conversation_recent_max_turns,
                broker._settings.conversation_recent_max_turns,
            ),
            max_bytes=min(
                max_bytes or broker._settings.conversation_recent_max_bytes,
                broker._settings.conversation_recent_max_bytes,
            ),
            cursor=cursor,
        )
    except InvalidHistoryCursorError as exc:
        raise HTTPException(
            409, {"code": "history_cursor_invalid", "recovery": "recent", "history_protocol": 2}
        ) from exc
    except ConversationSnapshotTooLargeError as exc:
        raise HTTPException(413, {"code": "history_page_too_large", "recovery": "retry"}) from exc
    except ValueError as exc:
        raise HTTPException(400, "Malformed history cursor") from exc


@app.get("/api/conversation/tool-result/{tool_use_id}")
async def get_tool_result(tool_use_id: str) -> dict:
    """Return one full tool_result block by its tool_use_id.

    The lazy-load target for a shallow conversation: when a client expands an
    elided tool_result placeholder it fetches the full content here. Scans the
    completed turns and the in-flight turn for the matching block (the same
    authoritative data the conversation endpoint serves). 404 when no such
    tool_result exists in this session.
    """

    # P1 input elision (2026-07-12): the response also carries the matching tool_use's FULL
    # ``input`` so an elided-input card expands from the same lazy fetch. Use and result live
    # in DIFFERENT turns (assistant emits the use; the result rides the next user event), so
    # collect both across all sources. An input-only hit (tool still running) answers 200 with
    # empty content instead of 404 — the expand shows the input immediately.
    found_result: dict | None = None
    found_input = None

    sources = [turn.parts for turn in broker._conversation_turns]
    sources.append(list(broker._live_tool_details.get(tool_use_id, {}).values()))
    in_progress = broker._serialize_in_progress_turn()
    if in_progress is not None:
        sources.append(in_progress.get("parts", []))

    for parts in sources:
        for block in parts:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("id") == tool_use_id:
                candidate = block.get("input")
                if candidate is not None and not is_elided_input(candidate):
                    found_input = candidate
            elif block.get("type") == "tool_result" and block.get("tool_use_id") == tool_use_id:
                found_result = block
        if found_result is not None and found_input is not None:
            break

    if found_result is not None or found_input is not None:
        out = {
            "tool_use_id": tool_use_id,
            "content": (found_result or {}).get("content", ""),
            "is_error": bool((found_result or {}).get("is_error", False)),
        }
        if found_input is not None:
            out["input"] = found_input
        return out

    raise HTTPException(status_code=404, detail=f"tool_result not found: {tool_use_id}")


@app.get("/api/conversation/tool-result/{tool_use_id}/files/{file_uuid}")
async def download_send_user_file(tool_use_id: str, file_uuid: str) -> FileResponse:
    """Serve a file from a matched native ``SendUserFile`` call/result pair."""
    try:
        canonical_uuid = str(uuid.UUID(file_uuid))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=404, detail="SendUserFile attachment not found") from None
    if canonical_uuid != file_uuid.lower():
        raise HTTPException(status_code=404, detail="SendUserFile attachment not found")

    sources = [turn.parts for turn in broker._conversation_turns]
    in_progress = broker._serialize_in_progress_turn()
    if in_progress is not None:
        sources.append(in_progress.get("parts", []))

    call: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    for parts in sources:
        for block in parts:
            if not isinstance(block, dict):
                continue
            if (
                block.get("type") == "tool_use"
                and block.get("id") == tool_use_id
                and str(block.get("name") or "").lower() == "senduserfile"
            ):
                call = block
            elif (
                block.get("type") == "tool_result"
                and block.get("tool_use_id") == tool_use_id
                and not bool(block.get("is_error", False))
            ):
                result = block

    if call is None or result is None:
        raise HTTPException(status_code=404, detail="SendUserFile attachment not found")

    payload = result.get("content", {})
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = {}
    attachments = payload.get("attachments", []) if isinstance(payload, dict) else []
    attachment = next(
        (
            item
            for item in attachments
            if isinstance(item, dict) and str(item.get("file_uuid") or "").lower() == canonical_uuid
        ),
        None,
    )
    if attachment is None:
        raise HTTPException(status_code=404, detail="SendUserFile attachment not found")

    raw_path = attachment.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        raise HTTPException(status_code=404, detail="SendUserFile attachment not found")

    call_input = call.get("input") if isinstance(call.get("input"), dict) else {}
    requested = call_input.get("files", [])
    if not isinstance(requested, list) or raw_path not in requested:
        raise HTTPException(status_code=404, detail="SendUserFile attachment not found")

    try:
        resolved = Path(raw_path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        raise HTTPException(status_code=410, detail="file no longer available") from None
    if not resolved.is_file():
        raise HTTPException(status_code=410, detail="file no longer available")
    if resolved.stat().st_size > broker._settings.max_presented_file_bytes:
        raise HTTPException(status_code=413, detail="file exceeds max_presented_file_bytes")

    media_type = str(attachment.get("media_type") or "").strip()
    if not media_type:
        media_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
    return FileResponse(resolved, filename=resolved.name, media_type=media_type)


# --- Capabilities API ---


class _SlashCommandRequest(BaseModel):
    """Request body for sending a slash command to the active transport."""

    command: str
    arguments: str = ""
    pane_id: str = ""


@app.get("/api/capabilities")
async def get_capabilities() -> dict:
    """Return transport capabilities so the frontend knows which controls to render."""
    if not broker._transport:
        raise HTTPException(status_code=503, detail="Transport not initialized")
    payload = asdict(broker._transport.capabilities)
    payload["room_prompt_resend"] = broker._room_bridge is not None
    return payload


@app.get("/api/plan")
async def get_plan() -> dict:
    """Claude's current plan/task list (from its TodoWrite tool), or empty if none."""
    plan = broker._current_plan
    if plan is None:
        return {"tasks": [], "counts": {"total": 0}}
    return {"tasks": plan.get("tasks", []), "counts": plan.get("counts", {})}


@app.get("/api/runtime-options")
async def get_runtime_options(refresh: bool = False) -> dict:
    """Native, session-scoped model/effort/tier catalog; never the TUI menu."""
    if not broker._transport:
        raise HTTPException(status_code=503, detail="Transport not initialized")
    if not broker._transport.capabilities.runtime_options:
        raise HTTPException(status_code=409, detail="Runtime options not supported")
    try:
        return await broker._transport.get_runtime_options(refresh=refresh)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class _RuntimeOptionsRequest(BaseModel):
    options: dict[str, Any]
    request_id: str | None = None


@app.post("/api/runtime-options")
async def set_runtime_options(body: _RuntimeOptionsRequest) -> dict:
    """Acknowledge next-turn settings, not a changed in-flight model or completed turn."""
    if not broker._transport:
        raise HTTPException(status_code=503, detail="Transport not initialized")
    if not broker._transport.capabilities.runtime_options:
        raise HTTPException(status_code=409, detail="Runtime options not supported")
    try:
        return await broker.handle_runtime_options(body.options, request_id=body.request_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/agents")
async def get_agents(include_finished: bool = False) -> dict:
    """Return active agents, optionally including recently completed subagents."""
    broker._reap_dead_teammates()
    agents = [broker._enrich_agent_row(agent) for agent in broker._running_agents.values()]
    if include_finished:
        agents.extend(broker._finished_agents)
    return {"agents": agents}


@app.get("/api/slash-commands")
async def get_slash_commands(refresh: bool = Query(True)) -> dict[str, Any]:
    """Return slash commands available in the active CLI session."""
    if not broker._transport:
        raise HTTPException(status_code=503, detail="Transport not initialized")
    commands = await broker.discover_slash_commands(refresh=refresh)
    return {"commands": commands, "count": len(commands)}


@app.post("/api/slash-commands/send")
async def send_slash_command(body: _SlashCommandRequest) -> dict[str, str]:
    """Dispatch a supported native action; `sent` is acceptance, not turn completion."""
    if not broker._transport:
        raise HTTPException(status_code=503, detail="Transport not initialized")
    command_text = "/" + body.command.strip().lstrip("/") + " " + body.arguments
    from skuld.effort import effort_argument

    argument = effort_argument(command_text)
    if argument is not None:
        try:
            await broker.handle_effort(argument)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"status": "sent", "command": "/effort"}
    if not broker._transport.capabilities.slash_commands:
        raise HTTPException(status_code=501, detail="Slash commands not supported")
    command = body.command.strip()
    if not command:
        raise HTTPException(status_code=400, detail="Command is required")
    try:
        await broker._transport.send_control(
            "slash_command",
            command=command,
            arguments=body.arguments,
            pane_id=body.pane_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (RuntimeError, TimeoutError) as exc:
        # A timeout may have crossed the native boundary. Never retry a command
        # automatically (fork/review/compact may already have been accepted).
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    name = command.split(maxsplit=1)[0]
    if not name.startswith("/"):
        name = f"/{name}"
    return {"status": "sent", "command": name}


@app.post("/api/claude/hooks")
async def receive_claude_hook(payload: dict[str, Any]) -> dict[str, bool]:
    """Receive Claude Code HTTP hook callbacks for interactive tmux sessions."""
    await broker.handle_claude_hook(payload)
    return {"ok": True}


# --- Service Management API ---


@app.post("/api/services", response_model=ServiceStatus)
async def create_service(request: ServiceCreateRequest) -> ServiceStatus:
    """Start a new local service."""
    if not broker.service_manager:
        raise HTTPException(status_code=503, detail="Service manager not initialized")
    return await broker.service_manager.add_service(request)


@app.get("/api/services", response_model=list[ServiceStatus])
async def list_services() -> list[ServiceStatus]:
    """List all local services."""
    if not broker.service_manager:
        raise HTTPException(status_code=503, detail="Service manager not initialized")
    return await broker.service_manager.list_services()


@app.get("/api/services/{name}", response_model=ServiceStatus)
async def get_service(name: str) -> ServiceStatus:
    """Get status of a specific service."""
    if not broker.service_manager:
        raise HTTPException(status_code=503, detail="Service manager not initialized")

    result = await broker.service_manager.get_service(name)
    if not result:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")
    return result


@app.delete("/api/services/{name}")
async def delete_service(name: str) -> dict:
    """Stop and remove a local service."""
    if not broker.service_manager:
        raise HTTPException(status_code=503, detail="Service manager not initialized")

    removed = await broker.service_manager.remove_service(name)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")
    return {"status": "removed", "name": name}


@app.get("/api/services/{name}/logs")
async def get_service_logs(name: str, lines: int = 100) -> dict:
    """Get logs for a service."""
    if not broker.service_manager:
        raise HTTPException(status_code=503, detail="Service manager not initialized")

    logs = await broker.service_manager.get_logs(name, lines=lines)
    if logs is None:
        raise HTTPException(status_code=404, detail=f"No logs found for service '{name}'")
    return {"name": name, "lines": lines, "logs": logs}


@app.post("/api/services/{name}/restart", response_model=ServiceStatus)
async def restart_service(name: str) -> ServiceStatus:
    """Restart a local service."""
    if not broker.service_manager:
        raise HTTPException(status_code=503, detail="Service manager not initialized")

    result = await broker.service_manager.restart_service(name)
    if not result:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")
    return result


register_file_routes(app, lambda: broker)

# --------------------------------------------------------------------------- present-file
#
# The `present-file <path>` command (engine-agnostic PATH shim) lets a Forge agent hand the user a
# file from its session workspace or home. The broker stages a copy under a broker-owned directory
# on the persistence mount, mints an opaque file_id, and emits a self-contained `conversation.turn`
# (durable through PASS-1 rebuild and live broadcast). Both source and staging paths are resolved
# beneath their explicit roots, and the blob is served by opaque id rather than a client path. This
# is the in-app analogue of the harness SendUserFile tool.

_PRESENTED_ID_RE = re.compile(r"^pf_[0-9a-f]{32}$")
# file_id -> staged realpath. Rebuilt from the self-describing staging dir on startup.
_presented_registry: dict[str, str] = {}


def _presented_staging_dir() -> Path:
    """Broker-owned staging root, OUTSIDE the workspace (keeps the git tree pristine) and on the
    session home mount (survives broker restart)."""
    home = Path(broker._settings.home_path).resolve(strict=True)
    return resolve_contained_path(home, ".forge-presented")


def _rebuild_presented_registry() -> None:
    """Repopulate the file_id -> staged-path registry from the self-describing staging dir
    ({staging}/{file_id}/{basename}) so present-file cards keep resolving after a broker restart."""
    _presented_registry.clear()
    try:
        root = _presented_staging_dir()
        if not root.is_dir():
            return
        for entry in root.iterdir():
            if entry.is_symlink() or not entry.is_dir():
                continue
            if not _PRESENTED_ID_RE.fullmatch(entry.name):
                continue
            try:
                safe_entry = resolve_contained_path(root, entry.name, strict=True)
            except UnsafePathError:
                continue
            files = [
                item for item in safe_entry.iterdir() if item.is_file() and not item.is_symlink()
            ]
            if files:
                _presented_registry[entry.name] = str(files[0])
    except (OSError, UnsafePathError) as exc:  # pragma: no cover - best-effort recovery
        logger.warning("present-file: registry rebuild failed: %s", repr(exc))


@app.post("/api/present-file")
async def present_file(body: dict) -> dict:
    """Stage a host file, emit a durable present_file turn, and return its id/metadata.

    Loopback, in-session agent only (the `present-file` shim curls this). Body:
    ``{ "path": "<absolute host path>", "caption"?: str, "title"?: str }``.
    """
    raw_path = str((body or {}).get("path") or "").strip()
    if not raw_path:
        raise HTTPException(400, "path is required")
    caption = str((body or {}).get("caption") or "").strip() or None
    title = str((body or {}).get("title") or "").strip() or None

    workspace_root = os.path.realpath(os.path.abspath(broker.workspace_dir))
    home_root = os.path.realpath(os.path.abspath(broker._settings.home_path))
    candidate_source = os.path.realpath(os.path.abspath(os.path.expanduser(raw_path)))
    workspace_prefix = workspace_root.rstrip(os.sep) + os.sep
    home_prefix = home_root.rstrip(os.sep) + os.sep
    if candidate_source == workspace_root:
        safe_source = Path(workspace_root)
    elif candidate_source.startswith(workspace_prefix):
        safe_source = Path(candidate_source)
    elif candidate_source == home_root:
        safe_source = Path(home_root)
    elif candidate_source.startswith(home_prefix):
        safe_source = Path(candidate_source)
    else:
        raise HTTPException(400, "path is outside the session roots")
    if not safe_source.exists():
        raise HTTPException(404, "no such file")
    if not safe_source.is_file():
        raise HTTPException(400, "not a regular file")
    size = safe_source.stat().st_size
    if size > broker._settings.max_presented_file_bytes:
        raise HTTPException(413, "file exceeds max_presented_file_bytes")

    file_id = "pf_" + uuid.uuid4().hex
    name = title or safe_source.name
    mime = mimetypes.guess_type(safe_source.name)[0] or "application/octet-stream"

    # Stage a COPY so a later /tmp GC or edit cannot change/lose what the user tapped.
    try:
        dest_dir = _presented_staging_dir() / file_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = resolve_contained_path(dest_dir, "content", allow_root=False)
        shutil.copyfile(safe_source, dest)
    except (OSError, UnsafePathError) as exc:
        logger.warning("present-file: staging copy failed: %s", repr(exc))
        raise HTTPException(500, "could not stage file") from None
    _presented_registry[file_id] = str(dest)

    turn = _build_present_file_turn(file_id, name, mime, size, caption)
    broker._append_turn(turn)
    await broker._channels.broadcast({"type": "conversation.turn", "turn": asdict(turn)})
    logger.info(
        "present-file: staged %s (%d bytes) as %s",
        repr(name),
        size,
        file_id,
    )
    return {"file_id": file_id, "name": name, "size": size, "mime": mime}


def _build_present_file_turn(
    file_id: str, name: str, mime: str, size: int, caption: str | None
) -> ConversationTurn:
    """Build a turn shared by live memory, durable log, and broadcast paths."""
    tool_input: dict = {"file_id": file_id, "name": name, "mime": mime, "size": size}
    if caption:
        tool_input["caption"] = caption
    part = {"id": file_id, "name": "present_file", "type": "tool_use", "input": tool_input}
    return ConversationTurn(
        id=str(uuid.uuid4()),
        role="assistant",
        parts=[part],
        content=caption or name,
        metadata={"cost": None, "model": None, "usage": {}, "present_file": True},
    )


@app.get("/api/files/presented/{file_id}")
async def download_presented_file(file_id: str) -> FileResponse:
    """Serve a staged presented file by OPAQUE id (never a client path → traversal-safe)."""
    if not _PRESENTED_ID_RE.fullmatch(file_id):
        raise HTTPException(400, "invalid file_id")
    path = _presented_registry.get(file_id)
    if path is None:
        _rebuild_presented_registry()
        path = _presented_registry.get(file_id)
    if path is None:
        raise HTTPException(404, "unknown file_id")
    try:
        safe_path = resolve_path_in_roots(path, (_presented_staging_dir(),))
    except UnsafePathError:
        raise HTTPException(410, "file no longer available") from None
    if safe_path.is_symlink() or not safe_path.is_file():
        raise HTTPException(410, "file no longer available")
    return FileResponse(
        path=safe_path,
        filename=safe_path.name,
        media_type=mimetypes.guess_type(safe_path.name)[0] or "application/octet-stream",
    )


class _SendMessageRequest(BaseModel):
    """Request body for session-to-session messaging."""

    session_id: str
    content: str


class _RoomMessageRequest(BaseModel):
    """Request body for a human message entering a room session."""

    content: str
    source: str = "external"
    participant_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    deliver_to_transport: bool = Field(
        default=True,
        description=(
            "Also hand the message to the broker's own CLI transport. Set false "
            "for room commentary addressed to no member, which would otherwise "
            "lazy-start a transport that has nothing to answer."
        ),
    )


class _DirectedRoomMessageRequest(BaseModel):
    """Request body for a directed human message entering a room session."""

    target_peer_id: str
    content: str
    source: str = "external"
    participant_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class _ResendPromptRequest(BaseModel):
    """Request body for resending the configured initial prompt into a room."""

    source: str = "external"
    metadata: dict[str, Any] = Field(default_factory=dict)


class _RoomWorkflowEventRequest(BaseModel):
    """A deterministic event injected into the active workflow mesh."""

    event_type: str = Field(min_length=1)
    content: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    request_id: str = Field(min_length=1, max_length=200)
    source: str = "ting"


class _RoomJoinRequest(BaseModel):
    """Request body for joining a live Environment room."""

    participant_id: str
    display_name: str = ""
    environment_id: str
    role: str = "observer"
    room_id: str = ""
    capabilities: list[str] = Field(default_factory=list)
    surfaces: list[str] = Field(default_factory=lambda: ["skuld.room"])
    environment_action_authorities: list[str] = Field(default_factory=list)


class _RoomHeartbeatRequest(BaseModel):
    """Request body for human Environment heartbeat/state updates."""

    participant_id: str
    status: str | None = None
    wakefulness: str | None = None
    attention_state: str | None = None


class _RoomLeaveRequest(BaseModel):
    """Request body for leaving a live Environment room."""

    participant_id: str
    reason: str = "left"


class _RoomCloseRequest(BaseModel):
    """Request body for closing a live Environment huddle."""

    room_id: str
    reason: str = "closed"
    summary: str = ""


class _RoomCapabilityRequest(BaseModel):
    """Request body for verifying a participant control capability."""

    participant_id: str
    capability: str


class _WorkflowGateResolveRequest(BaseModel):
    """Request body for resolving a pending workflow gate."""

    decision: str
    notes: str = ""
    source: str = "human"


@app.post("/api/message")
async def send_message_to_session(body: _SendMessageRequest) -> dict:
    """Send a message to another session via Volundr's WS proxy.

    The broker injects its own auth token so the calling session
    never sees credentials.
    """
    if not broker.volundr_api_url:
        raise HTTPException(503, "Volundr API URL not configured")

    client = await broker._get_http_client()
    url = f"{FORGE_SESSIONS_PATH}/{body.session_id}/messages"
    try:
        resp = await client.post(url, json={"content": body.content})
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, f"Volundr error: {e.response.text[:500]}")
    except httpx.RequestError as e:
        raise HTTPException(502, f"Failed to reach Volundr: {e}")

    return {"status": "sent", "target_session_id": body.session_id}


@app.post("/api/room/message")
async def send_room_message(body: _RoomMessageRequest) -> dict:
    """Inject a human-originated message into the active room session."""
    try:
        message_id = await broker.handle_human_room_message(
            body.content,
            source=body.source,
            participant_id=body.participant_id,
            metadata=body.metadata,
            deliver_to_transport=body.deliver_to_transport,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    except LookupError as exc:
        raise HTTPException(404, str(exc))

    return {"status": "sent", "message_id": message_id}


@app.post("/api/room/direct")
async def send_directed_room_message(body: _DirectedRoomMessageRequest) -> dict:
    """Inject a human-originated directed room message."""
    try:
        message_id = await broker.handle_directed_room_message(
            body.target_peer_id,
            body.content,
            source=body.source,
            metadata={**body.metadata, "participant_id": body.participant_id}
            if body.participant_id
            else body.metadata,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    except LookupError as exc:
        raise HTTPException(404, str(exc))

    return {"status": "sent", "message_id": message_id}


@app.post("/api/room/resend-prompt")
async def resend_room_initial_prompt(body: _ResendPromptRequest) -> dict:
    """Resend the configured initial prompt into the active room/flock session."""
    try:
        message_id = await broker.handle_resend_initial_prompt(
            source=body.source,
            metadata=body.metadata,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))

    return {"status": "sent", "message_id": message_id}


@app.post("/api/room/workflow-events")
async def publish_room_workflow_event(body: _RoomWorkflowEventRequest) -> dict:
    """Publish a typed continuation into the mesh with a stable event identity."""
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", body.request_id):
        raise HTTPException(400, "Invalid request_id")
    try:
        event_id = await broker.handle_publish_mesh_event(
            body.event_type,
            body.content,
            source=body.source,
            payload=body.payload,
            request_id=body.request_id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"status": "published", "event_id": event_id, "event_type": body.event_type}


@app.post("/api/room/join")
async def join_room(body: _RoomJoinRequest) -> dict:
    """Join a human participant to a live Valkyrie Environment."""
    try:
        participant = await broker.join_human_environment(
            participant_id=body.participant_id,
            display_name=body.display_name,
            environment_id=body.environment_id,
            role=body.role,
            room_id=body.room_id,
            capabilities=body.capabilities or None,
            surfaces=body.surfaces or None,
            environment_action_authorities=body.environment_action_authorities,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    except PermissionError as exc:
        raise HTTPException(403, str(exc))

    return {"status": "joined", "participant": participant}


@app.post("/api/room/heartbeat")
async def heartbeat_room(body: _RoomHeartbeatRequest) -> dict:
    """Update human participant presence in a live Valkyrie Environment."""
    try:
        participant = await broker.heartbeat_human_environment(
            participant_id=body.participant_id,
            status=body.status,
            wakefulness=body.wakefulness,
            attention_state=body.attention_state,
        )
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    except LookupError as exc:
        raise HTTPException(404, str(exc))

    return {"status": "ok", "participant": participant}


@app.post("/api/room/leave")
async def leave_room(body: _RoomLeaveRequest) -> dict:
    """Leave a human participant from a live Valkyrie Environment."""
    try:
        await broker.leave_human_environment(
            participant_id=body.participant_id,
            reason=body.reason,
        )
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except PermissionError as exc:
        raise HTTPException(403, str(exc))

    return {"status": "left", "participant_id": body.participant_id}


@app.post("/api/room/close")
async def close_room(body: _RoomCloseRequest) -> dict:
    """Close a live Environment huddle, publishing its transcript for archival."""
    try:
        closed = await broker.close_environment_huddle(
            room_id=body.room_id,
            reason=body.reason,
            summary=body.summary,
        )
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    return {"status": "closed", **closed}


@app.post("/api/room/require-capability")
async def require_room_capability(body: _RoomCapabilityRequest) -> dict:
    """Verify a joined participant holds a control capability (403 otherwise)."""
    try:
        broker.require_room_capability(body.participant_id, body.capability)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    return {"status": "ok", "participant_id": body.participant_id, "capability": body.capability}


@app.get("/api/room/participants")
async def get_room_participants(environment_id: str | None = Query(default=None)) -> dict:
    """Return the current participants in the active room session."""
    return {"participants": broker.get_room_participants(environment_id=environment_id)}


@app.get("/api/workflow/gates")
async def get_workflow_gates() -> dict:
    """Return native workflow gate states for the active session."""
    return {"gates": broker.list_workflow_gates()}


class _HelpAnswerRequest(BaseModel):
    """Request body answering a peer's pending help request."""

    answer: str
    source: str = "external"


@app.get("/api/help/requests")
async def get_help_requests() -> dict:
    """Return peer help requests (genuine agent questions) for this session."""
    return {"requests": broker.list_help_requests()}


@app.post("/api/help/requests/{request_id}/answer")
async def answer_help_request(request_id: str, body: _HelpAnswerRequest) -> dict:
    """Answer a pending help request; the answer routes to the asking peer."""
    try:
        message_id = await broker.answer_help_request(
            request_id,
            body.answer,
            source=body.source,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"status": "answered", "message_id": message_id}


@app.post("/api/workflow/gates/{gate_id}/resolve")
async def resolve_workflow_gate(
    request: Request,
    gate_id: str,
    body: _WorkflowGateResolveRequest,
) -> dict:
    """Resolve a pending native workflow gate and publish its outcome."""
    if (
        request.headers.get(WORKFLOW_GATE_INTENT_HEADER, "").strip().lower()
        != WORKFLOW_GATE_INTENT_RESOLVE
    ):
        raise HTTPException(428, "Missing explicit workflow gate intent header")
    try:
        gate = await broker.resolve_workflow_gate(
            gate_id,
            body.decision,
            notes=body.notes,
            source=body.source,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))

    return {"status": "resolved", "gate": gate}


@app.get("/api/communication/routes")
async def get_communication_routes() -> dict:
    """Return active external communication routes for the live session."""
    return {"routes": broker.get_communication_routes()}


class _TokenRedactFilter(logging.Filter):
    """Redact bearer and channel credentials from log messages."""

    _patterns = (
        (re.compile(r"access_token=[^\s\"&]+"), "access_token=[REDACTED]"),
        (
            re.compile(r"(https?://api\.telegram\.org/bot)[^/\s\"]+"),
            r"\1[REDACTED]",
        ),
    )

    def _redact(self, value: object) -> object:
        original = value if isinstance(value, str) else str(value)
        redacted = original
        for pattern, replacement in self._patterns:
            redacted = pattern.sub(replacement, redacted)
        return redacted if redacted != original or isinstance(value, str) else value

    def filter(self, record: logging.LogRecord) -> bool:
        if hasattr(record, "msg"):
            record.msg = self._redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(self._redact(arg) for arg in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: self._redact(value) for key, value in record.args.items()}
        return True


@app.get("/api/control-state")
async def get_control_state() -> dict:
    """Expose live controls and retained, explicitly non-answerable recovery cards."""
    return {
        "questions": [
            *broker._unrestored_questions.values(),
            *broker._pending_ask_user_questions.values(),
        ],
        "permissions": [
            *broker._unrestored_permissions.values(),
            *broker._pending_permission_requests.values(),
        ],
    }
