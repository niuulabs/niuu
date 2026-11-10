"""REST routes for a project's team integrations (``/projects/{id}/integrations``).

Every member reads; project owners connect, route and remove. A provider's app
install (Slack, Linear) starts at ``POST /api/v1/integrations/oauth/{slug}/authorize-team``;
a service account (Jira) connects here with its client credentials.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from niuu.domain.models import IntegrationConnection, IntegrationUsage
from volundr.adapters.inbound.rest_integrations import CatalogEntryResponse
from volundr.domain.notifications import (
    TEAM_CHANNEL_KEY,
    TEAM_DELIVER_TO_KEY,
    TEAM_EVENT_KEY,
    NotificationRule,
    TeamDeliverTo,
    TeamRouteEvent,
)
from volundr.domain.project_ports import ProjectAccessDeniedError
from volundr.domain.services.client_credentials import ClientCredentialsError
from volundr.domain.services.project_integrations import (
    ProjectIntegrationService,
    TeamIntegrationError,
    TeamIntegrationNotFoundError,
)
from volundr.domain.services.projects import ProjectNotFoundError


@contextmanager
def team_http_errors() -> Iterator[None]:
    """Map team-integration failures to HTTP: 404 hidden, 403 role, 422 fix, 502 provider."""
    try:
        yield
    except (ProjectNotFoundError, TeamIntegrationNotFoundError) as exc:
        raise HTTPException(404, str(exc)) from exc
    except ProjectAccessDeniedError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ClientCredentialsError as exc:
        raise HTTPException(502, str(exc)) from exc
    except TeamIntegrationError as exc:
        raise HTTPException(422, str(exc)) from exc


class TeamIntegrationResponse(BaseModel):
    id: str
    slug: str
    integration_type: str
    config: dict[str, Any] = Field(description="Non-secret settings, such as the team channel")
    enabled: bool
    connected_by: str = Field(description="The owner who installed it")
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, connection: IntegrationConnection) -> TeamIntegrationResponse:
        return cls(
            id=connection.id,
            slug=connection.slug,
            integration_type=str(connection.integration_type),
            config=dict(connection.config),
            enabled=connection.enabled,
            connected_by=connection.connected_by,
            created_at=connection.created_at,
            updated_at=connection.updated_at,
        )


class ServiceAccountConnect(BaseModel):
    """A provider service account's OAuth client credentials (``client_credentials``)."""

    slug: str = Field(min_length=1, max_length=100)
    credential: dict[str, str] = Field(description="client_id and client_secret")
    config: dict[str, Any] = Field(default_factory=dict)


class TeamIntegrationChange(BaseModel):
    config: dict[str, Any] | None = None
    enabled: bool | None = None


class TeamRouteResponse(BaseModel):
    event: TeamRouteEvent
    connection_id: str
    deliver_to: TeamDeliverTo
    channel: str = ""
    enabled: bool

    @classmethod
    def of(cls, rule: NotificationRule) -> TeamRouteResponse:
        return cls(
            event=TeamRouteEvent(rule.config[TEAM_EVENT_KEY]),
            connection_id=rule.integration_connection_id or "",
            deliver_to=TeamDeliverTo(rule.config[TEAM_DELIVER_TO_KEY]),
            channel=str(rule.config.get(TEAM_CHANNEL_KEY) or ""),
            enabled=rule.enabled,
        )


class TeamRouteChange(BaseModel):
    connection_id: str = Field(min_length=1)
    deliver_to: TeamDeliverTo
    channel: str = Field(default="", max_length=200)
    enabled: bool = True


class TeamUsageResponse(BaseModel):
    connection_id: str
    action: str
    acting_for: str
    actor: str
    subject: str
    session_id: str
    occurred_at: datetime | None

    @classmethod
    def of(cls, usage: IntegrationUsage) -> TeamUsageResponse:
        return cls(
            connection_id=usage.connection_id,
            action=usage.action,
            acting_for=usage.acting_for,
            actor=usage.actor,
            subject=usage.subject,
            session_id=usage.session_id,
            occurred_at=usage.occurred_at,
        )


class TeamUsagePage(BaseModel):
    items: list[TeamUsageResponse]
    next_before: datetime | None = Field(description="Pass as `before` for older uses")


def create_project_integrations_router(
    service: ProjectIntegrationService, principal_for_request
) -> APIRouter:
    router = APIRouter(prefix="/projects/{project_id}/integrations", tags=["Projects"])

    @router.get("/catalog")
    async def catalog(request: Request, project_id: UUID) -> list[CatalogEntryResponse]:
        """What a project owner can install for the team."""
        principal = await principal_for_request(request)
        with team_http_errors():
            await service.list(project_id, principal)
        return [CatalogEntryResponse.from_definition(d) for d in service.catalog()]

    @router.get("")
    async def list_integrations(
        request: Request, project_id: UUID
    ) -> list[TeamIntegrationResponse]:
        principal = await principal_for_request(request)
        with team_http_errors():
            return [
                TeamIntegrationResponse.of(c) for c in await service.list(project_id, principal)
            ]

    @router.post("", status_code=201)
    async def connect_service_account(
        request: Request, project_id: UUID, data: ServiceAccountConnect
    ) -> TeamIntegrationResponse:
        principal = await principal_for_request(request)
        with team_http_errors():
            connection = await service.connect_service_account(
                project_id,
                principal,
                slug=data.slug,
                credential=data.credential,
                config=data.config,
            )
        return TeamIntegrationResponse.of(connection)

    @router.patch("/{connection_id}")
    async def change_integration(
        request: Request, project_id: UUID, connection_id: str, data: TeamIntegrationChange
    ) -> TeamIntegrationResponse:
        principal = await principal_for_request(request)
        with team_http_errors():
            connection = await service.update(
                project_id, principal, connection_id, config=data.config, enabled=data.enabled
            )
        return TeamIntegrationResponse.of(connection)

    @router.delete("/{connection_id}", status_code=204)
    async def remove_integration(request: Request, project_id: UUID, connection_id: str) -> None:
        principal = await principal_for_request(request)
        with team_http_errors():
            await service.remove(project_id, principal, connection_id)

    @router.get("/routes")
    async def list_routes(request: Request, project_id: UUID) -> list[TeamRouteResponse]:
        principal = await principal_for_request(request)
        with team_http_errors():
            return [TeamRouteResponse.of(r) for r in await service.routes(project_id, principal)]

    @router.put("/routes/{event}")
    async def set_route(
        request: Request, project_id: UUID, event: TeamRouteEvent, data: TeamRouteChange
    ) -> TeamRouteResponse:
        principal = await principal_for_request(request)
        with team_http_errors():
            rule = await service.set_route(
                project_id,
                principal,
                event=event,
                connection_id=data.connection_id,
                deliver_to=data.deliver_to,
                channel=data.channel,
                enabled=data.enabled,
            )
        return TeamRouteResponse.of(rule)

    @router.delete("/routes/{event}", status_code=204)
    async def remove_route(request: Request, project_id: UUID, event: TeamRouteEvent) -> None:
        principal = await principal_for_request(request)
        with team_http_errors():
            await service.remove_route(project_id, principal, event)

    @router.get("/usage")
    async def usage(
        request: Request, project_id: UUID, before: datetime | None = Query(None)
    ) -> TeamUsagePage:
        principal = await principal_for_request(request)
        with team_http_errors():
            items, next_before = await service.usage(project_id, principal, before=before)
        return TeamUsagePage(
            items=[TeamUsageResponse.of(u) for u in items], next_before=next_before
        )

    return router
