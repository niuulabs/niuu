"""FastAPI REST adapter for OAuth2 integration flows."""

from __future__ import annotations

import logging
import time
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from niuu.http_compat import LegacyRouteNotice, warn_on_legacy_route
from niuu.ports.credentials import CredentialRefreshLockPort
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
from volundr.domain.services.oauth_clients import (
    DEFAULT_APP,
    OAuthClient,
    OAuthClientRegistry,
    app_key,
)

logger = logging.getLogger(__name__)

STATE_TTL_SECONDS = 300  # 5 minutes


class _OAuthAccessFilter(logging.Filter):
    """Keep callback codes and state out of Uvicorn's otherwise normal access log."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) == 5:
            path = str(record.args[2]).partition("?")[0]
            if path in {
                "/api/v1/integrations/oauth/callback",
                "/api/v1/integrations/oauth/mcp/callback",
            }:
                record.args = (*record.args[:2], path, *record.args[3:])
        return True


class AuthorizeResponse(BaseModel):
    """Response for the authorize endpoint."""

    url: str = Field(description="OAuth2 authorization URL to redirect the user to")


class AuthorizeRequest(BaseModel):
    """Per-account values retained across the browser authorization redirect."""

    credential_name: str = Field(default="", max_length=253)
    oauth_app: str = Field(default="", max_length=64)
    config: dict[str, Any] = Field(default_factory=dict)


class DisconnectRequest(BaseModel):
    """Select one account when a provider has multiple connections."""

    connection_id: str = Field(default="", max_length=100)


def _build_oauth_router(
    oauth_config: OAuthConfig,
    integration_registry: IntegrationRegistry,
    credential_store: CredentialStorePort,
    integration_repo: IntegrationRepository,
    *,
    oauth_clients: OAuthClientRegistry | None = None,
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

    def _build_redirect_uri(request_base_url: str) -> str:
        base = (oauth_config.redirect_base_url or request_base_url).rstrip("/")
        return f"{base}{prefix}/callback"

    def _oauth_client(slug: str, app: str = DEFAULT_APP) -> OAuthClient | None:
        if oauth_clients is not None:
            return oauth_clients.get(slug, app)
        configured = oauth_config.clients.get(slug)
        if configured is None or app != DEFAULT_APP:
            return None
        return OAuthClient(
            slug=slug,
            client_id=configured.client_id,
            client_secret=configured.client_secret,
            base_url=configured.base_url,
            source="configured",
        )

    def _provider(slug: str, app: str = DEFAULT_APP) -> OAuth2Provider:
        defn = integration_registry.get_definition(slug)
        if defn is None or defn.oauth is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No OAuth configuration for integration: {slug}",
            )
        client = _oauth_client(slug, app)
        if client is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"OAuth client not configured for integration: {slug}",
            )
        if defn.oauth.client_secret_required and not client.client_secret:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"OAuth client secret not configured for integration: {slug}",
            )
        spec = replace(
            defn.oauth,
            authorize_url=client.endpoint(defn.oauth.authorize_url),
            token_url=client.endpoint(defn.oauth.token_url),
            revoke_url=(client.endpoint(defn.oauth.revoke_url) if defn.oauth.revoke_url else ""),
        )
        return OAuth2Provider(
            spec=spec,
            client_id=client.client_id,
            client_secret=client.client_secret,
        )

    def _validate_config(slug: str, config: dict[str, Any]) -> None:
        defn = integration_registry.get_definition(slug)
        if defn is None:
            return
        missing = [
            key
            for key in defn.config_schema.get("required", [])
            if config.get(key) is None
            or (isinstance(config.get(key), str) and not str(config[key]).strip())
        ]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=[f"'{key}' is required" for key in missing],
            )

    def _start_authorization(
        *,
        slug: str,
        user_id: str,
        tenant_id: str,
        credential_name: str = "",
        oauth_app: str = "",
        config: dict[str, Any] | None = None,
        request_base_url: str = "",
    ) -> AuthorizeResponse:
        defn = integration_registry.get_definition(slug)
        if defn is None or defn.oauth is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No OAuth configuration for integration: {slug}",
            )
        app = app_key(oauth_app)
        provider = _provider(slug, app)
        connection_config = dict(config or {})
        _validate_config(slug, connection_config)
        default_credential_name = (
            defn.credential_enrollment.default_credential_name
            if defn.credential_enrollment is not None
            else f"{slug}-oauth-token"
        )
        resolved_credential_name = credential_name.strip() or default_credential_name

        _cleanup_expired()
        state = OAuth2Provider.generate_state()
        redirect_uri = _build_redirect_uri(request_base_url)
        if not redirect_uri.startswith(("http://", "https://")):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="OAuth redirect base URL is not configured for this install",
            )
        _pending_states[state] = {
            "slug": slug,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "redirect_uri": redirect_uri,
            "credential_name": resolved_credential_name,
            "oauth_app": app,
            "config": connection_config,
            "expires_at": time.monotonic() + STATE_TTL_SECONDS,
        }
        return AuthorizeResponse(
            url=provider.authorization_url(state=state, redirect_uri=redirect_uri)
        )

    @router.get(
        "/{slug}/authorize",
        response_model=AuthorizeResponse,
    )
    async def authorize(
        slug: str,
        request: Request,
        response: Response,
        credential_name: str = "",
        oauth_app: str = "",
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
        return _start_authorization(
            slug=slug,
            user_id=principal.user_id,
            tenant_id=principal.tenant_id,
            credential_name=credential_name,
            oauth_app=oauth_app,
            request_base_url=str(request.base_url),
        )

    @router.post("/{slug}/authorize", response_model=AuthorizeResponse)
    async def authorize_account(
        slug: str,
        data: AuthorizeRequest,
        request: Request,
        principal: Principal = Depends(extract_principal),
    ) -> AuthorizeResponse:
        """Start OAuth while retaining this account's name and adapter configuration."""
        return _start_authorization(
            slug=slug,
            user_id=principal.user_id,
            tenant_id=principal.tenant_id,
            credential_name=data.credential_name,
            oauth_app=data.oauth_app,
            config=data.config,
            request_base_url=str(request.base_url),
        )

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
        credential_name = pending["credential_name"]
        oauth_app = pending["oauth_app"]
        connection_config = dict(pending["config"])

        defn = integration_registry.get_definition(slug)
        if defn is None or defn.oauth is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Integration definition not found: {slug}",
            )

        provider = _provider(slug, oauth_app)

        credentials = await provider.exchange_code(code, redirect_uri)

        existing_connections = await integration_repo.list_connections(user_id)
        collision = next(
            (
                item
                for item in existing_connections
                if item.credential_name == credential_name and item.slug != slug
            ),
            None,
        )
        if collision is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="That credential name is already used by another integration",
            )

        metadata = {
            "source": "oauth2",
            "integration": slug,
            "auth_state": "active",
            "tenant_id": pending["tenant_id"],
            "oauth_app": oauth_app,
            "oauth_token_field": next(
                (k for k, v in defn.oauth.token_field_mapping.items() if v == "access_token"),
                "access_token",
            ),
        }
        if credentials.get("expires_at"):
            metadata["auth_expires_at"] = credentials["expires_at"]
        await credential_store.store(
            owner_type="user",
            owner_id=user_id,
            name=credential_name,
            secret_type=SecretType.OAUTH_TOKEN,
            data=credentials,
            metadata=metadata,
        )

        now = datetime.now(UTC)
        existing = next(
            (
                item
                for item in existing_connections
                if item.slug == slug and item.credential_name == credential_name
            ),
            None,
        )
        connection = IntegrationConnection(
            id=existing.id if existing is not None else str(uuid4()),
            owner_id=user_id,
            integration_type=defn.integration_type,
            adapter=defn.adapter,
            credential_name=credential_name,
            config={**connection_config, "oauth_app": oauth_app},
            enabled=True,
            created_at=existing.created_at if existing is not None else now,
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
        data: DisconnectRequest | None = None,
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
        requested_id = data.connection_id if data is not None else ""
        connection = next(
            (
                candidate
                for candidate in connections
                if candidate.slug == slug and (not requested_id or candidate.id == requested_id)
            ),
            None,
        )
        if connection is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No connection found for integration: {slug}",
            )

        defn = integration_registry.get_definition(slug)
        if defn is not None and defn.oauth is not None:
            oauth_app = str(connection.config.get("oauth_app") or DEFAULT_APP)
            if _oauth_client(slug, oauth_app) is not None:
                cred_value = await credential_store.get_value(
                    "user",
                    principal.user_id,
                    connection.credential_name,
                )
                if cred_value:
                    token = cred_value.get("access_token", "")
                    if token:
                        provider = _provider(slug, oauth_app)
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
    oauth_clients: OAuthClientRegistry | None = None,
    credential_lock: CredentialRefreshLockPort | None = None,
) -> APIRouter:
    """Create the canonical shared OAuth router."""
    access_logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, _OAuthAccessFilter) for item in access_logger.filters):
        access_logger.addFilter(_OAuthAccessFilter())
    router = _build_oauth_router(
        oauth_config,
        integration_registry,
        credential_store,
        integration_repo,
        oauth_clients=oauth_clients,
        prefix="/api/v1/integrations/oauth",
    )
    from volundr.adapters.inbound.rest_mcp_oauth import create_mcp_oauth_router

    router.include_router(
        create_mcp_oauth_router(oauth_config, credential_store, integration_repo, credential_lock)
    )
    return router


def create_canonical_oauth_router(
    oauth_config: OAuthConfig,
    integration_registry: IntegrationRegistry,
    credential_store: CredentialStorePort,
    integration_repo: IntegrationRepository,
    oauth_clients: OAuthClientRegistry | None = None,
    credential_lock: CredentialRefreshLockPort | None = None,
) -> APIRouter:
    """Backward-compatible alias for the canonical shared OAuth router."""
    return create_oauth_router(
        oauth_config,
        integration_registry,
        credential_store,
        integration_repo,
        oauth_clients,
        credential_lock,
    )
