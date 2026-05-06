"""REST adapter for GitHub webhook ingestion.

Receives GitHub webhook payloads, validates HMAC-SHA256 signatures, translates
to Sleipnir catalog events, and publishes them asynchronously.

Supported events:
- pull_request (opened)  → github.pr.opened
- pull_request (merged)  → github.pr.merged
- push (to main)         → github.push.main
- issues (opened)        → github.issue.opened

Unknown event types are silently accepted (200 OK, no publish).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from collections import deque

from fastapi import APIRouter, Header, HTTPException, Request, status

from sleipnir.domain import catalog
from sleipnir.ports.events import SleipnirPublisher
from volundr.config import GitHubWebhookConfig

logger = logging.getLogger(__name__)

_GITHUB_SOURCE = "volundr:github-webhook"

# Strong references to background tasks prevent premature GC.
_background_tasks: set[asyncio.Task] = set()


def _sanitize_log(value: object) -> str:
    """Sanitize a value for safe log output (prevent log injection)."""
    return str(value).replace("\n", "\\n").replace("\r", "\\r")


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------


class _SlidingWindowRateLimiter:
    """Simple in-memory sliding window rate limiter (global, not per-IP).

    Tracks event timestamps in a deque and rejects when the count in the
    last 60 seconds exceeds the configured limit.
    """

    def __init__(self, max_per_minute: int) -> None:
        self._max = max_per_minute
        self._window_seconds = 60
        self._timestamps: deque[float] = deque()

    def allow(self) -> bool:
        now = time.monotonic()
        cutoff = now - self._window_seconds
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()
        if len(self._timestamps) >= self._max:
            return False
        self._timestamps.append(now)
        return True


# ---------------------------------------------------------------------------
# Signature validation
# ---------------------------------------------------------------------------


def _verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    """Return True if the HMAC-SHA256 signature is valid.

    GitHub sends the signature as ``sha256=<hex>`` in the
    ``X-Hub-Signature-256`` header.
    """
    if not signature_header:
        return False
    if not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(
        secret.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    received = signature_header[len("sha256=") :]
    return hmac.compare_digest(expected, received)


# ---------------------------------------------------------------------------
# Payload translation
# ---------------------------------------------------------------------------


def _translate_pull_request(
    payload: dict,
    delivery_id: str,
) -> catalog.SleipnirEvent | None:
    """Translate a pull_request webhook payload to a Sleipnir event."""
    action = payload.get("action", "")
    pr = payload.get("pull_request", {})
    repo = payload.get("repository", {}).get("full_name", "")

    if action == "opened":
        return catalog.github_pr_opened(
            repo=repo,
            branch=pr.get("head", {}).get("ref", ""),
            author=pr.get("user", {}).get("login", ""),
            pr_url=pr.get("html_url", ""),
            title=pr.get("title", ""),
            source=_GITHUB_SOURCE,
            correlation_id=delivery_id,
        )

    if action == "closed" and pr.get("merged"):
        return catalog.github_pr_merged(
            repo=repo,
            branch=pr.get("base", {}).get("ref", ""),
            merge_sha=pr.get("merge_commit_sha", ""),
            source=_GITHUB_SOURCE,
            correlation_id=delivery_id,
        )

    return None


def _translate_push(
    payload: dict,
    delivery_id: str,
) -> catalog.SleipnirEvent | None:
    """Translate a push webhook payload to a Sleipnir event (main branch only)."""
    ref = payload.get("ref", "")
    if ref not in ("refs/heads/main", "refs/heads/master"):
        return None
    repo = payload.get("repository", {}).get("full_name", "")
    pusher = payload.get("pusher", {}).get("name", "")
    commits = payload.get("commits", [])
    return catalog.github_push_main(
        repo=repo,
        commits=commits,
        pusher=pusher,
        source=_GITHUB_SOURCE,
        correlation_id=delivery_id,
    )


def _translate_issues(
    payload: dict,
    delivery_id: str,
) -> catalog.SleipnirEvent | None:
    """Translate an issues webhook payload to a Sleipnir event."""
    action = payload.get("action", "")
    if action != "opened":
        return None
    issue = payload.get("issue", {})
    repo = payload.get("repository", {}).get("full_name", "")
    labels = [lbl.get("name", "") for lbl in issue.get("labels", [])]
    return catalog.github_issue_opened(
        repo=repo,
        title=issue.get("title", ""),
        body=issue.get("body", "") or "",
        labels=labels,
        author=issue.get("user", {}).get("login", ""),
        source=_GITHUB_SOURCE,
        correlation_id=delivery_id,
    )


def _translate(
    event_type: str,
    payload: dict,
    delivery_id: str,
) -> catalog.SleipnirEvent | None:
    """Dispatch to the right translator; return None for unrecognised types."""
    match event_type:
        case "pull_request":
            return _translate_pull_request(payload, delivery_id)
        case "push":
            return _translate_push(payload, delivery_id)
        case "issues":
            return _translate_issues(payload, delivery_id)
        case _:
            return None


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_webhooks_router(
    publisher: SleipnirPublisher | None,
    config: GitHubWebhookConfig,
) -> APIRouter:
    """Create FastAPI router for webhook ingestion endpoints."""
    router = APIRouter(prefix="/api/v1/webhooks")
    rate_limiter = _SlidingWindowRateLimiter(config.rate_limit_per_minute)

    @router.post(
        "/github",
        status_code=status.HTTP_200_OK,
        tags=["Webhooks"],
        summary="Receive GitHub webhook events",
    )
    async def receive_github_webhook(
        request: Request,
        x_hub_signature_256: str | None = Header(default=None),
        x_github_event: str | None = Header(default=None),
        x_github_delivery: str | None = Header(default=None),
    ) -> dict:
        """Ingest a GitHub webhook payload and publish as a Sleipnir event.

        - Validates ``X-Hub-Signature-256`` (returns 401 on failure).
        - Rate-limited to ``webhooks.github.rate_limit_per_minute`` (returns 429).
        - Unknown event types return 200 OK without publishing.
        - Publishes asynchronously; response is returned within 5 s.
        """
        body = await request.body()

        if not config.enabled:
            return {"status": "ignored", "reason": "webhook ingestion disabled"}

        if config.secret:
            if not _verify_signature(config.secret, body, x_hub_signature_256):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid webhook signature",
                )

        if not rate_limiter.allow():
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded",
            )

        try:
            payload = json.loads(body)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid JSON payload",
            )

        event_type = (x_github_event or "").strip().lower()
        delivery_id = (x_github_delivery or "").strip()

        event = _translate(event_type, payload, delivery_id)
        if event is None:
            logger.debug(
                "GitHub webhook: no event published for type=%r delivery=%s",
                _sanitize_log(event_type),
                _sanitize_log(delivery_id),
            )
            return {"status": "ignored", "event_type": event_type}

        if publisher is None:
            logger.warning(
                "GitHub webhook received but Sleipnir publisher is not configured; "
                "event %s will not be published",
                event.event_type,
            )
            return {"status": "accepted", "event_type": event.event_type, "published": False}

        task = asyncio.create_task(_publish(publisher, event, delivery_id))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

        logger.info(
            "GitHub webhook: queued %s delivery=%s",
            _sanitize_log(event.event_type),
            _sanitize_log(delivery_id),
        )
        return {"status": "accepted", "event_type": event.event_type}

    return router


async def _publish(
    publisher: SleipnirPublisher,
    event: catalog.SleipnirEvent,
    delivery_id: str,
) -> None:
    """Publish event asynchronously; failures are logged, not re-raised."""
    try:
        await publisher.publish(event)
    except Exception:
        logger.exception(
            "Failed to publish GitHub webhook event %s (delivery=%s)",
            _sanitize_log(event.event_type),
            _sanitize_log(delivery_id),
        )
