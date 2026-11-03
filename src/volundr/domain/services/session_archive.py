"""Application service for file-backed session transcript and log archives."""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from niuu.domain.text_projection import projection_revision
from niuu.domain.transcript_rebuild import rebuild_turns
from volundr.domain.models import LocalMountSource
from volundr.domain.services.attachments_index import (
    FOLD_EXCLUDED_KINDS,
    AttachmentsFold,
    build_attachments_index,
)
from volundr.log_aggregate import aggregate_workspace_logs
from volundr.session_archive import (
    ArchivePathError,
    load_workspace_transcript,
    resolve_contained_path,
)

logger = logging.getLogger(__name__)

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
        attachments_cache_size: int = 256,
        attachments_page_size: int = 200,
        attachments_max_pending: int = 50_000,
        tail_initial_frames: int = 2000,
        tail_growth: int = 2,
        event_log_page_size: int = 5000,
    ) -> None:
        self._session_service = session_service
        self._storage = storage
        self._archive_store = archive_store
        self._chronicle_service = chronicle_service
        self._event_log_repository = event_log_repository
        # session id -> (durable head seq, incremental fold, index payload); LRU-bounded.
        self._attachments_cache: OrderedDict[str, tuple[int, AttachmentsFold, dict[str, Any]]] = (
            OrderedDict()
        )
        self._attachments_cache_size = attachments_cache_size
        self._attachments_page_size = attachments_page_size
        self._attachments_max_pending = attachments_max_pending
        self._attachments_locks: dict[str, asyncio.Lock] = {}
        # `tail_turns` rebuild (LEX-1345): first window and per-step growth, in frames.
        self._tail_initial_frames = tail_initial_frames
        self._tail_growth = tail_growth
        self._event_log_page_size = event_log_page_size

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

    async def get_transcript(self, session_id: UUID, *, tail_turns: int = 0) -> dict[str, Any]:
        """Return the persisted transcript payload for a session.

        ``tail_turns > 0`` is a hint that the caller only needs the newest turns:
        the durable event-log rebuild then reads only the log tail (see
        :meth:`_load_event_log_tail`). Other sources are single documents and are
        returned whole; the caller slices them.
        """
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

        if tail_turns > 0:
            event_log_transcript = await self._load_event_log_tail(session_id, tail_turns)
        else:
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
        if fmt not in {"json", "md"}:
            raise ValueError(f"Unsupported transcript format: {fmt}")
        session_id_str = str(session_id)
        _, candidates = await self._workspace_dir_candidates(session_id)

        for candidate in candidates:
            manifest = self._archive_store.load_manifest(
                session_id=session_id_str,
                workspace_dir=candidate,
            )
            if not self._manifest_declares_transcript(manifest, fmt):
                continue
            existing = self._transcript_artifact_path(
                session_id_str,
                fmt,
                workspace_dir=candidate,
            )
            if existing is not None:
                return existing

        manifest = self._archive_store.load_manifest(session_id=session_id_str)
        if self._manifest_declares_transcript(manifest, fmt):
            existing = self._transcript_artifact_path(session_id_str, fmt)
            if existing is not None:
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

    @staticmethod
    def _manifest_declares_transcript(manifest: dict[str, Any] | None, fmt: str) -> bool:
        """Return whether a trusted manifest declares the requested fixed artifact."""
        if not isinstance(manifest, dict):
            return False
        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, dict):
            return False
        expected = {
            "json": ("transcript_json", "transcript.json"),
            "md": ("transcript_md", "transcript.md"),
        }.get(fmt)
        if expected is None:
            return False
        key, filename = expected
        return artifacts.get(key) == filename

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

    async def get_attachments(self, session_id: UUID) -> dict[str, Any]:
        """Every document/image the session delivered, folded from the durable log.

        The durable event log is the source: it is the complete record, including the
        in-progress tail of a running session (an on-demand archive can predate a resume).
        The fold is cached per session: an unchanged head seq answers from memory, and a
        grown log re-reads only the frames after the last settled turn. Per-session
        single-flight keeps concurrent cold calls to one fold.
        """
        sid = str(session_id)
        lock = self._attachments_locks.setdefault(sid, asyncio.Lock())
        async with lock:
            head = await self.latest_event_seq(session_id)
            cached = self._attachments_cache.get(sid)
            if cached is not None and head > 0 and cached[0] == head:
                self._attachments_cache.move_to_end(sid)
                return cached[2]
            if self._event_log_repository is None or head <= 0:
                return await self._transcript_attachments(session_id)
            fold = (
                cached[1]
                if cached is not None
                else AttachmentsFold(sid, max_pending=self._attachments_max_pending)
            )
            pending: list = []
            after = fold.resume_after
            while True:
                page = await self._event_log_repository.read_after_excluding(
                    session_id,
                    after_seq=after,
                    limit=self._attachments_page_size,
                    exclude_kinds=FOLD_EXCLUDED_KINDS,
                )
                if not page:
                    break
                after = page[-1].seq
                pending = await asyncio.to_thread(fold.settle, [*pending, *page])
            items, total_turns = await asyncio.to_thread(fold.items, pending)
            payload = {
                "session_id": sid,
                "revision": str(head),
                "complete": not fold.truncated,
                "total_turns": total_turns,
                "items": items,
            }
            self._attachments_cache[sid] = (head, fold, payload)
            self._attachments_cache.move_to_end(sid)
            while len(self._attachments_cache) > self._attachments_cache_size:
                evicted, _ = self._attachments_cache.popitem(last=False)
                self._attachments_locks.pop(evicted, None)
            return payload

    async def _transcript_attachments(self, session_id: UUID) -> dict[str, Any]:
        """Sessions with no durable log: index the stored archive/workspace transcript."""
        sid = str(session_id)
        try:
            transcript = await self.get_transcript(session_id)
        except SessionArchiveNotAvailableError:
            transcript = {}
        turns = transcript.get("turns") if isinstance(transcript, dict) else None
        turns = turns if isinstance(turns, list) else []
        return {
            "session_id": sid,
            "revision": projection_revision(turns),
            "complete": True,
            "total_turns": len(turns),
            "items": await asyncio.to_thread(build_attachments_index, turns, sid),
        }

    async def _load_event_log_transcript(
        self, session_id: UUID, max_frames: int = 50_000
    ) -> dict[str, Any] | None:
        """Rebuild a stopped-session transcript from the durable event log.

        BUG-2: the durable ``session_event_log`` holds the COMPLETE work (assistant /
        content_block_delta / result / terminal_frame frames), not just finished
        ``conversation.turn`` rows. A tmux session that crashes mid-turn never produces a
        ``conversation.turn`` for its open work, so the old "conversation.turn-only" read
        returned nothing. Page in the ordered frame list and hand it to the pure
        reducer (which mirrors the broker's live folding and surfaces interrupted turns).

        TAIL-BOUNDED (2026-07-12, the "stale first-50k transcript" fix): the ``max_frames``
        ceiling used to keep the FIRST 50k frames — on a huge session (lexi-frontend-
        presentation: 216,918 frames) the fallback served the transcript as it looked DAYS
        ago (27 turns of a 112-turn session, verified against the phone's poisoned cache
        anchor). A bounded read must keep the NEWEST frames: when the log exceeds the
        ceiling, start from ``latest_seq - max_frames`` so the rebuild covers the recent
        tail. The leading cut can land mid-turn — the reducer's normal interrupted-turn
        handling absorbs that fragment; ``total_turns`` under-reports deep history (the
        windowed client shows fewer "earlier" turns in this degraded mode), which is the
        right trade against showing days-stale content as current.
        """
        if self._event_log_repository is None:
            return None

        after_seq = 0
        try:
            latest = await self._event_log_repository.latest_seq(session_id)
        except Exception:  # tolerate repos without a fast MAX(seq) — fall back to head read
            latest = 0
        if latest > max_frames:
            after_seq = latest - max_frames
            logger.warning(
                "Event-log rebuild for session %s truncated to the last %d of %d frames",
                str(session_id).replace("\n", "\\n").replace("\r", "\\r"),
                int(max_frames),
                int(latest),
            )

        all_entries: list = []
        limit = 5000
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
            "projection_revision": projection_revision(result.turns),
            "is_active": False,
            "last_activity": "",
        }

    async def _load_event_log_tail(
        self, session_id: UUID, tail_turns: int, max_frames: int = 50_000
    ) -> dict[str, Any] | None:
        """Rebuild only the newest ``tail_turns`` turns from the durable event log.

        LEX-1345: the full rebuild decodes up to ``max_frames`` frames on every read
        (0.7-3.7 s on Thor's stopped sessions, 80k-250k frames). Doubling, not a
        larger factor: on a sparse log (37 turns in 50k frames) the window that first
        holds the tail is what dominates the cost. A caller asking for the
        last few turns needs only the end of the log, so read backwards in widening
        windows (by default 2k, 4k, 8k, ... frames, each step adding only the older
        frames it lacks) and stop once the reduced window holds MORE than ``tail_turns``
        turns.
        The surplus matters: the oldest turn of a window that starts mid-log can be a
        fragment cut at the boundary, and it is the one sliced away. Every returned
        turn starts after the cut, so it is complete. The window never grows past
        ``max_frames``, the same ceiling as the full rebuild.

        The payload marks ``history_tail``: its turns are the newest of the session but
        carry no absolute index, because the turns before the window were never counted.
        """
        if self._event_log_repository is None:
            return None
        latest = await self._event_log_repository.latest_seq(session_id)
        if latest <= 0:
            return None  # no durable frames: the same answer as the full rebuild

        entries: list = []
        low = latest  # frames with seq in (low, latest] are loaded
        window = min(self._tail_initial_frames, max_frames)
        while True:
            target_low = max(0, latest - window)
            entries = [*await self._read_seq_range(session_id, target_low, low), *entries]
            low = target_low
            result = rebuild_turns(entries)
            if low == 0 or len(result.turns) > tail_turns or window >= max_frames:
                break
            window = min(window * self._tail_growth, max_frames)

        turns = result.turns[-tail_turns:]
        if not turns:
            return None
        return {
            "turns": turns,
            "projection_revision": projection_revision(turns),
            "is_active": False,
            "last_activity": "",
            "history_tail": True,
        }

    async def _read_seq_range(self, session_id: UUID, after_seq: int, upto_seq: int) -> list:
        """Durable frames with ``after_seq < seq <= upto_seq``, ordered by seq."""
        assert self._event_log_repository is not None
        out: list = []
        cursor = after_seq
        while cursor < upto_seq:
            want = min(self._event_log_page_size, upto_seq - cursor)
            page = await self._event_log_repository.read_after(
                session_id, after_seq=cursor, limit=want
            )
            if not page:
                break
            out.extend(entry for entry in page if int(entry.seq) <= upto_seq)
            cursor = max(cursor, max(int(entry.seq) for entry in page))
            if len(page) < want:
                break
        return out

    def _transcript_artifact_path(
        self,
        session_id: str,
        fmt: str,
        *,
        workspace_dir: Path | None = None,
    ) -> Path | None:
        if fmt not in {"json", "md"}:
            raise ValueError(f"Unsupported transcript format: {fmt}")
        try:
            root = self._archive_store.archive_root(
                session_id=session_id,
                workspace_dir=workspace_dir,
            )
            if fmt == "json":
                artifact = self._archive_store.transcript_json_path(
                    session_id=session_id,
                    workspace_dir=workspace_dir,
                )
                return resolve_contained_path(root, artifact)
            if fmt == "md":
                artifact = self._archive_store.transcript_markdown_path(
                    session_id=session_id,
                    workspace_dir=workspace_dir,
                )
                return resolve_contained_path(root, artifact)
        except ArchivePathError:
            raise
        except ValueError:
            if workspace_dir is None:
                return None
            raise
        raise AssertionError("Unreachable transcript format fallthrough")

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
        timeline = await self._chronicle_service.session_timeline(session_id)
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
