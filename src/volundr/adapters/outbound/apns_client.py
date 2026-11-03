"""APNs over HTTP/2 with token-based (JWT) authentication.

One client serves every push type (alert, Live Activity, widgets) and both APNs
hosts: each push names the environment its token was issued in, because a
development install's token is only valid on the sandbox host and a TestFlight or
App Store install's only on production.

The signing key is read from ``private_key_path`` (the ``AuthKey_<KEYID>.p8`` file
from the Apple Developer portal) when the client is built. A missing, unreadable or
wrong key is a configuration error raised there, never a degraded client. The key
and the provider JWT are never logged.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from pathlib import Path

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from volundr.domain.push import (
    APNS_ACCEPTED_STATUS,
    APNS_EXPIRED_PROVIDER_TOKEN,
    ApnsPush,
    ApnsResult,
    PushEnvironment,
)
from volundr.domain.push_ports import ApplePushClient

logger = logging.getLogger(__name__)

#: Apple's signing requirement for provider tokens.
_JWT_ALGORITHM = "ES256"
_KEY_CURVE = "secp256r1"


class ApnsConfigurationError(ValueError):
    """The APNs settings cannot work; the message says what to fix."""


def load_signing_key(private_key_path: str) -> str:
    """Read and check the ``.p8`` signing key; returns its PEM text.

    Raises :class:`ApnsConfigurationError` naming the path (never the contents).
    """
    if not private_key_path:
        raise ApnsConfigurationError(
            "push.apns.private_key_path is empty: set it to the AuthKey_<KEYID>.p8 file"
        )
    path = Path(private_key_path).expanduser()
    try:
        pem = path.read_text(encoding="ascii")
    except OSError as exc:
        raise ApnsConfigurationError(
            f"The APNs signing key {path} cannot be read ({type(exc).__name__}): "
            "fix push.apns.private_key_path or the file's permissions"
        ) from None
    except UnicodeError:
        raise ApnsConfigurationError(
            f"The APNs signing key {path} is not a PEM (.p8) file"
        ) from None
    check_signing_key(pem, source=str(path))
    return pem


def check_signing_key(pem: str, *, source: str) -> None:
    """Raise unless ``pem`` is an EC P-256 private key, which ES256 needs."""
    try:
        key = load_pem_private_key(pem.encode("ascii"), password=None)
    except (ValueError, TypeError, UnicodeError):
        raise ApnsConfigurationError(
            f"The APNs signing key from {source} is not an unencrypted PEM private key"
        ) from None
    if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != _KEY_CURVE:
        raise ApnsConfigurationError(
            f"The APNs signing key from {source} is not an EC P-256 key (APNs signs with ES256)"
        )


class ApnsClient(ApplePushClient):
    """Sends pushes to APNs; one HTTP/2 connection per APNs host, reused."""

    def __init__(
        self,
        *,
        team_id: str,
        key_id: str,
        private_key: str,
        production_url: str,
        sandbox_url: str,
        token_ttl_seconds: float,
        request_timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not team_id or not key_id:
            raise ApnsConfigurationError("APNs needs push.apns.team_id and push.apns.key_id")
        if not production_url or not sandbox_url:
            raise ApnsConfigurationError(
                "APNs needs push.apns.production_url and push.apns.sandbox_url"
            )
        check_signing_key(private_key, source="the configured key")
        self._team_id = team_id
        self._key_id = key_id
        self._private_key = private_key
        self._hosts = {
            PushEnvironment.PRODUCTION: production_url.rstrip("/"),
            PushEnvironment.SANDBOX: sandbox_url.rstrip("/"),
        }
        self._token_ttl = token_ttl_seconds
        self._clock = clock
        self._owns_client = client is None
        # HTTP/2 is mandatory for APNs; httpx raises here when ``h2`` is missing.
        self._client = client or httpx.AsyncClient(http2=True, timeout=request_timeout_seconds)
        self._jwt: str | None = None
        self._jwt_issued_at = 0.0

    def _provider_token(self, *, fresh: bool = False) -> str:
        """The cached provider JWT, re-signed once it is older than the TTL."""
        now = self._clock()
        if not fresh and self._jwt is not None and now - self._jwt_issued_at < self._token_ttl:
            return self._jwt
        self._jwt = jwt.encode(
            {"iss": self._team_id, "iat": int(now)},
            self._private_key,
            algorithm=_JWT_ALGORITHM,
            headers={"kid": self._key_id},
        )
        self._jwt_issued_at = now
        return self._jwt

    @staticmethod
    def _headers(push: ApnsPush, provider_token: str) -> dict[str, str]:
        headers = {
            "authorization": f"bearer {provider_token}",
            "apns-topic": push.topic,
            "apns-push-type": push.push_type.value,
            "apns-priority": str(push.priority),
            "content-type": "application/json",
        }
        if push.collapse_id:
            headers["apns-collapse-id"] = push.collapse_id
        if push.expiration is not None:
            headers["apns-expiration"] = str(push.expiration)
        return headers

    async def send(self, push: ApnsPush) -> ApnsResult:
        body = push.body()
        result = await self._post(push, body, self._provider_token())
        if result.reason != APNS_EXPIRED_PROVIDER_TOKEN:
            return result
        # APNs judged the cached JWT too old (clock skew, a long idle): sign anew, once.
        return await self._post(push, body, self._provider_token(fresh=True))

    async def _post(self, push: ApnsPush, body: bytes, provider_token: str) -> ApnsResult:
        url = f"{self._hosts[push.environment]}/3/device/{push.token}"
        try:
            response = await self._client.post(
                url, content=body, headers=self._headers(push, provider_token)
            )
        except httpx.HTTPError as exc:
            return ApnsResult(status_code=None, reason=type(exc).__name__)
        if response.status_code == APNS_ACCEPTED_STATUS:
            return ApnsResult(
                status_code=response.status_code, apns_id=response.headers.get("apns-id", "")
            )
        return ApnsResult(
            status_code=response.status_code,
            reason=_reason(response),
            retry_after=_retry_after(response),
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _reason(response: httpx.Response) -> str:
    """APNs' ``reason`` string (``BadDeviceToken``, ``Unregistered`` ...), if any."""
    try:
        payload = json.loads(response.content or b"{}")
    except ValueError:
        return ""
    reason = payload.get("reason") if isinstance(payload, dict) else None
    return reason if isinstance(reason, str) else ""


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None
