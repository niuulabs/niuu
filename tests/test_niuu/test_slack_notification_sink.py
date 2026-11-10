"""The Slack bot notification sink: routing, Block Kit rendering and Slack's error classes."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from niuu.adapters.notifications.integrations import DEFAULT_INTEGRATION_SINKS
from niuu.adapters.notifications.slack import (
    MESSAGE_TEXT_KNOB,
    SECTION_TEXT_KNOB,
    SLACK_MESSAGE_TEXT_MAX_CHARS,
    SLACK_SECTION_TEXT_MAX_CHARS,
    SlackNotificationSink,
)
from niuu.cap_telemetry import cap_hit_counts, reset_cap_hits
from niuu.domain.notifications import (
    NotificationKind,
    NotificationSeverity,
    NotificationSource,
)
from niuu.ports.notifications import (
    DeliveryResult,
    NotificationDeliveryError,
    OutboundNotification,
)

CREATED = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
TOKEN = "xoxb-SECRET-bot-token"
EMAIL = "dana.private@example.com"


def message(**overrides) -> OutboundNotification:
    values = {
        "notification_id": "n-1",
        "title": "Deploy <prod> & verify",
        "kind": NotificationKind.DECISION,
        "severity": NotificationSeverity.WARNING,
        "source": NotificationSource.AGENT,
        "created_at": CREATED,
        "body": "Pick one: a < b",
        "session_label": "fix-auth",
        "url": "https://forge.example.com/volundr/session/s",
    }
    values.update(overrides)
    return OutboundNotification(**values)


class Slack:
    """A fake Slack Web API recording every request."""

    def __init__(self, *responses: httpx.Response) -> None:
        self.requests: list[httpx.Request] = []
        self._responses = list(responses)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self._responses:
            return self._responses.pop(0)
        return ok({"ts": "1731229200.000100"})

    def sink(self, **kwargs) -> SlackNotificationSink:
        client = httpx.AsyncClient(transport=httpx.MockTransport(self))
        kwargs.setdefault("channel", "#general")
        return SlackNotificationSink(access_token=TOKEN, client=client, **kwargs)

    def body(self, index: int = 0) -> dict:
        return json.loads(self.requests[index].content)


def ok(extra: dict | None = None) -> httpx.Response:
    return httpx.Response(200, json={"ok": True, **(extra or {})})


def refused(error: str, **kwargs) -> httpx.Response:
    return httpx.Response(200, json={"ok": False, "error": error}, **kwargs)


@pytest.fixture(autouse=True)
def _reset_caps():
    reset_cap_hits()
    yield
    reset_cap_hits()


class TestConstruction:
    def test_registered_for_the_slack_integration(self):
        assert (
            DEFAULT_INTEGRATION_SINKS["slack"]
            == "niuu.adapters.notifications.slack.SlackNotificationSink"
        )

    def test_empty_token_is_refused(self):
        with pytest.raises(ValueError, match="access_token"):
            SlackNotificationSink(access_token="")

    def test_unknown_kwargs_are_ignored_and_name_is_kept(self):
        sink = SlackNotificationSink(access_token=TOKEN, name="integration:slack", min_urgency="x")
        assert sink.name == "integration:slack"

    async def test_defaults_do_not_skip_and_close_is_repeatable(self):
        sink = Slack().sink()
        assert sink.skip_reason(message()) is None
        await sink.close()
        await sink.close()


class TestPosting:
    async def test_posts_to_the_connection_channel(self):
        slack = Slack(ok({"ts": "1731229200.000100"}))
        result = await slack.sink().send(message())

        assert result == DeliveryResult(provider_message_id="1731229200.000100")
        request = slack.requests[0]
        assert request.method == "POST"
        assert str(request.url) == "https://slack.com/api/chat.postMessage"
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert slack.body()["channel"] == "#general"

    async def test_destination_overrides_the_connection_channel(self):
        slack = Slack()
        await slack.sink().send(message(destination="#payments-team"))
        assert slack.body()["channel"] == "#payments-team"

    async def test_api_base_url_is_honoured(self):
        slack = Slack()
        await slack.sink(api_base_url="https://slack.test/api/").send(message())
        assert str(slack.requests[0].url) == "https://slack.test/api/chat.postMessage"

    async def test_missing_ts_gives_no_provider_id(self):
        slack = Slack(ok())
        assert (await slack.sink().send(message())).provider_message_id is None

    async def test_blocks_carry_title_body_context_and_link(self):
        slack = Slack()
        await slack.sink().send(message())

        payload = slack.body()
        assert payload["text"] == (
            "Deploy <prod> & verify\nPick one: a < b\nhttps://forge.example.com/volundr/session/s"
        )
        section, context, actions = payload["blocks"]
        assert section == {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Deploy &lt;prod&gt; &amp; verify*\nPick one: a &lt; b",
            },
        }
        assert context["elements"][0]["text"] == "fix-auth"
        button = actions["elements"][0]
        assert button["text"]["text"] == "Open in Niuu"
        assert button["url"] == "https://forge.example.com/volundr/session/s"

    async def test_no_url_no_label_no_body_renders_title_only(self):
        slack = Slack()
        await slack.sink().send(message(url=None, session_label=None, body=""))

        payload = slack.body()
        assert payload["text"] == "Deploy <prod> & verify"
        assert [block["type"] for block in payload["blocks"]] == ["section"]
        assert payload["blocks"][0]["text"]["text"] == "*Deploy &lt;prod&gt; &amp; verify*"

    async def test_non_http_url_is_not_linked(self):
        slack = Slack()
        await slack.sink().send(message(url="javascript:alert(1)"))
        assert "actions" not in [block["type"] for block in slack.body()["blocks"]]
        assert "javascript" not in slack.body()["text"]


class TestDirectMessage:
    async def test_looks_up_the_user_then_posts_to_their_id(self):
        slack = Slack(ok({"user": {"id": "U0123"}}), ok({"ts": "9.1"}))
        result = await slack.sink(channel="").send(
            message(recipient_email=EMAIL, destination="#ignored")
        )

        assert result.provider_message_id == "9.1"
        lookup, post = slack.requests
        assert lookup.method == "GET"
        assert lookup.url.path == "/api/users.lookupByEmail"
        assert lookup.url.params["email"] == EMAIL
        assert lookup.headers["authorization"] == f"Bearer {TOKEN}"
        assert json.loads(post.content)["channel"] == "U0123"

    async def test_unknown_email_is_permanent_and_does_not_name_the_email(self):
        slack = Slack(refused("users_not_found"))
        with pytest.raises(NotificationDeliveryError) as raised:
            await slack.sink().send(message(recipient_email=EMAIL))

        assert raised.value.retryable is False
        assert "no account" in str(raised.value)
        assert EMAIL not in str(raised.value)
        assert len(slack.requests) == 1

    async def test_lookup_without_a_user_id_is_permanent(self):
        slack = Slack(ok({"user": {}}))
        with pytest.raises(NotificationDeliveryError, match="users:read.email") as raised:
            await slack.sink().send(message(recipient_email=EMAIL))
        assert raised.value.retryable is False

    async def test_lookup_transport_failure_hides_the_email(self):
        def boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(f"cannot reach {request.url}", request=request)

        sink = SlackNotificationSink(
            access_token=TOKEN, client=httpx.AsyncClient(transport=httpx.MockTransport(boom))
        )
        with pytest.raises(NotificationDeliveryError) as raised:
            await sink.send(message(recipient_email=EMAIL))
        assert raised.value.retryable is True
        assert EMAIL not in str(raised.value)
        assert TOKEN not in str(raised.value)


class TestFailures:
    async def test_no_destination_anywhere_is_permanent_and_names_the_fix(self):
        slack = Slack()
        with pytest.raises(NotificationDeliveryError, match="set the team channel") as raised:
            await slack.sink(channel="").send(message())
        assert raised.value.retryable is False
        assert slack.requests == []

    async def test_blank_destination_falls_to_the_connection_channel(self):
        slack = Slack()
        await slack.sink().send(message(destination="  "))
        assert slack.body()["channel"] == "#general"

    @pytest.mark.parametrize("error", ["not_in_channel", "channel_not_found"])
    async def test_channel_errors_say_to_invite_the_bot(self, error):
        with pytest.raises(NotificationDeliveryError, match="invite the bot") as raised:
            await Slack(refused(error)).sink().send(message())
        assert raised.value.retryable is False

    @pytest.mark.parametrize(
        "error", ["invalid_auth", "token_revoked", "account_inactive", "not_authed"]
    )
    async def test_auth_errors_say_to_reinstall_the_app(self, error):
        with pytest.raises(NotificationDeliveryError, match="reinstall the Slack app") as raised:
            await Slack(refused(error)).sink().send(message())
        assert raised.value.retryable is False
        assert TOKEN not in str(raised.value)

    async def test_missing_scope_says_to_reinstall_with_scopes(self):
        with pytest.raises(NotificationDeliveryError, match="chat:write") as raised:
            await Slack(refused("missing_scope")).sink().send(message())
        assert raised.value.retryable is False

    async def test_unknown_slack_error_is_permanent_and_named(self):
        with pytest.raises(NotificationDeliveryError, match="invalid_blocks") as raised:
            await Slack(refused("invalid_blocks")).sink().send(message())
        assert raised.value.retryable is False

    async def test_ok_false_ratelimited_is_retryable_with_retry_after(self):
        response = refused("ratelimited", headers={"Retry-After": "7"})
        with pytest.raises(NotificationDeliveryError) as raised:
            await Slack(response).sink().send(message())
        assert raised.value.retryable is True
        assert raised.value.retry_after == 7.0

    async def test_http_429_is_retryable_with_retry_after(self):
        response = httpx.Response(429, headers={"Retry-After": "30"}, text="slow down")
        with pytest.raises(NotificationDeliveryError) as raised:
            await Slack(response).sink().send(message())
        assert raised.value.retryable is True
        assert raised.value.retry_after == 30.0
        assert raised.value.status_code == 429

    async def test_http_5xx_is_retryable(self):
        with pytest.raises(NotificationDeliveryError) as raised:
            await Slack(httpx.Response(503)).sink().send(message())
        assert raised.value.retryable is True

    async def test_slack_side_internal_error_is_retryable(self):
        with pytest.raises(NotificationDeliveryError) as raised:
            await Slack(refused("internal_error")).sink().send(message())
        assert raised.value.retryable is True

    async def test_http_4xx_is_permanent(self):
        with pytest.raises(NotificationDeliveryError) as raised:
            await Slack(httpx.Response(403, json={"error": "forbidden"})).sink().send(message())
        assert raised.value.retryable is False
        assert "forbidden" in str(raised.value)

    async def test_non_json_success_is_retryable(self):
        with pytest.raises(NotificationDeliveryError, match="not JSON") as raised:
            await Slack(httpx.Response(200, text="<html>")).sink().send(message())
        assert raised.value.retryable is True

    async def test_network_failure_is_retryable_and_hides_the_token(self):
        def boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout(f"timed out {request.headers['authorization']}")

        sink = SlackNotificationSink(
            access_token=TOKEN,
            channel="#c",
            client=httpx.AsyncClient(transport=httpx.MockTransport(boom)),
        )
        with pytest.raises(NotificationDeliveryError) as raised:
            await sink.send(message())
        assert raised.value.retryable is True
        assert TOKEN not in str(raised.value)
        assert "ConnectTimeout" in str(raised.value)

    @pytest.mark.parametrize(
        "response",
        [
            refused("invalid_auth"),
            refused("not_in_channel"),
            refused("users_not_found"),
            refused("ratelimited"),
            httpx.Response(500, text=f"echo {TOKEN}"),
        ],
    )
    async def test_the_token_is_never_in_an_error(self, response):
        with pytest.raises(NotificationDeliveryError) as raised:
            await Slack(response).sink().send(message(recipient_email=None))
        assert TOKEN not in str(raised.value)


class TestTruncation:
    async def test_long_body_is_cut_visibly_and_counted(self):
        slack = Slack()
        await slack.sink().send(message(body="x" * 5000, url=None))

        payload = slack.body()
        section = payload["blocks"][0]["text"]["text"]
        assert len(section) == SLACK_SECTION_TEXT_MAX_CHARS
        assert section.endswith("…")
        # The text fallback is far under Slack's own limit, so it keeps the full body.
        assert payload["text"].endswith("x" * 5000)
        counts = cap_hit_counts()
        assert counts == {SECTION_TEXT_KNOB: 1}

    async def test_text_fallback_over_slack_limit_is_cut_and_counted(self):
        slack = Slack()
        await slack.sink().send(message(body="y" * (SLACK_MESSAGE_TEXT_MAX_CHARS + 10), url=None))

        payload = slack.body()
        assert len(payload["text"]) == SLACK_MESSAGE_TEXT_MAX_CHARS
        assert payload["text"].endswith("…")
        assert cap_hit_counts() == {SECTION_TEXT_KNOB: 1, MESSAGE_TEXT_KNOB: 1}

    async def test_a_message_within_limits_counts_nothing(self):
        await Slack().sink().send(message())
        assert cap_hit_counts() == {}
