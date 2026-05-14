"""Notification subscription repository port — lookup for Telegram auth."""

from __future__ import annotations

from abc import ABC, abstractmethod


class NotificationSubscriptionRepository(ABC):
    """Read-only port for notification subscription lookups."""

    @abstractmethod
    async def find_owner_by_telegram_chat_id(self, chat_id: str) -> str | None:
        """Return the owner_id for a telegram subscription matching *chat_id*, or None."""
        ...
