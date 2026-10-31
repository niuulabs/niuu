"""Realm summary SSE route."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from yggdrasil.connection_manager import ConnectionManager, SSEClientLimitExceededError
from yggdrasil.sse.poller import RealmPoller
from yggdrasil.stream import stream_realm_summary


def create_sse_router(
    connection_manager: ConnectionManager,
    poller: RealmPoller,
    disconnect_check_interval: float,
) -> APIRouter:
    """Create the realm summary SSE router."""
    router = APIRouter(prefix="/api/v1/yggdrasil/realms", tags=["Yggdrasil"])

    @router.get("/{realm_id}/stream", summary="Stream realm health summary")
    async def stream_realm(request: Request, realm_id: str) -> StreamingResponse:
        """Stream an immediate summary and subsequent changed summaries."""
        initial_summary = poller.summary_for(realm_id)
        if initial_summary is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Realm summary is unavailable",
            )
        try:
            queue = connection_manager.connect(realm_id)
        except SSEClientLimitExceededError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="SSE client limit exceeded",
            ) from error

        return StreamingResponse(
            stream_realm_summary(
                request,
                realm_id,
                connection_manager,
                initial_summary,
                queue,
                disconnect_check_interval,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return router
