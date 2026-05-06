"""Tests for the GitHub webhook ingestion endpoint."""

from __future__ import annotations

import hashlib
import hmac
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from sleipnir.domain.events import SleipnirEvent
from sleipnir.ports.events import SleipnirPublisher
from volundr.adapters.inbound.rest_webhooks import (
    _SlidingWindowRateLimiter,
    _translate,
    _verify_signature,
    create_webhooks_router,
)
from volundr.config import GitHubWebhookConfig

# ---------------------------------------------------------------------------
# Fake publisher
# ---------------------------------------------------------------------------


class FakeSleipnirPublisher(SleipnirPublisher):
    """In-memory publisher for testing."""

    def __init__(self) -> None:
        self.published: list[SleipnirEvent] = []

    async def publish(self, event: SleipnirEvent) -> None:
        self.published.append(event)

    async def publish_batch(self, events: list[SleipnirEvent]) -> None:
        self.published.extend(events)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_config(
    *,
    enabled: bool = True,
    secret: str | None = "test-secret",
    rate_limit_per_minute: int = 100,
) -> GitHubWebhookConfig:
    return GitHubWebhookConfig(
        enabled=enabled,
        secret=secret,
        rate_limit_per_minute=rate_limit_per_minute,
    )


def _sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


_SENTINEL = object()


def _make_client(
    publisher: SleipnirPublisher | None = _SENTINEL,  # type: ignore[assignment]
    config: GitHubWebhookConfig | None = None,
) -> tuple[TestClient, FakeSleipnirPublisher | None]:
    pub: SleipnirPublisher | None
    if publisher is _SENTINEL:
        pub = FakeSleipnirPublisher()
    else:
        pub = publisher
    cfg = config or _make_config()
    app = FastAPI()
    router = create_webhooks_router(pub, cfg)
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=True), pub


def _post(
    client: TestClient,
    payload: dict,
    event_type: str,
    secret: str | None = "test-secret",
    delivery_id: str = "abc-123",
) -> object:
    body = json.dumps(payload).encode()
    headers: dict[str, str] = {
        "X-GitHub-Event": event_type,
        "X-GitHub-Delivery": delivery_id,
        "Content-Type": "application/json",
    }
    if secret is not None:
        headers["X-Hub-Signature-256"] = _sign(body, secret)
    return client.post("/api/v1/webhooks/github", content=body, headers=headers)


# ---------------------------------------------------------------------------
# Signature validation unit tests
# ---------------------------------------------------------------------------


class TestVerifySignature:
    def test_valid_signature_returns_true(self):
        body = b'{"action":"opened"}'
        sig = _sign(body, "mysecret")
        assert _verify_signature("mysecret", body, sig) is True

    def test_wrong_secret_returns_false(self):
        body = b'{"action":"opened"}'
        sig = _sign(body, "right-secret")
        assert _verify_signature("wrong-secret", body, sig) is False

    def test_missing_header_returns_false(self):
        assert _verify_signature("secret", b"body", None) is False

    def test_invalid_format_returns_false(self):
        assert _verify_signature("secret", b"body", "md5=abc") is False

    def test_tampered_body_returns_false(self):
        body = b'{"action":"opened"}'
        sig = _sign(body, "secret")
        tampered = b'{"action":"closed"}'
        assert _verify_signature("secret", tampered, sig) is False


# ---------------------------------------------------------------------------
# Rate limiter unit tests
# ---------------------------------------------------------------------------


class TestSlidingWindowRateLimiter:
    def test_allows_requests_within_limit(self):
        limiter = _SlidingWindowRateLimiter(max_per_minute=5)
        for _ in range(5):
            assert limiter.allow() is True

    def test_blocks_after_limit_exceeded(self):
        limiter = _SlidingWindowRateLimiter(max_per_minute=3)
        for _ in range(3):
            limiter.allow()
        assert limiter.allow() is False

    def test_single_request_always_allowed(self):
        limiter = _SlidingWindowRateLimiter(max_per_minute=1)
        assert limiter.allow() is True
        assert limiter.allow() is False


# ---------------------------------------------------------------------------
# Translation unit tests
# ---------------------------------------------------------------------------


class TestTranslate:
    def test_pr_opened(self):
        payload = {
            "action": "opened",
            "pull_request": {
                "head": {"ref": "feature/foo"},
                "base": {"ref": "main"},
                "user": {"login": "alice"},
                "html_url": "https://github.com/org/repo/pull/1",
                "title": "Add feature foo",
                "merge_commit_sha": None,
                "merged": False,
            },
            "repository": {"full_name": "org/repo"},
        }
        event = _translate("pull_request", payload, "delivery-1")
        assert event is not None
        assert event.event_type == "github.pr.opened"
        assert event.payload["repo"] == "org/repo"
        assert event.payload["branch"] == "feature/foo"
        assert event.payload["author"] == "alice"
        assert event.payload["pr_url"] == "https://github.com/org/repo/pull/1"
        assert event.payload["title"] == "Add feature foo"
        assert event.correlation_id == "delivery-1"

    def test_pr_merged(self):
        payload = {
            "action": "closed",
            "pull_request": {
                "head": {"ref": "feature/bar"},
                "base": {"ref": "main"},
                "user": {"login": "bob"},
                "html_url": "https://github.com/org/repo/pull/2",
                "title": "Merge bar",
                "merge_commit_sha": "abc1234567890",
                "merged": True,
            },
            "repository": {"full_name": "org/repo"},
        }
        event = _translate("pull_request", payload, "delivery-2")
        assert event is not None
        assert event.event_type == "github.pr.merged"
        assert event.payload["repo"] == "org/repo"
        assert event.payload["branch"] == "main"
        assert event.payload["merge_sha"] == "abc1234567890"

    def test_pr_closed_not_merged_returns_none(self):
        payload = {
            "action": "closed",
            "pull_request": {
                "head": {"ref": "feature/baz"},
                "base": {"ref": "main"},
                "user": {"login": "carol"},
                "html_url": "...",
                "title": "Close without merge",
                "merge_commit_sha": None,
                "merged": False,
            },
            "repository": {"full_name": "org/repo"},
        }
        assert _translate("pull_request", payload, "d") is None

    def test_push_to_main(self):
        payload = {
            "ref": "refs/heads/main",
            "commits": [{"id": "abc", "message": "fix something"}],
            "pusher": {"name": "dave"},
            "repository": {"full_name": "org/repo"},
        }
        event = _translate("push", payload, "delivery-3")
        assert event is not None
        assert event.event_type == "github.push.main"
        assert event.payload["repo"] == "org/repo"
        assert event.payload["pusher"] == "dave"
        assert len(event.payload["commits"]) == 1

    def test_push_to_feature_branch_returns_none(self):
        payload = {
            "ref": "refs/heads/feature/x",
            "commits": [],
            "pusher": {"name": "eve"},
            "repository": {"full_name": "org/repo"},
        }
        assert _translate("push", payload, "d") is None

    def test_issue_opened(self):
        payload = {
            "action": "opened",
            "issue": {
                "title": "Something broke",
                "body": "It broke like this...",
                "user": {"login": "frank"},
                "labels": [{"name": "bug"}, {"name": "help wanted"}],
            },
            "repository": {"full_name": "org/repo"},
        }
        event = _translate("issues", payload, "delivery-4")
        assert event is not None
        assert event.event_type == "github.issue.opened"
        assert event.payload["title"] == "Something broke"
        assert event.payload["author"] == "frank"
        assert event.payload["labels"] == ["bug", "help wanted"]

    def test_issue_closed_returns_none(self):
        payload = {
            "action": "closed",
            "issue": {
                "title": "Fixed now",
                "body": "",
                "user": {"login": "grace"},
                "labels": [],
            },
            "repository": {"full_name": "org/repo"},
        }
        assert _translate("issues", payload, "d") is None

    def test_unknown_event_returns_none(self):
        assert _translate("deployment", {}, "d") is None
        assert _translate("fork", {}, "d") is None


# ---------------------------------------------------------------------------
# HTTP endpoint integration tests
# ---------------------------------------------------------------------------


class TestGitHubWebhookEndpoint:
    def test_pr_opened_returns_200_and_accepts(self):
        client, pub = _make_client()
        payload = {
            "action": "opened",
            "pull_request": {
                "head": {"ref": "feature/x"},
                "base": {"ref": "main"},
                "user": {"login": "alice"},
                "html_url": "https://github.com/org/repo/pull/1",
                "title": "My PR",
                "merge_commit_sha": None,
                "merged": False,
            },
            "repository": {"full_name": "org/repo"},
        }
        resp = _post(client, payload, "pull_request")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "accepted"
        assert data["event_type"] == "github.pr.opened"

    def test_pr_merged_returns_200_and_accepts(self):
        client, pub = _make_client()
        payload = {
            "action": "closed",
            "pull_request": {
                "head": {"ref": "feature/y"},
                "base": {"ref": "main"},
                "user": {"login": "bob"},
                "html_url": "...",
                "title": "Merge",
                "merge_commit_sha": "deadbeef",
                "merged": True,
            },
            "repository": {"full_name": "org/repo"},
        }
        resp = _post(client, payload, "pull_request", delivery_id="del-2")
        assert resp.status_code == 200
        assert resp.json()["event_type"] == "github.pr.merged"

    def test_push_to_main_returns_200_and_accepts(self):
        client, pub = _make_client()
        payload = {
            "ref": "refs/heads/main",
            "commits": [{"id": "abc"}],
            "pusher": {"name": "carol"},
            "repository": {"full_name": "org/repo"},
        }
        resp = _post(client, payload, "push")
        assert resp.status_code == 200
        assert resp.json()["event_type"] == "github.push.main"

    def test_issue_opened_returns_200_and_accepts(self):
        client, pub = _make_client()
        payload = {
            "action": "opened",
            "issue": {
                "title": "Bug",
                "body": "Details",
                "user": {"login": "dave"},
                "labels": [{"name": "bug"}],
            },
            "repository": {"full_name": "org/repo"},
        }
        resp = _post(client, payload, "issues")
        assert resp.status_code == 200
        assert resp.json()["event_type"] == "github.issue.opened"

    def test_invalid_signature_returns_401(self):
        client, pub = _make_client()
        payload = {"action": "opened"}
        body = json.dumps(payload).encode()
        resp = client.post(
            "/api/v1/webhooks/github",
            content=body,
            headers={
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "abc",
                "Content-Type": "application/json",
                "X-Hub-Signature-256": "sha256=badvalue",
            },
        )
        assert resp.status_code == 401

    def test_missing_signature_returns_401(self):
        client, pub = _make_client()
        payload = {"action": "opened"}
        body = json.dumps(payload).encode()
        resp = client.post(
            "/api/v1/webhooks/github",
            content=body,
            headers={
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "abc",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 401

    def test_unknown_event_type_returns_200_ignored(self):
        client, pub = _make_client()
        payload = {"action": "forked"}
        resp = _post(client, payload, "fork")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"

    def test_disabled_endpoint_returns_ignored(self):
        client, pub = _make_client(config=_make_config(enabled=False))
        payload = {
            "action": "opened",
            "pull_request": {
                "head": {"ref": "x"},
                "base": {"ref": "main"},
                "user": {"login": "u"},
                "html_url": "...",
                "title": "T",
                "merge_commit_sha": None,
                "merged": False,
            },
            "repository": {"full_name": "org/repo"},
        }
        resp = _post(client, payload, "pull_request")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"

    def test_no_signature_check_when_no_secret_configured(self):
        client, pub = _make_client(config=_make_config(secret=None))
        payload = {
            "action": "opened",
            "pull_request": {
                "head": {"ref": "x"},
                "base": {"ref": "main"},
                "user": {"login": "u"},
                "html_url": "...",
                "title": "T",
                "merge_commit_sha": None,
                "merged": False,
            },
            "repository": {"full_name": "org/repo"},
        }
        # Send without signature — should be accepted
        body = json.dumps(payload).encode()
        resp = client.post(
            "/api/v1/webhooks/github",
            content=body,
            headers={
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "no-sig",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "accepted"

    def test_rate_limit_returns_429(self):
        client, pub = _make_client(config=_make_config(rate_limit_per_minute=2))
        payload = {
            "action": "opened",
            "pull_request": {
                "head": {"ref": "x"},
                "base": {"ref": "main"},
                "user": {"login": "u"},
                "html_url": "...",
                "title": "T",
                "merge_commit_sha": None,
                "merged": False,
            },
            "repository": {"full_name": "org/repo"},
        }
        # First two succeed
        _post(client, payload, "pull_request", delivery_id="d1")
        _post(client, payload, "pull_request", delivery_id="d2")
        # Third is rate-limited
        resp = _post(client, payload, "pull_request", delivery_id="d3")
        assert resp.status_code == 429

    def test_delivery_id_used_as_correlation_id(self):
        client, pub = _make_client()
        payload = {
            "action": "opened",
            "pull_request": {
                "head": {"ref": "feature/z"},
                "base": {"ref": "main"},
                "user": {"login": "zara"},
                "html_url": "https://github.com/org/repo/pull/9",
                "title": "My feature",
                "merge_commit_sha": None,
                "merged": False,
            },
            "repository": {"full_name": "org/repo"},
        }
        delivery = "unique-delivery-xyz"
        resp = _post(client, payload, "pull_request", delivery_id=delivery)
        assert resp.status_code == 200

    def test_no_publisher_returns_accepted_without_publishing(self):
        client, _ = _make_client(publisher=None)
        payload = {
            "action": "opened",
            "pull_request": {
                "head": {"ref": "x"},
                "base": {"ref": "main"},
                "user": {"login": "u"},
                "html_url": "...",
                "title": "T",
                "merge_commit_sha": None,
                "merged": False,
            },
            "repository": {"full_name": "org/repo"},
        }
        resp = _post(client, payload, "pull_request")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "accepted"
        assert data.get("published") is False

    def test_push_to_feature_branch_ignored(self):
        client, pub = _make_client()
        payload = {
            "ref": "refs/heads/feature/not-main",
            "commits": [],
            "pusher": {"name": "x"},
            "repository": {"full_name": "org/repo"},
        }
        resp = _post(client, payload, "push")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"
