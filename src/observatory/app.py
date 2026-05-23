"""Observatory FastAPI app and route handlers."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from niuu.ports.http_auth import HttpAuthPort
from niuu.service_databases import apply_service_database_settings, database_pool
from niuu.service_settings import Settings
from niuu.settings_schema import (
    SettingsFieldSchema,
    SettingsProviderSchema,
    SettingsSectionSchema,
)
from niuu.utils import import_class, resolve_secret_kwargs
from observatory.discovery import ObservatoryDiscoveryService
from observatory.registry import (
    InMemoryObservatoryRegistryRepository,
    ObservatoryRegistryRepository,
    PostgresObservatoryRegistryRepository,
    RegistryNotFoundError,
    RegistryValidationError,
)
KEEPALIVE_INTERVAL = 15.0


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


def _create_http_auth_adapter(config) -> HttpAuthPort:
    cls = import_class(config.adapter)
    kwargs = resolve_secret_kwargs(config.kwargs, config.secret_kwargs_env)
    return cls(**kwargs)


async def _topology_stream(discovery: ObservatoryDiscoveryService) -> AsyncGenerator[str, None]:
    """Yield topology snapshots whenever the local view changes."""
    last_timestamp: str | None = None
    while True:
        snapshot = await discovery.get_topology_snapshot()
        timestamp = str(snapshot.get("timestamp") or "")
        if timestamp != last_timestamp:
            last_timestamp = timestamp
            yield _to_sse(snapshot, event="topology.snapshot")
        else:
            yield ": keepalive\n\n"
        await asyncio.sleep(KEEPALIVE_INTERVAL)


async def _events_stream(discovery: ObservatoryDiscoveryService) -> AsyncGenerator[str, None]:
    """Replay current events, then emit fresh ones whenever they change."""
    seen_ids: set[str] = set()
    while True:
        emitted = False
        for item in await discovery.get_events():
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
                        SettingsFieldSchema(
                            key="guild_auth_adapter",
                            label="Guild Auth Adapter",
                            type="text",
                            value=request.app.state.guild_auth_adapter,
                            description="Dynamic auth adapter used for Guild requests.",
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
            _topology_stream(_discovery(request)),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @router.get("/events", summary="Stream observatory events")
    @router.get("/events/stream", summary="Stream observatory events")
    async def events(request: Request) -> StreamingResponse:
        return StreamingResponse(
            _events_stream(_discovery(request)),
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
) -> FastAPI:
    """Create the Observatory ASGI app."""
    loaded_settings = apply_service_database_settings(settings or Settings(), "observatory")
    discovery = discovery_service
    if discovery is None:
        guild_cfg = loaded_settings.observatory.guild
        discovery = ObservatoryDiscoveryService(
            guild_url=guild_cfg.url,
            auth=_create_http_auth_adapter(guild_cfg.auth),
            timeout_seconds=guild_cfg.timeout_seconds,
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        if registry_repository is not None:
            app.state.registry_repository = registry_repository
            await app.state.registry_repository.ensure_seeded()
            app.state.discovery_service = discovery
            yield
            return

        async with database_pool(loaded_settings.database) as pool:
            repo = PostgresObservatoryRegistryRepository(pool)
            await repo.ensure_seeded()
            app.state.registry_repository = repo
            app.state.discovery_service = discovery
            yield

    app = FastAPI(title="Observatory API", lifespan=lifespan)
    app.state.settings = loaded_settings
    app.state.registry_repository = registry_repository or InMemoryObservatoryRegistryRepository()
    app.state.discovery_service = discovery
    app.state.guild_url = getattr(discovery, "guild_url", getattr(discovery, "base_url", ""))
    app.state.guild_auth_adapter = loaded_settings.observatory.guild.auth.adapter

    @app.get("/health", tags=["Health"])
    async def health() -> dict[str, object]:
        return {
            "status": "healthy",
            "guildUrl": app.state.guild_url,
        }

    app.include_router(create_router())
    return app
