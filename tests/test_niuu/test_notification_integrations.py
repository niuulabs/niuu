"""Messaging integrations as notification sinks and channels, and the Ting move."""

from __future__ import annotations

import importlib
from datetime import UTC, datetime

import pytest

from niuu.adapters.notifications.integrations import (
    DEFAULT_INTEGRATION_SINKS,
    IntegrationSinkResolver,
    NotificationChannelFactory,
    integration_slug,
)
from niuu.adapters.notifications.telegram import (
    TelegramNotificationAdapter,
    TelegramNotificationSink,
)
from niuu.adapters.notifications.webhook import (
    WebhookNotificationAdapter,
    WebhookNotificationSink,
)
from niuu.domain.models import IntegrationConnection, IntegrationType
from niuu.ports.channel_resolver import NotificationChannelUnavailableError
from niuu.ports.notifications import (
    DeliveryResult,
    NotificationSink,
    NotificationSinkUnavailableError,
    OutboundNotification,
)
from niuu.utils import import_class
from tests.test_ting.conftest import StubCredentialStore, StubIntegrationRepo

NOW = datetime(2026, 9, 23, tzinfo=UTC)
OLD_TELEGRAM = "ting.adapters.telegram_notification.TelegramNotificationAdapter"
OLD_WEBHOOK = "ting.adapters.webhook_notification.WebhookNotificationAdapter"


class CustomSink(NotificationSink):
    """A third-party sink stored directly as a connection's adapter."""

    def __init__(self, *, name: str, token: str, region: str = "eu") -> None:
        self._name = name
        self.token = token
        self.region = region

    @property
    def name(self) -> str:
        return self._name

    async def send(self, message: OutboundNotification) -> DeliveryResult:
        return DeliveryResult()


def connection(**overrides) -> IntegrationConnection:
    values = {
        "id": "conn-1",
        "owner_id": "user-1",
        "integration_type": IntegrationType.MESSAGING,
        "adapter": "",
        "credential_name": "tg",
        "config": {},
        "enabled": True,
        "created_at": NOW,
        "updated_at": NOW,
        "slug": "telegram",
    }
    values.update(overrides)
    return IntegrationConnection(**values)


def resolver(*connections, credentials=None, **kwargs) -> IntegrationSinkResolver:
    store = StubCredentialStore(
        values=credentials
        if credentials is not None
        else {"user:user-1:tg": {"bot_token": "tok", "chat_id": "42"}}
    )
    return IntegrationSinkResolver(StubIntegrationRepo(list(connections)), store, **kwargs)


class TestIntegrationSinkResolver:
    async def test_catalog_telegram_connection_with_empty_adapter_resolves(self):
        sink = await resolver(connection()).for_connection("user-1", "conn-1")
        assert isinstance(sink, TelegramNotificationSink)
        assert sink.name == "integration:telegram"
        assert sink._bot_token == "tok" and sink._chat_id == "42"  # noqa: SLF001
        await sink.close()

    async def test_legacy_ting_path_without_slug_resolves_to_the_sink(self):
        conn = connection(adapter=OLD_TELEGRAM, slug="", config={"message_thread_id": 9})
        sink = await resolver(conn).for_connection("user-1", "conn-1")
        assert isinstance(sink, TelegramNotificationSink)
        assert sink._message_thread_id == 9  # noqa: SLF001
        await sink.close()

    async def test_webhook_connection_merges_credential_and_config(self):
        conn = connection(
            adapter=OLD_WEBHOOK,
            slug="webhook",
            credential_name="wh",
            config={"url": "https://hooks.example.com/x", "min_urgency": "low"},
        )
        sink = await resolver(conn, credentials={"user:user-1:wh": {"secret": "s"}}).for_connection(
            "user-1", "conn-1"
        )
        assert isinstance(sink, WebhookNotificationSink)
        assert sink._secret == "s"  # noqa: SLF001
        await sink.close()

    async def test_config_overrides_credential_and_defaults_apply(self):
        conn = connection(config={"chat_id": "99"})
        sink = await resolver(conn, sink_kwargs={"timeout": 4.0}).for_connection("user-1", "conn-1")
        assert sink._chat_id == "99"  # noqa: SLF001
        assert sink._client.timeout.read == 4.0  # noqa: SLF001
        await sink.close()

    async def test_unmapped_slug_may_store_a_sink_class(self):
        conn = connection(slug="pager", adapter=f"{__name__}.CustomSink", credential_name="pg")
        sink = await resolver(
            conn, credentials={"user:user-1:pg": {"token": "abc"}}
        ).for_connection("user-1", "conn-1")
        assert isinstance(sink, CustomSink) and sink.token == "abc"
        assert sink.name == "integration:pager"

    async def test_operator_mapping_overrides_the_default(self):
        conn = connection(credential_name="pg")
        sink = await resolver(
            conn,
            credentials={"user:user-1:pg": {"token": "t"}},
            sink_adapters={"telegram": f"{__name__}.CustomSink"},
        ).for_connection("user-1", "conn-1")
        assert isinstance(sink, CustomSink)

    @pytest.mark.parametrize(
        ("conn", "owner", "match"),
        [
            (connection(), "someone-else", "was not found"),
            (connection(id="other"), "user-1", "was not found"),
            (connection(enabled=False), "user-1", "is disabled"),
            (
                connection(integration_type=IntegrationType.ISSUE_TRACKER),
                "user-1",
                "not a messaging integration",
            ),
            (connection(slug="slack", adapter=""), "user-1", "No notification sink"),
            (connection(slug="slack", adapter="no.such.Module"), "user-1", "cannot be imported"),
            (connection(slug="slack", adapter=OLD_TELEGRAM), "user-1", "not a NotificationSink"),
            (connection(credential_name="missing"), "user-1", "Credential 'missing'"),
        ],
    )
    async def test_unusable_connections_are_permanent(self, conn, owner, match):
        with pytest.raises(NotificationSinkUnavailableError, match=match) as info:
            await resolver(conn).for_connection(owner, "conn-1")
        assert not info.value.retryable

    async def test_constructor_rejection_names_no_values(self):
        conn = connection(slug="pager", adapter=f"{__name__}.CustomSink", credential_name="pg")
        with pytest.raises(NotificationSinkUnavailableError, match="TypeError") as info:
            await resolver(
                conn, credentials={"user:user-1:pg": {"wrong": "super-secret"}}
            ).for_connection("user-1", "conn-1")
        assert "super-secret" not in str(info.value)

    def test_default_mapping_and_slug_inference(self):
        assert set(DEFAULT_INTEGRATION_SINKS) == {"telegram", "webhook"}
        assert integration_slug(connection(slug="", adapter=OLD_WEBHOOK)) == "webhook"
        assert integration_slug(connection(slug="", adapter="x.Y")) == ""


class TestNotificationChannelFactory:
    async def test_empty_adapter_uses_the_default_channel_for_its_slug(self):
        factory = NotificationChannelFactory(
            StubIntegrationRepo([connection()]),
            StubCredentialStore(values={"user:user-1:tg": {"bot_token": "t", "chat_id": "1"}}),
        )
        channels = await factory.for_owner("user-1")
        assert len(channels) == 1 and isinstance(channels[0], TelegramNotificationAdapter)
        await channels[0].close()

    async def test_unknown_slug_without_adapter_raises(self):
        factory = NotificationChannelFactory(
            StubIntegrationRepo([connection(slug="slack")]),
            StubCredentialStore(values={"user:user-1:tg": {"bot_token": "t", "chat_id": "1"}}),
        )
        with pytest.raises(NotificationChannelUnavailableError, match="'slack'.*no channel"):
            await factory.for_owner("user-1")


class TestTingCompatibility:
    """Stored class paths and Ting imports keep resolving after the move to niuu."""

    def test_old_class_paths_resolve_to_the_shared_classes(self):
        assert import_class(OLD_TELEGRAM) is TelegramNotificationAdapter
        assert import_class(OLD_WEBHOOK) is WebhookNotificationAdapter
        factory = import_class(
            "ting.adapters.notification_channel_factory.NotificationChannelFactory"
        )
        assert factory is NotificationChannelFactory

    def test_old_port_modules_re_export(self):
        old_channel = importlib.import_module("ting.ports.notification_channel")
        new_channel = importlib.import_module("niuu.ports.notification_channel")
        for name in ("Notification", "NotificationChannel", "NotificationUrgency"):
            assert getattr(old_channel, name) is getattr(new_channel, name)
        old_resolver = importlib.import_module("ting.ports.channel_resolver")
        new_resolver = importlib.import_module("niuu.ports.channel_resolver")
        assert old_resolver.ChannelResolverPort is new_resolver.ChannelResolverPort

    def test_niuu_does_not_import_ting_or_volundr(self):
        import pathlib

        root = pathlib.Path(importlib.import_module("niuu").__file__).parent
        for path in [
            root / "ports/notifications.py",
            root / "ports/notification_channel.py",
            root / "ports/channel_resolver.py",
            *sorted((root / "adapters/notifications").glob("*.py")),
        ]:
            source = path.read_text()
            assert "from ting" not in source and "import ting" not in source, path
            assert "from volundr" not in source and "import volundr" not in source, path
