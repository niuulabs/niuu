"""REST adapter for session trace spans."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, Request, status
from pydantic import BaseModel, Field

from volundr.domain.models import SessionSpan, SessionSpanStatus
from volundr.domain.ports import SessionSpanRepository
from volundr.domain.services.session import SessionAccessDeniedError, SessionService


def _utc_now() -> datetime:
    return datetime.now(UTC)


class SpanStartRequest(BaseModel):
    id: UUID
    session_id: UUID
    trace_id: UUID
    kind: str = Field(..., min_length=1, max_length=128)
    name: str = Field(..., min_length=1, max_length=255)
    source_service: str = Field(..., min_length=1, max_length=64)
    parent_span_id: UUID | None = None
    actor_type: str | None = Field(default=None, max_length=64)
    actor_id: str | None = Field(default=None, max_length=255)
    actor_label: str | None = Field(default=None, max_length=255)
    started_at: datetime = Field(default_factory=_utc_now)
    attributes: dict = Field(default_factory=dict)


class SpanFinishRequest(BaseModel):
    session_id: UUID
    ended_at: datetime = Field(default_factory=_utc_now)
    status: str = Field(default=SessionSpanStatus.COMPLETED.value, min_length=1, max_length=64)
    attributes: dict = Field(default_factory=dict)


class SpanCompleteRequest(SpanStartRequest):
    ended_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    status: str = Field(default=SessionSpanStatus.COMPLETED.value, min_length=1, max_length=64)


class SessionSpanResponse(BaseModel):
    id: UUID
    session_id: UUID
    trace_id: UUID
    parent_span_id: UUID | None
    kind: str
    name: str
    status: str
    started_at: str
    ended_at: str | None
    duration_ms: int | None
    actor_type: str | None
    actor_id: str | None
    actor_label: str | None
    source_service: str
    attributes: dict

    @classmethod
    def from_span(cls, span: SessionSpan) -> "SessionSpanResponse":
        return cls(
            id=span.id,
            session_id=span.session_id,
            trace_id=span.trace_id,
            parent_span_id=span.parent_span_id,
            kind=span.kind,
            name=span.name,
            status=span.status.value,
            started_at=span.started_at.isoformat(),
            ended_at=span.ended_at.isoformat() if span.ended_at is not None else None,
            duration_ms=span.duration_ms,
            actor_type=span.actor_type,
            actor_id=span.actor_id,
            actor_label=span.actor_label,
            source_service=span.source_service,
            attributes=span.attributes,
        )


class TraceLaneResponse(BaseModel):
    key: str
    label: str
    kind: str


class SessionTraceResponse(BaseModel):
    trace_id: UUID
    session_id: UUID
    started_at: str | None
    ended_at: str | None
    duration_ms: int | None
    spans: list[SessionSpanResponse]
    lanes: list[TraceLaneResponse]


class SessionTraceSummaryResponse(BaseModel):
    total_duration_ms: int | None
    provisioning_duration_ms: int
    setup_duration_ms: int
    workflow_duration_ms: int
    publish_duration_ms: int
    cleanup_duration_ms: int
    active_execution_duration_ms: int
    waiting_duration_ms: int
    turn_count: int
    tool_call_count: int
    longest_span: SessionSpanResponse | None


def _lane_key(span: SessionSpan) -> tuple[str, str, str]:
    if span.kind.startswith("session.workflow"):
        return ("workflow", f"workflow:{span.name}", span.name)
    if span.actor_type or span.actor_id or span.actor_label:
        actor_kind = span.actor_type or "actor"
        actor_label = span.actor_label or span.actor_id or actor_kind
        actor_id = span.actor_id or actor_label
        return (actor_kind, f"{actor_kind}:{actor_id}", actor_label)
    return ("system", "system", "system")


def _sum_durations(spans: Iterable[SessionSpan], predicate) -> int:
    total = 0
    for span in spans:
        if span.duration_ms is not None and predicate(span):
            total += span.duration_ms
    return total


def _trace_bounds(spans: list[SessionSpan]) -> tuple[datetime | None, datetime | None, int | None]:
    if not spans:
        return None, None, None
    root = next((span for span in spans if span.kind == "session.lifecycle"), None)
    started_at = root.started_at if root is not None else min(span.started_at for span in spans)
    ended_candidates = [span.ended_at for span in spans if span.ended_at is not None]
    ended_at = root.ended_at if root is not None and root.ended_at is not None else (
        max(ended_candidates) if ended_candidates else None
    )
    duration_ms = None
    if root is not None and root.duration_ms is not None:
        duration_ms = root.duration_ms
    elif started_at is not None and ended_at is not None:
        duration_ms = max(0, int((ended_at - started_at).total_seconds() * 1000))
    return started_at, ended_at, duration_ms


def _build_summary(spans: list[SessionSpan]) -> SessionTraceSummaryResponse:
    _, _, total_duration_ms = _trace_bounds(spans)
    longest = max(
        (span for span in spans if span.duration_ms is not None),
        key=lambda span: span.duration_ms or 0,
        default=None,
    )
    return SessionTraceSummaryResponse(
        total_duration_ms=total_duration_ms,
        provisioning_duration_ms=_sum_durations(
            spans, lambda span: span.kind == "session.provisioning"
        ),
        setup_duration_ms=_sum_durations(spans, lambda span: span.kind == "session.setup"),
        workflow_duration_ms=_sum_durations(
            spans, lambda span: span.kind == "session.workflow"
        ),
        publish_duration_ms=_sum_durations(
            spans,
            lambda span: span.kind == "session.publish"
            or (span.kind == "session.workflow" and "publish" in span.name.lower()),
        ),
        cleanup_duration_ms=_sum_durations(spans, lambda span: span.kind == "session.cleanup"),
        active_execution_duration_ms=_sum_durations(
            spans, lambda span: span.kind.startswith("turn.")
        ),
        waiting_duration_ms=_sum_durations(spans, lambda span: span.kind.startswith("wait.")),
        turn_count=sum(1 for span in spans if span.kind.startswith("turn.")),
        tool_call_count=sum(1 for span in spans if span.kind == "tool.call"),
        longest_span=SessionSpanResponse.from_span(longest) if longest is not None else None,
    )


def create_trace_router(
    span_repository: SessionSpanRepository,
    session_service: SessionService | None = None,
    *,
    prefix: str = "/api/v1/forge",
) -> APIRouter:
    router = APIRouter(prefix=prefix)

    async def _check_event_access(
        request: Request, session_id: UUID, action: str = "emit_trace"
    ) -> None:
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
                detail=f"Not authorized to access trace for session {session_id}",
            )

    @router.post(
        "/spans/start",
        response_model=SessionSpanResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["Trace"],
    )
    async def start_span(request: Request, data: SpanStartRequest) -> SessionSpanResponse:
        await _check_event_access(request, data.session_id)
        span = SessionSpan(
            id=data.id,
            session_id=data.session_id,
            trace_id=data.trace_id,
            parent_span_id=data.parent_span_id,
            kind=data.kind,
            name=data.name,
            status=SessionSpanStatus.RUNNING,
            started_at=data.started_at,
            actor_type=data.actor_type,
            actor_id=data.actor_id,
            actor_label=data.actor_label,
            source_service=data.source_service,
            attributes=data.attributes,
        )
        stored = await span_repository.upsert_span(span)
        return SessionSpanResponse.from_span(stored)

    @router.post(
        "/spans/{span_id}/finish",
        response_model=SessionSpanResponse,
        tags=["Trace"],
    )
    async def finish_span(
        request: Request,
        data: SpanFinishRequest,
        span_id: UUID = Path(..., description="Span identifier"),
    ) -> SessionSpanResponse:
        await _check_event_access(request, data.session_id)
        finished = await span_repository.finish_span(
            span_id,
            ended_at=data.ended_at,
            status=data.status,
            attributes=data.attributes,
        )
        if finished is None:
            raise HTTPException(status_code=404, detail=f"Span not found: {span_id}")
        return SessionSpanResponse.from_span(finished)

    @router.post(
        "/spans/complete",
        response_model=SessionSpanResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["Trace"],
    )
    async def complete_span(request: Request, data: SpanCompleteRequest) -> SessionSpanResponse:
        await _check_event_access(request, data.session_id)
        ended_at = data.ended_at
        if ended_at is None and data.duration_ms is not None:
            ended_at = data.started_at + timedelta(milliseconds=data.duration_ms)
        if ended_at is None:
            ended_at = _utc_now()
        duration_ms = data.duration_ms
        if duration_ms is None:
            duration_ms = max(0, int((ended_at - data.started_at).total_seconds() * 1000))
        span = SessionSpan(
            id=data.id,
            session_id=data.session_id,
            trace_id=data.trace_id,
            parent_span_id=data.parent_span_id,
            kind=data.kind,
            name=data.name,
            status=SessionSpanStatus(data.status),
            started_at=data.started_at,
            ended_at=ended_at,
            duration_ms=duration_ms,
            actor_type=data.actor_type,
            actor_id=data.actor_id,
            actor_label=data.actor_label,
            source_service=data.source_service,
            attributes=data.attributes,
        )
        stored = await span_repository.upsert_span(span)
        return SessionSpanResponse.from_span(stored)

    @router.get(
        "/sessions/{session_id}/trace",
        response_model=SessionTraceResponse,
        tags=["Trace"],
    )
    async def get_session_trace(request: Request, session_id: UUID) -> SessionTraceResponse:
        await _check_event_access(request, session_id, action="read_trace")
        spans = await span_repository.list_spans(session_id)
        if spans:
            trace_id = spans[0].trace_id
        else:
            trace_id = session_id
        started_at, ended_at, duration_ms = _trace_bounds(spans)
        lane_map: dict[str, TraceLaneResponse] = {}
        for span in spans:
            lane_kind, lane_key, lane_label = _lane_key(span)
            lane_map.setdefault(
                lane_key,
                TraceLaneResponse(key=lane_key, label=lane_label, kind=lane_kind),
            )
        return SessionTraceResponse(
            trace_id=trace_id,
            session_id=session_id,
            started_at=started_at.isoformat() if started_at is not None else None,
            ended_at=ended_at.isoformat() if ended_at is not None else None,
            duration_ms=duration_ms,
            spans=[SessionSpanResponse.from_span(span) for span in spans],
            lanes=list(lane_map.values()),
        )

    @router.get(
        "/sessions/{session_id}/trace/summary",
        response_model=SessionTraceSummaryResponse,
        tags=["Trace"],
    )
    async def get_session_trace_summary(
        request: Request,
        session_id: UUID,
    ) -> SessionTraceSummaryResponse:
        await _check_event_access(request, session_id, action="read_trace")
        spans = await span_repository.list_spans(session_id)
        return _build_summary(spans)

    return router
