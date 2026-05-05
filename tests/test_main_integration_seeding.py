"""Tests for config-seeded integrations at Volundr startup."""

from __future__ import annotations

from unittest.mock import AsyncMock

from volundr.adapters.outbound.memory_integrations import InMemoryIntegrationRepository
from volundr.config import (
    IntegrationsConfig,
    IntegrationType,
    SecretType,
    SeededIntegrationConnectionConfig,
    SeededIntegrationCredentialConfig,
    Settings,
)
from volundr.main import _seed_configured_integrations, _seeded_integration_connection_id


async def test_seed_configured_integrations_stores_credential_and_connection() -> None:
    integration_repo = InMemoryIntegrationRepository()
    credential_store = AsyncMock()
    settings = Settings(
        integrations=IntegrationsConfig(
            seed_connections=[
                SeededIntegrationConnectionConfig(
                    owner_id="dev-user",
                    integration_type=IntegrationType.MESSAGING,
                    adapter="tyr.adapters.telegram_notification.TelegramNotificationAdapter",
                    credential_name="telegram-main",
                    slug="telegram",
                    enabled=True,
                    config={"notify_only": True},
                    credential=SeededIntegrationCredentialConfig(
                        secret_type=SecretType.GENERIC,
                        data={"bot_token": "foobar", "chat_id": "foobar"},
                    ),
                )
            ]
        )
    )

    await _seed_configured_integrations(
        integration_repo=integration_repo,
        credential_store=credential_store,
        settings=settings,
    )

    credential_store.store.assert_awaited_once_with(
        owner_type="user",
        owner_id="dev-user",
        name="telegram-main",
        secret_type=SecretType.GENERIC,
        data={"bot_token": "foobar", "chat_id": "foobar"},
        metadata={},
    )
    seeded_id = _seeded_integration_connection_id(
        owner_type="user",
        owner_id="dev-user",
        integration_type="messaging",
        adapter="tyr.adapters.telegram_notification.TelegramNotificationAdapter",
        credential_name="telegram-main",
        slug="telegram",
    )
    connection = await integration_repo.get_connection(seeded_id)
    assert connection is not None
    assert connection.owner_id == "dev-user"
    assert connection.integration_type == IntegrationType.MESSAGING
    assert connection.credential_name == "telegram-main"
    assert connection.slug == "telegram"
    assert connection.enabled is True
    assert connection.config == {"notify_only": True}


def test_seeded_integration_connection_id_is_stable() -> None:
    first = _seeded_integration_connection_id(
        owner_type="user",
        owner_id="dev-user",
        integration_type="messaging",
        adapter="tyr.adapters.telegram_notification.TelegramNotificationAdapter",
        credential_name="telegram-main",
        slug="telegram",
    )
    second = _seeded_integration_connection_id(
        owner_type="user",
        owner_id="dev-user",
        integration_type="messaging",
        adapter="tyr.adapters.telegram_notification.TelegramNotificationAdapter",
        credential_name="telegram-main",
        slug="telegram",
    )

    assert first == second


async def test_seed_configured_integrations_respects_explicit_id() -> None:
    integration_repo = InMemoryIntegrationRepository()
    credential_store = AsyncMock()
    settings = Settings(
        integrations=IntegrationsConfig(
            seed_connections=[
                SeededIntegrationConnectionConfig(
                    id="f976d725-2a19-558a-a2d0-99258577f615",
                    owner_id="dev-user",
                    integration_type=IntegrationType.ISSUE_TRACKER,
                    adapter="volundr.adapters.outbound.linear.LinearAdapter",
                    credential_name="linear-config",
                    slug="linear",
                    enabled=True,
                    credential=SeededIntegrationCredentialConfig(
                        secret_type=SecretType.API_KEY,
                        data={"api_key": "linear-foobar"},
                    ),
                )
            ]
        )
    )

    await _seed_configured_integrations(
        integration_repo=integration_repo,
        credential_store=credential_store,
        settings=settings,
    )

    connection = await integration_repo.get_connection("f976d725-2a19-558a-a2d0-99258577f615")
    assert connection is not None
    assert connection.slug == "linear"
    assert connection.credential_name == "linear-config"
