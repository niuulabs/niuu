"""Domain services for session management."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, UUID, uuid5

try:
    from sleipnir.domain.catalog import volundr_session_failed as _catalog_failed
    from sleipnir.domain.catalog import volundr_session_started as _catalog_started
except ImportError:
    _catalog_started = None  # type: ignore[assignment]
    _catalog_failed = None  # type: ignore[assignment]

from volundr.domain.execution_catalog import ExecutionCatalogError, ExecutionSelectionError
from volundr.domain.models import (
    CleanupTarget,
    CommunicationRoute,
    EventType,
    GitSource,
    IntegrationConnection,
    IntegrationType,
    LocalMountSource,
    Principal,
    RealtimeEvent,
    Session,
    SessionActivityState,
    SessionSource,
    SessionSpec,
    SessionStatus,
    TenantRole,
)
from volundr.domain.ports import (
    AttentionNotifier,
    AuthorizationPort,
    ChronicleRepository,
    CommunicationRouteRepository,
    EventBroadcaster,
    IntegrationRepository,
    LaunchSpecProvider,
    PodManager,
    Resource,
    SessionCapacity,
    SessionCommunicationPort,
    SessionContext,
    SessionContribution,
    SessionContributor,
    SessionExecutionResolver,
    SessionRepository,
    SessionSpanRepository,
    StoragePort,
)
from volundr.domain.projects import SessionCoordination
from volundr.domain.session_read_state import SessionReadState, SessionReadStateChange

if TYPE_CHECKING:
    from niuu.ports.user_integration import UserIntegrationPort
    from volundr.adapters.outbound.git_registry import GitProviderRegistry

logger = logging.getLogger(__name__)


def _sanitize_log(value: object) -> str:
    """Sanitize a value for safe log output (prevent log injection)."""
    return str(value).replace("\n", "\\n").replace("\r", "\\r")


def _is_workflow_child_local_mount(session: Session, workload_config: dict) -> bool:
    """Identify isolated workflow-child workspaces that need no Forge credential."""
    if not isinstance(session.source, LocalMountSource):
        return False
    provenance = workload_config.get("provenance")
    return isinstance(provenance, dict) and isinstance(provenance.get("workflow_execution"), dict)


class SessionNotFoundError(Exception):
    """Raised when a session is not found."""

    def __init__(self, session_id: UUID):
        self.session_id = session_id
        super().__init__(f"Session not found: {session_id}")


class SessionCapacityError(Exception):
    """Raised when the runtime has no free session slot.

    The message carries the numbers and the runtime's own remedy (where the
    limit is raised), because the person who sees it is the one who has to
    decide between stopping a session and raising the cap.
    """

    def __init__(self, capacity: SessionCapacity):
        self.capacity = capacity
        super().__init__(
            f"No session slot is free: {capacity.active} of {capacity.limit} sessions are "
            f"running on this host. Stop or archive a session, or {capacity.remedy}."
        )


class SessionStateError(Exception):
    """Raised when a session operation is invalid for current state."""

    def __init__(self, session_id: UUID, operation: str, current_status: SessionStatus):
        self.session_id = session_id
        self.operation = operation
        self.current_status = current_status
        super().__init__(
            f"Cannot {operation} session {session_id}: current status is {current_status.value}"
        )


class SessionAccessDeniedError(Exception):
    """Raised when a principal lacks permission to access a session."""

    def __init__(self, session_id: UUID, user_id: str):
        self.session_id = session_id
        self.user_id = user_id
        super().__init__(f"Access denied: user {user_id} cannot access session {session_id}")


class RepoValidationError(Exception):
    """Raised when repository validation fails."""

    def __init__(self, repo: str, reason: str):
        self.repo = repo
        self.reason = reason
        super().__init__(f"Repository validation failed for '{repo}': {reason}")


class SessionService:
    """Service for managing coding sessions."""

    def __init__(
        self,
        repository: SessionRepository,
        pod_manager: PodManager,
        git_registry: GitProviderRegistry | None = None,
        validate_repos: bool = True,
        broadcaster: EventBroadcaster | None = None,
        launch_spec_provider: LaunchSpecProvider | None = None,
        authorization: AuthorizationPort | None = None,
        contributors: list[SessionContributor] | None = None,
        provisioning_timeout: float = 300.0,
        provisioning_initial_delay: float = 5.0,
        integration_repo: IntegrationRepository | None = None,
        storage: StoragePort | None = None,
        chronicle_repository: ChronicleRepository | None = None,
        sleipnir_publisher: object | None = None,
        communication_route_repository: CommunicationRouteRepository | None = None,
        session_communication_port: SessionCommunicationPort | None = None,
        attention_notifier: AttentionNotifier | None = None,
        runtime_backend: str = "kubernetes",
        execution_resolver: SessionExecutionResolver | None = None,
        public_origin: str = "http://localhost:8080",
        span_repository: SessionSpanRepository | None = None,
        user_integration: UserIntegrationPort | None = None,
    ):
        self._repository = repository
        self._pod_manager = pod_manager
        self._git_registry = git_registry
        self._user_integration = user_integration
        self._validate_repos = validate_repos
        self._broadcaster = broadcaster
        self._launch_spec_provider = launch_spec_provider
        self._authorization = authorization
        self._contributors = contributors or []
        self._provisioning_timeout = provisioning_timeout
        self._provisioning_initial_delay = provisioning_initial_delay
        self._provisioning_tasks: dict[UUID, asyncio.Task] = {}
        self._activity_stop_tasks: set[asyncio.Task[None]] = set()
        self._attention_notify_tasks: set[asyncio.Task[None]] = set()
        self._attention_notifier = attention_notifier
        self._integration_repo = integration_repo
        self._storage = storage
        self._chronicle_repository = chronicle_repository
        self._sleipnir_publisher = sleipnir_publisher
        self._communication_route_repository = communication_route_repository
        self._session_communication_port = session_communication_port
        self._span_repository = span_repository
        self._runtime_backend = runtime_backend
        self._execution_resolver = execution_resolver
        normalized_public_origin = public_origin.rstrip("/")
        if normalized_public_origin.startswith("https://"):
            self._public_ws_origin = "wss://" + normalized_public_origin.removeprefix("https://")
        elif normalized_public_origin.startswith("http://"):
            self._public_ws_origin = "ws://" + normalized_public_origin.removeprefix("http://")
        else:
            self._public_ws_origin = normalized_public_origin

    async def create_session(
        self,
        name: str,
        model: str,
        source: SessionSource | None = None,
        launch_spec: str | None = None,
        launch_spec_id: UUID | None = None,
        principal: Principal | None = None,
        workspace_id: UUID | None = None,
        tracker_issue_id: str | None = None,
        issue_tracker_url: str | None = None,
        origin: str = "volundr",
        external_session_id: str | None = None,
        coordination: SessionCoordination | None = None,
        session_id: UUID | None = None,
        project_context: str = "",
    ) -> Session:
        """Create a new session.

        Args:
            name: Session name.
            model: Model identifier.
            source: Workspace source (git or local_mount). Defaults to empty GitSource.
            launch_spec: Optional system launch spec name. When provided, its
                repos/model fill in defaults for source and model if not
                explicitly provided.
            launch_spec_id: Optional user launch spec id to associate with the session.
            principal: Authenticated identity. When provided, sets owner_id
                and tenant_id on the session.
            origin: Where the session originated (volundr, claude, codex).
            external_session_id: Native CLI session id for imported sessions.

        Returns:
            Created session.

        Raises:
            RepoValidationError: If repository validation is enabled and fails.
        """
        if source is None:
            source = GitSource()

        # Resolve launch-spec defaults when a launch spec is specified
        if launch_spec and self._launch_spec_provider:
            spec = self._launch_spec_provider.get(launch_spec)
            if spec is not None:
                logger.info("Applying launch spec: %s", _sanitize_log(launch_spec))
                # Use first repo from the spec if caller didn't provide one
                if isinstance(source, GitSource) and not source.repo and spec.repos:
                    first_repo = spec.repos[0]
                    source = GitSource(
                        repo=first_repo.get("url", ""),
                        branch=first_repo.get("branch", source.branch or "main"),
                    )
                # Use model from the spec directly
                if not model and spec.model:
                    model = spec.model

        repo = source.repo if isinstance(source, GitSource) else ""

        logger.info(
            "Creating session: name=%s, model=%s, source_type=%s, repo=%s",
            _sanitize_log(name),
            _sanitize_log(model),
            _sanitize_log(source.type),
            _sanitize_log(repo),
        )
        logger.debug(
            "Session creation config: git_registry=%s, validate_repos=%s",
            "configured" if self._git_registry else "not configured",
            self._validate_repos,
        )

        if isinstance(source, GitSource) and repo and self._validate_repos:
            await self._validate_repository(repo, principal)

        session = Session(
            **({"id": session_id} if session_id else {}),
            coordination=coordination,
            name=name,
            model=model,
            source=source,
            launch_spec_id=launch_spec_id,
            owner_id=principal.user_id if principal else None,
            tenant_id=principal.tenant_id if principal else None,
            workspace_id=workspace_id,
            tracker_issue_id=tracker_issue_id,
            issue_tracker_url=issue_tracker_url,
            origin=origin,
            external_session_id=external_session_id,
            workload_config={"project_context": project_context} if project_context else {},
        )
        await self._check_access(session, principal, "create")
        created = await self._repository.create(session)

        if self._broadcaster is not None:
            await self._broadcaster.publish_session_created(created)

        return created

    async def _validate_repository(self, repo: str, principal: Principal | None = None) -> None:
        """Validate deployments with the owner's integration, as repository browsing does."""
        if (
            self._runtime_backend in {"kubernetes", "openshell"}
            and principal is not None
            and self._user_integration is not None
        ):
            provider = await self._user_integration.find_git_provider_for(repo, principal.user_id)
        elif self._git_registry is not None:
            provider = self._git_registry.get_provider(repo)
        else:
            logger.debug("Skipping repo validation: no git registry configured")
            return

        if provider is None:
            raise RepoValidationError(repo, "no git provider supports this repository URL")
        if not await provider.validate_repo(repo):
            raise RepoValidationError(repo, "repository does not exist or is not accessible")
        logger.info(
            "Repository validation successful for %s (provider: %s)",
            _sanitize_log(repo),
            provider.name,
        )

    async def _check_access(
        self,
        session: Session,
        principal: Principal | None,
        action: str = "read",
    ) -> None:
        """Verify principal has access to the session via AuthorizationPort.

        Configured authorization requires a principal. Internal lifecycle
        operations use private methods after establishing their own preconditions.

        Raises:
            SessionAccessDeniedError: If the principal lacks permission.
        """
        if self._authorization is None:
            return
        if principal is None:
            raise SessionAccessDeniedError(session.id, "unauthenticated")

        resource = Resource(
            kind="session",
            id=str(session.id),
            attr={
                "owner_id": session.owner_id,
                "tenant_id": session.tenant_id,
            },
        )

        if not await self._authorization.is_allowed(principal, action, resource):
            raise SessionAccessDeniedError(session.id, principal.user_id)

    async def get_session(self, session_id: UUID) -> Session | None:
        """Get a session by ID."""
        return await self._repository.get(session_id)

    async def get_many_sessions(self, session_ids: list[UUID]) -> dict[UUID, Session]:
        """Batch-fetch sessions by ID. Returns only the ones that exist."""
        return await self._repository.get_many(session_ids)

    async def with_read_states(
        self, sessions: list[Session], principal: Principal | None
    ) -> list[Session]:
        if principal is None or not sessions:
            return sessions
        states = await self._repository.get_read_states([s.id for s in sessions], principal.user_id)
        return [s.model_copy(update={"read_state": states.get(s.id)}) for s in sessions]

    async def get_read_state(self, session_id: UUID, principal: Principal) -> SessionReadState:
        session = await self._repository.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        await self._check_access(session, principal, "read")
        states = await self._repository.get_read_states([session_id], principal.user_id)
        if session_id not in states:
            raise SessionNotFoundError(session_id)
        return states[session_id]

    async def change_read_state(
        self,
        session_id: UUID,
        change: SessionReadStateChange,
        principal: Principal,
    ) -> SessionReadState:
        session = await self._repository.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        await self._check_access(session, principal, "read")
        state = await self._repository.change_read_state(session_id, principal.user_id, change)
        await self.notify_read_state_changed(session_id, reader_id=principal.user_id)
        return state

    async def notify_read_state_changed(
        self, session_id: UUID, *, reader_id: str | None = None
    ) -> None:
        if self._broadcaster is None:
            return
        session = await self._repository.get(session_id)
        if session is None:
            return
        # Hint only: no reader's private marker is exposed to another reader. Consumers
        # re-read the authorized projection. Finals use the owner-scoped fleet route.
        await self._broadcaster.publish(
            RealtimeEvent(
                type=EventType.SESSION_READ_STATE,
                data={
                    "session_id": str(session_id),
                    "owner_id": reader_id or session.owner_id or "",
                },
                timestamp=datetime.now(UTC),
            )
        )

    async def update_activity(
        self,
        session_id: UUID,
        state: SessionActivityState,
        metadata: dict,
        state_since: datetime | None = None,
        turn_started_at: datetime | None = None,
    ) -> Session:
        """Update a session's activity state and broadcast an SSE event.

        ``state_since`` is the broker-stamped UTC timestamp of when the session
        entered ``state`` (None for older brokers that don't report it). It is
        persisted and re-broadcast so clients can render an accurate elapsed
        time without re-deriving it from event arrival.

        ``turn_started_at`` is the broker-stamped UTC timestamp of when the
        CURRENT turn started (the prompt instant), stable across intra-turn
        state flips; None when no turn is in flight OR the broker is too old to
        report it. An unstamped heartbeat retains the known current turn;
        idle/stopped/error always clear it. No artificial turn start is invented;
        clients use ``state_since`` when the turn start is unknown.

        Raises SessionNotFoundError if the session doesn't exist.
        """
        session = await self._repository.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)

        # The broker rides the CLI/agent conversation id on activity reports.
        # Persist it as a first-class field (so the session can be resumed after
        # a stop) and keep it OUT of activity_metadata, which is clobbered below.
        cli_session_id = metadata.pop("cli_session_id", None)
        if cli_session_id and cli_session_id != session.cli_session_id:
            session.cli_session_id = cli_session_id

        # Capture the prior attention context BEFORE we overwrite it, so we can
        # tell a fresh "needs the user" transition from a repeated report.
        previous_state = session.activity_state
        previous_request_id = (session.activity_metadata or {}).get("request_id")

        # Ignore delayed reports before mutating any fields or emitting an SSE event.
        if (
            state_since
            and session.activity_state_since
            and state_since < session.activity_state_since
        ):
            return session
        same_bucket = previous_state == state or (
            previous_state is not None and previous_state.is_busy and state.is_busy
        )
        previous_since = session.activity_state_since
        previous_turn = session.turn_started_at
        session.activity_state = state
        # The broker stamps state_since only on a real change; fall back to "now"
        # when an older broker omits it so the field is never null for a live
        # session that just transitioned.
        session.activity_state_since = (
            state_since or (previous_since if same_bucket else None) or datetime.now(UTC)
        )
        # Preserve a known anchor on old-broker heartbeats, clear it on turn end.
        session.turn_started_at = (
            None
            if state
            in (SessionActivityState.IDLE, SessionActivityState.STOPPED, SessionActivityState.ERROR)
            else turn_started_at or (previous_turn if same_bucket else None)
        )
        session.activity_metadata = metadata
        if state is SessionActivityState.ERROR:
            message = str(metadata.get("error") or metadata.get("message") or "").strip()
            session.error = message or "Workflow agent failed"
        # Treat each activity report as a liveness heartbeat so the reconciler can
        # tell a live-but-idle session from one whose broker has died.
        session.last_active = datetime.now(UTC)
        # A heartbeat is PROOF OF LIFE — a lingering liveness verdict is now
        # demonstrably false, so clear it (only liveness errors: real failure
        # records from other paths must stay visible).
        if session.error and session.error.startswith("liveness:"):
            session.error = None
        updated = await self._repository.update(session)
        # A concurrent newer activity report may have won the database comparison.
        # Never publish the stale caller's state/timing pair over that winner.
        if (updated.activity_state, updated.activity_state_since) != (
            state,
            session.activity_state_since,
        ):
            return updated

        is_new_attention = self._is_new_attention_request(
            state, previous_state, metadata, previous_request_id
        )

        if self._broadcaster is not None:
            await self._broadcaster.publish(
                RealtimeEvent(
                    type=EventType.SESSION_ACTIVITY,
                    data={
                        "session_id": str(session_id),
                        "state": state.value,
                        "activity_state_since": (
                            updated.activity_state_since.isoformat()
                            if updated.activity_state_since
                            else None
                        ),
                        "turn_started_at": (
                            updated.turn_started_at.isoformat() if updated.turn_started_at else None
                        ),
                        "metadata": metadata,
                        "owner_id": session.owner_id or "",
                        "tenant_id": session.tenant_id or "",
                    },
                    timestamp=updated.updated_at,
                )
            )
            # A fresh "needs the user" transition (or a new pending request while
            # already awaiting) fires a dedicated, high-urgency event. Unlike the
            # routine activity event above, this one is forwarded to the platform
            # bus so a notification / push fan-out can alert the owner.
            if is_new_attention:
                await self._broadcaster.publish(
                    RealtimeEvent(
                        type=EventType.SESSION_NEEDS_INPUT,
                        data={
                            "session_id": str(session_id),
                            "session_name": updated.name,
                            "owner_id": session.owner_id or "",
                            "tenant_id": session.tenant_id or "",
                            "kind": metadata.get("kind", "question"),
                            "prompt": metadata.get("prompt", "") or "",
                            "request_id": metadata.get("request_id", "") or "",
                        },
                        timestamp=updated.updated_at,
                    )
                )

        # Fan a push out to the owner's devices off the activity hot-path (a slow
        # APNs/webhook call must not delay Skuld's activity report response).
        if is_new_attention and self._attention_notifier is not None:
            self._schedule_attention_notify(updated, metadata)

        if self._should_auto_stop_after_activity(updated, state, metadata):
            self._schedule_activity_stop(updated.id)
        return updated

    def _schedule_attention_notify(self, session: Session, metadata: dict) -> None:
        """Dispatch a needs-input push in the background, tracking the task."""
        task = asyncio.create_task(
            self._attention_notifier.notify_needs_input(
                session,
                kind=metadata.get("kind", "question"),
                prompt=metadata.get("prompt", "") or "",
                request_id=metadata.get("request_id", "") or "",
            ),
            name=f"attention-notify-{session.id}",
        )
        self._attention_notify_tasks.add(task)
        task.add_done_callback(self._attention_notify_tasks.discard)

    @staticmethod
    def _is_new_attention_request(
        state: SessionActivityState,
        previous_state: SessionActivityState | None,
        metadata: dict,
        previous_request_id: str | None,
    ) -> bool:
        """True when a report represents a NEW request for the user's attention.

        Fires on the transition into ``awaiting_input``, and again if a new
        pending request (different ``request_id``) arrives while the session is
        still awaiting — so a second question is not swallowed. Re-reports of the
        same pending request, and Skuld's periodic heartbeats, do not re-fire,
        avoiding notification spam.
        """
        if state is not SessionActivityState.AWAITING_INPUT:
            return False
        if metadata.get("heartbeat"):
            return False
        if previous_state is not SessionActivityState.AWAITING_INPUT:
            return True
        return metadata.get("request_id") != previous_request_id

    async def reconcile_liveness(
        self,
        stale_after_seconds: int,
        *,
        exempt_workload_types: list[str] | None = None,
    ) -> int:
        """Mark running sessions with no recent activity heartbeat as stopped.

        A session whose broker has died otherwise sits in ``running`` forever
        with a stale ``chat_endpoint``; clients then open a socket to a tombstone
        and see nothing. Reconciling clears the endpoint and flips the status to
        ``stopped`` (resumable) so the list reflects reality.

        Workload types in *exempt_workload_types* (long-lived workloads that
        idle by design) are never heartbeat-reaped; the pod-status reconcile
        loop remains their authoritative death check.

        Returns the number of sessions reconciled.
        """
        exempt = set(exempt_workload_types or [])
        threshold = datetime.now(UTC) - timedelta(seconds=stale_after_seconds)
        stale = await self._repository.list_stale_running(threshold)
        reconciled = 0
        for session in stale:
            if session.workload_type in exempt:
                continue
            stopped = session.model_copy(
                update={
                    "status": SessionStatus.STOPPED,
                    "chat_endpoint": None,
                    "code_endpoint": None,
                    "error": "liveness: no activity heartbeat — broker presumed dead",
                    "updated_at": datetime.now(UTC),
                }
            )
            result = await self._repository.update(stopped)
            reconciled += 1
            logger.warning(
                "Liveness: marked stale running session %s as stopped (last_active=%s)",
                session.id,
                session.last_active,
            )
            if self._broadcaster is not None:
                await self._broadcaster.publish_session_updated(result)
        return reconciled

    def _should_auto_stop_after_activity(
        self,
        session: Session,
        state: SessionActivityState,
        metadata: dict,
    ) -> bool:
        """Return True when an activity report authoritatively completes a flock session."""
        if session.status != SessionStatus.RUNNING:
            return False
        if session.workload_type != "ravn_flock":
            return False
        if state != SessionActivityState.IDLE:
            return False
        return metadata.get("completion_source") == "ravn_flock"

    def _schedule_activity_stop(self, session_id: UUID) -> None:
        """Stop a completed flock session in the background after activity broadcast."""
        task = asyncio.create_task(
            self._auto_stop_completed_flock_session(session_id),
            name=f"auto-stop-{session_id}",
        )
        self._activity_stop_tasks.add(task)
        task.add_done_callback(self._on_activity_stop_done)

    def _on_activity_stop_done(self, task: asyncio.Task[None]) -> None:
        self._activity_stop_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("Auto-stop after flock completion failed: %s", exc, exc_info=exc)

    async def _auto_stop_completed_flock_session(self, session_id: UUID) -> None:
        """Stop a flock session that has reported an authoritative terminal outcome."""
        try:
            session = await self._repository.get(session_id)
            if session is None:
                raise SessionNotFoundError(session_id)
            await self._stop_session(session, None)
        except SessionNotFoundError:
            logger.debug(
                "Skipping auto-stop for completed flock session %s because it no longer exists",
                session_id,
            )
        except SessionStateError:
            logger.debug(
                "Skipping auto-stop for completed flock session %s because it is no longer "
                "stoppable",
                session_id,
            )

    async def list_sessions(
        self,
        status: SessionStatus | None = None,
        include_archived: bool = False,
        principal: Principal | None = None,
    ) -> list[Session]:
        """List sessions, excluding archived by default.

        Args:
            status: Optional status filter. When set, only sessions with
                this status are returned (overrides include_archived).
            include_archived: When True and status is None, archived
                sessions are included in the results.
            principal: Authenticated identity. When provided, scopes results
                to the principal's tenant. Non-admin users see only their own
                sessions.
        """
        tenant_id, owner_id = self.visibility_scope(principal)

        sessions = await self._repository.list(
            status=status,
            tenant_id=tenant_id,
            owner_id=owner_id,
        )
        if status is None and not include_archived:
            sessions = [s for s in sessions if s.status != SessionStatus.ARCHIVED]
        if principal is not None and self._authorization is not None:
            allowed = await self._authorization.filter_allowed(
                principal,
                "list",
                [
                    Resource(
                        kind="session",
                        id=str(s.id),
                        attr={"owner_id": s.owner_id, "tenant_id": s.tenant_id},
                    )
                    for s in sessions
                ],
            )
            allowed_ids = {r.id for r in allowed if r.kind == "session"}
            sessions = [s for s in sessions if str(s.id) in allowed_ids]
        return sessions

    def visibility_scope(self, principal: Principal | None) -> tuple[str | None, str | None]:
        """Return the ``(tenant_id, owner_id)`` bounds of what *principal* may list.

        ``None`` means unbounded. Every principal is bounded to its own tenant;
        only a tenant admin sees other owners' sessions in that tenant. With
        authorization configured, an absent principal is refused, never unbounded.

        Raises:
            PermissionError: Authorization is configured and no principal was given.
        """
        if self._authorization is not None and principal is None:
            raise PermissionError("An authenticated principal is required to list sessions")
        if principal is None:
            return None, None
        if TenantRole.ADMIN in principal.roles:
            return principal.tenant_id, None
        return principal.tenant_id, principal.user_id

    @staticmethod
    def within_scope(
        scope: tuple[str | None, str | None],
        *,
        owner_id: str | None,
        tenant_id: str | None,
    ) -> bool:
        """Return whether an owner/tenant attribution lies inside a ``visibility_scope``.

        Missing owner or tenant never widens visibility: an unowned resource is
        inside only an owner-unbounded (tenant admin) scope, and an untenanted
        one inside no bounded scope.
        """
        tenant_scope, owner_scope = scope
        if tenant_scope is not None and (tenant_id or None) != tenant_scope:
            return False
        if owner_scope is not None and (owner_id or None) != owner_scope:
            return False
        return True

    @staticmethod
    def attributed_resource(
        resource_id: str,
        *,
        owner_id: str | None,
        tenant_id: str | None,
        room_viewers: Iterable[str] = (),
        room_approvers: Iterable[str] = (),
    ) -> Resource:
        """Describe a session, or history attributed to one, to the authorization policy.

        ``room_viewers``/``room_approvers`` are the Cedar Set attributes
        computed from ACTIVE, unexpired ``session_participants`` grants (see
        ``SessionParticipantService.active_grants``). They are empty by
        default: every existing call site (read/update/delete/list,
        chronicle attribution) never populates them, and the room-scoped
        actions (``read_room``/``attach``/``resolve_gate``) are the only
        Cedar actions whose policies reference them, so leaving them empty
        never changes what those existing call sites can already do. There is
        no separate room_speakers set: every active participant may speak,
        so "viewer" already covers it — see RoomGrants' docstring.
        """
        return Resource(
            kind="session",
            id=resource_id,
            attr={
                "owner_id": owner_id or None,
                "tenant_id": tenant_id or None,
                "room_viewers": list(room_viewers),
                "room_approvers": list(room_approvers),
            },
        )

    async def authorizes(
        self, principal: Principal | None, action: str, resource: Resource
    ) -> bool:
        """Return whether the configured authorization lets *principal* do *action*.

        Without authorization every action is allowed. With authorization
        configured, an absent principal is refused, never allowed.

        Raises:
            PermissionError: Authorization is configured and no principal was given.
        """
        if self._authorization is None:
            return True
        if principal is None:
            raise PermissionError("An authenticated principal is required")
        return await self._authorization.is_allowed(principal, action, resource)

    async def filter_authorized(
        self,
        principal: Principal | None,
        action: str,
        resources: list[Resource],
    ) -> list[Resource]:
        """Return the *resources* the configured authorization lets *principal* act on.

        Raises:
            PermissionError: Authorization is configured and no principal was given.
        """
        if self._authorization is None:
            return resources
        if principal is None:
            raise PermissionError("An authenticated principal is required")
        return await self._authorization.filter_allowed(principal, action, resources)

    async def get_authorized_session(
        self, session_id: UUID, principal: Principal | None, action: str
    ) -> Session:
        """Return a session inside *principal*'s visibility scope that it may do *action* on.

        Raises:
            PermissionError: Authorization is configured and no principal was given.
            SessionNotFoundError: No such session, or it is outside the scope.
            SessionAccessDeniedError: The policy denies *action*.
        """
        scope = self.visibility_scope(principal)
        session = await self._repository.get(session_id)
        if session is None or not self.within_scope(
            scope, owner_id=session.owner_id, tenant_id=session.tenant_id
        ):
            raise SessionNotFoundError(session_id)
        resource = self.attributed_resource(
            str(session.id), owner_id=session.owner_id, tenant_id=session.tenant_id
        )
        if not await self.authorizes(principal, action, resource):
            user_id = principal.user_id if principal is not None else "unauthenticated"
            raise SessionAccessDeniedError(session.id, user_id)
        return session

    async def may_observe(
        self,
        principal: Principal | None,
        *,
        session_id: str,
        owner_id: str | None,
        tenant_id: str | None,
    ) -> bool:
        """Return whether ``list_sessions`` would show this session to *principal*.

        The realtime event stream uses this so a subscriber receives events for
        exactly the sessions the list endpoint shows it. Missing owner or tenant
        never widens visibility: an unowned session is visible only to a tenant
        admin, and an untenanted one to no bounded principal.
        """
        scope = self.visibility_scope(principal)
        if not self.within_scope(scope, owner_id=owner_id, tenant_id=tenant_id):
            return False
        return await self.authorizes(
            principal,
            "list",
            self.attributed_resource(session_id, owner_id=owner_id, tenant_id=tenant_id),
        )

    async def update_session(
        self,
        session_id: UUID,
        name: str | None = None,
        model: str | None = None,
        branch: str | None = None,
        tracker_issue_id: str | None = None,
        principal: Principal | None = None,
    ) -> Session:
        """Update a session's name, model, branch, and/or tracker issue."""
        session = await self._repository.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)

        await self._check_access(session, principal, "update")

        updates: dict = {"updated_at": Session.model_fields["updated_at"].default_factory()}
        if name is not None:
            updates["name"] = name
        if model is not None:
            updates["model"] = model
        if branch is not None and isinstance(session.source, GitSource):
            updates["source"] = session.source.model_copy(update={"branch": branch})
        if tracker_issue_id is not None:
            updates["tracker_issue_id"] = tracker_issue_id

        updated = session.model_copy(update=updates)
        result = await self._repository.update(updated)

        if self._broadcaster is not None:
            await self._broadcaster.publish_session_updated(result)

        return result

    async def update_coordination(
        self, session: Session, coordination: SessionCoordination, principal: Principal | None
    ) -> Session:
        """Persist a validated project assignment without touching its runtime."""
        from volundr.domain.project_ports import ProjectConflictError

        await self._check_access(session, principal, "update")
        result = await self._repository.update_coordination(session, coordination)
        if result is None:
            raise ProjectConflictError("Session project changed; reload before assigning")
        if self._broadcaster is not None:
            await self._broadcaster.publish_session_updated(result)
        return result

    async def delete_session(
        self,
        session_id: UUID,
        principal: Principal | None = None,
        cleanup_targets: list[CleanupTarget] | None = None,
    ) -> bool:
        """Delete a session.

        If the session is running, attempts to stop its pods first. Pod stop
        failures are logged but do not prevent session deletion, since the
        primary goal is to clean up the session record.

        Session-scoped workspace storage is always removed. Optional
        *cleanup_targets* lists additional resources to permanently remove
        (e.g. chronicles).
        """
        session = await self._repository.get(session_id)
        if session is None:
            return False

        await self._check_access(session, principal, "delete")

        targets = {CleanupTarget.WORKSPACE_STORAGE, *(cleanup_targets or [])}

        # Cancel provisioning task if active
        self._cancel_provisioning_task(session_id)

        cleanup_context = None
        if self._contributors:
            cleanup_context = SessionContext(
                principal=principal,
                **await self._execution_context(session),
            )

        durable_compute = self._runtime_backend == "vm" or self._execution_resolver is not None

        try:
            stopped = await self._pod_manager.stop(session)
            if durable_compute and not stopped:
                raise RuntimeError("Compute infrastructure deletion was not confirmed")
        except Exception as e:
            if durable_compute:
                raise
            logger.warning(
                "Failed to stop infrastructure for session %s during deletion: %s. "
                "Proceeding with session deletion.",
                _sanitize_log(session_id),
                _sanitize_log(e),
            )

        # Run contributor cleanup in reverse order
        await self._run_cleanup(session, principal, context=cleanup_context)

        deleted = await self._repository.delete(session_id)

        # Run optional resource cleanup after session record is gone
        if deleted:
            if self._span_repository is not None:
                await self._span_repository.delete_by_session(session_id)
            await self._run_targeted_cleanup(session_id, targets)

        if deleted and self._broadcaster is not None:
            await self._broadcaster.publish_session_deleted(
                session_id,
                owner_id=session.owner_id,
                tenant_id=session.tenant_id,
            )

        return deleted

    async def _run_targeted_cleanup(
        self,
        session_id: UUID,
        targets: set[CleanupTarget],
    ) -> None:
        """Run user-selected resource cleanup after session deletion.

        Each target is handled independently; failures are logged but do not
        block other cleanup actions.
        """
        if not targets:
            return

        if CleanupTarget.WORKSPACE_STORAGE in targets:
            await self._cleanup_workspace_storage(session_id)

        if CleanupTarget.CHRONICLES in targets:
            await self._cleanup_chronicles(session_id)

    async def _cleanup_workspace_storage(self, session_id: UUID) -> None:
        if self._storage is None:
            logger.warning(
                "Workspace storage cleanup requested for session %s but no storage port configured",
                _sanitize_log(session_id),
            )
            return
        try:
            await self._storage.delete_workspace(str(session_id))
            logger.info("Deleted workspace PVC for session %s", _sanitize_log(session_id))
        except Exception:
            logger.warning(
                "Failed to delete workspace PVC for session %s",
                _sanitize_log(session_id),
                exc_info=True,
            )

    async def _cleanup_chronicles(self, session_id: UUID) -> None:
        if self._chronicle_repository is None:
            logger.warning(
                "Chronicle cleanup requested for session %s but no chronicle repository configured",
                _sanitize_log(session_id),
            )
            return
        try:
            chronicle = await self._chronicle_repository.get_by_session(session_id)
            if chronicle is not None:
                await self._chronicle_repository.delete(chronicle.id)
                logger.info(
                    "Deleted chronicle %s for session %s",
                    _sanitize_log(chronicle.id),
                    _sanitize_log(session_id),
                )
        except Exception:
            logger.warning(
                "Failed to delete chronicles for session %s",
                _sanitize_log(session_id),
                exc_info=True,
            )

    async def ensure_capacity(self, session: Session | None = None) -> None:
        """Raise SessionCapacityError when the runtime has no free slot.

        Runtimes without a fixed cap report no capacity and are never refused.
        """
        capacity = (
            await self._pod_manager.capacity_for(session)
            if session is not None
            else await self._pod_manager.capacity()
        )
        if capacity is None or capacity.available > 0:
            return
        raise SessionCapacityError(capacity)

    async def _resolve_execution_config(
        self, session: Session, workload_config: dict, launch_spec: str | None
    ) -> dict:
        """Retain an existing pin, or resolve once before any runtime side effects."""
        config = dict(workload_config)
        pinned = session.workload_config.get("_compute_execution")
        if pinned is not None:
            config["_compute_execution"] = pinned
        elif (
            not config.get("execution_profile")
            and not config.get("compute_profile")
            and not await self._execution_resolver.has_legacy_allocation(session)
        ):
            if self._launch_spec_provider is not None:
                selected = (
                    self._launch_spec_provider.get(launch_spec)
                    if launch_spec
                    else self._launch_spec_provider.get_default("session")
                )
                if launch_spec and selected is None:
                    raise ExecutionSelectionError("Selected launch spec is not configured")
                if selected is not None:
                    for key in ("execution_profile", "compute_profile"):
                        if selected.workload_config.get(key):
                            config[key] = selected.workload_config[key]
        selected_session = session.model_copy(update={"workload_config": config})
        plan = await self._execution_resolver.resolve_execution(selected_session)
        if plan is not None:
            config["_compute_execution"] = self._execution_resolver.execution_reference(plan)
        return config

    async def _execution_context(self, session: Session) -> dict:
        if "_compute_execution" not in session.workload_config:
            return {"runtime_backend": self._runtime_backend}
        if self._execution_resolver is None:
            raise ExecutionCatalogError("Pinned compute execution requires its configured catalog")
        plan = await self._execution_resolver.execution_for(session)
        return {
            "runtime_backend": plan.runtime.contributor_backend,
            "storage_backend": plan.runtime.storage_mode,
            "runtime_capabilities": plan.runtime.capabilities,
        }

    async def start_session(
        self,
        session_id: UUID,
        definition: str | None = None,
        launch_spec: str | None = None,
        principal: Principal | None = None,
        terminal_restricted: bool = False,
        credential_names: list[str] | None = None,
        integration_ids: list[str] | None = None,
        resource_config: dict | None = None,
        system_prompt: str = "",
        initial_prompt: str = "",
        workload_type: str = "session",
        workload_config: dict | None = None,
    ) -> Session:
        """Start a session — returns immediately, provisions in background.

        Matches the Go CLI pattern: HTTP response returns with status
        "starting" before any git clone or process spawn happens.
        The background task transitions through provisioning → running.
        """
        session = await self._repository.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)

        await self._check_access(session, principal, "start")

        if not session.can_start():
            raise SessionStateError(session_id, "start", session.status)

        if workload_config is not None and "_compute_execution" in workload_config:
            raise ExecutionSelectionError(
                "Compute execution references are managed by the session service"
            )

        # Restart parity: persist the definition the first time it is supplied and
        # reuse the stored one on later restarts, so a session keeps its transport
        # (e.g. Grok ACP) instead of falling back to the platform default.
        definition = definition or session.session_definition

        # Restart parity for workload identity: the REST start endpoint calls us
        # with the DEFAULT workload_type ("session") and no config, so a naive
        # copy would demote a special workload (e.g. a ravn flock) to a plain
        # CLI session on every restart — vanishing from the fleet. Fall back to
        # the stored values whenever the caller did not explicitly override
        # them, and thread them through provisioning so the contributor
        # pipeline re-applies the workload spec.
        if workload_type == "session" and session.workload_type != "session":
            workload_type = session.workload_type
        if not workload_config and session.workload_config:
            workload_config = dict(session.workload_config)
        if self._execution_resolver is not None:
            workload_config = await self._resolve_execution_config(
                session, workload_config or {}, launch_spec
            )
        elif (workload_config or {}).get("execution_profile") or "_compute_execution" in (
            workload_config or {}
        ):
            raise ExecutionSelectionError(
                "Compute execution selection requires a configured catalog"
            )
        if workload_type == "ravn_flock" and not initial_prompt:
            initial_prompt = str((workload_config or {}).get("initiative_context") or "")

        if not integration_ids:
            integration_ids = list((workload_config or {}).get("integration_ids") or [])
        if integration_ids:
            workload_config = {**(workload_config or {}), "integration_ids": integration_ids}

        # A project briefing is a persisted snapshot, including on restart. It is
        # separate from repository-owned AGENTS.md / CLAUDE.md files.
        project_context = session.workload_config.get("project_context", "")
        if project_context:
            workload_config = {**(workload_config or {}), "project_context": project_context}

        # Set chat_endpoint eagerly — Flux/Gateway sessions know their public
        # route before the pod is ready; local mode falls back to the root proxy.
        # Refuse here, before the session flips to STARTING, so the caller
        # gets the answer instead of a session that fails a moment later.
        await self.ensure_capacity(session)

        chat_endpoint = self._pod_manager.initial_chat_endpoint(session)
        if not chat_endpoint:
            chat_endpoint = f"{self._public_ws_origin}/s/{session_id}/session"

        starting = session.model_copy(
            update={
                "status": SessionStatus.STARTING,
                "session_definition": definition,
                "chat_endpoint": chat_endpoint,
                "code_endpoint": None,
                # A restart is an explicit "bring it back": stale failure
                # detail (e.g. the liveness reaper's "broker presumed dead")
                # must not survive onto the healthy relaunched session — the
                # clients render `error` as a Session-error banner verbatim.
                "error": None,
                "updated_at": datetime.now(UTC),
                "workload_type": workload_type,
                # Persist the workload config so workload identity (e.g. a
                # flock's personas) survives restarts.
                "workload_config": workload_config or {},
            }
        )
        await self._repository.update(starting)

        if self._broadcaster is not None:
            await self._broadcaster.publish_session_updated(starting)

        # Launch provisioning in background — don't block the HTTP response
        task = asyncio.create_task(
            self._provision_background(
                starting,
                principal=principal,
                definition=definition,
                launch_spec=launch_spec,
                terminal_restricted=terminal_restricted,
                credential_names=credential_names,
                integration_ids=integration_ids,
                resource_config=resource_config,
                system_prompt=system_prompt,
                initial_prompt=initial_prompt,
                workload_type=workload_type,
                workload_config=workload_config,
            ),
            name=f"provision-{session_id}",
        )
        self._provisioning_tasks[session_id] = task
        task.add_done_callback(lambda t: self._provisioning_tasks.pop(session_id, None))

        return starting

    async def _provision_background(
        self,
        session: Session,
        principal: Principal | None = None,
        definition: str | None = None,
        launch_spec: str | None = None,
        terminal_restricted: bool = False,
        credential_names: list[str] | None = None,
        integration_ids: list[str] | None = None,
        resource_config: dict | None = None,
        system_prompt: str = "",
        initial_prompt: str = "",
        workload_type: str = "session",
        workload_config: dict | None = None,
    ) -> None:
        """Background task: run contributor pipeline, start pods, update status."""
        try:
            result = await self._start_with_pipeline(
                session,
                principal,
                definition,
                launch_spec,
                terminal_restricted,
                credential_names=credential_names,
                integration_ids=integration_ids,
                resource_config=resource_config,
                system_prompt=system_prompt,
                initial_prompt=initial_prompt,
                workload_type=workload_type,
                workload_config=workload_config,
            )

            # Contributors may have persisted launch configuration. Keep it
            # when transitioning from starting to provisioning.
            session = await self._repository.get(session.id)
            if session is None:
                return
            provisioning = (
                session.with_status(SessionStatus.PROVISIONING)
                .with_endpoints(
                    result.chat_endpoint or session.chat_endpoint,
                    result.code_endpoint,
                )
                .with_pod_name(result.pod_name)
            )
            final = await self._repository.update(provisioning)

            if self._broadcaster is not None:
                await self._broadcaster.publish_session_updated(final)

            # Launch readiness poller
            poll_task = asyncio.create_task(self._poll_readiness(final))
            self._provisioning_tasks[final.id] = poll_task
            poll_task.add_done_callback(lambda t: self._provisioning_tasks.pop(final.id, None))

        except Exception as e:
            error = str(e).strip()
            if not error:
                error = (
                    "Provisioning timed out"
                    if isinstance(e, TimeoutError)
                    else "Provisioning failed"
                )
            logger.error("Provisioning failed for session %s: %s", session.id, error)
            # A failed start may already own partially-created infrastructure.
            # Stop it before publishing the terminal session verdict so pending
            # provider requests do not remain bound to a failed session forever.
            #
            # A cleanup failure here must never vanish (no-fallbacks): for a
            # durable-compute backend (VM, or any backend behind an execution
            # resolver) the machine keeps running and capacity keeps being
            # charged if we merely warn-and-continue as before. Both failures
            # are recorded on the session so an operator sees the full story,
            # and reconcile_active_sessions() now also sweeps FAILED sessions
            # for durable-compute backends (previously kubernetes-only) so the
            # leaked infrastructure gets a retry on the next reconcile pass
            # instead of being excluded from the sweep forever.
            cleanup_error: str | None = None
            try:
                await self._pod_manager.stop(session)
            except Exception as cleanup_exc:
                cleanup_error = str(cleanup_exc).strip() or repr(cleanup_exc)
                logger.error(
                    "Failed to clean up infrastructure after provisioning failure for "
                    "session %s; infrastructure may still be running and will be retried "
                    "by the next reconcile sweep",
                    _sanitize_log(session.id),
                    exc_info=True,
                )
            session = await self._repository.get(session.id)
            if session is None:
                return
            if cleanup_error is not None:
                error = f"{error}; cleanup after provisioning failure also failed: {cleanup_error}"
            failed = session.with_status(SessionStatus.FAILED).with_error(error)
            await self._repository.update(failed)

            if self._broadcaster is not None:
                await self._broadcaster.publish_session_updated(failed)

    async def _start_with_pipeline(
        self,
        session: Session,
        principal: Principal | None,
        definition: str | None,
        launch_spec: str | None,
        terminal_restricted: bool,
        credential_names: list[str] | None = None,
        integration_ids: list[str] | None = None,
        resource_config: dict | None = None,
        system_prompt: str = "",
        initial_prompt: str = "",
        workload_type: str = "session",
        workload_config: dict | None = None,
    ):
        """Run the contributor pipeline and start pods with merged spec."""
        workload_config = {**session.workload_config, **(workload_config or {})}
        # A caller cannot change the internally persisted execution during contribution.
        if "_compute_execution" in session.workload_config:
            workload_config["_compute_execution"] = session.workload_config["_compute_execution"]
        # Auto-include all enabled integrations when none are specified.
        # Keep the fetched connections so contributors don't re-fetch by ID.
        resolved_connections: list[IntegrationConnection] = []
        if integration_ids:
            # Caller specified IDs — fetch them in bulk
            if self._integration_repo:
                fetched = await asyncio.gather(
                    *(self._integration_repo.get_connection(cid) for cid in integration_ids),
                )
                if any(c is None or not c.enabled for c in fetched):
                    raise ValueError("Selected integration connection is missing or disabled")
                resolved_connections = list(fetched)
                if principal and any(
                    connection.owner_id != principal.user_id for connection in resolved_connections
                ):
                    raise ValueError("Integration connection not found")
        elif principal and self._integration_repo:
            all_connections = await self._integration_repo.list_connections(
                principal.user_id,
            )
            resolved_connections = [c for c in all_connections if c.enabled]

        if integration_ids and self._integration_repo is None:
            raise ValueError("Selected integrations require a configured integration repository")
        # A saved selection can predate the owner's Git integration. Preserve
        # explicit source-control choices; otherwise attach enabled sources.
        if (
            session.repo
            and principal
            and self._integration_repo
            and integration_ids
            and not any(
                c.integration_type == IntegrationType.SOURCE_CONTROL for c in resolved_connections
            )
        ):
            source_connections = await self._integration_repo.list_connections(
                principal.user_id,
                integration_type=IntegrationType.SOURCE_CONTROL,
            )
            resolved_connections.extend(c for c in source_connections if c.enabled)

        workflow_child_local_mount = _is_workflow_child_local_mount(session, workload_config)
        if workflow_child_local_mount:
            resolved_connections = [
                connection
                for connection in resolved_connections
                if connection.integration_type != IntegrationType.SOURCE_CONTROL
            ]

        if resolved_connections or workflow_child_local_mount:
            workload_config = {
                **(workload_config or {}),
                "integration_ids": [c.id for c in resolved_connections],
            }
            session = session.model_copy(update={"workload_config": workload_config})
            await self._repository.update(session)

        context = SessionContext(
            principal=principal,
            definition=definition,
            launch_spec=launch_spec,
            **await self._execution_context(session),
            terminal_restricted=terminal_restricted,
            credential_names=tuple(credential_names or ()),
            integration_ids=tuple(c.id for c in resolved_connections),
            integration_connections=tuple(resolved_connections),
            resource_config=resource_config or {},
            system_prompt=system_prompt,
            initial_prompt=initial_prompt,
            workload_type=workload_type,
            workload_config=workload_config or {},
        )

        contributions: list[SessionContribution] = []
        for contributor in self._contributors:
            contribution = await contributor.contribute(session, context)
            contributions.append(contribution)

        spec = SessionSpec.merge(contributions)
        if session.coordination and (
            project_context := session.workload_config.get("project_context")
        ):
            # Append after persona/launch-spec resolution. Supplying a project
            # brief as an ad-hoc system prompt would overwrite those instructions.
            session_values = spec.values.setdefault("session", {})
            existing_prompt = session_values.get("systemPrompt", "")
            session_values["systemPrompt"] = "\n\n".join(
                part for part in (existing_prompt, project_context) if part
            )
        self._overlay_resume_session(session, spec)
        return await self._pod_manager.start(session, spec=spec)

    @staticmethod
    def _overlay_resume_session(session: Session, spec: SessionSpec) -> None:
        """Seed the broker with a conversation id to resume, when one exists.

        Imported sessions carry the harness's own session/thread id
        (``external_session_id``) and must be pinned to a resume-capable
        transport for their origin. Volundr-born sessions carry the
        ``cli_session_id`` captured from broker activity reports — restarting
        them reloads the prior conversation on whatever transport their
        definition selects (SDK, persistent subprocess, and Codex WebSocket
        all support resume).
        """
        if session.external_session_id:
            broker = spec.values.setdefault("broker", {})
            broker["resumeSessionId"] = session.external_session_id
            if session.origin == "claude":
                broker["cliType"] = "claude"
                broker["transportAdapter"] = (
                    "skuld.transports.persistent_subprocess.PersistentSubprocessTransport"
                )
            if session.origin == "codex":
                broker["cliType"] = "codex-ws"
                broker["transportAdapter"] = "skuld.transports.codex_ws.CodexWebSocketTransport"
            return

        if session.cli_session_id:
            broker = spec.values.setdefault("broker", {})
            broker.setdefault("resumeSessionId", session.cli_session_id)

    async def _run_cleanup(
        self,
        session: Session,
        principal: Principal | None,
        *,
        context: SessionContext | None = None,
    ) -> None:
        """Run contributor cleanup in reverse config order.

        Failures are logged but don't block other contributors.
        """
        if self._communication_route_repository is not None:
            try:
                await self._communication_route_repository.deactivate_routes_for_session(session.id)
            except Exception:
                logger.warning(
                    "Failed to deactivate communication routes for session %s",
                    session.id,
                    exc_info=True,
                )

        if not self._contributors:
            return

        if context is None:
            context = SessionContext(
                principal=principal,
                **await self._execution_context(session),
            )
        for contributor in reversed(self._contributors):
            try:
                await contributor.cleanup(session, context)
            except Exception:
                logger.warning(
                    "Cleanup failed for contributor %s",
                    contributor.name,
                    exc_info=True,
                )

    async def _poll_readiness(
        self,
        session: Session,
        *,
        skip_initial_delay: bool = False,
    ) -> None:
        """Wait for backend readiness and persist a terminal runtime verdict."""
        if not skip_initial_delay:
            await asyncio.sleep(self._provisioning_initial_delay)

        try:
            result_status = await self._pod_manager.wait_for_ready(
                session, self._provisioning_timeout
            )
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Readiness check failed for session %s", session.id)
            return

        # Re-fetch to check it's still PROVISIONING (could have been stopped/deleted)
        current = await self._repository.get(session.id)
        if current is None or current.status != SessionStatus.PROVISIONING:
            return

        if result_status in {SessionStatus.STARTING, SessionStatus.PROVISIONING}:
            logger.info(
                "Session %s is still reconciling after readiness wait",
                session.id,
            )
            return

        if result_status != SessionStatus.FAILED:
            await self._persist_reconciled_session(current, result_status)
            return

        status_detail = await self._pod_manager.status_detail(current)
        msg = status_detail or "Provisioning failed: infrastructure reported failure"
        failed = current.with_status(SessionStatus.FAILED).with_error(msg)
        await self._repository.update(failed)
        if self._broadcaster is not None:
            await self._broadcaster.publish_session_updated(failed)
        await self._emit_session_failed(failed, msg)

    def _cancel_provisioning_task(self, session_id: UUID) -> None:
        """Cancel an active provisioning task if one exists."""
        task = self._provisioning_tasks.pop(session_id, None)
        if task is not None and not task.done():
            task.cancel()

    async def _emit_session_started(self, session: Session) -> None:
        """Publish volundr.session.started to Sleipnir (no-op when absent)."""
        if self._sleipnir_publisher is None or _catalog_started is None:
            return
        try:
            from volundr.domain.models import GitSource  # noqa: PLC0415

            repo = session.source.repo if isinstance(session.source, GitSource) else ""
            branch = session.source.branch if isinstance(session.source, GitSource) else ""
            event = _catalog_started(
                session_id=str(session.id),
                user_id=session.owner_id or "",
                repo=repo,
                branch=branch or "",
                source="volundr",
                correlation_id=str(session.id),
            )
            await self._sleipnir_publisher.publish(event)
        except Exception:
            logger.warning("Failed to emit volundr.session.started; continuing.", exc_info=True)

    async def _emit_session_failed(self, session: Session, error: str) -> None:
        """Publish volundr.session.failed to Sleipnir (no-op when absent)."""
        if self._sleipnir_publisher is None or _catalog_failed is None:
            return
        try:
            event = _catalog_failed(
                session_id=str(session.id),
                error=error,
                user_id=session.owner_id or "",
                source="volundr",
                correlation_id=str(session.id),
            )
            await self._sleipnir_publisher.publish(event)
        except Exception:
            logger.warning("Failed to emit volundr.session.failed; continuing.", exc_info=True)

    async def _register_communication_routes(self, session: Session) -> None:
        """Sync active external communication targets exposed by the live session."""
        if (
            self._communication_route_repository is None
            or self._session_communication_port is None
            or not session.owner_id
        ):
            return

        try:
            targets = await self._session_communication_port.list_communication_targets(session.id)
        except Exception:
            logger.warning(
                "Failed to discover communication targets for session %s",
                session.id,
                exc_info=True,
            )
            return

        for target in targets:
            route = CommunicationRoute(
                id=_communication_route_id(
                    session_id=session.id,
                    platform=target.platform.value,
                    conversation_id=target.conversation_id,
                    thread_id=target.thread_id,
                ),
                platform=target.platform,
                conversation_id=target.conversation_id,
                thread_id=target.thread_id,
                session_id=session.id,
                owner_id=session.owner_id,
                mode=target.mode,
                default_target=target.default_target,
                metadata=target.metadata,
            )
            try:
                await self._communication_route_repository.upsert_route(route)
            except Exception:
                logger.warning(
                    "Failed to upsert communication route for session %s (%s:%s/%s)",
                    session.id,
                    target.platform.value,
                    target.conversation_id,
                    target.thread_id,
                    exc_info=True,
                )

    async def stop_session(
        self,
        session_id: UUID,
        principal: Principal | None = None,
    ) -> Session:
        """Stop a session's pods."""
        session = await self._repository.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)

        await self._check_access(session, principal, "stop")
        return await self._stop_session(session, principal)

    async def _stop_session(self, session: Session, principal: Principal | None) -> Session:
        """Stop an authorized session or complete an internal lifecycle transition."""
        session_id = session.id
        if not session.can_stop():
            raise SessionStateError(session_id, "stop", session.status)

        # Cancel provisioning task if active
        self._cancel_provisioning_task(session_id)

        stopping = session.with_status(SessionStatus.STOPPING)
        await self._repository.update(stopping)

        if self._broadcaster is not None:
            await self._broadcaster.publish_session_updated(stopping)

        try:
            cleanup_context = None
            if self._contributors:
                cleanup_context = SessionContext(
                    principal=principal,
                    **await self._execution_context(session),
                )
            stopped = await self._pod_manager.stop(session)
            if not stopped:
                if self._runtime_backend == "vm" or self._execution_resolver is not None:
                    raise RuntimeError("Compute infrastructure stop was not confirmed")
                logger.warning(
                    "Pod manager could not find/cancel pods for session %s "
                    "(may already be stopped or task ID mismatch)",
                    _sanitize_log(session_id),
                )

            # Run contributor cleanup in reverse order
            await self._run_cleanup(session, principal, context=cleanup_context)

            stopped = (
                stopping.with_status(SessionStatus.STOPPED)
                .with_cleared_endpoints()
                .model_copy(update={"error": None})
            )
            final = await self._repository.update(stopped)

            if self._broadcaster is not None:
                await self._broadcaster.publish_session_updated(final)

            return final
        except Exception as e:
            failed = stopping.with_status(SessionStatus.FAILED).with_error(str(e))
            await self._repository.update(failed)

            if self._broadcaster is not None:
                await self._broadcaster.publish_session_updated(failed)

            raise

    async def record_activity(self, session_id: UUID, message_count: int, tokens: int) -> Session:
        """Record activity metrics for a session."""
        session = await self._repository.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)

        updated = session.with_activity(message_count, tokens)
        return await self._repository.update(updated)

    async def archive_session(
        self,
        session_id: UUID,
        principal: Principal | None = None,
    ) -> Session:
        """Archive a session. Stops pod if running, marks as archived."""
        session = await self._repository.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)

        await self._check_access(session, principal, "update")

        # If running/starting/provisioning, stop first
        if session.status in (
            SessionStatus.RUNNING,
            SessionStatus.STARTING,
            SessionStatus.PROVISIONING,
        ):
            await self.stop_session(session_id, principal=principal)
            session = await self._repository.get(session_id)

        # Only stopped/failed/created sessions can be archived
        if session.status not in (
            SessionStatus.STOPPED,
            SessionStatus.FAILED,
            SessionStatus.CREATED,
        ):
            raise SessionStateError(session_id, "archive", session.status)

        now = datetime.now(UTC)
        archived = session.model_copy(
            update={
                "status": SessionStatus.ARCHIVED,
                "archived_at": now,
                "updated_at": now,
                "pod_name": None,
                "chat_endpoint": None,
                "code_endpoint": None,
            }
        )
        updated = await self._repository.update(archived)

        if self._broadcaster:
            await self._broadcaster.publish_session_updated(updated)

        return updated

    async def restore_session(
        self,
        session_id: UUID,
        principal: Principal | None = None,
    ) -> Session:
        """Restore an archived session to stopped state."""
        session = await self._repository.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)

        await self._check_access(session, principal, "update")

        if session.status != SessionStatus.ARCHIVED:
            raise SessionStateError(session_id, "restore", session.status)

        restored = session.model_copy(
            update={
                "status": SessionStatus.STOPPED,
                "archived_at": None,
                "updated_at": datetime.now(UTC),
            }
        )
        updated = await self._repository.update(restored)

        if self._broadcaster:
            await self._broadcaster.publish_session_updated(updated)

        return updated

    async def archive_stopped_sessions(self) -> list[UUID]:
        """Bulk archive all stopped sessions."""
        sessions = await self._repository.list(status=SessionStatus.STOPPED)
        archived_ids = []
        for s in sessions:
            await self.archive_session(s.id)
            archived_ids.append(s.id)
        return archived_ids

    async def reconcile_provisioning_sessions(self) -> None:
        """Re-launch polling or mark FAILED for sessions stuck in PROVISIONING.

        Called on application startup to handle sessions that were left
        in PROVISIONING state after a restart.
        """
        sessions = await self._repository.list(status=SessionStatus.PROVISIONING)
        for session in sessions:
            logger.info(
                "Reconciling stuck PROVISIONING session %s, re-launching readiness poll",
                session.id,
            )
            task = asyncio.create_task(self._poll_readiness(session, skip_initial_delay=True))
            self._provisioning_tasks[session.id] = task
            task.add_done_callback(
                lambda t, sid=session.id: self._provisioning_tasks.pop(sid, None)
            )

    def _reconciled_session(
        self,
        session: Session,
        actual_status: SessionStatus,
        status_detail: str | None = None,
    ) -> Session:
        """Return the corrected session row for a pod-status divergence.

        Dead runtimes clear endpoints and stamp a queryable ``liveness:`` error.
        Recovered runtimes clear stale errors and restore deterministic endpoints.
        """
        if actual_status == SessionStatus.STOPPED:
            return (
                session.with_status(SessionStatus.STOPPED)
                .with_cleared_endpoints()
                .with_error("liveness: pod is no longer running — endpoint cleared")
            )
        if actual_status == SessionStatus.FAILED:
            return (
                session.with_status(SessionStatus.FAILED)
                .with_cleared_endpoints()
                .with_error(status_detail or "liveness: session runtime is no longer available")
            )

        target_status = actual_status
        if session.status == SessionStatus.FAILED and actual_status == SessionStatus.STARTING:
            target_status = SessionStatus.PROVISIONING
        updated = session.with_status(target_status)
        return updated.model_copy(
            update={
                "chat_endpoint": session.chat_endpoint
                or self._pod_manager.initial_chat_endpoint(session),
                "code_endpoint": session.code_endpoint
                or self._pod_manager.initial_code_endpoint(session),
                "error": status_detail,
            }
        )

    async def _persist_reconciled_session(
        self,
        session: Session,
        actual_status: SessionStatus,
        status_detail: str | None = None,
    ) -> Session:
        updated = self._reconciled_session(session, actual_status, status_detail)
        final = await self._repository.update(updated)
        if self._broadcaster is not None:
            await self._broadcaster.publish_session_updated(final)
        if session.status != SessionStatus.RUNNING and final.status == SessionStatus.RUNNING:
            await self._register_communication_routes(final)
            await self._emit_session_started(final)
        return final

    async def reconcile_active_sessions(self) -> int:
        """Reconcile stored session rows against the pod manager.

        Active rows follow runtime state. In Kubernetes mode, failed rows may
        recover from a Ready HelmRelease and terminal rows shed orphaned runtime
        resources. Intentional terminal states are never resurrected.

        Because the verdict comes from ``pod_manager.status()`` and not a
        heartbeat clock, an idle-but-alive session is never false-reaped — this is
        the mechanism the disabled-by-default last_active reaper could not provide.

        Returns the number of sessions reconciled.
        """
        statuses = [
            SessionStatus.STARTING,
            SessionStatus.PROVISIONING,
            SessionStatus.RUNNING,
            SessionStatus.STOPPING,
        ]
        durable_compute = self._runtime_backend == "vm" or self._execution_resolver is not None
        if self._runtime_backend == "kubernetes":
            statuses.extend(
                [
                    SessionStatus.FAILED,
                    SessionStatus.STOPPED,
                    SessionStatus.ARCHIVED,
                ]
            )
        elif durable_compute:
            # Kubernetes sheds orphaned resources for terminal rows on its own,
            # so only FAILED (a row a provisioning-cleanup failure can leave
            # bound to still-running compute) needs to keep being swept for a
            # durable-compute backend; see the comment in _provision_background.
            statuses.append(SessionStatus.FAILED)
        sessions = [
            session for status in statuses for session in await self._repository.list(status=status)
        ]
        reconciled = 0
        for session in sessions:
            if session.status == SessionStatus.FAILED and self._runtime_backend != "kubernetes":
                # Swept only to release compute a failed cleanup left bound. Never
                # ask for its status: a VM runtime reports a still-bound lease as
                # provisioning and restarts it, which would resurrect the session.
                if await self._pod_manager.stop(session):
                    reconciled += 1
                continue

            actual_status = await self._pod_manager.status(session)
            status_detail = await self._pod_manager.status_detail(session)

            if session.status in {
                SessionStatus.STOPPING,
                SessionStatus.STOPPED,
                SessionStatus.ARCHIVED,
            }:
                if actual_status == SessionStatus.STOPPED:
                    if session.status != SessionStatus.STOPPING:
                        continue
                    stopped = (
                        session.with_status(SessionStatus.STOPPED)
                        .with_cleared_endpoints()
                        .model_copy(update={"error": None})
                    )
                    final = await self._repository.update(stopped)
                    if self._broadcaster is not None:
                        await self._broadcaster.publish_session_updated(final)
                    reconciled += 1
                    continue
                if await self._pod_manager.stop(session):
                    reconciled += 1
                continue

            if session.status == SessionStatus.FAILED and actual_status in {
                SessionStatus.FAILED,
                SessionStatus.STOPPED,
            }:
                if actual_status == SessionStatus.FAILED and await self._pod_manager.stop(session):
                    reconciled += 1
                continue

            if actual_status == session.status:
                if (
                    actual_status in {SessionStatus.STARTING, SessionStatus.PROVISIONING}
                    and session.error != status_detail
                ):
                    await self._persist_reconciled_session(session, actual_status, status_detail)
                    reconciled += 1
                continue

            logger.info(
                "Reconciling active session %s from %s to %s",
                session.id,
                session.status.value,
                actual_status.value,
            )
            final = await self._persist_reconciled_session(session, actual_status, status_detail)
            reconciled += 1
            if actual_status == SessionStatus.FAILED and self._runtime_backend == "kubernetes":
                await self._pod_manager.stop(final)
        return reconciled

    async def reconcile_session_if_active(self, session_id: UUID) -> Session | None:
        """Reconcile one active or recoverable failed row and return it."""
        session = await self._repository.get(session_id)
        if session is None:
            return None
        reconcilable_statuses = {
            SessionStatus.STARTING,
            SessionStatus.PROVISIONING,
            SessionStatus.RUNNING,
        }
        if self._runtime_backend == "kubernetes":
            reconcilable_statuses.add(SessionStatus.FAILED)
        if session.status not in reconcilable_statuses:
            return session

        actual_status = await self._pod_manager.status(session)
        status_detail = await self._pod_manager.status_detail(session)
        if actual_status == session.status:
            if (
                actual_status in {SessionStatus.STARTING, SessionStatus.PROVISIONING}
                and session.error != status_detail
            ):
                return await self._persist_reconciled_session(session, actual_status, status_detail)
            return session

        logger.info(
            "Reconciling session %s from %s to %s",
            session.id,
            session.status.value,
            actual_status.value,
        )
        if session.status == SessionStatus.FAILED and actual_status in {
            SessionStatus.FAILED,
            SessionStatus.STOPPED,
        }:
            return session

        return await self._persist_reconciled_session(session, actual_status, status_detail)

    async def mark_session_dead(self, session_id: UUID) -> Session | None:
        """Force a single session to be reconciled against the pod manager NOW.

        Invoked from out-of-band death signals (a local broker process exiting, a
        WS proxy that can't reach the pod) so the row reflects reality promptly
        instead of waiting for the next periodic sweep. Pod-status authoritative:
        if the pod manager still reports the session live, the row is left
        untouched (no false reap).
        """
        return await self.reconcile_session_if_active(session_id)


def _communication_route_id(
    *,
    session_id: UUID,
    platform: str,
    conversation_id: str,
    thread_id: str | None,
) -> UUID:
    """Return a stable route UUID for a session communication target."""
    value = (
        f"volundr:communication-route:{session_id}:{platform}:{conversation_id}:{thread_id or ''}"
    )
    return uuid5(NAMESPACE_URL, value)
