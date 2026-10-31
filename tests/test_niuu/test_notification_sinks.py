"""The shared notification sinks: Telegram and webhook rendering, errors and signing."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from niuu.adapters.notifications.delivery_http import (
    is_retryable_status,
    redact_url,
    retry_after_seconds,
    sign_body,
)
from niuu.adapters.notifications.telegram import (
    MAX_TELEGRAM_TEXT_UNITS,
    TelegramNotificationSink,
    format_telegram_html,
)
from niuu.adapters.notifications.webhook import WebhookNotificationSink
from niuu.domain.notifications import (
    NotificationKind,
    NotificationSeverity,
    NotificationSource,
)
from niuu.ports.notifications import (
    DeliveryResult,
    NotificationDeliveryError,
    NotificationSink,
    OutboundLink,
    OutboundNotification,
)

CREATED = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
BOT_TOKEN = "123456:SECRET-bot-token"


def message(**overrides) -> OutboundNotification:
    values = {
        "notification_id": "0b8f0c5e-1111-4222-8333-944445555666",
        "title": "Deploy <prod> & verify",
        "kind": NotificationKind.DECISION,
        "severity": NotificationSeverity.WARNING,
        "source": NotificationSource.AGENT,
        "created_at": CREATED,
        "body": "Pick one: a < b && c > d",
        "owner_id": "user-1",
        "session_id": "5f5f5f5f-0000-4000-8000-000000000001",
        "session_label": "fix-auth <main>",
        "host_label": "thor",
        "url": "https://forge.example.com/volundr/session/s#notification-n",
        "links": (
            OutboundLink(label="PR #7 <draft>", url="https://github.com/o/r/pull/7?a=1&b=2"),
            OutboundLink(label="Evil", url="javascript:alert(1)"),
            OutboundLink(label="No url"),
        ),
        "delivery_id": "d-1",
        "attempt": 2,
    }
    values.update(overrides)
    return OutboundNotification(**values)


def telegram(handler, **kwargs) -> TelegramNotificationSink:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return TelegramNotificationSink(bot_token=BOT_TOKEN, chat_id="-100200", client=client, **kwargs)


def webhook(handler, **kwargs) -> WebhookNotificationSink:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    kwargs.setdefault("url", "https://hooks.example.com/forge/T0KEN-PATH?sig=abc")
    return WebhookNotificationSink(client=client, **kwargs)


class TestOutboundNotification:
    def test_payload_is_complete_json(self):
        payload = message().to_payload()
        assert json.loads(json.dumps(payload)) == payload
        assert payload["kind"] == "decision"
        assert payload["severity"] == "warning"
        assert payload["source"] == "agent"
        assert payload["session_name"] == "fix-auth <main>"
        assert payload["host"] == "thor"
        assert payload["created_at"] == CREATED.isoformat()
        assert payload["links"][0] == {
            "label": "PR #7 <draft>",
            "url": "https://github.com/o/r/pull/7?a=1&b=2",
            "kind": "url",
        }

    async def test_base_sink_defaults(self):
        class Minimal(NotificationSink):
            name = "minimal"

            async def send(self, message: OutboundNotification) -> DeliveryResult:
                return DeliveryResult()

        sink = Minimal()
        assert sink.skip_reason(message()) is None
        assert await sink.close() is None


class TestTelegramFormatting:
    def test_layout_escapes_every_user_string(self):
        text = format_telegram_html(message())
        lines = text.splitlines()
        assert lines[0] == "⚠️ <b>Deploy &lt;prod&gt; &amp; verify</b>"
        assert lines[1] == "<i>Warning · decision · fix-auth &lt;main&gt; · thor</i>"
        assert "Pick one: a &lt; b &amp;&amp; c &gt; d" in text
        assert (
            '• <a href="https://github.com/o/r/pull/7?a=1&amp;b=2">PR #7 &lt;draft&gt;</a>' in text
        )
        assert "javascript:" not in text
        assert "No url" not in text
        assert text.endswith(
            '<a href="https://forge.example.com/volundr/session/s#notification-n">'
            "Open session in Forge</a>"
        )

    def test_quotes_in_urls_cannot_break_out_of_the_attribute(self):
        text = format_telegram_html(
            message(url='https://forge.example.com/x"><b>pwn', links=(), session_id=None)
        )
        assert 'href="https://forge.example.com/x&quot;&gt;&lt;b&gt;pwn"' in text
        assert "Open in Forge" in text

    @pytest.mark.parametrize(
        ("severity", "emoji"),
        [
            (NotificationSeverity.INFO, "ℹ️"),
            (NotificationSeverity.SUCCESS, "✅"),
            (NotificationSeverity.CRITICAL, "\U0001f6a8"),
        ],
    )
    def test_severity_emoji(self, severity, emoji):
        assert format_telegram_html(message(severity=severity)).startswith(emoji)

    def test_minimal_message_has_no_empty_sections(self):
        text = format_telegram_html(
            message(body="  ", links=(), url=None, session_label=None, host_label=None)
        )
        assert text == "⚠️ <b>Deploy &lt;prod&gt; &amp; verify</b>\n<i>Warning · decision</i>"

    def test_long_body_is_cut_to_the_bot_api_limit(self):
        body = "é&<" * 5000 + "😀" * 3000
        text = format_telegram_html(message(body=body))
        visible = format_telegram_html.__globals__["_visible_units"](text)
        assert visible <= MAX_TELEGRAM_TEXT_UNITS
        assert "…" in text
        assert "Open session in Forge" in text  # the link survives truncation

    def test_body_is_dropped_when_no_room_is_left(self):
        text = format_telegram_html(message(body="x" * 50), max_units=40)
        assert "xxx" not in text


class TestTelegramSink:
    async def test_success_posts_html_and_returns_message_id(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["json"] = json.loads(request.content)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

        sink = telegram(handler, message_thread_id="7")
        result = await sink.send(message())
        await sink.close()

        assert result.provider_message_id == "42"
        assert seen["url"] == f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        assert seen["json"]["parse_mode"] == "HTML"
        assert seen["json"]["chat_id"] == "-100200"
        assert seen["json"]["message_thread_id"] == 7
        assert seen["json"]["disable_web_page_preview"] is True
        assert seen["json"]["text"] == format_telegram_html(message())
        assert sink.name == "telegram"

    async def test_success_without_json_is_still_delivered(self):
        sink = telegram(lambda request: httpx.Response(200, text="ok"))
        assert (await sink.send(message())).provider_message_id is None

    async def test_rate_limit_is_retryable_with_retry_after(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429,
                json={
                    "ok": False,
                    "error_code": 429,
                    "description": "Too Many Requests: retry after 17",
                    "parameters": {"retry_after": 17},
                },
            )

        with pytest.raises(NotificationDeliveryError) as info:
            await telegram(handler).send(message())
        assert info.value.retryable
        assert info.value.retry_after == 17
        assert info.value.status_code == 429
        assert "Too Many Requests" in str(info.value)

    async def test_server_error_is_retryable(self):
        sink = telegram(lambda request: httpx.Response(502, text="bad gateway"))
        with pytest.raises(NotificationDeliveryError) as info:
            await sink.send(message())
        assert info.value.retryable and info.value.status_code == 502

    @pytest.mark.parametrize("status", [400, 401, 403, 404])
    async def test_client_errors_are_permanent(self, status):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                status, json={"ok": False, "description": "Bad Request: chat not found"}
            )

        with pytest.raises(NotificationDeliveryError) as info:
            await telegram(handler).send(message())
        assert not info.value.retryable
        assert BOT_TOKEN not in str(info.value)

    async def test_ok_false_on_200_is_permanent(self):
        sink = telegram(lambda request: httpx.Response(200, json={"ok": False, "description": "x"}))
        with pytest.raises(NotificationDeliveryError) as info:
            await sink.send(message())
        assert not info.value.retryable

    async def test_network_error_is_retryable_and_never_leaks_the_token(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout(f"timed out calling {request.url}", request=request)

        with pytest.raises(NotificationDeliveryError) as info:
            await telegram(handler).send(message())
        assert info.value.retryable
        assert BOT_TOKEN not in str(info.value)
        assert "ConnectTimeout" in str(info.value)

    @pytest.mark.parametrize("kwargs", [{"bot_token": ""}, {"chat_id": ""}])
    async def test_missing_credentials_are_permanent(self, kwargs):
        values = {"bot_token": BOT_TOKEN, "chat_id": "1", **kwargs}
        sink = TelegramNotificationSink(**values, client=httpx.AsyncClient())
        with pytest.raises(NotificationDeliveryError) as info:
            await sink.send(message())
        assert not info.value.retryable
        await sink.close()

    def test_integration_config_extras_are_ignored(self):
        sink = TelegramNotificationSink(
            bot_token="t", chat_id=5, min_urgency="high", notify_only=True, timeout=3.5
        )
        assert sink._chat_id == "5"  # noqa: SLF001
        assert sink._client.timeout.read == 3.5  # noqa: SLF001


class TestWebhookSink:
    async def test_posts_the_full_notification_signed(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = request.content
            seen["headers"] = request.headers
            return httpx.Response(204, headers={"X-Request-Id": "rq-9"})

        sink = webhook(handler, secret="s3cret", headers={"X-Team": "ops"}, name="ops-webhook")
        result = await sink.send(message())
        await sink.close()

        body = json.loads(seen["body"])
        assert body["type"] == "forge.notification"
        assert body["version"] == 1
        assert body["delivery_id"] == "d-1"
        assert body["attempt"] == 2
        assert body["notification"] == message().to_payload()
        expected = hmac.new(b"s3cret", seen["body"], hashlib.sha256).hexdigest()
        assert seen["headers"]["x-niuu-signature"] == f"sha256={expected}"
        assert seen["headers"]["x-niuu-event"] == "forge.notification"
        assert seen["headers"]["x-niuu-delivery"] == "d-1"
        assert seen["headers"]["x-team"] == "ops"
        assert seen["headers"]["content-type"] == "application/json"
        assert result.provider_message_id == "rq-9"
        assert sink.name == "ops-webhook"

    async def test_unsigned_without_secret(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["headers"] = request.headers
            return httpx.Response(200)

        await webhook(handler).send(message(delivery_id=None))
        assert "x-niuu-signature" not in seen["headers"]
        assert "x-niuu-delivery" not in seen["headers"]

    @pytest.mark.parametrize("status", [408, 425, 429, 500, 503])
    async def test_transient_statuses_are_retryable(self, status):
        sink = webhook(lambda request: httpx.Response(status, headers={"Retry-After": "30"}))
        with pytest.raises(NotificationDeliveryError) as info:
            await sink.send(message())
        assert info.value.retryable
        assert info.value.retry_after == 30

    @pytest.mark.parametrize("status", [301, 400, 401, 404, 410])
    async def test_other_statuses_are_permanent(self, status):
        sink = webhook(lambda request: httpx.Response(status))
        with pytest.raises(NotificationDeliveryError) as info:
            await sink.send(message())
        assert not info.value.retryable
        # Only the scheme and host survive redaction: no token path, no query.
        assert str(info.value) == f"webhook https://hooks.example.com returned HTTP {status}"

    async def test_network_errors_are_retryable_and_redacted(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(f"cannot reach {request.url}", request=request)

        with pytest.raises(NotificationDeliveryError) as info:
            await webhook(handler).send(message())
        assert info.value.retryable
        assert "T0KEN-PATH" not in str(info.value)

    async def test_missing_url_is_permanent(self):
        sink = WebhookNotificationSink(url="", client=httpx.AsyncClient())
        with pytest.raises(NotificationDeliveryError) as info:
            await sink.send(message())
        assert not info.value.retryable
        await sink.close()

    def test_timeout_comes_from_kwargs(self):
        sink = WebhookNotificationSink(url="https://x.example", timeout=2.5, min_urgency="low")
        assert sink._client.timeout.connect == 2.5  # noqa: SLF001


class TestHttpHelpers:
    def test_signature_matches_hmac_sha256(self):
        assert sign_body("k", b"{}") == "sha256=" + hmac.new(b"k", b"{}", "sha256").hexdigest()

    @pytest.mark.parametrize(
        ("status", "retryable"), [(200, False), (404, False), (408, True), (429, True), (500, True)]
    )
    def test_retryable_statuses(self, status, retryable):
        assert is_retryable_status(status) is retryable

    def test_retry_after_parsing(self):
        assert retry_after_seconds(httpx.Response(429)) is None
        assert retry_after_seconds(httpx.Response(429, headers={"Retry-After": "5"})) == 5
        assert retry_after_seconds(httpx.Response(429, headers={"Retry-After": "-3"})) == 0
        assert retry_after_seconds(httpx.Response(429, headers={"Retry-After": "soon"})) is None
        later = datetime.now(UTC) + timedelta(seconds=120)
        header = format_datetime(later, usegmt=True)
        parsed = retry_after_seconds(httpx.Response(503, headers={"Retry-After": header}))
        assert parsed is not None and 100 < parsed <= 120
        naive = format_datetime(later.replace(tzinfo=None))
        assert retry_after_seconds(httpx.Response(503, headers={"Retry-After": naive})) > 100

    def test_redact_url_keeps_only_scheme_and_host(self):
        assert (
            redact_url("https://hooks.slack.com/services/T/B/SECRET") == "https://hooks.slack.com"
        )
        assert redact_url("http://localhost:8123/hook?token=x") == "http://localhost:8123"
        assert redact_url("not a url") == "<invalid url>"
