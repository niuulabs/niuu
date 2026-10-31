"""Telegram delivery through the Bot API.

Two classes share one Bot API client:

* :class:`TelegramNotificationSink` implements the strict
  :class:`~niuu.ports.notifications.NotificationSink` used by the Forge outbox. It
  renders HTML with every piece of user text escaped, and raises
  :class:`~niuu.ports.notifications.NotificationDeliveryError` (retryable for 429
  and 5xx, permanent for other 4xx).
* :class:`TelegramNotificationAdapter` is the best-effort per-user
  :class:`~niuu.ports.notification_channel.NotificationChannel` Ting uses. It
  moved here from ``ting.adapters.telegram_notification``, which re-exports it so
  stored ``integration_connections.adapter`` class paths keep resolving.
"""

from __future__ import annotations

import html
import logging
import re
from typing import Any

import httpx

from niuu.adapters.notifications.delivery_http import (
    DEFAULT_TIMEOUT_SECONDS,
    status_failure,
    transport_failure,
)
from niuu.domain.notifications import NotificationSeverity
from niuu.ports.notification_channel import (
    Notification,
    NotificationChannel,
    NotificationUrgency,
)
from niuu.ports.notifications import (
    DeliveryResult,
    NotificationDeliveryError,
    NotificationSink,
    OutboundNotification,
)

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"
# Bot API limit for a message's text after entity parsing, in UTF-16 code units.
MAX_TELEGRAM_TEXT_UNITS = 4096
_TARGET = "Telegram Bot API"
_ELLIPSIS = "…"
_TAG = re.compile(r"<[^>]+>")

SEVERITY_EMOJI: dict[NotificationSeverity, str] = {
    NotificationSeverity.INFO: "\u2139\ufe0f",
    NotificationSeverity.SUCCESS: "\u2705",
    NotificationSeverity.WARNING: "\u26a0\ufe0f",
    NotificationSeverity.CRITICAL: "\U0001f6a8",
}

_URGENCY_EMOJI = {
    NotificationUrgency.HIGH: "\u26a0\ufe0f",
    NotificationUrgency.MEDIUM: "\u2139\ufe0f",
    NotificationUrgency.LOW: "\u2705",
}

_URGENCY_RANK = {
    NotificationUrgency.LOW: 0,
    NotificationUrgency.MEDIUM: 1,
    NotificationUrgency.HIGH: 2,
}


def _utf16_units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _truncate_units(text: str, limit: int) -> str:
    """Cut ``text`` to at most ``limit`` UTF-16 units, marking the cut with an ellipsis."""
    if _utf16_units(text) <= limit:
        return text
    if limit <= _utf16_units(_ELLIPSIS):
        return ""
    budget = limit - _utf16_units(_ELLIPSIS)
    kept: list[str] = []
    used = 0
    for char in text:
        width = _utf16_units(char)
        if used + width > budget:
            break
        kept.append(char)
        used += width
    return "".join(kept).rstrip() + _ELLIPSIS


def _text(value: str) -> str:
    return html.escape(value, quote=False)


def _href(url: str | None) -> str | None:
    """An escaped ``href`` for http(s) URLs only; anything else is not linked."""
    if not url or not url.lower().startswith(("https://", "http://")):
        return None
    return html.escape(url, quote=True)


def _visible_units(markup: str) -> int:
    return _utf16_units(html.unescape(_TAG.sub("", markup)))


def format_telegram_html(
    message: OutboundNotification, *, max_units: int = MAX_TELEGRAM_TEXT_UNITS
) -> str:
    """Render ``message`` as Telegram HTML (``parse_mode=HTML``).

    Layout: severity emoji and bold title, an italic context line (severity,
    kind, session, host), the body, the notification's links, then a link back
    to Forge. All user text is HTML-escaped; only http(s) URLs become links. The
    body is shortened so the whole message stays within the Bot API limit.
    """
    emoji = SEVERITY_EMOJI.get(message.severity, "")
    head = [f"{emoji} <b>{_text(message.title)}</b>".strip()]
    context = [
        message.severity.value.capitalize(),
        message.kind.value.replace("_", " "),
        message.session_label or "",
        message.host_label or "",
    ]
    head.append("<i>" + _text(" · ".join(part for part in context if part)) + "</i>")

    tail: list[str] = []
    link_lines = [
        f'• <a href="{href}">{_text(link.label)}</a>'
        for link in message.links
        if (href := _href(link.url)) is not None
    ]
    if link_lines:
        tail.extend(["", *link_lines])
    forge_href = _href(message.url)
    if forge_href is not None:
        label = "Open session in Forge" if message.session_id else "Open in Forge"
        tail.extend(["", f'<a href="{forge_href}">{_text(label)}</a>'])

    body = message.body.strip()
    if not body:
        return "\n".join([*head, *tail])
    fixed = "\n".join([*head, "", *tail])
    # One more newline joins the body to what follows it.
    budget = max_units - _visible_units(fixed) - 1
    body_markup = _text(_truncate_units(body, budget))
    if not body_markup:
        return "\n".join([*head, *tail])
    return "\n".join([*head, "", body_markup, *tail])


def _thread_id(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(str(value))


class TelegramNotificationSink(NotificationSink):
    """Sends notifications to one Telegram chat via the Bot API ``sendMessage``.

    Keyword arguments come from config or from an owner's messaging integration
    (credential ``bot_token`` and ``chat_id`` plus connection config). Unknown
    keys, such as Ting's ``min_urgency``, are ignored so one connection serves
    both consumers.
    """

    def __init__(
        self,
        bot_token: str = "",
        chat_id: str | int = "",
        *,
        name: str = "telegram",
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        message_thread_id: int | str | None = None,
        disable_web_page_preview: bool | None = True,
        api_base: str = TELEGRAM_API,
        client: httpx.AsyncClient | None = None,
        **_unused: object,
    ) -> None:
        self._name = name
        self._bot_token = bot_token
        self._chat_id = str(chat_id).strip()
        self._message_thread_id = _thread_id(message_thread_id)
        self._disable_web_page_preview = disable_web_page_preview
        self._api_base = api_base.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=timeout)

    @property
    def name(self) -> str:
        return self._name

    async def send(self, message: OutboundNotification) -> DeliveryResult:
        return await self.send_text(format_telegram_html(message), parse_mode="HTML")

    async def send_text(self, text: str, *, parse_mode: str | None) -> DeliveryResult:
        """POST ``sendMessage``; raise :class:`NotificationDeliveryError` on failure."""
        if not self._bot_token or not self._chat_id:
            raise NotificationDeliveryError(
                "Telegram sink needs a bot_token and a chat_id", retryable=False
            )
        payload: dict[str, Any] = {"chat_id": self._chat_id, "text": text}
        if self._disable_web_page_preview is not None:
            payload["disable_web_page_preview"] = self._disable_web_page_preview
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if self._message_thread_id is not None:
            payload["message_thread_id"] = self._message_thread_id
        url = f"{self._api_base}/bot{self._bot_token}/sendMessage"
        try:
            response = await self._client.post(url, json=payload)
        except httpx.HTTPError as exc:
            raise transport_failure(exc, _TARGET) from None
        data = _json_or_none(response)
        if response.is_success and (data is None or data.get("ok", True)):
            result = (data or {}).get("result") or {}
            message_id = result.get("message_id") if isinstance(result, dict) else None
            return DeliveryResult(
                provider_message_id=str(message_id) if message_id is not None else None
            )
        description = str((data or {}).get("description") or "")
        parameters = (data or {}).get("parameters") or {}
        retry_after = parameters.get("retry_after") if isinstance(parameters, dict) else None
        if response.is_success:
            raise NotificationDeliveryError(
                f"{_TARGET} refused the message: {description}", retryable=False
            )
        raise status_failure(
            _TARGET,
            response,
            description=description,
            retry_after=float(retry_after) if isinstance(retry_after, int | float) else None,
        )

    async def close(self) -> None:
        await self._client.aclose()


def _json_or_none(response: httpx.Response) -> dict[str, Any] | None:
    try:
        data = response.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


class TelegramNotificationAdapter(NotificationChannel):
    """Telegram channel for Ting's event notifications.

    ``should_notify`` filters by urgency, and ``send`` raises
    :class:`~niuu.ports.notifications.NotificationDeliveryError` when Telegram does
    not accept the message. Messages use legacy Markdown formatting.
    """

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        min_urgency: str = "low",
        notify_only: bool = False,
        message_thread_id: int | None = None,
        **_unused: object,
    ) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._min_urgency = NotificationUrgency(min_urgency)
        self._notify_only = notify_only
        self._message_thread_id = message_thread_id
        self._sink = TelegramNotificationSink(
            bot_token,
            chat_id,
            timeout=timeout,
            message_thread_id=message_thread_id,
            disable_web_page_preview=None,
        )

    def should_notify(self, notification: Notification) -> bool:
        """Only notify if the urgency meets the minimum threshold."""
        return _URGENCY_RANK.get(notification.urgency, 0) >= _URGENCY_RANK[self._min_urgency]

    async def send(self, notification: Notification) -> None:
        """Send a formatted message to the Telegram chat; raise if it is not accepted."""
        await self._sink.send_text(self._format_message(notification), parse_mode="Markdown")

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._sink.close()

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
