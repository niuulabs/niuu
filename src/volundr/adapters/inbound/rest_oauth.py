"""FastAPI REST adapter for OAuth2 integration flows."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from niuu.http_compat import LegacyRouteNotice, warn_on_legacy_route
from volundr.adapters.inbound.auth import extract_principal
from volundr.adapters.outbound.oauth2_provider import OAuth2Provider
from volundr.config import OAuthConfig
from volundr.domain.models import (
    IntegrationConnection,
    Principal,
    SecretType,
)
from volundr.domain.ports import CredentialStorePort, IntegrationRepository
from volundr.domain.services.integration_registry import IntegrationRegistry

logger = logging.getLogger(__name__)

STATE_TTL_SECONDS = 300  # 5 minutes


class AuthorizeResponse(BaseModel):
    """Response for the authorize endpoint."""

    url: str = Field(description="OAuth2 authorization URL to redirect the user to")


def _build_oauth_router(
    oauth_config: OAuthConfig,
    integration_registry: IntegrationRegistry,
    credential_store: CredentialStorePort,
    integration_repo: IntegrationRepository,
    *,
    prefix: str,
    deprecated: bool = False,
    canonical_prefix: str | None = None,
) -> APIRouter:
    """Create FastAPI router for OAuth2 integration flows."""
    router = APIRouter(
        prefix=prefix,
        tags=["OAuth"],
    )

    # Pending states: state -> {slug, user_id, redirect_uri, expires_at}
    _pending_states: dict[str, dict] = {}

    def _cleanup_expired() -> None:
        """Remove expired state entries lazily."""
        now = time.monotonic()
        expired = [k for k, v in _pending_states.items() if v["expires_at"] < now]
        for k in expired:
            _pending_states.pop(k, None)

    def _build_redirect_uri(slug: str) -> str:
        base = oauth_config.redirect_base_url.rstrip("/")
        return f"{base}{prefix}/callback"

    @router.get(
        "/{slug}/authorize",
        response_model=AuthorizeResponse,
    )
    async def authorize(
        slug: str,
        request: Request,
        response: Response,
        principal: Principal = Depends(extract_principal),
    ) -> AuthorizeResponse:
        """Start an OAuth2 authorization flow for an integration."""
        if deprecated and canonical_prefix is not None:
            warn_on_legacy_route(
                request=request,
                response=response,
                notice=LegacyRouteNotice(
                    legacy_path=f"{prefix}/{slug}/authorize",
                    canonical_path=f"{canonical_prefix}/{slug}/authorize",
                ),
            )
        defn = integration_registry.get_definition(slug)
        if defn is None or defn.oauth is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No OAuth configuration for integration: {slug}",
            )

        client_cfg = oauth_config.clients.get(slug)
        if client_cfg is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"OAuth client not configured for integration: {slug}",
            )

        provider = OAuth2Provider(
            spec=defn.oauth,
            client_id=client_cfg.client_id,
            client_secret=client_cfg.client_secret,
        )

        _cleanup_expired()
        state = OAuth2Provider.generate_state()
        redirect_uri = _build_redirect_uri(slug)

        _pending_states[state] = {
            "slug": slug,
            "user_id": principal.user_id,
            "redirect_uri": redirect_uri,
            "expires_at": time.monotonic() + STATE_TTL_SECONDS,
        }

        url = provider.authorization_url(state=state, redirect_uri=redirect_uri)
        return AuthorizeResponse(url=url)

    @router.get("/callback")
    async def oauth_callback(
        request: Request,
        code: str = Query(description="Authorization code from the provider"),
        state: str = Query(description="State parameter for CSRF validation"),
    ) -> HTMLResponse:
        """Handle the OAuth2 callback from the provider."""
        _cleanup_expired()

        pending = _pending_states.pop(state, None)
        if pending is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired OAuth state",
            )

        slug = pending["slug"]
        user_id = pending["user_id"]
        redirect_uri = pending["redirect_uri"]

        defn = integration_registry.get_definition(slug)
        if defn is None or defn.oauth is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Integration definition not found: {slug}",
            )

        client_cfg = oauth_config.clients.get(slug)
        if client_cfg is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"OAuth client not configured: {slug}",
            )

        provider = OAuth2Provider(
            spec=defn.oauth,
            client_id=client_cfg.client_id,
            client_secret=client_cfg.client_secret,
        )

        credentials = await provider.exchange_code(code, redirect_uri)

        credential_name = f"{slug}-oauth-token"
        await credential_store.store(
            owner_type="user",
            owner_id=user_id,
            name=credential_name,
            secret_type=SecretType.OAUTH_TOKEN,
            data=credentials,
            metadata={"source": "oauth2", "integration": slug},
        )

        now = datetime.now(UTC)
        connection = IntegrationConnection(
            id=str(uuid4()),
            owner_id=user_id,
            integration_type=defn.integration_type,
            adapter=defn.adapter,
            credential_name=credential_name,
            config={},
            enabled=True,
            created_at=now,
            updated_at=now,
            slug=slug,
        )
        await integration_repo.save_connection(connection)

        logger.info(
            "OAuth connection created: slug=%s user=%s",
            slug,
            user_id,
        )

        body_style = (
            "font-family:system-ui;display:flex;align-items:center;"
            "justify-content:center;height:100vh;margin:0;"
            "background:#09090b;color:#fafafa;"
        )
        html = (
            "<!DOCTYPE html>"
            "<html><head><title>Connected</title></head>"
            f'<body style="{body_style}">'
            '<div style="text-align:center;">'
            f"<h2>Connected to {defn.name}</h2>"
            "<p>This window will close automatically.</p>"
            "<script>setTimeout(function(){window.close()},2000)"
            "</script></div></body></html>"
        )
        response = HTMLResponse(content=html)
        if deprecated and canonical_prefix is not None:
            warn_on_legacy_route(
                request=request,
                response=response,
                notice=LegacyRouteNotice(
                    legacy_path=f"{prefix}/callback",
                    canonical_path=f"{canonical_prefix}/callback",
                ),
            )
        return response

    @router.post("/{slug}/disconnect", status_code=status.HTTP_204_NO_CONTENT)
    async def disconnect(
        slug: str,
        request: Request,
        response: Response,
        principal: Principal = Depends(extract_principal),
    ) -> None:
        """Disconnect an OAuth integration — revoke token and remove connection."""
        if deprecated and canonical_prefix is not None:
            warn_on_legacy_route(
                request=request,
                response=response,
                notice=LegacyRouteNotice(
                    legacy_path=f"{prefix}/{slug}/disconnect",
                    canonical_path=f"{canonical_prefix}/{slug}/disconnect",
                ),
            )
        connections = await integration_repo.list_connections(principal.user_id)
        connection = next((c for c in connections if c.slug == slug), None)
        if connection is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No connection found for integration: {slug}",
            )

        defn = integration_registry.get_definition(slug)
        if defn is not None and defn.oauth is not None:
            client_cfg = oauth_config.clients.get(slug)
            if client_cfg is not None:
                cred_value = await credential_store.get_value(
                    "user",
                    principal.user_id,
                    connection.credential_name,
                )
                if cred_value:
                    token = cred_value.get("access_token", "")
                    if token:
                        provider = OAuth2Provider(
                            spec=defn.oauth,
                            client_id=client_cfg.client_id,
                            client_secret=client_cfg.client_secret,
                        )
                        await provider.revoke_token(token)

        await credential_store.delete(
            "user",
            principal.user_id,
            connection.credential_name,
        )
        await integration_repo.delete_connection(connection.id)

    return router


def create_oauth_router(
    oauth_config: OAuthConfig,
    integration_registry: IntegrationRegistry,
    credential_store: CredentialStorePort,
    integration_repo: IntegrationRepository,
) -> APIRouter:
    """Create the legacy Volundr OAuth router."""
    return _build_oauth_router(
        oauth_config,
        integration_registry,
        credential_store,
        integration_repo,
        prefix="/api/v1/volundr/integrations/oauth",
        deprecated=True,
        canonical_prefix="/api/v1/integrations/oauth",
    )


def create_canonical_oauth_router(
    oauth_config: OAuthConfig,
    integration_registry: IntegrationRegistry,
    credential_store: CredentialStorePort,
    integration_repo: IntegrationRepository,
) -> APIRouter:
    """Create the canonical shared OAuth router."""
    return _build_oauth_router(
        oauth_config,
        integration_registry,
        credential_store,
        integration_repo,
        prefix="/api/v1/integrations/oauth",
    )
