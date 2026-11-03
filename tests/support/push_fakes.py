"""In-memory fakes of the push gateway's ports, for tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

from niuu.domain.notifications import NotificationSeverity
from volundr.domain.models import DevicePlatform, DeviceToken
from volundr.domain.ports import PushTokenRegistry
from volundr.domain.push import (
    ApnsPush,
    ApnsResult,
    GuildNotification,
    PushEnvironment,
    PushTokenKind,
)
from volundr.domain.push_ports import (
    ApplePushClient,
    GuildFeed,
    GuildFeedError,
    GuildFeedPage,
    PushDelivery,
    PushDeliveryRepository,
    PushDeliveryStatus,
)
from volundr.domain.services.push_gateway import (
    AlertSettings,
    BackgroundSettings,
    LiveActivitySettings,
    PushGatewaySettings,
    WidgetSettings,
)

START = datetime(2026, 10, 2, 6, 40, 14, tzinfo=UTC)
THOR = "inst-thor"
MINI = "inst-mini-m6"
BUNDLE = "com.xteo.lexi.chat"


class Clock:
    """A settable clock shared by the gateway and the fakes."""

    def __init__(self, now: datetime = START) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def guild_notification(
    notification_id: str = "n-1",
    *,
    instance_id: str = THOR,
    seq: int = 1,
    owner_id: str = "dev-user",
    kind: str = "milestone",
    severity: str = "success",
    source: str = "agent",
    title: str = "Build finished",
    body: str = "All 244 tests pass",
    session_id: str | None = "sess-1",
    session_name: str | None = "horde-coordinator",
    turn_id: str | None = "nt_1",
    created_at: datetime | None = START,
    read: bool = False,
    image_url: str | None = None,
    image_thumbnail_url: str | None = None,
) -> GuildNotification:
    return GuildNotification(
        id=notification_id,
        instance_id=instance_id,
        seq=seq,
        owner_id=owner_id,
        kind=kind,
        severity=severity,
        source=source,
        title=title,
        body=body,
        session_id=session_id,
        session_name=session_name,
        turn_id=turn_id,
        created_at=created_at,
        read=read,
        image_url=image_url,
        image_thumbnail_url=image_thumbnail_url,
    )


def device(
    token: str,
    kind: PushTokenKind = PushTokenKind.ALERT,
    *,
    owner_id: str = "dev-user",
    environment: PushEnvironment = PushEnvironment.PRODUCTION,
    platform: DevicePlatform = DevicePlatform.IOS,
    app_bundle_id: str | None = BUNDLE,
    activity_id: str | None = None,
) -> DeviceToken:
    return DeviceToken(
        owner_id=owner_id,
        platform=platform,
        token=token,
        app_bundle_id=app_bundle_id,
        kind=kind,
        environment=environment,
        activity_id=activity_id,
    )


class FakeGuildFeed(GuildFeed):
    """A Guild of in-memory node feeds, read node by node like the real one."""

    def __init__(self) -> None:
        self.feeds: dict[str, list[GuildNotification]] = {}
        self.unread_counts: dict[str, int] = {}
        #: The Guild itself is unreachable: every call fails.
        self.error: GuildFeedError | None = None
        #: Nodes that do not answer (the registry still lists them).
        self.down: dict[str, GuildFeedError] = {}
        self.nodes_calls = 0
        self.newest_calls: list[tuple[str, int]] = []
        self.after_calls: list[tuple[str, int]] = []
        self.events: asyncio.Queue[str | Exception | None] = asyncio.Queue()
        self.closed = False

    def add(self, *notifications: GuildNotification) -> None:
        for notification in notifications:
            self.feeds.setdefault(notification.instance_id, []).append(notification)

    def add_node(self, instance_id: str) -> None:
        self.feeds.setdefault(instance_id, [])

    def _fail(self, node: str | None = None) -> None:
        if self.error is not None:
            raise self.error
        if node is not None and node in self.down:
            raise self.down[node]

    async def nodes(self) -> list[str]:
        self._fail()
        self.nodes_calls += 1
        return list(self.feeds)

    async def newest(self, node: str, *, limit: int) -> GuildFeedPage:
        self.newest_calls.append((node, limit))
        self._fail(node)
        items = sorted(self.feeds.get(node, []), key=lambda item: item.seq, reverse=True)
        return GuildFeedPage(
            items=items[:limit],
            position=max((item.seq for item in items), default=0),
            unread_count=self.unread_counts.get(node, 0),
        )

    async def after(self, node: str, position: int, *, limit: int) -> GuildFeedPage:
        self.after_calls.append((node, position))
        self._fail(node)
        items = sorted(
            (item for item in self.feeds.get(node, []) if item.seq > position),
            key=lambda item: item.seq,
        )[:limit]
        return GuildFeedPage(
            items=items, position=max((item.seq for item in items), default=position)
        )

    async def notification_events(self) -> AsyncIterator[str]:
        while True:
            event = await self.events.get()
            if event is None:
                return
            if isinstance(event, Exception):
                raise event
            yield event

    async def close(self) -> None:
        self.closed = True


class FakeDeviceRepository(PushTokenRegistry):
    """Device registrations with the throttle state, on an injected clock."""

    def __init__(self, clock: Callable[[], datetime]) -> None:
        self._clock = clock
        self.devices: dict[UUID, DeviceToken] = {}
        self.owed: set[UUID] = set()
        self.deleted: list[DeviceToken] = []

    def add(self, *devices: DeviceToken) -> None:
        for item in devices:
            self.devices[item.id] = item

    def tokens(self, kind: PushTokenKind | None = None) -> list[str]:
        return sorted(
            item.token for item in self.devices.values() if kind is None or item.kind is kind
        )

    async def upsert(self, device: DeviceToken) -> DeviceToken:
        for existing in list(self.devices.values()):
            same = existing.token == device.token and existing.kind is device.kind
            replaced = (
                device.kind is PushTokenKind.LIVE_ACTIVITY
                and device.activity_id is not None
                and existing.owner_id == device.owner_id
                and existing.kind is device.kind
                and existing.activity_id == device.activity_id
            )
            if same or replaced:
                del self.devices[existing.id]
        # Like the device_tokens table: an omitted environment is stored as production.
        if device.environment is None:
            device = device.model_copy(update={"environment": PushEnvironment.PRODUCTION})
        self.devices[device.id] = device
        return device

    async def list_for_owner(
        self, owner_id: str, *, kinds: Sequence[PushTokenKind] | None = None
    ) -> list[DeviceToken]:
        return [
            item
            for item in self.devices.values()
            if item.owner_id == owner_id and (kinds is None or item.kind in kinds)
        ]

    async def delete(self, owner_id: str, token: str, *, kind: PushTokenKind | None = None) -> bool:
        matches = [
            item
            for item in self.devices.values()
            if item.owner_id == owner_id
            and item.token == token
            and (kind is None or item.kind is kind)
        ]
        for item in matches:
            del self.devices[item.id]
        return bool(matches)

    async def delete_registration(self, device_id: UUID) -> bool:
        removed = self.devices.pop(device_id, None)
        if removed is not None:
            self.deleted.append(removed)
        return removed is not None

    def _elapsed(self, item: DeviceToken, min_interval_seconds: float) -> bool:
        if item.last_push_at is None:
            return True
        return (self._clock() - item.last_push_at).total_seconds() >= min_interval_seconds

    async def claim_push(self, device_id: UUID, *, min_interval_seconds: float) -> bool:
        item = self.devices.get(device_id)
        if item is None:
            return False
        if not self._elapsed(item, min_interval_seconds):
            self.owed.add(device_id)
            return False
        item.last_push_at = self._clock()
        self.owed.discard(device_id)
        return True

    async def release_push(self, device_id: UUID) -> None:
        item = self.devices.get(device_id)
        if item is None:
            return
        item.last_push_at = None
        self.owed.add(device_id)

    async def count_by_environment(self) -> dict[str, dict[str, int]]:
        counts: dict[str, dict[str, int]] = {}
        for item in self.devices.values():
            env = (item.environment or PushEnvironment.PRODUCTION).value
            counts.setdefault(env, {})[item.kind.value] = (
                counts.get(env, {}).get(item.kind.value, 0) + 1
            )
        return counts

    async def claim_owed_pushes(
        self, kind: PushTokenKind, *, min_interval_seconds: float
    ) -> list[DeviceToken]:
        claimed = [
            item
            for device_id in sorted(self.owed, key=str)
            if (item := self.devices.get(device_id)) is not None
            and item.kind is kind
            and self._elapsed(item, min_interval_seconds)
        ]
        for item in claimed:
            item.last_push_at = self._clock()
            self.owed.discard(item.id)
        return claimed


@dataclass
class _Row:
    notification: GuildNotification
    created_at: datetime
    status: PushDeliveryStatus = PushDeliveryStatus.PENDING
    detail: str = ""
    attempts: int = 0
    next_attempt_at: datetime = START
    settled_at: datetime | None = None


class FakePushDeliveryRepository(PushDeliveryRepository):
    """The gateway's outbox and positions, on an injected clock."""

    def __init__(self, clock: Callable[[], datetime]) -> None:
        self._clock = clock
        self.positions: dict[str, int] = {}
        self.rows: dict[tuple[str, str], _Row] = {}
        self.pruned_before: list[datetime] = []

    def row(self, notification_id: str, instance_id: str = THOR) -> _Row:
        return self.rows[(instance_id, notification_id)]

    def statuses(self) -> dict[str, str]:
        return {key[1]: row.status.value for key, row in self.rows.items()}

    async def load_positions(self) -> dict[str, int]:
        return dict(self.positions)

    async def save_positions(self, positions: Mapping[str, int]) -> None:
        self.positions.update(positions)

    async def record(
        self, notifications: list[GuildNotification], positions: Mapping[str, int]
    ) -> list[GuildNotification]:
        fresh = []
        for notification in notifications:
            key = (notification.instance_id, notification.id)
            if key in self.rows:
                continue
            now = self._clock()
            self.rows[key] = _Row(notification=notification, created_at=now, next_attempt_at=now)
            fresh.append(notification)
        self.positions.update(positions)
        return fresh

    async def claim_due(self, *, limit: int, lease_seconds: float) -> list[PushDelivery]:
        now = self._clock()
        due = [
            row
            for row in self.rows.values()
            if row.status is PushDeliveryStatus.PENDING and row.next_attempt_at <= now
        ][:limit]
        claimed = []
        for row in due:
            row.attempts += 1
            row.next_attempt_at = now + timedelta(seconds=lease_seconds)
            claimed.append(
                PushDelivery(
                    notification=row.notification, attempts=row.attempts, created_at=row.created_at
                )
            )
        return claimed

    def _claimed(self, delivery: PushDelivery) -> _Row | None:
        row = self.rows.get((delivery.notification.instance_id, delivery.notification.id))
        if row is None or row.status is not PushDeliveryStatus.PENDING:
            return None
        return row if row.attempts == delivery.attempts else None

    async def settle(self, delivery: PushDelivery, status: PushDeliveryStatus, detail: str) -> bool:
        row = self._claimed(delivery)
        if row is None:
            return False
        row.status, row.detail, row.settled_at = status, detail, self._clock()
        return True

    async def retry(self, delivery: PushDelivery, *, detail: str, retry_at: datetime) -> bool:
        row = self._claimed(delivery)
        if row is None:
            return False
        row.detail, row.next_attempt_at = detail, retry_at
        return True

    async def count_sent_since(self, owner_id: str, since: datetime) -> int:
        return sum(
            1
            for row in self.rows.values()
            if row.notification.owner_id == owner_id
            and row.status is PushDeliveryStatus.SENT
            and row.settled_at is not None
            and row.settled_at >= since
        )

    async def prune_settled(self, before: datetime) -> int:
        self.pruned_before.append(before)
        stale = [
            key
            for key, row in self.rows.items()
            if row.settled_at is not None and row.settled_at < before
        ]
        for key in stale:
            del self.rows[key]
        return len(stale)


@dataclass
class FakeApns(ApplePushClient):
    """Records every push; answers 200 unless a token has a scripted answer."""

    sent: list[ApnsPush] = field(default_factory=list)
    answers: dict[str, list[ApnsResult]] = field(default_factory=dict)
    closed: bool = False

    def answer(self, token: str, *results: ApnsResult) -> None:
        self.answers.setdefault(token, []).extend(results)

    async def send(self, push: ApnsPush) -> ApnsResult:
        self.sent.append(push)
        queued = self.answers.get(push.token)
        if queued:
            return queued.pop(0)
        return ApnsResult(status_code=200, apns_id=f"apns-{len(self.sent)}")

    async def close(self) -> None:
        self.closed = True

    def of_type(self, push_type: str) -> list[ApnsPush]:
        return [push for push in self.sent if push.push_type.value == push_type]


def alert_settings(**overrides) -> AlertSettings:
    values = {
        "enabled": True,
        "kinds": frozenset({"milestone", "decision", "attention", "error"}),
        "min_severity": NotificationSeverity.INFO,
        "sources": frozenset(),
        "attention_kinds": frozenset({"attention", "decision", "error"}),
        "low_priority_kinds": frozenset(),
        "body_chars": 280,
        "expiration_seconds": 3600,
        "max_age_seconds": 900.0,
        "rate_limit": None,
        "quiet_hours": None,
        "category": "FORGE_ACTIVITY",
        "sound": "default",
        "deep_link_base": "lexichat://activity",
        "session_subtitle": True,
        "batch_size": 20,
        "lease_seconds": 60.0,
        "max_attempts": 3,
        "backoff_base_seconds": 15.0,
        "backoff_max_seconds": 600.0,
    }
    values.update(overrides)
    return AlertSettings(**values)


def gateway_settings(
    *,
    alerts: AlertSettings | None = None,
    live_activity: LiveActivitySettings | None = None,
    widgets: WidgetSettings | None = None,
    background: BackgroundSettings | None = None,
    **overrides,
) -> PushGatewaySettings:
    values = {
        "bundle_id": BUNDLE,
        "poll_interval_seconds": 30.0,
        "stream_enabled": True,
        "stream_retry_seconds": 5.0,
        "page_size": 50,
        "feed_page_size": 20,
        "catch_up_items": 0,
        "discovery_interval_seconds": 300.0,
        "node_retry_seconds": 30.0,
        "feedless_recheck_seconds": 3600.0,
        "retention_seconds": 604800.0,
        "public_api_url": "http://thor.tail737f2a.ts.net:8080",
        "origin_label": "Thor",
        "alerts": alerts or alert_settings(),
        "live_activity": live_activity
        or LiveActivitySettings(
            enabled=True,
            priority=10,
            stale_seconds=28800,
            start_enabled=False,
            start_min_interval_seconds=3600.0,
            relevance_score=10,
        ),
        "widgets": widgets or WidgetSettings(enabled=True, priority=5, min_interval_seconds=300.0),
        # Off unless a test turns it on: most tests count the pushes of one type.
        "background": background
        or BackgroundSettings(enabled=False, priority=5, min_interval_seconds=900.0),
    }
    values.update(overrides)
    return PushGatewaySettings(**values)
