"""Slack delivery through a Slack app bot (OAuth v2 ``xoxb-`` token).

:class:`SlackNotificationSink` implements the strict
:class:`~niuu.ports.notifications.NotificationSink` used by the Forge outbox. A
team's Slack connection stores ``{"access_token": "xoxb-..."}``; the sink posts to a
channel (the route's ``destination``, else the connection's ``channel``) or, when the
notification names a ``recipient_email``, direct-messages that person.

Slack answers most failures with HTTP 200 and ``{"ok": false, "error": "..."}``, so
the body is checked, not only the status. Every failure raises
:class:`~niuu.ports.notifications.NotificationDeliveryError`; neither the token nor an
email address is ever put in a message or a log line.
"""

from __future__ import annotations

from typing import Any

import httpx

from niuu.adapters.notifications.delivery_http import (
    DEFAULT_TIMEOUT_SECONDS,
    retry_after_seconds,
    status_failure,
    transport_failure,
)
from niuu.cap_telemetry import record_truncation
from niuu.ports.notifications import (
    DeliveryResult,
    NotificationDeliveryError,
    NotificationSink,
    OutboundNotification,
)

SLACK_API = "https://slack.com/api"
#: Slack API limit: the text of a Block Kit section, in characters.
SLACK_SECTION_TEXT_MAX_CHARS = 3000
#: Slack API limit: the ``text`` of ``chat.postMessage``, in characters.
SLACK_MESSAGE_TEXT_MAX_CHARS = 40000
#: Knobs reported when a Slack limit forces a cut (the limits are Slack's, not ours).
SECTION_TEXT_KNOB = "slack.api.section_text_max_chars"
MESSAGE_TEXT_KNOB = "slack.api.message_text_max_chars"
_TARGET = "Slack API"
_ELLIPSIS = "…"
_OPEN_LABEL = "Open in Niuu"

_AUTH_ERRORS = frozenset({"invalid_auth", "token_revoked", "account_inactive", "not_authed"})
_CHANNEL_ERRORS = frozenset({"not_in_channel", "channel_not_found"})
# Slack error codes that describe a condition on Slack's side that a retry can clear.
_RETRYABLE_ERRORS = frozenset(
    {"ratelimited", "internal_error", "fatal_error", "service_unavailable", "request_timeout"}
)


def _mrkdwn(value: str) -> str:
    """Escape the three characters Slack's mrkdwn treats as control characters."""
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _http_url(url: str | None) -> str | None:
    if not url or not url.lower().startswith(("https://", "http://")):
        return None
    return url


def _fit(text: str, limit: int, *, knob: str, what: str, remedy: str) -> str:
    """Cut ``text`` to ``limit`` characters, visibly and counted."""
    if len(text) <= limit:
        return text
    kept = text[: limit - len(_ELLIPSIS)].rstrip() + _ELLIPSIS
    record_truncation(knob, what=what, kept=len(kept), total=len(text), remedy=remedy)
    return kept


def _json_or_none(response: httpx.Response) -> dict[str, Any] | None:
    try:
        data = response.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


class SlackNotificationSink(NotificationSink):
    """Posts Forge notifications as a Slack bot: to a channel, or as a DM to one person.

    Keyword arguments come from config or from a project team's messaging connection
    (credential ``access_token``, config ``channel``). Unknown keys are ignored so one
    connection can serve other consumers.
    """

    def __init__(
        self,
        access_token: str = "",
        channel: str = "",
        *,
        name: str = "slack",
        api_base_url: str = SLACK_API,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        client: httpx.AsyncClient | None = None,
        **_extra: object,
    ) -> None:
        if not access_token:
            raise ValueError(
                "Slack sink needs an access_token (the bot token of the installed Slack app)"
            )
        self._name = name
        self._access_token = access_token
        self._channel = channel.strip()
        self._api_base = api_base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=timeout)

    @property
    def name(self) -> str:
        return self._name

    async def send(self, message: OutboundNotification) -> DeliveryResult:
        channel = await self._target(message)
        payload = {"channel": channel, **_render(message)}
        data = await self._call("POST", "chat.postMessage", json=payload)
        ts = data.get("ts")
        return DeliveryResult(provider_message_id=str(ts) if ts is not None else None)

    async def _target(self, message: OutboundNotification) -> str:
        """The Slack channel or user id to post to; raises when there is none."""
        if message.recipient_email:
            data = await self._call(
                "GET", "users.lookupByEmail", params={"email": message.recipient_email}
            )
            user = data.get("user")
            user_id = user.get("id") if isinstance(user, dict) else None
            if not user_id:
                raise NotificationDeliveryError(
                    "Slack users.lookupByEmail returned no user id; check the Slack app's "
                    "users:read.email scope",
                    retryable=False,
                )
            return str(user_id)
        channel = (message.destination or "").strip() or self._channel
        if channel:
            return channel
        raise NotificationDeliveryError(
            "Slack notification has no channel to post to: set the team channel on the "
            "Slack connection or on the project's notification route",
            retryable=False,
        )

    async def _call(self, method: str, api_method: str, **request: Any) -> dict[str, Any]:
        """Call a Slack Web API method and return its ``ok`` body, else raise."""
        url = f"{self._api_base}/{api_method}"
        headers = {"Authorization": f"Bearer {self._access_token}"}
        try:
            response = await self._client.request(method, url, headers=headers, **request)
        except httpx.HTTPError as exc:
            raise transport_failure(exc, _TARGET) from None
        data = _json_or_none(response)
        if not response.is_success:
            error = str((data or {}).get("error") or "")
            raise status_failure(_TARGET, response, description=error)
        if data is None:
            raise NotificationDeliveryError(
                f"{_TARGET} {api_method} returned a body that is not JSON", retryable=True
            )
        if data.get("ok") is True:
            return data
        raise _slack_error(api_method, str(data.get("error") or "unknown_error"), response)

    async def close(self) -> None:
        await self._client.aclose()


def _slack_error(
    api_method: str, error: str, response: httpx.Response
) -> NotificationDeliveryError:
    """Map a Slack ``ok: false`` answer to a retryable or permanent delivery error."""
    if error == "users_not_found":
        return NotificationDeliveryError(
            "Slack has no account for that email in this workspace; the person needs a "
            "Slack account here, or route their notifications to a channel",
            retryable=False,
        )
    if error in _CHANNEL_ERRORS:
        return NotificationDeliveryError(
            f"Slack could not post to the channel ({error}): invite the bot to the channel "
            "(/invite the Slack app) or fix the channel on the connection or route",
            retryable=False,
        )
    if error in _AUTH_ERRORS:
        return NotificationDeliveryError(
            f"Slack rejected the bot token ({error}): reinstall the Slack app for the project",
            retryable=False,
        )
    if error == "missing_scope":
        return NotificationDeliveryError(
            f"Slack app lacks a scope for {api_method}: reinstall the Slack app for the "
            "project and grant chat:write and users:read.email",
            retryable=False,
        )
    if error in _RETRYABLE_ERRORS:
        return NotificationDeliveryError(
            f"{_TARGET} {api_method} failed ({error})",
            retryable=True,
            retry_after=retry_after_seconds(response),
            status_code=response.status_code,
        )
    return NotificationDeliveryError(
        f"{_TARGET} {api_method} refused the request ({error})", retryable=False
    )


def _render(message: OutboundNotification) -> dict[str, Any]:
    """The ``text`` fallback and Block Kit ``blocks`` for ``message``."""
    title = _mrkdwn(message.title)
    body = _mrkdwn(message.body.strip())
    url = _http_url(message.url)

    plain = "\n".join(part for part in (message.title, message.body.strip(), url or "") if part)
    text = _fit(
        plain,
        SLACK_MESSAGE_TEXT_MAX_CHARS,
        knob=MESSAGE_TEXT_KNOB,
        what="Slack message text",
        remedy="shorten the notification body; Slack rejects longer message text",
    )

    head = f"*{title}*"
    section = f"{head}\n{body}" if body else head
    section = _fit(
        section,
        SLACK_SECTION_TEXT_MAX_CHARS,
        knob=SECTION_TEXT_KNOB,
        what="Slack section text",
        remedy="shorten the notification body; Slack rejects longer section text",
    )
    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": section}}
    ]
    if message.session_label:
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": _mrkdwn(message.session_label)}],
            }
        )
    if url:
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": _OPEN_LABEL},
                        "url": url,
                    }
                ],
            }
        )
    return {"text": text, "blocks": blocks}
