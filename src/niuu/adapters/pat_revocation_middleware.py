"""FastAPI middleware for PAT revocation enforcement.

Intercepts incoming requests with ``Authorization: Bearer <token>`` headers
and validates the token against the PAT revocation store.  Non-PAT tokens
and requests without Bearer tokens are passed through unchanged.
"""

from __future__ import annotations

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse


class PATRevocationMiddleware(BaseHTTPMiddleware):
    """Reject requests that use a revoked PAT.

    The middleware reads the ``PATValidator`` from ``app.state.pat_validator``.
    If the validator is not set (e.g. PAT feature is disabled), all requests
    are passed through.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        validator = getattr(request.app.state, "pat_validator", None)
        if validator is None:
            return await call_next(request)

        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return await call_next(request)

        raw_token = auth[7:]
        if not await validator.is_valid(raw_token):
            return JSONResponse(
                status_code=401,
                content={"detail": "Token has been revoked"},
            )

        return await call_next(request)
