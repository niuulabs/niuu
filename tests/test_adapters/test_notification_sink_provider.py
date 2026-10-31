"""Configured notification sinks: dynamic building from config and the sink provider."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from niuu.adapters.notifications.webhook import WebhookNotificationSink
from niuu.ports.notifications import (
    DeliveryResult,
    NotificationSink,
    NotificationSinkResolver,
    NotificationSinkUnavailableError,
    OutboundNotification,
)
from volundr.adapters.outbound.notification_sinks import (
    ConfiguredNotificationSinks,
    NotificationSinkBuildError,
    build_configured_sinks,
    build_sink,
)
from volundr.adapters.outbound.push_notification_sink import PushNotificationSink
from volundr.domain.notifications import NotificationRule


class ClosingSink(NotificationSink):
    def __init__(self, *, name: str = "x", fail_close: bool = False) -> None:
        self._name = name
        self.fail_close = fail_close
        self.closed = 0

    @property
    def name(self) -> str:
        return self._name

    async def send(self, message: OutboundNotification) -> DeliveryResult:
        return DeliveryResult()

    async def close(self) -> None:
        self.closed += 1
        if self.fail_close:
            raise RuntimeError("close failed")


class NotASink:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


class LeakySink(ClosingSink):
    def __init__(self, *, secret: str, name: str = "x") -> None:
        raise ValueError(f"bad secret {secret}")


class Resolver(NotificationSinkResolver):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def for_connection(self, owner_id: str, connection_id: str) -> NotificationSink:
        self.calls.append((owner_id, connection_id))
        return ClosingSink(name="integration:telegram")


def rule(**overrides) -> NotificationRule:
    now = datetime(2026, 9, 23, tzinfo=UTC)
    values = {
        "id": uuid4(),
        "owner_id": "user-1",
        "name": "r",
        "sink": "ops",
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return NotificationRule(**values)


class TestBuildSinks:
    def test_webhook_sink_from_config_with_secret_from_env(self, monkeypatch):
        monkeypatch.setenv("FORGE_OPS_WEBHOOK_SECRET", "from-env")
        sink = build_sink(
            {
                "name": "ops-webhook",
                "label": "Ops",
                "adapter": "niuu.adapters.notifications.webhook.WebhookNotificationSink",
                "url": "https://hooks.example.com/forge",
                "timeout": 3,
                "secret_kwargs_env": {"secret": "FORGE_OPS_WEBHOOK_SECRET"},
            },
            {"device_repository": object()},
        )
        assert isinstance(sink, WebhookNotificationSink)
        assert sink.name == "ops-webhook"
        assert sink._secret == "from-env"  # noqa: SLF001
        assert sink._client.timeout.read == 3  # noqa: SLF001

    def test_push_sink_gets_the_dependencies_it_names(self):
        devices, channel = object(), object()
        sink = build_sink(
            {
                "name": "push",
                "adapter": "volundr.adapters.outbound.push_notification_sink.PushNotificationSink",
                "max_body_chars": 100,
            },
            {"device_repository": devices, "push_channel": channel},
        )
        assert isinstance(sink, PushNotificationSink)
        assert sink._devices is devices and sink._channel is channel  # noqa: SLF001

    def test_non_sink_classes_are_rejected(self):
        with pytest.raises(TypeError, match="not a NotificationSink"):
            build_sink({"name": "x", "adapter": f"{__name__}.NotASink"}, {})

    def test_good_sinks_are_built(self):
        sinks = build_configured_sinks([{"name": "good", "adapter": f"{__name__}.ClosingSink"}], {})
        assert list(sinks) == ["good"]

    def test_unimportable_sink_raises(self):
        entries = [
            {"name": "good", "adapter": f"{__name__}.ClosingSink"},
            {"name": "missing", "adapter": "no.such.Sink"},
        ]
        with pytest.raises(NotificationSinkBuildError, match="'missing'.*notifications.sinks"):
            build_configured_sinks(entries, {})

    def test_sink_missing_its_dependencies_raises_naming_them(self):
        entries = [
            {
                "name": "push",
                "adapter": "volundr.adapters.outbound.push_notification_sink.PushNotificationSink",
            },
        ]
        with pytest.raises(NotificationSinkBuildError, match="'push'.*device_repository"):
            build_configured_sinks(entries, {})

    def test_constructor_errors_do_not_echo_their_message(self):
        entries = [{"name": "leaky", "adapter": f"{__name__}.LeakySink", "secret": "hunter2"}]
        with pytest.raises(NotificationSinkBuildError) as raised:
            build_configured_sinks(entries, {})
        assert "ValueError" in str(raised.value)
        assert "hunter2" not in str(raised.value)
        assert raised.value.__cause__ is None and raised.value.__suppress_context__


class TestConfiguredNotificationSinks:
    async def test_named_sinks_are_shared_and_closed_on_shutdown(self):
        ops = ClosingSink(name="ops")
        broken = ClosingSink(name="broken", fail_close=True)
        provider = ConfiguredNotificationSinks({"ops": ops, "broken": broken})
        assert provider.names == ["broken", "ops"]

        sink = await provider.open(rule())
        assert sink is ops
        await provider.release(sink)
        assert ops.closed == 0

        with pytest.raises(ExceptionGroup, match="Closing notification sinks failed"):
            await provider.close()
        assert ops.closed == 1 and broken.closed == 1

    async def test_unknown_named_sink_is_retryable(self):
        with pytest.raises(NotificationSinkUnavailableError, match="'ghost'") as info:
            await ConfiguredNotificationSinks({}).open(rule(sink="ghost"))
        assert info.value.retryable

    async def test_integration_rules_resolve_per_use_and_are_closed(self):
        resolver = Resolver()
        provider = ConfiguredNotificationSinks({}, integration_resolver=resolver)
        sink = await provider.open(rule(sink="integration", integration_connection_id="c-1"))
        assert resolver.calls == [("user-1", "c-1")]
        await provider.release(sink)
        assert sink.closed == 1

    async def test_integration_rules_without_a_resolver_are_permanent(self):
        with pytest.raises(NotificationSinkUnavailableError) as info:
            await ConfiguredNotificationSinks({}).open(
                rule(sink="integration", integration_connection_id="c-1")
            )
        assert not info.value.retryable
