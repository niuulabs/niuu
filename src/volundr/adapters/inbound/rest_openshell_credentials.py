"""OpenShell dynamic provider credential grant endpoint."""

from __future__ import annotations

from urllib.parse import parse_qs

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from volundr.domain.ports import OpenShellCredentialGrantPort

MAX_TOKEN_REQUEST_BYTES = 64 * 1024


def create_openshell_credentials_router(
    broker: OpenShellCredentialGrantPort,
    *,
    prefix: str = "/api/v1/internal/openshell",
) -> APIRouter:
    """Create the internal OAuth2 assertion endpoint used by OpenShell supervisors."""

    router = APIRouter(prefix=prefix, tags=["openshell-internal"])

    @router.post("/credential-token", include_in_schema=False)
    async def exchange_credential(request: Request) -> JSONResponse:
        body = await request.body()
        if len(body) > MAX_TOKEN_REQUEST_BYTES:
            return _oauth_error("invalid_request", "request body is too large", 413)
        try:
            form = parse_qs(body.decode("utf-8"), keep_blank_values=True, strict_parsing=True)
            value = lambda key: str(form.get(key, [""])[0])  # noqa: E731
            token = await broker.exchange_credential_grant(
                client_assertion=value("client_assertion"),
                client_assertion_type=value("client_assertion_type"),
                grant_type=value("grant_type"),
                audience=value("audience"),
                scope=value("scope"),
            )
        except (UnicodeDecodeError, ValueError) as exc:
            return _oauth_error("invalid_grant", str(exc), 401)

        return JSONResponse(
            {
                "access_token": token.access_token,
                "token_type": token.token_type,
                "expires_in": token.expires_in,
                "scope": value("scope"),
            }
        )

    return router


def _oauth_error(error: str, description: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        {"error": error, "error_description": description},
        status_code=status_code,
        headers={"Cache-Control": "no-store"},
    )
