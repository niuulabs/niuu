"""Ravn FastAPI sub-application.

Mounted by RavnPlugin into the niuu platform server under /api/v1/ravn/.
Exposes session management and persona management endpoints consumed by the
CLI and the web UI.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette import status as http_status

from niuu.settings_schema import (
    SettingsFieldSchema,
    SettingsProviderSchema,
    SettingsSectionSchema,
)
from ravn.api.runtime_data import (
    create_trigger as create_runtime_trigger,
)
from ravn.api.runtime_data import (
    delete_trigger as delete_runtime_trigger,
)
from ravn.api.runtime_data import (
    get_budget as get_runtime_budget,
)
from ravn.api.runtime_data import (
    get_fleet_budget as get_runtime_fleet_budget,
)
from ravn.api.runtime_data import (
    get_raven as get_runtime_raven,
)
from ravn.api.runtime_data import (
    get_session as get_runtime_session,
)
from ravn.api.runtime_data import (
    list_messages as list_runtime_messages,
)
from ravn.api.runtime_data import (
    list_ravens as list_runtime_ravens,
)
from ravn.api.runtime_data import (
    list_sessions as list_runtime_sessions,
)
from ravn.api.runtime_data import (
    list_triggers as list_runtime_triggers,
)
from ravn.api.warden_stream import WardenStreamBroker
from ravn.ports.warden_deployer import WardenDeploymentError
from ravn.warden import (
    WardenFeatures,
    WardenSpec,
    WardenStore,
    build_warden_store,
    resolve_deployment_adapter,
)

if TYPE_CHECKING:
    from ravn.ports.persona import PersonaRegistryPort


class TriggerCreateRequest(BaseModel):
    kind: str
    persona_name: str
    spec: str
    enabled: bool = True


class WardenCreateRequest(BaseModel):
    name: str
    persona: str = "research-and-distill"
    profile: str = ""
    deployment: str = "launchd"
    deployment_kwargs: dict[str, object] = Field(default_factory=dict)
    features: WardenFeatures | None = None
    mount_names: list[str] = Field(default_factory=list)
    write_mount: str = ""
    category_scope: list[str] = Field(default_factory=list)
    autostart: bool = False
    created_by: str = "api"


def create_app(
    persona_loader: PersonaRegistryPort | None = None,
    warden_store: WardenStore | None = None,
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
    store = warden_store or build_warden_store()
    stream_broker = WardenStreamBroker()

    async def publish_warden_update(event: str, warden: WardenSpec) -> None:
        await stream_broker.publish(event, warden)

    def encode_sse(event: str, payload: dict[str, object]) -> str:
        return f"event: {event}\ndata: {json.dumps(payload)}\n\n"

    @app.get("/api/v1/ravn/status")
    async def status_endpoint() -> dict:
        """Return basic Ravn platform status."""
        return {"service": "ravn", "session_count": 0, "healthy": True}

    @app.get("/api/v1/ravn/settings", response_model=SettingsProviderSchema)
    async def settings_endpoint() -> SettingsProviderSchema:
        sessions = list_runtime_sessions()
        ravens = list_runtime_ravens()
        triggers = list_runtime_triggers()
        fleet_budget = get_runtime_fleet_budget()
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
                            key="trigger_count",
                            label="Trigger Count",
                            type="number",
                            value=len(triggers),
                            read_only=True,
                        ),
                        SettingsFieldSchema(
                            key="fleet_budget_usd",
                            label="Fleet Budget (USD)",
                            type="number",
                            value=float(fleet_budget.get("remaining_usd", 0.0)),
                            read_only=True,
                        ),
                    ],
                )
            ],
        )

    @app.get("/api/v1/ravn/sessions")
    async def list_sessions_endpoint() -> list:
        """List active agent sessions (stub — populated by gateway in production)."""
        return list_runtime_sessions()

    @app.get("/api/v1/ravn/ravens")
    async def list_ravens_endpoint() -> list[dict]:
        """List the currently known ravn runtime instances."""
        return list_runtime_ravens()

    @app.get("/api/v1/ravn/wardens", response_model=list[WardenSpec])
    async def list_wardens_endpoint() -> list[WardenSpec]:
        """List persisted wardens."""
        return store.list()

    @app.post("/api/v1/ravn/wardens", response_model=WardenSpec, status_code=201)
    async def create_warden_endpoint(body: WardenCreateRequest) -> WardenSpec:
        """Create and persist a new warden."""
        spec = WardenSpec(
            id="",
            name=body.name,
            persona=body.persona,
            profile=body.profile,
            deployment=body.deployment,
            deployment_adapter=resolve_deployment_adapter(body.deployment),
            deployment_kwargs=body.deployment_kwargs,
            features=body.features or WardenFeatures(),
            mimir={
                "mount_names": body.mount_names,
                "write_mount": body.write_mount,
                "category_scope": body.category_scope,
            },
            autostart=body.autostart,
            created_by=body.created_by,
        )
        created = store.create(spec)
        await publish_warden_update("warden.created", created)
        return created

    @app.get("/api/v1/ravn/wardens/{warden_id}", response_model=WardenSpec)
    async def get_warden_endpoint(warden_id: str) -> WardenSpec:
        """Return one persisted warden."""
        warden = store.get(warden_id)
        if warden is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="Warden not found",
            )
        return warden

    @app.get("/api/v1/ravn/wardens/{warden_id}/stream")
    async def stream_warden_endpoint(warden_id: str, request: Request) -> StreamingResponse:
        """Stream SSE updates for one persisted warden."""
        if store.get(warden_id) is None:
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
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="Warden not found",
            )
        await publish_warden_update("warden.installed", warden)
        return warden

    @app.post("/api/v1/ravn/wardens/{warden_id}/start", response_model=WardenSpec)
    async def start_warden_endpoint(warden_id: str) -> WardenSpec:
        """Mark an installed warden as started."""
        existing = store.get(warden_id)
        if existing is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="Warden not found",
            )
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
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="Warden not found",
            )
        await publish_warden_update("warden.started", started)
        return started

    @app.post("/api/v1/ravn/wardens/{warden_id}/stop", response_model=WardenSpec)
    async def stop_warden_endpoint(warden_id: str) -> WardenSpec:
        """Stop an installed warden."""
        existing = store.get(warden_id)
        if existing is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="Warden not found",
            )
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
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="Warden not found",
            )
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
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="Warden not found",
            )
        await publish_warden_update("warden.uninstalled", uninstalled)
        return uninstalled

    @app.get("/api/v1/ravn/ravens/{ravn_id}")
    async def get_raven_endpoint(ravn_id: str) -> dict:
        """Return one ravn runtime instance."""
        ravn = get_runtime_raven(ravn_id)
        if ravn is None:
            raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Ravn not found")
        return ravn

    @app.get("/api/v1/ravn/sessions/{session_id}")
    async def get_session_endpoint(session_id: str) -> dict:
        """Return one ravn session."""
        session_data = get_runtime_session(session_id)
        if session_data is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="Session not found",
            )
        return session_data

    @app.get("/api/v1/ravn/sessions/{session_id}/messages")
    async def list_session_messages(session_id: str) -> list[dict]:
        """Return transcript messages for one ravn session."""
        if get_runtime_session(session_id) is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="Session not found",
            )
        return list_runtime_messages(session_id)

    @app.post("/api/v1/ravn/sessions/{session_id}/stop")
    async def stop_session(session_id: str) -> dict:
        """Stop an active agent session."""
        return {"session_id": session_id, "status": "stopped"}

    @app.get("/api/v1/ravn/triggers")
    async def triggers() -> list[dict]:
        """List trigger definitions."""
        return list_runtime_triggers()

    @app.post("/api/v1/ravn/triggers", status_code=http_status.HTTP_201_CREATED)
    async def create_trigger_endpoint(body: TriggerCreateRequest) -> dict:
        """Create one trigger definition."""
        return create_runtime_trigger(
            kind=body.kind,
            persona_name=body.persona_name,
            spec=body.spec,
            enabled=body.enabled,
        )

    @app.delete("/api/v1/ravn/triggers/{trigger_id}", status_code=http_status.HTTP_204_NO_CONTENT)
    async def delete_trigger_endpoint(trigger_id: str) -> Response:
        """Delete a trigger definition."""
        if not delete_runtime_trigger(trigger_id):
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="Trigger not found",
            )
        return Response(status_code=http_status.HTTP_204_NO_CONTENT)

    @app.get("/api/v1/ravn/budget/fleet")
    async def fleet_budget() -> dict:
        """Return aggregate budget state for the fleet."""
        return get_runtime_fleet_budget()

    @app.get("/api/v1/ravn/budget/{ravn_id}")
    async def budget(ravn_id: str) -> dict:
        """Return budget state for one ravn."""
        budget_state = get_runtime_budget(ravn_id)
        if budget_state is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="Budget not found",
            )
        return budget_state

    if persona_loader is not None:
        from ravn.api.personas import create_personas_router

        app.include_router(create_personas_router(persona_loader))

    return app
