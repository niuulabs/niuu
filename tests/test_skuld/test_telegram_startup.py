"""A configured Telegram channel that cannot start fails the session startup.

``telegram.enabled`` is the operator's decision to be reachable from a chat. Every way that
decision cannot be honoured (credentials, dependency, topic, start) raises with the setting to
change, and the broker reports it through ``startup_status()`` and to Forge.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from skuld.broker import Broker
from skuld.channels import TelegramChannelError
from skuld.config import SkuldSettings, TelegramConfig


def _broker(tmp_path: Path, **telegram: object) -> Broker:
    return Broker(
        settings=SkuldSettings(
            session={"id": "tg-session", "workspace_dir": str(tmp_path)},
            telegram={"enabled": True, **telegram},
            chronicle_watcher_enabled=False,
        )
    )


class TestTelegramConfig:
    def test_unknown_topic_mode_fails_at_config_load(self):
        with pytest.raises(ValidationError, match="topic_mode"):
            TelegramConfig(topic_mode="per_chat")

    def test_enabled_fixed_topic_without_thread_id_fails_at_config_load(self):
        with pytest.raises(ValidationError, match="telegram.message_thread_id"):
            TelegramConfig(enabled=True, topic_mode="fixed_topic")

    def test_explicit_shared_chat_is_valid(self):
        assert TelegramConfig(enabled=True, topic_mode="shared_chat").topic_mode == "shared_chat"

    def test_fixed_topic_with_thread_id_is_valid(self):
        config = TelegramConfig(enabled=True, topic_mode="fixed_topic", message_thread_id=5)

        assert config.message_thread_id == 5


class TestResolveCredentials:
    async def test_direct_credentials_win(self, tmp_path):
        broker = _broker(tmp_path, bot_token="123:abc", chat_id="-100")

        assert await broker._resolve_telegram_credentials() == ("123:abc", "-100")

    async def test_unreadable_credential_store_is_fatal_and_chained(self, tmp_path):
        broker = _broker(
            tmp_path,
            credential_name="telegram-main",
            credential_store_adapter="no.such.module.Store",
        )

        with pytest.raises(TelegramChannelError) as excinfo:
            await broker._resolve_telegram_credentials()

        message = str(excinfo.value)
        assert "'telegram-main'" in message
        assert "no.such.module.Store" in message
        assert "telegram.credential_store_kwargs" in message
        assert excinfo.value.__cause__ is not None

    async def test_credential_entry_not_found_is_fatal(self, tmp_path):
        broker = _broker(
            tmp_path,
            credential_name="telegram-main",
            credential_store_kwargs={"base_dir": str(tmp_path / "credentials")},
        )

        with pytest.raises(TelegramChannelError) as excinfo:
            await broker._resolve_telegram_credentials()

        message = str(excinfo.value)
        assert "'telegram-main' was not found" in message
        assert "user/dev-user" in message
        assert "telegram.credential_name" in message

    async def test_no_credentials_configured_returns_what_is_set(self, tmp_path):
        broker = _broker(tmp_path, bot_token="123:abc")

        assert await broker._resolve_telegram_credentials() == ("123:abc", "")


class TestInitTelegramChannel:
    async def test_disabled_telegram_registers_nothing(self, tmp_path):
        broker = Broker(
            settings=SkuldSettings(
                session={"id": "tg-session", "workspace_dir": str(tmp_path)},
                telegram={"enabled": False},
            )
        )

        await broker._init_telegram_channel()

        assert broker._channels.by_type("telegram") == []

    async def test_missing_bot_token_is_fatal(self, tmp_path):
        broker = _broker(tmp_path, chat_id="-100")

        with pytest.raises(TelegramChannelError) as excinfo:
            await broker._init_telegram_channel()

        message = str(excinfo.value)
        assert "telegram.bot_token" in message
        assert "telegram.chat_id" not in message
        assert "telegram.enabled: false" in message
        assert broker._channels.by_type("telegram") == []

    async def test_missing_chat_id_is_fatal(self, tmp_path):
        broker = _broker(tmp_path, bot_token="123:abc")

        with pytest.raises(TelegramChannelError, match="telegram.chat_id is not set"):
            await broker._init_telegram_channel()

    async def test_missing_both_names_both(self, tmp_path):
        broker = _broker(tmp_path)

        with pytest.raises(
            TelegramChannelError, match="telegram.bot_token and telegram.chat_id is not set"
        ):
            await broker._init_telegram_channel()

    async def test_missing_library_is_fatal(self, tmp_path):
        broker = _broker(tmp_path, bot_token="123:abc", chat_id="-100")

        with patch("skuld.channels.HAS_TELEGRAM", False):
            with pytest.raises(TelegramChannelError, match="python-telegram-bot is not installed"):
                await broker._init_telegram_channel()

        assert broker._channels.by_type("telegram") == []

    async def test_start_failure_is_fatal_and_chained(self, tmp_path):
        broker = _broker(tmp_path, bot_token="123:abc", chat_id="-100", notify_only=True)
        cause = ConnectionError("api.telegram.org unreachable")

        with (
            patch("skuld.channels.HAS_TELEGRAM", True),
            patch("skuld.channels.Bot", side_effect=cause),
        ):
            with pytest.raises(TelegramChannelError) as excinfo:
                await broker._init_telegram_channel()

        message = str(excinfo.value)
        assert "api.telegram.org unreachable" in message
        assert "failed to start" in message
        assert excinfo.value.__cause__ is cause
        assert broker._channels.by_type("telegram") == []

    async def test_topic_failure_surfaces_unchanged(self, tmp_path):
        broker = _broker(tmp_path, bot_token="123:abc", chat_id="-100", notify_only=True)
        bot = AsyncMock()
        bot.create_forum_topic = AsyncMock(side_effect=RuntimeError("not a forum"))

        with (
            patch("skuld.channels.HAS_TELEGRAM", True),
            patch("skuld.channels.Bot", return_value=bot),
        ):
            with pytest.raises(TelegramChannelError, match="not a forum") as excinfo:
                await broker._init_telegram_channel()

        assert "telegram.topic_mode: shared_chat" in str(excinfo.value)
        assert broker._channels.by_type("telegram") == []

    async def test_working_channel_is_registered(self, tmp_path):
        broker = _broker(
            tmp_path,
            bot_token="123:abc",
            chat_id="-100",
            notify_only=True,
            topic_mode="shared_chat",
        )

        with (
            patch("skuld.channels.HAS_TELEGRAM", True),
            patch("skuld.channels.Bot", return_value=AsyncMock()),
        ):
            await broker._init_telegram_channel()

        (channel,) = broker._channels.by_type("telegram")
        assert channel.is_open


class TestStartupWithFailingTelegram:
    async def _start(self, broker: Broker) -> None:
        transport = AsyncMock()
        transport.on_event = MagicMock()
        with (
            patch.object(broker, "_create_transport", return_value=transport),
            patch("skuld.broker.ServiceManager") as service_manager_cls,
        ):
            service_manager_cls.return_value = AsyncMock()
            await broker.startup()

    async def test_startup_reports_the_telegram_reason_and_stays_unready(self, tmp_path):
        broker = _broker(tmp_path, chat_id="-100")

        await self._start(broker)

        status = broker.startup_status()
        assert status["ready"] is False
        assert status["startup_state"] == "failed"
        assert status["error"].startswith("TelegramChannelError: ")
        assert "telegram.bot_token is not set" in status["error"]
        assert broker._activity_state == "error"
        assert broker._activity_heartbeat_task is None

    async def test_startup_tells_forge_the_session_failed(self, tmp_path):
        broker = _broker(tmp_path, chat_id="-100")
        broker.volundr_api_url = "http://forge.test"
        broker._post_activity_payload = AsyncMock(return_value=True)  # type: ignore[method-assign]

        await self._start(broker)
        for _ in range(5):
            await asyncio.sleep(0)

        reports = [call.args[0] for call in broker._post_activity_payload.await_args_list]
        (failed,) = [report for report in reports if report["state"] == "error"]
        assert failed["metadata"]["startup_failed"] is True
        assert failed["metadata"]["error"] == broker.startup_status()["error"]
        assert "telegram.bot_token is not set" in failed["metadata"]["error"]

    async def test_startup_does_not_start_later_steps_after_the_failure(self, tmp_path):
        broker = _broker(tmp_path, chat_id="-100")
        broker._init_event_log = AsyncMock()  # type: ignore[method-assign]

        await self._start(broker)

        broker._init_event_log.assert_not_awaited()
