"""Domain service for session chronicles."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from volundr.domain.models import (
    Chronicle,
    ChronicleStatus,
    CommitSummary,
    FileSummary,
    GitSource,
    Session,
    TimelineEvent,
    TimelineResponse,
)
from volundr.domain.ports import (
    ChronicleRepository,
    EventBroadcaster,
    TimelineRepository,
)

from .session import SessionNotFoundError, SessionService

logger = logging.getLogger(__name__)


def _sanitize_log(value: object) -> str:
    """Sanitize a value for safe log output (prevent log injection)."""
    return str(value).replace("\n", "\\n").replace("\r", "\\r")


class ChronicleNotFoundError(Exception):
    """Raised when a chronicle is not found."""

    def __init__(self, chronicle_id: UUID):
        self.chronicle_id = chronicle_id
        super().__init__(f"Chronicle not found: {chronicle_id}")


def _detect_git_info(path: str) -> dict[str, str]:
    """Detect git remote, branch, and project name from a local path.

    Uses GitPython to inspect the repo. Returns empty dict if not a git repo.
    """
    result: dict[str, str] = {}
    try:
        from git import InvalidGitRepositoryError, Repo

        repo = Repo(path, search_parent_directories=True)

        try:
            result["branch"] = repo.active_branch.name
        except TypeError:
            # Detached HEAD
            pass

        if repo.remotes:
            url = repo.remotes[0].url
            result["remote"] = url
            result["project"] = url.rstrip("/").split("/")[-1].replace(".git", "")
    except (InvalidGitRepositoryError, Exception):
        pass
    return result


class ChronicleService:
    """Service for managing session chronicles."""

    def __init__(
        self,
        chronicle_repository: ChronicleRepository,
        session_service: SessionService,
        broadcaster: EventBroadcaster | None = None,
        timeline_repository: TimelineRepository | None = None,
    ):
        self._chronicle_repository = chronicle_repository
        self._session_service = session_service
        self._broadcaster = broadcaster
        self._timeline_repository = timeline_repository

    async def create_chronicle(self, session_id: UUID) -> Chronicle:
        """Create a chronicle from a session's current state.

        Captures session metadata into a chronicle entry.
        """
        session = await self._session_service.get_session(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)

        # Derive project/repo/branch from source type
        from volundr.domain.models import LocalMountSource

        if isinstance(session.source, LocalMountSource) and session.source.local_path:
            local_path = session.source.local_path
            git_info = _detect_git_info(local_path)
            dir_name = local_path.rstrip("/").split("/")[-1]
            project = git_info.get("project") or dir_name or session.name
            repo = git_info.get("remote") or local_path
            branch = git_info.get("branch") or "local"
        else:
            repo = session.repo or session.name
            branch = session.branch or "main"
            project = repo.rstrip("/").split("/")[-1].replace(".git", "") or session.name

        config_snapshot = {
            "name": session.name,
            "model": session.model,
            "repo": repo,
            "branch": branch,
        }

        chronicle = Chronicle(
            session_id=session_id,
            status=ChronicleStatus.DRAFT,
            project=project,
            repo=repo,
            branch=branch,
            model=session.model,
            config_snapshot=config_snapshot,
            token_usage=session.tokens_used,
        )

        created = await self._chronicle_repository.create(chronicle)
        logger.info(
            "Chronicle created: id=%s, session=%s, project=%s",
            _sanitize_log(created.id),
            _sanitize_log(session_id),
            _sanitize_log(project),
        )
        return created

    async def get_chronicle(self, chronicle_id: UUID) -> Chronicle | None:
        """Get a chronicle by ID."""
        return await self._chronicle_repository.get(chronicle_id)

    async def get_chronicle_by_session(self, session_id: UUID) -> Chronicle | None:
        """Get the most recent chronicle for a session."""
        return await self._chronicle_repository.get_by_session(session_id)

    async def list_chronicles(
        self,
        project: str | None = None,
        repo: str | None = None,
        model: str | None = None,
        tags: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Chronicle]:
        """List chronicles with optional filters."""
        return await self._chronicle_repository.list(
            project=project,
            repo=repo,
            model=model,
            tags=tags,
            limit=limit,
            offset=offset,
        )

    async def update_chronicle(
        self,
        chronicle_id: UUID,
        summary: str | None = None,
        key_changes: list[str] | None = None,
        unfinished_work: str | None = None,
        tags: list[str] | None = None,
        status: ChronicleStatus | None = None,
    ) -> Chronicle:
        """Update a chronicle's mutable fields."""
        chronicle = await self._chronicle_repository.get(chronicle_id)
        if chronicle is None:
            raise ChronicleNotFoundError(chronicle_id)

        updates: dict = {"updated_at": datetime.now(UTC)}
        if summary is not None:
            updates["summary"] = summary
        if key_changes is not None:
            updates["key_changes"] = key_changes
        if unfinished_work is not None:
            updates["unfinished_work"] = unfinished_work
        if tags is not None:
            updates["tags"] = tags
        if status is not None:
            updates["status"] = status

        updated = chronicle.model_copy(update=updates)
        result = await self._chronicle_repository.update(updated)
        logger.info("Chronicle updated: id=%s", _sanitize_log(chronicle_id))
        return result

    async def delete_chronicle(self, chronicle_id: UUID) -> bool:
        """Delete a chronicle."""
        deleted = await self._chronicle_repository.delete(chronicle_id)
        if deleted:
            logger.info("Chronicle deleted: id=%s", _sanitize_log(chronicle_id))
        return deleted

    async def create_or_update_from_broker(
        self,
        session_id: UUID,
        summary: str | None = None,
        key_changes: list[str] | None = None,
        unfinished_work: str | None = None,
        duration_seconds: int | None = None,
    ) -> Chronicle:
        """Create or update a chronicle from broker-reported data.

        Idempotent: if a DRAFT chronicle already exists for this session,
        it is enriched with the supplied data.  Otherwise a new DRAFT is
        created from the session's current state.

        This is the ingestion point for the broker's ``_report_chronicle``
        POST at shutdown time.
        """
        existing = await self._chronicle_repository.get_by_session(session_id)

        if existing is not None and existing.status == ChronicleStatus.DRAFT:
            updates: dict = {"updated_at": datetime.now(UTC)}
            if summary is not None:
                updates["summary"] = summary
            if key_changes is not None:
                updates["key_changes"] = key_changes
            if unfinished_work is not None:
                updates["unfinished_work"] = unfinished_work
            if duration_seconds is not None:
                updates["duration_seconds"] = duration_seconds

            updated = existing.model_copy(update=updates)
            result = await self._chronicle_repository.update(updated)
            logger.info(
                "Chronicle enriched from broker: id=%s, session=%s",
                _sanitize_log(result.id),
                _sanitize_log(session_id),
            )
            return result

        # No existing draft — create a fresh chronicle
        chronicle = await self.create_chronicle(session_id)

        # Apply broker data on top of the freshly-created chronicle
        updates = {"updated_at": datetime.now(UTC)}
        if summary is not None:
            updates["summary"] = summary
        if key_changes is not None:
            updates["key_changes"] = key_changes
        if unfinished_work is not None:
            updates["unfinished_work"] = unfinished_work
        if duration_seconds is not None:
            updates["duration_seconds"] = duration_seconds

        if len(updates) > 1:  # more than just updated_at
            enriched = chronicle.model_copy(update=updates)
            chronicle = await self._chronicle_repository.update(enriched)

        logger.info(
            "Chronicle created from broker: id=%s, session=%s",
            _sanitize_log(chronicle.id),
            _sanitize_log(session_id),
        )
        return chronicle

    async def reforge(self, chronicle_id: UUID) -> Session:
        """Relaunch a session from a chronicle entry.

        Creates a new session with the same configuration as the original,
        linking it to the parent chronicle for traceability.
        """
        chronicle = await self._chronicle_repository.get(chronicle_id)
        if chronicle is None:
            raise ChronicleNotFoundError(chronicle_id)

        config = chronicle.config_snapshot
        name = config.get("name", f"Reforged: {chronicle.project}")
        model = config.get("model", chronicle.model)
        repo = config.get("repo", chronicle.repo)
        branch = config.get("branch", chronicle.branch)

        session = await self._session_service.create_session(
            name=f"{name} (reforged)",
            model=model,
            source=GitSource(repo=repo, branch=branch),
        )

        logger.info(
            "Session reforged: chronicle=%s -> session=%s",
            _sanitize_log(chronicle_id),
            _sanitize_log(session.id),
        )
        return session

    async def get_chain(self, chronicle_id: UUID) -> list[Chronicle]:
        """Get the full reforge chain for a chronicle."""
        return await self._chronicle_repository.get_chain(chronicle_id)

    async def add_timeline_event(self, session_id: UUID, event: TimelineEvent) -> TimelineEvent:
        """Add a timeline event for a session's chronicle.

        Persists the event and publishes it via SSE if a broadcaster is
        configured.
        """
        if self._timeline_repository is None:
            raise RuntimeError("Timeline repository not configured")

        stored = await self._timeline_repository.add_event(event)
        logger.info(
            "Timeline event added: session=%s, type=%s, t=%d",
            _sanitize_log(session_id),
            _sanitize_log(event.type.value),
            event.t,
        )

        if self._broadcaster is not None:
            timeline = await self._build_timeline(event.chronicle_id, session_id)
            await self._broadcaster.publish_chronicle_event(
                session_id=session_id,
                event=stored,
                timeline=timeline,
            )

        return stored

    async def get_timeline(self, session_id: UUID) -> TimelineResponse | None:
        """Get the full timeline for a session.

        Returns None if no chronicle or no timeline repository is configured.
        """
        if self._timeline_repository is None:
            return None

        chronicle = await self._chronicle_repository.get_by_session(session_id)
        if chronicle is None:
            return None

        return await self._build_timeline(chronicle.id, session_id)

    async def _build_timeline(self, chronicle_id: UUID, session_id: UUID) -> TimelineResponse:
        """Build a full TimelineResponse from stored events."""
        events = await self._timeline_repository.get_events(chronicle_id)
        files = self._aggregate_files(events)
        commits = self._aggregate_commits(events)
        token_burn = self._aggregate_token_burn(events)

        return TimelineResponse(
            events=events,
            files=files,
            commits=commits,
            token_burn=token_burn,
        )

    @staticmethod
    def _aggregate_files(events: list[TimelineEvent]) -> list[FileSummary]:
        """Aggregate file events into deduplicated file summaries."""
        file_map: dict[str, dict] = {}
        for ev in events:
            if ev.type.value != "file":
                continue
            path = ev.label
            if path not in file_map:
                file_map[path] = {
                    "status": "new" if ev.action == "created" else "mod",
                    "ins": 0,
                    "del": 0,
                }
            entry = file_map[path]
            entry["ins"] += ev.ins or 0
            entry["del"] += ev.del_ or 0
            if ev.action == "deleted":
                entry["status"] = "del"
            elif ev.action == "created" and entry["status"] != "del":
                entry["status"] = "new"

        return [
            FileSummary(path=path, status=d["status"], ins=d["ins"], del_=d["del"])
            for path, d in file_map.items()
        ]

    @staticmethod
    def _aggregate_commits(events: list[TimelineEvent]) -> list[CommitSummary]:
        """Extract git events into commit summaries, newest first."""
        commits = []
        for ev in events:
            if ev.type.value != "git":
                continue
            if ev.hash is None:
                continue
            time_str = ""
            if ev.created_at is not None:
                time_str = ev.created_at.strftime("%H:%M")
            commits.append(CommitSummary(hash=ev.hash[:7], msg=ev.label, time=time_str))
        commits.reverse()
        return commits

    @staticmethod
    def _aggregate_token_burn(events: list[TimelineEvent]) -> list[int]:
        """Bucket message tokens into 5-minute intervals."""
        if not events:
            return []

        max_t = max(ev.t for ev in events)
        bucket_count = (max_t // 300) + 1
        buckets = [0] * bucket_count

        for ev in events:
            if ev.type.value != "message":
                continue
            if ev.tokens is None:
                continue
            bucket_idx = ev.t // 300
            if bucket_idx < bucket_count:
                buckets[bucket_idx] += ev.tokens

        return buckets
