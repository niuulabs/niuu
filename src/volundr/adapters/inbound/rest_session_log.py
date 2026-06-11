"""REST adapter for the durable session event log (full-fidelity transcript).

Two endpoints:
  * ``POST /sessions/{id}/log``       — append frames (producer: skuld), idempotent
  * ``GET  /sessions/{id}/log``       — cursor replay (consumers: web, iOS)

The log is the transcript source of truth. Producers append every frame with a
monotonic per-session ``seq``; consumers replay from ``?after=<seq>`` so a client
attaching at any time — including mid-turn or on a fresh device — reconstructs the
full conversation, with nothing dropped.
"""

import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, Query, Request, status
from pydantic import BaseModel, Field

from volundr.domain.models import SessionLogEntry
from volundr.domain.ports import SessionEventLogRepository
from volundr.domain.services.session import SessionAccessDeniedError, SessionService

logger = logging.getLogger(__name__)

MAX_LOG_BATCH = 1000
DEFAULT_REPLAY_LIMIT = 1000
MAX_REPLAY_LIMIT = 5000
MAX_KIND_LENGTH = 64


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
) -> APIRouter:
    """Create the FastAPI router for the durable session event log."""
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
    ) -> list[SessionLogEntryResponse]:
        """Replay the session transcript from a cursor (full fidelity)."""
        await _check_access(request, session_id, "read")
        entries = await log_repository.read_after(session_id, after_seq=after, limit=limit)
        return [SessionLogEntryResponse.from_entry(e) for e in entries]

    return router
