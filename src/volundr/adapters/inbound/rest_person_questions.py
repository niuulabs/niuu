"""Questions a session's agent puts to one person of its project (``ask_person``).

``POST /api/v1/forge/sessions/{id}/person-questions``
    The session's broker registers a question for a named member of the session's project:
    Forge checks the person is on that project, records one notification for them (theirs
    alone, open until answered) and returns the question's id and who was asked. Asking again
    with the same ``request_id`` returns the same question without notifying again.

``POST /api/v1/forge/sessions/{id}/person-questions/{question_id}/answer``
    The broker reports who answered, which settles the addressee's notification.

Both are the session's own act, so they accept only the session's own ``forge_session``
credential (``forge:notify``): the broker is the one that sees who answered in the room, and
a person must not be able to mark a question answered, or put one, from outside it.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from niuu.domain.notifications import MAX_BODY_CHARS
from niuu.domain.person_question import person_question_id
from niuu.domain.services.token_scope import (
    FORGE_NOTIFY_SCOPE,
    FORGE_SESSION_TOKEN_USE,
    require_scope,
)
from volundr.adapters.inbound.auth import extract_principal
from volundr.adapters.inbound.forge_session_auth import require_bound_session
from volundr.adapters.inbound.rest_session_people import SessionPersonResponse
from volundr.domain.models import Principal, Session
from volundr.domain.notifications import NotificationNotFoundError
from volundr.domain.services.notifications import NotificationService
from volundr.domain.services.session import SessionAccessDeniedError, SessionService
from volundr.domain.services.session_participants import SessionParticipantService
from volundr.domain.session_participants import SessionNotInProjectError

_REQUEST_ID_PATTERN = r"^[A-Za-z0-9._:-]{1,200}$"


class PersonQuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=MAX_BODY_CHARS)
    request_id: str = Field(pattern=_REQUEST_ID_PATTERN)


class PersonQuestionResponse(BaseModel):
    question_id: str = Field(
        description="The question's message id; the answer is the reply to it."
    )
    notification_id: UUID = Field(description="The addressee's notification.")
    addressee: SessionPersonResponse


class PersonAnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answered_by_user_id: str = Field(min_length=1, max_length=200)
    answered_by_name: str = Field(default="", max_length=200)


class PersonAnswerResponse(BaseModel):
    question_id: str
    notification_id: UUID
    resolved: bool


def create_person_questions_router(
    participants: SessionParticipantService,
    session_service: SessionService,
    notification_service: NotificationService | None,
    *,
    prefix: str = "/api/v1/forge",
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["Sessions"])

    def _notifications() -> NotificationService:
        if notification_service is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "ask_person tells the person it asks through a notification, and this Forge "
                "runs without them (notifications.enabled is false). Enable notifications.",
            )
        return notification_service

    async def _own_session(request: Request, session_id: UUID) -> tuple[Session, Principal]:
        principal = await extract_principal(request)
        require_bound_session(request, session_id)
        if principal.token_use != FORGE_SESSION_TOKEN_USE or principal.bound_session_id != str(
            session_id
        ):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Only the session itself may use its person questions, with its own Forge "
                "session credential (forge_mcp.session_tokens)",
            )
        session = await session_service.get_session(session_id)
        if session is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Session not found: {session_id}")
        return session, principal

    @router.post(
        "/sessions/{session_id}/person-questions",
        response_model=PersonQuestionResponse,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_scope(FORGE_NOTIFY_SCOPE))],
        responses={
            200: {
                "model": PersonQuestionResponse,
                "description": "Already asked (same request_id)",
            },
            409: {"description": "The session is not attached to a project"},
            422: {"description": "The person is not a member of the session's project"},
        },
    )
    async def ask_person(
        request: Request,
        body: PersonQuestionRequest,
        response: Response,
        session_id: UUID = Path(description="Session id"),
    ) -> PersonQuestionResponse:
        """Put a question to one member of the session's project (201), or return the one
        already put for this ``request_id`` (200)."""
        service = _notifications()
        session, principal = await _own_session(request, session_id)
        try:
            members = await participants.list_people(session, principal)
        except SessionAccessDeniedError:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, f"Not authorized to ask people of {session_id}"
            ) from None
        except SessionNotInProjectError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        addressee = next((m for m in members if m.user_id == body.user_id), None)
        if addressee is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"{body.user_id!r} is not a member of this session's project, so it cannot be "
                "asked. Call find_people to see who is, and ask one of them.",
            )
        question_id = person_question_id(str(session.id), addressee.user_id, body.request_id)
        notification, created = await service.ask_person(
            session,
            addressee_id=addressee.user_id,
            question=body.question,
            question_id=question_id,
        )
        if not created:
            response.status_code = status.HTTP_200_OK
        return PersonQuestionResponse(
            question_id=question_id,
            notification_id=notification.id,
            addressee=SessionPersonResponse.from_member(addressee),
        )

    @router.post(
        "/sessions/{session_id}/person-questions/{question_id}/answer",
        response_model=PersonAnswerResponse,
        dependencies=[Depends(require_scope(FORGE_NOTIFY_SCOPE))],
        responses={404: {"description": "No such question was put to a person"}},
    )
    async def answer_person_question(
        request: Request,
        body: PersonAnswerRequest,
        session_id: UUID = Path(description="Session id"),
        question_id: str = Path(pattern=r"^[0-9a-f-]{36}$"),
    ) -> PersonAnswerResponse:
        """Report that the question was answered, which settles the addressee's notification."""
        service = _notifications()
        session, _ = await _own_session(request, session_id)
        try:
            settled = await service.settle_person_question(
                session,
                question_id=question_id,
                answered_by=body.answered_by_name or body.answered_by_user_id,
            )
        except NotificationNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        return PersonAnswerResponse(
            question_id=question_id, notification_id=settled.id, resolved=settled.resolved
        )

    return router
