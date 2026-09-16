"""User-owned MCP connections, using browser OAuth and the existing vault.

Pending state and PKCE verifiers live in the credential store so callbacks can
reach any replica. Runtime processes receive access tokens, never client secrets
or refresh tokens.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.routing import APIRoute
from mcp.client.auth.exceptions import OAuthFlowError
from mcp.client.auth.oauth2 import PKCEParameters
from mcp.client.auth.utils import issuers_match
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import BaseModel, Field

from niuu.ports.credentials import CredentialRefreshLockPort, OAuthApplicationStorePort
from volundr.adapters.inbound.auth import extract_principal
from volundr.adapters.outbound.mcp_oauth import MCPOAuthDiscovery, validate_mcp_url
from volundr.config import OAuthConfig
from volundr.domain.models import IntegrationConnection, IntegrationType, Principal, SecretType
from volundr.domain.ports import CredentialStorePort, IntegrationRepository


class MCPConnectRequest(BaseModel):
    server_url: str = Field(max_length=2048)
    name: str = Field(default="MCP server", max_length=100)
    connection_id: str = Field(default="", max_length=100)
    client_id: str = Field(default="", max_length=2048)
    client_secret: str = Field(default="", max_length=4096)
    token_endpoint_auth_method: str = "none"
    api_token: str = Field(default="", max_length=16384)
    auth_header: str = Field(default="Authorization", max_length=100)
    auth_prefix: str = Field(default="Bearer ", max_length=100)


class _MCPOAuthRoute(APIRoute):
    """Do not expose token responses or credentials in validation tracebacks."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_handler(request):
            try:
                return await handler(request)
            except (ValueError, OAuthFlowError, httpx.HTTPError):
                raise HTTPException(
                    422, "MCP authentication failed; check the server and client configuration"
                ) from None

        return safe_handler


def _identity(*parts: str) -> str:
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


def create_mcp_oauth_router(
    config: OAuthConfig,
    store: CredentialStorePort,
    repository: IntegrationRepository,
    credential_lock: CredentialRefreshLockPort | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/mcp", tags=["MCP integrations"], route_class=_MCPOAuthRoute)
    discovery = MCPOAuthDiscovery(request_timeout=config.mcp_request_timeout_seconds)
    base = config.redirect_base_url.rstrip("/")
    callback = f"{base}/api/v1/integrations/oauth/mcp/callback"
    client_url = f"{base}/api/v1/integrations/oauth/mcp/client-metadata"

    def client_metadata() -> dict:
        validate_mcp_url(callback)
        return {
            "client_id": client_url,
            "client_name": "Niuu",
            "redirect_uris": [callback],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }

    @router.get("/client-metadata")
    async def metadata_document() -> dict:
        return client_metadata()

    async def public_client_document() -> bool:
        # Browser callbacks can work on a private install; CIMD cannot, because
        # the authorization server itself must fetch this document publicly.
        try:
            async with discovery.client() as http:
                response = await http.get(client_url)
            return response.status_code == 200 and response.json().get("client_id") == client_url
        except (ValueError, OSError, httpx.HTTPError):
            return False

    async def connection_for(
        data: MCPConnectRequest, principal: Principal
    ) -> IntegrationConnection:
        if data.connection_id:
            connection = await repository.get_connection(data.connection_id)
            if (
                connection is None
                or connection.owner_id != principal.user_id
                or connection.slug != "mcp"
                or connection.config.get("tenant_id") != principal.tenant_id
            ):
                raise HTTPException(404, "MCP connection not found")
            if connection.config["mcp_url"] != data.server_url:
                raise HTTPException(409, "Create a new connection when changing the MCP server")
            return replace(connection, config=dict(connection.config))
        now = datetime.now(UTC)
        connection_id = str(uuid4())
        return IntegrationConnection(
            id=connection_id,
            owner_id=principal.user_id,
            integration_type=IntegrationType.MCP,
            adapter="",
            credential_name=f"mcp-{connection_id}",
            config={
                "mcp_url": data.server_url,
                "name": data.name,
                "tenant_id": principal.tenant_id,
            },
            enabled=True,
            created_at=now,
            updated_at=now,
            slug="mcp",
        )

    @router.post("/discover")
    async def discover(data: MCPConnectRequest, principal: Principal = Depends(extract_principal)):
        try:
            result = await discovery.discover(data.server_url)
        except (ValueError, httpx.HTTPError) as exc:
            raise HTTPException(
                422, "MCP OAuth discovery failed; check the server URL and metadata"
            ) from exc
        return {
            "resource": result.resource,
            "issuer": str(result.metadata.issuer),
            "scope": result.scope,
            "registration_available": bool(
                result.metadata.registration_endpoint
                or (
                    result.metadata.client_id_metadata_document_supported
                    and await public_client_document()
                )
            ),
        }

    @router.post("/connect")
    async def connect(data: MCPConnectRequest, principal: Principal = Depends(extract_principal)):
        validate_mcp_url(data.server_url)
        connection = await connection_for(data, principal)
        if data.api_token:
            if (
                not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*", data.auth_header)
                or data.auth_header.lower() in {"host", "cookie", "proxy-authorization"}
                or any(c in data.api_token + data.auth_prefix for c in "\r\n")
            ):
                raise HTTPException(422, "Invalid MCP authentication header")
            connection.config.update(auth_header=data.auth_header, auth_prefix=data.auth_prefix)
            await store.store(
                "user",
                principal.user_id,
                connection.credential_name,
                SecretType.GENERIC,
                {"access_token": data.api_token},
                {
                    "integration": "mcp",
                    "tenant_id": principal.tenant_id,
                    "mcp_url": data.server_url,
                },
            )
            await repository.save_connection(connection)
            return {"connection_id": connection.id}

        connection.config.update(auth_header="Authorization", auth_prefix="Bearer ")
        info = await discovery.discover(data.server_url)
        metadata = info.metadata
        # The existing OpenBao engine owns token renewal. Never quietly switch
        # to a process-local refresh loop on an unconfigured installation.
        if not isinstance(store, OAuthApplicationStorePort) or not store.manages_oauth_applications:
            raise HTTPException(503, "MCP OAuth requires managed OpenBao OAuth applications")
        if not store.supports_resource_indicators:
            raise HTTPException(503, "Upgrade the OpenBao OAuth plugin for MCP resource binding")
        if credential_lock is None:
            raise HTTPException(503, "MCP OAuth state coordination is not configured")
        registration = client_metadata()
        if info.scope:
            registration["scope"] = info.scope
        client_key = _identity(
            principal.tenant_id, principal.user_id, str(metadata.issuer), callback
        )
        client = None
        if data.client_id:
            client = {
                "client_id": data.client_id,
                "client_secret": data.client_secret,
                "token_endpoint_auth_method": data.token_endpoint_auth_method,
            }
        if client is None:
            saved = await store.get_value("system", "mcp-oauth-clients", client_key)
            client = json.loads(saved["client"]) if saved else None
            if client and client.get("client_secret_expires_at"):
                if int(client["client_secret_expires_at"]) <= datetime.now(UTC).timestamp():
                    client = None
        if (
            client is None
            and metadata.client_id_metadata_document_supported
            and await public_client_document()
        ):
            client = {"client_id": client_url, "token_endpoint_auth_method": "none"}
        if client is None and metadata.registration_endpoint:
            registration.pop("client_id")
            async with discovery.client() as http:
                response = await http.post(str(metadata.registration_endpoint), json=registration)
            if response.status_code not in {200, 201}:
                raise HTTPException(
                    422, "OAuth client registration failed; use a registered client"
                )
            client = OAuthClientInformationFull.model_validate_json(response.content).model_dump(
                mode="json"
            )
        if client is None:
            raise HTTPException(422, "This server requires a registered OAuth client ID")
        method = client.get("token_endpoint_auth_method") or "none"
        supported = metadata.token_endpoint_auth_methods_supported or ["client_secret_basic"]
        if (
            method not in {"none", "client_secret_basic", "client_secret_post"}
            or method not in supported
        ):
            raise HTTPException(422, "OAuth client authentication method is not supported")
        if method != "none" and not client.get("client_secret"):
            raise HTTPException(422, "OAuth client requires a client secret")
        await store.store(
            "system",
            "mcp-oauth-clients",
            client_key,
            SecretType.GENERIC,
            {"client": json.dumps(client)},
        )
        # Server definitions are immutable identities: changing the issuer or
        # client must not reroute refresh tokens for an existing grant.
        app = _identity(
            client_key,
            str(metadata.token_endpoint),
            client["client_id"],
            client.get("client_secret") or "",
            method,
        )
        await store.configure_oauth_application(
            slug="mcp",
            app=app,
            client_id=client["client_id"],
            client_secret=client.get("client_secret") or "",
            authorize_url=str(metadata.authorization_endpoint),
            token_url=str(metadata.token_endpoint),
            token_endpoint_auth_method=method,
            public_endpoints_only=True,
        )
        pkce = PKCEParameters.generate()
        state = secrets.token_urlsafe(32)
        pending = {
            "owner_id": principal.user_id,
            "tenant_id": principal.tenant_id,
            "connection_id": connection.id,
            "credential_name": connection.credential_name,
            "config": connection.config,
            "oauth_app": app,
            "client": client,
            "token_url": str(metadata.token_endpoint),
            "issuer": str(metadata.issuer),
            "resource": info.resource,
            "redirect_uri": callback,
            "verifier": pkce.code_verifier,
            "expires_at": (
                datetime.now(UTC) + timedelta(seconds=config.mcp_state_ttl_seconds)
            ).isoformat(),
        }
        await store.store(
            "system",
            "mcp-oauth-pending",
            _identity(state),
            SecretType.GENERIC,
            {"pending": json.dumps(pending)},
        )
        params = {
            "response_type": "code",
            "client_id": client["client_id"],
            "redirect_uri": callback,
            "state": state,
            "resource": info.resource,
            "code_challenge": pkce.code_challenge,
            "code_challenge_method": "S256",
        }
        if info.scope:
            params["scope"] = info.scope
        separator = "&" if "?" in str(metadata.authorization_endpoint) else "?"
        return {"url": str(metadata.authorization_endpoint) + separator + urlencode(params)}

    @router.get("/callback", response_class=HTMLResponse)
    async def complete(state: str, code: str = "", error: str = "", iss: str = ""):
        if credential_lock is None:
            raise HTTPException(503, "MCP OAuth state coordination is not configured")
        async with credential_lock.hold("system", "mcp-oauth-pending", _identity(state)):
            saved = await store.get_value("system", "mcp-oauth-pending", _identity(state))
            if saved is None:
                raise HTTPException(400, "Invalid or expired MCP OAuth state")
            pending = json.loads(saved["pending"])
            if pending["redirect_uri"] != callback:
                raise HTTPException(400, "Complete MCP sign-in on the originating installation")
            await store.delete("system", "mcp-oauth-pending", _identity(state))
        if datetime.fromisoformat(pending["expires_at"]) <= datetime.now(UTC):
            raise HTTPException(400, "MCP sign-in expired; start again")
        if error or not code or (iss and not issuers_match(iss, pending["issuer"])):
            raise HTTPException(400, "MCP authorization failed; start again")
        client = pending["client"]
        params = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": callback,
            "client_id": client["client_id"],
            "code_verifier": pending["verifier"],
            "resource": pending["resource"],
        }
        auth = None
        method = client.get("token_endpoint_auth_method") or "none"
        if method == "client_secret_basic":
            auth = httpx.BasicAuth(client["client_id"], client["client_secret"])
        if method == "client_secret_post":
            params["client_secret"] = client["client_secret"]
        async with discovery.client() as http:
            response = await http.post(pending["token_url"], data=params, auth=auth)
        if response.status_code != 200:
            raise HTTPException(422, "MCP token exchange failed; reconnect")
        token = OAuthToken.model_validate_json(response.content)
        if token.token_type.lower() != "bearer":
            raise HTTPException(422, "MCP server must issue bearer access tokens")
        now = datetime.now(UTC)
        values = {"access_token": token.access_token}
        if token.refresh_token:
            values["refresh_token"] = token.refresh_token
        if token.expires_in is not None:
            values["expires_at"] = (now + timedelta(seconds=token.expires_in)).isoformat()
        await store.store(
            "user",
            pending["owner_id"],
            pending["credential_name"],
            SecretType.OAUTH_TOKEN,
            values,
            {
                "integration": "mcp",
                "tenant_id": pending["tenant_id"],
                "oauth_app": pending["oauth_app"],
                "oauth_token_field": "access_token",
                "mcp_url": pending["config"]["mcp_url"],
                "mcp_resource": pending["resource"],
                "auth_state": "active",
                **(
                    {"auth_expires_at": values["expires_at"]}
                    if "expires_at" in values and not token.refresh_token
                    else {}
                ),
            },
        )
        connection = IntegrationConnection(
            id=pending["connection_id"],
            owner_id=pending["owner_id"],
            integration_type=IntegrationType.MCP,
            adapter="",
            slug="mcp",
            credential_name=pending["credential_name"],
            config=pending["config"],
            enabled=True,
            created_at=now,
            updated_at=now,
        )
        await repository.save_connection(connection)
        return HTMLResponse(
            "<!doctype html><title>MCP connected</title>"
            "<p>MCP connected. You can close this window.</p>"
        )

    return router
