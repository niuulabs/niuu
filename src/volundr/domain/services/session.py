"""Domain services for session management."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, UUID, uuid5

try:
    from sleipnir.domain.catalog import volundr_session_failed as _catalog_failed
    from sleipnir.domain.catalog import volundr_session_started as _catalog_started
    from sleipnir.ports.events import SleipnirPublisher as _SleipnirPublisher
except ImportError:
    _SleipnirPublisher = None  # type: ignore[assignment,misc]
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
    AuthorizationPort,
    ChronicleRepository,
    CommunicationRouteRepository,
    EventBroadcaster,
    IntegrationRepository,
    PodManager,
    Resource,
    SessionCommunicationPort,
    SessionContext,
    SessionContribution,
    SessionContributor,
    SessionRepository,
    StoragePort,
    TemplateProvider,
)

if TYPE_CHECKING:
    from volundr.adapters.outbound.git_registry import GitProviderRegistry

logger = logging.getLogger(__name__)


def _sanitize_log(value: object) -> str:
    """Sanitize a value for safe log output (prevent log injection)."""
    return str(value).replace("\n", "\\n").replace("\r", "\\r")


def _public_loopback_host() -> str:
    """Return the loopback host we publish to browser-facing clients."""
    host = os.environ.get("NIUU_SERVER_HOST", "127.0.0.1").strip() or "127.0.0.1"
    return "localhost" if host == "127.0.0.1" else host


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
        template_provider: TemplateProvider | None = None,
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
    ):
        self._repository = repository
        self._pod_manager = pod_manager
        self._git_registry = git_registry
        self._validate_repos = validate_repos
        self._broadcaster = broadcaster
        self._template_provider = template_provider
        self._authorization = authorization
        self._contributors = contributors or []
        self._provisioning_timeout = provisioning_timeout
        self._provisioning_initial_delay = provisioning_initial_delay
        self._provisioning_tasks: dict[UUID, asyncio.Task] = {}
        self._integration_repo = integration_repo
        self._storage = storage
        self._chronicle_repository = chronicle_repository
        self._sleipnir_publisher = sleipnir_publisher
        self._communication_route_repository = communication_route_repository
        self._session_communication_port = session_communication_port

    async def create_session(
        self,
        name: str,
        model: str,
        source: SessionSource | None = None,
        template_name: str | None = None,
        preset_id: UUID | None = None,
        principal: Principal | None = None,
        workspace_id: UUID | None = None,
        tracker_issue_id: str | None = None,
        issue_tracker_url: str | None = None,
    ) -> Session:
        """Create a new session.

        Args:
            name: Session name.
            model: Model identifier.
            source: Workspace source (git or local_mount). Defaults to empty GitSource.
            template_name: Optional workspace template name. When provided,
                the template's repos/profile are used to fill in defaults
                for source and model if not explicitly provided.
            preset_id: Optional preset ID to associate with the session.
            principal: Authenticated identity. When provided, sets owner_id
                and tenant_id on the session.

        Returns:
            Created session.

        Raises:
            RepoValidationError: If repository validation is enabled and fails.
        """
        if source is None:
            source = GitSource()

        # Resolve template defaults when a template is specified
        if template_name and self._template_provider:
            template = self._template_provider.get(template_name)
            if template is not None:
                logger.info("Applying workspace template: %s", _sanitize_log(template_name))
                # Use first repo from template if caller didn't provide one
                if isinstance(source, GitSource) and not source.repo and template.repos:
                    first_repo = template.repos[0]
                    source = GitSource(
                        repo=first_repo.get("url", ""),
                        branch=first_repo.get("branch", source.branch or "main"),
                    )
                # Use model from template directly (unified template)
                if not model and template.model:
                    model = template.model

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
            preset_id=preset_id,
            owner_id=principal.user_id if principal else None,
            tenant_id=principal.tenant_id if principal else None,
            workspace_id=workspace_id,
            tracker_issue_id=tracker_issue_id,
            issue_tracker_url=issue_tracker_url,
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
    ) -> Session:
        """Update a session's activity state and broadcast an SSE event.

        Raises SessionNotFoundError if the session doesn't exist.
        """
        session = await self._repository.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)

        session.activity_state = state
        session.activity_metadata = metadata
        updated = await self._repository.update(session)

        if self._broadcaster is not None:
            await self._broadcaster.publish(
                RealtimeEvent(
                    type=EventType.SESSION_ACTIVITY,
                    data={
                        "session_id": str(session_id),
                        "state": state.value,
                        "metadata": metadata,
                        "owner_id": session.owner_id or "",
                    },
                    timestamp=updated.updated_at,
                )
            )
        return updated

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

        Optional *cleanup_targets* lists additional resources to permanently
        remove (e.g. workspace PVC, chronicles).  An empty/None list preserves
        the current default behaviour (archive workspace, keep chronicles).
        """
        session = await self._repository.get(session_id)
        if session is None:
            return False

        await self._check_access(session, principal, "delete")

        targets = set(cleanup_targets or [])

        # Cancel provisioning task if active
        self._cancel_provisioning_task(session_id)

        if session.status in (SessionStatus.RUNNING, SessionStatus.PROVISIONING):
            try:
                await self._pod_manager.stop(session)
            except Exception as e:
                logger.warning(
                    "Failed to stop pods for session %s during deletion: %s. "
                    "Proceeding with session deletion.",
                    _sanitize_log(session_id),
                    _sanitize_log(e),
                )

        # Run contributor cleanup in reverse order
        await self._run_cleanup(session, principal)

        deleted = await self._repository.delete(session_id)

        # Run optional resource cleanup after session record is gone
        if deleted:
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
        profile_name: str | None = None,
        template_name: str | None = None,
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

        # Set chat_endpoint eagerly — URL is deterministic from session ID
        host = _public_loopback_host()
        port = os.environ.get("NIUU_SERVER_PORT", "8080")
        chat_endpoint = f"ws://{host}:{port}/s/{session_id}/session"

        starting = session.model_copy(
            update={
                "status": SessionStatus.STARTING,
                "chat_endpoint": chat_endpoint,
                "code_endpoint": None,
                "updated_at": datetime.now(UTC),
                "workload_type": workload_type,
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
                template_name=template_name,
                profile_name=profile_name,
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
        template_name: str | None = None,
        profile_name: str | None = None,
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
                template_name,
                profile_name,
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
        template_name: str | None,
        profile_name: str | None,
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
            template_name=template_name,
            profile_name=profile_name,
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
        return await self._pod_manager.start(session, spec=spec)

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
        """Wait for backend readiness, then transition to RUNNING or FAILED."""
        if not skip_initial_delay:
            await asyncio.sleep(self._provisioning_initial_delay)

        error_detail = ""
        try:
            result_status = await self._pod_manager.wait_for_ready(
                session, self._provisioning_timeout
            )
        except asyncio.CancelledError:
            return
        except Exception as exc:
            result_status = SessionStatus.FAILED
            error_detail = str(exc)
            logger.exception("Readiness check failed for session %s", session.id)

        # Re-fetch to check it's still PROVISIONING (could have been stopped/deleted)
        current = await self._repository.get(session.id)
        if current is None or current.status != SessionStatus.PROVISIONING:
            return

        if result_status == SessionStatus.RUNNING:
            running = current.with_status(SessionStatus.RUNNING)
            await self._repository.update(running)
            if self._broadcaster is not None:
                await self._broadcaster.publish_session_updated(running)
            await self._register_communication_routes(running)
            await self._emit_session_started(running)
            return

        if error_detail:
            msg = f"Provisioning failed: {error_detail}"
        else:
            msg = "Provisioning timed out: infrastructure did not become ready"
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


def _communication_route_id(
    *,
    session_id: UUID,
    platform: str,
    conversation_id: str,
    thread_id: str | None,
) -> UUID:
    """Return a stable route UUID for a session communication target."""
    value = (
        f"volundr:communication-route:{session_id}:{platform}:"
        f"{conversation_id}:{thread_id or ''}"
    )
    return uuid5(NAMESPACE_URL, value)
