"""Shared Forge application service for route orchestration."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from niuu.domain.session_description import DESCRIPTION_FIELDS, SessionDescriptionPatch
from niuu.forge_mcp.credentials import normalize_grants
from volundr.domain.models import (
    HOSTED_LAUNCH_KEY,
    HOSTED_MEMBERS_KEY,
    HOSTED_UNIT_KEY,
    HostedLaunch,
    Session,
    SessionStatus,
)
from volundr.domain.ports import HostedUnitError, PersonaNotPlayableError
from volundr.domain.services.forge_session_launch import (
    FORGE_MCP_KEY,
    GRANTS_KEY,
    launch_id_of,
)
from volundr.domain.services.session import SessionNotFoundError, SessionStateError
from volundr.domain.session_broker import broker_base_url
from volundr.domain.workflow_session import (
    PERSONA_DEFINITION_KEY,
    PERSONA_NAME_KEY,
    WORKFLOW_EVENT_FILTERS_KEY,
    WORKFLOW_MEMBER_KEY,
    WORKFLOW_ORCHESTRATION_KEY,
    WORKFLOW_PERSONA_ALIAS_KEY,
    WORKFLOW_RUN_ID_KEY,
    WORKFLOW_RUN_ROOM_KEY,
    WORKFLOW_STEP_HOST_KEY,
)

if TYPE_CHECKING:
    from niuu.ports.session_proxy import SessionProxyTarget
    from volundr.adapters.inbound.rest import SessionCreate
    from volundr.domain.models import (
        Chronicle,
        ModelProvider,
        Principal,
        SessionActivityState,
        Timeline,
        TimelineEvent,
        TimelineEventType,
        WorkspaceStatus,
    )
    from volundr.domain.ports import PersonaPlayabilityPort, Playability, PricingProvider
    from volundr.domain.services.chronicle import ChronicleService
    from volundr.domain.services.repo import ProviderInfo, RepoService
    from volundr.domain.services.session_archive import SessionArchiveService
    from volundr.domain.services.session_participants import SessionParticipantService
    from volundr.domain.services.stats import StatsService
    from volundr.domain.services.token import TokenService
    from volundr.domain.services.workspace import WorkspaceService
    from volundr.domain.session_changes import SessionChanges
    from volundr.domain.session_participants import SessionParticipant
    from volundr.domain.session_read_state import SessionReadState, SessionReadStateChange

    from .session import SessionService


#: The port of the first member's broker inside a unit; the next member's is one above, and
#: so on. The unit root's own Skuld keeps its usual port (8081), so members start just above
#: it. The composition root passes the configured value as ``hosted_member_base_port``.
DEFAULT_HOSTED_MEMBER_BASE_PORT = 8082

_LIVE_UNIT_STATUSES = frozenset(
    {SessionStatus.STARTING, SessionStatus.PROVISIONING, SessionStatus.RUNNING}
)


class HostedMemberError(SessionStateError):
    """A member of a run's unit cannot be created, started or stopped in the unit's state.

    A ``SessionStateError``, so the session routes already answer it with 409; ``reason``
    names what is wrong and what to do about it.
    """

    def __init__(
        self,
        session_id: UUID,
        operation: str,
        current_status: SessionStatus | None,
        reason: str,
    ) -> None:
        self.session_id = session_id
        self.operation = operation
        self.current_status = current_status
        self.reason = reason
        Exception.__init__(self, reason)


class HostedUnitNotFoundError(SessionNotFoundError):
    """The unit a member names does not exist.

    A ``SessionNotFoundError``, so a caller answers it as not found (404), not as a
    session in the wrong state; ``reason`` names the unit and what to do about it.
    """

    def __init__(self, unit_id: UUID, reason: str) -> None:
        self.session_id = unit_id
        self.reason = reason
        Exception.__init__(self, reason)


class HostedUnitInUseError(SessionStateError):
    """A unit root cannot be deleted while it still hosts members.

    A ``SessionStateError``, so the routes answer it with 409 and the message; the members
    are deleted first, then the unit.
    """

    def __init__(self, unit_id: UUID, member_count: int) -> None:
        self.session_id = unit_id
        self.operation = "delete"
        self.current_status = None
        self.member_count = member_count
        Exception.__init__(
            self,
            f"session {unit_id} is the unit of {member_count} member session(s): "
            "delete the members first",
        )


class ForgeService:
    """Compose the session, stats, pricing, and token services behind Forge routes."""

    def __init__(
        self,
        session_service: SessionService,
        *,
        stats_service: StatsService | None = None,
        token_service: TokenService | None = None,
        pricing_provider: PricingProvider | None = None,
        repo_service: RepoService | None = None,
        chronicle_service: ChronicleService | None = None,
        archive_service: SessionArchiveService | None = None,
        workspace_service: WorkspaceService | None = None,
        project_service=None,
        session_participant_service: SessionParticipantService,
        hosted_member_base_port: int = DEFAULT_HOSTED_MEMBER_BASE_PORT,
        persona_playability: PersonaPlayabilityPort | None = None,
    ) -> None:
        if not 0 < hosted_member_base_port < 65536:
            raise ValueError(
                f"hosted_member_base_port must be a TCP port, got {hosted_member_base_port}"
            )
        self._hosted_member_base_port = hosted_member_base_port
        self._session_service = session_service
        self._stats_service = stats_service
        self._token_service = token_service
        self._pricing_provider = pricing_provider
        self._repo_service = repo_service
        self._chronicle_service = chronicle_service
        self._archive_service = archive_service
        self._workspace_service = workspace_service
        self._project_service = project_service
        self._session_participant_service = session_participant_service
        self._persona_playability = persona_playability

    @property
    def has_broadcaster(self) -> bool:
        return self._session_service._broadcaster is not None

    def with_workspace_service(self, workspace_service: WorkspaceService) -> ForgeService:
        return ForgeService(
            self._session_service,
            stats_service=self._stats_service,
            token_service=self._token_service,
            pricing_provider=self._pricing_provider,
            repo_service=self._repo_service,
            chronicle_service=self._chronicle_service,
            archive_service=self._archive_service,
            workspace_service=workspace_service,
            project_service=self._project_service,
            session_participant_service=self._session_participant_service,
            hosted_member_base_port=self._hosted_member_base_port,
            persona_playability=self._persona_playability,
        )

    async def list_sessions(
        self,
        *,
        status=None,
        include_archived: bool = False,
        principal: Principal | None = None,
    ) -> list[Session]:
        sessions = await self._session_service.list_sessions(
            status=status,
            include_archived=include_archived,
            principal=principal,
        )
        return await self._hosted_views(sessions)

    async def list_participant_only_sessions(
        self,
        *,
        status=None,
        include_archived: bool = False,
        principal: Principal | None = None,
        exclude_ids: frozenset[UUID] = frozenset(),
    ) -> list[tuple[Session, SessionParticipant]]:
        """Sessions *principal* actively participates in but does not own.

        Returns each session paired with the caller's own grant (for
        rendering a room summary that shows their role). Excludes
        ``exclude_ids`` (the caller's own ``list_sessions`` result, so a
        session already owned/tenant-visible is never duplicated) and honors
        the status filter, matching ``list_sessions``'s own contract. Cedar
        authorizes these for ``read_room``, not ``read`` — the caller must
        never render the full session detail for one of these (see
        SessionParticipantService.list_participant_sessions).
        """
        if principal is None:
            return []
        pairs = await self._session_participant_service.list_participant_sessions(
            principal, include_archived=include_archived
        )
        if status is not None:
            pairs = [(s, g) for s, g in pairs if s.status == status]
        return [(s, g) for s, g in pairs if s.id not in exclude_ids]

    async def archive_stopped_sessions(self) -> list[UUID]:
        return await self._session_service.archive_stopped_sessions()

    async def create_and_start_session(
        self,
        data: SessionCreate,
        *,
        principal: Principal | None = None,
    ) -> Session:
        # Refuse an escalating create before a dispatch claim or session row exists.
        coordination = getattr(data, "coordination", None)
        forge_mcp = getattr(data, "forge_mcp", None)
        self._session_service.check_forge_mcp_grants(
            principal,
            tuple(forge_mcp.grants) if forge_mcp is not None else (),
            launch_spec=data.launch_spec,
            role=coordination.role if coordination is not None else None,
        )
        hosted_by = getattr(data, "hosted_by", None)
        if isinstance(hosted_by, UUID):
            await self._require_open_unit(hosted_by, data, principal)
        if coordination is not None:
            if self._project_service is None:
                raise ValueError("Projects are not enabled on this Forge host")
            async with self._project_service.dispatch(data, principal) as (existing, options):
                if existing is not None and existing.status.value != "created":
                    return existing
                return await self._launch_session(data, principal, existing, options)
        if getattr(data, "dispatch_id", None) is not None:
            raise ValueError("dispatch_id currently requires project coordination metadata")
        return await self._launch_session(data, principal)

    async def _launch_session(self, data, principal, existing=None, project_options=None):
        resolved_definition = self._resolve_session_definition(data.model, data.definition)
        self.require_playable(
            data.model, getattr(data, "persona_definition", None), data.definition
        )
        forge_mcp = getattr(data, "forge_mcp", None)
        requested_grants = tuple(forge_mcp.grants) if forge_mcp is not None else ()
        # No slot, no record: a session created only to fail would sit in the
        # list as an error the person did not ask for.
        await self._session_service.ensure_capacity()
        session = existing or await self._session_service.create_session(
            name=data.name,
            model=data.model,
            source=data.source,
            launch_spec=data.launch_spec,
            launch_spec_id=data.launch_spec_id,
            principal=principal,
            workspace_id=data.workspace_id,
            tracker_issue_id=data.issue_id,
            issue_tracker_url=data.issue_url,
            description=_seeded_description(data),
            **(project_options or {}),
        )
        workload_config = dict(data.workload_config or {})
        persona_name = getattr(data, "persona_name", "")
        if persona_name:
            workload_config[PERSONA_NAME_KEY] = persona_name
        persona_definition = getattr(data, "persona_definition", None)
        if persona_definition is not None:
            workload_config[PERSONA_DEFINITION_KEY] = persona_definition
        if getattr(data, "workflow_step_host", False) is True:
            workload_config[WORKFLOW_STEP_HOST_KEY] = True
        workflow_run_id = getattr(data, "workflow_run_id", None)
        if getattr(data, "workflow_run_room", False) is True:
            workload_config[WORKFLOW_RUN_ROOM_KEY] = True
            workload_config[WORKFLOW_RUN_ID_KEY] = str(workflow_run_id)
        elif workflow_run_id is not None:
            workload_config[WORKFLOW_RUN_ID_KEY] = str(workflow_run_id)
            workload_config[WORKFLOW_MEMBER_KEY] = data.workflow_member
        # What Skuld needs to serve the run's steps: kept as sent, so a request that left one
        # out stays without it and the workflow_step_host contributor refuses it by name.
        persona_alias = getattr(data, "workflow_persona_alias", None)
        if isinstance(persona_alias, str):
            workload_config[WORKFLOW_PERSONA_ALIAS_KEY] = persona_alias
        event_filters = getattr(data, "workflow_event_filters", None)
        if isinstance(event_filters, dict):
            workload_config[WORKFLOW_EVENT_FILTERS_KEY] = dict(event_filters)
        orchestration = getattr(data, "workflow_orchestration", None)
        if isinstance(orchestration, str):
            workload_config[WORKFLOW_ORCHESTRATION_KEY] = orchestration
        if requested_grants:
            workload_config[FORGE_MCP_KEY] = {
                GRANTS_KEY: [grant.value for grant in normalize_grants(requested_grants)]
            }
        hosted_by = getattr(data, "hosted_by", None)
        hosts_members = getattr(data, "hosts_members", False) is True
        if isinstance(hosted_by, UUID) or hosts_members:
            launch = HostedLaunch(
                definition=resolved_definition,
                launch_spec=data.launch_spec,
                terminal_restricted=data.terminal_restricted,
                credential_names=list(data.credential_names),
                integration_ids=list(data.integration_ids),
                resource_config=dict(data.resource_config or {}),
                system_prompt=data.system_prompt,
                initial_prompt=data.initial_prompt,
                workload_type=data.workload_type,
            )
            if isinstance(hosted_by, UUID):
                workload_config[HOSTED_LAUNCH_KEY] = launch.model_dump()
                return await self._hold_member(
                    session, hosted_by, workload_config, launch, principal, created=existing is None
                )
            workload_config[HOSTED_UNIT_KEY] = launch.model_dump()
            return await self._hold(session, workload_config, launch)
        return await self._session_service.start_session(
            session.id,
            definition=resolved_definition,
            launch_spec=data.launch_spec,
            principal=principal,
            terminal_restricted=data.terminal_restricted,
            credential_names=data.credential_names,
            integration_ids=data.integration_ids,
            resource_config=data.resource_config or None,
            system_prompt=data.system_prompt,
            initial_prompt=data.initial_prompt,
            workload_type=data.workload_type,
            workload_config=workload_config or None,
        )

    # -- A run's unit and the members it hosts ---------------------------------------------

    async def _require_open_unit(self, unit_id: UUID, data, principal: Principal | None) -> None:
        """Refuse a member for a unit that is missing, is not a unit, belongs to someone else
        or to another run, or has already started: a run's members are fixed at that point."""
        unit = await self._session_service.get_session(unit_id)
        if unit is None:
            raise HostedUnitNotFoundError(unit_id, f"unit session {unit_id} does not exist")
        if not unit.is_unit_root:
            raise HostedMemberError(
                unit_id,
                "add a member to",
                unit.status,
                f"session {unit_id} is not a unit: create it with hosts_members to host a "
                "run's members",
            )
        if unit.status != SessionStatus.CREATED:
            raise HostedMemberError(
                unit_id,
                "add a member to",
                unit.status,
                f"a run's members are fixed when its unit starts: unit session {unit_id} is "
                f"{unit.status.value}",
            )
        if principal is not None and (
            unit.owner_id != principal.user_id or unit.tenant_id != principal.tenant_id
        ):
            raise HostedMemberError(
                unit_id,
                "add a member to",
                unit.status,
                f"unit session {unit_id} belongs to another owner",
            )
        unit_run = unit.workload_config.get(WORKFLOW_RUN_ID_KEY)
        member_run = getattr(data, "workflow_run_id", None)
        if (str(member_run) if member_run is not None else None) != unit_run:
            raise HostedMemberError(
                unit_id,
                "add a member to",
                unit.status,
                f"unit session {unit_id} hosts workflow run {unit_run}, not {member_run}: a "
                "member joins the unit of its own run",
            )

    async def _hold(self, session: Session, workload_config: dict, launch: HostedLaunch) -> Session:
        """Keep a created session, with its launch, until its unit starts."""
        held = await self._session_service._repository.update(
            session.model_copy(
                update={
                    "workload_config": workload_config,
                    "session_definition": launch.definition,
                    "workload_type": launch.workload_type,
                    "updated_at": datetime.now(UTC),
                }
            )
        )
        await self._publish_updated(held)
        return held

    async def _hold_member(
        self,
        session: Session,
        unit_id: UUID,
        workload_config: dict,
        launch: HostedLaunch,
        principal: Principal | None,
        *,
        created: bool,
    ) -> Session:
        """Attach a created session to its unit, atomically with the unit still being open."""
        held = await self._hold(session, workload_config, launch)
        attached = await self._session_service._repository.attach_to_unit(
            held.id, unit_id, self._hosted_member_base_port
        )
        if attached is None:
            # The unit started (or was removed) after the check above. The member this call
            # created must not outlive the refusal.
            if created:
                await self._session_service.delete_session(held.id, principal=principal)
            raise HostedMemberError(
                unit_id,
                "add a member to",
                None,
                f"a run's members are fixed when its unit starts: unit session {unit_id} "
                "started or was removed while the member was being added",
            )
        await self._publish_updated(attached)
        return await self._hosted_view(attached)

    async def _publish_updated(self, session: Session) -> None:
        broadcaster = self._session_service._broadcaster
        if broadcaster is not None:
            await broadcaster.publish_session_updated(session)

    async def _hosted_view(self, session: Session, *, reconcile: bool = False) -> Session:
        """``session`` as its unit shows it, when it is a hosted member; else unchanged."""
        unit_id = getattr(session, "hosted_by", None)
        if not isinstance(unit_id, UUID):
            return session
        unit = (
            await self._session_service.reconcile_session_if_active(unit_id)
            if reconcile
            else await self._session_service.get_session(unit_id)
        )
        return self._member_in(session, unit)

    def _member_in(self, member: Session, unit: Session | None) -> Session:
        """``member`` as its unit shows it, at the endpoint its runtime names for it."""
        if unit is None:
            return member.model_copy(
                update={
                    "status": SessionStatus.FAILED,
                    "chat_endpoint": None,
                    "code_endpoint": None,
                    "error": f"its unit session {member.hosted_by} is gone",
                }
            )
        return member.hosted_in(unit, self._session_service.member_chat_endpoint(member))

    async def _hosted_views(self, sessions: list[Session]) -> list[Session]:
        unit_ids = {
            s.hosted_by for s in sessions if isinstance(getattr(s, "hosted_by", None), UUID)
        }
        if not unit_ids:
            return sessions
        known = {s.id: s for s in sessions if s.id in unit_ids}
        missing = [unit_id for unit_id in unit_ids if unit_id not in known]
        units = {**known, **await self._session_service.get_many_sessions(missing)}
        return [
            self._member_in(s, units.get(s.hosted_by))
            if isinstance(getattr(s, "hosted_by", None), UUID)
            else s
            for s in sessions
        ]

    async def _prepare_member(
        self, member: Session, principal: Principal | None, *, launch_spec: str | None
    ) -> Session:
        """A new launch of a member: its own launch id (so its own credential), and STARTING.

        The previous launch's credential and mesh identity are retired exactly as a
        restart of an ordinary session retires them.
        """
        launch = HostedLaunch.model_validate(member.workload_config.get(HOSTED_LAUNCH_KEY) or {})
        await self._session_service._retire_mesh_identity(
            member.id, launch_id_of(member.workload_config)
        )
        workload_config = self._session_service._with_forge_mcp_launch(
            member, principal, member.workload_config, launch_spec or launch.launch_spec
        )
        prepared = member.model_copy(
            update={
                "status": SessionStatus.STARTING,
                "session_definition": launch.definition or member.session_definition,
                "workload_type": launch.workload_type,
                "workload_config": workload_config,
                "chat_endpoint": self._session_service.member_chat_endpoint(member),
                "code_endpoint": None,
                "error": None,
                "updated_at": datetime.now(UTC),
            }
        )
        return await self._session_service._repository.update(prepared)

    async def _start_unit(
        self,
        root: Session,
        *,
        launch_spec: str | None,
        principal: Principal | None,
        integration_ids: list[str] | None,
    ) -> Session:
        """Start a unit root together with every member it hosts.

        The members get their new launches first, because the runtime that renders the unit
        reads them (and mints their credentials) while the root starts. If the root cannot
        start, every member is put back as it was.
        """
        await self.ensure_access(root, principal, "start")
        if not root.can_start():
            raise SessionStateError(root.id, "start", root.status)
        launch = HostedLaunch.model_validate(root.workload_config[HOSTED_UNIT_KEY])
        repository = self._session_service._repository
        members = await repository.list_hosted(root.id)
        prepared = [
            await self._prepare_member(m, principal, launch_spec=launch_spec) for m in members
        ]
        unit_config = {
            # The pool execution the service pinned stays on the row; a caller may not send it.
            **{k: v for k, v in root.workload_config.items() if k != "_compute_execution"},
            HOSTED_MEMBERS_KEY: [{"id": str(m.id), "port": m.hosted_port} for m in members],
        }
        try:
            started = await self._session_service.start_session(
                root.id,
                definition=launch.definition,
                launch_spec=launch_spec or launch.launch_spec,
                principal=principal,
                terminal_restricted=launch.terminal_restricted,
                credential_names=launch.credential_names,
                integration_ids=integration_ids or launch.integration_ids,
                resource_config=launch.resource_config or None,
                system_prompt=launch.system_prompt,
                initial_prompt=launch.initial_prompt,
                workload_type=launch.workload_type,
                workload_config=unit_config,
            )
        except BaseException:
            for original in members:
                await repository.update(original)
            raise
        for member in prepared:
            await self._publish_updated(member)
        return started

    async def _start_member(
        self,
        member: Session,
        *,
        launch_spec: str | None,
        principal: Principal | None,
    ) -> Session:
        """Start a member again inside its running unit, under a new launch."""
        await self.ensure_access(member, principal, "start")
        unit = await self._session_service.reconcile_session_if_active(member.hosted_by)
        if unit is None or unit.status not in _LIVE_UNIT_STATUSES:
            raise HostedMemberError(
                member.id,
                "start",
                member.status,
                f"session {member.id} runs in unit session {member.hosted_by}, which is "
                f"{unit.status.value if unit is not None else 'gone'}: start the unit, and its "
                "members start with it",
            )
        effective = member.hosted_in(unit)
        if not effective.can_start():
            raise SessionStateError(member.id, "start", effective.status)
        prepared = await self._prepare_member(member, principal, launch_spec=launch_spec)
        try:
            await self._session_service.restart_hosted_member(prepared, principal)
        except BaseException:
            # The member did not restart: it is as it was, not recorded under a new launch.
            await self._session_service._repository.update(member)
            raise
        await self._publish_updated(prepared)
        return self._member_in(prepared, unit)

    async def _end_member(
        self, member: Session, principal: Principal | None, *, unit_running: bool
    ) -> Session:
        return await self._session_service.end_hosted_member(
            member, principal, unit_running=unit_running
        )

    async def _stop_member(self, member: Session, principal: Principal | None) -> Session:
        """Stop one member's Skuld and harness; the unit and the other members keep running."""
        await self.ensure_access(member, principal, "stop")
        unit = await self._session_service.reconcile_session_if_active(member.hosted_by)
        if unit is None:
            raise HostedMemberError(
                member.id,
                "stop",
                member.status,
                f"unit session {member.hosted_by} of session {member.id} is gone",
            )
        effective = member.hosted_in(unit)
        if not effective.can_stop():
            raise SessionStateError(member.id, "stop", effective.status)
        try:
            stopped = await self._end_member(
                member.model_copy(update={"status": effective.status}),
                principal,
                unit_running=True,
            )
        except HostedUnitError as exc:
            # The runtime could not confirm the member's process ended: the member stays as
            # it was and the caller is told why (a session state conflict, 409), not 500.
            raise HostedMemberError(member.id, "stop", effective.status, str(exc)) from exc
        return self._member_in(stopped, unit)

    def _resolve_session_definition(
        self,
        model: str,
        explicit_definition: str | None,
    ) -> str | None:
        if explicit_definition:
            return explicit_definition
        return catalog_session_definition(self._pricing_provider, model)

    def check_playable(
        self, model: str, persona: Mapping[str, Any], *, definition: str | None = None
    ) -> Playability:
        """Whether ``persona`` can be played on the harness a session of ``model`` runs."""
        if self._persona_playability is None:
            raise PersonaNotPlayableError(
                "this Forge cannot check personas against harnesses: no persona playability "
                "checker is configured"
            )
        return self._persona_playability.check(model, persona, definition=definition)

    def require_playable(
        self, model: str, persona: Mapping[str, Any] | None, definition: str | None
    ) -> None:
        """Refuse a session whose persona its harness would refuse to start, before it exists."""
        if persona is None:
            return
        verdict = self.check_playable(model, persona, definition=definition)
        if verdict.playable:
            return
        raise PersonaNotPlayableError(
            f"persona {persona.get('id')!r} cannot run on model {model!r} "
            f"({verdict.transport or verdict.definition or 'no harness'}): "
            + "; ".join(verdict.reasons)
        )

    async def with_read_states(
        self, sessions: list[Session], principal: Principal | None
    ) -> list[Session]:
        return await self._session_service.with_read_states(sessions, principal)

    async def session_changes_since(
        self, cursor: int, principal: Principal | None
    ) -> SessionChanges:
        return await self._session_service.changes_since(cursor, principal)

    async def get_read_state(self, session_id: UUID, principal: Principal) -> SessionReadState:
        return await self._session_service.get_read_state(session_id, principal)

    async def change_read_state(
        self, session_id: UUID, change: SessionReadStateChange, principal: Principal
    ) -> SessionReadState:
        return await self._session_service.change_read_state(session_id, change, principal)

    async def get_session(self, session_id: UUID) -> Session | None:
        session = await self._session_service.reconcile_session_if_active(session_id)
        if session is None:
            return None
        return await self._hosted_view(session, reconcile=True)

    async def reconcile_session(self, session_id: UUID) -> Session | None:
        """Force a pod-status reconcile of one session (used on a dead-pod op)."""
        return await self._session_service.mark_session_dead(session_id)

    async def get_authorized_session(
        self, session_id: UUID, *, principal: Principal | None, action: str
    ) -> Session:
        session = await self._session_service.get_authorized_session(session_id, principal, action)
        return await self._hosted_view(session)

    async def ensure_access(
        self,
        session: Session,
        principal: Principal | None,
        action: str,
    ) -> None:
        await self._session_service._check_access(session, principal, action)

    async def update_session(
        self,
        *,
        session_id: UUID,
        name: str | None = None,
        model: str | None = None,
        branch: str | None = None,
        tracker_issue_id: str | None = None,
        principal: Principal | None = None,
    ) -> Session:
        return await self._session_service.update_session(
            session_id=session_id,
            name=name,
            model=model,
            branch=branch,
            tracker_issue_id=tracker_issue_id,
            principal=principal,
        )

    async def update_description(
        self,
        session_id: UUID,
        patch: SessionDescriptionPatch,
        *,
        principal: Principal | None = None,
    ) -> Session:
        return await self._session_service.update_description(session_id, patch, principal)

    async def worked_seconds_24h(self, session_ids: list[UUID]) -> dict[UUID, int] | None:
        return await self._session_service.worked_seconds_24h(session_ids)

    async def get_forge_mcp_grants(self, session_id: UUID, *, principal: Principal | None = None):
        return await self._session_service.get_forge_mcp_grants(session_id, principal)

    async def set_forge_mcp_grants(
        self, session_id: UUID, grants, *, principal: Principal | None = None
    ):
        return await self._session_service.set_forge_mcp_grants(session_id, grants, principal)

    async def delete_session(
        self,
        session_id: UUID,
        *,
        principal: Principal | None = None,
        cleanup_targets=None,
    ) -> bool:
        members = await self._session_service._repository.list_hosted(session_id)
        if members:
            raise HostedUnitInUseError(session_id, len(members))
        return await self._session_service.delete_session(
            session_id,
            principal=principal,
            cleanup_targets=cleanup_targets,
        )

    async def start_session(
        self,
        session_id: UUID,
        *,
        launch_spec: str | None = None,
        principal: Principal | None = None,
        integration_ids: list[str] | None = None,
    ) -> Session:
        session = await self._session_service.get_session(session_id)
        if isinstance(session, Session) and session.hosted_by is not None:
            return await self._start_member(session, launch_spec=launch_spec, principal=principal)
        if isinstance(session, Session) and session.is_unit_root:
            return await self._start_unit(
                session,
                launch_spec=launch_spec,
                principal=principal,
                integration_ids=integration_ids,
            )
        return await self._session_service.start_session(
            session_id,
            launch_spec=launch_spec,
            principal=principal,
            integration_ids=integration_ids,
        )

    async def stop_session(
        self,
        session_id: UUID,
        *,
        principal: Principal | None = None,
    ) -> Session:
        session = await self._session_service.get_session(session_id)
        if isinstance(session, Session) and session.hosted_by is not None:
            return await self._stop_member(session, principal)
        if isinstance(session, Session) and session.is_unit_root:
            members = await self._session_service._repository.list_hosted(session.id)
            stopped = await self._session_service.stop_session(session_id, principal=principal)
            for member in members:
                await self._end_member(member, principal, unit_running=False)
            return stopped
        return await self._session_service.stop_session(session_id, principal=principal)

    async def update_activity(
        self,
        session_id: UUID,
        activity_state: SessionActivityState,
        metadata: dict | None,
        state_since: datetime | None = None,
        turn_started_at: datetime | None = None,
    ) -> Session:
        # FAULT A: the REST endpoint passes ``state_since=`` (the broker-stamped
        # UTC transition time). The facade previously dropped it, so the kwarg
        # raised TypeError on EVERY activity report — swallowed into a false 204,
        # so activity_state never persisted and the SSE never fired. Forward it.
        # ``turn_started_at`` (the stable turn anchor) hit the same trap: added to
        # the REST endpoint and the deep service but not this facade, so every
        # activity report raised TypeError → 500. Any new activity kwarg MUST be
        # threaded through here too.
        # Forward only when present so existing 3-positional callers/mocks stay
        # exactly equivalent (the deep method defaults both kwargs itself).
        kwargs: dict = {}
        if state_since is not None:
            kwargs["state_since"] = state_since
        if turn_started_at is not None:
            kwargs["turn_started_at"] = turn_started_at
        return await self._session_service.update_activity(
            session_id, activity_state, metadata, **kwargs
        )

    async def archive_session(
        self,
        session_id: UUID,
        *,
        principal: Principal | None = None,
    ) -> Session:
        return await self._session_service.archive_session(session_id, principal=principal)

    async def restore_session(
        self,
        session_id: UUID,
        *,
        principal: Principal | None = None,
    ) -> Session:
        return await self._session_service.restore_session(session_id, principal=principal)

    def list_models(self):
        if self._pricing_provider is None:
            raise RuntimeError("Pricing provider not available")
        return self._pricing_provider.list_models()

    def list_providers(self) -> list[ProviderInfo]:
        if self._repo_service is None:
            raise RuntimeError("Repo service not available")
        return self._repo_service.list_providers()

    async def list_branches(self, repo_url: str, *, user_id: str | None = None) -> list[str]:
        if self._repo_service is None:
            raise RuntimeError("Repo service not available")
        return await self._repo_service.list_branches(repo_url, user_id=user_id)

    async def create_or_update_chronicle_from_broker(
        self,
        *,
        session_id: UUID,
        principal: Principal | None,
        summary: str | None = None,
        key_changes: list[str] | None = None,
        unfinished_work: str | None = None,
        duration_seconds: int | None = None,
    ) -> Chronicle:
        return await self._chronicles().create_or_update_from_broker(
            session_id,
            principal=principal,
            summary=summary,
            key_changes=key_changes,
            unfinished_work=unfinished_work,
            duration_seconds=duration_seconds,
        )

    async def list_chronicles(
        self,
        *,
        principal: Principal | None,
        project: str | None = None,
        repo: str | None = None,
        model: str | None = None,
        tags: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Chronicle]:
        return await self._chronicles().list_chronicles(
            principal=principal,
            project=project,
            repo=repo,
            model=model,
            tags=tags,
            limit=limit,
            offset=offset,
        )

    async def create_chronicle(self, session_id: UUID, *, principal: Principal | None) -> Chronicle:
        return await self._chronicles().create_chronicle(session_id, principal=principal)

    async def get_chronicle(
        self, chronicle_id: UUID, *, principal: Principal | None
    ) -> Chronicle | None:
        return await self._chronicles().get_chronicle(chronicle_id, principal=principal)

    async def update_chronicle(
        self,
        chronicle_id: UUID,
        *,
        principal: Principal | None,
        summary: str | None = None,
        key_changes: list[str] | None = None,
        unfinished_work: str | None = None,
        tags: list[str] | None = None,
        status=None,
    ) -> Chronicle:
        return await self._chronicles().update_chronicle(
            chronicle_id,
            principal=principal,
            summary=summary,
            key_changes=key_changes,
            unfinished_work=unfinished_work,
            tags=tags,
            status=status,
        )

    async def delete_chronicle(self, chronicle_id: UUID, *, principal: Principal | None) -> None:
        await self._chronicles().delete_chronicle(chronicle_id, principal=principal)

    async def reforge_chronicle(
        self, chronicle_id: UUID, *, principal: Principal | None
    ) -> Session:
        return await self._chronicles().reforge(chronicle_id, principal=principal)

    async def get_chronicle_chain(
        self, chronicle_id: UUID, *, principal: Principal | None
    ) -> list[Chronicle]:
        return await self._chronicles().get_chain(chronicle_id, principal=principal)

    async def get_session_chronicle(
        self, session_id: UUID, *, principal: Principal | None
    ) -> Chronicle | None:
        return await self._chronicles().get_session_chronicle(session_id, principal=principal)

    async def get_timeline(
        self, session_id: UUID, *, principal: Principal | None
    ) -> Timeline | None:
        return await self._chronicles().get_timeline(session_id, principal=principal)

    async def add_timeline_event(
        self,
        session_id: UUID,
        *,
        principal: Principal | None,
        t: int,
        type: TimelineEventType,
        label: str,
        tokens: int | None = None,
        action: str | None = None,
        ins: int | None = None,
        del_: int | None = None,
        hash: str | None = None,
        exit_code: int | None = None,
    ) -> TimelineEvent:
        return await self._chronicles().add_timeline_event(
            session_id,
            principal=principal,
            t=t,
            type=type,
            label=label,
            tokens=tokens,
            action=action,
            ins=ins,
            del_=del_,
            hash=hash,
            exit_code=exit_code,
        )

    def _chronicles(self) -> ChronicleService:
        if self._chronicle_service is None:
            raise RuntimeError("Chronicle service not available")
        return self._chronicle_service

    async def list_workspaces(
        self,
        *,
        user_id: str,
        status: WorkspaceStatus | None = None,
    ) -> list:
        if self._workspace_service is None:
            raise RuntimeError("Workspace service not available")
        return await self._workspace_service.list_workspaces(user_id, status)

    async def list_all_workspaces(self, status: WorkspaceStatus | None = None) -> list:
        if self._workspace_service is None:
            raise RuntimeError("Workspace service not available")
        return await self._workspace_service.list_all_workspaces(status)

    async def delete_workspace_by_session(self, session_id: str) -> bool:
        if self._workspace_service is None:
            raise RuntimeError("Workspace service not available")
        return await self._workspace_service.delete_workspace_by_session(session_id)

    async def get_sessions_for_workspaces(self, workspaces: list) -> dict:
        session_ids = [ws.session_id for ws in workspaces]
        return await self._session_service._repository.get_many(session_ids)

    async def get_session_proxy_target(self, session_id: UUID) -> tuple[Session, str]:
        session = await self.get_session(session_id)
        if session is None:
            raise LookupError(f"Session not found: {session_id}")
        if not session.chat_endpoint:
            raise ValueError(f"Session {session_id} has no active endpoint")
        return session, broker_base_url(session.chat_endpoint)

    async def get_broker_target(self, session: Session) -> SessionProxyTarget | None:
        """The broker target Forge dials for ``session``; None means its chat endpoint."""
        return await self._session_service.broker_target(session)

    async def get_transcript(self, session_id: UUID, *, tail_turns: int = 0) -> dict:
        """The persisted transcript; ``tail_turns > 0`` lets the durable rebuild read only
        the newest turns (LEX-1345)."""
        if self._archive_service is None:
            raise RuntimeError("Session archive service not available")
        if tail_turns > 0:
            return await self._archive_service.get_transcript(session_id, tail_turns=tail_turns)
        return await self._archive_service.get_transcript(session_id)

    async def get_attachments(self, session_id: UUID) -> dict:
        """The session's document/image index (LEX-1259), from the durable rebuild."""
        if self._archive_service is None:
            raise RuntimeError("Session archive service not available")
        return await self._archive_service.get_attachments(session_id)

    async def durable_latest_seq(self, session_id: UUID) -> int:
        """Cheap durable-freshness signal (event-log MAX(seq), 0 when unavailable). Lets the read
        path cache the durable turn count and skip the expensive rebuild while it's unchanged."""
        if self._archive_service is None:
            return 0
        return await self._archive_service.latest_event_seq(session_id)

    async def get_transcript_download_path(self, session_id: UUID, fmt: str):
        if self._archive_service is None:
            raise RuntimeError("Session archive service not available")
        return await self._archive_service.get_transcript_download_path(session_id, fmt)

    async def get_archive_manifest(self, session_id: UUID) -> dict:
        if self._archive_service is None:
            raise RuntimeError("Session archive service not available")
        return await self._archive_service.get_archive_manifest(session_id)

    async def build_archive(self, session_id: UUID, *, force: bool = False) -> dict:
        if self._archive_service is None:
            raise RuntimeError("Session archive service not available")
        return await self._archive_service.build_archive(session_id, force=force)

    async def get_aggregated_logs(
        self,
        session_id: UUID,
        *,
        lines: int = 200,
        level: str = "DEBUG",
        participants: set[str] | None = None,
        query: str = "",
    ) -> dict:
        if self._archive_service is None:
            raise RuntimeError("Session archive service not available")
        return await self._archive_service.get_logs(
            session_id,
            lines=lines,
            level=level,
            participants=participants,
            query=query,
        )

    async def get_stats(self, principal: Principal | None):
        if self._stats_service is None:
            raise RuntimeError("Stats service not available")
        return await self._stats_service.get_stats(principal)

    async def record_usage(
        self,
        *,
        session_id: UUID,
        tokens: int,
        provider: ModelProvider,
        model: str,
        message_count: int = 0,
        cost: float | None = None,
    ):
        if self._token_service is None:
            raise RuntimeError("Token service not available")
        return await self._token_service.record_usage(
            session_id=session_id,
            tokens=tokens,
            provider=provider,
            model=model,
            message_count=message_count,
            cost=cost,
        )


def _seeded_description(data) -> SessionDescriptionPatch | None:
    """The description keys a create request sent, normalized; None when it sent none."""
    sent = getattr(data, "model_fields_set", set())
    values = {name: getattr(data, name) for name in DESCRIPTION_FIELDS if name in sent}
    if not values:
        return None
    return SessionDescriptionPatch.model_validate(values)


def catalog_session_definition(pricing_provider: PricingProvider | None, model: str) -> str | None:
    """The session definition the model catalog gives ``model``, if it names one."""
    if pricing_provider is None:
        return None
    normalized_model = str(model or "").strip()
    if not normalized_model:
        return None
    for candidate in pricing_provider.list_models():
        if str(getattr(candidate, "id", "") or "").strip() != normalized_model:
            continue
        definition = str(getattr(candidate, "session_definition", "") or "").strip()
        return definition or None
    return None
