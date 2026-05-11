"""Telegram notification adapter — sends formatted notifications via Telegram Bot API."""

from __future__ import annotations

import logging

import httpx

from tyr.ports.notification_channel import (
    Notification,
    NotificationChannel,
    NotificationUrgency,
)

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"

_URGENCY_EMOJI = {
    NotificationUrgency.HIGH: "\u26a0\ufe0f",
    NotificationUrgency.MEDIUM: "\u2139\ufe0f",
    NotificationUrgency.LOW: "\u2705",
}


class TelegramNotificationAdapter(NotificationChannel):
    """Sends notifications to a Telegram chat via the Bot API."""

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        *,
        timeout: float = 10.0,
        min_urgency: str = "low",
        notify_only: bool = False,
        message_thread_id: int | None = None,
        **_unused: object,
    ) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._client = httpx.AsyncClient(timeout=timeout)
        self._min_urgency = NotificationUrgency(min_urgency)
        self._notify_only = notify_only
        self._message_thread_id = message_thread_id
        self._urgency_rank = {
            NotificationUrgency.LOW: 0,
            NotificationUrgency.MEDIUM: 1,
            NotificationUrgency.HIGH: 2,
        }

    def should_notify(self, notification: Notification) -> bool:
        """Only notify if the urgency meets the minimum threshold."""
        return (
            self._urgency_rank.get(notification.urgency, 0) >= self._urgency_rank[self._min_urgency]
        )

    async def send(self, notification: Notification) -> None:
        """Send a formatted message to the Telegram chat."""
        if not self._bot_token:
            logger.warning("Cannot send notification — bot_token not configured")
            return

        text = self._format_message(notification)
        url = f"{TELEGRAM_API}/bot{self._bot_token}/sendMessage"
        try:
            resp = await self._client.post(
                url,
                json={
                    "chat_id": self._chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                    **(
                        {"message_thread_id": self._message_thread_id}
                        if self._message_thread_id is not None
                        else {}
                    ),
                },
            )
            if resp.status_code != 200:
                logger.warning(
                    "Telegram API returned %d for chat %s",
                    resp.status_code,
                    self._chat_id,
                )
        except Exception:
            logger.warning(
                "Failed to send Telegram notification to %s",
                self._chat_id,
                exc_info=True,
            )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    @staticmethod
    def _format_message(notification: Notification) -> str:
        """Format a notification as a Markdown message."""
        emoji = _URGENCY_EMOJI.get(notification.urgency, "")
        parts = [f"{emoji} *{notification.title}*", "", notification.body]

        pr_url = notification.metadata.get("pr_url")
        if pr_url:
            parts.append(f"\n[View PR]({pr_url})")

        tracker_id = notification.metadata.get("tracker_id")
        if tracker_id:
            parts.append(f"Ticket: {tracker_id}")

        return "\n".join(parts)
