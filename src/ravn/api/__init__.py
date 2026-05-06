"""Ravn FastAPI sub-application.

Mounted by RavnPlugin into the niuu platform server under /api/v1/ravn/.
Exposes session management and persona management endpoints consumed by the
CLI and the web UI.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel
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

if TYPE_CHECKING:
    from ravn.ports.persona import PersonaRegistryPort


class TriggerCreateRequest(BaseModel):
    kind: str
    persona_name: str
    spec: str
    enabled: bool = True


def create_app(persona_loader: PersonaRegistryPort | None = None) -> FastAPI:
    """Create and return the Ravn FastAPI sub-application.

    Args:
        persona_loader: Optional persona registry. When provided, the persona
            CRUD routes are mounted at /api/v1/ravn/personas. When omitted
            (e.g. in tests that only need session endpoints) persona routes
            are not included.
    """
    app = FastAPI(title="Ravn API", docs_url=None, redoc_url=None)

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
