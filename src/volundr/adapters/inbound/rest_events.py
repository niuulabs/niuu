"""REST adapter for the session event pipeline."""

import logging
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Path, Query, Request, status
from pydantic import BaseModel, Field

from volundr.domain.models import SessionEvent, SessionEventType
from volundr.domain.ports import SessionEventRepository
from volundr.domain.services.event_ingestion import EventIngestionService
from volundr.domain.services.session import SessionAccessDeniedError, SessionService

logger = logging.getLogger(__name__)


# -- Request / Response models ------------------------------------------------


class EventIngestRequest(BaseModel):
    """Single event submission from a Skuld pod."""

    session_id: UUID = Field(
        description="Session this event belongs to",
        examples=["550e8400-e29b-41d4-a716-446655440000"],
    )
    event_type: str = Field(
        ...,
        min_length=1,
        description="Event type (e.g. message_user, file_modified)",
        examples=["message_user"],
    )
    timestamp: datetime = Field(
        description="When the event occurred",
        examples=["2025-01-15T10:30:00Z"],
    )
    data: dict = Field(
        default_factory=dict,
        description="Event-type-specific payload",
        examples=[{"content": "Hello"}],
    )
    sequence: int = Field(
        ...,
        ge=0,
        description="Monotonic event sequence number",
        examples=[0],
    )
    tokens_in: int | None = Field(
        default=None,
        ge=0,
        description="Input tokens (token_usage events)",
        examples=[150],
    )
    tokens_out: int | None = Field(
        default=None,
        ge=0,
        description="Output tokens (token_usage events)",
        examples=[75],
    )
    cost: float | None = Field(
        default=None,
        ge=0,
        description="Cost in USD (token_usage events)",
        examples=[0.0025],
    )
    duration_ms: int | None = Field(
        default=None,
        ge=0,
        description="Duration in milliseconds (tool/terminal events)",
        examples=[1200],
    )
    model: str | None = Field(
        default=None,
        max_length=100,
        description="LLM model identifier (token_usage events)",
        examples=["claude-sonnet-4-6"],
    )


class EventBatchRequest(BaseModel):
    """Batch event submission."""

    events: list[EventIngestRequest] = Field(
        ...,
        min_length=1,
        max_length=500,
        description="List of events to ingest (max 500 per batch)",
    )


class SessionEventResponse(BaseModel):
    """Response model for a session event."""

    id: UUID = Field(
        description="Unique event identifier",
        examples=["550e8400-e29b-41d4-a716-446655440000"],
    )
    session_id: UUID = Field(
        description="Session this event belongs to",
        examples=["660e8400-e29b-41d4-a716-446655440000"],
    )
    event_type: str = Field(description="Event type", examples=["message_user"])
    timestamp: str = Field(
        description="ISO 8601 event timestamp", examples=["2025-01-15T10:30:00Z"]
    )
    data: dict = Field(description="Event-type-specific payload", examples=[{"content": "Hello"}])
    sequence: int = Field(description="Monotonic sequence number", examples=[0])
    tokens_in: int | None = Field(
        default=None,
        description="Input tokens consumed",
        examples=[150],
    )
    tokens_out: int | None = Field(
        default=None,
        description="Output tokens generated",
        examples=[75],
    )
    cost: float | None = Field(
        default=None,
        description="Cost in USD",
        examples=[0.0025],
    )
    duration_ms: int | None = Field(
        default=None,
        description="Duration in milliseconds",
        examples=[1200],
    )
    model: str | None = Field(
        default=None,
        description="LLM model identifier",
        examples=["claude-sonnet-4-6"],
    )

    @classmethod
    def from_event(cls, event: SessionEvent) -> "SessionEventResponse":
        return cls(
            id=event.id,
            session_id=event.session_id,
            event_type=event.event_type.value,
            timestamp=event.timestamp.isoformat(),
            data=event.data,
            sequence=event.sequence,
            tokens_in=event.tokens_in,
            tokens_out=event.tokens_out,
            cost=float(event.cost) if event.cost is not None else None,
            duration_ms=event.duration_ms,
            model=event.model,
        )


class SinkHealthResponse(BaseModel):
    """Health status of all event sinks."""

    sinks: dict[str, bool] = Field(
        description="Map of sink name to healthy status",
        examples=[{"postgres": True, "websocket": True}],
    )


# -- Router factory -----------------------------------------------------------


def create_events_router(
    ingestion_service: EventIngestionService,
    event_repository: SessionEventRepository,
    session_service: SessionService | None = None,
) -> APIRouter:
    """Create FastAPI router for event pipeline endpoints."""
    router = APIRouter(prefix="/api/v1/volundr")

    async def _check_event_access(
        request: Request, session_id: UUID, action: str = "emit_event"
    ) -> None:
        """Check that the caller is authorized to emit events for a session."""
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
                detail=f"Not authorized to emit events for session {session_id}",
            )

    @router.post(
        "/events",
        response_model=SessionEventResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["Events"],
    )
    async def ingest_event(request: Request, data: EventIngestRequest) -> SessionEventResponse:
        """Ingest a single session event into the pipeline."""
        await _check_event_access(request, data.session_id)
        try:
            event_type = SessionEventType(data.event_type)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Invalid event_type: {data.event_type}",
            )

        event = SessionEvent(
            id=uuid4(),
            session_id=data.session_id,
            event_type=event_type,
            timestamp=data.timestamp,
            data=data.data,
            sequence=data.sequence,
            tokens_in=data.tokens_in,
            tokens_out=data.tokens_out,
            cost=Decimal(str(data.cost)) if data.cost is not None else None,
            duration_ms=data.duration_ms,
            model=data.model,
        )
        await ingestion_service.ingest(event)
        return SessionEventResponse.from_event(event)

    @router.post(
        "/events/batch",
        response_model=list[SessionEventResponse],
        status_code=status.HTTP_201_CREATED,
        tags=["Events"],
    )
    async def ingest_event_batch(
        request: Request, data: EventBatchRequest
    ) -> list[SessionEventResponse]:
        """Ingest a batch of session events into the pipeline."""
        # Check access for all unique session IDs in the batch
        checked_sessions: set[UUID] = set()
        for item in data.events:
            if item.session_id not in checked_sessions:
                await _check_event_access(request, item.session_id)
                checked_sessions.add(item.session_id)

        events: list[SessionEvent] = []
        for item in data.events:
            try:
                event_type = SessionEventType(item.event_type)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"Invalid event_type: {item.event_type}",
                )
            events.append(
                SessionEvent(
                    id=uuid4(),
                    session_id=item.session_id,
                    event_type=event_type,
                    timestamp=item.timestamp,
                    data=item.data,
                    sequence=item.sequence,
                    tokens_in=item.tokens_in,
                    tokens_out=item.tokens_out,
                    cost=Decimal(str(item.cost)) if item.cost is not None else None,
                    duration_ms=item.duration_ms,
                    model=item.model,
                )
            )
        await ingestion_service.ingest_batch(events)
        return [SessionEventResponse.from_event(e) for e in events]

    @router.get(
        "/sessions/{session_id}/events",
        response_model=list[SessionEventResponse],
        tags=["Events"],
    )
    async def get_session_events(
        session_id: UUID = Path(description="Session UUID to query events for"),
        event_type: str | None = Query(
            default=None,
            description="Filter by event type (e.g. message_user)",
        ),
        after: datetime | None = Query(
            default=None,
            description="Return events after this ISO 8601 timestamp",
        ),
        before: datetime | None = Query(
            default=None,
            description="Return events before this ISO 8601 timestamp",
        ),
        limit: int = Query(
            default=200,
            ge=1,
            le=2000,
            description="Maximum number of events to return",
        ),
        offset: int = Query(
            default=0,
            ge=0,
            description="Number of events to skip for pagination",
        ),
    ) -> list[SessionEventResponse]:
        """Query events for a session."""
        types = None
        if event_type:
            try:
                types = [SessionEventType(event_type)]
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"Invalid event_type: {event_type}",
                )
        events = await event_repository.get_events(
            session_id,
            event_types=types,
            after=after,
            before=before,
            limit=limit,
            offset=offset,
        )
        return [SessionEventResponse.from_event(e) for e in events]

    @router.get(
        "/sessions/{session_id}/events/counts",
        response_model=dict[str, int],
        tags=["Events"],
    )
    async def get_event_counts(
        session_id: UUID = Path(description="Session UUID to get event counts for"),
    ) -> dict[str, int]:
        """Get event type counts for a session."""
        return await event_repository.get_event_counts(session_id)

    @router.get(
        "/sessions/{session_id}/events/tokens",
        response_model=list[dict],
        tags=["Events"],
    )
    async def get_token_timeline(
        session_id: UUID = Path(description="Session UUID to get token timeline for"),
        bucket_seconds: int = Query(
            default=300,
            ge=60,
            le=3600,
            description="Time bucket size in seconds for aggregation",
        ),
    ) -> list[dict]:
        """Get token burn timeline for a session."""
        return await event_repository.get_token_timeline(session_id, bucket_seconds)

    @router.get("/events/health", response_model=SinkHealthResponse, tags=["Events"])
    async def get_sink_health() -> SinkHealthResponse:
        """Get health status of all event sinks."""
        return SinkHealthResponse(sinks=ingestion_service.sink_health())

    return router
