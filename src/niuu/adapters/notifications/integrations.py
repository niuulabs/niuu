"""Resolve notification channels and sinks from an owner's messaging integrations.

A MESSAGING ``integration_connection`` names an adapter class path, a credential
in the credential store, and adapter config. Two consumers read it:

* :class:`NotificationChannelFactory` (Ting's event notifications) builds every
  enabled connection of an owner as a
  :class:`~niuu.ports.notification_channel.NotificationChannel`, and raises when
  one cannot be built. It moved here
  from ``ting.adapters.notification_channel_factory``, which re-exports it.
* :class:`IntegrationSinkResolver` (the Forge delivery outbox) builds ONE
  connection, named by a delivery rule, as a strict
  :class:`~niuu.ports.notifications.NotificationSink`.

Both use the dynamic adapter pattern, ``import_class(path)(**credential, **config)``.
The stored ``adapter`` is often not what a consumer needs: connections created
from the integration catalog may store an empty adapter, and older rows store
Ting channel paths. Two explicit tables close that gap, keyed by integration
slug; neither consumer branches on adapter types.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from niuu.domain.models import IntegrationConnection, IntegrationType, is_push_device
from niuu.ports.channel_resolver import ChannelResolverPort, NotificationChannelUnavailableError
from niuu.ports.credentials import CredentialStorePort
from niuu.ports.integrations import IntegrationRepository
from niuu.ports.notification_channel import NotificationChannel
from niuu.ports.notifications import (
    NotificationSink,
    NotificationSinkResolver,
    NotificationSinkUnavailableError,
)
from niuu.utils import import_class

logger = logging.getLogger(__name__)

#: Credentials of integration connections are stored per user.
CREDENTIAL_OWNER_TYPE = "user"

#: Integration slug → the strict sink that delivers Forge notifications for it.
DEFAULT_INTEGRATION_SINKS: dict[str, str] = {
    "telegram": "niuu.adapters.notifications.telegram.TelegramNotificationSink",
    "webhook": "niuu.adapters.notifications.webhook.WebhookNotificationSink",
}

#: Integration slug → the best-effort channel Ting uses when a connection stores
#: no adapter (catalog-created connections copy the definition's adapter, which
#: older catalogs left empty).
DEFAULT_CHANNEL_ADAPTERS: dict[str, str] = {
    "telegram": "niuu.adapters.notifications.telegram.TelegramNotificationAdapter",
    "webhook": "niuu.adapters.notifications.webhook.WebhookNotificationAdapter",
}

#: Channel class paths stored on connections without a slug (seeded or older
#: rows) → the integration slug they belong to. Includes the pre-move Ting paths.
KNOWN_CHANNEL_SLUGS: dict[str, str] = {
    "ting.adapters.telegram_notification.TelegramNotificationAdapter": "telegram",
    "niuu.adapters.notifications.telegram.TelegramNotificationAdapter": "telegram",
    "ting.adapters.webhook_notification.WebhookNotificationAdapter": "webhook",
    "niuu.adapters.notifications.webhook.WebhookNotificationAdapter": "webhook",
}


def integration_slug(connection: IntegrationConnection) -> str:
    """The connection's catalog slug, inferred from a known channel path if unset."""
    return connection.slug or KNOWN_CHANNEL_SLUGS.get(connection.adapter, "")


class NotificationChannelFactory(ChannelResolverPort):
    """Resolve notification channels for a user from their integration connections.

    Each enabled MESSAGING connection is built from its stored class path (or,
    when that is empty, the default channel for its slug) with its credential
    and config merged as kwargs. An enabled connection that cannot be built (no
    credential, no adapter, or a constructor that refuses its kwargs) raises
    :class:`~niuu.ports.channel_resolver.NotificationChannelUnavailableError`: the
    owner asked for that channel, so it is fixed or disabled, never left out.
    """

    def __init__(
        self,
        integration_repo: IntegrationRepository,
        credential_store: CredentialStorePort,
        *,
        channel_adapters: Mapping[str, str] | None = None,
    ) -> None:
        self._integration_repo = integration_repo
        self._credential_store = credential_store
        self._channel_adapters = dict(
            DEFAULT_CHANNEL_ADAPTERS if channel_adapters is None else channel_adapters
        )

    async def for_owner(self, owner_id: str) -> list[NotificationChannel]:
        """Return all active notification channels for a given owner."""
        connections = await self._integration_repo.list_connections(
            owner_id, integration_type=IntegrationType.MESSAGING
        )

        channels: list[NotificationChannel] = []
        for conn in connections:
            # Push devices are delivered to by the push sink, not built as channels.
            if not conn.enabled or is_push_device(conn):
                continue

            cred = await self._credential_store.get_value(
                CREDENTIAL_OWNER_TYPE, owner_id, conn.credential_name
            )
            if cred is None:
                raise NotificationChannelUnavailableError(
                    f"Messaging connection {conn.id} has no credential "
                    f"{conn.credential_name!r}; store it or disable the connection"
                )
            adapter = conn.adapter or self._channel_adapters.get(conn.slug, "")
            if not adapter:
                raise NotificationChannelUnavailableError(
                    f"Messaging connection {conn.id} (slug {conn.slug!r}) names no channel "
                    "adapter and none is known for its slug; set its adapter or disable it"
                )
            channels.append(self._build(conn, adapter, cred))

        return channels

    @staticmethod
    def _build(
        conn: IntegrationConnection, adapter: str, cred: Mapping[str, Any]
    ) -> NotificationChannel:
        try:
            cls = import_class(adapter)
            return cls(**{**cred, **conn.config})
        except Exception as exc:
            # Only the type: the kwargs are the owner's credential.
            raise NotificationChannelUnavailableError(
                f"Messaging connection {conn.id} ({adapter}) could not be built: "
                f"{type(exc).__name__}; fix or disable the connection"
            ) from None


class IntegrationSinkResolver(NotificationSinkResolver):
    """Build the Forge notification sink behind one of an owner's integrations.

    The sink class is chosen by the connection's slug (``sink_adapters``, from
    ``notifications.integration_sinks``). A connection whose slug has no entry
    may store a :class:`NotificationSink` class path directly as its adapter.
    The credential and the connection config become the sink's kwargs, plus
    ``name`` and any ``sink_kwargs`` defaults (such as a timeout).
    """

    def __init__(
        self,
        integration_repo: IntegrationRepository,
        credential_store: CredentialStorePort,
        *,
        sink_adapters: Mapping[str, str] | None = None,
        sink_kwargs: Mapping[str, Any] | None = None,
    ) -> None:
        self._integration_repo = integration_repo
        self._credential_store = credential_store
        self._sink_adapters = dict(
            DEFAULT_INTEGRATION_SINKS if sink_adapters is None else sink_adapters
        )
        self._sink_kwargs = dict(sink_kwargs or {})

    def sink_class_path(self, connection: IntegrationConnection) -> str:
        """The sink class for ``connection``; empty when none can be determined."""
        return self._sink_adapters.get(integration_slug(connection)) or connection.adapter

    async def for_connection(self, owner_id: str, connection_id: str) -> NotificationSink:
        connection = await self._integration_repo.get_connection(connection_id)
        if connection is None or connection.owner_id != owner_id:
            raise _unavailable(f"Integration connection {connection_id} was not found")
        if connection.integration_type != IntegrationType.MESSAGING:
            raise _unavailable(
                f"Integration connection {connection_id} is not a messaging integration"
            )
        if not connection.enabled:
            raise _unavailable(f"Integration connection {connection_id} is disabled")

        path = self.sink_class_path(connection)
        if not path:
            raise _unavailable(
                f"No notification sink is configured for integration "
                f"{connection.slug or connection_id!r}; map its slug under "
                "notifications.integration_sinks"
            )
        sink_cls = _sink_class(path)

        credential = await self._credential_store.get_value(
            CREDENTIAL_OWNER_TYPE, owner_id, connection.credential_name
        )
        if credential is None:
            raise _unavailable(
                f"Credential {connection.credential_name!r} for integration connection "
                f"{connection_id} was not found"
            )
        slug = integration_slug(connection) or "custom"
        kwargs = {
            **self._sink_kwargs,
            **credential,
            **connection.config,
            "name": f"integration:{slug}",
        }
        try:
            return sink_cls(**kwargs)
        except (TypeError, ValueError) as exc:
            # The message names the rejected argument, never its value.
            raise _unavailable(
                f"Integration connection {connection_id} cannot build {path}: {type(exc).__name__}"
            ) from None


def _sink_class(path: str) -> type[NotificationSink]:
    try:
        cls = import_class(path)
    except (ImportError, AttributeError, ValueError):
        raise _unavailable(f"Notification sink class {path!r} cannot be imported") from None
    if not isinstance(cls, type) or not issubclass(cls, NotificationSink):
        raise _unavailable(f"{path!r} is not a NotificationSink")
    return cls


def _unavailable(message: str) -> NotificationSinkUnavailableError:
    return NotificationSinkUnavailableError(message, retryable=False)
