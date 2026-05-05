"""Shared Telegram inbound consumer for routing replies into live sessions."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from niuu.domain.models import IntegrationType
from niuu.ports.credentials import CredentialStorePort
from niuu.ports.integrations import IntegrationRepository
from volundr.domain.models import CommunicationPlatform, InboundCommunicationMessage
from volundr.domain.ports import CommunicationCursorRepository
from volundr.domain.services.communication_ingress import CommunicationIngressService

logger = logging.getLogger(__name__)

try:
    from telegram import Bot

    HAS_TELEGRAM = True
except ImportError:  # pragma: no cover
    Bot = object  # type: ignore[assignment]
    HAS_TELEGRAM = False


@dataclass(frozen=True)
class TelegramBotBinding:
    """Resolved Telegram bot binding from integrations + credentials."""

    bot_token: str
    owner_ids: tuple[str, ...]
    connection_ids: tuple[str, ...]


class TelegramIngressService:
    """Poll Telegram updates and route replies via the communication ingress service."""

    def __init__(
        self,
        integration_repo: IntegrationRepository,
        credential_store: CredentialStorePort,
        communication_ingress: CommunicationIngressService,
        cursor_repository: CommunicationCursorRepository | None = None,
        *,
        refresh_interval_s: float = 30.0,
        poll_timeout_s: int = 20,
    ) -> None:
        self._integration_repo = integration_repo
        self._credential_store = credential_store
        self._communication_ingress = communication_ingress
        self._cursor_repository = cursor_repository
        self._refresh_interval_s = refresh_interval_s
        self._poll_timeout_s = poll_timeout_s
        self._manager_task: asyncio.Task | None = None
        self._bot_tasks: dict[str, asyncio.Task] = {}
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        """Start the shared Telegram ingress manager."""
        if self._manager_task is not None:
            return
        if not HAS_TELEGRAM:
            logger.warning("python-telegram-bot not installed; Telegram ingress disabled")
            return
        self._stop_event.clear()
        self._manager_task = asyncio.create_task(self._run_manager())

    async def stop(self) -> None:
        """Stop the shared Telegram ingress manager and all pollers."""
        self._stop_event.set()
        manager = self._manager_task
        self._manager_task = None
        if manager is not None:
            manager.cancel()
            await asyncio.gather(manager, return_exceptions=True)

        tasks = list(self._bot_tasks.values())
        self._bot_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_manager(self) -> None:
        try:
            while not self._stop_event.is_set():
                bindings = await self._load_bindings()
                active_tokens = {binding.bot_token for binding in bindings}
                for binding in bindings:
                    if binding.bot_token in self._bot_tasks:
                        continue
                    self._bot_tasks[binding.bot_token] = asyncio.create_task(
                        self._run_bot(binding)
                    )

                stale = [token for token in self._bot_tasks if token not in active_tokens]
                for token in stale:
                    task = self._bot_tasks.pop(token)
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._refresh_interval_s,
                    )
                except TimeoutError:
                    continue
        except asyncio.CancelledError:
            raise

    async def _load_bindings(self) -> list[TelegramBotBinding]:
        connections = await self._integration_repo.list_connections_global(
            IntegrationType.MESSAGING,
            slug="telegram",
            enabled_only=True,
        )
        grouped: dict[str, dict[str, set[str]]] = {}
        for connection in connections:
            try:
                values = await self._credential_store.get_value(
                    "user",
                    connection.owner_id,
                    connection.credential_name,
                )
            except Exception:
                logger.warning(
                    "Failed to resolve Telegram credential %s for %s",
                    connection.credential_name,
                    connection.owner_id,
                    exc_info=True,
                )
                continue
            if not values:
                continue
            bot_token = str(values.get("bot_token") or "").strip()
            if not bot_token:
                continue
            bucket = grouped.setdefault(bot_token, {"owners": set(), "connections": set()})
            bucket["owners"].add(connection.owner_id)
            bucket["connections"].add(connection.id)

        return [
            TelegramBotBinding(
                bot_token=token,
                owner_ids=tuple(sorted(bucket["owners"])),
                connection_ids=tuple(sorted(bucket["connections"])),
            )
            for token, bucket in grouped.items()
        ]

    async def _run_bot(self, binding: TelegramBotBinding) -> None:
        bot = Bot(token=binding.bot_token)
        offset = await self._load_offset(binding)
        try:
            while not self._stop_event.is_set():
                updates = await bot.get_updates(
                    offset=offset,
                    timeout=self._poll_timeout_s,
                    allowed_updates=["message", "edited_message"],
                )
                max_offset = offset
                for update in updates:
                    max_offset = max(max_offset or 0, int(update.update_id) + 1)
                    await self._handle_update(update)
                if max_offset is not None and max_offset != offset:
                    offset = max_offset
                    await self._store_offset(binding, offset)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Telegram ingress poller failed", exc_info=True)

    async def _handle_update(self, update: object) -> None:
        message = (
            getattr(update, "message", None)
            or getattr(update, "edited_message", None)
            or getattr(update, "effective_message", None)
        )
        if message is None:
            return
        text = getattr(message, "text", None) or getattr(message, "caption", None)
        if not text or not str(text).strip():
            return

        sender = getattr(message, "from_user", None) or getattr(message, "from_", None)
        if sender is not None and getattr(sender, "is_bot", False):
            return

        chat = getattr(message, "chat", None)
        chat_id = getattr(chat, "id", None)
        if chat_id is None:
            chat_id = getattr(message, "chat_id", None)
        if chat_id is None:
            return

        thread_id = getattr(message, "message_thread_id", None)
        inbound = InboundCommunicationMessage(
            platform=CommunicationPlatform.TELEGRAM,
            conversation_id=str(chat_id),
            thread_id=str(thread_id) if thread_id is not None else None,
            sender_external_id=str(getattr(sender, "id", "")),
            sender_display_name=_telegram_sender_name(sender),
            text=str(text),
            raw=_telegram_raw_metadata(update, message),
        )
        await self._communication_ingress.handle_inbound_message(inbound)

    async def _load_offset(self, binding: TelegramBotBinding) -> int | None:
        if self._cursor_repository is None:
            return None
        value = await self._cursor_repository.get_cursor(
            CommunicationPlatform.TELEGRAM.value,
            _consumer_key(binding.bot_token),
        )
        if value in (None, ""):
            return None
        try:
            return int(value)
        except ValueError:
            logger.warning("Invalid stored Telegram cursor %r; ignoring", value)
            return None

    async def _store_offset(self, binding: TelegramBotBinding, offset: int) -> None:
        if self._cursor_repository is None:
            return
        await self._cursor_repository.upsert_cursor(
            CommunicationPlatform.TELEGRAM.value,
            _consumer_key(binding.bot_token),
            str(offset),
        )


def _telegram_sender_name(sender: object | None) -> str:
    if sender is None:
        return ""
    username = getattr(sender, "username", None)
    first_name = getattr(sender, "first_name", None)
    last_name = getattr(sender, "last_name", None)
    if username:
        return str(username)
    full_name = " ".join(part for part in [first_name, last_name] if part)
    return full_name.strip()


def _telegram_raw_metadata(update: object, message: object) -> dict[str, Any]:
    sender = getattr(message, "from_user", None) or getattr(message, "from_", None)
    chat = getattr(message, "chat", None)
    return {
        "update_id": getattr(update, "update_id", None),
        "message_id": getattr(message, "message_id", None),
        "chat_type": getattr(chat, "type", None),
        "sender_username": getattr(sender, "username", None),
    }


def _consumer_key(bot_token: str) -> str:
    return hashlib.sha256(bot_token.encode("utf-8")).hexdigest()


def build_test_update(
    *,
    update_id: int,
    chat_id: int,
    text: str,
    message_thread_id: int | None = None,
    sender_id: int = 1,
    sender_name: str = "User",
    sender_username: str = "user",
    is_bot: bool = False,
) -> object:
    """Helper for tests to build a Telegram-like update object."""
    sender = SimpleNamespace(
        id=sender_id,
        first_name=sender_name,
        last_name=None,
        username=sender_username,
        is_bot=is_bot,
    )
    chat = SimpleNamespace(id=chat_id, type="supergroup")
    message = SimpleNamespace(
        text=text,
        message_id=1,
        message_thread_id=message_thread_id,
        from_user=sender,
        chat=chat,
    )
    return SimpleNamespace(update_id=update_id, message=message)
