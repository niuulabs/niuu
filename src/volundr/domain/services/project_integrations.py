"""Team integrations: connections a Forge project owns, used by everyone on it.

A team connection acts as the team's bot or service account, never as a person:
its ``owner_id`` is ``project:<id>`` and its credential is stored under the
``project`` credential owner, so no per-person lookup ever finds it, and a
missing team connection is never replaced by someone's personal one.

Project owners connect, route and remove; every member sees the connections and
what they were used for. Each use is recorded (:class:`IntegrationUsage`).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4, uuid5

from niuu.domain.models import (
    PROJECT_CREDENTIAL_OWNER,
    IntegrationConnection,
    IntegrationType,
    IntegrationUsage,
    project_owner_id,
)
from niuu.ports.credentials import CredentialStorePort
from niuu.ports.integrations import IntegrationRepository, IntegrationUsageLog
from volundr.domain.models import (
    IntegrationDefinition,
    Principal,
    SecretType,
    TeamInstallMethod,
)
from volundr.domain.notification_ports import NotificationRuleRepository
from volundr.domain.notifications import (
    INTEGRATION_SINK,
    TEAM_CHANNEL_KEY,
    TEAM_DELIVER_TO_KEY,
    TEAM_EVENT_KEY,
    TEAM_PROJECT_KEY,
    NotificationRule,
    NotificationRuleMatch,
    TeamDeliverTo,
    TeamRouteEvent,
    team_route,
)
from volundr.domain.projects import ForgeProject, ProjectRole
from volundr.domain.services.client_credentials import ClientCredentialsMinter
from volundr.domain.services.integration_registry import IntegrationRegistry

if TYPE_CHECKING:
    from volundr.domain.services.projects import ProjectService

#: Namespace of a team route's rule id: one rule per (project, event).
_TEAM_ROUTE_NAMESPACE = UUID("8c1f2a4e-6b3d-4e5f-9a7c-2d1e0f3b4a5c")


class TeamIntegrationError(ValueError):
    """A team integration request cannot be carried out; the message names the fix."""


class TeamIntegrationNotFoundError(LookupError):
    """The connection or route is not one of this project's."""


def team_route_rule_id(project_id: UUID | str, event: TeamRouteEvent) -> UUID:
    return uuid5(_TEAM_ROUTE_NAMESPACE, f"{project_id}:{event.value}")


class ProjectIntegrationService:
    """Connect, route and audit the integrations a project's team shares."""

    def __init__(
        self,
        projects: ProjectService,
        *,
        integrations: IntegrationRepository,
        credentials: CredentialStorePort,
        registry: IntegrationRegistry,
        rules: NotificationRuleRepository,
        usage: IntegrationUsageLog,
        minter: ClientCredentialsMinter,
        usage_page_size: int,
    ) -> None:
        if usage_page_size < 1:
            raise ValueError("projects.team_integrations.usage_page_size must be at least 1")
        self._projects = projects
        self._integrations = integrations
        self._credentials = credentials
        self._registry = registry
        self._rules = rules
        self._usage = usage
        self._minter = minter
        self._usage_page_size = usage_page_size

    # --- reading (every member) ----------------------------------------

    def catalog(self) -> list[IntegrationDefinition]:
        """The catalog entries a project owner may install for the team."""
        return [d for d in self._registry.list_definitions() if d.team_install is not None]

    async def list(
        self, project_id: UUID, principal: Principal | None
    ) -> list[IntegrationConnection]:
        project = await self._projects.get(project_id, principal)
        return await self._integrations.list_connections(project_owner_id(project.id))

    async def routes(self, project_id: UUID, principal: Principal | None) -> list[NotificationRule]:
        project = await self._projects.get(project_id, principal)
        owner = project_owner_id(project.id)
        return [r for r in await self._rules.list_for_owner(owner) if team_route(r, owner)]

    async def usage(
        self, project_id: UUID, principal: Principal | None, *, before: datetime | None = None
    ) -> tuple[list[IntegrationUsage], datetime | None]:
        """One page of uses, newest first, and the cursor of the next page (or ``None``)."""
        project = await self._projects.get(project_id, principal)
        page = await self._usage.list_for_project(
            str(project.id), limit=self._usage_page_size + 1, before=before
        )
        if len(page) <= self._usage_page_size:
            return page, None
        page = page[: self._usage_page_size]
        return page, page[-1].occurred_at

    async def connection(self, project_id: UUID | str, connection_id: str) -> IntegrationConnection:
        """The project's connection ``connection_id``; raises when it is not the project's."""
        connection = await self._integrations.get_connection(connection_id)
        if connection is None or connection.project_id != str(project_id):
            raise TeamIntegrationNotFoundError("That integration is not one of this project's")
        return connection

    # --- managing (owners) ---------------------------------------------

    async def owner_project(self, project_id: UUID, principal: Principal | None) -> ForgeProject:
        return await self._projects.get(project_id, principal, ProjectRole.OWNER)

    def definition(self, slug: str) -> IntegrationDefinition:
        definition = self._registry.get_definition(slug)
        if definition is None or definition.team_install is None:
            raise TeamIntegrationError(f"{slug!r} cannot be installed for a project team")
        return definition

    async def connect_service_account(
        self,
        project_id: UUID,
        principal: Principal | None,
        *,
        slug: str,
        credential: dict[str, str],
        config: dict[str, Any],
    ) -> IntegrationConnection:
        """Connect a provider service account (``client_credentials``) for the team.

        The first token is minted here, so a wrong client id or secret fails now, not
        on the first ticket an agent touches.
        """
        project = await self.owner_project(project_id, principal)
        definition = self.definition(slug)
        spec = definition.team_install
        assert spec is not None
        if spec.method is not TeamInstallMethod.CLIENT_CREDENTIALS:
            raise TeamIntegrationError(
                f"{definition.name} is installed for a team through its app; "
                "start the install from the project's Team integrations"
            )
        self._require_config(definition, config)
        values = await self._minter.mint(spec, dict(credential))
        return await self.save(
            project,
            definition,
            connected_by=principal.user_id if principal else "",
            credential=values,
            config=config,
            secret_type=SecretType.GENERIC,
            metadata={"source": "team_install", "method": spec.method.value},
        )

    async def save(
        self,
        project: ForgeProject,
        definition: IntegrationDefinition,
        *,
        connected_by: str,
        credential: dict[str, str],
        config: dict[str, Any],
        secret_type: SecretType,
        metadata: dict[str, str],
    ) -> IntegrationConnection:
        """Store a team connection of ``definition`` and its credential under the project.

        A project has at most one connection per integration: installing again replaces
        the credential of the existing one.
        """
        self._require_config(definition, config)
        owner = project_owner_id(project.id)
        existing = next(
            (
                c
                for c in await self._integrations.list_connections(owner)
                if c.slug == definition.slug
            ),
            None,
        )
        credential_name = (
            existing.credential_name if existing else f"team-{definition.slug}-{uuid4().hex[:8]}"
        )
        await self._credentials.store(
            owner_type=PROJECT_CREDENTIAL_OWNER,
            owner_id=str(project.id),
            name=credential_name,
            secret_type=secret_type,
            data=credential,
            metadata={**metadata, "integration": definition.slug, "connected_by": connected_by},
        )
        now = datetime.now(UTC)
        connection = IntegrationConnection(
            id=existing.id if existing else str(uuid4()),
            owner_id=owner,
            integration_type=IntegrationType(definition.integration_type),
            adapter=definition.adapter,
            credential_name=credential_name,
            config={**(existing.config if existing else {}), **config},
            enabled=True,
            created_at=existing.created_at if existing else now,
            updated_at=now,
            slug=definition.slug,
            project_id=str(project.id),
            connected_by=connected_by,
        )
        saved = await self._integrations.save_connection(connection)
        await self._usage.record(
            IntegrationUsage(
                connection_id=saved.id,
                project_id=str(project.id),
                action="connect" if existing is None else "reconnect",
                acting_for=connected_by,
                actor="forge",
                subject=definition.name,
            )
        )
        return saved

    async def update(
        self,
        project_id: UUID,
        principal: Principal | None,
        connection_id: str,
        *,
        config: dict[str, Any] | None = None,
        enabled: bool | None = None,
    ) -> IntegrationConnection:
        project = await self.owner_project(project_id, principal)
        existing = await self.connection(project.id, connection_id)
        merged = {**existing.config, **(config or {})}
        definition = self.definition(existing.slug)
        self._require_config(definition, merged)
        updated = replace(
            existing,
            config=merged,
            enabled=existing.enabled if enabled is None else enabled,
            updated_at=datetime.now(UTC),
        )
        return await self._integrations.save_connection(updated)

    async def remove(
        self, project_id: UUID, principal: Principal | None, connection_id: str
    ) -> None:
        """Remove a team connection, its credential and every team route through it."""
        project = await self.owner_project(project_id, principal)
        existing = await self.connection(project.id, connection_id)
        owner = project_owner_id(project.id)
        for rule in await self._rules.list_for_owner(owner):
            if rule.integration_connection_id == existing.id:
                await self._rules.delete(owner, rule.id)
        await self._integrations.delete_connection(existing.id)
        await self._credentials.delete(
            PROJECT_CREDENTIAL_OWNER, str(project.id), existing.credential_name
        )
        await self._usage.record(
            IntegrationUsage(
                connection_id=existing.id,
                project_id=str(project.id),
                action="disconnect",
                acting_for=principal.user_id if principal else "",
                actor="forge",
                subject=existing.slug,
            )
        )

    async def set_route(
        self,
        project_id: UUID,
        principal: Principal | None,
        *,
        event: TeamRouteEvent,
        connection_id: str,
        deliver_to: TeamDeliverTo,
        channel: str = "",
        enabled: bool = True,
    ) -> NotificationRule:
        """Send ``event`` through the team's messaging connection, to a channel or each person."""
        project = await self.owner_project(project_id, principal)
        connection = await self.connection(project.id, connection_id)
        if connection.integration_type != IntegrationType.MESSAGING:
            raise TeamIntegrationError("Team routes go through a messaging integration")
        channel = channel.strip()
        if (
            deliver_to is TeamDeliverTo.CHANNEL
            and not channel
            and not str(connection.config.get(TEAM_CHANNEL_KEY) or "").strip()
        ):
            raise TeamIntegrationError(
                "Name the channel, or set the team channel on the connection first"
            )
        owner = project_owner_id(project.id)
        rule_id = team_route_rule_id(project.id, event)
        now = datetime.now(UTC)
        existing = await self._rules.get(owner, rule_id)
        rule = NotificationRule(
            id=rule_id,
            owner_id=owner,
            name=f"Team: {event.value.replace('_', ' ')}",
            enabled=enabled,
            match=NotificationRuleMatch(project_ids=[str(project.id)]),
            sink=INTEGRATION_SINK.name,
            integration_connection_id=connection.id,
            config={
                TEAM_PROJECT_KEY: str(project.id),
                TEAM_EVENT_KEY: event.value,
                TEAM_DELIVER_TO_KEY: deliver_to.value,
                TEAM_CHANNEL_KEY: channel,
            },
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        if existing is None:
            return await self._rules.create(rule)
        updated = await self._rules.update(rule)
        if updated is None:
            raise TeamIntegrationNotFoundError("That team route was removed meanwhile")
        return updated

    async def remove_route(
        self, project_id: UUID, principal: Principal | None, event: TeamRouteEvent
    ) -> None:
        project = await self.owner_project(project_id, principal)
        await self._rules.delete(
            project_owner_id(project.id), team_route_rule_id(project.id, event)
        )

    # --- use -----------------------------------------------------------

    async def record_use(self, usage: IntegrationUsage) -> None:
        """Record one use of a team connection. Raises when it cannot be recorded."""
        await self._usage.record(usage)

    @staticmethod
    def _require_config(definition: IntegrationDefinition, config: dict[str, Any]) -> None:
        spec = definition.team_install
        missing = [k for k in (spec.required_config if spec else ()) if not config.get(k)]
        if missing:
            raise TeamIntegrationError(
                f"A team {definition.name} connection needs {', '.join(missing)}"
            )
