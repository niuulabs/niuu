"""Outbound NotificationChannel adapters for session attention pushes.

Selected via the dynamic-adapter config (``push.adapter`` + ``kwargs``):

- ``LoggingNotificationChannel`` — default; logs only, no external calls.
- ``WebhookNotificationChannel`` — POSTs the push to a relay URL (the relay
  owns device fan-out). HTTP/1.1, no extra dependencies.
- ``ApnsNotificationChannel`` — direct Apple Push, token-based (JWT) auth.
  Requires an HTTP/2 client (``h2`` installed); inject one for tests.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time

import httpx

from volundr.domain.models import DevicePlatform, DeviceToken, PushMessage
from volundr.domain.ports import NotificationChannel

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
        if not self._url:
            return
        payload = {
            "type": "session.needs_input",
            "owner_id": message.owner_id,
            "session_id": message.session_id,
            "kind": message.kind,
            "title": message.title,
            "body": message.body,
            "urgency": message.urgency,
            "request_id": message.request_id,
            "device_tokens": [{"platform": d.platform.value, "token": d.token} for d in devices],
        }
        headers = {"content-type": "application/json"}
        if self._secret:
            body = httpx.Request("POST", self._url, json=payload).content
            signature = hmac.new(self._secret.encode(), body, hashlib.sha256).hexdigest()
            headers["X-Niuu-Signature"] = f"sha256={signature}"
        try:
            client = await self._get_client()
            resp = await client.post(self._url, json=payload, headers=headers)
            if resp.status_code >= 400:
                logger.warning(
                    "Push webhook returned %d for owner %s", resp.status_code, message.owner_id
                )
        except Exception:
            logger.warning(
                "Push webhook delivery failed for owner %s", message.owner_id, exc_info=True
            )


class ApnsNotificationChannel(NotificationChannel):
    """Direct Apple Push Notification service channel (token-based auth).

    Signs a short-lived ES256 JWT (PyJWT + cryptography) with the .p8 key and
    delivers one request per iOS device over HTTP/2. Pushes use the
    ``time-sensitive`` interruption level so the alert and Live Activity / widget
    surface even under Focus.
    """

    _PROD_HOST = "https://api.push.apple.com"
    _SANDBOX_HOST = "https://api.sandbox.push.apple.com"

    def __init__(
        self,
        *,
        team_id: str,
        key_id: str,
        private_key: str,
        bundle_id: str,
        use_sandbox: bool = False,
        token_ttl_seconds: int = 3000,
        client: httpx.AsyncClient | None = None,
    ):
        self._team_id = team_id
        self._key_id = key_id
        self._private_key = private_key
        self._bundle_id = bundle_id
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

    async def send(self, message: PushMessage, devices: list[DeviceToken]) -> None:
        ios_devices = [d for d in devices if d.platform is DevicePlatform.IOS]
        if not ios_devices:
            return
        try:
            auth = self._auth_token()
            client = await self._get_client()
        except Exception:
            logger.warning(
                "APNs auth/client setup failed for owner %s", message.owner_id, exc_info=True
            )
            return

        payload = {
            "aps": {
                "alert": {"title": message.title, "body": message.body},
                "sound": "default",
                "interruption-level": "time-sensitive",
            },
            "session_id": message.session_id,
            "kind": message.kind,
            "request_id": message.request_id,
        }
        for device in ios_devices:
            topic = device.app_bundle_id or self._bundle_id
            try:
                resp = await client.post(
                    f"{self._host}/3/device/{device.token}",
                    json=payload,
                    headers={
                        "authorization": f"bearer {auth}",
                        "apns-topic": topic,
                        "apns-push-type": "alert",
                        "apns-priority": "10",
                    },
                )
                if resp.status_code >= 400:
                    logger.warning(
                        "APNs rejected push (status=%d) for owner %s",
                        resp.status_code,
                        message.owner_id,
                    )
            except Exception:
                logger.warning("APNs delivery failed for owner %s", message.owner_id, exc_info=True)
