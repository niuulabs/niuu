"""The notification outbox dispatcher: claims due deliveries and settles each one.

``NotificationRepository.project`` schedules a ``pending`` delivery for every rule
that selects a new notification, in the same transaction that records it. This
background loop turns those rows into external messages:

1. **Claim** up to ``batch_size`` due rows with a lease (``FOR UPDATE SKIP
   LOCKED``), so any number of dispatchers on any number of hosts share the
   outbox without sending a row twice. A worker that dies mid-send leaves its
   rows ``claimed``; once the lease expires another worker re-claims them.
2. **Gate** each row at claim time: a disabled rule, quiet hours (in the rule's
   timezone, with ``allow_min_severity`` letting urgent items through) and the
   rule's rate limit settle it as ``suppressed``.
3. **Render** the sink-neutral :class:`~niuu.ports.notifications.OutboundNotification`
   with its deep link, and **send** it through the rule's sink, bounded by
   ``send_timeout_seconds``. A send only starts while at least that much of the
   lease remains, so a slow batch can never overlap a re-claim.
4. **Settle**, fenced by ``(delivery id, attempts)``: ``delivered``; ``failed``
   with exponential backoff and jitter (honouring a provider's ``retry_after``);
   ``dead`` for permanent errors or after ``max_attempts``.

Delivery is at-least-once: a send that times out, or a crash between a
successful send and its settle, is retried. Receivers can de-duplicate on the
delivery id.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from niuu.ports.notifications import (
    NotificationDeliveryError,
    NotificationSink,
    OutboundLink,
    OutboundNotification,
)
from volundr.domain.notification_ports import (
    NotificationDeliveryRepository,
    NotificationSinkProvider,
)
from volundr.domain.notifications import (
    ClaimedDelivery,
    NotificationDelivery,
    NotificationRateLimit,
    NotificationRule,
)

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class DispatcherSettings:
    """Timing and limits for :class:`NotificationDispatcher` (from ``NotificationsConfig``)."""

    poll_interval_seconds: float
    batch_size: int
    lease_seconds: float
    send_timeout_seconds: float
    max_concurrent_sends: int
    max_attempts: int
    backoff_base_seconds: float
    backoff_max_seconds: float
    backoff_jitter_ratio: float
    default_rate_limit: NotificationRateLimit | None = None

    def __post_init__(self) -> None:
        if self.batch_size < 1 or self.max_concurrent_sends < 1 or self.max_attempts < 1:
            raise ValueError("batch_size, max_concurrent_sends and max_attempts must be >= 1")
        if self.send_timeout_seconds >= self.lease_seconds:
            raise ValueError("send_timeout_seconds must be shorter than lease_seconds")
        if self.backoff_base_seconds > self.backoff_max_seconds:
            raise ValueError("backoff_base_seconds must not exceed backoff_max_seconds")
        if not 0.0 <= self.backoff_jitter_ratio <= 1.0:
            raise ValueError("backoff_jitter_ratio must be between 0 and 1")


@dataclass
class DispatcherStats:
    """Counters since start, for logs and diagnostics (``app.state``)."""

    ticks: int = 0
    claimed: int = 0
    delivered: int = 0
    retried: int = 0
    dead: int = 0
    suppressed: int = 0
    lease_skips: int = 0
    stale_settles: int = 0
    loop_errors: int = 0
    last_tick_at: datetime | None = None
    by_sink: dict[str, int] = field(default_factory=dict)


class NotificationRenderer:
    """Turns a claimed delivery into the sink-neutral outbound message.

    ``public_base_url`` is the browser-facing Forge origin. Without it messages
    carry no deep link. Relative notification links (``/volundr/...``) are made
    absolute against it.
    """

    def __init__(
        self,
        *,
        public_base_url: str,
        session_link_path: str,
        feed_link_path: str,
        resident_link_path: str,
        resident_session_link_path: str,
        host_label: str = "",
    ) -> None:
        self._base = public_base_url.strip().rstrip("/")
        self._session_link_path = session_link_path
        self._feed_link_path = feed_link_path
        self._resident_link_path = resident_link_path
        self._resident_session_link_path = resident_session_link_path
        self._host_label = host_label.strip()

    def deep_link(
        self,
        notification_id: str,
        session_id: str | None,
        *,
        resident_id: str | None = None,
        resident_session_id: str | None = None,
    ) -> str | None:
        """The session at the notification, the resident's channel, or else the feed."""
        if not self._base:
            return None
        if session_id:
            path = self._session_link_path.format(
                session_id=session_id, notification_id=notification_id
            )
            return self._base + path
        if resident_id and resident_session_id:
            path = self._resident_session_link_path.format(
                resident_id=resident_id,
                resident_session_id=resident_session_id,
                notification_id=notification_id,
            )
            return self._base + path
        if resident_id:
            path = self._resident_link_path.format(
                resident_id=resident_id, notification_id=notification_id
            )
            return self._base + path
        return self._base + self._feed_link_path

    def _absolute(self, url: str | None) -> str | None:
        if url and url.startswith("/") and not url.startswith("//") and self._base:
            return self._base + url
        return url

    def render(self, claimed: ClaimedDelivery) -> OutboundNotification:
        notification = claimed.notification
        notification_id = str(notification.id)
        session_id = str(notification.session_id) if notification.session_id else None
        return OutboundNotification(
            notification_id=notification_id,
            title=notification.title,
            kind=notification.kind,
            severity=notification.severity,
            source=notification.source,
            created_at=notification.created_at,
            body=notification.body,
            owner_id=notification.owner_id,
            session_id=session_id,
            session_label=notification.session_name,
            host_label=self._host_label or None,
            project_id=notification.project_id,
            url=self.deep_link(
                notification_id,
                session_id,
                resident_id=notification.resident_id,
                resident_session_id=notification.resident_session_id,
            ),
            resident_id=notification.resident_id,
            resident_session_id=notification.resident_session_id,
            links=tuple(
                OutboundLink(label=link.label, url=self._absolute(link.url), kind=link.kind.value)
                for link in notification.links
            ),
            engine=notification.engine,
            model=notification.model,
            correlation_id=notification.correlation_id,
            delivery_id=str(claimed.delivery.id),
            attempt=claimed.delivery.attempts,
            metadata={
                **notification.metadata,
                "session_seq": notification.session_seq,
                "rule_id": str(claimed.rule.id),
                "rule_name": claimed.rule.name,
            },
        )


class _SinkCache:
    """Resolves each distinct sink once per poll and releases them afterwards."""

    def __init__(self, provider: NotificationSinkProvider) -> None:
        self._provider = provider
        self._entries: dict[tuple[str, str, str], asyncio.Task[NotificationSink]] = {}

    @staticmethod
    def _key(rule: NotificationRule) -> tuple[str, str, str]:
        if rule.integration_connection_id:
            return ("integration", rule.owner_id, rule.integration_connection_id)
        return ("named", "", rule.sink)

    async def get(self, rule: NotificationRule) -> NotificationSink:
        key = self._key(rule)
        task = self._entries.get(key)
        if task is None:
            task = asyncio.ensure_future(self._provider.open(rule))
            self._entries[key] = task
        return await asyncio.shield(task)

    async def release_all(self) -> None:
        entries = list(self._entries.values())
        self._entries.clear()
        for task in entries:
            if not task.done():
                task.cancel()
                continue
            if task.cancelled() or task.exception() is not None:
                continue
            try:
                await self._provider.release(task.result())
            except Exception:
                logger.warning("Failed to release a notification sink", exc_info=True)


class _RateWindows:
    """Per-rule rate limit for one poll.

    Each rule's recent deliveries are counted once, when the poll first needs
    them; sends this poll then add reservations. Counting the database again
    after a send settled would count that send twice. A failed send gives its
    reservation back.
    """

    def __init__(self, deliveries: NotificationDeliveryRepository) -> None:
        self._deliveries = deliveries
        self._recent: dict[object, int] = {}
        self._reserved: dict[object, int] = {}
        self._locks: dict[object, asyncio.Lock] = {}

    async def reserve(
        self, rule: NotificationRule, limit: NotificationRateLimit, now: datetime
    ) -> bool:
        lock = self._locks.setdefault(rule.id, asyncio.Lock())
        async with lock:
            if rule.id not in self._recent:
                since = now - timedelta(seconds=limit.window_seconds)
                self._recent[rule.id] = await self._deliveries.count_delivered_since(rule.id, since)
            reserved = self._reserved.get(rule.id, 0)
            if self._recent[rule.id] + reserved >= limit.max_count:
                return False
            self._reserved[rule.id] = reserved + 1
            return True

    def release(self, rule: NotificationRule) -> None:
        self._reserved[rule.id] = max(self._reserved.get(rule.id, 0) - 1, 0)


class NotificationDispatcher:
    """Background loop delivering the notification outbox (see the module docstring)."""

    def __init__(
        self,
        deliveries: NotificationDeliveryRepository,
        sinks: NotificationSinkProvider,
        renderer: NotificationRenderer,
        settings: DispatcherSettings,
        *,
        clock: Callable[[], datetime] = _utc_now,
        monotonic: Callable[[], float] = time.monotonic,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._deliveries = deliveries
        self._sinks = sinks
        self._renderer = renderer
        self._settings = settings
        self._clock = clock
        self._monotonic = monotonic
        self._jitter = jitter
        self._stopping = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.stats = DispatcherStats()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._stopping = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="notification-dispatcher")
        logger.info(
            "Notification dispatcher started (batch=%d, lease=%.0fs, max_attempts=%d)",
            self._settings.batch_size,
            self._settings.lease_seconds,
            self._settings.max_attempts,
        )

    async def stop(self) -> None:
        """Stop polling; in-flight sends get ``send_timeout_seconds`` to finish.

        Rows still in flight after that stay ``claimed`` and are re-claimed by a
        dispatcher once their lease expires.
        """
        task = self._task
        if task is None:
            return
        self._stopping.set()
        done, _ = await asyncio.wait({task}, timeout=self._settings.send_timeout_seconds)
        if not done:
            task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        self._task = None
        logger.info(
            "Notification dispatcher stopped: delivered=%d retried=%d dead=%d suppressed=%d",
            self.stats.delivered,
            self.stats.retried,
            self.stats.dead,
            self.stats.suppressed,
        )

    async def _loop(self) -> None:
        while not self._stopping.is_set():
            try:
                claimed = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.stats.loop_errors += 1
                logger.exception("Notification dispatcher poll failed")
                claimed = 0
            if claimed >= self._settings.batch_size:
                continue  # A full batch: more rows are probably due right now.
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stopping.wait(), timeout=self._settings.poll_interval_seconds
                )

    async def run_once(self) -> int:
        """Claim one batch and settle it; returns how many rows were claimed."""
        started = self._monotonic()
        batch = await self._deliveries.claim_due(
            limit=self._settings.batch_size, lease_seconds=self._settings.lease_seconds
        )
        self.stats.ticks += 1
        self.stats.last_tick_at = self._clock()
        if not batch:
            return 0
        self.stats.claimed += len(batch)
        # Measured from before the claim, so it never overestimates the DB lease.
        deadline = started + self._settings.lease_seconds
        sinks = _SinkCache(self._sinks)
        windows = _RateWindows(self._deliveries)
        slots = asyncio.Semaphore(self._settings.max_concurrent_sends)
        try:
            await asyncio.gather(
                *(self._process(item, deadline, sinks, windows, slots) for item in batch)
            )
        finally:
            await sinks.release_all()
        return len(batch)

    async def _process(
        self,
        claimed: ClaimedDelivery,
        deadline: float,
        sinks: _SinkCache,
        windows: _RateWindows,
        slots: asyncio.Semaphore,
    ) -> None:
        try:
            await self._deliver(claimed, deadline, sinks, windows, slots)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A bug in one row must not stall the batch; the row stays claimed and
            # is retried after its lease.
            self.stats.loop_errors += 1
            logger.exception("Notification delivery %s could not be processed", claimed.delivery.id)

    async def _deliver(
        self,
        claimed: ClaimedDelivery,
        deadline: float,
        sinks: _SinkCache,
        windows: _RateWindows,
        slots: asyncio.Semaphore,
    ) -> None:
        delivery, notification, rule = claimed.delivery, claimed.notification, claimed.rule
        if delivery.attempts > self._settings.max_attempts:
            await self._settle_dead(
                delivery,
                f"Gave up after {delivery.attempts - 1} attempts (the last claim's lease expired)",
            )
            return
        if not rule.enabled:
            await self._suppress(delivery, "The rule is disabled")
            return
        now = self._clock()
        if rule.quiet_hours is not None and rule.quiet_hours.holds_back(notification.severity, now):
            quiet = rule.quiet_hours
            await self._suppress(
                delivery,
                f"Quiet hours {quiet.start}-{quiet.end} {quiet.timezone} "
                f"(only {quiet.allow_min_severity.value} and above are delivered)",
            )
            return
        limit = rule.rate_limit or self._settings.default_rate_limit
        if limit is not None and not await windows.reserve(rule, limit, now):
            await self._suppress(
                delivery,
                f"Rate limit: at most {limit.max_count} per {limit.window_seconds}s for this rule",
            )
            return
        reserved = limit is not None
        sent = False
        try:
            sent = await self._send(claimed, deadline, sinks, slots)
        finally:
            if reserved and not sent:
                windows.release(rule)

    async def _send(
        self,
        claimed: ClaimedDelivery,
        deadline: float,
        sinks: _SinkCache,
        slots: asyncio.Semaphore,
    ) -> bool:
        delivery, rule = claimed.delivery, claimed.rule
        try:
            sink = await sinks.get(rule)
        except NotificationDeliveryError as exc:
            await self._settle_failure(delivery, exc)
            return False
        except Exception as exc:
            # Resolution hit infrastructure (the credential store, the database):
            # retry later rather than give up on the owner's rule.
            logger.warning("Resolving the sink for rule %s failed: %s", rule.id, type(exc).__name__)
            await self._settle_failure(
                delivery,
                NotificationDeliveryError(
                    f"Resolving the sink failed: {type(exc).__name__}", retryable=True
                ),
            )
            return False
        message = self._renderer.render(claimed)
        reason = sink.skip_reason(message)
        if reason:
            await self._suppress(delivery, reason)
            return False
        async with slots:
            if self._monotonic() + self._settings.send_timeout_seconds > deadline:
                # Not enough lease left to finish safely. Leave the row claimed; a
                # dispatcher re-claims it when the lease expires.
                self.stats.lease_skips += 1
                logger.warning(
                    "Notification delivery %s not sent: too little of its lease remains",
                    delivery.id,
                )
                return False
            try:
                result = await asyncio.wait_for(
                    sink.send(message), timeout=self._settings.send_timeout_seconds
                )
            except NotificationDeliveryError as exc:
                await self._settle_failure(delivery, exc)
                return False
            except TimeoutError:
                await self._settle_failure(
                    delivery,
                    NotificationDeliveryError(
                        f"{sink.name} did not answer within "
                        f"{self._settings.send_timeout_seconds:g}s",
                        retryable=True,
                    ),
                )
                return False
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # An unexpected sink bug. Its message may carry request details, so
                # only the type is kept; the traceback goes to the log.
                logger.warning(
                    "Sink %s raised %s for delivery %s",
                    sink.name,
                    type(exc).__name__,
                    delivery.id,
                )
                await self._settle_failure(
                    delivery,
                    NotificationDeliveryError(
                        f"{sink.name} failed: {type(exc).__name__}", retryable=True
                    ),
                )
                return False
        if await self._deliveries.mark_delivered(delivery):
            self.stats.delivered += 1
            self.stats.by_sink[sink.name] = self.stats.by_sink.get(sink.name, 0) + 1
            logger.info(
                "Delivered notification %s via %s (delivery %s, attempt %d%s)",
                claimed.notification.id,
                sink.name,
                delivery.id,
                delivery.attempts,
                f", provider id {result.provider_message_id}" if result.provider_message_id else "",
            )
            return True
        self._stale(delivery)
        return True

    def backoff_seconds(self, attempts: int, retry_after: float | None = None) -> float:
        """Exponential backoff with jitter for the retry after attempt ``attempts``."""
        settings = self._settings
        exponent = max(attempts - 1, 0)
        delay = min(settings.backoff_max_seconds, settings.backoff_base_seconds * 2**exponent)
        spread = settings.backoff_jitter_ratio * (2 * self._jitter() - 1)
        delay = min(settings.backoff_max_seconds, max(delay * (1 + spread), 0.0))
        if retry_after is not None:
            delay = max(delay, retry_after)
        return delay

    async def _settle_failure(
        self, delivery: NotificationDelivery, error: NotificationDeliveryError
    ) -> None:
        if not error.retryable:
            await self._settle_dead(delivery, str(error))
            return
        if delivery.attempts >= self._settings.max_attempts:
            await self._settle_dead(
                delivery, f"{error} (gave up after {delivery.attempts} attempts)"
            )
            return
        delay = self.backoff_seconds(delivery.attempts, error.retry_after)
        retry_at = self._clock() + timedelta(seconds=delay)
        if not await self._deliveries.mark_failed(delivery, error=str(error), retry_at=retry_at):
            self._stale(delivery)
            return
        self.stats.retried += 1
        logger.warning(
            "Notification delivery %s failed (attempt %d), retrying in %.3gs: %s",
            delivery.id,
            delivery.attempts,
            delay,
            error,
        )

    async def _settle_dead(self, delivery: NotificationDelivery, reason: str) -> None:
        if not await self._deliveries.mark_failed(delivery, error=reason, retry_at=None):
            self._stale(delivery)
            return
        self.stats.dead += 1
        logger.warning("Notification delivery %s is dead: %s", delivery.id, reason)

    async def _suppress(self, delivery: NotificationDelivery, reason: str) -> None:
        if not await self._deliveries.mark_suppressed(delivery, reason=reason):
            self._stale(delivery)
            return
        self.stats.suppressed += 1
        logger.info("Notification delivery %s suppressed: %s", delivery.id, reason)

    def _stale(self, delivery: NotificationDelivery) -> None:
        self.stats.stale_settles += 1
        logger.warning(
            "Notification delivery %s was re-claimed by another dispatcher; "
            "this attempt's result is discarded",
            delivery.id,
        )
