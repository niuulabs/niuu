"""Application service for file-backed session transcript and log archives."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from volundr.domain.models import LocalMountSource
from volundr.domain.services.transcript_rebuild import rebuild_turns
from volundr.log_aggregate import aggregate_workspace_logs
from volundr.session_archive import load_workspace_transcript

if TYPE_CHECKING:
    from uuid import UUID

    from volundr.domain.models import Session
    from volundr.domain.ports import ArchiveStorePort, SessionEventLogRepository, StoragePort
    from volundr.domain.services.chronicle import ChronicleService
    from volundr.domain.services.session import SessionService


class SessionArchiveNotAvailableError(RuntimeError):
    """Raised when a session archive cannot be resolved from workspace storage."""


def _payload_has_turns(payload: Any) -> bool:
    """True when a transcript payload carries at least one renderable turn.

    BUG-2: an EMPTY archive/workspace result ({"turns": []}) must not pre-empt the
    durable-log reducer for the tmux/crash sessions we need to rebuild.
    """
    return isinstance(payload, dict) and bool(payload.get("turns"))


class SessionArchiveService:
    """Serve stopped-session transcript and logs from workspace-backed storage."""

    def __init__(
        self,
        session_service: SessionService,
        storage: StoragePort,
        archive_store: ArchiveStorePort,
        *,
        chronicle_service: ChronicleService | None = None,
        event_log_repository: SessionEventLogRepository | None = None,
    ) -> None:
        self._session_service = session_service
        self._storage = storage
        self._archive_store = archive_store
        self._chronicle_service = chronicle_service
        self._event_log_repository = event_log_repository

    async def resolve_workspace_dir(self, session_id: UUID) -> Path:
        """Resolve the first accessible workspace path for a session."""
        _, candidates = await self._workspace_dir_candidates(session_id)
        for candidate in candidates:
            if candidate.exists():
                return candidate
        raise SessionArchiveNotAvailableError(
            f"No accessible workspace path for session {session_id}"
        )

    async def latest_event_seq(self, session_id: UUID) -> int:
        """Cheap durable-freshness signal: the MAX(seq) of the session's event log (0 when none).

        A stable seq guarantees the durable transcript hasn't grown, so a cached turn count keyed
        on it stays valid — lets the read path skip the expensive `get_transcript` rebuild when the
        durable log is unchanged (see the FAULT-C reconciliation in the REST adapter)."""
        if self._event_log_repository is None:
            return 0
        try:
            return await self._event_log_repository.latest_seq(session_id)
        except Exception:  # pragma: no cover - a freshness probe must never fail the read
            return 0

    async def get_transcript(self, session_id: UUID) -> dict[str, Any]:
        """Return the persisted transcript payload for a session."""
        session_id_str = str(session_id)
        _, candidates = await self._workspace_dir_candidates(session_id)

        for candidate in candidates:
            archived = self._archive_store.load_transcript(
                session_id=session_id_str,
                workspace_dir=candidate,
            )
            if _payload_has_turns(archived):
                return archived

        archived = self._archive_store.load_transcript(session_id=session_id_str)
        if _payload_has_turns(archived):
            return archived

        event_log_transcript = await self._load_event_log_transcript(session_id)
        if event_log_transcript is not None:
            return event_log_transcript

        for candidate in candidates:
            if not candidate.exists():
                continue
            return load_workspace_transcript(candidate, session_id_str)

        raise SessionArchiveNotAvailableError(
            f"No accessible workspace path for session {session_id}"
        )

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
        session_id_str = str(session_id)
        _, candidates = await self._workspace_dir_candidates(session_id)

        for candidate in candidates:
            archived = self._archive_store.load_aggregated_logs(
                session_id=session_id_str,
                workspace_dir=candidate,
            )
            if archived is None:
                continue
            archived["session_id"] = session_id_str
            return archived

        archived = self._archive_store.load_aggregated_logs(session_id=session_id_str)
        if archived is not None:
            archived["session_id"] = session_id_str
            return archived

        for candidate in candidates:
            if not candidate.exists():
                continue
            payload = aggregate_workspace_logs(
                candidate,
                lines=lines,
                level=level,
                participants=participants,
                query=query,
            )
            payload["session_id"] = session_id_str
            return payload

        raise SessionArchiveNotAvailableError(
            f"No accessible workspace path for session {session_id}"
        )

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
        session_id_str = str(session_id)
        _, candidates = await self._workspace_dir_candidates(session_id)

        for candidate in candidates:
            manifest = self._archive_store.load_manifest(
                session_id=session_id_str,
                workspace_dir=candidate,
            )
            if manifest is not None:
                return manifest

        manifest = self._archive_store.load_manifest(session_id=session_id_str)
        if manifest is not None:
            return manifest

        return await self.build_archive(session_id)

    async def get_transcript_download_path(self, session_id: UUID, fmt: str) -> Path:
        """Return a local file path for transcript download."""
        session_id_str = str(session_id)
        _, candidates = await self._workspace_dir_candidates(session_id)

        for candidate in candidates:
            existing = self._transcript_artifact_path(
                session_id_str,
                fmt,
                workspace_dir=candidate,
            )
            if existing is not None and existing.exists():
                return existing

        existing = self._transcript_artifact_path(session_id_str, fmt)
        if existing is not None and existing.exists():
            return existing

        workspace_dir = await self.resolve_workspace_dir(session_id)
        await self.build_archive(session_id)

        final_path = self._transcript_artifact_path(
            session_id_str,
            fmt,
            workspace_dir=workspace_dir,
        )
        if final_path is None:
            raise SessionArchiveNotAvailableError(
                f"No transcript artifact available for session {session_id}"
            )
        return final_path

    async def get_archive_root(self, session_id: UUID) -> Path:
        """Return the archive root after ensuring it exists."""
        session_id_str = str(session_id)
        _, candidates = await self._workspace_dir_candidates(session_id)

        for candidate in candidates:
            manifest = self._archive_store.load_manifest(
                session_id=session_id_str,
                workspace_dir=candidate,
            )
            if manifest is None:
                continue
            return self._archive_store.archive_root(
                session_id=session_id_str,
                workspace_dir=candidate,
            )

        manifest = self._archive_store.load_manifest(session_id=session_id_str)
        if manifest is not None:
            return self._archive_store.archive_root(session_id=session_id_str)

        workspace_dir = await self.resolve_workspace_dir(session_id)
        await self.build_archive(session_id)
        return self._archive_store.archive_root(
            session_id=session_id_str,
            workspace_dir=workspace_dir,
        )

    async def _workspace_dir_candidates(self, session_id: UUID) -> tuple[Session, list[Path]]:
        session = await self._session_service.get_session(session_id)
        if session is None:
            raise LookupError(f"Session not found: {session_id}")

        candidates: list[Path] = []
        seen: set[Path] = set()

        def add_candidate(raw_path: str | Path | None) -> None:
            if not raw_path:
                return
            path = Path(raw_path)
            if path in seen:
                return
            seen.add(path)
            candidates.append(path)

        add_candidate(self._storage.resolve_session_workspace_path(str(session_id)))

        code_endpoint = session.code_endpoint
        if code_endpoint:
            try:
                parsed = urlsplit(code_endpoint)
            except ValueError:
                parsed = None
            if parsed and parsed.scheme == "file" and parsed.path:
                add_candidate(parsed.path)

        if isinstance(session.source, LocalMountSource) and session.source.local_path:
            add_candidate(session.source.local_path)

        workspace = await self._storage.get_workspace_by_session(str(session_id))
        if workspace is not None:
            pvc_path = Path(workspace.pvc_name)
            if pvc_path.is_absolute():
                add_candidate(pvc_path)

        return session, candidates

    async def _load_event_log_transcript(self, session_id: UUID) -> dict[str, Any] | None:
        """Rebuild a stopped-session transcript from the durable event log.

        BUG-2: the durable ``session_event_log`` holds the COMPLETE work (assistant /
        content_block_delta / result / terminal_frame frames), not just finished
        ``conversation.turn`` rows. A tmux session that crashes mid-turn never produces a
        ``conversation.turn`` for its open work, so the old "conversation.turn-only" read
        returned nothing. Page in the full ordered frame list and hand it to the pure
        reducer (which mirrors the broker's live folding and surfaces interrupted turns).
        """
        if self._event_log_repository is None:
            return None

        all_entries: list = []
        after_seq = 0
        limit = 5000
        max_frames = 50_000  # hard ceiling — bounded read for huge sessions
        while True:
            entries = await self._event_log_repository.read_after(
                session_id,
                after_seq=after_seq,
                limit=limit,
            )
            if not entries:
                break
            for entry in entries:
                after_seq = max(after_seq, int(entry.seq))
            all_entries.extend(entries)
            if len(entries) < limit or len(all_entries) >= max_frames:
                break

        result = rebuild_turns(all_entries)
        if not result.turns:
            return None  # preserve the existing None -> fall-through contract
        return {
            "turns": result.turns,
            "is_active": False,
            "last_activity": "",
        }

    def _transcript_artifact_path(
        self,
        session_id: str,
        fmt: str,
        *,
        workspace_dir: Path | None = None,
    ) -> Path | None:
        if fmt == "json":
            try:
                return self._archive_store.transcript_json_path(
                    session_id=session_id,
                    workspace_dir=workspace_dir,
                )
            except ValueError:
                if workspace_dir is None:
                    return None
                raise
        if fmt == "md":
            try:
                return self._archive_store.transcript_markdown_path(
                    session_id=session_id,
                    workspace_dir=workspace_dir,
                )
            except ValueError:
                if workspace_dir is None:
                    return None
                raise
        raise ValueError(f"Unsupported transcript format: {fmt}")

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
