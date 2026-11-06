"""VM guest metadata endpoint: a guest agent pulls its session's credentials."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from volundr.domain.guest_credentials import (
    METADATA_CREDENTIALS_PATH,
    GuestAuthenticationError,
    GuestCredentials,
    GuestCredentialUnavailableError,
    GuestRequest,
)
from volundr.domain.services.guest_metadata import GuestMetadataService, GuestRequestError

MAX_METADATA_REQUEST_BYTES = 64 * 1024

#: Every credential response, success or not, must stay out of shared caches.
_NO_STORE = {"Cache-Control": "no-store"}


def create_compute_metadata_router(service: GuestMetadataService) -> APIRouter:
    """Create the internal endpoint served to VM guest agents (``METADATA_CREDENTIALS_PATH``).

    The route carries no bearer JWT of its own to Envoy: the guest proves its allocation
    through the configured ``GuestIdentity``, so Envoy's JWT filter must bypass exactly
    this path (see the Volundr chart's ``guestMetadata`` values).
    """

    router = APIRouter(tags=["compute-metadata-internal"])

    @router.post(METADATA_CREDENTIALS_PATH, include_in_schema=False)
    async def credentials(request: Request) -> JSONResponse:
        body = await request.body()
        if len(body) > MAX_METADATA_REQUEST_BYTES:
            return _error("request body is too large", 413)
        guest_request = GuestRequest(
            method=request.method,
            path=request.url.path,
            headers={name.lower(): value for name, value in request.headers.items()},
            body=body,
        )
        try:
            issued = await service.credentials(guest_request)
        except GuestAuthenticationError:
            return _error("unauthorized", 401)
        except GuestCredentialUnavailableError as exc:
            return _error(str(exc), 409)
        except GuestRequestError as exc:
            return _error(str(exc), 400)
        return JSONResponse(_payload(issued), headers=_NO_STORE)

    return router


def _payload(issued: GuestCredentials) -> dict:
    forge_session = issued.forge_session
    return {
        "service_account_tokens": [
            {
                "volume": token.volume,
                "path": token.path,
                "token": token.token,
                "expires_at": _utc_iso(token.expires_at),
            }
            for token in issued.service_account_tokens
        ],
        "forge_session": (
            None
            if forge_session is None
            else {"token": forge_session.token, "grants": list(forge_session.grants)}
        ),
    }


def _utc_iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC).isoformat()
    return moment.astimezone(UTC).isoformat()


def _error(reason: str, status_code: int) -> JSONResponse:
    return JSONResponse({"error": reason}, status_code=status_code, headers=_NO_STORE)
