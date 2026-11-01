"""Push devices stored as integrations, their tokens in the credential store."""

from __future__ import annotations

from dataclasses import replace

import pytest

from niuu.adapters.memory_credential_store import MemoryCredentialStore
from niuu.adapters.notifications.integrations import NotificationChannelFactory
from niuu.domain.models import PUSH_DEVICE_SLUG, IntegrationType
from volundr.adapters.outbound.integration_device_tokens import IntegrationDeviceTokenRepository
from volundr.adapters.outbound.memory_integrations import InMemoryIntegrationRepository
from volundr.config import PushNotificationConfig
from volundr.domain.models import DevicePlatform, DeviceToken


def _device(token: str = "apns-token-1", owner: str = "user-1", **extra) -> DeviceToken:
    return DeviceToken(
        owner_id=owner,
        platform=DevicePlatform.IOS,
        token=token,
        app_bundle_id="com.niuu.lexi.chat",
        **extra,
    )


@pytest.fixture
def stores():
    integrations = InMemoryIntegrationRepository()
    credentials = MemoryCredentialStore()
    return integrations, credentials, IntegrationDeviceTokenRepository(integrations, credentials)


class TestIntegrationDeviceTokenRepository:
    async def test_register_creates_a_messaging_connection_and_a_credential(self, stores):
        integrations, credentials, repo = stores

        stored = await repo.upsert(_device(environment="sandbox"))

        [connection] = await integrations.list_connections("user-1")
        assert connection.integration_type == IntegrationType.MESSAGING
        assert connection.slug == PUSH_DEVICE_SLUG
        assert connection.enabled
        assert connection.config == {
            "platform": "ios",
            "app_bundle_id": "com.niuu.lexi.chat",
            "environment": "sandbox",
        }
        # The token is a secret: only the credential store holds it.
        assert "apns-token-1" not in repr(connection)
        secret = await credentials.get_value("user", "user-1", connection.credential_name)
        assert secret == {"device_token": "apns-token-1"}
        assert stored.token == "apns-token-1"
        assert stored.environment == "sandbox"
        assert str(stored.id) == connection.id

    async def test_registering_again_updates_one_connection_and_keeps_it_disabled(self, stores):
        integrations, _, repo = stores
        first = await repo.upsert(_device())
        [connection] = await integrations.list_connections("user-1")
        await integrations.save_connection(replace(connection, enabled=False))

        again = await repo.upsert(_device(environment="production"))

        [connection] = await integrations.list_connections("user-1")
        assert again.id == first.id
        assert not connection.enabled
        assert connection.config["environment"] == "production"
        assert await repo.list_for_owner("user-1") == []

    async def test_list_returns_only_the_owners_push_devices_with_tokens(self, stores):
        integrations, credentials, repo = stores
        await repo.upsert(_device("a"))
        await repo.upsert(_device("b"))
        await repo.upsert(_device("other", owner="user-2"))
        lost = await repo.upsert(_device("c"))
        [lost_connection] = [
            c for c in await integrations.list_connections("user-1") if c.id == str(lost.id)
        ]
        await credentials.delete("user", "user-1", lost_connection.credential_name)

        devices = await repo.list_for_owner("user-1")

        assert sorted(d.token for d in devices) == ["a", "b"]
        assert all(d.app_bundle_id == "com.niuu.lexi.chat" for d in devices)

    async def test_delete_removes_connection_and_credential(self, stores):
        integrations, credentials, repo = stores
        await repo.upsert(_device())
        [connection] = await integrations.list_connections("user-1")

        assert await repo.delete("user-1", "apns-token-1")

        assert await integrations.list_connections("user-1") == []
        assert await credentials.get_value("user", "user-1", connection.credential_name) is None
        assert not await repo.delete("user-1", "apns-token-1")
        assert not await repo.delete("user-2", "apns-token-1")

    async def test_ting_does_not_build_push_devices_as_channels(self, stores):
        integrations, credentials, repo = stores
        await repo.upsert(_device())

        factory = NotificationChannelFactory(integrations, credentials)

        assert await factory.for_owner("user-1") == []


class TestDeviceRegistrySelection:
    @pytest.mark.parametrize(
        ("registry", "mini_mode", "expected"),
        [
            ("auto", False, True),
            ("auto", True, False),
            ("integrations", True, True),
            ("postgres", False, False),
        ],
    )
    def test_auto_follows_mini_mode(self, registry, mini_mode, expected):
        config = PushNotificationConfig(device_registry=registry)
        assert config.uses_integration_devices(mini_mode=mini_mode) is expected
