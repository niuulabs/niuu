"""Ravn FastAPI sub-application.

Mounted by RavnPlugin into the niuu platform server under /api/v1/ravn/.
Exposes session management and persona management endpoints consumed by the
CLI and the web UI.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal
from uuid import UUID

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from starlette import status as http_status

from niuu.adapters.inbound.auth import extract_principal
from niuu.adapters.inbound.auth_context import current_bearer_token
from niuu.adapters.inbound.source_health import set_source_health_header
from niuu.domain.models import Principal
from niuu.domain.services.token_scope import workload_owner_scoped
from niuu.settings_schema import (
    SettingsFieldSchema,
    SettingsProviderSchema,
    SettingsSectionSchema,
)
from niuu.utils import import_class
from ravn.adapters.platform_runtime import HttpPlatformRuntimeAdapter, PlatformRequestError
from ravn.api.persistence_wiring import aclose_stores, build_budget_ledger, build_trigger_store
from ravn.api.residents import (
    ResidentDirectory,
    ResidentLogsUnavailableError,
    ResidentNotFoundError,
    ResidentSessionsUnavailableError,
    StandaloneDiscoveryUnavailableError,
    forward_auth,
)
from ravn.api.trigger_validation import (
    TriggerValidationError,
    check_repo_allowlisted,
    validate_trigger_spec,
)
from ravn.config import Settings
from ravn.domain.budget_totals import BudgetTotals
from ravn.domain.trigger_record import TriggerRecord
from ravn.dynamic_adapters import build_dynamic_adapter
from ravn.ports.budget_ledger import BudgetLedgerPort
from ravn.ports.platform_runtime import PlatformRuntimePort
from ravn.ports.resident_discovery import ResidentDiscoveryPort
from ravn.ports.resident_logs import ResidentLogPort, ResidentLogSourceError
from ravn.ports.resident_roster import ResidentRosterPort
from ravn.ports.trigger_store import TriggerStorePort

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from ravn.ports.persona import PersonaRegistryPort


logger = logging.getLogger(__name__)

#: Roles that may delete a trigger they do not own (mirrors residents.py's
#: `_is_discovered_visible` admin override).
_TRIGGER_ADMIN_ROLES = frozenset({"volundr:admin", "ravn:admin"})


class TriggerCreateRequest(BaseModel):
    kind: str = Field(min_length=1, max_length=50)
    persona_name: str = Field(min_length=1, max_length=255)
    spec: str = Field(min_length=1, max_length=500)
    enabled: bool = True
    repo: str = Field(default="", max_length=255)


class BudgetSpendRequest(BaseModel):
    cost_usd: float = Field(ge=0)
    model: str = Field(min_length=1, max_length=255)
    cap_usd: float = Field(ge=0)
    warn_at: float = Field(ge=0, le=1)


class ResidentCreateRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    name: str = Field(min_length=1, max_length=255)
    profile_id: str = Field(min_length=1, max_length=100)
    persona_name: str = Field(default="", max_length=255)
    model: str = Field(default="", max_length=255)
    flock_id: UUID | None = None
    flock_member_id: UUID | None = None
    flock_role: str = Field(default="", max_length=100)
    flock_peer_id: str = Field(default="", max_length=255)
    realm_id: UUID | None = None


_FORWARDED_PLATFORM_STATUSES = frozenset({401, 403, 404, 409, 422})


def _raise_platform_error(exc: httpx.HTTPStatusError) -> None:
    try:
        payload = exc.response.json()
    except ValueError:
        payload = {}
    detail = payload.get("detail") if isinstance(payload, dict) else None
    raise HTTPException(
        status_code=exc.response.status_code,
        detail=detail or str(exc),
    ) from exc


def create_app(
    persona_loader: PersonaRegistryPort | None = None,
    settings: Settings | None = None,
    resident_discovery: ResidentDiscoveryPort | None = None,
    platform_runtime: PlatformRuntimePort | None = None,
    resident_roster: ResidentRosterPort | None = None,
    resident_logs: ResidentLogPort | None = None,
) -> FastAPI:
    """Create and return the Ravn FastAPI sub-application.

    Args:
        persona_loader: Optional persona registry. When provided, the persona
            CRUD routes are mounted at /api/v1/ravn/personas. When omitted
            (e.g. in tests that only need session endpoints) persona routes
            are not included.
    """
    app = FastAPI(title="Ravn API", docs_url=None, redoc_url=None)

    @app.exception_handler(PlatformRequestError)
    async def platform_request_failed(request: Request, exc: PlatformRequestError) -> JSONResponse:
        # The platform's refusal of the caller's credential is the caller's
        # answer; anything else it could not serve is an upstream failure.
        status_code = (
            exc.status_code
            if exc.status_code in _FORWARDED_PLATFORM_STATUSES
            else http_status.HTTP_502_BAD_GATEWAY
        )
        return JSONResponse(status_code=status_code, content={"detail": exc.detail})

    @app.exception_handler(StandaloneDiscoveryUnavailableError)
    async def standalone_discovery_unavailable(
        request: Request, exc: StandaloneDiscoveryUnavailableError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": str(exc)},
        )

    loaded_settings = settings or Settings()
    app.state.identity = import_class(loaded_settings.api_auth.adapter)(
        **loaded_settings.api_auth.kwargs
    )
    # gateway.platform.base_url is the canonical "where is the platform API"
    # setting (config file, or RAVN_GATEWAY__PLATFORM__BASE_URL env override).
    platform = platform_runtime or HttpPlatformRuntimeAdapter(
        base_url=loaded_settings.gateway.platform.base_url,
        timeout_seconds=loaded_settings.gateway.platform.timeout,
    )
    if resident_logs is None:
        resident_logs = build_dynamic_adapter(loaded_settings.resident_logs)
    resident_directory = ResidentDirectory(
        platform=platform,
        discovery=resident_discovery,
        roster=resident_roster,
        logs=resident_logs,
        platform_tenant_id=loaded_settings.platform_tenant_id,
    )
    trigger_store = build_trigger_store(loaded_settings.trigger_store)
    budget_ledger = build_budget_ledger(loaded_settings.budget_ledger)
    # See TriggerExecutionConfig: this process only stores triggers, it
    # cannot detect whether any resident actually executes them.
    _trigger_execution_enabled = loaded_settings.trigger_execution.enabled

    @app.get("/api/v1/ravn/status")
    async def status_endpoint(
        request: Request,
        response: Response,
        principal: Principal = Depends(extract_principal),
    ) -> dict:
        """Return status derived from live session and resident discovery."""
        auth_headers, auth_params = forward_auth(request)
        try:
            ravens = await resident_directory.list_ravens(
                principal,
                auth_headers,
                auth_params,
            )
            sessions_result = await resident_directory.list_sessions(
                principal,
                auth_headers,
                auth_params,
            )
        except Exception as exc:
            logger.warning("status: runtime discovery unavailable", exc_info=True)
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Ravn runtime discovery is unavailable",
            ) from exc
        set_source_health_header(response, sessions_result.source_failures)
        return {
            "service": "ravn",
            "session_count": len(sessions_result.sessions),
            "fleet_member_count": len(ravens),
            # A degraded contributor (e.g. standalone discovery down) means
            # the session/fleet counts above are an undercount, not a clean
            # "everything reachable" — do not claim healthy over that.
            "healthy": not sessions_result.source_failures,
        }

    @app.get("/api/v1/ravn/settings", response_model=SettingsProviderSchema)
    async def settings_endpoint(
        request: Request,
        response: Response,
        principal: Principal = Depends(extract_principal),
    ) -> SettingsProviderSchema:
        auth_headers, auth_params = forward_auth(request)
        try:
            ravens = await resident_directory.list_ravens(
                principal,
                auth_headers,
                auth_params,
            )
            sessions_result = await resident_directory.list_sessions(
                principal,
                auth_headers,
                auth_params,
            )
        except Exception as exc:
            logger.warning("settings: runtime discovery unavailable", exc_info=True)
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Ravn runtime discovery is unavailable",
            ) from exc
        sessions = sessions_result.sessions
        set_source_health_header(response, sessions_result.source_failures)
        return SettingsProviderSchema(
            title="Ravn",
            subtitle="runtime and agent settings",
            scope="service",
            sections=[
                SettingsSectionSchema(
                    id="runtime",
                    label="Runtime",
                    description="Mounted Ravn runtime capabilities and current fleet state.",
                    fields=[
                        SettingsFieldSchema(
                            key="persona_registry_available",
                            label="Persona Registry",
                            type="boolean",
                            value=persona_loader is not None,
                            description=(
                                "Whether persona-backed runtime routes are "
                                "mounted in this host profile."
                            ),
                            read_only=True,
                        ),
                        SettingsFieldSchema(
                            key="active_session_count",
                            label="Active Session Count",
                            type="number",
                            value=len(sessions),
                            read_only=True,
                        ),
                        SettingsFieldSchema(
                            key="fleet_member_count",
                            label="Fleet Member Count",
                            type="number",
                            value=len(ravens),
                            read_only=True,
                        ),
                        SettingsFieldSchema(
                            key="trigger_store_available",
                            label="Trigger Store",
                            type="boolean",
                            value=trigger_store is not None,
                            description=(
                                "Durable trigger storage — opt-in (trigger_store.adapter, "
                                "empty by default); the chart renders the Postgres adapter "
                                "when database.enabled or the file adapter when "
                                "persistence.enabled. False means POST/GET/DELETE "
                                "/api/v1/ravn/triggers return 503. Storage being available "
                                "does not mean any resident executes what is stored here — "
                                "see trigger_execution_enabled."
                            ),
                            read_only=True,
                        ),
                        SettingsFieldSchema(
                            key="trigger_execution_enabled",
                            label="Trigger Execution",
                            type="boolean",
                            value=_trigger_execution_enabled,
                            description=(
                                "Whether any resident in this deployment actually polls "
                                "and runs stored triggers (trigger_execution.enabled, "
                                "operator-declared — see ravn.config.TriggerExecutionConfig "
                                "for why this process cannot detect it itself). False means "
                                "triggers can be created and stored but nothing runs them."
                            ),
                            read_only=True,
                        ),
                        SettingsFieldSchema(
                            key="budget_store_available",
                            label="Budget Store",
                            type="boolean",
                            value=budget_ledger is not None,
                            description=(
                                "Durable budget ledger — opt-in (budget_ledger.adapter, "
                                "empty by default); the chart renders the Postgres adapter "
                                "when database.enabled or the file adapter when "
                                "persistence.enabled. False means /api/v1/ravn/budget/* "
                                "return 503. Written by each resident's DailyBudgetTracker "
                                "as real spend happens."
                            ),
                            read_only=True,
                        ),
                    ],
                )
            ],
        )

    @app.get("/api/v1/ravn/sessions")
    async def list_sessions_endpoint(
        request: Request,
        response: Response,
        principal: Principal = Depends(extract_principal),
    ) -> list[dict]:
        """List the caller's live ravn sessions (flock rooms + residents)."""
        auth_headers, auth_params = forward_auth(request)
        result = await resident_directory.list_sessions(principal, auth_headers, auth_params)
        set_source_health_header(response, result.source_failures)
        return result.sessions

    @app.get("/api/v1/ravn/ravens")
    async def list_ravens_endpoint(
        request: Request,
        principal: Principal = Depends(extract_principal),
        visibility: Literal["system"] | None = Query(
            default=None,
            description=(
                "`system` lists only residents visible to every member of the "
                "caller's org, without the caller's own; for shared views such as "
                "the Observatory."
            ),
        ),
    ) -> list[dict]:
        """List durable residents and visible compatibility deployments."""
        if visibility == "system":
            return await resident_directory.list_system_ravens(principal)
        auth_headers, auth_params = forward_auth(request)
        return await resident_directory.list_ravens(principal, auth_headers, auth_params)

    @app.post("/api/v1/ravn/ravens", status_code=http_status.HTTP_201_CREATED)
    async def create_raven_endpoint(
        body: ResidentCreateRequest,
        request: Request,
        _: Principal = Depends(extract_principal),
    ) -> dict:
        """Deploy one resident through this target's configured backend."""
        auth_headers, auth_params = forward_auth(request)
        try:
            return await resident_directory.create_raven(
                body.model_dump(mode="json"),
                auth_headers,
                auth_params,
            )
        except httpx.HTTPStatusError as exc:
            _raise_platform_error(exc)

    async def control_raven_endpoint(
        ravn_id: str,
        action: str,
        request: Request,
    ) -> dict:
        auth_headers, auth_params = forward_auth(request)
        try:
            return await resident_directory.control_raven(
                ravn_id,
                action,
                auth_headers,
                auth_params,
            )
        except httpx.HTTPStatusError as exc:
            _raise_platform_error(exc)

    @app.post("/api/v1/ravn/ravens/{ravn_id}/restart")
    async def restart_raven_endpoint(
        ravn_id: str,
        request: Request,
        _: Principal = Depends(extract_principal),
    ) -> dict:
        return await control_raven_endpoint(ravn_id, "restart", request)

    @app.get("/api/v1/ravn/ravens/{ravn_id}/logs")
    async def raven_logs_endpoint(
        ravn_id: str,
        request: Request,
        lines: int = Query(default=200, ge=1, le=5000),
        source: list[str] = Query(default_factory=list),
        min_level: str = Query(default="", max_length=32),
        principal: Principal = Depends(extract_principal),
    ) -> dict:
        auth_headers, auth_params = forward_auth(request)
        try:
            return await resident_directory.get_raven_logs(
                ravn_id,
                principal=principal,
                lines=lines,
                sources=tuple(source),
                min_level=min_level,
                auth_headers=auth_headers,
                auth_params=auth_params,
            )
        except httpx.HTTPStatusError as exc:
            _raise_platform_error(exc)
        except ResidentNotFoundError as exc:
            raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=str(exc))
        except ResidentLogsUnavailableError as exc:
            raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=str(exc))
        except ResidentLogSourceError as exc:
            raise HTTPException(status_code=http_status.HTTP_502_BAD_GATEWAY, detail=str(exc))
        except ValueError as exc:
            # An unknown min_level or a container name that is not one.
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
            )

    @app.post("/api/v1/ravn/ravens/{ravn_id}/suspend")
    async def suspend_raven_endpoint(
        ravn_id: str,
        request: Request,
        _: Principal = Depends(extract_principal),
    ) -> dict:
        return await control_raven_endpoint(ravn_id, "suspend", request)

    @app.post("/api/v1/ravn/ravens/{ravn_id}/resume")
    async def resume_raven_endpoint(
        ravn_id: str,
        request: Request,
        _: Principal = Depends(extract_principal),
    ) -> dict:
        return await control_raven_endpoint(ravn_id, "resume", request)

    @app.delete(
        "/api/v1/ravn/ravens/{ravn_id}",
        status_code=http_status.HTTP_204_NO_CONTENT,
    )
    async def delete_raven_endpoint(
        ravn_id: str,
        request: Request,
        _: Principal = Depends(extract_principal),
    ) -> Response:
        auth_headers, auth_params = forward_auth(request)
        try:
            await resident_directory.delete_raven(ravn_id, auth_headers, auth_params)
        except httpx.HTTPStatusError as exc:
            _raise_platform_error(exc)
        return Response(status_code=http_status.HTTP_204_NO_CONTENT)

    @app.get("/api/v1/ravn/ravens/{ravn_id}/sessions")
    async def list_resident_sessions_endpoint(
        ravn_id: str,
        request: Request,
        principal: Principal = Depends(extract_principal),
    ) -> list[dict]:
        auth_headers, auth_params = forward_auth(request)
        try:
            return await resident_directory.list_resident_sessions(
                ravn_id, principal, auth_headers, auth_params
            )
        except httpx.HTTPStatusError as exc:
            _raise_platform_error(exc)

    @app.post(
        "/api/v1/ravn/ravens/{ravn_id}/sessions",
        status_code=http_status.HTTP_201_CREATED,
    )
    async def create_resident_session_endpoint(
        ravn_id: str,
        body: dict,
        request: Request,
        principal: Principal = Depends(extract_principal),
    ) -> dict:
        auth_headers, auth_params = forward_auth(request)
        try:
            return await resident_directory.create_resident_session(
                ravn_id, body, principal, auth_headers, auth_params
            )
        except httpx.HTTPStatusError as exc:
            _raise_platform_error(exc)
        except ResidentSessionsUnavailableError as exc:
            raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=str(exc))

    @app.delete(
        "/api/v1/ravn/ravens/{ravn_id}/sessions/{session_id}",
        status_code=http_status.HTTP_204_NO_CONTENT,
    )
    async def delete_resident_session_endpoint(
        ravn_id: str,
        session_id: str,
        request: Request,
        principal: Principal = Depends(extract_principal),
    ) -> Response:
        auth_headers, auth_params = forward_auth(request)
        try:
            await resident_directory.delete_resident_session(
                ravn_id, session_id, principal, auth_headers, auth_params
            )
        except httpx.HTTPStatusError as exc:
            _raise_platform_error(exc)
        except ResidentSessionsUnavailableError as exc:
            raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=str(exc))
        return Response(status_code=http_status.HTTP_204_NO_CONTENT)

    @app.get("/api/v1/ravn/deployment-profiles")
    async def list_resident_profiles_endpoint(
        request: Request,
        _: Principal = Depends(extract_principal),
    ) -> list[dict]:
        """List resident profiles enabled on this target."""
        auth_headers, auth_params = forward_auth(request)
        return await resident_directory.list_profiles(auth_headers, auth_params)

    @app.get("/api/v1/ravn/ravens/{ravn_id}")
    async def get_raven_endpoint(
        ravn_id: str,
        request: Request,
        principal: Principal = Depends(extract_principal),
    ) -> dict:
        """Return one caller-visible resident ravn by its id."""
        auth_headers, auth_params = forward_auth(request)
        ravn = await resident_directory.get_raven(
            ravn_id,
            principal,
            auth_headers,
            auth_params,
        )
        if ravn is None:
            raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Ravn not found")
        return ravn

    @app.get("/api/v1/ravn/sessions/{session_id}")
    async def get_session_endpoint(
        session_id: str,
        request: Request,
        ravn_id: str | None = None,
        principal: Principal = Depends(extract_principal),
    ) -> dict:
        """Return one live ravn session."""
        auth_headers, auth_params = forward_auth(request)
        session_data = await resident_directory.get_session(
            session_id,
            principal,
            auth_headers,
            auth_params,
            ravn_id=ravn_id,
        )
        if session_data is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="Session not found",
            )
        return session_data

    @app.get("/api/v1/ravn/sessions/{session_id}/messages")
    async def list_session_messages(
        session_id: str,
        request: Request,
        ravn_id: str | None = None,
        principal: Principal = Depends(extract_principal),
    ) -> list[dict]:
        """Return transcript messages for one ravn session.

        The live transcript is delivered over the session's Skuld chat
        WebSocket (the shared chat replays history on connect), so this REST
        endpoint returns an empty list for a live session rather than seed
        data — the UI opens the chat for history.
        """
        auth_headers, auth_params = forward_auth(request)
        session_data = await resident_directory.get_session(
            session_id,
            principal,
            auth_headers,
            auth_params,
            ravn_id=ravn_id,
        )
        if session_data is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="Session not found",
            )
        return []

    @app.post("/api/v1/ravn/sessions/{session_id}/stop")
    async def stop_session(session_id: str, request: Request) -> dict:
        """Stop a Forge-backed Ravn session through its owning service."""
        auth_headers, auth_params = forward_auth(request)
        try:
            stopped = await resident_directory.stop_session(
                session_id,
                auth_headers,
                auth_params,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500:
                detail = "Forge session lifecycle is unavailable"
            else:
                try:
                    detail = exc.response.json().get("detail", "Forge rejected session stop")
                except ValueError:
                    detail = "Forge rejected session stop"
            raise HTTPException(
                status_code=exc.response.status_code,
                detail=detail,
            ) from exc
        except Exception as exc:
            logger.warning("stop session: Forge lifecycle unavailable", exc_info=True)
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Forge session lifecycle is unavailable",
            ) from exc
        if stopped is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="Forge-backed Ravn session not found",
            )
        return stopped

    def _trigger_to_payload(record: TriggerRecord) -> dict:
        return {
            "id": record.id,
            "kind": record.kind,
            "persona_name": record.persona_name,
            "spec": record.spec,
            "repo": record.repo,
            "enabled": record.enabled,
            "created_at": record.created_at.isoformat(),
        }

    def _budget_to_payload(totals: BudgetTotals) -> dict:
        return {
            "spent_usd": totals.spent_usd,
            "cap_usd": totals.cap_usd,
            "warn_at": totals.warn_at,
        }

    def _require_trigger_store() -> TriggerStorePort:
        """Trigger routes are opt-in (see ``ravn.config.TriggerStoreConfig``)
        — unconfigured is a real, valid deployment state (every chart with
        no new values set), not a bug, so this is a clean 503 with the
        remedy rather than an AttributeError on ``None``."""
        if trigger_store is None:
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Ravn trigger persistence is unavailable",
            )
        return trigger_store

    def _require_budget_ledger() -> BudgetLedgerPort:
        """Budget routes are opt-in (see ``ravn.config.BudgetLedgerConfig``)
        — same reasoning as ``_require_trigger_store``."""
        if budget_ledger is None:
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Ravn budget persistence is unavailable",
            )
        return budget_ledger

    @app.get("/api/v1/ravn/triggers")
    async def triggers(
        principal: Principal = Depends(extract_principal),
    ) -> list[dict]:
        """List durably-registered triggers for the caller's tenant."""
        records = await _require_trigger_store().list_triggers(tenant_id=principal.tenant_id)
        return [_trigger_to_payload(record) for record in records]

    @app.post("/api/v1/ravn/triggers", status_code=http_status.HTTP_201_CREATED)
    async def create_trigger_endpoint(
        request: Request,
        body: TriggerCreateRequest,
        principal: Principal = Depends(extract_principal),
    ) -> dict:
        """Register one durable trigger, scoped to the caller's tenant.

        Rejects (422) a kind/spec the resident could never schedule or fire —
        a trigger that looks accepted but never runs is worse than an error.
        Also rejects (403) attaching a trigger to a persona the caller cannot
        administer, and (422) an event trigger's repo the caller's tenant was
        never granted — see ``ravn.config.TriggerRepoAllowlistConfig``.
        """
        store = _require_trigger_store()
        try:
            validate_trigger_spec(body.kind, body.spec, repo=body.repo)
        except TriggerValidationError as exc:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc
        if body.kind == "event":
            try:
                check_repo_allowlisted(
                    tenant_id=principal.tenant_id,
                    repo=body.repo,
                    grants=loaded_settings.trigger_repo_allowlist.grants,
                )
            except TriggerValidationError as exc:
                raise HTTPException(
                    status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=str(exc),
                ) from exc
        auth_headers, auth_params = forward_auth(request)
        ravens = await resident_directory.list_ravens(principal, auth_headers, auth_params)
        administered_personas = {
            str(raven.get("persona_name") or "") for raven in ravens if raven.get("persona_name")
        }
        if body.persona_name not in administered_personas:
            raise HTTPException(
                status_code=http_status.HTTP_403_FORBIDDEN,
                detail=(
                    f"persona {body.persona_name!r} is not one of your administered "
                    "residents — a trigger can only be attached to a persona you can see "
                    "via GET /api/v1/ravn/ravens"
                ),
            )
        record = await store.create_trigger(
            kind=body.kind,
            persona_name=body.persona_name,
            spec=body.spec,
            enabled=body.enabled,
            owner_id=principal.user_id,
            tenant_id=principal.tenant_id,
            repo=body.repo,
        )
        return {**_trigger_to_payload(record), "execution_enabled": _trigger_execution_enabled}

    @app.delete("/api/v1/ravn/triggers/{trigger_id}", status_code=http_status.HTTP_204_NO_CONTENT)
    async def delete_trigger_endpoint(
        trigger_id: str,
        principal: Principal = Depends(extract_principal),
    ) -> Response:
        """Delete one durable trigger, if the caller's tenant owns it.

        A malformed id (not a UUID — the only shape ``create_trigger`` ever
        mints) is a 404, never a store-level cast error.
        """
        store = _require_trigger_store()
        try:
            UUID(trigger_id)
        except ValueError:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="Trigger not found",
            ) from None
        existing = await store.get_trigger(trigger_id)
        if existing is None or existing.tenant_id != principal.tenant_id:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="Trigger not found",
            )
        is_owner = existing.owner_id == principal.user_id
        is_admin = bool(_TRIGGER_ADMIN_ROLES.intersection(principal.roles))
        if not is_owner and not is_admin:
            raise HTTPException(
                status_code=http_status.HTTP_403_FORBIDDEN,
                detail="Only the trigger's owner or a tenant admin may delete it",
            )
        await store.delete_trigger(trigger_id)
        return Response(status_code=http_status.HTTP_204_NO_CONTENT)

    @app.get("/api/v1/ravn/budget/fleet")
    async def fleet_budget(
        principal: Principal = Depends(extract_principal),
    ) -> dict:
        """Return today's fleet-wide budget ledger totals for the caller's tenant."""
        totals = await _require_budget_ledger().fleet_totals(
            day=datetime.now(UTC).date(),
            tenant_id=principal.tenant_id,
        )
        return _budget_to_payload(totals)

    @app.get("/api/v1/ravn/budget/me")
    async def budget_me(
        principal: Principal = Depends(extract_principal),
    ) -> dict:
        """A resident's own today's totals — identity derived, no id in the URL.

        Called by ``ResidentBudgetPort``'s platform adapter to seed the
        in-memory tracker at startup. ``ravn_id`` is ``principal.user_id``:
        real per-resident identity only when the workload-identity mapping
        this caller matched sets ``owner_id_claim`` (see
        ``niuu.domain.services.workload_identity``); a caller matched by a
        mapping without it shares an id with every other caller of that
        mapping, same as any other route keyed by ``principal.user_id``.

        ``owner_scoped`` surfaces that fact to the caller (rather than
        leaving it undiscoverable) so ``PlatformBudgetReporter.seed_today``
        can refuse to run under a possibly-shared identity instead of
        silently reporting numbers that belong to every resident the
        matched mapping admits.
        """
        totals = await _require_budget_ledger().totals_for(
            principal.user_id,
            tenant_id=principal.tenant_id,
            day=datetime.now(UTC).date(),
        )
        payload = _budget_to_payload(totals)
        payload["owner_scoped"] = workload_owner_scoped(current_bearer_token() or "")
        return payload

    @app.post("/api/v1/ravn/budget/spend")
    async def budget_spend(
        body: BudgetSpendRequest,
        principal: Principal = Depends(extract_principal),
    ) -> dict:
        """Record one resident's real spend; ravn_id/tenant_id are never client-supplied.

        Restricted to owner-scoped workload callers (see
        ``niuu.domain.services.token_scope.workload_owner_scoped``) — without
        this, any authenticated caller (a human session, an ordinary PAT, or
        a workload token that only proves membership in a shared mapping)
        could write spend rows under any ``ravn_id``/``tenant_id`` its own
        principal happens to carry, inflating that tenant's fleet totals.
        Only a resident's own ``PlatformBudgetReporter`` — the one caller
        this route exists for — ever presents an owner-scoped token.
        """
        ledger = _require_budget_ledger()
        if not workload_owner_scoped(current_bearer_token() or ""):
            raise HTTPException(
                status_code=http_status.HTTP_403_FORBIDDEN,
                detail=(
                    "budget/spend requires an owner-scoped workload token — see "
                    "niuu.domain.services.workload_identity's owner_id_claim mapping"
                ),
            )
        await ledger.record_spend(
            principal.user_id,
            tenant_id=principal.tenant_id,
            day=datetime.now(UTC).date(),
            cost_usd=body.cost_usd,
            cap_usd=body.cap_usd,
            warn_at=body.warn_at,
        )
        totals = await ledger.totals_for(
            principal.user_id,
            tenant_id=principal.tenant_id,
            day=datetime.now(UTC).date(),
        )
        return _budget_to_payload(totals)

    @app.get("/api/v1/ravn/budget/{ravn_id}")
    async def budget(
        ravn_id: str,
        principal: Principal = Depends(extract_principal),
    ) -> dict:
        """Return today's budget ledger totals for one ravn in the caller's tenant."""
        totals = await _require_budget_ledger().totals_for(
            ravn_id,
            tenant_id=principal.tenant_id,
            day=datetime.now(UTC).date(),
        )
        return _budget_to_payload(totals)

    if persona_loader is not None:
        from ravn.api.personas import create_personas_router

        app.include_router(create_personas_router(persona_loader))

    import contextlib  # noqa: PLC0415

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
        # Connect now, not on the first real request — a bad DSN must fail
        # this deployment's startup loudly, not surface as an opaque 500 on
        # someone's first trigger poll or budget report.
        for store in (trigger_store, budget_ledger):
            warmup = getattr(store, "warmup", None)
            if warmup is not None:
                await warmup()
        try:
            yield
        finally:
            await resident_directory.aclose()
            await aclose_stores()

    app.router.lifespan_context = lifespan

    return app
