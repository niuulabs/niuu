"""REST adapter for the durable session event log (full-fidelity transcript).

Two endpoints:
  * ``POST /sessions/{id}/log``       — append frames (producer: skuld), idempotent
  * ``GET  /sessions/{id}/log``       — cursor replay (consumers: web, iOS)

The log is the transcript source of truth. Producers append every frame with a
monotonic per-session ``seq``; consumers replay from ``?after=<seq>`` so a client
attaching at any time — including mid-turn or on a fresh device — reconstructs the
full conversation, with nothing dropped.

## Unified visibility contract (SRD FR-7 / INV-10)

The cold read applies the SAME internal-visibility gate as the live broadcast and
the paced replay: internal ``tool_use``/``tool_result`` blocks are filtered out by
the SHARED :func:`skuld.channels.filter_internal_blocks` predicate, with the SAME
default (``show_internal`` HIDDEN unless asked) and the SAME toggle semantics
(``?show_internal=true`` here == the ``set_internal_visibility`` wire-message on the
streaming paths). With internals hidden, the dropped frame set is identical across
live, replay, and this cold read for the same data. The raw-frame envelope (seq,
kind, role, request_id, payload, ts) is preserved verbatim for frames that pass the
gate; only the ``payload`` is filtered (and a frame whose payload is wholly internal
is dropped entirely, exactly as on the streaming paths).
"""

import logging
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, Query, Request, status
from pydantic import BaseModel, Field

from niuu.domain.transcript_reducer import NON_BROADCAST_KINDS
from skuld.channels import filter_internal_blocks
from volundr.domain.models import SessionLogEntry
from volundr.domain.ports import SessionEventLogRepository
from volundr.domain.services.session import SessionAccessDeniedError, SessionService

logger = logging.getLogger(__name__)

MAX_LOG_BATCH = 1000
DEFAULT_REPLAY_LIMIT = 1000
MAX_REPLAY_LIMIT = 5000
MAX_KIND_LENGTH = 64

# Unified read-path visibility default (SRD FR-7 / INV-10): internal blocks are
# HIDDEN by default on every read path. Kept in lock-step with the live
# ``WebSocketChannel(show_internal=False)`` default and ``ReplayConfig`` — the
# composition root passes ``settings.replay.default_show_internal`` so all three
# read paths share ONE configured default.
DEFAULT_SHOW_INTERNAL = False


def _gate_entries(entries: list[SessionLogEntry], *, show_internal: bool) -> list[SessionLogEntry]:
    """Apply the shared internal-visibility filter to a batch of raw frames.

    Reuses the exact ``filter_internal_blocks`` predicate the live broadcast and
    the paced replay use, threading the per-stream ``open_block_type`` so a
    ``content_block_delta``/``content_block_stop`` belonging to an internal block
    is dropped identically. A frame whose payload is wholly internal is dropped;
    a frame with mixed content keeps its non-internal blocks. The raw envelope is
    otherwise preserved verbatim.
    """
    if show_internal:
        return entries
    gated: list[SessionLogEntry] = []
    open_block_type: str | None = None
    for entry in entries:
        filtered, open_block_type = filter_internal_blocks(
            entry.payload, open_block_type=open_block_type
        )
        if filtered is None:
            continue
        gated.append(entry if filtered is entry.payload else replace(entry, payload=filtered))
    return gated


class LogEntryIngest(BaseModel):
    """A single full-fidelity frame submitted by the producer."""

    seq: int = Field(..., ge=0, description="Monotonic per-session sequence number")
    kind: str = Field(
        ...,
        min_length=1,
        max_length=MAX_KIND_LENGTH,
        description="Wire frame type (assistant, content_block_delta, tool_use, ...)",
    )
    payload: dict = Field(default_factory=dict, description="Raw frame, preserved verbatim")
    role: str | None = Field(default=None, max_length=32)
    request_id: str | None = Field(default=None, description="Turn correlation id")
    ts: datetime | None = Field(default=None, description="Frame timestamp (defaults to now)")


class LogBatchRequest(BaseModel):
    """Batch of frames to append (producer retries are idempotent on seq)."""

    entries: list[LogEntryIngest] = Field(..., min_length=1, max_length=MAX_LOG_BATCH)


class LogAppendResponse(BaseModel):
    """Result of an append: how many submitted and the new cursor head."""

    submitted: int = Field(description="Number of entries submitted")
    latest_seq: int = Field(description="Highest seq now stored for the session")


class LogHeadResponse(BaseModel):
    """Cursor head for a session's log (highest seq stored)."""

    latest_seq: int = Field(description="Highest seq stored for the session, 0 if none")


class SessionLogEntryResponse(BaseModel):
    """A single replayed frame."""

    session_id: UUID
    seq: int
    kind: str
    role: str | None = None
    request_id: str | None = None
    payload: dict
    ts: str

    @classmethod
    def from_entry(cls, entry: SessionLogEntry) -> "SessionLogEntryResponse":
        return cls(
            session_id=entry.session_id,
            seq=entry.seq,
            kind=entry.kind,
            role=entry.role,
            request_id=entry.request_id,
            payload=entry.payload,
            ts=entry.ts.isoformat(),
        )


def create_session_log_router(
    log_repository: SessionEventLogRepository,
    session_service: SessionService | None = None,
    *,
    prefix: str = "/api/v1/forge",
    default_show_internal: bool = DEFAULT_SHOW_INTERNAL,
) -> APIRouter:
    """Create the FastAPI router for the durable session event log.

    ``default_show_internal`` is the unified read-path visibility default (SRD
    FR-7 / INV-10); the composition root threads ``ReplayConfig`` so cold-read,
    replay, and live all share ONE configured default.
    """
    router = APIRouter(prefix=prefix)

    async def _check_access(request: Request, session_id: UUID, action: str) -> None:
        if session_service is None:
            return
        from volundr.adapters.inbound.auth import extract_principal

        principal = await extract_principal(request)
        session = await session_service.get_session(session_id)
        if session is None:
            return
        try:
            await session_service._check_access(session, principal, action)
        except SessionAccessDeniedError:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Not authorized to access the event log for session {session_id}",
            )

    @router.post(
        "/sessions/{session_id}/log",
        response_model=LogAppendResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["Events"],
    )
    async def append_log(
        request: Request,
        data: LogBatchRequest,
        session_id: UUID = Path(description="Session UUID to append frames for"),
    ) -> LogAppendResponse:
        """Append full-fidelity frames to the session's durable log (idempotent)."""
        await _check_access(request, session_id, "emit_event")
        now = datetime.now(UTC)
        entries = [
            SessionLogEntry(
                session_id=session_id,
                seq=item.seq,
                kind=item.kind,
                payload=item.payload,
                ts=item.ts or now,
                role=item.role,
                request_id=item.request_id,
            )
            for item in data.entries
        ]
        submitted = await log_repository.append(entries)
        latest = await log_repository.latest_seq(session_id)
        return LogAppendResponse(submitted=submitted, latest_seq=latest)

    @router.get(
        "/sessions/{session_id}/log/head",
        response_model=LogHeadResponse,
        tags=["Events"],
    )
    async def log_head(
        request: Request,
        session_id: UUID = Path(description="Session UUID to read the log cursor head for"),
    ) -> LogHeadResponse:
        """Return the highest seq stored — lets a producer resume after restart."""
        await _check_access(request, session_id, "read")
        latest = await log_repository.latest_seq(session_id)
        return LogHeadResponse(latest_seq=latest)

    @router.get(
        "/sessions/{session_id}/log",
        response_model=list[SessionLogEntryResponse],
        tags=["Events"],
    )
    async def replay_log(
        request: Request,
        session_id: UUID = Path(description="Session UUID to replay the log for"),
        after: int = Query(
            default=0,
            ge=0,
            description="Return frames with seq greater than this cursor",
        ),
        limit: int = Query(
            default=DEFAULT_REPLAY_LIMIT,
            ge=1,
            le=MAX_REPLAY_LIMIT,
            description="Maximum number of frames to return",
        ),
        show_internal: bool = Query(
            default=default_show_internal,
            description=(
                "Include internal tool_use/tool_result blocks. Default matches the "
                "unified live/replay default (hidden); set true to unhide — the same "
                "filter the live and replay paths apply (SRD FR-7 / INV-10)."
            ),
        ),
    ) -> list[SessionLogEntryResponse]:
        """Replay the session transcript from a cursor (full fidelity), gated by the
        same internal-visibility filter as the live and replay read paths."""
        await _check_access(request, session_id, "read")
        entries = await log_repository.read_after(session_id, after_seq=after, limit=limit)
        # Drop synthetic reducer-seed rows (e.g. conversation.turn) from the RAW
        # wire stream ALWAYS — independent of show_internal. They are never
        # broadcast live, so surfacing them here would break literal frame-for-frame
        # live==replay==cold equality (SRD INV-5). They remain in the durable log
        # (read_after) and still authoritatively drive the reduce/rebuild path.
        streamable = [e for e in entries if e.kind not in NON_BROADCAST_KINDS]
        gated = _gate_entries(streamable, show_internal=show_internal)
        return [SessionLogEntryResponse.from_entry(e) for e in gated]

    return router
