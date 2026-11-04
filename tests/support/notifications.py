"""In-memory notification repositories for service and REST tests.

They implement the same semantics as the Postgres adapters (dedupe by key,
monotonic seqs, rule evaluation on new rows only, forward-only watermark CAS) so
tests can exercise the domain without a database.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from niuu.domain.notifications import NotificationImageRef
from volundr.domain.notification_ports import (
    NotificationDeliveryRepository,
    NotificationImageRepository,
    NotificationImageScheduler,
    NotificationRepository,
    NotificationRuleRepository,
)
from volundr.domain.notifications import (
    ClaimedDelivery,
    DeliveryStatus,
    Notification,
    NotificationCandidate,
    NotificationDelivery,
    NotificationImage,
    NotificationImageContent,
    NotificationImageState,
    NotificationImageVariant,
    NotificationPage,
    NotificationQuery,
    NotificationReadStateConflictError,
    NotificationRule,
    NotificationScope,
    PendingNotificationImage,
    ProcessedNotificationImage,
    ReadWatermark,
    rules_matching,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class InMemoryImageRow:
    """One ``forge_notification_images`` row."""

    notification_id: UUID
    session_id: UUID | None
    ref: NotificationImageRef
    created_at: datetime
    state: NotificationImageState = NotificationImageState.PENDING
    attempts: int = 0
    lease_until: datetime | None = None
    stored: ProcessedNotificationImage | None = None
    error: str | None = None

    def view(self) -> NotificationImage:
        stored = self.stored
        return NotificationImage(
            state=self.state,
            alt=self.ref.alt,
            mime=stored.mime if stored else None,
            width=stored.width if stored else None,
            height=stored.height if stored else None,
            error=self.error,
        )


class RecordingImageScheduler(NotificationImageScheduler):
    def __init__(self) -> None:
        self.wakes = 0

    def wake(self) -> None:
        self.wakes += 1


class InMemoryNotificationStore:
    """Shared state; ``feed``, ``rule_repo``, ``outbox`` and ``images`` are the ports.

    ``clock`` stands in for the database's ``now()`` so tests can move time;
    ``image_scheduler`` records how often the projection asked for image copies.
    """

    def __init__(self, clock: Callable[[], datetime] = _utc_now) -> None:
        self.clock = clock
        self.notifications: dict[str, Notification] = {}
        self.rules: dict[UUID, NotificationRule] = {}
        self.deliveries: dict[UUID, NotificationDelivery] = {}
        self.watermarks: dict[str, ReadWatermark] = {}
        self.reads: set[tuple[str, UUID]] = set()
        self.image_rows: dict[UUID, InMemoryImageRow] = {}
        self.project_calls = 0
        self.seq = 0
        self.feed = InMemoryNotificationRepository(self)
        self.rule_repo = InMemoryNotificationRuleRepository(self)
        self.outbox = InMemoryNotificationDeliveryRepository(self)
        self.images = InMemoryNotificationImageRepository(self)
        self.image_scheduler = RecordingImageScheduler()

    def image_of(self, notification_id: UUID) -> NotificationImage | None:
        row = self.image_rows.get(notification_id)
        return row.view() if row is not None else None

    def with_image(self, notification: Notification) -> Notification:
        image = self.image_of(notification.id)
        if image is None:
            return notification
        return notification.model_copy(update={"image": image})


class InMemoryNotificationRepository(NotificationRepository):
    def __init__(self, store: InMemoryNotificationStore) -> None:
        self.store = store

    async def project(self, candidates: list[NotificationCandidate]) -> list[Notification]:
        store = self.store
        store.project_calls += 1
        created: list[Notification] = []
        for candidate in candidates:
            if candidate.dedupe_key in store.notifications:
                continue
            store.seq += 1
            notification = Notification(
                **candidate.model_dump(exclude={"image"}),
                id=candidate.id,
                seq=store.seq,
                created_at=store.clock(),
            )
            store.notifications[candidate.dedupe_key] = notification
            if candidate.image is not None:
                store.image_rows[notification.id] = InMemoryImageRow(
                    notification_id=notification.id,
                    session_id=notification.session_id,
                    ref=candidate.image,
                    created_at=store.clock(),
                )
            created.append(store.with_image(notification))
        enabled = [rule for rule in store.rules.values() if rule.enabled]
        now = store.clock()
        for notification in created:
            for rule in rules_matching(notification, enabled):
                delivery = NotificationDelivery(
                    id=uuid4(),
                    notification_id=notification.id,
                    rule_id=rule.id,
                    sink=rule.sink,
                    status=DeliveryStatus.PENDING,
                    attempts=0,
                    next_attempt_at=now,
                    created_at=now,
                    updated_at=now,
                )
                store.deliveries[delivery.id] = delivery
        return created

    def _visible(self, scope: NotificationScope) -> list[Notification]:
        return [
            self.store.with_image(n)
            for n in self.store.notifications.values()
            if scope.allows(n.owner_id, n.tenant_id)
        ]

    async def get(self, notification_id: UUID) -> Notification | None:
        found = next(
            (n for n in self.store.notifications.values() if n.id == notification_id), None
        )
        return self.store.with_image(found) if found is not None else None

    async def list_feed(
        self, scope: NotificationScope, query: NotificationQuery, *, read_through_seq: int
    ) -> NotificationPage:
        items = self._visible(scope)
        severities = query.severities()
        items = [
            n
            for n in items
            if (not query.kinds or n.kind in query.kinds)
            and (not severities or n.severity in severities)
            and (not query.sources or n.source in query.sources)
            and (query.session_id is None or n.session_id == query.session_id)
            and (query.project_id is None or n.project_id == query.project_id)
            and (
                query.unread is None
                or (n.seq > read_through_seq and (scope.user_id, n.id) not in self.store.reads)
                == query.unread
            )
            and (query.unresolved is None or (n.resolved_at is None) == query.unresolved)
            and (query.before is None or n.seq < query.before)
            and (query.after is None or n.seq > query.after)
        ]
        items.sort(key=lambda n: n.seq, reverse=not query.ascending)
        page = items[: query.limit]
        more = len(items) > query.limit and not query.ascending
        return NotificationPage(items=page, next_before=page[-1].seq if more else None)

    async def list_for_session(
        self, session_id: UUID, *, after: int, limit: int
    ) -> list[Notification]:
        items = [
            self.store.with_image(n)
            for n in self.store.notifications.values()
            if n.session_id == session_id and n.seq > after
        ]
        return sorted(items, key=lambda n: n.seq)[:limit]

    async def feed_counts(
        self, scope: NotificationScope, *, read_through_seq: int
    ) -> tuple[int, int]:
        items = self._visible(scope)
        head = max((n.seq for n in items), default=0)
        return head, sum(
            1
            for n in items
            if n.seq > read_through_seq and (scope.user_id, n.id) not in self.store.reads
        )

    async def get_watermark(self, user_id: str) -> ReadWatermark:
        return self.store.watermarks.get(user_id, ReadWatermark())

    async def advance_watermark(
        self, user_id: str, *, read_through_seq: int, expected_revision: int
    ) -> ReadWatermark:
        current = self.store.watermarks.get(user_id, ReadWatermark())
        if current.revision != expected_revision:
            raise NotificationReadStateConflictError("changed")
        updated = ReadWatermark(
            read_through_seq=max(current.read_through_seq, read_through_seq),
            revision=current.revision + 1,
        )
        self.store.watermarks[user_id] = updated
        return updated

    async def read_ids(self, user_id: str, notification_ids: list[UUID]) -> set[UUID]:
        return {key for key in notification_ids if (user_id, key) in self.store.reads}

    def _resolve_rows(
        self, rows: list[Notification], *, resolved_at, resolved_by, resolution
    ) -> list[Notification]:
        resolved = []
        for row in rows:
            updated = row.model_copy(
                update={
                    "resolved_at": resolved_at,
                    "resolved_by_notification_id": resolved_by,
                    "resolution": resolution,
                }
            )
            self.store.notifications[row.dedupe_key] = updated
            resolved.append(self.store.with_image(updated))
        return sorted(resolved, key=lambda n: n.seq)

    async def resolve(
        self, owner_id, notification_ids, *, resolved_at, resolved_by, resolution
    ) -> list[Notification]:
        rows = [
            n
            for n in self.store.notifications.values()
            if n.owner_id == owner_id and n.id in notification_ids and n.resolved_at is None
        ]
        return self._resolve_rows(
            rows, resolved_at=resolved_at, resolved_by=resolved_by, resolution=resolution
        )

    async def latest_milestone_seqs(self, session_ids) -> dict:
        latest: dict = {}
        for n in self.store.notifications.values():
            if n.kind.value == "milestone" and n.session_id in session_ids:
                latest[n.session_id] = max(latest.get(n.session_id, 0), n.seq)
        return latest

    async def resolve_correlated(
        self, owner_id, correlation_id, *, before_seq, problems_only, resolved_at, resolved_by
    ) -> list[Notification]:
        rows = [
            n
            for n in self.store.notifications.values()
            if n.owner_id == owner_id
            and n.correlation_id == correlation_id
            and n.seq < before_seq
            and n.resolved_at is None
            and (
                not problems_only
                or n.kind.value in ("attention", "error")
                or n.severity.value == "critical"
            )
        ]
        return self._resolve_rows(
            rows, resolved_at=resolved_at, resolved_by=resolved_by, resolution=None
        )

    async def mark_read(self, user_id: str, notification_id: UUID) -> None:
        if (user_id, notification_id) in self.store.reads:
            return
        self.store.reads.add((user_id, notification_id))
        old = await self.get_watermark(user_id)
        self.store.watermarks[user_id] = ReadWatermark(old.read_through_seq, old.revision + 1)


class InMemoryNotificationRuleRepository(NotificationRuleRepository):
    def __init__(self, store: InMemoryNotificationStore) -> None:
        self.rules = store.rules
        self.deliveries = store.deliveries

    async def list_for_owner(self, owner_id: str) -> list[NotificationRule]:
        return [rule for rule in self.rules.values() if rule.owner_id == owner_id]

    async def get(self, owner_id: str, rule_id: UUID) -> NotificationRule | None:
        rule = self.rules.get(rule_id)
        return rule if rule is not None and rule.owner_id == owner_id else None

    async def list_enabled_for_owner(self, owner_id: str) -> list[NotificationRule]:
        return [rule for rule in await self.list_for_owner(owner_id) if rule.enabled]

    async def create(self, rule: NotificationRule) -> NotificationRule:
        self.rules[rule.id] = rule
        return rule

    async def create_if_absent(self, rule: NotificationRule) -> NotificationRule | None:
        if rule.id in self.rules:
            return None
        self.rules[rule.id] = rule
        return rule

    async def update(self, rule: NotificationRule) -> NotificationRule | None:
        current = self.rules.get(rule.id)
        if current is None or current.owner_id != rule.owner_id:
            return None
        self.rules[rule.id] = rule
        return rule

    async def delete(self, owner_id: str, rule_id: UUID) -> bool:
        current = self.rules.get(rule_id)
        if current is None or current.owner_id != owner_id:
            return False
        del self.rules[rule_id]
        for delivery_id in [d.id for d in self.deliveries.values() if d.rule_id == rule_id]:
            del self.deliveries[delivery_id]
        return True


_CLAIMABLE = (DeliveryStatus.PENDING, DeliveryStatus.FAILED, DeliveryStatus.CLAIMED)


class InMemoryNotificationDeliveryRepository(NotificationDeliveryRepository):
    """The outbox with the Postgres claim semantics: due rows, leases, fenced settles.

    Claims are atomic (no awaits inside), which is what ``FOR UPDATE SKIP LOCKED``
    gives concurrent workers against the real table.
    """

    def __init__(self, store: InMemoryNotificationStore) -> None:
        self.store = store
        self.deliveries = store.deliveries
        self.claim_calls = 0

    async def claim_due(self, *, limit: int, lease_seconds: float) -> list[ClaimedDelivery]:
        self.claim_calls += 1
        now = self.store.clock()
        due = sorted(
            (
                d
                for d in self.deliveries.values()
                if d.status in _CLAIMABLE and d.next_attempt_at <= now
            ),
            key=lambda d: (d.next_attempt_at, d.created_at),
        )[:limit]
        by_id = {n.id: n for n in self.store.notifications.values()}
        lease = now + timedelta(seconds=lease_seconds)
        claimed: list[ClaimedDelivery] = []
        for delivery in due:
            updated = delivery.model_copy(
                update={
                    "status": DeliveryStatus.CLAIMED,
                    "attempts": delivery.attempts + 1,
                    "lease_until": lease,
                    "next_attempt_at": lease,
                    "updated_at": now,
                }
            )
            self.deliveries[delivery.id] = updated
            claimed.append(
                ClaimedDelivery(
                    delivery=updated,
                    notification=by_id[delivery.notification_id],
                    rule=self.store.rules[delivery.rule_id],
                )
            )
        return claimed

    def _settle(self, delivery: NotificationDelivery, **changes: object) -> bool:
        current = self.deliveries.get(delivery.id)
        if (
            current is None
            or current.attempts != delivery.attempts
            or current.status is not DeliveryStatus.CLAIMED
        ):
            return False
        self.deliveries[delivery.id] = current.model_copy(
            update={**changes, "lease_until": None, "updated_at": self.store.clock()}
        )
        return True

    async def mark_delivered(self, delivery: NotificationDelivery) -> bool:
        return self._settle(
            delivery,
            status=DeliveryStatus.DELIVERED,
            delivered_at=self.store.clock(),
            last_error=None,
        )

    async def mark_failed(self, delivery, *, error, retry_at) -> bool:  # type: ignore[override]
        if retry_at is None:
            return self._settle(delivery, status=DeliveryStatus.DEAD, last_error=error)
        return self._settle(
            delivery, status=DeliveryStatus.FAILED, next_attempt_at=retry_at, last_error=error
        )

    async def mark_suppressed(self, delivery, *, reason) -> bool:  # type: ignore[override]
        return self._settle(delivery, status=DeliveryStatus.SUPPRESSED, last_error=reason)

    async def list_for_notification(self, notification_id: UUID) -> list[NotificationDelivery]:
        return [d for d in self.deliveries.values() if d.notification_id == notification_id]

    async def count_delivered_since(self, rule_id: UUID, since: datetime) -> int:
        return sum(
            1
            for d in self.deliveries.values()
            if d.rule_id == rule_id
            and d.status is DeliveryStatus.DELIVERED
            and d.delivered_at is not None
            and d.delivered_at >= since
        )


class InMemoryNotificationImageRepository(NotificationImageRepository):
    """Image copies with the Postgres claim semantics: leases and fenced settles."""

    def __init__(self, store: InMemoryNotificationStore) -> None:
        self.store = store
        self.rows = store.image_rows

    async def claim_pending(
        self, *, limit: int, lease_seconds: float
    ) -> list[PendingNotificationImage]:
        now = self.store.clock()
        due = sorted(
            (
                row
                for row in self.rows.values()
                if row.state is NotificationImageState.PENDING
                and (row.lease_until is None or row.lease_until <= now)
            ),
            key=lambda row: row.created_at,
        )[:limit]
        claimed = []
        for row in due:
            row.attempts += 1
            row.lease_until = now + timedelta(seconds=lease_seconds)
            claimed.append(
                PendingNotificationImage(
                    notification_id=row.notification_id,
                    session_id=row.session_id,
                    ref=row.ref,
                    attempts=row.attempts,
                )
            )
        return claimed

    def _claimed(self, pending: PendingNotificationImage) -> InMemoryImageRow | None:
        row = self.rows.get(pending.notification_id)
        if (
            row is None
            or row.attempts != pending.attempts
            or row.state is not NotificationImageState.PENDING
        ):
            return None
        return row

    async def mark_ready(
        self, pending: PendingNotificationImage, image: ProcessedNotificationImage
    ) -> bool:
        row = self._claimed(pending)
        if row is None:
            return False
        row.state, row.stored, row.error, row.lease_until = (
            NotificationImageState.READY,
            image,
            None,
            None,
        )
        return True

    async def mark_failed(self, pending: PendingNotificationImage, *, error: str) -> bool:
        row = self._claimed(pending)
        if row is None:
            return False
        row.state, row.error, row.lease_until = NotificationImageState.FAILED, error, None
        return True

    async def get_content(
        self, notification_id: UUID, variant: NotificationImageVariant
    ) -> NotificationImageContent | None:
        row = self.rows.get(notification_id)
        if row is None or row.stored is None or row.state is not NotificationImageState.READY:
            return None
        if variant is NotificationImageVariant.THUMB:
            return NotificationImageContent(
                mime=row.stored.thumbnail_mime, content=row.stored.thumbnail
            )
        return NotificationImageContent(mime=row.stored.mime, content=row.stored.content)
