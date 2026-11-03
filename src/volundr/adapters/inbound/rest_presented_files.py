"""REST adapter for durable presented files (control-plane F2).

``PUT /sessions/{id}/files/presented/{file_id}`` lets a session's broker hand Forge
the bytes it just staged for ``present-file``. Forge keeps the copy in its own store
and answers with the file's durable URL, which the broker writes into the
``present_file`` turn. Reading goes through the existing ``GET`` route of the same
path (``rest.py``), which serves this copy first and the live broker second.
"""

from __future__ import annotations

import logging
from urllib.parse import unquote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from pydantic import BaseModel

from niuu.domain.presented_files import (
    PRESENTED_FILE_NAME_HEADER,
    PRESENTED_FILES_QUOTA_CODE,
    PRESENTED_FILES_QUOTA_STATUS,
)
from niuu.domain.services.token_scope import FORGE_NOTIFY_SCOPE, require_scope
from volundr.adapters.inbound.forge_session_auth import require_bound_session
from volundr.domain.presented_files import (
    PRESENTED_FILE_ID_PATTERN,
    PresentedFileConflictError,
    PresentedFileQuota,
    PresentedFileQuotaError,
    PresentedFileStore,
    durable_file_url,
    presented_file_path,
)
from volundr.domain.services.session import SessionAccessDeniedError, SessionService

logger = logging.getLogger(__name__)

NAME_HEADER = PRESENTED_FILE_NAME_HEADER
MAX_NAME_CHARS = 255


class PresentedFileStoredResponse(BaseModel):
    file_id: str
    session_id: UUID
    name: str
    mime: str
    size: int
    sha256: str
    path: str
    url: str | None


def create_presented_files_router(
    store: PresentedFileStore,
    session_service: SessionService,
    *,
    public_base_url: str = "",
    max_bytes: int,
    quota: PresentedFileQuota,
    prefix: str = "/api/v1/forge",
) -> APIRouter:
    router = APIRouter(prefix=prefix)

    @router.put(
        "/sessions/{session_id}/files/presented/{file_id}",
        response_model=PresentedFileStoredResponse,
        tags=["Sessions"],
        dependencies=[Depends(require_scope(FORGE_NOTIFY_SCOPE))],
    )
    async def store_presented_file(
        request: Request,
        session_id: UUID = Path(description="Session that presented the file"),
        file_id: str = Path(pattern=PRESENTED_FILE_ID_PATTERN, description="present-file id"),
    ) -> PresentedFileStoredResponse:
        """Keep a durable copy of a presented file (the session's own broker only)."""
        require_bound_session(request, session_id)
        session = await session_service.get_session(session_id)
        if session is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Session not found: {session_id}")
        from volundr.adapters.inbound.auth import extract_principal

        principal = await extract_principal(request)
        try:
            await session_service._check_access(session, principal, "emit_event")
        except SessionAccessDeniedError:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not authorized") from None

        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > max_bytes:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "file exceeds max_bytes")
        chunks: list[bytes] = []
        received = 0
        async for chunk in request.stream():
            received += len(chunk)
            if received > max_bytes:
                raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "file exceeds max_bytes")
            chunks.append(chunk)
        data = b"".join(chunks)
        name = " ".join(unquote(request.headers.get(NAME_HEADER, "")).split())[:MAX_NAME_CHARS]
        name = name.replace("/", "_").replace("\\", "_") or file_id
        mime = (request.headers.get("content-type") or "").split(";")[0].strip()
        mime = mime or "application/octet-stream"
        try:
            async with quota.lock:
                await quota.admit(store, session_id, file_id, len(data))
                record = await store.put(session_id, file_id, name=name, mime=mime, data=data)
        except PresentedFileQuotaError as exc:
            logger.error(
                "presented file refused by quota: session=%s file=%s size=%d used=%d "
                "limit=%d refusals=%d",
                session_id,
                file_id,
                exc.size,
                exc.used,
                exc.limit,
                quota.refusals,
            )
            raise HTTPException(
                PRESENTED_FILES_QUOTA_STATUS,
                {"code": PRESENTED_FILES_QUOTA_CODE, "message": str(exc)},
            ) from None
        except PresentedFileConflictError:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "a different file is stored under this id"
            ) from None
        # The session may have been deleted while the body streamed: its cleanup has
        # then already run, so drop what this request wrote rather than orphan it.
        if await session_service.get_session(session_id) is None:
            await store.delete_session(session_id)
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Session not found: {session_id}")
        logger.info(
            "presented file stored: session=%s file=%s size=%d", session_id, file_id, len(data)
        )
        return PresentedFileStoredResponse(
            file_id=record.file_id,
            session_id=record.session_id,
            name=record.name,
            mime=record.mime,
            size=record.size,
            sha256=record.sha256,
            path=presented_file_path(session_id, file_id, prefix=prefix),
            url=durable_file_url(public_base_url, session_id, file_id, prefix=prefix),
        )

    return router
