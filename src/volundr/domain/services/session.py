"""Domain services for session management."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, UUID, uuid5

try:
    from sleipnir.domain.catalog import volundr_session_failed as _catalog_failed
    from sleipnir.domain.catalog import volundr_session_started as _catalog_started
except ImportError:
    _catalog_started = None  # type: ignore[assignment]
    _catalog_failed = None  # type: ignore[assignment]

from volundr.domain.models import (
    CleanupTarget,
    CommunicationRoute,
    EventType,
    GitSource,
    IntegrationConnection,
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
    SessionCommunicationPort,
    SessionContext,
    SessionContribution,
    SessionContributor,
    SessionRepository,
    SessionSpanRepository,
    StoragePort,
)

if TYPE_CHECKING:
    from volundr.adapters.outbound.git_registry import GitProviderRegistry

logger = logging.getLogger(__name__)


def _sanitize_log(value: object) -> str:
    """Sanitize a value for safe log output (prevent log injection)."""
    return str(value).replace("\n", "\\n").replace("\r", "\\r")


class SessionNotFoundError(Exception):
    """Raised when a session is not found."""

    def __init__(self, session_id: UUID):
        self.session_id = session_id
        super().__init__(f"Session not found: {session_id}")


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
        public_origin: str = "http://localhost:8080",
        span_repository: SessionSpanRepository | None = None,
    ):
        self._repository = repository
        self._pod_manager = pod_manager
        self._git_registry = git_registry
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

        if isinstance(source, GitSource) and repo:
            if self._git_registry and self._validate_repos:
                await self._validate_repository(repo)
            elif not self._git_registry:
                logger.debug("Skipping repo validation: no git registry configured")
            elif not self._validate_repos:
                logger.debug("Skipping repo validation: validation disabled")

        session = Session(
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
        )
        created = await self._repository.create(session)

        if self._broadcaster is not None:
            await self._broadcaster.publish_session_created(created)

        return created

    async def _validate_repository(self, repo: str) -> None:
        """Validate that a repository exists and is accessible.

        Args:
            repo: Repository URL.

        Raises:
            RepoValidationError: If validation fails.
        """
        logger.info("Starting repository validation for: %s", _sanitize_log(repo))

        if self._git_registry is None:
            logger.warning(
                "Git registry not configured, skipping repository validation for: %s",
                _sanitize_log(repo),
            )
            return

        logger.debug(
            "Git registry has %d provider(s) registered",
            len(self._git_registry.providers),
        )

        provider = self._git_registry.get_provider(repo)
        if provider is None:
            logger.error(
                "No git provider supports repository URL: %s (registered providers: %s)",
                _sanitize_log(repo),
                ", ".join(
                    f"{p.name} ({p.provider_type.value})" for p in self._git_registry.providers
                )
                if self._git_registry.providers
                else "none",
            )
            raise RepoValidationError(repo, "no git provider supports this repository URL")

        logger.debug(
            "Found provider %s (%s) for repository: %s",
            provider.name,
            provider.provider_type.value,
            _sanitize_log(repo),
        )

        is_valid = await self._git_registry.validate_repo(repo)
        if not is_valid:
            logger.error(
                "Repository validation failed for %s using provider %s",
                _sanitize_log(repo),
                provider.name,
            )
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

        Delegates to the configured authorization adapter. No-op when
        principal is None (backward compat / dev mode) or when no
        authorization adapter is configured.

        Raises:
            SessionAccessDeniedError: If the principal lacks permission.
        """
        if principal is None:
            return

        if self._authorization is None:
            return

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

    async def update_activity(
        self,
        session_id: UUID,
        state: SessionActivityState,
        metadata: dict,
        state_since: datetime | None = None,
    ) -> Session:
        """Update a session's activity state and broadcast an SSE event.

        ``state_since`` is the broker-stamped UTC timestamp of when the session
        entered ``state`` (None for older brokers that don't report it). It is
        persisted and re-broadcast so clients can render an accurate elapsed
        time without re-deriving it from event arrival.

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

        session.activity_state = state
        # The broker stamps state_since only on a real change; fall back to "now"
        # when an older broker omits it so the field is never null for a live
        # session that just transitioned.
        session.activity_state_since = state_since or datetime.now(UTC)
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
                        "metadata": metadata,
                        "owner_id": session.owner_id or "",
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
            await self.stop_session(session_id)
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
        tenant_id = principal.tenant_id if principal else None
        owner_id = None
        if principal and TenantRole.ADMIN not in principal.roles:
            owner_id = principal.user_id

        if status is not None:
            return await self._repository.list(
                status=status,
                tenant_id=tenant_id,
                owner_id=owner_id,
            )

        sessions = await self._repository.list(
            tenant_id=tenant_id,
            owner_id=owner_id,
        )
        if include_archived:
            return sessions
        return [s for s in sessions if s.status != SessionStatus.ARCHIVED]

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

        try:
            await self._pod_manager.stop(session)
        except Exception as e:
            logger.warning(
                "Failed to stop infrastructure for session %s during deletion: %s. "
                "Proceeding with session deletion.",
                _sanitize_log(session_id),
                _sanitize_log(e),
            )

        # Run contributor cleanup in reverse order
        await self._run_cleanup(session, principal)

        deleted = await self._repository.delete(session_id)

        # Run optional resource cleanup after session record is gone
        if deleted:
            if self._span_repository is not None:
                await self._span_repository.delete_by_session(session_id)
            await self._run_targeted_cleanup(session_id, targets)

        if deleted and self._broadcaster is not None:
            await self._broadcaster.publish_session_deleted(session_id)

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

        # Set chat_endpoint eagerly — Flux/Gateway sessions know their public
        # route before the pod is ready; local mode falls back to the root proxy.
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
            logger.error("Provisioning failed for session %s: %s", session.id, e)
            failed = session.with_status(SessionStatus.FAILED).with_error(str(e))
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
        # Auto-include all enabled integrations when none are specified.
        # Keep the fetched connections so contributors don't re-fetch by ID.
        resolved_connections: list[IntegrationConnection] = []
        if integration_ids:
            # Caller specified IDs — fetch them in bulk
            if self._integration_repo:
                fetched = await asyncio.gather(
                    *(self._integration_repo.get_connection(cid) for cid in integration_ids),
                )
                resolved_connections = [c for c in fetched if c is not None and c.enabled]
        elif principal and self._integration_repo:
            all_connections = await self._integration_repo.list_connections(
                principal.user_id,
            )
            resolved_connections = [c for c in all_connections if c.enabled]

        context = SessionContext(
            principal=principal,
            definition=definition,
            launch_spec=launch_spec,
            runtime_backend=self._runtime_backend,
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

        context = SessionContext(principal=principal)
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

        msg = "Provisioning failed: infrastructure reported failure"
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

        if not session.can_stop():
            raise SessionStateError(session_id, "stop", session.status)

        # Cancel provisioning task if active
        self._cancel_provisioning_task(session_id)

        stopping = session.with_status(SessionStatus.STOPPING)
        await self._repository.update(stopping)

        if self._broadcaster is not None:
            await self._broadcaster.publish_session_updated(stopping)

        try:
            stopped = await self._pod_manager.stop(session)
            if not stopped:
                logger.warning(
                    "Pod manager could not find/cancel pods for session %s "
                    "(may already be stopped or task ID mismatch)",
                    _sanitize_log(session_id),
                )

            # Run contributor cleanup in reverse order
            await self._run_cleanup(session, principal)

            stopped = stopping.with_status(SessionStatus.STOPPED).with_cleared_endpoints()
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
            await self.stop_session(session_id)
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

    def _reconciled_session(self, session: Session, actual_status: SessionStatus) -> Session:
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
                .with_error("liveness: session runtime is no longer available")
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
                "error": None,
            }
        )

    async def _persist_reconciled_session(
        self,
        session: Session,
        actual_status: SessionStatus,
    ) -> Session:
        updated = self._reconciled_session(session, actual_status)
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
        ]
        if self._runtime_backend == "kubernetes":
            statuses.extend(
                [
                    SessionStatus.FAILED,
                    SessionStatus.STOPPING,
                    SessionStatus.STOPPED,
                    SessionStatus.ARCHIVED,
                ]
            )
        sessions = [
            session for status in statuses for session in await self._repository.list(status=status)
        ]
        reconciled = 0
        for session in sessions:
            actual_status = await self._pod_manager.status(session)

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
                continue

            logger.info(
                "Reconciling active session %s from %s to %s",
                session.id,
                session.status.value,
                actual_status.value,
            )
            final = await self._persist_reconciled_session(session, actual_status)
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
        if actual_status == session.status:
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

        return await self._persist_reconciled_session(session, actual_status)

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
