"""REST adapter for Forge notifications (contract §4, under ``/api/v1/forge``).

* ``GET  /notifications``                    the reader's feed (owner-scoped; admins
  see their tenant), newest first with ``before``, ascending with ``after``
* ``GET/PUT /notifications/read-state``      the reader's watermark (forward-only CAS)
* ``/notifications/rules``                   owner-scoped delivery rule CRUD
* ``GET  /notifications/sinks``              sinks a rule may name
* ``GET  /notifications/{id}/deliveries``    outbox rows for one notification
* ``GET  /notifications/{id}/image``         the stored image (``?variant=thumb`` for the
  thumbnail); readable by exactly those who may read the notification
* ``GET  /sessions/{id}/notifications``      one session's notifications, ascending
* ``POST /sessions/{id}/notifications``      direct submit with an idempotency key
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from niuu.domain.notifications import (
    NOTIFICATION_IMAGE_MIME_TYPES,
    NotificationAsk,
    NotificationDraft,
    NotificationKind,
    NotificationLink,
    NotificationSeverity,
    NotificationSource,
    NotificationSpeak,
)
from niuu.domain.services.token_scope import (
    FORGE_NOTIFY_SCOPE,
    FORGE_SESSION_READ_SCOPE,
    FORGE_SESSION_TOKEN_USE,
    require_scope,
)
from volundr.adapters.inbound.auth import extract_principal
from volundr.domain.models import Principal, Session
from volundr.domain.notifications import (
    DeliveryStatus,
    Notification,
    NotificationDelivery,
    NotificationImageState,
    NotificationImageVariant,
    NotificationNotFoundError,
    NotificationQuery,
    NotificationQuietHours,
    NotificationReadState,
    NotificationReadStateConflictError,
    NotificationRule,
    NotificationRuleMatch,
    NotificationRuleSpec,
    NotificationValidationError,
)
from volundr.domain.services.notifications import NotificationService
from volundr.domain.services.session import SessionAccessDeniedError, SessionService

MAX_IDEMPOTENCY_KEY_CHARS = 200


class NotificationImageResponse(BaseModel):
    """A stored notification image: where to load it and its shape."""

    url: str = Field(description="Forge-relative URL of the stored image")
    thumbnail_url: str = Field(description="Forge-relative URL of its thumbnail")
    mime: str
    width: int
    height: int
    alt: str | None = None


class NotificationInputRequestResponse(BaseModel):
    """The pending runtime request a system attention item stands for."""

    kind: str = Field(description="question, confirmation or permission")
    request_id: str | None = Field(
        default=None,
        description=(
            "The pending request's id, as the session's question/permission card carries "
            "it; answer it there (ask_user_answer / permission response). Null when the "
            "runtime reported none."
        ),
    )


class NotificationResolveRequest(BaseModel):
    """Body of ``PUT /notifications/{id}/resolve``."""

    model_config = ConfigDict(extra="forbid")

    resolution: str | None = Field(
        default=None, max_length=500, description="Why it is resolved, in a few words."
    )


class NotificationResponse(BaseModel):
    """Reader view: read through the host watermark or acknowledged individually."""

    id: UUID
    seq: int
    session_id: UUID | None = None
    session_seq: int | None = None
    session_name: str | None = None
    owner_id: str
    tenant_id: str | None = None
    project_id: str | None = None
    kind: NotificationKind
    severity: NotificationSeverity
    source: NotificationSource
    title: str
    body: str
    links: list[NotificationLink]
    engine: str | None = None
    model: str | None = None
    correlation_id: str | None = None
    turn_id: str | None = Field(
        default=None,
        description=(
            "Transcript turn this notification anchors to; null means it has no turn. "
            "Resolve its position with GET /sessions/{id}/conversation/turns/{turn_id}."
        ),
    )
    resident_id: UUID | None = Field(
        default=None,
        description=(
            "The resident (resident runtime id) this notification is about; set instead "
            "of session_id for a resident's notification."
        ),
    )
    resident_session_id: UUID | None = Field(
        default=None,
        description=(
            "The resident session (channel) it came from; null with a resident_id means "
            "the resident's main room."
        ),
    )
    odin_review_id: str | None = Field(
        default=None,
        description=(
            "The ODIN review item (a resident's question) this notification asks the "
            "operator to answer; null for every other notification."
        ),
    )
    odin_review_decide_path: str | None = Field(
        default=None,
        description=(
            "Path that decides odin_review_id, on the host serving the ODIN review queue "
            "(POST {decision: answered, answer})."
        ),
    )
    question: bool | None = Field(
        default=None,
        description="true: the notice declares a Question for the user (F11b); null: unset",
    )
    about_question: dict[str, str] | None = Field(
        default=None,
        description=(
            "{id: question:<host>:<uuid>, action: remind|replace|relay|resolved|withdrawn}: "
            "the earlier question this notice refers to; null when none"
        ),
    )
    ask: NotificationAsk | None = Field(
        default=None,
        description="Structured question/action items when question=true; null for legacy notices.",
    )
    input_request: NotificationInputRequestResponse | None = Field(
        default=None,
        description=(
            "Set on a system attention item: the runtime request (AskUserQuestion, "
            "confirmation, permission) the session or resident is blocked on; null otherwise."
        ),
    )
    created_at: datetime
    image: NotificationImageResponse | None = Field(
        default=None,
        description="The stored image; null when none was attached or none is stored (yet).",
    )
    image_state: NotificationImageState | None = Field(
        default=None,
        description=(
            "pending (Forge is copying it), ready (stored; see image) or failed (see "
            "image_error); null when no image was attached."
        ),
    )
    image_error: str | None = Field(
        default=None, description="Why the attached image could not be stored."
    )
    speak: NotificationSpeak | None = Field(
        default=None,
        description=(
            "{text, priority: now|next_window}: what a voice client may say aloud for "
            "this notification and how soon; null when none. Forge never speaks it."
        ),
    )
    resolved: bool = Field(
        default=False,
        description="The incident is over (LEX-1442): an alarm no surface should keep raising.",
    )
    resolved_at: datetime | None = None
    resolved_by_notification_id: UUID | None = Field(
        default=None,
        description="The notification that resolved this one; null when resolved by hand.",
    )
    resolution: str | None = Field(
        default=None, description="Free text given when an operator resolved it by hand."
    )
    stale: bool = Field(
        default=False,
        description=(
            "An unresolved alarm older than notifications.stale_after_seconds whose session "
            "has posted a newer milestone: probably over. Display only."
        ),
    )
    resolves: list[str] | None = Field(
        default=None,
        description="Notification or correlation ids this notification resolved, as sent.",
    )
    read: bool
    instance_id: str | None = Field(
        default=None,
        description=(
            "This node's instance id in its Guild (GET /api/v1/forge/guild); null when "
            "the node belongs to no Guild."
        ),
    )
    instance_name: str | None = None
    instance_slug: str | None = None

    @classmethod
    def build(
        cls,
        notification: Notification,
        read_through_seq: int,
        read_ids: set[UUID] | frozenset[UUID] = frozenset(),
        instance_fields: Mapping[str, str] | None = None,
    ) -> NotificationResponse:
        return cls(
            **notification.wire(),
            **(instance_fields or {}),
            read=notification.seq <= read_through_seq or notification.id in read_ids,
        )


class NotificationFeedResponse(BaseModel):
    revision: int = 0
    items: list[NotificationResponse]
    next_before: int | None = Field(
        default=None, description="Pass as ``before`` for the next (older) page; null at the end"
    )
    head_seq: int = Field(description="Newest seq in the reader's scope (0 when empty)")
    read_through_seq: int
    unread_count: int


class NotificationReadStateResponse(BaseModel):
    read_through_seq: int
    revision: int
    unread_count: int
    head_seq: int

    @classmethod
    def build(cls, state: NotificationReadState) -> NotificationReadStateResponse:
        return cls(
            read_through_seq=state.read_through_seq,
            revision=state.revision,
            unread_count=state.unread_count,
            head_seq=state.head_seq,
        )


class NotificationReadStateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    read_through_seq: int = Field(ge=0)
    expected_revision: int = Field(ge=0)


class NotificationSubmitRequest(NotificationDraft):
    """A ``NotificationDraft`` plus the caller's idempotency key (required)."""

    idempotency_key: str = Field(min_length=1, max_length=MAX_IDEMPOTENCY_KEY_CHARS)


class NotificationRuleResponse(BaseModel):
    id: UUID
    name: str
    enabled: bool
    match: NotificationRuleMatch
    sink: str
    integration_connection_id: str | None = None
    config: dict[str, Any]
    quiet_hours: NotificationQuietHours | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def build(cls, rule: NotificationRule) -> NotificationRuleResponse:
        return cls(**rule.model_dump(exclude={"owner_id"}))


class NotificationDeliveryResponse(BaseModel):
    id: UUID
    notification_id: UUID
    rule_id: UUID
    sink: str
    status: DeliveryStatus
    attempts: int
    next_attempt_at: datetime
    lease_until: datetime | None = None
    last_error: str | None = None
    delivered_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def build(cls, delivery: NotificationDelivery) -> NotificationDeliveryResponse:
        return cls(**delivery.model_dump())


class NotificationSinkResponse(BaseModel):
    name: str
    label: str
    requires_integration: bool


def _csv_enum[E: StrEnum](raw: str | None, enum_cls: type[E], name: str) -> tuple[E, ...]:
    if raw is None:
        return ()
    values: list[E] = []
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        try:
            value = enum_cls(token)
        except ValueError:
            allowed = ", ".join(member.value for member in enum_cls)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Unknown {name} {token!r}; expected one of: {allowed}",
            )
        if value not in values:
            values.append(value)
    return tuple(values)


def create_notifications_router(
    notification_service: NotificationService,
    session_service: SessionService,
    *,
    prefix: str = "/api/v1/forge",
    default_page_size: int,
    max_page_size: int,
    image_cache_max_age_seconds: int,
) -> APIRouter:
    """Create the notification feed, read-state, rules, submit and image routes."""
    image_cache_control = f"private, max-age={image_cache_max_age_seconds}, immutable"
    router = APIRouter(prefix=prefix, tags=["Notifications"])
    # Every served item names this node's Guild instance, like the Guild feed's.
    instance_fields = notification_service.instance_fields

    async def _session_for(session_id: UUID, principal: Principal, action: str) -> Session:
        session = await session_service.get_session(session_id)
        if session is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Session not found: {session_id}")
        try:
            await session_service._check_access(session, principal, action)
        except SessionAccessDeniedError:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Not authorized to access notifications for session {session_id}",
            )
        return session

    @router.get(
        "/notifications",
        response_model=NotificationFeedResponse,
        dependencies=[Depends(require_scope(FORGE_SESSION_READ_SCOPE))],
    )
    async def list_notifications(
        limit: int = Query(default=default_page_size, ge=1, le=max_page_size),
        before: int | None = Query(default=None, ge=1, description="Older than this seq"),
        after: int | None = Query(default=None, ge=0, description="Newer than this seq"),
        kind: str | None = Query(default=None, description="CSV of kinds"),
        min_severity: NotificationSeverity | None = Query(default=None),
        source: str | None = Query(default=None, description="CSV of sources"),
        session_id: UUID | None = Query(default=None),
        project_id: str | None = Query(default=None, min_length=1),
        unread: bool | None = Query(default=None),
        unresolved: bool | None = Query(
            default=None,
            description="true: only notifications not resolved yet; false: only resolved.",
        ),
        principal: Principal = Depends(extract_principal),
    ) -> NotificationFeedResponse:
        """The caller's notification feed with its read watermark and unread count."""
        if before is not None and after is not None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "Use either before or after, not both"
            )
        query = NotificationQuery(
            limit=limit,
            before=before,
            after=after,
            kinds=_csv_enum(kind, NotificationKind, "kind"),
            min_severity=min_severity,
            sources=_csv_enum(source, NotificationSource, "source"),
            session_id=session_id,
            project_id=project_id,
            unread=unread,
            unresolved=unresolved,
        )
        feed = await notification_service.list_feed(principal, query)
        return NotificationFeedResponse(
            items=[
                NotificationResponse.build(n, feed.read_through_seq, feed.read_ids, instance_fields)
                for n in feed.items
            ],
            revision=feed.revision,
            next_before=feed.next_before,
            head_seq=feed.head_seq,
            read_through_seq=feed.read_through_seq,
            unread_count=feed.unread_count,
        )

    @router.get(
        "/notifications/read-state",
        response_model=NotificationReadStateResponse,
        dependencies=[Depends(require_scope(FORGE_SESSION_READ_SCOPE))],
    )
    async def get_read_state(
        principal: Principal = Depends(extract_principal),
    ) -> NotificationReadStateResponse:
        state = await notification_service.get_read_state(principal)
        return NotificationReadStateResponse.build(state)

    @router.put(
        "/notifications/read-state",
        response_model=NotificationReadStateResponse,
        dependencies=[Depends(require_scope(FORGE_SESSION_READ_SCOPE))],
    )
    async def put_read_state(
        body: NotificationReadStateUpdate,
        principal: Principal = Depends(extract_principal),
    ) -> NotificationReadStateResponse:
        """Move the read watermark forward; 409 when ``expected_revision`` is stale."""
        try:
            state = await notification_service.update_read_state(
                principal,
                read_through_seq=body.read_through_seq,
                expected_revision=body.expected_revision,
            )
        except NotificationReadStateConflictError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
        except NotificationValidationError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
        return NotificationReadStateResponse.build(state)

    @router.put(
        "/notifications/{notification_id}/resolve",
        response_model=NotificationResponse,
        responses={404: {"description": "Not found"}},
    )
    async def resolve_notification(
        notification_id: UUID,
        body: NotificationResolveRequest | None = None,
        principal: Principal = Depends(extract_principal),
    ) -> NotificationResponse:
        """Mark a notification resolved by hand: its incident is over (LEX-1442).

        Idempotent; the first resolution is kept. Not open to session credentials (they
        resolve through ``notify`` ``resolves``). The resolved row is sent again on the
        live stream so clients replace it.
        """
        try:
            notification = await notification_service.resolve(
                principal, notification_id, resolution=body.resolution if body else None
            )
        except NotificationNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        state = await notification_service.get_read_state(principal)
        read_ids = await notification_service.read_ids(principal, [notification])
        return NotificationResponse.build(
            notification, state.read_through_seq, read_ids, instance_fields
        )

    @router.put(
        "/notifications/{notification_id}/read",
        dependencies=[Depends(require_scope(FORGE_SESSION_READ_SCOPE))],
    )
    async def mark_notification_read(
        notification_id: UUID,
        principal: Principal = Depends(extract_principal),
    ) -> dict:
        """Acknowledge exactly this visible activity, without advancing the watermark."""
        try:
            state = await notification_service.mark_read(principal, notification_id)
        except NotificationNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        return {
            "id": str(notification_id),
            "read": True,
            "read_state": NotificationReadStateResponse.build(state).model_dump(),
        }

    @router.get("/notifications/rules", response_model=list[NotificationRuleResponse])
    async def list_rules(
        principal: Principal = Depends(extract_principal),
    ) -> list[NotificationRuleResponse]:
        rules = await notification_service.list_rules(principal)
        return [NotificationRuleResponse.build(rule) for rule in rules]

    @router.post(
        "/notifications/rules",
        response_model=NotificationRuleResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_rule(
        body: NotificationRuleSpec,
        principal: Principal = Depends(extract_principal),
    ) -> NotificationRuleResponse:
        try:
            rule = await notification_service.create_rule(principal, body)
        except NotificationValidationError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
        return NotificationRuleResponse.build(rule)

    @router.put("/notifications/rules/{rule_id}", response_model=NotificationRuleResponse)
    async def update_rule(
        body: NotificationRuleSpec,
        rule_id: UUID = Path(description="Rule id"),
        principal: Principal = Depends(extract_principal),
    ) -> NotificationRuleResponse:
        try:
            rule = await notification_service.update_rule(principal, rule_id, body)
        except NotificationNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
        except NotificationValidationError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
        return NotificationRuleResponse.build(rule)

    @router.delete("/notifications/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_rule(
        rule_id: UUID = Path(description="Rule id"),
        principal: Principal = Depends(extract_principal),
    ) -> Response:
        try:
            await notification_service.delete_rule(principal, rule_id)
        except NotificationNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/notifications/sinks", response_model=list[NotificationSinkResponse])
    async def list_sinks(
        principal: Principal = Depends(extract_principal),
    ) -> list[NotificationSinkResponse]:
        """Sinks a rule may name: configured sinks, plus ``integration`` when the
        caller has an enabled messaging integration."""
        sinks = await notification_service.list_sinks(principal)
        return [
            NotificationSinkResponse(
                name=sink.name, label=sink.label, requires_integration=sink.requires_integration
            )
            for sink in sinks
        ]

    @router.get(
        "/notifications/{notification_id}/deliveries",
        response_model=list[NotificationDeliveryResponse],
    )
    async def list_deliveries(
        notification_id: UUID = Path(description="Notification id"),
        principal: Principal = Depends(extract_principal),
    ) -> list[NotificationDeliveryResponse]:
        try:
            deliveries = await notification_service.list_deliveries(principal, notification_id)
        except NotificationNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
        return [NotificationDeliveryResponse.build(delivery) for delivery in deliveries]

    @router.get(
        "/notifications/{notification_id}/image",
        response_class=Response,
        dependencies=[Depends(require_scope(FORGE_SESSION_READ_SCOPE))],
        responses={
            200: {
                "description": "The stored image bytes",
                "content": {mime: {} for mime in sorted(NOTIFICATION_IMAGE_MIME_TYPES)},
            },
            404: {"description": "No such notification for this reader, or no stored image"},
        },
    )
    async def get_notification_image(
        notification_id: UUID = Path(description="Notification id"),
        variant: NotificationImageVariant = Query(
            default=NotificationImageVariant.ORIGINAL,
            description="``thumb`` for the thumbnail (long side bounded), else the image",
        ),
        principal: Principal = Depends(extract_principal),
    ) -> Response:
        """A notification's stored image, for whoever may read the notification.

        The bytes of a stored image never change, so the response may be cached
        for a long time; a 404 (not stored yet) is not cacheable.
        """
        try:
            image = await notification_service.image_content(principal, notification_id, variant)
        except NotificationNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        return Response(
            content=image.content,
            media_type=image.mime,
            headers={"Cache-Control": image_cache_control, "X-Content-Type-Options": "nosniff"},
        )

    @router.get(
        "/sessions/{session_id}/notifications",
        response_model=list[NotificationResponse],
        tags=["Sessions"],
        dependencies=[Depends(require_scope(FORGE_SESSION_READ_SCOPE))],
    )
    async def list_session_notifications(
        session_id: UUID = Path(description="Session id"),
        after: int = Query(default=0, ge=0, description="Return notifications after this seq"),
        limit: int = Query(default=default_page_size, ge=1, le=max_page_size),
        principal: Principal = Depends(extract_principal),
    ) -> list[NotificationResponse]:
        """One session's notifications, ascending by feed seq."""
        await _session_for(session_id, principal, "read")
        items, read_through = await notification_service.list_for_session(
            principal, session_id, after=after, limit=limit
        )
        read_ids = await notification_service.read_ids(principal, items)
        return [
            NotificationResponse.build(item, read_through, read_ids, instance_fields)
            for item in items
        ]

    @router.post(
        "/sessions/{session_id}/notifications",
        response_model=NotificationResponse,
        status_code=status.HTTP_201_CREATED,
        responses={200: {"model": NotificationResponse, "description": "Deduplicated submit"}},
        tags=["Sessions"],
        dependencies=[Depends(require_scope(FORGE_NOTIFY_SCOPE))],
    )
    async def submit_notification(
        body: NotificationSubmitRequest,
        response: Response,
        session_id: UUID = Path(description="Session id"),
        principal: Principal = Depends(extract_principal),
    ) -> NotificationResponse:
        """Record a notification for a session (201), or return the one already
        recorded for this idempotency key (200). Feed-only: ``session_seq`` is null.

        ``source`` is ``agent`` when the session submits for itself with its own
        session credential, ``operator`` otherwise."""
        session = await _session_for(session_id, principal, "emit_event")
        draft = NotificationDraft.model_validate(body.model_dump(exclude={"idempotency_key"}))
        self_submitted = (
            principal.token_use == FORGE_SESSION_TOKEN_USE
            and principal.bound_session_id == str(session_id)
        )
        source = NotificationSource.AGENT if self_submitted else NotificationSource.OPERATOR
        try:
            notification, created = await notification_service.submit(
                session,
                principal,
                draft,
                idempotency_key=body.idempotency_key,
                source=source,
            )
        except NotificationValidationError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
        if not created:
            response.status_code = status.HTTP_200_OK
        read_through = await notification_service.read_through_seq(principal)
        return NotificationResponse.build(
            notification,
            read_through,
            await notification_service.read_ids(principal, [notification]),
            instance_fields,
        )

    return router
