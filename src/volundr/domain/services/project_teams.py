"""One project's team on every Forge that has the project.

The project's home Forge checks each change to its team: members, team integrations and
team routes. It raises the team's version and publishes it to the Guild, which has every
other Forge copy it. On a Forge that only has a copy of the project, a change goes to the
home through the Guild and nothing is decided here.

A Forge without a Guild is every project's home and publishes nothing. One whose Guild
cannot be reached keeps working from what it has: a change at the home is kept and
published when the team is next read, a copy stays as it was last copied, and both say so.

A project created here under an id the Guild already holds for another Forge is created as
that Forge's copy. One that was registered here on its own before (Ting adds a run's project
to each Forge it runs on) becomes a copy when the Guild refuses its team for another Forge's,
unless it has members, team integrations or routes that the home lacks: those are named, and
nothing is changed until someone adds them at the home or removes them here.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

from niuu.cap_telemetry import record_degraded
from niuu.domain.models import (
    IntegrationConnection,
    IntegrationType,
    project_owner_id,
)
from niuu.domain.project_team import (
    ProjectTeam,
    ProjectTeamRecord,
    TeamConnection,
    TeamMember,
    TeamRouteSetting,
)
from niuu.ports.integrations import IntegrationRepository
from volundr.domain.notification_ports import NotificationRuleRepository
from volundr.domain.notifications import TeamDeliverTo, TeamRouteEvent, team_route
from volundr.domain.project_ports import (
    ProjectConflictError,
    ProjectMemberRepository,
    ProjectTeamSyncRepository,
    TeamGuild,
    TeamGuildError,
    TeamGuildReply,
    TeamNotVisibleError,
)
from volundr.domain.projects import ForgeProject, ProjectMember, ProjectRole, ProjectTeamSync
from volundr.domain.services.project_integrations import team_route_rule

if TYPE_CHECKING:
    from volundr.domain.services.projects import ProjectService

#: What ``refresh`` did, as the Guild reports it for each Forge.
HOME = "home"
UPDATED = "updated"
CURRENT = "current"
ABSENT = "absent"
CONNECTIONS = "connections"
UNRECORDED = "unrecorded"
HIDDEN = "hidden"
UNLINKED = "unlinked"


class ProjectTeamService:
    def __init__(
        self,
        projects: ProjectService,
        *,
        members: ProjectMemberRepository,
        integrations: IntegrationRepository,
        rules: NotificationRuleRepository,
        sync: ProjectTeamSyncRepository,
        guild: TeamGuild | None,
        instance_id: str,
        keeps_shared_connections: bool,
        refresh_after_seconds: float,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """``guild`` is None on a Forge without one. ``instance_id`` is this Forge's own in
        the Guild's registry. ``keeps_shared_connections`` marks the Forge whose connection
        store is the Guild's shared one: it keeps every project's team connections, which is
        where the Guild's Ting reads them and their tokens are renewed."""
        self._projects = projects
        self._members = members
        self._integrations = integrations
        self._rules = rules
        self._sync = sync
        self._guild = guild
        self._instance_id = instance_id
        self._keeps_shared = keeps_shared_connections
        self._refresh_after = timedelta(seconds=refresh_after_seconds)
        self._now = now
        #: When a read last failed to reach the Guild, so reads do not each wait for one
        #: that is away. A change always tries.
        self._failed_at: dict[UUID, datetime] = {}

    # --- what the team is ------------------------------------------------

    async def copy_of(self, project_id: UUID) -> ForgeProject | None:
        """The project, when this Forge has only a copy of it and a Guild to ask for it."""
        if self._guild is None:
            return None
        project = await self._projects.repository.get(project_id)
        return project if project is not None and self.is_copy(project) else None

    def is_copy(self, project: ForgeProject) -> bool:
        return bool(project.home_instance_id) and project.home_instance_id != self._instance_id

    @property
    def linked(self) -> bool:
        return self._guild is not None

    async def status(self, project_id: UUID) -> ProjectTeamSync:
        return await self._sync.get(project_id)

    async def team(self, project: ForgeProject) -> ProjectTeam:
        """The team as this Forge holds it."""
        owner = project_owner_id(project.id)
        connections = await self._integrations.list_connections(owner)
        routes = [
            (rule, route)
            for rule in await self._rules.list_for_owner(owner)
            if (route := team_route(rule, owner)) is not None
        ]
        return ProjectTeam(
            project_id=project.id,
            tenant_id=project.tenant_id,
            version=(await self._sync.get(project.id)).version,
            members=[_from_member(m) for m in await self._members.list_for_project(project.id)],
            connections=[_from_connection(c) for c in connections],
            routes=[
                TeamRouteSetting(
                    event=route.event.value,
                    connection_id=UUID(rule.integration_connection_id or ""),
                    deliver_to=route.deliver_to.value,
                    channel=route.channel,
                    enabled=rule.enabled,
                    created_at=rule.created_at,
                    updated_at=rule.updated_at,
                )
                for rule, route in routes
            ],
        )

    # --- at the home -----------------------------------------------------

    async def changed(self, project_id: UUID) -> None:
        """The team changed here: a newer version, for the Guild to take."""
        if self._guild is None:
            return
        await self._sync.changed(project_id)

    async def publish_pending(
        self, project_id: UUID, bearer: str, *, changed: bool
    ) -> ProjectTeamSync:
        """Publish what the home still owes the Guild, and say where that stands.

        ``changed`` when the team was just changed, not only read."""
        state = await self._sync.get(project_id)
        # A team the Guild never took is owed to it too, changed or not.
        if self._guild is None or not (state.pending or state.synced_at is None):
            return state
        if not changed and self._failed_lately(project_id):
            return state
        project = await self._projects.repository.get(project_id)
        if project is None:
            return state
        try:
            reply = await self._guild.publish(project_id, self._instance_id, bearer)
        except TeamGuildError as exc:
            if exc.home_instance_id and exc.home_instance_id != self._instance_id:
                return await self._become_copy(project, exc.home_instance_id, bearer, exc)
            return await self._failed(project_id, "publish", exc)
        self._failed_at.pop(project_id, None)
        return await self._sync.published(project_id, reply.version, self._now())

    async def _become_copy(
        self, project: ForgeProject, home_id: str, bearer: str, refused: TeamGuildError
    ) -> ProjectTeamSync:
        """Hold the project as a copy of the Forge the Guild says is its home.

        Only when nothing here would be lost: a member, team integration or route that the
        home lacks is named instead, and the project stays as it is.
        """
        project_id = project.id
        copy = project.model_copy(update={"home_instance_id": home_id})
        try:
            record = await self._record(project_id, copy, bearer, fresh=True)
        except TeamGuildError as exc:
            return await self._failed(project_id, "adopt", exc)
        if isinstance(record, str):
            return await self._failed(
                project_id,
                "adopt",
                TeamGuildError(
                    f"{refused} The Guild has no team of Forge {home_id} for you to copy "
                    f"({record}), so this Forge still holds the project as its own."
                ),
            )
        lost = _only_here(await self.team(project), record.team)
        if lost:
            return await self._failed(
                project_id,
                "adopt",
                ProjectConflictError(
                    f"{refused} This Forge also has {', '.join(lost)}, which Forge {home_id} "
                    "does not. Add them there or remove them here, and this Forge then holds "
                    "the project as a copy of that Forge's."
                ),
            )
        try:
            await self._apply(copy, record, force=True)
        except ProjectConflictError as exc:
            return await self._failed(project_id, "adopt", exc)
        await self._projects.repository.update(
            copy.model_copy(update={"revision": project.revision + 1, "updated_at": self._now()}),
            project.revision,
        )
        self._failed_at.pop(project_id, None)
        return await self._sync.get(project_id)

    # --- a project new here --------------------------------------------------

    async def home_of_new(self, project_id: UUID, bearer: str) -> str:
        """The Forge the Guild holds a project of this id for, when that is another one.

        Empty when there is no Guild, the Guild holds no team of it for the caller, or the
        home is this Forge. Raises ``TeamGuildError`` when the Guild cannot be asked: a
        project created here then could be a second home.
        """
        if self._guild is None:
            return ""
        try:
            record = await self._guild.record(project_id, bearer)
        except TeamNotVisibleError:
            return ""
        return "" if record.home_instance_id == self._instance_id else record.home_instance_id

    async def created(self, project: ForgeProject, bearer: str) -> None:
        """Share a project just created here: a copy takes its team, a home publishes it.

        A failure is recorded on the project, as for any read."""
        if self._guild is None:
            return
        if self.is_copy(project):
            await self.refresh_for_read(project, bearer)
            return
        await self.publish_pending(project.id, bearer, changed=True)

    # --- on a copy ---------------------------------------------------------

    async def refresh_for_read(self, project: ForgeProject, bearer: str) -> None:
        """Copy the team when the copy is older than the configured age; a failure is
        recorded on the copy, which is what is then read."""
        state = await self._sync.get(project.id)
        if state.synced_at is not None and self._now() - state.synced_at < self._refresh_after:
            return
        if self._failed_lately(project.id):
            return
        try:
            await self.refresh(project.id, bearer, fresh=True)
        except (TeamGuildError, ProjectConflictError) as exc:
            await self._failed(project.id, "copy", exc)

    async def refresh(
        self, project_id: UUID, bearer: str, *, fresh: bool = False
    ) -> tuple[str, int]:
        """Take the team from the Guild; what was done and the version now held.

        ``fresh`` has the Guild ask the home first; without it the Guild's record is enough.

        Raises ``TeamGuildError`` when the Guild cannot answer and ``ProjectConflictError``
        when what it holds is not this project's team.
        """
        if self._guild is None:
            return UNLINKED, 0
        project = await self._projects.repository.get(project_id)
        if project is None and not self._keeps_shared:
            return ABSENT, 0
        if project is not None and not self.is_copy(project):
            return HOME, (await self._sync.get(project_id)).version
        record = await self._record(project_id, project, bearer, fresh)
        if isinstance(record, str):
            return record, 0
        if project is None:
            await self._replace_connections(record.team)
            return CONNECTIONS, record.team.version
        return await self._apply(project, record)

    async def forward(
        self,
        project: ForgeProject,
        *,
        method: str,
        operation: str,
        query: str,
        body: bytes,
        bearer: str,
    ) -> TeamGuildReply:
        """Make a change where the project's home is, then copy what came of it."""
        if self._guild is None:
            raise TeamGuildError("This Forge has no Guild to reach the project's home through")
        reply = await self._guild.forward(
            project.id,
            project.home_instance_id,
            method=method,
            operation=operation,
            query=query,
            body=body,
            bearer=bearer,
        )
        if reply.status < 400:
            try:
                await self.refresh(project.id, bearer)
            except (TeamGuildError, ProjectConflictError) as exc:
                await self._failed(project.id, "copy", exc)
        return reply

    async def start_install(
        self, project: ForgeProject, *, body: bytes, bearer: str
    ) -> TeamGuildReply:
        """Start an app install at the home, which is where the provider comes back to."""
        if self._guild is None:
            raise TeamGuildError("This Forge has no Guild to reach the project's home through")
        return await self._guild.start_install(project.home_instance_id, body=body, bearer=bearer)

    # --- applying -----------------------------------------------------------

    async def _record(
        self, project_id: UUID, project: ForgeProject | None, bearer: str, fresh: bool
    ) -> ProjectTeamRecord | str:
        """The Guild's record, or what to report instead of one."""
        assert self._guild is not None
        try:
            return await self._guild.record(project_id, bearer, fresh=fresh)
        except TeamNotVisibleError:
            if project is None:
                return HIDDEN
        # Nothing recorded for this caller: a home that never published, or a person who is
        # not on the project. The home, asked to publish now, tells which.
        try:
            await self._guild.publish(project_id, project.home_instance_id, bearer)
        except TeamNotVisibleError:
            return HIDDEN
        except TeamGuildError as exc:
            if exc.route_missing:
                return UNRECORDED
            raise
        try:
            return await self._guild.record(project_id, bearer)
        except TeamNotVisibleError:
            return HIDDEN

    async def _apply(
        self, project: ForgeProject, record: ProjectTeamRecord, *, force: bool = False
    ) -> tuple[str, int]:
        """Take the record's team. ``force`` takes it whatever version this Forge holds: a
        Forge that held the project as its own counted versions of its own."""
        team = record.team
        if record.home_instance_id != project.home_instance_id:
            raise ProjectConflictError(
                f"The Guild holds project {project.id} for Forge {record.home_instance_id}, "
                f"but this copy belongs to Forge {project.home_instance_id}; its team was not "
                "taken"
            )
        if team.tenant_id != project.tenant_id or team.project_id != project.id:
            raise ProjectConflictError(
                f"The Guild's team for project {project.id} is another org's or project's; it "
                "was not taken"
            )
        state = await self._sync.get(project.id)
        if not force and state.synced_at is not None and state.version >= team.version:
            await self._sync.copied(project.id, state.version, self._now())
            return CURRENT, state.version
        # Connections first: a route only ever points at one that is already here.
        await self._replace_connections(team)
        await self._members.replace(project.id, [_to_member(project, m) for m in team.members])
        await self._replace_routes(project, team)
        await self._sync.copied(project.id, team.version, self._now())
        return UPDATED, team.version

    async def _replace_connections(self, team: ProjectTeam) -> None:
        owner = project_owner_id(team.project_id)
        wanted = {str(connection.id) for connection in team.connections}
        for connection in team.connections:
            held = await self._integrations.get_connection(str(connection.id))
            if held is not None and held.project_id != str(team.project_id):
                raise ProjectConflictError(
                    f"Integration connection {connection.id} here is not project "
                    f"{team.project_id}'s; the Guild's team for it was not taken"
                )
        for connection in team.connections:
            await self._integrations.save_connection(_to_connection(team, connection))
        for held in await self._integrations.list_connections(owner):
            if held.id not in wanted:
                await self._integrations.delete_connection(held.id)

    async def _replace_routes(self, project: ForgeProject, team: ProjectTeam) -> None:
        owner = project_owner_id(project.id)
        wanted = {
            rule.id: rule
            for route in team.routes
            for rule in [
                team_route_rule(
                    project.id,
                    event=TeamRouteEvent(route.event),
                    connection_id=str(route.connection_id),
                    deliver_to=TeamDeliverTo(route.deliver_to),
                    channel=route.channel,
                    enabled=route.enabled,
                    created_at=route.created_at,
                    updated_at=route.updated_at,
                )
            ]
        }
        for rule in await self._rules.list_for_owner(owner):
            if team_route(rule, owner) is not None and rule.id not in wanted:
                await self._rules.delete(owner, rule.id)
        for rule in wanted.values():
            if await self._rules.get(owner, rule.id) is None:
                await self._rules.create(rule)
                continue
            await self._rules.update(rule)

    def _failed_lately(self, project_id: UUID) -> bool:
        failed = self._failed_at.get(project_id)
        return failed is not None and self._now() - failed < self._refresh_after

    async def _failed(self, project_id: UUID, what: str, exc: Exception) -> ProjectTeamSync:
        message = f"project {project_id}: {exc}"
        record_degraded(f"project_team.{what}", message)
        self._failed_at[project_id] = self._now()
        return await self._sync.failed(project_id, str(exc))


def _only_here(held: ProjectTeam, home: ProjectTeam) -> list[str]:
    """What this Forge's team has that the home's lacks, as the people asked to fix it read."""
    roles = {member.user_id: member.role for member in home.members}
    lost = [
        f"member {member.user_id} as {member.role}"
        for member in held.members
        if roles.get(member.user_id) != member.role
    ]
    connections = {connection.id for connection in home.connections}
    lost += [
        f"team integration {connection.slug or connection.integration_type} ({connection.id})"
        for connection in held.connections
        if connection.id not in connections
    ]
    routes = {_route_key(route) for route in home.routes}
    lost += [
        f"route of {route.event} to {route.channel or route.deliver_to}"
        for route in held.routes
        if _route_key(route) not in routes
    ]
    return lost


def _route_key(route: TeamRouteSetting) -> tuple[str, UUID, str, str, bool]:
    return (route.event, route.connection_id, route.deliver_to, route.channel, route.enabled)


def _from_member(member: ProjectMember) -> TeamMember:
    return TeamMember(
        user_id=member.user_id,
        role=member.role.value,
        expertise=member.expertise,
        skills=member.skills,
        added_by=member.added_by,
        created_at=member.created_at,
        updated_at=member.updated_at,
    )


def _to_member(project: ForgeProject, member: TeamMember) -> ProjectMember:
    return ProjectMember(
        project_id=project.id,
        user_id=member.user_id,
        tenant_id=project.tenant_id,
        role=ProjectRole(member.role),
        expertise=member.expertise,
        skills=member.skills,
        added_by=member.added_by,
        created_at=member.created_at,
        updated_at=member.updated_at,
    )


def _from_connection(connection: IntegrationConnection) -> TeamConnection:
    return TeamConnection(
        id=UUID(connection.id),
        integration_type=str(connection.integration_type),
        adapter=connection.adapter,
        credential_name=connection.credential_name,
        config=dict(connection.config),
        enabled=connection.enabled,
        slug=connection.slug,
        connected_by=connection.connected_by,
        created_at=connection.created_at,
        updated_at=connection.updated_at,
    )


def _to_connection(team: ProjectTeam, connection: TeamConnection) -> IntegrationConnection:
    settings: dict[str, Any] = dict(connection.config)
    return IntegrationConnection(
        id=str(connection.id),
        owner_id=project_owner_id(team.project_id),
        integration_type=IntegrationType(connection.integration_type),
        adapter=connection.adapter,
        credential_name=connection.credential_name,
        config=settings,
        enabled=connection.enabled,
        created_at=connection.created_at,
        updated_at=connection.updated_at,
        slug=connection.slug,
        project_id=str(team.project_id),
        connected_by=connection.connected_by,
    )
