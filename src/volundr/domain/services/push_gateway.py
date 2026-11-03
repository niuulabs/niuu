"""The Guild push gateway: Apple pushes for every notification in the Guild.

One Forge node (the gateway) holds the APNs key and every registered device. It
reads the Guild's merged notification feed and, for each new notification on any
node:

1. **Ingest.** Reads each node's feed after its stored position and records every
   new notification as a pending alert, with the advanced positions, in one
   transaction. Live events (this node's own, and optionally the Guild's stream)
   only wake the loop early; the feed is the source of truth and is read at least
   every poll interval, so a missed event or a restart loses nothing. Nodes are
   read separately and at the same time: one that does not answer fails only its
   own read and is left alone for ``node_retry_seconds``, so it never holds up
   the pushes for the others.
2. **Alert.** Claims pending alerts with a lease and settles each: ``suppressed``
   by the alert policy (kind, severity, source, age, quiet hours, rate limit),
   ``skipped`` when the owner has no alert device, ``sent`` when APNs accepted it
   for a device, retried with backoff when APNs was unreachable or throttling,
   ``failed`` otherwise.
3. **Live Activity.** Sends every Live Activity token of the owner an ``update``
   with the newest notifications of the Guild, or a push-to-start when the owner
   has no running activity (when enabled, at most once per interval).
4. **Widgets.** Reloads the owner's widgets, at most once per interval per token;
   a reload held back by the interval is sent when the interval ends.
5. **Background.** Sends the app a silent push through its alert token, with the
   same interval rule, so iOS can wake it to read every server it knows.

A token APNs reports as dead (410, ``BadDeviceToken``, ``Unregistered``,
``ExpiredToken``) is deleted.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from niuu.domain.notifications import NotificationSeverity, severity_rank
from volundr.domain.models import DevicePlatform, DeviceToken, EventType
from volundr.domain.notifications import NotificationQuietHours, NotificationRateLimit
from volundr.domain.ports import EventBroadcaster, PushTokenRegistry
from volundr.domain.push import (
    APNS_MAX_COLLAPSE_ID_BYTES,
    APNS_PRIORITY_IMMEDIATE,
    APNS_PRIORITY_POWER_SAVING,
    LIVE_ACTIVITY_MAX_ITEMS,
    LIVE_ACTIVITY_TITLE_CHARS,
    ApnsPush,
    ApnsPushType,
    ApnsResult,
    EnvironmentAssumed,
    GuildNotification,
    InterruptionLevel,
    LiveActivityEvent,
    PushTestKind,
    PushTokenKind,
    absolute_url,
    activity_deep_link,
    alert_payload,
    apns_topic,
    background_payload,
    clip,
    live_activity_content_state,
    live_activity_payload,
    shorten,
    token_fingerprint,
    widget_payload,
)
from volundr.domain.push_ports import (
    ApplePushClient,
    GuildFeed,
    GuildFeedError,
    GuildFeedPage,
    GuildNodeHasNoFeedError,
    PushDelivery,
    PushDeliveryRepository,
    PushDeliveryStatus,
)

logger = logging.getLogger(__name__)

NO_ALERT_DEVICE = "The owner has no alert device registered"
ALERTS_DISABLED = "Alerts are switched off (push.gateway.alerts.enabled)"
ALREADY_READ = "Already read when the gateway picked it up"

#: Tokens pushed on every new notification, whatever its kind. The alert token is
#: among them for the silent background push.
_SURFACE_KINDS = (
    PushTokenKind.LIVE_ACTIVITY,
    PushTokenKind.LIVE_ACTIVITY_START,
    PushTokenKind.WIDGET,
    PushTokenKind.ALERT,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class AlertSettings:
    """The gateway's alert rule and delivery limits (``push.gateway.alerts``)."""

    enabled: bool
    kinds: frozenset[str]
    min_severity: NotificationSeverity
    #: Empty means any source.
    sources: frozenset[str]
    attention_kinds: frozenset[str]
    low_priority_kinds: frozenset[str]
    body_chars: int
    expiration_seconds: int
    max_age_seconds: float
    rate_limit: NotificationRateLimit | None
    quiet_hours: NotificationQuietHours | None
    category: str
    sound: str
    deep_link_base: str
    session_subtitle: bool
    batch_size: int
    lease_seconds: float
    max_attempts: int
    backoff_base_seconds: float
    backoff_max_seconds: float

    def __post_init__(self) -> None:
        if self.batch_size < 1 or self.max_attempts < 1 or self.body_chars < 1:
            raise ValueError("alerts batch_size, max_attempts and body_chars must be >= 1")
        if self.lease_seconds <= 0 or self.max_age_seconds <= 0:
            raise ValueError("alerts lease_seconds and max_age_seconds must be positive")
        if self.backoff_base_seconds > self.backoff_max_seconds:
            raise ValueError("alerts backoff_base_seconds must not exceed backoff_max_seconds")
        if not self.deep_link_base:
            raise ValueError("alerts deep_link_base must not be empty")


@dataclass(frozen=True)
class LiveActivitySettings:
    """``push.gateway.live_activity``."""

    enabled: bool
    priority: int
    stale_seconds: int
    start_enabled: bool
    start_min_interval_seconds: float
    relevance_score: float


@dataclass(frozen=True)
class WidgetSettings:
    """``push.gateway.widgets``."""

    enabled: bool
    priority: int
    min_interval_seconds: float


@dataclass(frozen=True)
class BackgroundSettings:
    """``push.gateway.background``."""

    enabled: bool
    priority: int
    min_interval_seconds: float


@dataclass(frozen=True)
class PushGatewaySettings:
    """Everything the gateway loop needs, from ``push.apns`` and ``push.gateway``."""

    bundle_id: str
    poll_interval_seconds: float
    stream_enabled: bool
    stream_retry_seconds: float
    page_size: int
    feed_page_size: int
    catch_up_items: int
    discovery_interval_seconds: float
    node_retry_seconds: float
    feedless_recheck_seconds: float
    retention_seconds: float
    public_api_url: str
    origin_label: str
    alerts: AlertSettings
    live_activity: LiveActivitySettings
    widgets: WidgetSettings
    background: BackgroundSettings

    def __post_init__(self) -> None:
        if not self.bundle_id:
            raise ValueError("The push gateway needs the app's bundle id (push.apns.bundle_id)")
        if self.page_size < 1 or self.feed_page_size < LIVE_ACTIVITY_MAX_ITEMS:
            raise ValueError(
                f"page_size must be >= 1 and feed_page_size >= {LIVE_ACTIVITY_MAX_ITEMS}"
            )
        if self.poll_interval_seconds <= 0 or self.stream_retry_seconds <= 0:
            raise ValueError("poll_interval_seconds and stream_retry_seconds must be positive")
        if self.catch_up_items < 0 or self.node_retry_seconds < 0:
            raise ValueError("catch_up_items and node_retry_seconds must not be negative")


@dataclass
class PushGatewayStats:
    """Counters since start, for the status route and the logs."""

    ticks: int = 0
    ingested: int = 0
    alerts_sent: int = 0
    alerts_suppressed: int = 0
    alerts_skipped: int = 0
    alerts_failed: int = 0
    alerts_retried: int = 0
    live_activity_updates: int = 0
    live_activity_starts: int = 0
    widget_reloads: int = 0
    background_wakes: int = 0
    tokens_pruned: int = 0
    apns_rejections: int = 0
    stale_settles: int = 0
    node_failures: int = 0
    feedless_rechecks: int = 0
    loop_errors: int = 0
    stream_reconnects: int = 0
    #: APNs answers per environment of the token: {"production": {"accepted": n, ...}}.
    apns_by_environment: dict[str, dict[str, int]] = field(default_factory=dict)
    #: Registrations stored as production without saying so: no environment, or an
    #: unknown one. Each is also logged as a warning.
    registrations_environment_missing: int = 0
    registrations_environment_unknown: int = 0
    last_tick_at: datetime | None = None
    last_error: str = ""


@dataclass(frozen=True)
class PushTestResult:
    """APNs' answer for one token of a test push. The token is never shown whole."""

    kind: str
    environment: str
    token_suffix: str
    accepted: bool
    detail: str
    apns_id: str = ""


#: How much of a token a test result shows, enough to tell two devices apart.
_TOKEN_SUFFIX_CHARS = 8


@dataclass
class _Tick:
    """One tick's lazily read newest page (shared by every owner's Live Activity)."""

    load: Callable[[], Awaitable[GuildFeedPage | None]]
    page: GuildFeedPage | None = None
    loaded: bool = False
    retry_owners: set[str] = field(default_factory=set)
    #: Tokens whose throttled push APNs could not take in this tick.
    undelivered: set[UUID] = field(default_factory=set)

    async def newest(self) -> GuildFeedPage | None:
        """The merged newest page, or ``None`` while no node answers."""
        if not self.loaded:
            self.page = await self.load()
            self.loaded = True
        return self.page


class PushGateway:
    """Reads the Guild feed and pushes to the owners' Apple devices."""

    def __init__(
        self,
        feed: GuildFeed,
        devices: PushTokenRegistry,
        deliveries: PushDeliveryRepository,
        apns: ApplePushClient,
        settings: PushGatewaySettings,
        *,
        local_events: EventBroadcaster | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._feed = feed
        self._local_events = local_events
        self._devices = devices
        self._deliveries = deliveries
        self._apns = apns
        self._settings = settings
        self._clock = clock
        self.stats = PushGatewayStats()
        self._positions: dict[str, int] | None = None
        #: The Guild's Forge nodes as its registry last listed them.
        self._nodes: list[str] = []
        #: Nodes that did not answer: their last error, and when to try them again.
        self._node_errors: dict[str, str] = {}
        self._feedless: dict[str, str] = {}
        self._node_retry_at: dict[str, datetime] = {}
        self._discover = True
        self._last_discovery: datetime | None = None
        self._last_prune: datetime | None = None
        self._retry_owners: set[str] = set()
        self._wake = asyncio.Event()
        self._stopping = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._stream_task: asyncio.Task[None] | None = None
        self._local_task: asyncio.Task[None] | None = None

    # -- Lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._loop(), name="push-gateway")
        if self._settings.stream_enabled:
            self._stream_task = asyncio.create_task(self._listen(), name="push-gateway-stream")
        if self._local_events is not None:
            self._local_task = asyncio.create_task(
                self._listen_locally(self._local_events), name="push-gateway-local-events"
            )
        logger.info(
            "Push gateway started: alerts=%s live_activity=%s widgets=%s",
            self._settings.alerts.enabled,
            self._settings.live_activity.enabled,
            self._settings.widgets.enabled,
        )

    async def stop(self) -> None:
        self._stopping.set()
        self._wake.set()
        for task in (self._local_task, self._stream_task, self._task):
            if task is None:
                continue
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self._task = self._stream_task = self._local_task = None
        await self._feed.close()
        await self._apns.close()

    def wake(self) -> None:
        """Run a tick now instead of waiting for the poll interval."""
        self._wake.set()

    async def _loop(self) -> None:
        while not self._stopping.is_set():
            # Cleared before the tick, so an event arriving mid-tick runs another one.
            self._wake.clear()
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except GuildFeedError as exc:
                self.stats.loop_errors += 1
                if str(exc) != self.stats.last_error:
                    # Said once when it starts, not on every poll while it lasts.
                    logger.warning("Push gateway could not read the Guild: %s", exc)
                self.stats.last_error = str(exc)
            except Exception as exc:
                self.stats.loop_errors += 1
                self.stats.last_error = type(exc).__name__
                logger.exception("Push gateway tick failed")
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    self._wake.wait(), timeout=self._settings.poll_interval_seconds
                )

    async def _listen(self) -> None:
        """Wake the loop on every live notification event; reconnect when lost."""
        while not self._stopping.is_set():
            try:
                async for instance_id in self._feed.notification_events():
                    self.note_event(instance_id)
            except asyncio.CancelledError:
                raise
            except GuildFeedError as exc:
                logger.warning("Push gateway event stream lost: %s", exc)
            except Exception:
                logger.exception("Push gateway event stream failed")
            self.stats.stream_reconnects += 1
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stopping.wait(), timeout=self._settings.stream_retry_seconds
                )

    async def _listen_locally(self, events: EventBroadcaster) -> None:
        """Wake the loop when this node itself records a notification (no network)."""
        async for event in events.subscribe():
            if event.type is EventType.SESSION_NOTIFICATION:
                self._wake.set()

    def note_event(self, instance_id: str) -> None:
        """A notification was recorded on ``instance_id``: read the feed now."""
        if self._positions is not None and instance_id not in self._positions:
            # A node the gateway is not reading yet: find it before reading.
            self._discover = True
        self._wake.set()

    # -- One tick ----------------------------------------------------------

    async def run_once(self) -> int:
        """Ingest, alert and refresh the surfaces once; returns how many were new."""
        now = self._clock()
        positions = await self._positions_for_tick(now)
        fresh = await self._ingest(positions, now)
        await self._deliver_alerts()
        tick = _Tick(load=self._newest_page)
        # An owner leaves the set only once refreshed: if this tick fails part-way
        # (the feed, the database), the next one still owes them their surfaces.
        self._retry_owners |= {notification.owner_id for notification in fresh}
        for owner_id in sorted(self._retry_owners):
            await self._refresh_surfaces(owner_id, tick)
            self._retry_owners.discard(owner_id)
        await self._send_owed_pushes(tick)
        self._retry_owners |= tick.retry_owners
        await self._prune(now)
        self.stats.ticks += 1
        self.stats.ingested += len(fresh)
        self.stats.last_tick_at = now
        self.stats.last_error = ""
        return len(fresh)

    # -- Reading the Guild, node by node -----------------------------------

    def _readable(self, nodes: list[str], now: datetime) -> list[str]:
        """``nodes`` minus those that failed lately and are not due for a retry."""
        return [
            node
            for node in nodes
            if node not in self._node_retry_at or self._node_retry_at[node] <= now
        ]

    async def _read_nodes(
        self,
        nodes: list[str],
        read: Callable[[str], Awaitable[GuildFeedPage]],
        now: datetime,
    ) -> dict[str, GuildFeedPage]:
        """Read several nodes at once; a node that fails is set aside, not fatal."""
        results = await asyncio.gather(*(read(node) for node in nodes), return_exceptions=True)
        pages: dict[str, GuildFeedPage] = {}
        for node, result in zip(nodes, results, strict=True):
            if isinstance(result, GuildFeedError):
                self._node_failed(node, result, now)
                continue
            if isinstance(result, BaseException):
                raise result
            self._node_answered(node)
            pages[node] = result
        return pages

    def _node_failed(self, node: str, error: GuildFeedError, now: datetime) -> None:
        text = str(error)
        if isinstance(error, GuildNodeHasNoFeedError):
            # Not an outage: the node has nothing to push until it gets the feed.
            wait = self._settings.feedless_recheck_seconds
            self._node_retry_at[node] = now + timedelta(seconds=wait)
            self.stats.feedless_rechecks += 1
            self._node_errors.pop(node, None)
            if node not in self._feedless:
                logger.info("Push gateway skips Guild node %s: %s", node, text)
            self._feedless[node] = text
            return
        self._feedless.pop(node, None)
        self._node_retry_at[node] = now + timedelta(seconds=self._settings.node_retry_seconds)
        self.stats.node_failures += 1
        if self._node_errors.get(node) == text:
            return  # Logged when it started; say it once, not on every retry.
        self._node_errors[node] = text
        logger.warning("Push gateway cannot read Guild node %s: %s", node, text)

    def _node_answered(self, node: str) -> None:
        self._node_retry_at.pop(node, None)
        self._feedless.pop(node, None)
        if self._node_errors.pop(node, None) is not None:
            logger.info("Push gateway reads Guild node %s again", node)

    async def _newest_page(self) -> GuildFeedPage | None:
        """The Guild's newest notifications, merged from every node that answers.

        ``None`` while no node answers: there is nothing to show yet.
        """
        now = self._clock()
        positions = self._positions or {}
        nodes = self._readable([node for node in self._nodes if node in positions], now)
        limit = self._settings.feed_page_size
        pages = await self._read_nodes(
            nodes, lambda node: self._feed.newest(node, limit=limit), now
        )
        if not pages:
            return None
        merged = sorted(
            (item for page in pages.values() for item in page.items),
            key=_recorded_order,
            reverse=True,
        )
        return GuildFeedPage(
            items=merged[:limit],
            unread_count=sum(page.unread_count for page in pages.values()),
        )

    async def _positions_for_tick(self, now: datetime) -> dict[str, int]:
        if self._positions is None:
            self._positions = await self._deliveries.load_positions()
        if self._discovery_due(self._positions, now):
            await self._discover_nodes(self._positions, now)
        return self._positions

    def _discovery_due(self, positions: dict[str, int], now: datetime) -> bool:
        interval = self._settings.discovery_interval_seconds
        last = self._last_discovery
        if self._discover or last is None or (now - last).total_seconds() >= interval:
            return True
        # A listed node that did not answer when it was found is tried again as
        # soon as its retry time comes, not only at the next interval.
        waiting = [node for node in self._nodes if node not in positions]
        return bool(self._readable(waiting, now))

    async def _discover_nodes(self, positions: dict[str, int], now: datetime) -> None:
        """List the Guild's nodes and start reading those without a position.

        A new node starts ``catch_up_items`` before its head, so a notification
        recorded just before the node was found is still pushed; older ones are
        never replayed, and the alert policy's age limit holds back stale ones.
        A node whose feed restarted (its head is behind the stored position, as
        after its database was recreated) is read from its head again. A node that
        does not answer is tried again at the next discovery.
        """
        self._nodes = await self._feed.nodes()
        heads = await self._read_nodes(
            self._readable(self._nodes, now), lambda node: self._feed.newest(node, limit=1), now
        )
        found = {
            node: max(0, page.position - self._settings.catch_up_items)
            for node, page in heads.items()
            if node not in positions
        }
        rewound = {
            node: page.position
            for node, page in heads.items()
            if node in positions and page.position < positions[node]
        }
        if found or rewound:
            await self._deliveries.save_positions({**found, **rewound})
            positions.update(found)
            positions.update(rewound)
        if found:
            logger.info("Push gateway now reads %d more Guild node(s)", len(found))
        if rewound:
            logger.warning(
                "Push gateway restarts %d Guild node(s) whose feed went back: %s",
                len(rewound),
                ", ".join(sorted(rewound)),
            )
        self._discover = False
        self._last_discovery = now

    async def _ingest(self, positions: dict[str, int], now: datetime) -> list[GuildNotification]:
        fresh: list[GuildNotification] = []
        limit = self._settings.page_size
        nodes = self._readable([node for node in self._nodes if node in positions], now)
        while nodes and not self._stopping.is_set():
            pages = await self._read_nodes(
                nodes, lambda node: self._feed.after(node, positions[node], limit=limit), now
            )
            items = sorted(
                (item for page in pages.values() for item in page.items), key=_recorded_order
            )
            advanced = {
                node: page.position
                for node, page in pages.items()
                if page.position != positions[node]
            }
            if not items and not advanced:
                break
            fresh.extend(await self._deliveries.record(items, advanced))
            positions.update(advanced)
            # A full page may have more behind it: read those nodes again.
            nodes = [node for node, page in pages.items() if len(page.items) >= limit]
        return fresh

    # -- Alerts ------------------------------------------------------------

    async def _deliver_alerts(self) -> None:
        alerts = self._settings.alerts
        while not self._stopping.is_set():
            batch = await self._deliveries.claim_due(
                limit=alerts.batch_size, lease_seconds=alerts.lease_seconds
            )
            for delivery in batch:
                await self._deliver_alert(delivery)
            if len(batch) < alerts.batch_size:
                return

    async def _deliver_alert(self, delivery: PushDelivery) -> None:
        alerts, notification = self._settings.alerts, delivery.notification
        now = self._clock()
        if delivery.attempts > alerts.max_attempts:
            await self._settle(
                delivery,
                PushDeliveryStatus.FAILED,
                f"Gave up after {delivery.attempts - 1} attempts",
            )
            return
        reason = self._hold_back_reason(notification, now)
        if reason:
            await self._settle(delivery, PushDeliveryStatus.SUPPRESSED, reason)
            return
        devices = await self._ios_devices(notification.owner_id, (PushTokenKind.ALERT,))
        if not devices:
            await self._settle(delivery, PushDeliveryStatus.SKIPPED, NO_ALERT_DEVICE)
            return
        limit = alerts.rate_limit
        if limit is not None:
            since = now - timedelta(seconds=limit.window_seconds)
            sent = await self._deliveries.count_sent_since(notification.owner_id, since)
            if sent >= limit.max_count:
                await self._settle(
                    delivery,
                    PushDeliveryStatus.SUPPRESSED,
                    f"Rate limit: at most {limit.max_count} alerts per {limit.window_seconds}s",
                )
                return
        results = [
            await self._send(device, self._alert_push(notification, device, now))
            for device in devices
        ]
        accepted = sum(1 for result in results if result.accepted)
        if accepted:
            await self._settle(
                delivery,
                PushDeliveryStatus.SENT,
                f"APNs accepted {accepted} of {len(results)} devices",
            )
            return
        detail = "APNs: " + "; ".join(sorted({result.describe() for result in results}))
        if any(result.retryable for result in results) and delivery.attempts < alerts.max_attempts:
            waits = [r.retry_after for r in results if r.retry_after is not None]
            delay = max([self._backoff(delivery.attempts), *waits])
            if await self._deliveries.retry(
                delivery, detail=detail, retry_at=now + timedelta(seconds=delay)
            ):
                self.stats.alerts_retried += 1
                return
            self.stats.stale_settles += 1
            return
        await self._settle(delivery, PushDeliveryStatus.FAILED, detail)

    def _backoff(self, attempts: int) -> float:
        alerts = self._settings.alerts
        return min(alerts.backoff_max_seconds, alerts.backoff_base_seconds * 2 ** (attempts - 1))

    def _hold_back_reason(self, notification: GuildNotification, now: datetime) -> str | None:
        """Why the alert policy does not alert for ``notification``, else ``None``."""
        alerts = self._settings.alerts
        if not alerts.enabled:
            return ALERTS_DISABLED
        if notification.kind not in alerts.kinds:
            return f"Kind {notification.kind!r} is not alerted (push.gateway.alerts.kinds)"
        if severity_rank(notification.severity) < severity_rank(alerts.min_severity):
            return f"Below the alert severity {alerts.min_severity.value!r}"
        if alerts.sources and notification.source not in alerts.sources:
            return f"Source {notification.source!r} is not alerted (push.gateway.alerts.sources)"
        if notification.read:
            return ALREADY_READ
        created_at = notification.created_at
        if created_at is not None and (now - created_at).total_seconds() > alerts.max_age_seconds:
            return f"Older than {alerts.max_age_seconds:g}s when the gateway picked it up"
        quiet = alerts.quiet_hours
        if quiet is not None and quiet.holds_back(_severity(notification), now):
            return (
                f"Quiet hours {quiet.start}-{quiet.end} {quiet.timezone} "
                f"(only {quiet.allow_min_severity.value} and above are alerted)"
            )
        return None

    def _alert_push(
        self, notification: GuildNotification, device: DeviceToken, now: datetime
    ) -> ApnsPush:
        alerts = self._settings.alerts
        attention = notification.kind in alerts.attention_kinds
        level = InterruptionLevel.TIME_SENSITIVE if attention else InterruptionLevel.ACTIVE
        priority = (
            APNS_PRIORITY_POWER_SAVING
            if notification.kind in alerts.low_priority_kinds
            else APNS_PRIORITY_IMMEDIATE
        )
        session_name = notification.session_name
        subtitle = (
            session_name
            if alerts.session_subtitle and session_name and session_name not in notification.title
            else None
        )
        base = self._settings.public_api_url
        payload = alert_payload(
            title=notification.title,
            body=shorten(notification.body, alerts.body_chars),
            subtitle=subtitle,
            deep_link=activity_deep_link(
                alerts.deep_link_base,
                notification.id,
                instance_id=notification.instance_id,
                session_id=notification.session_id,
                turn_id=notification.turn_id,
            ),
            interruption_level=level,
            category=alerts.category,
            sound=alerts.sound,
            notification=notification,
            attention=attention,
            image_url=absolute_url(base, notification.image_url),
            image_thumbnail_url=absolute_url(base, notification.image_thumbnail_url),
        )
        collapse_id = notification.id
        return ApnsPush(
            token=device.token,
            environment=device.environment,
            topic=self._topic(device, ApnsPushType.ALERT),
            push_type=ApnsPushType.ALERT,
            priority=priority,
            payload=payload,
            collapse_id=(
                collapse_id
                if len(collapse_id.encode("utf-8")) <= APNS_MAX_COLLAPSE_ID_BYTES
                else None
            ),
            expiration=int(now.timestamp()) + alerts.expiration_seconds,
        )

    async def _settle(
        self, delivery: PushDelivery, status: PushDeliveryStatus, detail: str
    ) -> None:
        if not await self._deliveries.settle(delivery, status, detail):
            self.stats.stale_settles += 1
            return
        match status:
            case PushDeliveryStatus.SENT:
                self.stats.alerts_sent += 1
            case PushDeliveryStatus.SUPPRESSED:
                self.stats.alerts_suppressed += 1
            case PushDeliveryStatus.SKIPPED:
                self.stats.alerts_skipped += 1
            case PushDeliveryStatus.FAILED:
                self.stats.alerts_failed += 1
                logger.warning(
                    "Push alert for notification %s failed: %s", delivery.notification.id, detail
                )

    # -- Live Activity and widgets -------------------------------------------

    async def _refresh_surfaces(self, owner_id: str, tick: _Tick) -> None:
        devices = await self._ios_devices(owner_id, _SURFACE_KINDS)
        if not devices:
            return
        by_kind: dict[PushTokenKind, list[DeviceToken]] = {kind: [] for kind in _SURFACE_KINDS}
        for device in devices:
            by_kind[device.kind].append(device)
        live = by_kind[PushTokenKind.LIVE_ACTIVITY]
        starts = by_kind[PushTokenKind.LIVE_ACTIVITY_START]
        if self._settings.live_activity.enabled and (live or starts):
            if await self._refresh_live_activity(owner_id, live, starts, tick):
                tick.retry_owners.add(owner_id)
        for kind in (PushTokenKind.WIDGET, PushTokenKind.ALERT):
            enabled, interval = self._throttle(kind)
            if not enabled:
                continue
            for device in by_kind[kind]:
                if await self._devices.claim_push(device.id, min_interval_seconds=interval):
                    await self._send_throttled(device, tick)

    async def _refresh_live_activity(
        self,
        owner_id: str,
        live: list[DeviceToken],
        starts: list[DeviceToken],
        tick: _Tick,
    ) -> bool:
        """Update (or start) the owner's Live Activity; True when a retry is owed."""
        settings = self._settings.live_activity
        page = await tick.newest()
        if page is None:
            return True  # No node answers right now: the update stays owed.
        now = self._clock()
        items = self._owner_items(owner_id, page)
        if not items:
            return False
        state = self._content_state(items, page, now)
        retry = False
        running = 0
        for device in live:
            result = await self._send(
                device, self._live_activity_push(device, LiveActivityEvent.UPDATE, state, now)
            )
            if result.accepted:
                self.stats.live_activity_updates += 1
            retry = retry or (not result.accepted and result.retryable)
            running += 0 if result.token_is_dead else 1
        if running or not settings.start_enabled:
            return retry
        # No activity is running for this owner: start one where the app allowed it.
        newest = items[0]
        for device in starts:
            claimed = await self._devices.claim_push(
                device.id, min_interval_seconds=settings.start_min_interval_seconds
            )
            if not claimed:
                continue
            result = await self._send(
                device,
                self._live_activity_push(
                    device,
                    LiveActivityEvent.START,
                    state,
                    now,
                    alert_title=clip(newest.title, LIVE_ACTIVITY_TITLE_CHARS),
                    alert_body=newest.session_name or self._settings.origin_label,
                ),
            )
            if result.accepted:
                self.stats.live_activity_starts += 1
                continue
            if result.retryable:
                await self._devices.release_push(device.id)
                retry = True
        return retry

    @staticmethod
    def _owner_items(owner_id: str, page: GuildFeedPage) -> list[GuildNotification]:
        return [item for item in page.items if item.owner_id == owner_id][:LIVE_ACTIVITY_MAX_ITEMS]

    def _content_state(
        self, items: list[GuildNotification], page: GuildFeedPage, now: datetime
    ) -> dict[str, Any]:
        return live_activity_content_state(
            items,
            unread_count=page.unread_count,
            updated_at=now,
            origin=self._settings.origin_label or None,
        )

    def _live_activity_push(
        self,
        device: DeviceToken,
        event: LiveActivityEvent,
        state: dict[str, Any],
        now: datetime,
        *,
        alert_title: str = "",
        alert_body: str = "",
    ) -> ApnsPush:
        settings = self._settings.live_activity
        timestamp = int(now.timestamp())
        return ApnsPush(
            token=device.token,
            environment=device.environment,
            topic=self._topic(device, ApnsPushType.LIVE_ACTIVITY),
            push_type=ApnsPushType.LIVE_ACTIVITY,
            priority=settings.priority,
            payload=live_activity_payload(
                event,
                state,
                timestamp=timestamp,
                stale_date=timestamp + settings.stale_seconds,
                relevance_score=settings.relevance_score,
                alert_title=alert_title,
                alert_body=alert_body,
            ),
        )

    def _throttle(self, kind: PushTokenKind) -> tuple[bool, float]:
        """Whether the throttled push of ``kind`` tokens is on, and its interval:
        the widget reload for widget tokens, the background push for alert tokens."""
        if kind is PushTokenKind.WIDGET:
            return self._settings.widgets.enabled, self._settings.widgets.min_interval_seconds
        background = self._settings.background
        return background.enabled, background.min_interval_seconds

    def _widget_push(self, device: DeviceToken) -> ApnsPush:
        return ApnsPush(
            token=device.token,
            environment=device.environment,
            topic=self._topic(device, ApnsPushType.WIDGETS),
            push_type=ApnsPushType.WIDGETS,
            priority=self._settings.widgets.priority,
            payload=widget_payload(),
        )

    def _background_push(self, device: DeviceToken) -> ApnsPush:
        return ApnsPush(
            token=device.token,
            environment=device.environment,
            topic=self._topic(device, ApnsPushType.BACKGROUND),
            push_type=ApnsPushType.BACKGROUND,
            priority=self._settings.background.priority,
            payload=background_payload(),
        )

    async def _send_throttled(self, device: DeviceToken, tick: _Tick) -> ApnsResult:
        """Send a claimed widget reload or background push."""
        widget = device.kind is PushTokenKind.WIDGET
        push = self._widget_push(device) if widget else self._background_push(device)
        result = await self._send(device, push)
        if result.accepted:
            if widget:
                self.stats.widget_reloads += 1
            else:
                self.stats.background_wakes += 1
            return result
        if result.retryable:
            # Not delivered: the push stays owed and goes out on a later tick.
            await self._devices.release_push(device.id)
            tick.undelivered.add(device.id)
        return result

    async def _send_owed_pushes(self, tick: _Tick) -> None:
        """Pushes the interval held back earlier, now that it has passed: the last
        change always reaches the widgets and wakes the app."""
        for kind in (PushTokenKind.WIDGET, PushTokenKind.ALERT):
            enabled, interval = self._throttle(kind)
            if not enabled:
                continue
            owed = await self._devices.claim_owed_pushes(kind, min_interval_seconds=interval)
            for device in owed:
                if device.id in tick.undelivered:
                    # APNs could not take it a moment ago: the next tick tries again.
                    await self._devices.release_push(device.id)
                    continue
                if device.platform is DevicePlatform.IOS:
                    await self._send_throttled(device, tick)

    # -- Shared ------------------------------------------------------------

    def _topic(self, device: DeviceToken, push_type: ApnsPushType) -> str:
        return apns_topic(device.app_bundle_id or self._settings.bundle_id, push_type)

    async def _ios_devices(
        self, owner_id: str, kinds: tuple[PushTokenKind, ...]
    ) -> list[DeviceToken]:
        devices = await self._devices.list_for_owner(owner_id, kinds=kinds)
        return [device for device in devices if device.platform is DevicePlatform.IOS]

    async def _send(self, device: DeviceToken, push: ApnsPush) -> ApnsResult:
        """Send one push; a token APNs reports as dead is deleted."""
        result = await self._apns.send(push)
        counts = self.stats.apns_by_environment.setdefault(push.environment.value, {})
        outcome = "accepted" if result.accepted else "rejected"
        counts[outcome] = counts.get(outcome, 0) + 1
        if result.accepted:
            return result
        self.stats.apns_rejections += 1
        if result.token_is_dead:
            await self._devices.delete_registration(device.id)
            self.stats.tokens_pruned += 1
            logger.info(
                "Deleted a dead %s token %s (%s) of owner %s (APNs: %s)",
                device.kind.value,
                token_fingerprint(device.token),
                device.environment or "registry default",
                device.owner_id,
                result.describe(),
            )
            return result
        logger.warning(
            "APNs refused a %s push for owner %s: %s",
            push.push_type.value,
            device.owner_id,
            result.describe(),
        )
        return result

    async def _prune(self, now: datetime) -> None:
        """Delete settled alerts past the retention, once per discovery interval."""
        interval = self._settings.discovery_interval_seconds
        if self._last_prune is not None and (now - self._last_prune).total_seconds() < interval:
            return
        self._last_prune = now
        await self._deliveries.prune_settled(
            now - timedelta(seconds=self._settings.retention_seconds)
        )

    # -- Operator tools ----------------------------------------------------

    async def send_test(
        self,
        owner_id: str,
        kind: PushTestKind,
        *,
        title: str,
        body: str,
        token: str | None = None,
    ) -> list[PushTestResult]:
        """Push once to the owner's registered tokens for ``kind``, ignoring throttles.

        ``token`` narrows it to that one registered token. The alert opens the app's
        activity list; the Live Activity pushes carry the owner's real current
        content; the background push and the widget reload are the real ones.
        """
        devices = [
            device
            for device in await self._ios_devices(owner_id, (kind.token_kind,))
            if token is None or device.token == token
        ]
        if not devices:
            return []
        now = self._clock()
        state: dict[str, Any] = {}
        if kind in (PushTestKind.LIVE_ACTIVITY, PushTestKind.LIVE_ACTIVITY_START):
            page = await self._newest_page()
            if page is None:
                raise GuildFeedError("No Guild node answers right now; try again in a moment")
            state = self._content_state(self._owner_items(owner_id, page), page, now)
        results: list[PushTestResult] = []
        for device in devices:
            result = await self._send(
                device, self._test_push(kind, device, state, now, title=title, body=body)
            )
            results.append(
                PushTestResult(
                    kind=kind.value,
                    environment=device.environment.value,
                    token_suffix=device.token[-_TOKEN_SUFFIX_CHARS:],
                    accepted=result.accepted,
                    detail=result.describe(),
                    apns_id=result.apns_id,
                )
            )
        return results

    def _test_push(
        self,
        kind: PushTestKind,
        device: DeviceToken,
        state: dict[str, Any],
        now: datetime,
        *,
        title: str,
        body: str,
    ) -> ApnsPush:
        alerts = self._settings.alerts
        match kind:
            case PushTestKind.LIVE_ACTIVITY:
                return self._live_activity_push(device, LiveActivityEvent.UPDATE, state, now)
            case PushTestKind.LIVE_ACTIVITY_START:
                return self._live_activity_push(
                    device,
                    LiveActivityEvent.START,
                    state,
                    now,
                    alert_title=clip(title, LIVE_ACTIVITY_TITLE_CHARS),
                    alert_body=body,
                )
            case PushTestKind.WIDGET:
                return self._widget_push(device)
            case PushTestKind.BACKGROUND:
                return self._background_push(device)
        return ApnsPush(
            token=device.token,
            environment=device.environment,
            topic=self._topic(device, ApnsPushType.ALERT),
            push_type=ApnsPushType.ALERT,
            priority=APNS_PRIORITY_IMMEDIATE,
            payload=alert_payload(
                title=title,
                body=shorten(body, alerts.body_chars),
                deep_link=alerts.deep_link_base,
                interruption_level=InterruptionLevel.ACTIVE,
                category=alerts.category,
                sound=alerts.sound,
            ),
            expiration=int(now.timestamp()) + alerts.expiration_seconds,
        )

    def note_environment_assumed(self, reason: EnvironmentAssumed) -> None:
        """Count a registration stored as production without a known environment."""
        if reason == "missing":
            self.stats.registrations_environment_missing += 1
        else:
            self.stats.registrations_environment_unknown += 1

    async def tokens_by_environment(self) -> dict[str, dict[str, int]]:
        """Registered tokens per APNs environment and kind: {"sandbox": {"alert": 1}}."""
        return await self._devices.count_by_environment()

    def status(self) -> dict[str, Any]:
        """What the gateway has done since it started (no tokens, no secrets)."""
        stats = asdict(self.stats)
        last_tick = self.stats.last_tick_at
        stats["last_tick_at"] = last_tick.isoformat() if last_tick is not None else None
        return {
            "running": self._task is not None and not self._task.done(),
            "nodes": dict(sorted((self._positions or {}).items())),
            "unreachable_nodes": dict(sorted(self._node_errors.items())),
            "feedless_nodes": dict(sorted(self._feedless.items())),
            "alerts_enabled": self._settings.alerts.enabled,
            "live_activity_enabled": self._settings.live_activity.enabled,
            "live_activity_start_enabled": self._settings.live_activity.start_enabled,
            "widgets_enabled": self._settings.widgets.enabled,
            "background_enabled": self._settings.background.enabled,
            **stats,
        }


_NO_TIME = datetime.min.replace(tzinfo=UTC)


def _recorded_order(notification: GuildNotification) -> tuple[datetime, str, int]:
    """Sort key across nodes: the time recorded, then the node, then its ``seq``."""
    return (notification.created_at or _NO_TIME, notification.instance_id, notification.seq)


def _severity(notification: GuildNotification) -> NotificationSeverity:
    """The notification's severity, or the lowest one when a node sent an unknown value."""
    try:
        return NotificationSeverity(notification.severity)
    except ValueError:
        return NotificationSeverity.INFO
