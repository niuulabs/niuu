"""Outbound NotificationChannel adapters for session attention pushes.

Selected via the dynamic-adapter config (``push.adapter`` + ``kwargs``):

- ``LoggingNotificationChannel`` — default; logs only, no external calls.
- ``WebhookNotificationChannel`` — POSTs the push to a relay URL (the relay
  owns device fan-out). HTTP/1.1, no extra dependencies.
- ``ApnsNotificationChannel`` — direct Apple Push, token-based (JWT) auth, for
  this node's needs-input pushes. APNs only speaks HTTP/2, so httpx needs ``h2``
  (the ``httpx[http2]`` extra). The Guild push gateway (``push.gateway``) is the
  full path: alerts for every notification, Live Activity and widget pushes.

A device registered with an ``environment`` ("sandbox" for development builds,
"production" for TestFlight/App Store) is pushed through that APNs host; one
without uses the channel's default (``use_sandbox``).

Each channel has two entry points, and both raise ``NotificationDeliveryError``
when nothing was delivered. ``send`` is what the needs-input attention push
uses (the session service runs it detached and reports a failure); ``deliver``
is what the notification outbox uses (through ``PushNotificationSink``) and also
describes what was delivered, so the outbox can retry or give up honestly.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Any

import httpx

from niuu.adapters.notifications.delivery_http import (
    is_retryable_status,
    redact_url,
    status_failure,
    transport_failure,
)
from niuu.ports.notifications import NotificationDeliveryError
from volundr.adapters.outbound.apns_client import check_signing_key, load_signing_key
from volundr.domain.models import DevicePlatform, DeviceToken, PushMessage
from volundr.domain.ports import NotificationChannel
from volundr.domain.push import PushTokenKind

logger = logging.getLogger(__name__)


class LoggingNotificationChannel(NotificationChannel):
    """No-op channel that records what WOULD be pushed. Safe default."""

    async def send(self, message: PushMessage, devices: list[DeviceToken]) -> None:
        logger.info(
            "Push (logging): owner=%s kind=%s session=%s devices=%d title=%r",
            message.owner_id,
            message.kind,
            message.session_id,
            len(devices),
            message.title,
        )


class WebhookNotificationChannel(NotificationChannel):
    """POST the push as JSON to a relay URL, optionally HMAC-signed.

    The relay (a push gateway) is responsible for resolving the owner's devices
    and delivering to APNs/FCM/web-push, so this channel does not require
    registered device tokens.
    """

    def __init__(
        self,
        *,
        url: str,
        secret: str = "",
        timeout_seconds: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ):
        self._url = url
        self._secret = secret
        self._timeout = timeout_seconds
        self._client = client

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def send(self, message: PushMessage, devices: list[DeviceToken]) -> None:
        await self.deliver(message, devices)

    async def deliver(self, message: PushMessage, devices: list[DeviceToken]) -> str:
        if not self._url:
            raise NotificationDeliveryError("Push relay has no url configured", retryable=False)
        payload: dict[str, Any] = {
            "type": message.event_type,
            "owner_id": message.owner_id,
            "session_id": message.session_id,
            "kind": message.kind,
            "title": message.title,
            "body": message.body,
            "urgency": message.urgency,
            "request_id": message.request_id,
            "device_tokens": [{"platform": d.platform.value, "token": d.token} for d in devices],
        }
        if message.notification_id:
            payload["notification_id"] = message.notification_id
        if message.url:
            payload["url"] = message.url
        payload.update(_resident_fields(message))
        if message.category:
            payload["category"] = message.category
        payload.update(message.data)
        headers = {"content-type": "application/json"}
        if self._secret:
            body = httpx.Request("POST", self._url, json=payload).content
            signature = hmac.new(self._secret.encode(), body, hashlib.sha256).hexdigest()
            headers["X-Niuu-Signature"] = f"sha256={signature}"
        target = f"push relay {redact_url(self._url)}"
        client = await self._get_client()
        try:
            resp = await client.post(self._url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise transport_failure(exc, target) from None
        if resp.status_code >= httpx.codes.BAD_REQUEST:
            raise status_failure(target, resp)
        return f"{target} accepted HTTP {resp.status_code}"


class ApnsNotificationChannel(NotificationChannel):
    """Direct Apple Push Notification service channel (token-based auth).

    Signs a short-lived ES256 JWT (PyJWT + cryptography) with the .p8 key and
    delivers one request per iOS alert token over HTTP/2, on the APNs host of the
    token's environment (``use_sandbox`` picks the host of a token that names none).
    Pushes use the ``time-sensitive`` interruption level so the alert surfaces
    even under Focus. The key comes from ``private_key_path`` (the .p8 file) or,
    as PEM text, from ``private_key``; a missing or wrong key raises here.
    """

    _PROD_HOST = "https://api.push.apple.com"
    _SANDBOX_HOST = "https://api.sandbox.push.apple.com"

    def __init__(
        self,
        *,
        team_id: str,
        key_id: str,
        bundle_id: str,
        private_key: str = "",
        private_key_path: str = "",
        use_sandbox: bool = False,
        token_ttl_seconds: int = 3000,
        client: httpx.AsyncClient | None = None,
    ):
        if bool(private_key) == bool(private_key_path):
            raise ValueError("APNs needs exactly one of private_key_path and private_key")
        if private_key_path:
            private_key = load_signing_key(private_key_path)
        else:
            check_signing_key(private_key, source="the private_key setting")
        self._team_id = team_id
        self._key_id = key_id
        self._private_key = private_key
        self._bundle_id = bundle_id
        # The host for a token that names no environment.
        self._host = self._SANDBOX_HOST if use_sandbox else self._PROD_HOST
        self._token_ttl = token_ttl_seconds
        self._client = client
        self._jwt_cache: str | None = None
        self._jwt_issued_at: float = 0.0

    def _auth_token(self) -> str:
        """Return a cached/fresh provider JWT (Apple allows reuse < 1h)."""
        import jwt  # PyJWT — local import keeps module import light

        now = time.time()
        if self._jwt_cache and (now - self._jwt_issued_at) < self._token_ttl:
            return self._jwt_cache
        token = jwt.encode(
            {"iss": self._team_id, "iat": int(now)},
            self._private_key,
            algorithm="ES256",
            headers={"kid": self._key_id},
        )
        self._jwt_cache = token
        self._jwt_issued_at = now
        return token

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            # HTTP/2 is mandatory for APNs; requires the `h2` package at deploy time.
            self._client = httpx.AsyncClient(http2=True, timeout=10.0)
        return self._client

    @staticmethod
    def _alert_devices(devices: list[DeviceToken]) -> list[DeviceToken]:
        """The tokens an alert can go to: iOS alert tokens, not Live Activity or widget ones."""
        return [
            d for d in devices if d.platform is DevicePlatform.IOS and d.kind is PushTokenKind.ALERT
        ]

    async def send(self, message: PushMessage, devices: list[DeviceToken]) -> None:
        # An owner without iOS alert tokens has nothing for APNs to deliver to.
        if not self._alert_devices(devices):
            return
        await self.deliver(message, devices)

    async def deliver(self, message: PushMessage, devices: list[DeviceToken]) -> str:
        ios_devices = self._alert_devices(devices)
        if not ios_devices:
            raise NotificationDeliveryError(
                "The owner has no registered iOS devices", retryable=False
            )
        try:
            auth = self._auth_token()
            client = await self._get_client()
        except Exception as exc:
            raise NotificationDeliveryError(
                f"APNs auth/client setup failed: {type(exc).__name__}", retryable=False
            ) from None

        payload: dict[str, Any] = {
            "aps": {
                "alert": {"title": message.title, "body": message.body},
                "sound": "default",
                "interruption-level": "time-sensitive",
            },
            "session_id": message.session_id,
            "kind": message.kind,
            "request_id": message.request_id,
        }
        if message.notification_id:
            payload["notification_id"] = message.notification_id
        if message.url:
            payload["url"] = message.url
        payload.update(_resident_fields(message))
        if message.category:
            payload["aps"]["category"] = message.category
        payload.update(message.data)
        accepted = 0
        failures: list[NotificationDeliveryError] = []
        # An outbox notification collapses on its id: a retried delivery replaces the alert
        # it already showed, and the app can post or withdraw the same alert by that id.
        headers = {"apns-collapse-id": message.notification_id} if message.notification_id else {}
        for device in ios_devices:
            error = await self._post(client, auth, device, payload, message.owner_id, headers)
            if error is None:
                accepted += 1
                continue
            failures.append(error)
        if accepted:
            return f"APNs accepted {accepted} of {len(ios_devices)} devices"
        raise NotificationDeliveryError(
            f"APNs delivered to none of {len(ios_devices)} devices: {failures[0]}",
            retryable=any(failure.retryable for failure in failures),
            retry_after=max(
                (f.retry_after for f in failures if f.retry_after is not None), default=None
            ),
        )

    def _host_for(self, device: DeviceToken) -> str:
        """The APNs host for the device's token environment (the default when unknown)."""
        if device.environment == "sandbox":
            return self._SANDBOX_HOST
        if device.environment == "production":
            return self._PROD_HOST
        return self._host

    async def _post(
        self,
        client: httpx.AsyncClient,
        auth: str,
        device: DeviceToken,
        payload: dict[str, Any],
        owner_id: str,
        headers: dict[str, str],
    ) -> NotificationDeliveryError | None:
        topic = device.app_bundle_id or self._bundle_id
        try:
            resp = await client.post(
                f"{self._host_for(device)}/3/device/{device.token}",
                json=payload,
                headers={
                    "authorization": f"bearer {auth}",
                    "apns-topic": topic,
                    "apns-push-type": "alert",
                    "apns-priority": "10",
                    **headers,
                },
            )
        except Exception as exc:
            # One device's failure must not stop delivery to the others.
            logger.warning("APNs delivery failed for owner %s: %s", owner_id, type(exc).__name__)
            return NotificationDeliveryError(
                f"{type(exc).__name__} while contacting APNs", retryable=True
            )
        if resp.status_code >= httpx.codes.BAD_REQUEST:
            logger.warning(
                "APNs rejected push (status=%d) for owner %s", resp.status_code, owner_id
            )
            return NotificationDeliveryError(
                f"APNs returned HTTP {resp.status_code}",
                retryable=is_retryable_status(resp.status_code),
                status_code=resp.status_code,
            )
        return None


def _resident_fields(message: PushMessage) -> dict[str, str]:
    """A resident's push names the resident and its channel, so a tap can open it.

    Absent for a session's push; ``resident_session_id`` is absent for the main room.
    """
    fields: dict[str, str] = {}
    if message.resident_id:
        fields["resident_id"] = message.resident_id
        if message.resident_session_id:
            fields["resident_session_id"] = message.resident_session_id
    return fields
