"""Notification channel factory — resolves per-user channels from integrations."""

from __future__ import annotations

import logging

from niuu.domain.models import IntegrationType
from niuu.ports.credentials import CredentialStorePort
from niuu.ports.integrations import IntegrationRepository
from niuu.utils import import_class
from ting.ports.channel_resolver import ChannelResolverPort
from ting.ports.notification_channel import NotificationChannel

logger = logging.getLogger(__name__)


class NotificationChannelFactory(ChannelResolverPort):
    """Resolve notification channels for a user from their integration connections.

    Uses the dynamic adapter pattern: each connection specifies a fully-qualified
    class path (e.g. ``ting.adapters.telegram_notification.TelegramNotificationAdapter``)
    and its config + credentials are merged as kwargs.
    """

    def __init__(
        self,
        integration_repo: IntegrationRepository,
        credential_store: CredentialStorePort,
    ) -> None:
        self._integration_repo = integration_repo
        self._credential_store = credential_store

    async def for_owner(self, owner_id: str) -> list[NotificationChannel]:
        """Return all active notification channels for a given owner."""
        connections = await self._integration_repo.list_connections(
            owner_id, integration_type=IntegrationType.MESSAGING
        )

        channels: list[NotificationChannel] = []
        for conn in connections:
            if not conn.enabled:
                continue

            cred = await self._credential_store.get_value("user", owner_id, conn.credential_name)
            if cred is None:
                logger.debug(
                    "Skipping channel %s — credential %s not found",
                    conn.id,
                    conn.credential_name,
                )
                continue

            try:
                cls = import_class(conn.adapter)
                channel = cls(**{**cred, **conn.config})
                channels.append(channel)
            except Exception:
                logger.warning(
                    "Failed to instantiate channel %s (%s)",
                    conn.id,
                    conn.adapter,
                    exc_info=True,
                )

        return channels
