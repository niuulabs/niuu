"""FastAPI REST adapter for integration management."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from niuu.adapters.inbound.rest_integration_models import IntegrationResponse
from niuu.domain.models import SecretType
from niuu.http_compat import LegacyRouteNotice, warn_on_legacy_route
from volundr.adapters.inbound.auth import extract_principal
from volundr.domain.models import (
    CredentialEnrollment,
    IntegrationConnection,
    IntegrationDefinition,
    IntegrationType,
    Principal,
)
from volundr.domain.ports import CredentialStorePort, IntegrationRepository
from volundr.domain.services.credential_enrollment import (
    CredentialEnrollmentError,
    CredentialEnrollmentService,
)
from volundr.domain.services.integration_registry import IntegrationRegistry
from volundr.domain.services.oauth_clients import (
    OAuthClient,
    OAuthClientError,
    OAuthClientRegistry,
    app_key,
)
from volundr.domain.services.tracker_factory import TrackerFactory

logger = logging.getLogger(__name__)


def _sanitize_log(value: object) -> str:
    """Sanitize a value for safe log output (prevent log injection)."""
    return str(value).replace("\n", "\\n").replace("\r", "\\r")


def _can_manage_connection(principal: Principal, connection: IntegrationConnection) -> bool:
    """Return True when the principal may manage the connection."""
    return connection.owner_id == principal.user_id or "volundr:admin" in principal.roles


# --- Request/Response models ---


class IntegrationCreateRequest(BaseModel):
    """Request model for creating an integration connection."""

    model_config = ConfigDict(populate_by_name=True)

    integration_type: str | None = Field(
        default=None,
        validation_alias=AliasChoices("integration_type", "integrationType", "type"),
        min_length=1,
        max_length=50,
        description="Integration category (source_control, issue_tracker, etc.)",
        examples=["issue_tracker"],
    )
    adapter: str | None = Field(
        default=None,
        max_length=500,
        description="Fully-qualified adapter class path (empty for env-only integrations)",
        examples=["volundr.adapters.trackers.linear.LinearAdapter"],
    )
    credential_name: str | None = Field(
        default=None,
        validation_alias=AliasChoices("credential_name", "credentialName"),
        min_length=1,
        max_length=253,
        description="Stored credential name for authentication",
        examples=["linear-api-key"],
    )
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Adapter-specific configuration key-value pairs",
        examples=[{"team_id": "TEAM-1"}],
    )
    enabled: bool = Field(
        default=True,
        description="Whether the integration is active",
        examples=[True],
    )
    slug: str = Field(
        default="",
        max_length=100,
        description="Catalog entry slug (references IntegrationDefinition)",
        examples=["linear"],
    )

    credential: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Optional inline credential payload. When provided, the integrations service stores"
            " it before creating the connection."
        ),
        examples=[{"name": "linear-main", "data": {"api_key": "lin_api_123"}}],
    )


def _secret_type_for_definition(defn: IntegrationDefinition) -> SecretType:
    """Choose a stored secret type for an integration definition."""

    if defn.auth_type == "oauth2_authorization_code":
        return SecretType.OAUTH_TOKEN
    return SecretType.GENERIC


def _validate_required_fields(schema: dict[str, Any], values: dict[str, Any]) -> list[str]:
    """Validate that all required schema fields are present and non-empty."""

    errors: list[str] = []
    for key in schema.get("required", []):
        value = values.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            errors.append(f"'{key}' is required")
    return errors


class IntegrationUpdateRequest(BaseModel):
    """Request model for updating an integration connection."""

    model_config = ConfigDict(populate_by_name=True)

    credential_name: str | None = Field(
        default=None,
        validation_alias=AliasChoices("credential_name", "credentialName"),
        max_length=253,
        description="New credential name (null to keep current)",
        examples=["linear-api-key"],
    )
    config: dict[str, Any] | None = Field(
        default=None,
        description="New adapter config (null to keep current)",
        examples=[{"team_id": "TEAM-2"}],
    )
    enabled: bool | None = Field(
        default=None,
        description="New enabled status (null to keep current)",
        examples=[True],
    )


class MCPServerSpecResponse(BaseModel):
    """Response model for an MCP server spec."""

    name: str = Field(description="MCP server name", examples=["linear-mcp"])
    command: str = Field(description="Command to launch the server", examples=["npx"])
    args: list[str] = Field(description="Command-line arguments", examples=[["@linear/mcp-server"]])
    env_from_credentials: dict[str, str] = Field(
        description="Map of env var name to credential field name",
        examples=[{"LINEAR_API_KEY": "token"}],
    )


class OAuthClientResponse(BaseModel):
    """An OAuth application this install signs in through (never the secret)."""

    slug: str
    app: str = Field(description="Which of the provider's applications; 'default' unless named")
    client_id: str
    has_secret: bool
    source: str = Field(description="configured (oauth.clients) or registered (from the wizard)")


class OAuthClientRegisterRequest(BaseModel):
    app: str = Field(default="", max_length=64, description="A name; empty means 'default'")
    client_id: str = Field(min_length=1, max_length=512)
    client_secret: str = Field(default="", max_length=1024)


class CatalogEntryResponse(BaseModel):
    """Response model for a single catalog entry."""

    id: str = Field(description="Unique integration identifier", examples=["linear"])
    slug: str = Field(description="Unique integration identifier", examples=["linear"])
    name: str = Field(description="Human-readable integration name", examples=["Linear"])
    description: str = Field(
        description="Integration description", examples=["Issue tracking with Linear"]
    )
    integration_type: str = Field(description="Integration category", examples=["issue_tracker"])
    adapter: str = Field(
        description="Fully-qualified adapter class path",
        examples=["volundr.adapters.trackers.linear.LinearAdapter"],
    )
    icon: str = Field(description="Icon identifier for the UI", examples=["linear"])
    credential_schema: dict = Field(
        description="JSON Schema for required credentials",
        examples=[{"type": "object", "properties": {"token": {"type": "string"}}}],
    )
    config_schema: dict = Field(
        description="JSON Schema for adapter configuration",
        examples=[{"type": "object", "properties": {"team_id": {"type": "string"}}}],
    )
    mcp_server: MCPServerSpecResponse | None = Field(
        default=None,
        description="MCP server spec if this integration provides one",
    )
    auth_type: str = Field(
        default="api_key",
        description="Authentication type (api_key, oauth2_authorization_code)",
        examples=["api_key"],
    )
    oauth_scopes: list[str] = Field(
        default_factory=list,
        description="OAuth scopes if auth_type is OAuth",
        examples=[["read", "write"]],
    )
    credential_enrollment: dict[str, str] | None = Field(
        default=None,
        description="Interactive credential enrollment metadata when supported",
    )
    sign_in_available: bool = Field(
        default=False,
        description="Whether the interactive sign-in can actually run on this install",
    )
    sign_in_needs_app: bool = Field(
        default=False,
        description=(
            "The sign-in works through an OAuth application the install owns and none is "
            "registered yet; PUT /oauth-clients/{slug} registers one"
        ),
    )

    @classmethod
    def from_definition(
        cls,
        defn: IntegrationDefinition,
    ) -> CatalogEntryResponse:
        """Create response from domain model."""
        mcp = None
        if defn.mcp_server is not None:
            mcp = MCPServerSpecResponse(
                name=defn.mcp_server.name,
                command=defn.mcp_server.command,
                args=list(defn.mcp_server.args),
                env_from_credentials=dict(defn.mcp_server.env_from_credentials),
            )
        oauth_scopes: list[str] = []
        if defn.oauth is not None:
            oauth_scopes = list(defn.oauth.scopes)
        return cls(
            id=defn.slug,
            slug=defn.slug,
            name=defn.name,
            description=defn.description,
            integration_type=defn.integration_type,
            adapter=defn.adapter,
            icon=defn.icon,
            credential_schema=defn.credential_schema,
            config_schema=defn.config_schema,
            mcp_server=mcp,
            auth_type=defn.auth_type,
            oauth_scopes=oauth_scopes,
            credential_enrollment=(
                {
                    "method": defn.credential_enrollment.method,
                    "credential_field": defn.credential_enrollment.credential_field,
                    "default_credential_name": (defn.credential_enrollment.default_credential_name),
                }
                if defn.credential_enrollment is not None
                else None
            ),
        )


class CredentialEnrollmentStartRequest(BaseModel):
    """Start or resume one user-scoped interactive integration login."""

    model_config = ConfigDict(populate_by_name=True)

    slug: str = Field(min_length=1, max_length=100)
    credential_name: str = Field(
        default="",
        validation_alias=AliasChoices("credential_name", "credentialName"),
        max_length=253,
    )
    oauth_app: str = Field(
        default="",
        validation_alias=AliasChoices("oauth_app", "oauthApp"),
        max_length=64,
        description="Which of the provider's OAuth applications this account signs in through",
    )
    connection_id: str = Field(
        default="",
        validation_alias=AliasChoices("connection_id", "connectionId"),
        max_length=100,
    )


class CredentialEnrollmentCodeRequest(BaseModel):
    code: str = Field(min_length=1, max_length=4096, repr=False)


class CredentialEnrollmentResponse(BaseModel):
    """Secret-free status for an interactive credential enrollment."""

    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    connection_id: str = Field(serialization_alias="connectionId")
    provider_slug: str = Field(serialization_alias="providerSlug")
    credential_name: str = Field(serialization_alias="credentialName")
    state: str
    verification_uri: str = Field(serialization_alias="verificationUri")
    user_code: str = Field(serialization_alias="userCode")
    expires_at: datetime = Field(serialization_alias="expiresAt")
    error_code: str = Field(serialization_alias="errorCode")
    input_required: bool = Field(default=False, serialization_alias="inputRequired")

    @classmethod
    def from_domain(cls, enrollment: CredentialEnrollment) -> CredentialEnrollmentResponse:
        return cls(
            id=enrollment.id,
            connection_id=enrollment.connection_id,
            provider_slug=enrollment.provider_slug,
            credential_name=enrollment.credential_name,
            state=enrollment.state.value,
            verification_uri=enrollment.verification_uri,
            user_code=enrollment.user_code,
            expires_at=enrollment.expires_at,
            error_code=enrollment.error_code,
            input_required=enrollment.method == "claude_setup",
        )


SOURCE_CONTROL_PROBE_LIMIT = 100
PROBE_TIMEOUT_SECONDS = 15.0


async def probe_source_control(
    connection: IntegrationConnection, credential: dict[str, str]
) -> IntegrationTestResult:
    """Prove the token works by listing what it can see, not by checking it exists."""
    provider_name = connection.adapter.rsplit(".", 1)[-1]
    token = credential.get("token", "")
    if not token:
        return IntegrationTestResult(
            success=False, provider=provider_name, error="Credential has no token field"
        )
    config = connection.config or {}
    if connection.slug == "gitlab":
        base = str(config.get("base_url") or "https://gitlab.com").rstrip("/")
        headers = {"PRIVATE-TOKEN": token, "Accept": "application/json"}
        user_url = f"{base}/api/v4/user"
        repos_url = (
            f"{base}/api/v4/projects?membership=true&simple=true"
            f"&order_by=last_activity_at&per_page={SOURCE_CONTROL_PROBE_LIMIT}"
        )
        name_key, login_key = "path_with_namespace", "username"
    else:
        base = str(config.get("base_url") or "https://api.github.com").rstrip("/")
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        user_url = f"{base}/user"
        repos_url = f"{base}/user/repos?sort=updated&per_page={SOURCE_CONTROL_PROBE_LIMIT}"
        name_key, login_key = "full_name", "login"
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS, headers=headers) as client:
        user_response = await client.get(user_url)
        if user_response.status_code != 200:
            return IntegrationTestResult(
                success=False,
                provider=provider_name,
                error=(
                    f"The token was rejected (HTTP {user_response.status_code}). "
                    "Check it is valid and has repository access."
                ),
            )
        user = str((user_response.json() or {}).get(login_key) or "")
        repos_response = await client.get(repos_url)
    if repos_response.status_code != 200:
        return IntegrationTestResult(
            success=False,
            provider=provider_name,
            user=user or None,
            error=f"Signed in as {user}, but listing repositories failed "
            f"(HTTP {repos_response.status_code}).",
        )
    rows = repos_response.json() or []
    names = [str(row.get(name_key) or "") for row in rows if isinstance(row, dict)]
    names = [name for name in names if name]
    more = 'rel="next"' in repos_response.headers.get("link", "")
    count = f"{len(names)}+" if more else str(len(names))
    return IntegrationTestResult(
        success=True,
        provider=provider_name,
        user=user or None,
        detail=f"{count} repositories reachable",
        repositories=names,
    )


async def probe_ai_provider(
    connection: IntegrationConnection,
    definition: IntegrationDefinition | None,
    credential: dict[str, str],
) -> IntegrationTestResult:
    """Call the provider's cheapest authenticated endpoint with the stored key."""
    provider_name = connection.adapter.rsplit(".", 1)[-1] or connection.slug
    probe = dict(definition.key_probe) if definition is not None else {}
    if not probe:
        # Sign-in credentials (subscriptions) and providers without a probe
        # are proven when a session uses them; existence is all we can say.
        return IntegrationTestResult(
            success=True, provider=provider_name, detail="Credential stored"
        )
    key = credential.get("api_key", "")
    if not key:
        return IntegrationTestResult(
            success=False, provider=provider_name, error="Credential has no api_key field"
        )
    headers = dict(probe.get("headers") or {})
    auth = str(probe.get("auth") or "bearer")
    if auth == "bearer":
        headers["Authorization"] = f"Bearer {key}"
    else:
        headers[auth] = key
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
        response = await client.get(str(probe["url"]), headers=headers)
    if response.status_code in (401, 403):
        return IntegrationTestResult(
            success=False,
            provider=provider_name,
            error=f"The provider rejected this key (HTTP {response.status_code}).",
        )
    if response.status_code != 200:
        return IntegrationTestResult(
            success=False,
            provider=provider_name,
            error=f"The provider answered HTTP {response.status_code} to the key check.",
        )
    payload = response.json() if "json" in response.headers.get("content-type", "") else {}
    models = payload.get("data") if isinstance(payload, dict) else None
    detail = (
        f"Key works · {len(models)} models available" if isinstance(models, list) else "Key works"
    )
    return IntegrationTestResult(success=True, provider=provider_name, detail=detail)


class IntegrationTestResult(BaseModel):
    """Response model for testing an integration connection."""

    success: bool = Field(description="Whether the test connection succeeded", examples=[True])
    provider: str = Field(description="Provider name", examples=["Linear"])
    workspace: str | None = Field(
        default=None,
        description="Workspace name if connected",
        examples=["My Workspace"],
    )
    user: str | None = Field(
        default=None,
        description="Authenticated user if connected",
        examples=["user@example.com"],
    )
    detail: str | None = Field(
        default=None,
        description="What the check proved, e.g. '42 repositories' or '31 models'",
    )
    repositories: list[str] = Field(
        default_factory=list,
        description="Repositories the credential can see (first page), for source control",
    )
    error: str | None = Field(
        default=None,
        description="Error message if test failed",
        examples=[None],
    )


# --- Router factory ---


def _build_integrations_router(
    integration_repo: IntegrationRepository,
    tracker_factory: TrackerFactory,
    *,
    prefix: str,
    deprecated: bool = False,
    canonical_prefix: str | None = None,
    registry: IntegrationRegistry | None = None,
    credential_store: CredentialStorePort | None = None,
    credential_enrollment_service: CredentialEnrollmentService | None = None,
    oauth_clients: OAuthClientRegistry | None = None,
) -> APIRouter:
    """Create FastAPI router for integration management endpoints."""
    router = APIRouter(
        prefix=prefix,
        tags=["Integrations"],
    )

    async def integration_response(connection: IntegrationConnection) -> IntegrationResponse:
        if credential_store is None:
            return IntegrationResponse.from_connection(connection)
        credential = await credential_store.get(
            "user",
            connection.owner_id,
            connection.credential_name,
        )
        if credential is None:
            return IntegrationResponse.from_connection(
                connection,
                credential_status="missing",
            )
        metadata = credential.metadata
        expires_at = metadata.get("auth_expires_at")
        auth_state = str(metadata.get("auth_state") or "configured")
        if expires_at and datetime.fromisoformat(str(expires_at)) <= datetime.now(UTC):
            auth_state = "auth_required"
        return IntegrationResponse.from_connection(
            connection,
            credential_status=auth_state,
            credential_expires_at=str(expires_at) if expires_at else None,
            credential_error_code=(
                str(metadata["auth_error_code"]) if metadata.get("auth_error_code") else None
            ),
            credential_status_updated_at=(
                str(metadata["auth_state_updated_at"])
                if metadata.get("auth_state_updated_at")
                else None
            ),
        )

    @router.get(
        "/catalog",
        response_model=list[CatalogEntryResponse],
    )
    async def list_catalog(
        request: Request,
        response: Response,
    ) -> list[CatalogEntryResponse]:
        """List all available integration definitions from the catalog."""
        if deprecated and canonical_prefix is not None:
            warn_on_legacy_route(
                request=request,
                response=response,
                notice=LegacyRouteNotice(
                    legacy_path=f"{prefix}/catalog",
                    canonical_path=f"{canonical_prefix}/catalog",
                ),
            )
        if registry is None:
            return []
        definitions = registry.list_definitions()
        entries = []
        for definition in definitions:
            entry = CatalogEntryResponse.from_definition(definition)
            if credential_enrollment_service is not None:
                entry.sign_in_available = credential_enrollment_service.available(definition.slug)
            if oauth_clients is not None and not entry.sign_in_available:
                entry.sign_in_needs_app = oauth_clients.supports(
                    definition.slug
                ) and not oauth_clients.has_any(definition.slug)
            entries.append(entry)
        return entries

    def _require_oauth_clients() -> OAuthClientRegistry:
        if oauth_clients is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="OAuth applications cannot be registered on this install",
            )
        return oauth_clients

    def _client_response(client: OAuthClient) -> OAuthClientResponse:
        return OAuthClientResponse(
            slug=client.slug,
            app=client.app,
            client_id=client.client_id,
            has_secret=bool(client.client_secret),
            source=client.source,
        )

    @router.get("/oauth-clients", response_model=list[OAuthClientResponse])
    async def list_oauth_clients(
        principal: Principal = Depends(extract_principal),
    ) -> list[OAuthClientResponse]:
        """The OAuth applications this install signs in through."""
        del principal
        return [_client_response(client) for client in _require_oauth_clients().list()]

    @router.put("/oauth-clients/{slug}", response_model=OAuthClientResponse)
    async def register_oauth_client(
        data: OAuthClientRegisterRequest,
        slug: str = Path(description="Integration slug, e.g. github"),
        principal: Principal = Depends(extract_principal),
    ) -> OAuthClientResponse:
        """Register the application (client id, optional secret) this install signs in through."""
        clients = _require_oauth_clients()
        try:
            client = await clients.register(
                slug, data.client_id, data.client_secret, app=app_key(data.app)
            )
        except OAuthClientError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
            ) from exc
        logger.info(
            "OAuth application %r for %s registered by %s", client.app, slug, principal.user_id
        )
        return _client_response(client)

    @router.delete("/oauth-clients/{slug}", status_code=status.HTTP_204_NO_CONTENT)
    async def remove_oauth_client(
        slug: str = Path(description="Integration slug, e.g. github"),
        app: str = "",
        principal: Principal = Depends(extract_principal),
    ) -> Response:
        """Forget a registered application; a configured one cannot be removed here."""
        del principal
        try:
            await _require_oauth_clients().remove(slug, app_key(app))
        except OAuthClientError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post(
        "/enrollments",
        response_model=CredentialEnrollmentResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def start_credential_enrollment(
        data: CredentialEnrollmentStartRequest,
        principal: Principal = Depends(extract_principal),
    ) -> CredentialEnrollmentResponse:
        if credential_enrollment_service is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Interactive credential enrollment is unavailable",
            )
        try:
            enrollment = await credential_enrollment_service.start(
                principal=principal,
                slug=data.slug,
                credential_name=data.credential_name,
                connection_id=data.connection_id,
                oauth_app=app_key(data.oauth_app) if data.oauth_app else "",
            )
        except CredentialEnrollmentError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc
        return CredentialEnrollmentResponse.from_domain(enrollment)

    @router.get(
        "/enrollments/{enrollment_id}",
        response_model=CredentialEnrollmentResponse,
    )
    async def get_credential_enrollment(
        enrollment_id: UUID,
        principal: Principal = Depends(extract_principal),
    ) -> CredentialEnrollmentResponse:
        if credential_enrollment_service is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Interactive credential enrollment is unavailable",
            )
        try:
            enrollment = await credential_enrollment_service.get(enrollment_id, principal)
        except CredentialEnrollmentError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        return CredentialEnrollmentResponse.from_domain(enrollment)

    @router.delete(
        "/enrollments/{enrollment_id}",
        response_model=CredentialEnrollmentResponse,
    )
    async def cancel_credential_enrollment(
        enrollment_id: UUID,
        principal: Principal = Depends(extract_principal),
    ) -> CredentialEnrollmentResponse:
        if credential_enrollment_service is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Interactive credential enrollment is unavailable",
            )
        try:
            enrollment = await credential_enrollment_service.cancel(enrollment_id, principal)
        except CredentialEnrollmentError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        return CredentialEnrollmentResponse.from_domain(enrollment)

    @router.post("/enrollments/{enrollment_id}/code", response_model=CredentialEnrollmentResponse)
    async def submit_enrollment_code(
        enrollment_id: UUID,
        data: CredentialEnrollmentCodeRequest,
        principal: Principal = Depends(extract_principal),
    ) -> CredentialEnrollmentResponse:
        if credential_enrollment_service is None:
            raise HTTPException(status_code=503, detail="Interactive enrollment is unavailable")
        try:
            enrollment = await credential_enrollment_service.submit_code(
                enrollment_id, principal, data.code
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return CredentialEnrollmentResponse.from_domain(enrollment)

    @router.get(
        "",
        response_model=list[IntegrationResponse],
    )
    async def list_integrations(
        request: Request,
        response: Response,
        principal: Principal = Depends(extract_principal),
    ) -> list[IntegrationResponse]:
        """List the current user's integration connections."""
        if deprecated and canonical_prefix is not None:
            warn_on_legacy_route(
                request=request,
                response=response,
                notice=LegacyRouteNotice(
                    legacy_path=prefix,
                    canonical_path=canonical_prefix,
                ),
            )
        connections = await integration_repo.list_connections(principal.user_id)
        return list(await asyncio.gather(*(integration_response(c) for c in connections)))

    @router.get(
        "/{connection_id}",
        response_model=IntegrationResponse,
    )
    async def get_integration(
        request: Request,
        response: Response,
        connection_id: str = Path(description="Integration connection UUID to retrieve"),
        principal: Principal = Depends(extract_principal),
    ) -> IntegrationResponse:
        """Get a single integration connection."""
        if deprecated and canonical_prefix is not None:
            warn_on_legacy_route(
                request=request,
                response=response,
                notice=LegacyRouteNotice(
                    legacy_path=f"{prefix}/{connection_id}",
                    canonical_path=f"{canonical_prefix}/{connection_id}",
                ),
            )
        existing = await integration_repo.get_connection(connection_id)
        if existing is None or not _can_manage_connection(principal, existing):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Integration not found: {connection_id}",
            )
        return await integration_response(existing)

    @router.post(
        "",
        response_model=IntegrationResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_integration(
        request: Request,
        response: Response,
        data: IntegrationCreateRequest,
        principal: Principal = Depends(extract_principal),
    ) -> IntegrationResponse:
        """Create a new integration connection."""
        if deprecated and canonical_prefix is not None:
            warn_on_legacy_route(
                request=request,
                response=response,
                notice=LegacyRouteNotice(
                    legacy_path=prefix,
                    canonical_path=canonical_prefix,
                ),
            )
        definition = (
            registry.get_definition(data.slug) if registry is not None and data.slug else None
        )
        integration_type = data.integration_type
        adapter = data.adapter or ""

        if definition is not None:
            integration_type = definition.integration_type
            adapter = definition.adapter

        if not integration_type:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="integration_type is required when slug does not resolve to a definition",
            )

        credential_name = data.credential_name
        if data.credential is not None:
            if credential_store is None:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Credential storage is not available for inline integration setup",
                )
            if definition is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="slug is required when creating inline integration credentials",
                )

            credential_name = str(data.credential.get("name", "")).strip()
            credential_data = data.credential.get("data")
            credential_metadata = data.credential.get("metadata") or {}
            if not credential_name:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="credential.name is required",
                )
            if not isinstance(credential_data, dict) or not credential_data:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="credential.data must be a non-empty object",
                )

            credential_errors = _validate_required_fields(
                definition.credential_schema,
                credential_data,
            )
            if credential_errors:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=credential_errors,
                )
            # Storing under a name another connection of the same provider
            # already uses would silently overwrite that account's secret.
            for existing in await integration_repo.list_connections(principal.user_id):
                if existing.slug == definition.slug and existing.credential_name == credential_name:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            f"{definition.name} is already connected as {credential_name!r}; "
                            "give this account another name"
                        ),
                    )

            await credential_store.store(
                owner_type="user",
                owner_id=principal.user_id,
                name=credential_name,
                secret_type=_secret_type_for_definition(definition),
                data={str(key): str(value) for key, value in credential_data.items()},
                metadata={
                    "source": "integration",
                    "integration": definition.slug,
                    "integration_type": str(definition.integration_type),
                    "auth_type": definition.auth_type,
                    "auth_state": "active",
                    **(
                        credential_metadata
                        if isinstance(credential_metadata, dict)
                        else {"metadata": str(credential_metadata)}
                    ),
                },
            )
        elif credential_name and credential_store is not None:
            existing = await credential_store.get("user", principal.user_id, credential_name)
            if existing is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Credential not found: {credential_name}",
                )

        if not credential_name:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="credential_name or credential is required",
            )

        now = datetime.now(UTC)
        connection = IntegrationConnection(
            id=str(uuid4()),
            owner_id=principal.user_id,
            integration_type=IntegrationType(integration_type),
            adapter=adapter,
            credential_name=credential_name,
            config=data.config,
            enabled=data.enabled,
            created_at=now,
            updated_at=now,
            slug=definition.slug if definition is not None else data.slug,
        )
        saved = await integration_repo.save_connection(connection)
        logger.info(
            "Created integration: type=%s adapter=%s user=%s",
            _sanitize_log(integration_type),
            _sanitize_log(adapter),
            principal.user_id,
        )
        return await integration_response(saved)

    @router.put(
        "/{connection_id}",
        response_model=IntegrationResponse,
    )
    async def update_integration(
        request: Request,
        response: Response,
        data: IntegrationUpdateRequest,
        connection_id: str = Path(description="Integration connection UUID to update"),
        principal: Principal = Depends(extract_principal),
    ) -> IntegrationResponse:
        """Update an integration connection."""
        if deprecated and canonical_prefix is not None:
            warn_on_legacy_route(
                request=request,
                response=response,
                notice=LegacyRouteNotice(
                    legacy_path=f"{prefix}/{connection_id}",
                    canonical_path=f"{canonical_prefix}/{connection_id}",
                ),
            )
        existing = await integration_repo.get_connection(connection_id)
        if existing is None or not _can_manage_connection(principal, existing):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Integration not found: {connection_id}",
            )

        now = datetime.now(UTC)
        updated = IntegrationConnection(
            id=existing.id,
            owner_id=existing.owner_id,
            integration_type=existing.integration_type,
            adapter=existing.adapter,
            credential_name=(
                data.credential_name
                if data.credential_name is not None
                else existing.credential_name
            ),
            config=data.config if data.config is not None else existing.config,
            enabled=data.enabled if data.enabled is not None else existing.enabled,
            created_at=existing.created_at,
            updated_at=now,
            slug=existing.slug,
        )
        saved = await integration_repo.save_connection(updated)
        return await integration_response(saved)

    @router.delete(
        "/{connection_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_integration(
        request: Request,
        response: Response,
        connection_id: str = Path(description="Integration connection UUID to delete"),
        principal: Principal = Depends(extract_principal),
    ) -> None:
        """Delete an integration connection."""
        if deprecated and canonical_prefix is not None:
            warn_on_legacy_route(
                request=request,
                response=response,
                notice=LegacyRouteNotice(
                    legacy_path=f"{prefix}/{connection_id}",
                    canonical_path=f"{canonical_prefix}/{connection_id}",
                ),
            )
        existing = await integration_repo.get_connection(connection_id)
        if existing is None or not _can_manage_connection(principal, existing):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Integration not found: {connection_id}",
            )
        await integration_repo.delete_connection(connection_id)

    @router.post(
        "/{connection_id}/test",
        response_model=IntegrationTestResult,
    )
    async def test_integration(
        request: Request,
        response: Response,
        connection_id: str = Path(description="Integration connection UUID to test"),
        principal: Principal = Depends(extract_principal),
    ) -> IntegrationTestResult:
        """Test an integration connection by instantiating the adapter."""
        if deprecated and canonical_prefix is not None:
            warn_on_legacy_route(
                request=request,
                response=response,
                notice=LegacyRouteNotice(
                    legacy_path=f"{prefix}/{connection_id}/test",
                    canonical_path=f"{canonical_prefix}/{connection_id}/test",
                ),
            )
        existing = await integration_repo.get_connection(connection_id)
        if existing is None or not _can_manage_connection(principal, existing):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Integration not found: {connection_id}",
            )

        try:
            if existing.integration_type == IntegrationType.ISSUE_TRACKER:
                adapter = await tracker_factory.create(existing)
                conn_status = await adapter.check_connection()
                return IntegrationTestResult(
                    success=conn_status.connected,
                    provider=conn_status.provider,
                    workspace=conn_status.workspace,
                    user=conn_status.user,
                )

            if existing.integration_type in (
                IntegrationType.SOURCE_CONTROL,
                IntegrationType.AI_PROVIDER,
            ):
                if credential_store is None:
                    return IntegrationTestResult(
                        success=False,
                        provider=existing.adapter.rsplit(".", 1)[-1],
                        error="Credential store not configured",
                    )
                cred_value = await credential_store.get_value(
                    "user",
                    principal.user_id,
                    existing.credential_name,
                )
                if cred_value is None:
                    return IntegrationTestResult(
                        success=False,
                        provider=existing.adapter.rsplit(".", 1)[-1],
                        error="Credential not found",
                    )
                definition = registry.get_definition(existing.slug) if registry else None
                if existing.integration_type == IntegrationType.SOURCE_CONTROL:
                    return await probe_source_control(existing, cred_value)
                return await probe_ai_provider(existing, definition, cred_value)

            return IntegrationTestResult(
                success=False,
                provider=existing.adapter.rsplit(".", 1)[-1],
                error=f"Test not supported for integration type: {existing.integration_type}",
            )
        except Exception as exc:
            logger.exception("Integration test failed for %s", _sanitize_log(connection_id))
            return IntegrationTestResult(
                success=False,
                provider=existing.adapter.rsplit(".", 1)[-1],
                error=str(exc),
            )

    return router


def create_integrations_router(
    integration_repo: IntegrationRepository,
    tracker_factory: TrackerFactory,
    prefix: str = "/api/v1/integrations",
    registry: IntegrationRegistry | None = None,
    credential_store: CredentialStorePort | None = None,
    credential_enrollment_service: CredentialEnrollmentService | None = None,
    oauth_clients: OAuthClientRegistry | None = None,
) -> APIRouter:
    """Create the canonical shared integrations router."""
    return _build_integrations_router(
        integration_repo,
        tracker_factory,
        prefix=prefix,
        registry=registry,
        credential_store=credential_store,
        credential_enrollment_service=credential_enrollment_service,
        oauth_clients=oauth_clients,
    )


def create_canonical_integrations_router(
    integration_repo: IntegrationRepository,
    tracker_factory: TrackerFactory,
    prefix: str = "/api/v1/integrations",
    registry: IntegrationRegistry | None = None,
    credential_store: CredentialStorePort | None = None,
    credential_enrollment_service: CredentialEnrollmentService | None = None,
    oauth_clients: OAuthClientRegistry | None = None,
) -> APIRouter:
    """Backward-compatible alias for the canonical shared integrations router."""
    return create_integrations_router(
        integration_repo,
        tracker_factory,
        prefix=prefix,
        registry=registry,
        credential_store=credential_store,
        credential_enrollment_service=credential_enrollment_service,
        oauth_clients=oauth_clients,
    )
