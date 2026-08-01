"""Observatory FastAPI app and route handlers."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from niuu.adapters.inbound.auth import extract_principal
from niuu.domain.agent_directory import (
    AgentDirectoryEntry,
    AgentDirectoryFilters,
    AgentDirectoryPage,
)
from niuu.domain.models import Principal
from niuu.service_databases import apply_service_database_settings, database_pool
from niuu.settings_schema import (
    SettingsFieldSchema,
    SettingsProviderSchema,
    SettingsSectionSchema,
)
from observatory.a2a_cards import HttpAgentCardResolver
from observatory.agent_directory import AgentDirectoryService
from observatory.discovery import ObservatoryDiscoveryService
from observatory.entity_discovery import build_discovery_adapter
from observatory.registry import (
    InMemoryObservatoryRegistryRepository,
    ObservatoryRegistryRepository,
    PostgresObservatoryRegistryRepository,
    RegistryNotFoundError,
    RegistryValidationError,
)
from volundr.config import Settings

KEEPALIVE_INTERVAL = 15.0
FORWARDED_AUTH_HEADERS = (
    "authorization",
    "x-auth-user-id",
    "x-auth-email",
    "x-auth-tenant",
    "x-auth-roles",
)


def _to_sse(payload: object, *, event: str | None = None) -> str:
    """Serialize a payload as one SSE frame."""
    lines: list[str] = []
    if event:
        lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(payload, ensure_ascii=False)}")
    return "\n".join(lines) + "\n\n"


def _repository(request: Request) -> ObservatoryRegistryRepository:
    return request.app.state.registry_repository


def _discovery(request: Request) -> ObservatoryDiscoveryService:
    return request.app.state.discovery_service


def _agent_directory(request: Request) -> AgentDirectoryService:
    return request.app.state.agent_directory_service


def _forward_headers(request: Request) -> dict[str, str]:
    headers: dict[str, str] = {}
    for name in FORWARDED_AUTH_HEADERS:
        value = request.headers.get(name)
        if value:
            headers[name] = value
    return headers


async def _topology_stream(
    discovery: ObservatoryDiscoveryService,
    headers: dict[str, str] | None = None,
) -> AsyncGenerator[str, None]:
    """Yield topology snapshots whenever the local view changes.

    Deduped on `revision`, which only changes when the graph does. The previous
    check compared `timestamp`, which is re-stamped on every materialization, so
    it never matched and the full snapshot went out on every tick regardless of
    whether anything had changed. `timestamp` remains the fallback for a
    producer that supplies no revision.
    """
    last_marker: str | None = None
    while True:
        snapshot = await discovery.get_topology_snapshot(headers=headers)
        marker = str(snapshot.get("revision") or snapshot.get("timestamp") or "")
        if marker != last_marker:
            last_marker = marker
            yield _to_sse(snapshot, event="topology.snapshot")
        else:
            yield ": keepalive\n\n"
        await asyncio.sleep(KEEPALIVE_INTERVAL)


async def _events_stream(
    discovery: ObservatoryDiscoveryService,
    headers: dict[str, str] | None = None,
) -> AsyncGenerator[str, None]:
    """Replay current events, then emit fresh ones whenever they change."""
    seen_ids: set[str] = set()
    while True:
        emitted = False
        for item in await discovery.get_events(headers=headers):
            event_id = str(item.get("id") or "")
            if event_id in seen_ids:
                continue
            seen_ids.add(event_id)
            emitted = True
            yield _to_sse(item, event="observatory.event")
        if not emitted:
            yield ": keepalive\n\n"
        await asyncio.sleep(KEEPALIVE_INTERVAL)


def create_router() -> APIRouter:
    """Create the Observatory API router."""
    router = APIRouter(prefix="/api/v1/observatory", tags=["Observatory"])

    @router.get("/health", summary="Observatory health")
    async def health(request: Request) -> dict[str, object]:
        return {
            "status": "healthy",
            "guildUrl": request.app.state.guild_url,
        }

    @router.get(
        "/agents",
        response_model=AgentDirectoryPage,
        summary="List principal-visible addressable A2A agents",
    )
    async def list_agents(
        request: Request,
        skill: list[str] | None = Query(default=None),
        tag: list[str] | None = Query(default=None),
        kind: list[str] | None = Query(default=None),
        observed_status: list[str] | None = Query(default=None, alias="status"),
        environment_id: list[str] | None = Query(default=None, alias="environmentId"),
        cluster: list[str] | None = Query(default=None),
        instance: list[str] | None = Query(default=None),
        principal: Principal = Depends(extract_principal),
    ) -> AgentDirectoryPage:
        return await _agent_directory(request).list_agents(
            principal,
            headers=_forward_headers(request),
            filters=AgentDirectoryFilters.from_values(
                skills=skill,
                tags=tag,
                kinds=kind,
                statuses=observed_status,
                environment_ids=environment_id,
                cluster_ids=cluster,
                instance_ids=instance,
            ),
        )

    @router.get(
        "/agents/{agent_id}",
        response_model=AgentDirectoryEntry,
        summary="Get one principal-visible addressable A2A agent",
    )
    async def get_agent(
        request: Request,
        agent_id: str,
        principal: Principal = Depends(extract_principal),
    ) -> AgentDirectoryEntry:
        entry = await _agent_directory(request).get_agent(
            agent_id,
            principal,
            headers=_forward_headers(request),
        )
        if entry is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
        return entry

    @router.get("/registry", summary="Get the observatory type registry")
    async def registry(request: Request) -> dict[str, object]:
        return await _repository(request).get_registry()

    @router.put("/registry", summary="Replace the observatory type registry")
    async def save_registry(request: Request, body: dict[str, Any]) -> dict[str, object]:
        try:
            return await _repository(request).save_registry(body)
        except RegistryValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            )

    @router.post("/registry/types", summary="Create one registry type")
    async def create_type(request: Request, body: dict[str, Any]) -> dict[str, object]:
        try:
            return await _repository(request).create_type(body)
        except RegistryValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            )

    @router.patch("/registry/types/{type_id}", summary="Update one registry type")
    async def update_type(
        request: Request, type_id: str, body: dict[str, Any]
    ) -> dict[str, object]:
        try:
            return await _repository(request).update_type(type_id, body)
        except RegistryNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
        except RegistryValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            )

    @router.delete("/registry/types/{type_id}", summary="Delete one registry type")
    async def delete_type(request: Request, type_id: str) -> dict[str, object]:
        try:
            return await _repository(request).delete_type(type_id)
        except RegistryNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    @router.get(
        "/settings",
        response_model=SettingsProviderSchema,
        summary="Get observatory settings schema",
    )
    async def settings(request: Request) -> SettingsProviderSchema:
        registry_payload = await _repository(request).get_registry()
        topology = await _discovery(request).get_topology_snapshot()
        return SettingsProviderSchema(
            title="Observatory",
            subtitle="registry and guild discovery",
            scope="service",
            sections=[
                SettingsSectionSchema(
                    id="streams",
                    label="Streams",
                    description=(
                        "Live topology and event stream characteristics for the "
                        "mounted observability surface."
                    ),
                    fields=[
                        SettingsFieldSchema(
                            key="keepalive_interval_seconds",
                            label="Keepalive Interval (seconds)",
                            type="number",
                            value=KEEPALIVE_INTERVAL,
                            description="How often idle SSE clients receive a keepalive frame.",
                            read_only=True,
                        ),
                        SettingsFieldSchema(
                            key="registry_type_count",
                            label="Registered Type Count",
                            type="number",
                            value=len(registry_payload.get("types", [])),
                            description="Number of types persisted in the observatory registry.",
                            read_only=True,
                        ),
                        SettingsFieldSchema(
                            key="topology_node_count",
                            label="Topology Node Count",
                            type="number",
                            value=len(topology.get("nodes", [])),
                            description="Current number of nodes in the discovered topology.",
                            read_only=True,
                        ),
                        SettingsFieldSchema(
                            key="guild_url",
                            label="Guild URL",
                            type="text",
                            value=request.app.state.guild_url,
                            description="Guild endpoint used for Observatory discovery.",
                            read_only=True,
                        ),
                    ],
                )
            ],
        )

    @router.get("/topology", summary="Stream live topology snapshots")
    @router.get("/topology/stream", summary="Stream live topology snapshots")
    async def topology(request: Request) -> StreamingResponse:
        return StreamingResponse(
            _topology_stream(_discovery(request), headers=_forward_headers(request)),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @router.get("/topology/snapshot", summary="Get one live topology snapshot")
    async def topology_snapshot(request: Request) -> dict[str, Any]:
        snapshot = await _discovery(request).get_topology_snapshot(
            headers=_forward_headers(request)
        )
        events = await _discovery(request).get_events(headers=_forward_headers(request))
        return {**snapshot, "events": events}

    @router.get("/events", summary="Stream observatory events")
    @router.get("/events/stream", summary="Stream observatory events")
    async def events(request: Request) -> StreamingResponse:
        return StreamingResponse(
            _events_stream(_discovery(request), headers=_forward_headers(request)),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return router


def create_app(
    settings: Settings | None = None,
    *,
    registry_repository: ObservatoryRegistryRepository | None = None,
    discovery_service: ObservatoryDiscoveryService | None = None,
    agent_directory_service: AgentDirectoryService | None = None,
) -> FastAPI:
    """Create the Observatory ASGI app."""
    loaded_settings = apply_service_database_settings(settings or Settings(), "observatory")
    app: FastAPI | None = None

    async def registry_type_ids() -> list[str]:
        """Entity types the registry currently knows about.

        Resolved per call rather than captured at construction: the repository
        is attached during lifespan, and operators can edit the registry through
        the API at any time without a restart.
        """
        repository = getattr(app.state, "registry_repository", None) if app else None
        if repository is None:
            raise RuntimeError("Registry repository is not ready")
        registry = await repository.get_registry()
        return [str(entry.get("id", "")) for entry in registry.get("types", [])]

    discovery = discovery_service
    if discovery is None:
        guild_cfg = loaded_settings.observatory.guild
        discovery = ObservatoryDiscoveryService(
            guild_url=guild_cfg.url,
            discovery_adapter=build_discovery_adapter(loaded_settings.observatory.discovery),
            registry_type_ids=registry_type_ids,
        )
    directory = agent_directory_service
    if directory is None:
        directory_cfg = loaded_settings.observatory.directory
        directory = AgentDirectoryService(
            discovery=discovery,
            card_resolver=HttpAgentCardResolver(
                timeout_seconds=directory_cfg.card_timeout_seconds,
                default_cache_ttl_seconds=directory_cfg.card_cache_ttl_seconds,
                signature_algorithms=directory_cfg.signature_algorithms,
                authenticated_card_origins=directory_cfg.authenticated_card_origins,
            ),
            instance_id=directory_cfg.instance_id,
            cluster_id=directory_cfg.cluster_id,
            max_concurrency=directory_cfg.local_max_concurrency,
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        if registry_repository is not None:
            app.state.registry_repository = registry_repository
            await app.state.registry_repository.ensure_seeded()
            app.state.discovery_service = discovery
            app.state.agent_directory_service = directory
            yield
            return

        async with database_pool(loaded_settings.database) as pool:
            repo = PostgresObservatoryRegistryRepository(pool)
            await repo.ensure_seeded()
            app.state.registry_repository = repo
            app.state.discovery_service = discovery
            app.state.agent_directory_service = directory
            yield

    app = FastAPI(title="Observatory API", lifespan=lifespan)
    app.state.settings = loaded_settings
    app.state.registry_repository = registry_repository or InMemoryObservatoryRegistryRepository()
    app.state.discovery_service = discovery
    app.state.agent_directory_service = directory
    app.state.guild_url = getattr(discovery, "guild_url", getattr(discovery, "base_url", ""))

    @app.get("/health", tags=["Health"])
    async def health() -> dict[str, object]:
        return {
            "status": "healthy",
            "guildUrl": app.state.guild_url,
        }

    app.include_router(create_router())
    return app
