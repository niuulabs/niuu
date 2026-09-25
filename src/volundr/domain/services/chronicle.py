"""Domain service for session chronicles."""

from __future__ import annotations

import logging
from contextlib import suppress
from datetime import UTC, datetime
from uuid import UUID, uuid4

from volundr.domain.models import (
    Chronicle,
    ChronicleStatus,
    CommitSummary,
    FileSummary,
    GitSource,
    LocalMountSource,
    Principal,
    Session,
    TimelineEvent,
    TimelineEventType,
    TimelineResponse,
)
from volundr.domain.ports import (
    ChronicleRepository,
    EventBroadcaster,
    Resource,
    TimelineRepository,
)

from .session import SessionAccessDeniedError, SessionNotFoundError, SessionService

logger = logging.getLogger(__name__)

_Scope = tuple[str | None, str | None]


def _sanitize_log(value: object) -> str:
    """Sanitize a value for safe log output (prevent log injection)."""

    return str(value).replace("\n", "\\n").replace("\r", "\\r")


def _resource_key(resource: Resource) -> tuple[str, str, object, object]:
    return (
        resource.kind,
        resource.id,
        resource.attr.get("owner_id"),
        resource.attr.get("tenant_id"),
    )


class ChronicleNotFoundError(Exception):
    """Raised when a chronicle is not found."""

    def __init__(self, chronicle_id: UUID):
        self.chronicle_id = chronicle_id
        super().__init__(f"Chronicle not found: {chronicle_id}")


class ChronicleAccessDeniedError(Exception):
    """Raised when a principal may see a chronicle but not perform an action on it."""

    def __init__(self, chronicle_id: UUID, action: str):
        self.chronicle_id = chronicle_id
        self.action = action
        super().__init__(f"Not authorized to {action} chronicle {chronicle_id}")


def _detect_git_info(path: str) -> dict[str, str]:
    """Detect git remote, branch, and project name from a local path.

    Uses GitPython to inspect the repo. Returns empty dict if not a git repo.
    """
    result: dict[str, str] = {}
    with suppress(Exception):
        from git import Repo

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
    return result


class ChronicleService:
    """Service for managing session chronicles.

    Methods taking a ``principal`` act on its behalf. Reads are bounded like
    ``GET /sessions`` (``SessionService.visibility_scope`` and the authorization
    policy's ``read``/``list``), and a chronicle outside that bound is reported
    as not found. Writes additionally need the policy to allow the action on a
    resource carrying the chronicle's own owner and tenant, which it keeps after
    its session is deleted. With authorization configured, an absent principal
    raises ``PermissionError``.

    ``get_chronicle_by_session`` and ``session_timeline`` serve in-process
    callers that have already established their authority (session archiving).
    """

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

    # --- In-process access -------------------------------------------------

    async def get_chronicle_by_session(self, session_id: UUID) -> Chronicle | None:
        """Get the most recent chronicle for a session, unscoped."""
        return await self._chronicle_repository.get_by_session(session_id)

    async def session_timeline(self, session_id: UUID) -> TimelineResponse | None:
        """Get the full timeline for a session, unscoped.

        Returns None if no chronicle or no timeline repository is configured.
        """
        if self._timeline_repository is None:
            return None

        chronicle = await self._chronicle_repository.get_by_session(session_id)
        if chronicle is None:
            return None

        return await self._build_timeline(chronicle.id, session_id)

    # --- On behalf of a principal ------------------------------------------

    async def create_chronicle(self, session_id: UUID, *, principal: Principal | None) -> Chronicle:
        """Create a chronicle from a session's current state.

        Raises:
            PermissionError: Authorization is configured and no principal was given.
            SessionNotFoundError: The session does not exist or is outside the
                principal's visibility scope.
            SessionAccessDeniedError: The policy denies ``report_chronicle``.
        """
        session = await self._session_service.get_authorized_session(
            session_id, principal, "report_chronicle"
        )
        return await self._create_from_session(session)

    async def get_chronicle(
        self, chronicle_id: UUID, *, principal: Principal | None
    ) -> Chronicle | None:
        """Get a chronicle by ID, or None when absent or not readable by *principal*."""
        scope = self._session_service.visibility_scope(principal)
        chronicle = await self._chronicle_repository.get(chronicle_id)
        return await self._readable(principal, scope, chronicle)

    async def get_session_chronicle(
        self, session_id: UUID, *, principal: Principal | None
    ) -> Chronicle | None:
        """Get a session's most recent chronicle, or None when absent or not readable."""
        scope = self._session_service.visibility_scope(principal)
        chronicle = await self._chronicle_repository.get_by_session(session_id)
        return await self._readable(principal, scope, chronicle)

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
        """List the chronicles *principal* may read, with optional filters."""
        scope = self._session_service.visibility_scope(principal)
        tenant_id, owner_id = scope
        chronicles = await self._chronicle_repository.list(
            tenant_id=tenant_id,
            owner_id=owner_id,
            project=project,
            repo=repo,
            model=model,
            tags=tags,
            limit=limit,
            offset=offset,
        )
        return await self._filter_readable(principal, scope, chronicles, "list")

    async def update_chronicle(
        self,
        chronicle_id: UUID,
        *,
        principal: Principal | None,
        summary: str | None = None,
        key_changes: list[str] | None = None,
        unfinished_work: str | None = None,
        tags: list[str] | None = None,
        status: ChronicleStatus | None = None,
    ) -> Chronicle:
        """Update a chronicle's mutable fields."""
        chronicle = await self._authorized_chronicle(chronicle_id, principal, "update")

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

    async def delete_chronicle(self, chronicle_id: UUID, *, principal: Principal | None) -> None:
        """Delete a chronicle."""
        await self._authorized_chronicle(chronicle_id, principal, "delete")
        if not await self._chronicle_repository.delete(chronicle_id):
            raise ChronicleNotFoundError(chronicle_id)
        logger.info("Chronicle deleted: id=%s", _sanitize_log(chronicle_id))

    async def create_or_update_from_broker(
        self,
        session_id: UUID,
        *,
        principal: Principal | None,
        summary: str | None = None,
        key_changes: list[str] | None = None,
        unfinished_work: str | None = None,
        duration_seconds: int | None = None,
    ) -> Chronicle:
        """Create or update a chronicle from broker-reported data.

        Idempotent: if a chronicle already exists for this session,
        it is enriched with the supplied data and marked COMPLETE.
        Otherwise a new chronicle is created from the session's current
        state and finalized immediately.

        This is the ingestion point for the broker's ``_report_chronicle``
        POST at shutdown time. The latest chronicle's attribution, or the
        session's when it has none yet, must allow ``report_chronicle``.
        """
        existing, session = await self._authorized_history(
            session_id, principal, "report_chronicle"
        )

        if existing is not None:
            updates: dict = {"updated_at": datetime.now(UTC)}
            if summary is not None:
                updates["summary"] = summary
            if key_changes is not None:
                updates["key_changes"] = key_changes
            if unfinished_work is not None:
                updates["unfinished_work"] = unfinished_work
            if duration_seconds is not None:
                updates["duration_seconds"] = duration_seconds
            updates["status"] = ChronicleStatus.COMPLETE

            updated = existing.model_copy(update=updates)
            result = await self._chronicle_repository.update(updated)
            logger.info(
                "Chronicle enriched from broker: id=%s, session=%s",
                _sanitize_log(result.id),
                _sanitize_log(session_id),
            )
            return result

        # No existing draft — create a fresh chronicle
        chronicle = await self._create_from_session(session)

        # Apply broker data on top of the freshly-created chronicle
        updates = {
            "updated_at": datetime.now(UTC),
            "status": ChronicleStatus.COMPLETE,
        }
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

    async def reforge(self, chronicle_id: UUID, *, principal: Principal | None) -> Session:
        """Relaunch a session from a chronicle entry.

        Reforging restarts the recorded session, so the policy must allow
        ``start`` on the chronicle. The new session has the same configuration
        as the original and belongs to *principal*.
        """
        chronicle = await self._authorized_chronicle(chronicle_id, principal, "start")

        config = chronicle.config_snapshot
        name = config.get("name", f"Reforged: {chronicle.project}")
        model = config.get("model", chronicle.model)
        repo = config.get("repo", chronicle.repo)
        branch = config.get("branch", chronicle.branch)

        session = await self._session_service.create_session(
            name=f"{name} (reforged)",
            model=model,
            source=GitSource(repo=repo, branch=branch),
            principal=principal,
        )

        logger.info(
            "Session reforged: chronicle=%s -> session=%s",
            _sanitize_log(chronicle_id),
            _sanitize_log(session.id),
        )
        return session

    async def get_chain(
        self, chronicle_id: UUID, *, principal: Principal | None
    ) -> list[Chronicle]:
        """Get the readable part of a chronicle's reforge chain.

        Empty unless *principal* may read the chronicle itself; ancestors it may
        not read are left out.
        """
        scope = self._session_service.visibility_scope(principal)
        chain = await self._chronicle_repository.get_chain(chronicle_id)
        readable = await self._filter_readable(principal, scope, chain, "read")
        if not any(c.id == chronicle_id for c in readable):
            return []
        return readable

    async def get_timeline(
        self, session_id: UUID, *, principal: Principal | None
    ) -> TimelineResponse | None:
        """Get the full timeline for a session's chronicle.

        Returns None if no timeline repository is configured, or the session
        has no chronicle *principal* may read.
        """
        scope = self._session_service.visibility_scope(principal)
        if self._timeline_repository is None:
            return None

        chronicle = await self._chronicle_repository.get_by_session(session_id)
        chronicle = await self._readable(principal, scope, chronicle)
        if chronicle is None:
            return None

        return await self._build_timeline(chronicle.id, session_id)

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
        """Add a timeline event to a session's chronicle, creating one if needed.

        The latest chronicle's attribution, or the session's when it has none
        yet, must allow ``report_timeline`` before anything is written. Persists
        the event and publishes it via SSE if a broadcaster is configured.
        """
        if self._timeline_repository is None:
            raise RuntimeError("Timeline repository not configured")

        chronicle, session = await self._authorized_history(
            session_id, principal, "report_timeline"
        )

        # The realtime event is scoped to the session's owner and tenant, so
        # resolve them before persisting rather than store an event we cannot
        # publish.
        if self._broadcaster is not None and session is None:
            session = await self._session_service.get_session(session_id)
            if session is None:
                raise SessionNotFoundError(session_id)

        if chronicle is None:
            chronicle = await self._create_from_session(session)

        event = TimelineEvent(
            id=uuid4(),
            chronicle_id=chronicle.id,
            session_id=session_id,
            t=t,
            type=type,
            label=label,
            tokens=tokens,
            action=action,
            ins=ins,
            del_=del_,
            hash=hash,
            exit_code=exit_code,
            created_at=datetime.now(UTC),
        )
        stored = await self._timeline_repository.add_event(event)
        logger.info(
            "Timeline event added: session=%s, type=%s, t=%d",
            _sanitize_log(session_id),
            _sanitize_log(event.type.value),
            event.t,
        )

        if self._broadcaster is not None:
            timeline = await self._build_timeline(chronicle.id, session_id)
            await self._broadcaster.publish_chronicle_event(
                session_id=session_id,
                event=stored,
                timeline=timeline,
                owner_id=session.owner_id,
                tenant_id=session.tenant_id,
            )

        return stored

    # --- Authorization -----------------------------------------------------

    def _resource(self, chronicle: Chronicle) -> Resource:
        return self._session_service.attributed_resource(
            str(chronicle.session_id or chronicle.id),
            owner_id=chronicle.owner_id,
            tenant_id=chronicle.tenant_id,
        )

    async def _filter_readable(
        self,
        principal: Principal | None,
        scope: _Scope,
        chronicles: list[Chronicle],
        action: str,
    ) -> list[Chronicle]:
        """Keep the chronicles inside *scope* that the policy lets *principal* read."""
        visible = [
            c
            for c in chronicles
            if SessionService.within_scope(scope, owner_id=c.owner_id, tenant_id=c.tenant_id)
        ]
        if not visible:
            return []
        resources = [self._resource(c) for c in visible]
        allowed = await self._session_service.filter_authorized(principal, action, resources)
        allowed_keys = {_resource_key(r) for r in allowed}
        return [
            c
            for c, resource in zip(visible, resources, strict=True)
            if _resource_key(resource) in allowed_keys
        ]

    async def _readable(
        self,
        principal: Principal | None,
        scope: _Scope,
        chronicle: Chronicle | None,
    ) -> Chronicle | None:
        if chronicle is None:
            return None
        readable = await self._filter_readable(principal, scope, [chronicle], "read")
        return readable[0] if readable else None

    async def _authorized_chronicle(
        self, chronicle_id: UUID, principal: Principal | None, action: str
    ) -> Chronicle:
        """Return the chronicle once the policy lets *principal* do *action* on it.

        Raises:
            PermissionError: Authorization is configured and no principal was given.
            ChronicleNotFoundError: The chronicle does not exist or is outside the
                principal's visibility scope.
            ChronicleAccessDeniedError: The policy denies *action*.
        """
        scope = self._session_service.visibility_scope(principal)
        chronicle = await self._chronicle_repository.get(chronicle_id)
        if chronicle is None or not SessionService.within_scope(
            scope, owner_id=chronicle.owner_id, tenant_id=chronicle.tenant_id
        ):
            raise ChronicleNotFoundError(chronicle_id)
        if not await self._session_service.authorizes(principal, action, self._resource(chronicle)):
            raise ChronicleAccessDeniedError(chronicle_id, action)
        return chronicle

    async def _authorized_history(
        self, session_id: UUID, principal: Principal | None, action: str
    ) -> tuple[Chronicle | None, Session | None]:
        """Authorize *action* on a session's history before anything is written.

        The latest chronicle's attribution decides; when the session has no
        chronicle yet, the session's owner and tenant, which a new chronicle
        inherits, decide. Returns ``(chronicle, None)`` or ``(None, session)``.

        Raises:
            PermissionError: Authorization is configured and no principal was given.
            SessionNotFoundError: The session has neither a chronicle nor a
                record, or its history is outside the principal's visibility scope.
            SessionAccessDeniedError: The policy denies *action*.
        """
        scope = self._session_service.visibility_scope(principal)
        chronicle = await self._chronicle_repository.get_by_session(session_id)
        if chronicle is None:
            session = await self._session_service.get_authorized_session(
                session_id, principal, action
            )
            return None, session
        if not SessionService.within_scope(
            scope, owner_id=chronicle.owner_id, tenant_id=chronicle.tenant_id
        ):
            raise SessionNotFoundError(session_id)
        if not await self._session_service.authorizes(principal, action, self._resource(chronicle)):
            user_id = principal.user_id if principal is not None else "unauthenticated"
            raise SessionAccessDeniedError(session_id, user_id)
        return chronicle, None

    # --- Construction ------------------------------------------------------

    async def _create_from_session(self, session: Session) -> Chronicle:
        """Create a chronicle capturing *session*'s current metadata."""
        # Derive project/repo/branch from source type
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
            session_id=session.id,
            status=ChronicleStatus.DRAFT,
            project=project,
            repo=repo,
            branch=branch,
            model=session.model,
            config_snapshot=config_snapshot,
            token_usage=session.tokens_used,
            owner_id=session.owner_id,
            tenant_id=session.tenant_id,
        )

        created = await self._chronicle_repository.create(chronicle)
        logger.info(
            "Chronicle created: id=%s, session=%s, project=%s",
            _sanitize_log(created.id),
            _sanitize_log(session.id),
            _sanitize_log(project),
        )
        return created

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
