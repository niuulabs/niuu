"""Ravn FastAPI sub-application.

Mounted by RavnPlugin into the niuu platform server under /api/v1/ravn/.
Exposes session management and persona management endpoints consumed by the
CLI and the web UI.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from starlette import status as http_status

from niuu.adapters.inbound.auth import extract_principal
from niuu.domain.models import Principal
from niuu.settings_schema import (
    SettingsFieldSchema,
    SettingsProviderSchema,
    SettingsSectionSchema,
)
from ravn.adapters.platform_runtime import HttpPlatformRuntimeAdapter
from ravn.api.residents import ResidentDirectory, forward_auth
from ravn.api.valkyries import (
    ValkyrieDashboardProjection,
    build_nats_review_command_publisher_from_env,
    build_nats_telemetry_subscription_from_env,
    create_valkyrie_router,
)
from ravn.api.warden_stream import WardenStreamBroker
from ravn.config import Settings
from ravn.ports.platform_runtime import PlatformRuntimePort
from ravn.ports.resident_discovery import ResidentDiscoveryPort
from ravn.ports.warden_deployer import WardenDeploymentError
from ravn.ports.warden_discovery import WardenDiscoveryPort
from ravn.resident_discovery import build_resident_discovery
from ravn.warden import (
    WardenConsoleConfig,
    WardenFeatures,
    WardenScheduleConfig,
    WardenSpec,
    WardenStore,
    build_warden_store,
    resolve_deployment_adapter,
)
from ravn.warden.discovery import build_warden_discovery
from ravn.warden.observability import synthetic_warden_dream_logs

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from ravn.ports.persona import PersonaRegistryPort


logger = logging.getLogger(__name__)

_CAPABILITY_UNAVAILABLE = "This Ravn host has no configured persistence adapter for this capability"


class TriggerCreateRequest(BaseModel):
    kind: str
    persona_name: str
    spec: str
    enabled: bool = True


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


class WardenCreateRequest(BaseModel):
    name: str
    persona: str = "mimir-warden"
    profile: str = ""
    model: str = "claude-sonnet-4-6"
    deployment: str = "launchd"
    deployment_kwargs: dict[str, object] = Field(default_factory=dict)
    features: WardenFeatures | None = None
    mount_names: list[str] = Field(default_factory=list)
    write_mount: str = ""
    read_mount_names: list[str] = Field(default_factory=list)
    write_mount_names: list[str] = Field(default_factory=list)
    category_scope: list[str] = Field(default_factory=list)
    schedules: WardenScheduleConfig | None = None
    console: WardenConsoleConfig | None = None
    autostart: bool = False
    created_by: str = "api"


class WardenLogEntry(BaseModel):
    id: str
    source: str
    line_number: int
    raw: str
    timestamp: str | None = None
    level: str | None = None
    logger: str | None = None
    message: str


_LOG_LINE_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) "
    r"(?P<logger>[\w.]+) (?P<level>[A-Z]+) (?P<message>.*)$"
)


def create_app(
    persona_loader: PersonaRegistryPort | None = None,
    warden_store: WardenStore | None = None,
    settings: Settings | None = None,
    warden_discovery: WardenDiscoveryPort | None = None,
    resident_discovery: ResidentDiscoveryPort | None = None,
    platform_runtime: PlatformRuntimePort | None = None,
) -> FastAPI:
    """Create and return the Ravn FastAPI sub-application.

    Args:
        persona_loader: Optional persona registry. When provided, the persona
            CRUD routes are mounted at /api/v1/ravn/personas. When omitted
            (e.g. in tests that only need session endpoints) persona routes
            are not included.
        warden_store: Optional persisted warden store. Uses the default
            filesystem-backed location when omitted.
    """
    app = FastAPI(title="Ravn API", docs_url=None, redoc_url=None)
    loaded_settings = settings or Settings()
    # gateway.platform.base_url is the canonical "where is the platform API"
    # setting (config file, or RAVN_GATEWAY__PLATFORM__BASE_URL env override).
    if resident_discovery is None:
        standalone_discovery = build_resident_discovery(loaded_settings.resident_discovery)
    else:
        standalone_discovery = resident_discovery
    platform = platform_runtime or HttpPlatformRuntimeAdapter(
        base_url=loaded_settings.gateway.platform.base_url,
        timeout_seconds=loaded_settings.gateway.platform.timeout,
    )
    resident_directory = ResidentDirectory(platform=platform, discovery=standalone_discovery)
    store = warden_store or build_warden_store()
    if warden_discovery is None:
        discovery = build_warden_discovery(loaded_settings.warden_discovery, store=store)
    else:
        discovery = warden_discovery
    stream_broker = WardenStreamBroker()

    async def publish_warden_update(event: str, warden: WardenSpec) -> None:
        await stream_broker.publish(event, warden)

    def encode_sse(event: str, payload: dict[str, object]) -> str:
        return f"event: {event}\ndata: {json.dumps(payload)}\n\n"

    def parse_log_entry(source: str, line_number: int, raw: str) -> WardenLogEntry:
        text = raw.rstrip("\n")
        match = _LOG_LINE_RE.match(text)
        if match:
            return WardenLogEntry(
                id=f"{source}:{line_number}",
                source=source,
                line_number=line_number,
                raw=text,
                timestamp=match.group("timestamp"),
                level=match.group("level"),
                logger=match.group("logger"),
                message=match.group("message"),
            )
        return WardenLogEntry(
            id=f"{source}:{line_number}",
            source=source,
            line_number=line_number,
            raw=text,
            message=text,
        )

    def read_log_entries(path: Path, *, source: str, limit: int) -> list[WardenLogEntry]:
        if not path.exists():
            return []
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
        start = max(0, len(lines) - limit)
        return [
            parse_log_entry(source, start + offset + 1, line)
            for offset, line in enumerate(lines[start:])
        ]

    def read_confined_log_entries(
        warden_dir: Path,
        configured_path: str,
        *,
        default_name: str,
        source: str,
        limit: int,
    ) -> list[WardenLogEntry]:
        root = warden_dir.expanduser().resolve()
        candidate = Path(configured_path).expanduser() if configured_path else root / default_name
        if not candidate.is_absolute():
            candidate = root / candidate
        candidate = candidate.resolve()
        if not candidate.is_relative_to(root):
            return []
        return read_log_entries(candidate, source=source, limit=limit)

    def merge_log_entries(*groups: list[WardenLogEntry], limit: int) -> list[WardenLogEntry]:
        merged = [entry for group in groups for entry in group]
        merged.sort(key=lambda entry: (_entry_sort_timestamp(entry.timestamp), entry.id))
        return merged[-limit:]

    async def list_known_wardens() -> list[WardenSpec]:
        return await discovery.list_wardens()

    async def get_known_warden(warden_id: str) -> WardenSpec | None:
        stored = store.get(warden_id)
        if stored is not None:
            return stored
        for warden in await list_known_wardens():
            if warden.id == warden_id:
                return warden
        return None

    async def raise_discovery_managed_if_present(warden_id: str) -> None:
        discovered = await get_known_warden(warden_id)
        if discovered is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="Warden not found",
            )
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="Warden is discovery-managed; change it through its source of truth",
        )

    def _entry_sort_timestamp(raw: str | None) -> datetime:
        if not raw:
            return datetime.min.replace(tzinfo=UTC)
        for candidate in (raw, raw.replace("Z", "+00:00")):
            try:
                parsed = datetime.fromisoformat(candidate)
                return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
            except ValueError:
                continue
        try:
            parsed = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S,%f")
            return parsed.replace(tzinfo=UTC)
        except ValueError:
            return datetime.min.replace(tzinfo=UTC)

    @app.get("/api/v1/ravn/status")
    async def status_endpoint(
        request: Request,
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
            sessions = await resident_directory.list_sessions(
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
        return {
            "service": "ravn",
            "session_count": len(sessions),
            "fleet_member_count": len(ravens),
            "healthy": True,
        }

    @app.get("/api/v1/ravn/settings", response_model=SettingsProviderSchema)
    async def settings_endpoint(
        request: Request,
        principal: Principal = Depends(extract_principal),
    ) -> SettingsProviderSchema:
        auth_headers, auth_params = forward_auth(request)
        try:
            ravens = await resident_directory.list_ravens(
                principal,
                auth_headers,
                auth_params,
            )
            sessions = await resident_directory.list_sessions(
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
                            value=False,
                            description=_CAPABILITY_UNAVAILABLE,
                            read_only=True,
                        ),
                        SettingsFieldSchema(
                            key="budget_store_available",
                            label="Budget Store",
                            type="boolean",
                            value=False,
                            description=_CAPABILITY_UNAVAILABLE,
                            read_only=True,
                        ),
                    ],
                )
            ],
        )

    @app.get("/api/v1/ravn/sessions")
    async def list_sessions_endpoint(
        request: Request,
        principal: Principal = Depends(extract_principal),
    ) -> list[dict]:
        """List the caller's live ravn sessions (flock rooms + residents)."""
        auth_headers, auth_params = forward_auth(request)
        return await resident_directory.list_sessions(principal, auth_headers, auth_params)

    @app.get("/api/v1/ravn/ravens")
    async def list_ravens_endpoint(
        request: Request,
        principal: Principal = Depends(extract_principal),
    ) -> list[dict]:
        """List durable residents and visible compatibility deployments."""
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
        _: Principal = Depends(extract_principal),
    ) -> dict:
        auth_headers, auth_params = forward_auth(request)
        try:
            return await resident_directory.get_raven_logs(
                ravn_id,
                lines=lines,
                sources=tuple(source),
                min_level=min_level,
                auth_headers=auth_headers,
                auth_params=auth_params,
            )
        except httpx.HTTPStatusError as exc:
            _raise_platform_error(exc)

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
        _: Principal = Depends(extract_principal),
    ) -> list[dict]:
        auth_headers, auth_params = forward_auth(request)
        try:
            return await resident_directory.list_resident_sessions(
                ravn_id, auth_headers, auth_params
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
        _: Principal = Depends(extract_principal),
    ) -> dict:
        auth_headers, auth_params = forward_auth(request)
        try:
            return await resident_directory.create_resident_session(
                ravn_id, body, auth_headers, auth_params
            )
        except httpx.HTTPStatusError as exc:
            _raise_platform_error(exc)

    @app.delete(
        "/api/v1/ravn/ravens/{ravn_id}/sessions/{session_id}",
        status_code=http_status.HTTP_204_NO_CONTENT,
    )
    async def delete_resident_session_endpoint(
        ravn_id: str,
        session_id: str,
        request: Request,
        _: Principal = Depends(extract_principal),
    ) -> Response:
        auth_headers, auth_params = forward_auth(request)
        try:
            await resident_directory.delete_resident_session(
                ravn_id, session_id, auth_headers, auth_params
            )
        except httpx.HTTPStatusError as exc:
            _raise_platform_error(exc)
        return Response(status_code=http_status.HTTP_204_NO_CONTENT)

    @app.get("/api/v1/ravn/deployment-profiles")
    async def list_resident_profiles_endpoint(
        request: Request,
        _: Principal = Depends(extract_principal),
    ) -> list[dict]:
        """List resident profiles enabled on this target."""
        auth_headers, auth_params = forward_auth(request)
        return await resident_directory.list_profiles(auth_headers, auth_params)

    @app.get("/api/v1/ravn/wardens", response_model=list[WardenSpec])
    async def list_wardens_endpoint() -> list[WardenSpec]:
        """List known wardens from configured discovery adapters."""
        return await list_known_wardens()

    @app.post("/api/v1/ravn/wardens", response_model=WardenSpec, status_code=201)
    async def create_warden_endpoint(body: WardenCreateRequest) -> WardenSpec:
        """Create and persist a new warden."""
        spec = WardenSpec(
            id="",
            name=body.name,
            persona=body.persona,
            profile=body.profile,
            model=body.model,
            deployment=body.deployment,
            deployment_adapter=resolve_deployment_adapter(body.deployment),
            deployment_kwargs=body.deployment_kwargs,
            features=body.features or WardenFeatures(),
            mimir={
                "mount_names": body.mount_names,
                "write_mount": body.write_mount,
                "read_mount_names": body.read_mount_names,
                "write_mount_names": body.write_mount_names,
                "category_scope": body.category_scope,
            },
            schedules=body.schedules or WardenScheduleConfig(),
            console=body.console or WardenConsoleConfig(),
            autostart=body.autostart,
            created_by=body.created_by,
        )
        created = store.create(spec)
        await publish_warden_update("warden.created", created)
        return created

    @app.get("/api/v1/ravn/wardens/{warden_id}", response_model=WardenSpec)
    async def get_warden_endpoint(warden_id: str) -> WardenSpec:
        """Return one known warden."""
        warden = await get_known_warden(warden_id)
        if warden is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="Warden not found",
            )
        return warden

    @app.get("/api/v1/ravn/wardens/{warden_id}/logs", response_model=list[WardenLogEntry])
    async def get_warden_logs_endpoint(
        warden_id: str,
        stream: str = "stdout",
        limit: int = 200,
    ) -> list[WardenLogEntry]:
        """Return recent warden log lines for one known warden."""
        warden = await get_known_warden(warden_id)
        if warden is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="Warden not found",
            )

        normalized_stream = stream.strip().lower()
        limit = max(1, min(limit, 1000))
        warden_dir = store.warden_dir(warden_id)
        stdout_entries = read_confined_log_entries(
            warden_dir,
            warden.supervisor.stdout_log,
            default_name="warden.log",
            source="stdout",
            limit=limit,
        )
        stderr_entries = read_confined_log_entries(
            warden_dir,
            warden.supervisor.stderr_log,
            default_name="warden.error.log",
            source="stderr",
            limit=limit,
        )
        dream_entries = [
            WardenLogEntry.model_validate(entry)
            for entry in synthetic_warden_dream_logs(warden, limit=limit)
        ]
        if normalized_stream == "stderr":
            return stderr_entries
        if normalized_stream == "stdout":
            return merge_log_entries(stdout_entries, dream_entries, limit=limit)
        if normalized_stream == "all":
            return merge_log_entries(stdout_entries, stderr_entries, dream_entries, limit=limit)
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="stream must be 'stdout', 'stderr', or 'all'",
        )

    @app.get("/api/v1/ravn/wardens/{warden_id}/activity", response_model=list[WardenLogEntry])
    async def get_warden_activity_endpoint(
        warden_id: str, limit: int = 200
    ) -> list[WardenLogEntry]:
        """Return recent parsed activity entries from both warden log streams."""
        warden = await get_known_warden(warden_id)
        if warden is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="Warden not found",
            )
        limit = max(1, min(limit, 1000))
        warden_dir = store.warden_dir(warden_id)
        stdout_entries = read_confined_log_entries(
            warden_dir,
            warden.supervisor.stdout_log,
            default_name="warden.log",
            source="stdout",
            limit=limit,
        )
        stderr_entries = read_confined_log_entries(
            warden_dir,
            warden.supervisor.stderr_log,
            default_name="warden.error.log",
            source="stderr",
            limit=limit,
        )
        merged = [*stdout_entries, *stderr_entries]
        merged.sort(key=lambda entry: (entry.timestamp or "", entry.id))
        return merged[-limit:]

    @app.get("/api/v1/ravn/wardens/{warden_id}/stream")
    async def stream_warden_endpoint(warden_id: str, request: Request) -> StreamingResponse:
        """Stream SSE updates for one known warden."""
        if await get_known_warden(warden_id) is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="Warden not found",
            )

        async def event_generator():
            queue = stream_broker.subscribe(warden_id)
            try:
                yield ": connected\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=25.0)
                    except TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    yield encode_sse(
                        event.event,
                        event.warden.model_dump(mode="json"),
                    )
            finally:
                stream_broker.unsubscribe(warden_id, queue)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/v1/ravn/wardens/{warden_id}/observe", response_model=WardenSpec)
    async def observe_warden_endpoint(warden_id: str) -> WardenSpec:
        """Refresh live backend status for one persisted warden."""
        try:
            observed = store.observe(warden_id)
        except WardenDeploymentError as exc:
            raise HTTPException(
                status_code=http_status.HTTP_502_BAD_GATEWAY,
                detail=str(exc),
            ) from exc
        if observed is None:
            observed = await get_known_warden(warden_id)
        if observed is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="Warden not found",
            )
        await publish_warden_update("warden.observed", observed)
        return observed

    @app.post("/api/v1/ravn/wardens/{warden_id}/install", response_model=WardenSpec)
    async def install_warden_endpoint(warden_id: str) -> WardenSpec:
        """Generate local service artifacts and mark a warden as installed."""
        try:
            warden = store.install(warden_id, workspace_root=Path.cwd())
        except WardenDeploymentError as exc:
            raise HTTPException(
                status_code=http_status.HTTP_502_BAD_GATEWAY,
                detail=str(exc),
            ) from exc
        if warden is None:
            await raise_discovery_managed_if_present(warden_id)
        await publish_warden_update("warden.installed", warden)
        return warden

    @app.post("/api/v1/ravn/wardens/{warden_id}/start", response_model=WardenSpec)
    async def start_warden_endpoint(warden_id: str) -> WardenSpec:
        """Mark an installed warden as started."""
        existing = store.get(warden_id)
        if existing is None:
            await raise_discovery_managed_if_present(warden_id)
        if not existing.supervisor.installed:
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail="Warden must be installed before it can be started",
            )
        try:
            started = store.start(warden_id)
        except WardenDeploymentError as exc:
            raise HTTPException(
                status_code=http_status.HTTP_502_BAD_GATEWAY,
                detail=str(exc),
            ) from exc
        if started is None:
            await raise_discovery_managed_if_present(warden_id)
        await publish_warden_update("warden.started", started)
        return started

    @app.post("/api/v1/ravn/wardens/{warden_id}/stop", response_model=WardenSpec)
    async def stop_warden_endpoint(warden_id: str) -> WardenSpec:
        """Stop an installed warden."""
        existing = store.get(warden_id)
        if existing is None:
            await raise_discovery_managed_if_present(warden_id)
        if not existing.supervisor.installed:
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail="Warden must be installed before it can be stopped",
            )
        try:
            stopped = store.stop(warden_id)
        except WardenDeploymentError as exc:
            raise HTTPException(
                status_code=http_status.HTTP_502_BAD_GATEWAY,
                detail=str(exc),
            ) from exc
        if stopped is None:
            await raise_discovery_managed_if_present(warden_id)
        await publish_warden_update("warden.stopped", stopped)
        return stopped

    @app.post("/api/v1/ravn/wardens/{warden_id}/uninstall", response_model=WardenSpec)
    async def uninstall_warden_endpoint(warden_id: str) -> WardenSpec:
        """Uninstall a warden deployment while keeping its persisted spec."""
        try:
            uninstalled = store.uninstall(warden_id)
        except WardenDeploymentError as exc:
            raise HTTPException(
                status_code=http_status.HTTP_502_BAD_GATEWAY,
                detail=str(exc),
            ) from exc
        if uninstalled is None:
            await raise_discovery_managed_if_present(warden_id)
        await publish_warden_update("warden.uninstalled", uninstalled)
        return uninstalled

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

    def raise_capability_unavailable(capability: str) -> None:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Ravn {capability} persistence is unavailable",
        )

    @app.get("/api/v1/ravn/triggers")
    async def triggers() -> list[dict]:
        """Report that no durable trigger store is configured."""
        raise_capability_unavailable("trigger")

    @app.post("/api/v1/ravn/triggers", status_code=http_status.HTTP_201_CREATED)
    async def create_trigger_endpoint(body: TriggerCreateRequest) -> dict:
        """Report that no durable trigger store is configured."""
        del body
        raise_capability_unavailable("trigger")

    @app.delete("/api/v1/ravn/triggers/{trigger_id}", status_code=http_status.HTTP_204_NO_CONTENT)
    async def delete_trigger_endpoint(trigger_id: str) -> Response:
        """Report that no durable trigger store is configured."""
        del trigger_id
        raise_capability_unavailable("trigger")

    @app.get("/api/v1/ravn/budget/fleet")
    async def fleet_budget() -> dict:
        """Report that no durable budget store is configured."""
        raise_capability_unavailable("budget")

    @app.get("/api/v1/ravn/budget/{ravn_id}")
    async def budget(ravn_id: str) -> dict:
        """Report that no durable budget store is configured."""
        del ravn_id
        raise_capability_unavailable("budget")

    if persona_loader is not None:
        from ravn.api.personas import create_personas_router

        app.include_router(create_personas_router(persona_loader))

    import contextlib  # noqa: PLC0415

    from ravn.adapters.valkyrie_history import (  # noqa: PLC0415
        build_valkyrie_history_store_from_env,
    )
    from ravn.api.odin_reviews import (  # noqa: PLC0415
        build_review_queue_store,
        create_odin_review_router,
    )
    from ravn.api.valkyrie_history_service import ValkyrieHistoryService  # noqa: PLC0415
    from ravn.api.valkyrie_metrics import create_valkyrie_metrics_router  # noqa: PLC0415
    from ravn.api.valkyrie_skills import (  # noqa: PLC0415
        ValkyrieSkillMirror,
        create_valkyrie_skills_router,
    )
    from ravn.api.valkyries import build_skuld_room_client_from_env  # noqa: PLC0415
    from ravn.odin.review_service import OdinReviewService  # noqa: PLC0415

    valkyrie_projection = ValkyrieDashboardProjection()
    valkyrie_learning_commands = build_nats_review_command_publisher_from_env(
        loaded_settings.valkyrie.command,
        loaded_settings.valkyrie.telemetry,
    )
    review_config = loaded_settings.valkyrie.odin_reviews
    odin_review_service = OdinReviewService(
        build_review_queue_store(review_config),
        publisher=valkyrie_learning_commands,
        ttl_seconds_by_kind=review_config.ttl_seconds_by_kind(),
        default_ttl_seconds=review_config.default_ttl_seconds,
    )
    valkyrie_history = ValkyrieHistoryService(
        build_valkyrie_history_store_from_env(),
        review_service=odin_review_service,
    )
    valkyrie_skills = ValkyrieSkillMirror()
    valkyrie_telemetry = build_nats_telemetry_subscription_from_env(
        valkyrie_projection,
        review_ingest=odin_review_service.ingest_event,
        history_ingest=valkyrie_history.ingest_event,
        skills_ingest=valkyrie_skills.ingest_event,
        config=loaded_settings.valkyrie.telemetry,
    )
    review_sweep_interval = review_config.sweep_interval_seconds
    brief_interval = loaded_settings.valkyrie.brief_interval_seconds

    async def _run_review_sweep() -> None:
        while True:
            await asyncio.sleep(review_sweep_interval)
            try:
                await odin_review_service.sweep_expired()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("odin review: expiry sweep failed")

    async def _run_valkyrie_brief() -> None:
        from datetime import timedelta  # noqa: PLC0415

        while True:
            await asyncio.sleep(brief_interval)
            try:
                filed = await valkyrie_history.file_morning_briefs(
                    window=timedelta(seconds=brief_interval),
                )
                if filed:
                    logger.info("valkyrie brief: filed %d environment brief(s)", filed)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("valkyrie brief: filing failed")

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
        review_sweep_task = asyncio.create_task(
            _run_review_sweep(),
            name="odin_review_sweep",
        )
        valkyrie_brief_task = (
            asyncio.create_task(_run_valkyrie_brief(), name="valkyrie_brief")
            if brief_interval > 0
            else None
        )
        if valkyrie_telemetry is not None:
            try:
                await valkyrie_telemetry.start()
            except Exception as exc:  # noqa: BLE001
                logger.warning("valkyrie telemetry subscription did not start: %s", exc)
        try:
            await valkyrie_learning_commands.start()
        except Exception as exc:  # noqa: BLE001
            logger.warning("valkyrie learning command publisher did not start: %s", exc)
        try:
            yield
        finally:
            review_sweep_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                _ = await review_sweep_task
            if valkyrie_brief_task is not None:
                valkyrie_brief_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    _ = await valkyrie_brief_task
            if valkyrie_telemetry is not None:
                await valkyrie_telemetry.stop()
            await valkyrie_learning_commands.stop()
            await resident_directory.aclose()

    app.router.lifespan_context = lifespan

    app.include_router(
        create_valkyrie_router(
            valkyrie_projection,
            review_command_publisher=valkyrie_learning_commands,
            room_client=build_skuld_room_client_from_env(loaded_settings.valkyrie.room),
            review_service=odin_review_service,
            history_service=valkyrie_history,
        )
    )
    app.include_router(create_valkyrie_skills_router(valkyrie_skills))
    app.include_router(create_valkyrie_metrics_router(valkyrie_skills, valkyrie_history))
    app.include_router(
        create_odin_review_router(
            odin_review_service,
            room_client=build_skuld_room_client_from_env(loaded_settings.valkyrie.room),
        )
    )

    return app
