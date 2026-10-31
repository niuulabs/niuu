"""HTTP helpers shared by the notification sinks: signing, error mapping, redaction."""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx

from niuu.ports.notifications import NotificationDeliveryError

SIGNATURE_HEADER = "X-Niuu-Signature"
DEFAULT_TIMEOUT_SECONDS = 10.0

# Statuses that describe a transient condition on the receiver's side: a timeout,
# "too early", a rate limit, or any server error. Every other 4xx is permanent.
_RETRYABLE_CLIENT_STATUSES = frozenset({408, 425, 429})
_FIRST_SERVER_ERROR = 500


def sign_body(secret: str, body: bytes) -> str:
    """The ``X-Niuu-Signature`` value for ``body``: ``sha256=<hex HMAC>``."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def is_retryable_status(status_code: int) -> bool:
    return status_code in _RETRYABLE_CLIENT_STATUSES or status_code >= _FIRST_SERVER_ERROR


def retry_after_seconds(response: httpx.Response) -> float | None:
    """Parse ``Retry-After`` (delta-seconds or an HTTP date); ``None`` if absent."""
    raw = response.headers.get("retry-after", "")
    if not isinstance(raw, str) or not raw.strip():
        return None
    raw = raw.strip()
    try:
        return max(float(raw), 0.0)
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max((when - datetime.now(UTC)).total_seconds(), 0.0)


def redact_url(url: str) -> str:
    """Scheme and host only: webhook paths and queries often embed tokens."""
    parts = urlsplit(url)
    if not parts.scheme or not parts.hostname:
        return "<invalid url>"
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{parts.hostname}{port}"


def transport_failure(exc: httpx.HTTPError, target: str) -> NotificationDeliveryError:
    """A retryable error for a network-level failure, without the request URL.

    ``str(exc)`` from httpx can include the full URL (a Telegram bot token, a
    webhook secret path), so only the exception type is reported.
    """
    return NotificationDeliveryError(
        f"{type(exc).__name__} while contacting {target}", retryable=True
    )


def status_failure(
    target: str,
    response: httpx.Response,
    *,
    description: str = "",
    retry_after: float | None = None,
) -> NotificationDeliveryError:
    """Map a non-success HTTP response to a retryable or permanent error."""
    status = response.status_code
    detail = f": {description}" if description else ""
    return NotificationDeliveryError(
        f"{target} returned HTTP {status}{detail}",
        retryable=is_retryable_status(status),
        retry_after=retry_after if retry_after is not None else retry_after_seconds(response),
        status_code=status,
    )
