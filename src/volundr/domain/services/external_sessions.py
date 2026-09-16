"""Service for discovering and importing external CLI sessions.

External sessions are Claude Code or Codex sessions that live in the
harness's own on-disk store. This service lists them across all
configured providers and imports them as Volundr sessions, so they can
be restarted (resumed) as regular Volundr-managed sessions.
"""

import logging
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

from volundr.domain.history_import import (
    HistoryImportError,
    HistoryImportSessionNotFoundError,
    HistoryImportValidationError,
)
from volundr.domain.models import (
    ExternalSessionRecord,
    LocalMountSource,
    Principal,
    Session,
    SessionLogEntry,
)
from volundr.domain.mount_policy import is_host_path_allowed
from volundr.domain.ports import (
    ExternalSessionProvider,
    SessionEventLogRepository,
    SessionRepository,
)
from volundr.domain.services.session import SessionService, _sanitize_log

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
        event_log_repository: SessionEventLogRepository | None = None,
    ):
        self._providers = {provider.name: provider for provider in providers}
        self._repository = repository
        self._session_service = session_service
        self._allowed_workspace_prefixes = allowed_workspace_prefixes or []
        self._allow_root_workspace = allow_root_workspace
        self._event_log = event_log_repository

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
        # Parse before creating a Forge row: malformed or unavailable native
        # history must never look like a successful, empty recovery.
        entries = await self._read_history(prov, record.external_id, uuid4())
        session = await self._session_service.create_session(
            name=session_name,
            model=record.model,
            source=LocalMountSource(local_path=record.workspace_path),
            principal=principal,
            origin=record.harness,
            external_session_id=record.external_id,
        )
        try:
            await self._persist_history(
                session.id,
                prov,
                record.external_id,
                [replace(entry, session_id=session.id) for entry in entries],
            )
        except HistoryImportError as exc:
            raise HistoryImportError(
                f"Session {session.id} was created, but its history was not imported. "
                f"Retry POST /sessions/{session.id}/history/import. {exc}"
            ) from exc
        logger.info(
            "Imported external session %s/%s as Volundr session %s",
            _sanitize_log(provider),
            _sanitize_log(record.external_id),
            session.id,
        )
        return session

    async def backfill_session(self, session_id: UUID, principal: Principal | None = None) -> dict:
        """Import the stored native identity into an existing empty recovery.

        The repository enforces inactivity and the empty-transcript guard under
        the same lock used by live writers. A successful retry is a no-op.
        """
        session = await self._repository.get(session_id)
        if session is None:
            raise HistoryImportSessionNotFoundError(f"Session {session_id} not found")
        await self._session_service._check_access(session, principal, "update")
        external_id = session.external_session_id
        if not external_id:
            raise HistoryImportValidationError("Session has no imported native session identity")
        prov = next((p for p in self._providers.values() if p.harness == session.origin), None)
        if prov is None:
            raise ExternalSessionProviderNotFoundError(session.origin or "unknown")
        record = await prov.get_session(external_id)
        if record is None:
            raise ExternalSessionNotFoundError(prov.name, external_id)
        if not self._workspace_allowed(record):
            raise ExternalSessionPathNotAllowedError(external_id, record.workspace_path)
        if not record.workspace_exists:
            raise ExternalSessionWorkspaceError(external_id, record.workspace_path)
        if not isinstance(session.source, LocalMountSource) or (
            Path(session.source.local_path).resolve() != Path(record.workspace_path).resolve()
        ):
            raise HistoryImportValidationError("Native transcript workspace differs from session")
        entries = await self._read_history(prov, external_id, session.id)
        imported = await self._persist_history(session.id, prov, external_id, entries)
        return {
            "session_id": str(session.id),
            "provider": prov.name,
            "external_session_id": external_id,
            "imported_frames": imported,
            "source_frames": len(entries),
            "partial": any(
                entry.payload.get("metadata", {}).get("native_import", {}).get("partial", False)
                for entry in entries
            ),
        }

    async def _read_history(
        self, provider: ExternalSessionProvider, external_id: str, session_id: UUID
    ) -> list[SessionLogEntry]:
        if self._event_log is None:
            raise HistoryImportError("Durable native history import is not configured")
        try:
            entries = await provider.read_transcript(external_id, session_id)
        except NotImplementedError as exc:
            raise HistoryImportError("Provider does not support native history import") from exc
        except (OSError, ValueError) as exc:
            raise HistoryImportValidationError(f"Cannot read native transcript: {exc}") from exc
        if not entries:
            raise HistoryImportValidationError("Native transcript has no recoverable messages")
        return entries

    async def _persist_history(
        self,
        session_id: UUID,
        provider: ExternalSessionProvider,
        external_id: str,
        entries: list[SessionLogEntry],
    ) -> int:
        if self._event_log is None:
            raise HistoryImportError("Durable native history import is not configured")
        try:
            return await self._event_log.import_history(
                session_id, f"{provider.name}:{external_id}", entries
            )
        except HistoryImportError:
            raise
        except Exception as exc:
            logger.exception("Native history persistence failed for session %s", session_id)
            raise HistoryImportError(
                "Native history could not be committed; retry is safe"
            ) from exc

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
