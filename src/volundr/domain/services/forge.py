"""Shared Forge application service for route orchestration."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
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
        WorkspaceStatus,
    )
    from volundr.domain.ports import PricingProvider
    from volundr.domain.services.chronicle import ChronicleService
    from volundr.domain.services.repo import ProviderInfo, RepoService
    from volundr.domain.services.stats import StatsService
    from volundr.domain.services.token import TokenService
    from volundr.domain.services.workspace import WorkspaceService

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
        workspace_service: WorkspaceService | None = None,
    ) -> None:
        self._session_service = session_service
        self._stats_service = stats_service
        self._token_service = token_service
        self._pricing_provider = pricing_provider
        self._repo_service = repo_service
        self._chronicle_service = chronicle_service
        self._workspace_service = workspace_service

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
            workspace_service=workspace_service,
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
        session = await self._session_service.create_session(
            name=data.name,
            model=data.model,
            source=data.source,
            template_name=data.template_name,
            preset_id=data.preset_id,
            principal=principal,
            workspace_id=data.workspace_id,
            tracker_issue_id=data.issue_id,
            issue_tracker_url=data.issue_url,
        )
        return await self._session_service.start_session(
            session.id,
            definition=data.definition,
            profile_name=data.profile_name,
            template_name=data.template_name,
            principal=principal,
            terminal_restricted=data.terminal_restricted,
            credential_names=data.credential_names,
            integration_ids=data.integration_ids,
            resource_config=data.resource_config or None,
            system_prompt=data.system_prompt,
            initial_prompt=data.initial_prompt,
            workload_type=data.workload_type,
            workload_config=data.workload_config or None,
        )

    async def get_session(self, session_id: UUID) -> Session | None:
        return await self._session_service.get_session(session_id)

    async def ensure_access(
        self,
        session: Session,
        principal: Principal | None,
        action: str = "view",
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
        profile_name: str | None = None,
        principal: Principal | None = None,
    ) -> Session:
        return await self._session_service.start_session(
            session_id,
            profile_name=profile_name,
            principal=principal,
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
    ) -> Session:
        return await self._session_service.update_activity(session_id, activity_state, metadata)

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
        summary: str,
        key_changes: list[str],
        unfinished_work: list[str],
        duration_seconds: int,
    ) -> Chronicle:
        if self._chronicle_service is None:
            raise RuntimeError("Chronicle service not available")
        return await self._chronicle_service.create_or_update_from_broker(
            session_id=session_id,
            summary=summary,
            key_changes=key_changes,
            unfinished_work=unfinished_work,
            duration_seconds=duration_seconds,
        )

    async def list_chronicles(
        self,
        *,
        project: str | None = None,
        repo: str | None = None,
        model: str | None = None,
        tags: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Chronicle]:
        if self._chronicle_service is None:
            raise RuntimeError("Chronicle service not available")
        return await self._chronicle_service.list_chronicles(
            project=project,
            repo=repo,
            model=model,
            tags=tags,
            limit=limit,
            offset=offset,
        )

    async def create_chronicle(self, session_id: UUID) -> Chronicle:
        if self._chronicle_service is None:
            raise RuntimeError("Chronicle service not available")
        return await self._chronicle_service.create_chronicle(session_id)

    async def get_chronicle(self, chronicle_id: UUID) -> Chronicle | None:
        if self._chronicle_service is None:
            raise RuntimeError("Chronicle service not available")
        return await self._chronicle_service.get_chronicle(chronicle_id)

    async def update_chronicle(
        self,
        chronicle_id: UUID,
        *,
        summary: str | None = None,
        key_changes: list[str] | None = None,
        unfinished_work: list[str] | None = None,
        tags: list[str] | None = None,
        status=None,
    ) -> Chronicle:
        if self._chronicle_service is None:
            raise RuntimeError("Chronicle service not available")
        return await self._chronicle_service.update_chronicle(
            chronicle_id,
            summary=summary,
            key_changes=key_changes,
            unfinished_work=unfinished_work,
            tags=tags,
            status=status,
        )

    async def delete_chronicle(self, chronicle_id: UUID) -> bool:
        if self._chronicle_service is None:
            raise RuntimeError("Chronicle service not available")
        return await self._chronicle_service.delete_chronicle(chronicle_id)

    async def reforge_chronicle(self, chronicle_id: UUID) -> Session:
        if self._chronicle_service is None:
            raise RuntimeError("Chronicle service not available")
        return await self._chronicle_service.reforge(chronicle_id)

    async def get_chronicle_chain(self, chronicle_id: UUID) -> list[Chronicle]:
        if self._chronicle_service is None:
            raise RuntimeError("Chronicle service not available")
        return await self._chronicle_service.get_chain(chronicle_id)

    async def get_session_chronicle(self, session_id: UUID) -> Chronicle | None:
        if self._chronicle_service is None:
            raise RuntimeError("Chronicle service not available")
        return await self._chronicle_service.get_chronicle_by_session(session_id)

    async def get_timeline(self, session_id: UUID) -> Timeline | None:
        if self._chronicle_service is None:
            raise RuntimeError("Chronicle service not available")
        return await self._chronicle_service.get_timeline(session_id)

    async def add_timeline_event(self, session_id: UUID, event: TimelineEvent) -> TimelineEvent:
        if self._chronicle_service is None:
            raise RuntimeError("Chronicle service not available")
        return await self._chronicle_service.add_timeline_event(session_id, event)

    async def ensure_session_chronicle(self, session_id: UUID) -> Chronicle:
        if self._chronicle_service is None:
            raise RuntimeError("Chronicle service not available")
        chronicle = await self._chronicle_service.get_chronicle_by_session(session_id)
        if chronicle is not None:
            return chronicle
        return await self._chronicle_service.create_chronicle(session_id)

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

    async def get_stats(self):
        if self._stats_service is None:
            raise RuntimeError("Stats service not available")
        return await self._stats_service.get_stats()

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
