"""Application service for file-backed session transcript and log archives."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from volundr.domain.models import LocalMountSource
from volundr.log_aggregate import aggregate_workspace_logs
from volundr.session_archive import load_workspace_transcript

if TYPE_CHECKING:
    from uuid import UUID

    from volundr.domain.ports import ArchiveStorePort, StoragePort
    from volundr.domain.services.chronicle import ChronicleService
    from volundr.domain.services.session import SessionService


class SessionArchiveNotAvailableError(RuntimeError):
    """Raised when a session archive cannot be resolved from workspace storage."""


class SessionArchiveService:
    """Serve stopped-session transcript and logs from workspace-backed storage."""

    def __init__(
        self,
        session_service: SessionService,
        storage: StoragePort,
        archive_store: ArchiveStorePort,
        *,
        chronicle_service: ChronicleService | None = None,
    ) -> None:
        self._session_service = session_service
        self._storage = storage
        self._archive_store = archive_store
        self._chronicle_service = chronicle_service

    async def resolve_workspace_dir(self, session_id: UUID) -> Path:
        """Resolve a workspace path for a session from storage or local endpoints."""
        session = await self._session_service.get_session(session_id)
        if session is None:
            raise LookupError(f"Session not found: {session_id}")

        resolved = self._storage.resolve_session_workspace_path(str(session_id))
        if resolved:
            path = Path(resolved)
            if path.exists():
                return path

        code_endpoint = session.code_endpoint
        if code_endpoint:
            try:
                parsed = urlsplit(code_endpoint)
            except ValueError:
                parsed = None
            if parsed and parsed.scheme == "file" and parsed.path:
                path = Path(parsed.path)
                if path.exists():
                    return path

        if isinstance(session.source, LocalMountSource) and session.source.local_path:
            path = Path(session.source.local_path)
            if path.exists():
                return path

        workspace = await self._storage.get_workspace_by_session(str(session_id))
        if workspace is not None:
            pvc_path = Path(workspace.pvc_name)
            if pvc_path.is_absolute() and pvc_path.exists():
                return pvc_path

        raise SessionArchiveNotAvailableError(
            f"No accessible workspace path for session {session_id}"
        )

    async def get_transcript(self, session_id: UUID) -> dict[str, Any]:
        """Return the persisted transcript payload for a session."""
        workspace_dir = await self.resolve_workspace_dir(session_id)
        return load_workspace_transcript(workspace_dir, str(session_id))

    async def get_logs(
        self,
        session_id: UUID,
        *,
        lines: int = 200,
        level: str = "DEBUG",
        participants: set[str] | None = None,
        query: str = "",
    ) -> dict[str, Any]:
        """Return aggregated logs directly from the workspace."""
        workspace_dir = await self.resolve_workspace_dir(session_id)
        payload = aggregate_workspace_logs(
            workspace_dir,
            lines=lines,
            level=level,
            participants=participants,
            query=query,
        )
        payload["session_id"] = str(session_id)
        return payload

    async def build_archive(self, session_id: UUID, *, force: bool = False) -> dict[str, Any]:
        """Materialize a normalized archive directory inside the workspace."""
        workspace_dir = await self.resolve_workspace_dir(session_id)
        if not force:
            manifest = self._archive_store.load_manifest(
                session_id=str(session_id),
                workspace_dir=workspace_dir,
            )
            if manifest is not None:
                return manifest

        transcript_payload = load_workspace_transcript(workspace_dir, str(session_id))
        aggregated_logs = aggregate_workspace_logs(workspace_dir, lines=5000, level="DEBUG")
        chronicle_payload = await self._load_chronicle_payload(session_id)
        timeline_payload = await self._load_timeline_payload(session_id)

        return self._archive_store.write_archive(
            session_id=str(session_id),
            workspace_dir=workspace_dir,
            transcript_payload=transcript_payload,
            aggregated_logs=aggregated_logs,
            chronicle_payload=chronicle_payload,
            timeline_payload=timeline_payload,
        )

    async def get_archive_manifest(self, session_id: UUID) -> dict[str, Any]:
        """Return the current archive manifest, building it on demand."""
        workspace_dir = await self.resolve_workspace_dir(session_id)
        manifest = self._archive_store.load_manifest(
            session_id=str(session_id),
            workspace_dir=workspace_dir,
        )
        if manifest is not None:
            return manifest
        return await self.build_archive(session_id)

    async def get_transcript_download_path(self, session_id: UUID, fmt: str) -> Path:
        """Return a local file path for transcript download."""
        workspace_dir = await self.resolve_workspace_dir(session_id)
        await self.build_archive(session_id)

        if fmt == "json":
            return self._archive_store.transcript_json_path(
                session_id=str(session_id),
                workspace_dir=workspace_dir,
            )
        if fmt == "md":
            return self._archive_store.transcript_markdown_path(
                session_id=str(session_id),
                workspace_dir=workspace_dir,
            )
        raise ValueError(f"Unsupported transcript format: {fmt}")

    async def get_archive_root(self, session_id: UUID) -> Path:
        """Return the archive root after ensuring it exists."""
        workspace_dir = await self.resolve_workspace_dir(session_id)
        await self.build_archive(session_id)
        return self._archive_store.archive_root(
            session_id=str(session_id),
            workspace_dir=workspace_dir,
        )

    async def _load_chronicle_payload(self, session_id: UUID) -> dict[str, Any] | None:
        if self._chronicle_service is None:
            return None
        chronicle = await self._chronicle_service.get_chronicle_by_session(session_id)
        if chronicle is None:
            return None
        return {
            "id": str(chronicle.id),
            "session_id": str(chronicle.session_id) if chronicle.session_id else None,
            "status": chronicle.status.value,
            "project": chronicle.project,
            "repo": chronicle.repo,
            "branch": chronicle.branch,
            "model": chronicle.model,
            "config_snapshot": chronicle.config_snapshot,
            "summary": chronicle.summary,
            "key_changes": chronicle.key_changes,
            "unfinished_work": chronicle.unfinished_work,
            "token_usage": chronicle.token_usage,
            "cost": float(chronicle.cost) if chronicle.cost is not None else None,
            "duration_seconds": chronicle.duration_seconds,
            "tags": chronicle.tags,
            "parent_chronicle_id": (
                str(chronicle.parent_chronicle_id) if chronicle.parent_chronicle_id else None
            ),
            "created_at": chronicle.created_at.isoformat(),
            "updated_at": chronicle.updated_at.isoformat(),
        }

    async def _load_timeline_payload(self, session_id: UUID) -> dict[str, Any] | None:
        if self._chronicle_service is None:
            return None
        timeline = await self._chronicle_service.get_timeline(session_id)
        if timeline is None:
            return None
        return {
            "events": [
                {
                    "t": event.t,
                    "type": event.type.value,
                    "label": event.label,
                    "tokens": event.tokens,
                    "action": event.action,
                    "ins": event.ins,
                    "del": event.del_,
                    "hash": event.hash,
                    "exit": event.exit_code,
                }
                for event in timeline.events
            ],
            "files": [
                {
                    "path": file.path,
                    "status": file.status,
                    "ins": file.ins,
                    "del": file.del_,
                }
                for file in timeline.files
            ],
            "commits": [
                {"hash": commit.hash, "msg": commit.msg, "time": commit.time}
                for commit in timeline.commits
            ],
            "token_burn": timeline.token_burn,
        }
