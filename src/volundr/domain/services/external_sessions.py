"""Service for discovering and importing external CLI sessions.

External sessions are Claude Code or Codex sessions that live in the
harness's own on-disk store. This service lists them across all
configured providers and imports them as Volundr sessions, so they can
be restarted (resumed) as regular Volundr-managed sessions.
"""

import logging
from uuid import UUID

from volundr.domain.models import (
    ExternalSessionRecord,
    LocalMountSource,
    Principal,
    Session,
)
from volundr.domain.mount_policy import is_host_path_allowed
from volundr.domain.ports import ExternalSessionProvider, SessionRepository
from volundr.domain.services.session import SessionService

logger = logging.getLogger(__name__)


class ExternalSessionProviderNotFoundError(Exception):
    """Raised when no provider matches the requested name."""

    def __init__(self, provider: str):
        self.provider = provider
        super().__init__(f"Unknown external session provider: {provider}")


class ExternalSessionNotFoundError(Exception):
    """Raised when an external session cannot be found in the provider's store."""

    def __init__(self, provider: str, external_id: str):
        self.provider = provider
        self.external_id = external_id
        super().__init__(f"External session not found: {provider}/{external_id}")


class ExternalSessionAlreadyImportedError(Exception):
    """Raised when the external session was already imported."""

    def __init__(self, external_id: str, session_id: UUID):
        self.external_id = external_id
        self.session_id = session_id
        super().__init__(f"External session {external_id} already imported as session {session_id}")


class ExternalSessionWorkspaceError(Exception):
    """Raised when the external session's workspace is unusable."""

    def __init__(self, external_id: str, workspace_path: str):
        self.external_id = external_id
        self.workspace_path = workspace_path
        super().__init__(
            f"Workspace for external session {external_id} is not available: {workspace_path!r}"
        )


class ExternalSessionPathNotAllowedError(Exception):
    """Raised when the external session's workspace violates the mount prefix policy."""

    def __init__(self, external_id: str, workspace_path: str):
        self.external_id = external_id
        self.workspace_path = workspace_path
        super().__init__(
            f"Workspace for external session {external_id} is outside the allowed "
            f"mount prefixes: {workspace_path!r}"
        )


class ExternalSessionService:
    """Lists external CLI sessions and imports them as Volundr sessions."""

    def __init__(
        self,
        providers: list[ExternalSessionProvider],
        repository: SessionRepository,
        session_service: SessionService,
        allowed_workspace_prefixes: list[str] | None = None,
        allow_root_workspace: bool = False,
    ):
        self._providers = {provider.name: provider for provider in providers}
        self._repository = repository
        self._session_service = session_service
        self._allowed_workspace_prefixes = allowed_workspace_prefixes or []
        self._allow_root_workspace = allow_root_workspace

    @property
    def provider_names(self) -> list[str]:
        return list(self._providers)

    async def list_external_sessions(
        self,
        provider: str | None = None,
    ) -> list[ExternalSessionRecord]:
        """List discoverable sessions across providers, newest first.

        Records that were already imported carry the Volundr session id
        in ``imported_session_id``.
        """
        if provider is not None and provider not in self._providers:
            raise ExternalSessionProviderNotFoundError(provider)

        providers = (
            [self._providers[provider]] if provider is not None else list(self._providers.values())
        )

        records: list[ExternalSessionRecord] = []
        for prov in providers:
            records.extend(await prov.list_sessions())

        imported = await self._imported_index()
        annotated = [
            record.model_copy(
                update={
                    "imported_session_id": imported.get(record.external_id),
                    "workspace_allowed": self._workspace_allowed(record),
                }
            )
            for record in records
        ]
        annotated.sort(
            key=lambda r: r.updated_at.timestamp() if r.updated_at else 0.0,
            reverse=True,
        )
        return annotated

    async def import_session(
        self,
        provider: str,
        external_id: str,
        name: str | None = None,
        principal: Principal | None = None,
    ) -> Session:
        """Import an external session as a stopped Volundr session.

        The created session points at the original working directory via
        a local mount, records its origin and native session id, and can
        then be started like any other Volundr session — the start path
        resumes the native CLI session.
        """
        prov = self._providers.get(provider)
        if prov is None:
            raise ExternalSessionProviderNotFoundError(provider)

        record = await prov.get_session(external_id)
        if record is None:
            raise ExternalSessionNotFoundError(provider, external_id)

        imported = await self._imported_index()
        existing = imported.get(record.external_id)
        if existing is not None:
            raise ExternalSessionAlreadyImportedError(record.external_id, existing)

        if not record.workspace_exists:
            raise ExternalSessionWorkspaceError(record.external_id, record.workspace_path)

        if not self._workspace_allowed(record):
            raise ExternalSessionPathNotAllowedError(record.external_id, record.workspace_path)

        session_name = name or self._default_name(record)
        session = await self._session_service.create_session(
            name=session_name,
            model=record.model,
            source=LocalMountSource(local_path=record.workspace_path),
            principal=principal,
            origin=record.harness,
            external_session_id=record.external_id,
        )
        logger.info(
            "Imported external session %s/%s as Volundr session %s",
            provider,
            record.external_id,
            session.id,
        )
        return session

    def _workspace_allowed(self, record: ExternalSessionRecord) -> bool:
        """Apply the allowed mount prefix policy to the record's workspace."""
        if not record.workspace_path:
            return False
        return is_host_path_allowed(
            record.workspace_path,
            self._allowed_workspace_prefixes,
            allow_root_mount=self._allow_root_workspace,
        )

    async def _imported_index(self) -> dict[str, UUID]:
        """Map external session id → Volundr session id for imported sessions."""
        sessions = await self._repository.list()
        return {
            session.external_session_id: session.id
            for session in sessions
            if session.external_session_id
        }

    @staticmethod
    def _default_name(record: ExternalSessionRecord) -> str:
        suffix = record.external_id.split("-")[0] or record.external_id
        return f"{record.harness}-import-{suffix}"
