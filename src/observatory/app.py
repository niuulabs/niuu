"""Observatory FastAPI app and route handlers."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter, FastAPI
from fastapi.responses import StreamingResponse

from niuu.settings_schema import (
    SettingsFieldSchema,
    SettingsProviderSchema,
    SettingsSectionSchema,
)
from observatory.data import get_events, get_registry, get_topology_snapshot

KEEPALIVE_INTERVAL = 15.0


def _to_sse(payload: object, *, event: str | None = None) -> str:
    """Serialize a payload as one SSE frame."""
    lines: list[str] = []
    if event:
        lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(payload, ensure_ascii=False)}")
    return "\n".join(lines) + "\n\n"


async def _topology_stream() -> AsyncGenerator[str, None]:
    """Yield an initial topology snapshot followed by keepalive comments."""
    yield _to_sse(get_topology_snapshot(), event="topology.snapshot")
    while True:
        await asyncio.sleep(KEEPALIVE_INTERVAL)
        yield ": keepalive\n\n"


async def _events_stream() -> AsyncGenerator[str, None]:
    """Replay recent events once per subscriber, then keep the stream open."""
    for item in get_events():
        yield _to_sse(item, event="observatory.event")
    while True:
        await asyncio.sleep(KEEPALIVE_INTERVAL)
        yield ": keepalive\n\n"


def create_router() -> APIRouter:
    """Create the Observatory API router."""
    router = APIRouter(prefix="/api/v1/observatory", tags=["Observatory"])

    @router.get("/registry", summary="Get the observatory type registry")
    async def registry() -> dict[str, object]:
        return get_registry()

    @router.get(
        "/settings",
        response_model=SettingsProviderSchema,
        summary="Get observatory settings schema",
    )
    async def settings() -> SettingsProviderSchema:
        registry_payload = get_registry()
        topology = get_topology_snapshot()
        return SettingsProviderSchema(
            title="Observatory",
            subtitle="topology and event settings",
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
                            description=(
                                "Number of types currently published in the observatory registry."
                            ),
                            read_only=True,
                        ),
                        SettingsFieldSchema(
                            key="topology_node_count",
                            label="Topology Node Count",
                            type="number",
                            value=len(topology.get("nodes", [])),
                            description="Current number of nodes in the topology snapshot.",
                            read_only=True,
                        ),
                        SettingsFieldSchema(
                            key="seed_event_count",
                            label="Seed Event Count",
                            type="number",
                            value=len(get_events()),
                            description=(
                                "Number of events replayed to a fresh "
                                "observatory subscriber before keepalives."
                            ),
                            read_only=True,
                        ),
                    ],
                )
            ],
        )

    @router.get("/topology", summary="Stream live topology snapshots")
    @router.get("/topology/stream", summary="Stream live topology snapshots")
    async def topology() -> StreamingResponse:
        return StreamingResponse(
            _topology_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @router.get("/events", summary="Stream observatory events")
    @router.get("/events/stream", summary="Stream observatory events")
    async def events() -> StreamingResponse:
        return StreamingResponse(
            _events_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return router


def create_app() -> FastAPI:
    """Create the Observatory ASGI app."""
    app = FastAPI(title="Observatory API")
    app.include_router(create_router())
    return app
