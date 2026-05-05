"""Tests for NotificationChannelContributor."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from niuu.domain.models import IntegrationType
from volundr.adapters.outbound.contributors.notification_channels import (
    NotificationChannelContributor,
)
from volundr.domain.models import GitSource, IntegrationConnection, Principal, Session
from volundr.domain.ports import SessionContext


@pytest.fixture
def session() -> Session:
    return Session(
        name="test",
        model="claude",
        source=GitSource(repo="", branch="main"),
        owner_id="user-1",
    )


@pytest.fixture
def principal() -> Principal:
    return Principal(user_id="user-1", email="u@x.com", tenant_id="t-1", roles=[])


def _telegram_connection(
    *,
    enabled: bool = True,
    config: dict | None = None,
) -> IntegrationConnection:
    return IntegrationConnection(
        id="conn-telegram",
        owner_id="user-1",
        integration_type=IntegrationType.MESSAGING,
        adapter="tyr.adapters.telegram_notification.TelegramNotificationAdapter",
        credential_name="telegram-cred",
        config=config or {},
        enabled=enabled,
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
        slug="telegram",
    )


class TestNotificationChannelContributor:
    async def test_name(self) -> None:
        contributor = NotificationChannelContributor()
        assert contributor.name == "notification_channels"

    async def test_non_flock_session_returns_empty(self, session, principal) -> None:
        contributor = NotificationChannelContributor(credential_store=AsyncMock())
        context = SessionContext(
            principal=principal,
            workload_type="session",
            integration_connections=(_telegram_connection(),),
        )

        result = await contributor.contribute(session, context)

        assert result.values == {}

    async def test_missing_owner_returns_empty(self, principal) -> None:
        contributor = NotificationChannelContributor(credential_store=AsyncMock())
        anon_session = Session(
            name="test",
            model="claude",
            source=GitSource(repo="", branch="main"),
        )
        context = SessionContext(
            principal=principal,
            workload_type="ravn_flock",
            integration_connections=(_telegram_connection(),),
        )

        result = await contributor.contribute(anon_session, context)

        assert result.values == {}

    async def test_missing_credential_store_returns_empty(self, session, principal) -> None:
        contributor = NotificationChannelContributor()
        context = SessionContext(
            principal=principal,
            workload_type="ravn_flock",
            integration_connections=(_telegram_connection(),),
        )

        result = await contributor.contribute(session, context)

        assert result.values == {}

    async def test_resolves_telegram_runtime_channel(self, session, principal) -> None:
        credential_store = AsyncMock()
        credential_store.get_value.return_value = {
            "bot_token": "bot-token",
            "chat_id": "chat-123",
        }
        contributor = NotificationChannelContributor(credential_store=credential_store)
        context = SessionContext(
            principal=principal,
            workload_type="ravn_flock",
            integration_connections=(_telegram_connection(),),
        )

        result = await contributor.contribute(session, context)

        assert result.values == {
            "broker": {
                "telegram": {
                    "enabled": True,
                    "botToken": "bot-token",
                    "chatId": "chat-123",
                    "notifyOnly": True,
                    "topicMode": "topic_per_session",
                }
            }
        }
        credential_store.get_value.assert_awaited_once_with("user", "user-1", "telegram-cred")

    async def test_respects_notify_only_override(self, session, principal) -> None:
        credential_store = AsyncMock()
        credential_store.get_value.return_value = {
            "bot_token": "bot-token",
            "chat_id": "chat-123",
        }
        contributor = NotificationChannelContributor(credential_store=credential_store)
        context = SessionContext(
            principal=principal,
            workload_type="ravn_flock",
            integration_connections=(_telegram_connection(config={"notify_only": False}),),
        )

        result = await contributor.contribute(session, context)

        assert result.values["broker"]["telegram"]["notifyOnly"] is False

    async def test_topic_per_session_defaults_on(self, session, principal) -> None:
        credential_store = AsyncMock()
        credential_store.get_value.return_value = {
            "bot_token": "bot-token",
            "chat_id": "chat-123",
        }
        contributor = NotificationChannelContributor(credential_store=credential_store)
        context = SessionContext(
            principal=principal,
            workload_type="ravn_flock",
            integration_connections=(_telegram_connection(config={}),),
        )

        result = await contributor.contribute(session, context)

        assert result.values["broker"]["telegram"]["topicMode"] == "topic_per_session"

    async def test_can_disable_topic_per_session(self, session, principal) -> None:
        credential_store = AsyncMock()
        credential_store.get_value.return_value = {
            "bot_token": "bot-token",
            "chat_id": "chat-123",
        }
        contributor = NotificationChannelContributor(credential_store=credential_store)
        context = SessionContext(
            principal=principal,
            workload_type="ravn_flock",
            integration_connections=(
                _telegram_connection(config={"topic_per_session": False}),
            ),
        )

        result = await contributor.contribute(session, context)

        assert result.values["broker"]["telegram"]["topicMode"] == "shared_chat"

    async def test_message_thread_id_selects_fixed_topic(self, session, principal) -> None:
        credential_store = AsyncMock()
        credential_store.get_value.return_value = {
            "bot_token": "bot-token",
            "chat_id": "chat-123",
        }
        contributor = NotificationChannelContributor(credential_store=credential_store)
        context = SessionContext(
            principal=principal,
            workload_type="ravn_flock",
            integration_connections=(
                _telegram_connection(config={"message_thread_id": 42}),
            ),
        )

        result = await contributor.contribute(session, context)

        assert result.values["broker"]["telegram"]["topicMode"] == "fixed_topic"
        assert result.values["broker"]["telegram"]["messageThreadId"] == 42

    async def test_skips_missing_credentials_and_unsupported_connections(
        self,
        session,
        principal,
    ) -> None:
        credential_store = AsyncMock()
        credential_store.get_value.side_effect = [
            None,
            {"bot_token": "bot-token", "chat_id": "chat-123"},
        ]
        contributor = NotificationChannelContributor(credential_store=credential_store)
        other = IntegrationConnection(
            id="conn-linear",
            owner_id="user-1",
            integration_type=IntegrationType.ISSUE_TRACKER,
            adapter="volundr.adapters.outbound.linear.LinearAdapter",
            credential_name="linear-cred",
            config={},
            enabled=True,
            created_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
            slug="linear",
        )
        second_tg = IntegrationConnection(
            id="conn-telegram-2",
            owner_id="user-1",
            integration_type=IntegrationType.MESSAGING,
            adapter="tyr.adapters.telegram_notification.TelegramNotificationAdapter",
            credential_name="telegram-cred-2",
            config={},
            enabled=True,
            created_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
            slug="telegram",
        )
        context = SessionContext(
            principal=principal,
            workload_type="ravn_flock",
            integration_connections=(_telegram_connection(), other, second_tg),
        )

        result = await contributor.contribute(session, context)

        assert result.values["broker"]["telegram"]["chatId"] == "chat-123"
        assert credential_store.get_value.await_count == 2
