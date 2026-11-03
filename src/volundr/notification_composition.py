"""Composition of Forge notification delivery (sinks, the resolver and the dispatcher)
and of the image worker that copies attached images.

Kept out of ``main.py`` so the wiring can be built (and tested) from config alone.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from niuu.adapters.notifications.integrations import IntegrationSinkResolver
from niuu.domain.guild_membership import GuildInstance
from niuu.ports.credentials import CredentialStorePort
from niuu.ports.integrations import IntegrationRepository
from niuu.utils import import_class, resolve_secret_kwargs
from volundr.adapters.outbound.notification_image_fetcher import (
    HttpNotificationImageFetcher,
    SessionLookup,
)
from volundr.adapters.outbound.notification_sinks import (
    ConfiguredNotificationSinks,
    build_configured_sinks,
)
from volundr.adapters.outbound.pillow_notification_images import (
    PillowNotificationImageProcessor,
)
from volundr.adapters.outbound.postgres_notifications import (
    PostgresNotificationDeliveryRepository,
)
from volundr.config import NotificationImagesConfig, NotificationsConfig, Settings
from volundr.domain.notification_ports import (
    NotificationImageRepository,
    NotificationRepository,
)
from volundr.domain.ports import DeviceTokenRepository, EventBroadcaster, NotificationChannel
from volundr.domain.services.notification_dispatcher import (
    DispatcherSettings,
    NotificationDispatcher,
    NotificationRenderer,
)
from volundr.domain.services.notification_images import (
    ImageWorkerSettings,
    NotificationImageWorker,
)

logger = logging.getLogger(__name__)


def create_push_channel(settings: Settings) -> NotificationChannel:
    """Build the configured push channel (``push.adapter`` + kwargs)."""
    channel_cls = import_class(settings.push.adapter)
    kwargs = resolve_secret_kwargs(settings.push.kwargs, settings.push.secret_kwargs_env)
    channel = channel_cls(**kwargs)
    if not isinstance(channel, NotificationChannel):
        raise TypeError(f"{settings.push.adapter} is not a NotificationChannel")
    return channel


def dispatcher_settings(config: NotificationsConfig) -> DispatcherSettings:
    dispatcher = config.dispatcher
    return DispatcherSettings(
        poll_interval_seconds=dispatcher.poll_interval_seconds,
        batch_size=dispatcher.batch_size,
        lease_seconds=dispatcher.lease_seconds,
        send_timeout_seconds=dispatcher.send_timeout_seconds,
        max_concurrent_sends=dispatcher.max_concurrent_sends,
        max_attempts=dispatcher.max_attempts,
        backoff_base_seconds=dispatcher.backoff_base_seconds,
        backoff_max_seconds=dispatcher.backoff_max_seconds,
        backoff_jitter_ratio=dispatcher.backoff_jitter_ratio,
        default_rate_limit=dispatcher.default_rate_limit,
    )


def notification_renderer(config: NotificationsConfig, public_origin: str) -> NotificationRenderer:
    """Deep links use ``notifications.public_web_url``, else the host's public origin."""
    base = (config.public_web_url or public_origin).strip()
    host_label = config.host_label or (urlsplit(base).hostname or "")
    return NotificationRenderer(
        public_base_url=base,
        session_link_path=config.session_link_path,
        feed_link_path=config.feed_link_path,
        resident_link_path=config.resident_link_path,
        resident_session_link_path=config.resident_session_link_path,
        host_label=host_label,
    )


@dataclass
class NotificationDelivery:
    """The running delivery pipeline: the dispatcher and the sinks it owns."""

    dispatcher: NotificationDispatcher
    sinks: ConfiguredNotificationSinks

    async def start(self) -> None:
        await self.dispatcher.start()

    async def stop(self) -> None:
        try:
            await self.dispatcher.stop()
        finally:
            await self.sinks.close()


def create_notification_delivery(
    settings: Settings,
    pool: Any,
    *,
    public_origin: str,
    integration_repository: IntegrationRepository | None,
    credential_store: CredentialStorePort | None,
    device_repository: DeviceTokenRepository | None,
    push_channel: NotificationChannel | None,
) -> NotificationDelivery | None:
    """Build the outbox dispatcher, or ``None`` when delivery is switched off.

    Delivery needs ``notifications.enabled`` and ``notifications.dispatcher.enabled``.
    Integration-backed rules also need the integration repository and the
    credential store; without them such rules settle as dead with a clear reason.
    """
    config = settings.notifications
    if not config.enabled or not config.dispatcher.enabled:
        logger.info(
            "Notification delivery disabled (notifications.enabled=%s, dispatcher.enabled=%s)",
            config.enabled,
            config.dispatcher.enabled,
        )
        return None
    dependencies: dict[str, Any] = {}
    if device_repository is not None:
        dependencies["device_repository"] = device_repository
    if push_channel is not None:
        dependencies["push_channel"] = push_channel
    resolver = None
    if integration_repository is not None and credential_store is not None:
        resolver = IntegrationSinkResolver(
            integration_repository,
            credential_store,
            sink_adapters=config.integration_sinks,
        )
    sinks = ConfiguredNotificationSinks(
        build_configured_sinks(config.sinks, dependencies),
        integration_resolver=resolver,
    )
    dispatcher = NotificationDispatcher(
        PostgresNotificationDeliveryRepository(
            pool, max_error_chars=config.dispatcher.max_error_chars
        ),
        sinks,
        notification_renderer(config, public_origin),
        dispatcher_settings(config),
    )
    return NotificationDelivery(dispatcher=dispatcher, sinks=sinks)


def image_worker_settings(config: NotificationImagesConfig) -> ImageWorkerSettings:
    return ImageWorkerSettings(
        poll_interval_seconds=config.poll_interval_seconds,
        batch_size=config.batch_size,
        lease_seconds=config.lease_seconds,
        fetch_timeout_seconds=config.fetch_timeout_seconds,
        max_concurrent_fetches=config.max_concurrent_fetches,
    )


def create_notification_image_worker(
    settings: Settings,
    *,
    notifications: NotificationRepository,
    images: NotificationImageRepository,
    session_lookup: SessionLookup,
    broadcaster: EventBroadcaster | None,
    guild_instance: GuildInstance | None = None,
) -> NotificationImageWorker:
    """Build the worker that copies attached images (``notifications.images``).

    ``file_id`` images are read through the session's broker, routed like every
    other server-side broker call (``openshell_internal_gateway_url``).
    """
    config = settings.notifications.images
    return NotificationImageWorker(
        images,
        notifications,
        HttpNotificationImageFetcher(
            session_lookup=session_lookup,
            gateway_url=settings.openshell_internal_gateway_url,
            timeout_seconds=config.fetch_timeout_seconds,
            max_redirects=config.max_redirects,
            allow_private_networks=config.allow_private_networks,
        ),
        PillowNotificationImageProcessor(
            thumbnail_max_side=config.thumbnail_max_side,
            thumbnail_jpeg_quality=config.thumbnail_jpeg_quality,
        ),
        image_worker_settings(config),
        broadcaster=broadcaster,
        guild_instance=guild_instance,
    )
