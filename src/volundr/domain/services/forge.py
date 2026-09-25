"""Shared Forge application service for route orchestration."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from volundr.adapters.inbound.rest import SessionCreate
    from volundr.domain.models import (
        Chronicle,
        ModelProvider,
        Principal,
        Session,
        SessionActivityState,
        Timeline,
        TimelineEvent,
        TimelineEventType,
        WorkspaceStatus,
    )
    from volundr.domain.ports import PricingProvider
    from volundr.domain.services.chronicle import ChronicleService
    from volundr.domain.services.repo import ProviderInfo, RepoService
    from volundr.domain.services.session_archive import SessionArchiveService
    from volundr.domain.services.stats import StatsService
    from volundr.domain.services.token import TokenService
    from volundr.domain.services.workspace import WorkspaceService
    from volundr.domain.session_read_state import SessionReadState, SessionReadStateChange

    from .session import SessionService


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
    ) -> None:
        self._session_service = session_service
        self._stats_service = stats_service
        self._token_service = token_service
        self._pricing_provider = pricing_provider
        self._repo_service = repo_service
        self._chronicle_service = chronicle_service
        self._archive_service = archive_service
        self._workspace_service = workspace_service
        self._project_service = project_service

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
        )

    async def list_sessions(
        self,
        *,
        status=None,
        include_archived: bool = False,
        principal: Principal | None = None,
    ) -> list[Session]:
        return await self._session_service.list_sessions(
            status=status,
            include_archived=include_archived,
            principal=principal,
        )

    async def archive_stopped_sessions(self) -> list[UUID]:
        return await self._session_service.archive_stopped_sessions()

    async def create_and_start_session(
        self,
        data: SessionCreate,
        *,
        principal: Principal | None = None,
    ) -> Session:
        if getattr(data, "coordination", None) is not None:
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
            **(project_options or {}),
        )
        workload_config = dict(data.workload_config or {})
        persona_name = getattr(data, "persona_name", "")
        if persona_name:
            workload_config["persona"] = persona_name
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

    def _resolve_session_definition(
        self,
        model: str,
        explicit_definition: str | None,
    ) -> str | None:
        if explicit_definition:
            return explicit_definition
        if self._pricing_provider is None:
            return None
        normalized_model = str(model or "").strip()
        if not normalized_model:
            return None
        for candidate in self._pricing_provider.list_models():
            if str(getattr(candidate, "id", "") or "").strip() != normalized_model:
                continue
            definition = str(getattr(candidate, "session_definition", "") or "").strip()
            return definition or None
        return None

    async def with_read_states(
        self, sessions: list[Session], principal: Principal | None
    ) -> list[Session]:
        return await self._session_service.with_read_states(sessions, principal)

    async def get_read_state(self, session_id: UUID, principal: Principal) -> SessionReadState:
        return await self._session_service.get_read_state(session_id, principal)

    async def change_read_state(
        self, session_id: UUID, change: SessionReadStateChange, principal: Principal
    ) -> SessionReadState:
        return await self._session_service.change_read_state(session_id, change, principal)

    async def get_session(self, session_id: UUID) -> Session | None:
        return await self._session_service.reconcile_session_if_active(session_id)

    async def reconcile_session(self, session_id: UUID) -> Session | None:
        """Force a pod-status reconcile of one session (used on a dead-pod op)."""
        return await self._session_service.mark_session_dead(session_id)

    async def get_authorized_session(
        self, session_id: UUID, *, principal: Principal | None, action: str
    ) -> Session:
        return await self._session_service.get_authorized_session(session_id, principal, action)

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

    async def delete_session(
        self,
        session_id: UUID,
        *,
        principal: Principal | None = None,
        cleanup_targets=None,
    ) -> bool:
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
        base_url = session.chat_endpoint.replace("wss://", "https://").replace("ws://", "http://")
        if base_url.endswith("/session"):
            base_url = base_url[: -len("/session")]
        return session, base_url

    async def get_transcript(self, session_id: UUID) -> dict:
        if self._archive_service is None:
            raise RuntimeError("Session archive service not available")
        return await self._archive_service.get_transcript(session_id)

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
