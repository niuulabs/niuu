"""Forge notification service: projection, the reader feed, watermarks and rules.

Producers never write the feed directly. A notification is *projected* from a
durable source (a stored session-log row, a needs-input transition, or a direct
submit carrying an idempotency key) into ``forge_notifications`` together with
its scheduled deliveries (and a pending image copy when it carries an image), in
one transaction. Only newly inserted rows are then broadcast as
``session_notification``, so producer retries never duplicate a row, a delivery or
a live event. A new row with an image is broadcast by the image worker once its
image settles (:mod:`volundr.domain.services.notification_images`).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from niuu.domain.guild_membership import GuildInstance
from niuu.domain.models import IntegrationType, Principal
from niuu.domain.notifications import (
    AGENT_KINDS,
    MAX_BODY_CHARS,
    MAX_TITLE_CHARS,
    NotificationDraft,
    NotificationKind,
    NotificationSeverity,
    NotificationSource,
    draft_from_log_payload,
    notification_id,
    summarize_reply,
    turn_dedupe_key,
)
from volundr.domain.models import (
    ResidentRuntime,
    Session,
    SessionLogEntry,
)
from volundr.domain.notification_ports import (
    NotificationDeliveryRepository,
    NotificationImageRepository,
    NotificationImageScheduler,
    NotificationRecorder,
    NotificationRepository,
    NotificationRuleRepository,
)
from volundr.domain.notifications import (
    INTEGRATION_SINK,
    RESIDENT_ID_KEY,
    RESIDENT_SESSION_ID_KEY,
    Notification,
    NotificationCandidate,
    NotificationDelivery,
    NotificationFeed,
    NotificationImageContent,
    NotificationImageState,
    NotificationImageVariant,
    NotificationNotFoundError,
    NotificationQuery,
    NotificationReadState,
    NotificationRule,
    NotificationRuleSpec,
    NotificationScope,
    NotificationSinkInfo,
    NotificationValidationError,
    attention_push_rule,
)
from volundr.domain.ports import EventBroadcaster, IntegrationRepository
from volundr.domain.services.notification_images import notification_event
from volundr.domain.session_read_state import is_final_output

logger = logging.getLogger(__name__)

EngineResolver = Callable[[Session], str | None]


def engine_resolver_for(
    cli_types: Mapping[str, str | None], default_definition: str
) -> EngineResolver:
    """Map a session to its engine through its session definition's broker CLI type.

    ``cli_types`` maps a session-definition key to ``defaults.broker.cliType``
    (``claude``, ``codex-ws``, ``grok`` ...). The engine is the CLI family, so
    ``codex-ws`` is reported as ``codex``. Sessions persisted before definitions
    were recorded ran on the default definition.
    """

    def resolve(session: Session) -> str | None:
        cli_type = cli_types.get(session.session_definition or default_definition)
        if not cli_type:
            return None
        return cli_type.split("-", 1)[0]

    return resolve


@dataclass(frozen=True)
class _SessionContext:
    """Who a notification belongs to and what it is about.

    ``session_id`` is null for a resident's notification: it is anchored by
    ``anchor`` (the resident and its channel) instead.
    """

    session_id: UUID | None
    session_name: str
    owner_id: str
    tenant_id: str | None
    project_id: str | None
    engine: str | None
    model: str | None
    anchor: dict[str, str] = field(default_factory=dict)


def _bounded(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)].rstrip() + "…"


#: Body of an attention item whose request carries no prompt (it is also the push body).
_DEFAULT_ATTENTION_PROMPTS = {
    "question": "The agent is asking you a question",
    "confirmation": "The agent needs your confirmation",
    "permission": "The agent needs permission to continue",
}
_DEFAULT_ATTENTION_PROMPT = "The agent needs your attention"


def attention_dedupe_key(session_id: UUID, state_since: datetime, request_id: str) -> str:
    """Dedupe key for one needs-input request.

    ``attention:{session_id}:{state_since ISO}``, suffixed with the pending
    ``request_id`` when the broker reports one: a second question raised while the
    session is still awaiting input keeps the same ``state_since`` and must not be
    swallowed as a duplicate of the first.
    """
    key = f"attention:{session_id}:{state_since.isoformat()}"
    if not request_id:
        return key
    return f"{key}:{request_id}"


def submit_dedupe_key(session_id: UUID | None, principal_id: str, idempotency_key: str) -> str:
    return f"submit:{session_id or '-'}:{principal_id}:{idempotency_key}"


def _resident_anchor(resident_id: UUID, resident_session_id: UUID | None) -> str:
    return f"{resident_id}:{resident_session_id or 'room'}"


def resident_attention_dedupe_key(
    resident_id: UUID, resident_session_id: UUID | None, request_id: str
) -> str:
    """Dedupe key for one request a resident waits on its owner for.

    ``resident-attention:{resident}:{channel or 'room'}:{request_id}``. The resident
    names the request, so a re-published question stays one notification and its
    answer retires exactly that one.
    """
    if not request_id:
        raise ValueError("A resident attention request needs a request_id")
    return f"resident-attention:{_resident_anchor(resident_id, resident_session_id)}:{request_id}"


def resident_notify_dedupe_key(
    resident_id: UUID, resident_session_id: UUID | None, idempotency_key: str
) -> str:
    """Dedupe key for a notification a resident's agent sent on purpose."""
    if not idempotency_key:
        raise ValueError("A resident notification needs an idempotency_key")
    return f"resident-notify:{_resident_anchor(resident_id, resident_session_id)}:{idempotency_key}"


class NotificationService(NotificationRecorder):
    """Projects, stores and serves Forge notifications."""

    def __init__(
        self,
        repository: NotificationRepository,
        rule_repository: NotificationRuleRepository,
        delivery_repository: NotificationDeliveryRepository,
        *,
        image_repository: NotificationImageRepository,
        image_scheduler: NotificationImageScheduler,
        broadcaster: EventBroadcaster | None = None,
        reply_ready_enabled: bool,
        reply_title_chars: int,
        reply_body_chars: int,
        sinks: list[NotificationSinkInfo],
        stale_after_seconds: float | None = None,
        engine_resolver: EngineResolver | None = None,
        integration_repository: IntegrationRepository | None = None,
        attention_retire_page_size: int = 200,
        guild_instance: GuildInstance | None = None,
    ) -> None:
        if attention_retire_page_size < 1:
            raise ValueError("attention_retire_page_size must be positive")
        if stale_after_seconds is not None and stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")
        self._stale_after = (
            timedelta(seconds=stale_after_seconds) if stale_after_seconds is not None else None
        )
        self._attention_retire_page_size = attention_retire_page_size
        self._repository = repository
        self._rules = rule_repository
        self._deliveries = delivery_repository
        self._images = image_repository
        self._image_scheduler = image_scheduler
        self._broadcaster = broadcaster
        self._reply_ready_enabled = reply_ready_enabled
        self._reply_title_chars = reply_title_chars
        self._reply_body_chars = reply_body_chars
        self._sinks = list(sinks)
        self._engine_resolver = engine_resolver
        self._integrations = integration_repository
        self._instance_fields = guild_instance.fields() if guild_instance else {}

    # -- Projection --------------------------------------------------------

    def is_projectable(self, payload: dict) -> bool:
        """Cheap pre-filter: could this log frame produce a notification?"""
        if draft_from_log_payload(payload) is not None:
            return True
        return self._reply_ready_enabled and is_final_output(payload)

    async def project_log_entries(
        self, session: Session, entries: list[SessionLogEntry]
    ) -> list[Notification]:
        """Project STORED session-log rows; returns (and broadcasts) only new rows."""
        context = self._context(session)
        candidates: list[NotificationCandidate] = []
        for entry in sorted(entries, key=lambda item: item.seq):
            if entry.session_id != session.id:
                raise ValueError("Every projected log row must belong to the session")
            candidates.extend(self._candidates_for_entry(context, entry))
        return await self._record(candidates)

    def _candidates_for_entry(
        self, context: _SessionContext, entry: SessionLogEntry
    ) -> list[NotificationCandidate]:
        candidates: list[NotificationCandidate] = []
        parsed = draft_from_log_payload(entry.payload)
        if parsed is not None:
            turn_id, draft = parsed
            candidates.append(
                self._candidate(
                    context,
                    dedupe_key=turn_dedupe_key(context.session_id, turn_id, draft.kind),
                    draft=draft,
                    source=NotificationSource.AGENT,
                    session_seq=entry.seq,
                    metadata={"turn_id": turn_id},
                )
            )
        if self._reply_ready_enabled and is_final_output(entry.payload):
            turn = entry.payload["turn"]
            title, body = summarize_reply(
                turn["content"],
                title_chars=self._reply_title_chars,
                body_chars=self._reply_body_chars,
            )
            draft = NotificationDraft(
                kind=NotificationKind.REPLY_READY,
                severity=NotificationSeverity.SUCCESS,
                title=title,
                body=body,
            )
            candidates.append(
                self._candidate(
                    context,
                    dedupe_key=turn_dedupe_key(
                        context.session_id, turn["id"], NotificationKind.REPLY_READY
                    ),
                    draft=draft,
                    source=NotificationSource.SYSTEM,
                    session_seq=entry.seq,
                    metadata={"turn_id": turn["id"]},
                )
            )
        return candidates

    async def record_attention(
        self,
        session: Session,
        *,
        state_since: datetime,
        kind: str,
        prompt: str,
        request_id: str,
    ) -> Notification | None:
        context = self._context(session)
        draft = NotificationDraft(
            kind=NotificationKind.ATTENTION,
            severity=NotificationSeverity.WARNING,
            title=_bounded(f"{context.session_name} needs your input", MAX_TITLE_CHARS),
            body=_bounded(
                prompt.strip() or _DEFAULT_ATTENTION_PROMPTS.get(kind, _DEFAULT_ATTENTION_PROMPT),
                MAX_BODY_CHARS,
            ),
        )
        candidate = self._candidate(
            context,
            dedupe_key=attention_dedupe_key(session.id, state_since, request_id),
            draft=draft,
            source=NotificationSource.SYSTEM,
            session_seq=None,
            metadata={"input_kind": kind, "request_id": request_id},
        )
        created = await self._record([candidate])
        return created[0] if created else None

    async def retire_attention(self, session: Session) -> int:
        """Acknowledge the owner's unread attention items once the question is settled.

        Only items above the owner's read watermark can be unread; each is marked read
        individually, so the rest of the feed's read state is untouched.
        """
        owner = session.owner_id or ""
        if not owner:
            return 0
        after = (await self._repository.get_watermark(owner)).read_through_seq
        retired = 0
        while True:
            page = await self._repository.list_for_session(
                session.id, after=after, limit=self._attention_retire_page_size
            )
            attention = [item for item in page if item.kind == NotificationKind.ATTENTION]
            if attention:
                already = await self._repository.read_ids(owner, [item.id for item in attention])
                for item in attention:
                    if item.id not in already:
                        await self._repository.mark_read(owner, item.id)
                        retired += 1
            if len(page) < self._attention_retire_page_size:
                return retired
            after = page[-1].seq

    # -- Residents ---------------------------------------------------------
    #
    # A resident (a long-lived Ravn) is not a Forge session, so its notifications
    # carry no session_id. They are anchored to the resident and, when they come
    # from one of its channels, that channel (metadata resident_id /
    # resident_session_id, on the wire as top-level fields). Ownership comes from
    # the resident's own record; everything else (dedupe, delivery scheduling, the
    # live event) is the same path a session's notification takes.

    async def record_resident_attention(
        self,
        resident: ResidentRuntime,
        *,
        resident_session_id: UUID | None,
        kind: str,
        prompt: str,
        request_id: str,
    ) -> Notification | None:
        """Record (idempotently) that ``resident`` waits for its owner on ``request_id``.

        The same ``attention`` / ``system`` item a waiting session raises, so the
        owner's "Needs-input pushes" rule delivers it. ``None`` when it was already
        recorded.
        """
        context = self._resident_context(resident, resident_session_id)
        draft = NotificationDraft(
            kind=NotificationKind.ATTENTION,
            severity=NotificationSeverity.WARNING,
            title=_bounded(f"{context.session_name} needs your input", MAX_TITLE_CHARS),
            body=_bounded(
                prompt.strip() or _DEFAULT_ATTENTION_PROMPTS.get(kind, _DEFAULT_ATTENTION_PROMPT),
                MAX_BODY_CHARS,
            ),
        )
        candidate = self._candidate(
            context,
            dedupe_key=resident_attention_dedupe_key(resident.id, resident_session_id, request_id),
            draft=draft,
            source=NotificationSource.SYSTEM,
            session_seq=None,
            metadata={"input_kind": kind, "request_id": request_id},
        )
        created = await self._record([candidate])
        return created[0] if created else None

    async def retire_resident_attention(
        self,
        resident: ResidentRuntime,
        *,
        resident_session_id: UUID | None,
        request_ids: list[str],
    ) -> int:
        """Acknowledge the owner's unread attention items for these settled requests.

        Mirrors :meth:`retire_attention`: once the resident stops waiting (its owner
        answered), its "needs your input" items must not stay unread. Each is marked
        read individually, so the rest of the owner's read state is untouched. A
        request that was never recorded has nothing to retire.
        """
        owner = resident.owner_id
        ids: list[UUID] = []
        for request_id in request_ids:
            candidate_id = notification_id(
                resident_attention_dedupe_key(resident.id, resident_session_id, request_id)
            )
            if await self._repository.get(candidate_id) is not None:
                ids.append(candidate_id)
        if not ids:
            return 0
        already = await self._repository.read_ids(owner, ids)
        retired = 0
        for item_id in ids:
            if item_id not in already:
                await self._repository.mark_read(owner, item_id)
                retired += 1
        return retired

    async def record_system_attention(
        self,
        owner_id: str,
        *,
        dedupe_key: str,
        title: str,
        body: str,
        metadata: Mapping[str, object],
    ) -> Notification | None:
        """Record (idempotently) a session-less attention item for ``owner_id``.

        The ``attention`` / ``warning`` / ``system`` item a waiting session raises,
        for a producer that has no Forge session or resident of its own (the ODIN
        review queue). The producer names the item (``dedupe_key``), so a replay,
        or a second replica, records it once. ``None`` when it was already recorded.
        """
        candidate = NotificationCandidate(
            dedupe_key=dedupe_key,
            owner_id=owner_id,
            kind=NotificationKind.ATTENTION,
            severity=NotificationSeverity.WARNING,
            source=NotificationSource.SYSTEM,
            title=_bounded(title, MAX_TITLE_CHARS),
            body=_bounded(body, MAX_BODY_CHARS),
            metadata=dict(metadata),
        )
        created = await self._record([candidate])
        return created[0] if created else None

    async def retire_system_attention(self, owner_id: str, *, dedupe_key: str) -> bool:
        """Acknowledge the owner's unread item recorded under ``dedupe_key``.

        Mirrors :meth:`retire_resident_attention` for :meth:`record_system_attention`:
        only that item is marked read. ``False`` when it was never recorded or is
        already read.
        """
        item_id = notification_id(dedupe_key)
        if await self._repository.get(item_id) is None:
            return False
        if item_id in await self._repository.read_ids(owner_id, [item_id]):
            return False
        await self._repository.mark_read(owner_id, item_id)
        return True

    async def submit_for_resident(
        self,
        resident: ResidentRuntime,
        draft: NotificationDraft,
        *,
        resident_session_id: UUID | None,
        idempotency_key: str,
    ) -> tuple[Notification, bool]:
        """Record a notification the resident's agent sent on purpose (``source=agent``).

        The resident counterpart of a session submitting for itself: the same kinds
        are allowed and a retry with the same key returns the recorded notification.
        Returns ``(notification, created)``.
        """
        if draft.kind not in AGENT_KINDS:
            raise NotificationValidationError(
                f"Kind {draft.kind.value!r} is derived by Forge and cannot be submitted"
            )
        context = self._resident_context(resident, resident_session_id)
        candidate = self._candidate(
            context,
            dedupe_key=resident_notify_dedupe_key(
                resident.id, resident_session_id, idempotency_key
            ),
            draft=draft,
            source=NotificationSource.AGENT,
            session_seq=None,
            metadata={"submitted_by": f"resident:{resident.id}"},
        )
        created = await self._record([candidate])
        if created:
            return created[0], True
        existing = await self._repository.get(candidate.id)
        if existing is None:
            raise RuntimeError(f"Deduplicated notification {candidate.id} is not readable")
        return existing, False

    @staticmethod
    def _resident_context(
        resident: ResidentRuntime, resident_session_id: UUID | None
    ) -> _SessionContext:
        anchor = {RESIDENT_ID_KEY: str(resident.id)}
        if resident_session_id is not None:
            anchor[RESIDENT_SESSION_ID_KEY] = str(resident_session_id)
        return _SessionContext(
            session_id=None,
            session_name=resident.name,
            owner_id=resident.owner_id,
            tenant_id=resident.tenant_id,
            project_id=None,
            engine=resident.engine.value,
            model=resident.model or None,
            anchor=anchor,
        )

    async def submit(
        self,
        session: Session,
        principal: Principal,
        draft: NotificationDraft,
        *,
        idempotency_key: str,
        source: NotificationSource = NotificationSource.OPERATOR,
    ) -> tuple[Notification, bool]:
        """Direct submit; returns ``(notification, created)`` (False when deduped).

        ``source`` is ``agent`` when the session itself submits (with its own session
        credential); its idempotency keys then live apart from its owner's.
        """
        if draft.kind not in AGENT_KINDS:
            raise NotificationValidationError(
                f"Kind {draft.kind.value!r} is derived by Forge and cannot be submitted"
            )
        submitter = (
            principal.user_id
            if source is NotificationSource.OPERATOR
            else f"{source.value}:{principal.user_id}"
        )
        context = self._context(session)
        candidate = self._candidate(
            context,
            dedupe_key=submit_dedupe_key(session.id, submitter, idempotency_key),
            draft=draft,
            source=source,
            session_seq=None,
            metadata={"submitted_by": principal.user_id},
        )
        created = await self._record([candidate])
        if created:
            return created[0], True
        existing = await self._repository.get(candidate.id)
        if existing is None:
            raise RuntimeError(f"Deduplicated notification {candidate.id} is not readable")
        return existing, False

    def burst_candidate(
        self,
        session: Session,
        *,
        dedupe_key: str,
        draft: NotificationDraft,
        metadata: dict,
    ) -> NotificationCandidate:
        """A ``system`` notice about ``session`` that another store commits (F1n).

        The artifact hold inserts it in its own emission transaction, then hands the
        committed row back to :meth:`broadcast_created`.
        """
        return self._candidate(
            self._context(session),
            dedupe_key=dedupe_key,
            draft=draft,
            source=NotificationSource.SYSTEM,
            session_seq=None,
            metadata=metadata,
        )

    async def broadcast_created(self, notification: Notification) -> None:
        """Broadcast a notification committed outside :meth:`_record` (post-commit)."""
        await self._broadcast(notification)

    def _context(self, session: Session) -> _SessionContext:
        coordination = session.coordination
        return _SessionContext(
            session_id=session.id,
            session_name=session.name,
            owner_id=session.owner_id or "",
            tenant_id=session.tenant_id,
            project_id=str(coordination.project_id) if coordination else None,
            engine=self._engine_resolver(session) if self._engine_resolver else None,
            model=session.effective_model or None,
        )

    @staticmethod
    def _candidate(
        context: _SessionContext,
        *,
        dedupe_key: str,
        draft: NotificationDraft,
        source: NotificationSource,
        session_seq: int | None,
        metadata: dict,
    ) -> NotificationCandidate:
        return NotificationCandidate(
            dedupe_key=dedupe_key,
            session_id=context.session_id,
            session_seq=session_seq,
            session_name=context.session_name,
            owner_id=context.owner_id,
            tenant_id=context.tenant_id,
            project_id=context.project_id,
            kind=draft.kind,
            severity=draft.severity,
            source=source,
            title=draft.title,
            body=draft.body,
            links=draft.links,
            engine=context.engine,
            model=context.model,
            correlation_id=draft.correlation_id,
            metadata={
                **metadata,
                **context.anchor,
                **draft.question_metadata(),
                **draft.resolution_metadata(),
            },
            image=draft.image,
            speak=draft.speak,
        )

    async def _record(self, candidates: list[NotificationCandidate]) -> list[Notification]:
        if not candidates:
            return []
        created = await self._repository.project(candidates)
        images_pending = False
        for notification in created:
            if (
                notification.image is not None
                and notification.image.state is NotificationImageState.PENDING
            ):
                # Announced by the image worker once the image settles, so the live
                # event carries the stored image (or its failure) like the feed does.
                images_pending = True
                continue
            await self._broadcast(notification)
        if images_pending:
            self._image_scheduler.wake()
        for notification in created:
            await self._apply_resolutions(notification)
        return created

    async def _apply_resolutions(self, notification: Notification) -> None:
        """Resolve what a new notification closes (LEX-1442).

        * Its ``resolves`` refs: notification ids, or correlation ids standing for
          every earlier notification of that incident (any kind).
        * A milestone with a ``correlation_id`` closes that incident's earlier
          problems: attention and error notifications and anything critical.

        Only the same owner's notifications are touched. Every row resolved is sent
        again on the live stream, so clients replace it in place.
        """
        refs = notification.metadata.get("resolves") or []
        ids: list[UUID] = []
        resolved: list[Notification] = []
        now = datetime.now(UTC)
        for ref in refs:
            try:
                ids.append(UUID(ref))
            except ValueError:
                resolved += await self._repository.resolve_correlated(
                    notification.owner_id,
                    ref,
                    before_seq=notification.seq,
                    problems_only=False,
                    resolved_at=now,
                    resolved_by=notification.id,
                )
        if ids:
            resolved += await self._repository.resolve(
                notification.owner_id,
                [i for i in ids if i != notification.id],
                resolved_at=now,
                resolved_by=notification.id,
                resolution=None,
            )
        if notification.kind is NotificationKind.MILESTONE and notification.correlation_id:
            resolved += await self._repository.resolve_correlated(
                notification.owner_id,
                notification.correlation_id,
                before_seq=notification.seq,
                problems_only=True,
                resolved_at=now,
                resolved_by=notification.id,
            )
        for row in resolved:
            await self._broadcast(row)

    async def resolve(
        self, principal: Principal, notification_id: UUID, *, resolution: str | None
    ) -> Notification:
        """An operator resolves one notification by hand (idempotent)."""
        notification = await self._repository.get(notification_id)
        scope = NotificationScope.for_principal(principal)
        if notification is None or not scope.allows(notification.owner_id, notification.tenant_id):
            raise NotificationNotFoundError(f"Notification not found: {notification_id}")
        if notification.resolved:
            return notification
        changed = await self._repository.resolve(
            notification.owner_id,
            [notification_id],
            resolved_at=datetime.now(UTC),
            resolved_by=None,
            resolution=resolution,
        )
        for row in changed:
            await self._broadcast(row)
        return changed[0] if changed else await self._repository.get(notification_id)

    @property
    def instance_fields(self) -> dict[str, str]:
        """This node's Guild instance (``instance_id``/``_name``/``_slug``) for every
        notification it serves, so a client reading the node directly gets the same
        item shape as the Guild feed. Empty when the node belongs to no Guild."""
        return dict(self._instance_fields)

    async def _broadcast(self, notification: Notification) -> None:
        if self._broadcaster is None:
            return
        await self._broadcaster.publish(notification_event(notification, self._instance_fields))

    # -- Reading -----------------------------------------------------------

    async def list_feed(self, principal: Principal, query: NotificationQuery) -> NotificationFeed:
        scope = NotificationScope.for_principal(principal)
        watermark = await self._repository.get_watermark(principal.user_id)
        page = await self._repository.list_feed(
            scope, query, read_through_seq=watermark.read_through_seq
        )
        page = replace(page, items=await self._mark_stale(page.items))
        head, unread = await self._repository.feed_counts(
            scope, read_through_seq=watermark.read_through_seq
        )
        return NotificationFeed(
            items=page.items,
            next_before=page.next_before,
            head_seq=head,
            read_through_seq=watermark.read_through_seq,
            unread_count=unread,
            revision=watermark.revision,
            read_ids=await self.read_ids(principal, page.items),
        )

    async def list_for_session(
        self, principal: Principal, session_id: UUID, *, after: int, limit: int
    ) -> tuple[list[Notification], int]:
        """A session's notifications (ascending) and the reader's watermark.

        The caller has already authorized ``principal`` for the session.
        """
        watermark = await self._repository.get_watermark(principal.user_id)
        items = await self._repository.list_for_session(session_id, after=after, limit=limit)
        return await self._mark_stale(items), watermark.read_through_seq

    async def _mark_stale(self, items: list[Notification]) -> list[Notification]:
        """Flag alarms whose session has moved on (LEX-1442 item 4); display only."""
        if self._stale_after is None:
            return items
        cutoff = datetime.now(UTC) - self._stale_after
        candidates = [
            n
            for n in items
            if n.resolved_at is None
            and n.session_id is not None
            and n.created_at <= cutoff
            and (
                n.kind in (NotificationKind.ATTENTION, NotificationKind.ERROR)
                or n.severity is NotificationSeverity.CRITICAL
            )
        ]
        if not candidates:
            return items
        latest = await self._repository.latest_milestone_seqs(
            sorted({n.session_id for n in candidates}, key=str)
        )
        stale = {n.id for n in candidates if latest.get(n.session_id, 0) > n.seq}
        return [n.model_copy(update={"stale": True}) if n.id in stale else n for n in items]

    async def read_through_seq(self, principal: Principal) -> int:
        watermark = await self._repository.get_watermark(principal.user_id)
        return watermark.read_through_seq

    async def get_read_state(self, principal: Principal) -> NotificationReadState:
        scope = NotificationScope.for_principal(principal)
        watermark = await self._repository.get_watermark(principal.user_id)
        head, unread = await self._repository.feed_counts(
            scope, read_through_seq=watermark.read_through_seq
        )
        return NotificationReadState(
            read_through_seq=watermark.read_through_seq,
            revision=watermark.revision,
            unread_count=unread,
            head_seq=head,
        )

    async def update_read_state(
        self, principal: Principal, *, read_through_seq: int, expected_revision: int
    ) -> NotificationReadState:
        """Move the reader's watermark forward (compare-and-swap on ``revision``)."""
        scope = NotificationScope.for_principal(principal)
        current = await self._repository.get_watermark(principal.user_id)
        head, _ = await self._repository.feed_counts(
            scope, read_through_seq=current.read_through_seq
        )
        if read_through_seq > head:
            raise NotificationValidationError(
                f"Cannot mark unseen notifications as read (head is {head})"
            )
        watermark = await self._repository.advance_watermark(
            principal.user_id,
            read_through_seq=read_through_seq,
            expected_revision=expected_revision,
        )
        head, unread = await self._repository.feed_counts(
            scope, read_through_seq=watermark.read_through_seq
        )
        return NotificationReadState(
            read_through_seq=watermark.read_through_seq,
            revision=watermark.revision,
            unread_count=unread,
            head_seq=head,
        )

    async def read_ids(self, principal: Principal, items: list[Notification]) -> set[UUID]:
        return await self._repository.read_ids(principal.user_id, [item.id for item in items])

    async def mark_read(self, principal: Principal, notification_id: UUID) -> NotificationReadState:
        notification = await self._repository.get(notification_id)
        scope = NotificationScope.for_principal(principal)
        if notification is None or not scope.allows(notification.owner_id, notification.tenant_id):
            raise NotificationNotFoundError(f"Notification not found: {notification_id}")
        await self._repository.mark_read(principal.user_id, notification_id)
        return await self.get_read_state(principal)

    async def image_content(
        self,
        principal: Principal,
        notification_id: UUID,
        variant: NotificationImageVariant,
    ) -> NotificationImageContent:
        """The stored image bytes, readable by exactly those who may read the item."""
        notification = await self._repository.get(notification_id)
        scope = NotificationScope.for_principal(principal)
        if notification is None or not scope.allows(notification.owner_id, notification.tenant_id):
            raise NotificationNotFoundError(f"Notification not found: {notification_id}")
        content = await self._images.get_content(notification_id, variant)
        if content is None:
            raise NotificationNotFoundError(f"Notification {notification_id} has no stored image")
        return content

    async def list_deliveries(
        self, principal: Principal, notification_id: UUID
    ) -> list[NotificationDelivery]:
        notification = await self._repository.get(notification_id)
        scope = NotificationScope.for_principal(principal)
        if notification is None or not scope.allows(notification.owner_id, notification.tenant_id):
            raise NotificationNotFoundError(f"Notification not found: {notification_id}")
        return await self._deliveries.list_for_notification(notification_id)

    # -- Rules and sinks ---------------------------------------------------

    async def list_sinks(self, principal: Principal) -> list[NotificationSinkInfo]:
        sinks = list(self._sinks)
        if await self._messaging_connections(principal):
            sinks.append(INTEGRATION_SINK)
        return sinks

    async def ensure_attention_push_rule(self, owner_id: str, sink: str) -> NotificationRule | None:
        """Give ``owner_id`` the needs-input push rule unless they already route to ``sink``.

        Called when an owner registers a device while ``push.enabled`` is on. Any
        existing rule naming ``sink``, enabled or not, means the owner has decided
        how pushes reach them, so nothing is added; disabling the rule is how an
        owner opts out. Returns the rule it created, if any.
        """
        if sink not in {info.name for info in self._sinks}:
            raise NotificationValidationError(
                f"Sink {sink!r} is not configured; add it under notifications.sinks"
            )
        existing = await self._rules.list_for_owner(owner_id)
        if any(rule.sink == sink for rule in existing):
            return None
        return await self._rules.create_if_absent(
            attention_push_rule(owner_id, sink, datetime.now(UTC))
        )

    async def list_rules(self, principal: Principal) -> list[NotificationRule]:
        return await self._rules.list_for_owner(principal.user_id)

    async def create_rule(
        self, principal: Principal, spec: NotificationRuleSpec
    ) -> NotificationRule:
        await self._validate_rule(principal, spec)
        now = datetime.now(UTC)
        rule = NotificationRule(
            **spec.model_dump(),
            id=uuid4(),
            owner_id=principal.user_id,
            created_at=now,
            updated_at=now,
        )
        return await self._rules.create(rule)

    async def update_rule(
        self, principal: Principal, rule_id: UUID, spec: NotificationRuleSpec
    ) -> NotificationRule:
        existing = await self._rules.get(principal.user_id, rule_id)
        if existing is None:
            raise NotificationNotFoundError(f"Notification rule not found: {rule_id}")
        await self._validate_rule(principal, spec)
        updated = await self._rules.update(
            NotificationRule(
                **spec.model_dump(),
                id=rule_id,
                owner_id=principal.user_id,
                created_at=existing.created_at,
                updated_at=datetime.now(UTC),
            )
        )
        if updated is None:
            raise NotificationNotFoundError(f"Notification rule not found: {rule_id}")
        return updated

    async def delete_rule(self, principal: Principal, rule_id: UUID) -> None:
        if not await self._rules.delete(principal.user_id, rule_id):
            raise NotificationNotFoundError(f"Notification rule not found: {rule_id}")

    async def _validate_rule(self, principal: Principal, spec: NotificationRuleSpec) -> None:
        if spec.integration_connection_id is None:
            if spec.sink == INTEGRATION_SINK.name:
                raise NotificationValidationError(
                    "The integration sink needs an integration_connection_id"
                )
            if spec.sink not in {sink.name for sink in self._sinks}:
                raise NotificationValidationError(
                    f"Unknown sink {spec.sink!r}; configure it under notifications.sinks"
                )
            return
        connections = await self._messaging_connections(principal)
        if spec.integration_connection_id not in {conn.id for conn in connections}:
            raise NotificationValidationError(
                "integration_connection_id must name one of your enabled messaging integrations"
            )

    async def _messaging_connections(self, principal: Principal) -> list:
        if self._integrations is None:
            return []
        connections = await self._integrations.list_connections(
            principal.user_id, IntegrationType.MESSAGING
        )
        return [conn for conn in connections if conn.enabled]
