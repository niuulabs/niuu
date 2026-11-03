"""The exact payloads Forge pushes to the app (a wire contract with its Swift code)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from volundr.domain.push import (
    APNS_MAX_PAYLOAD_BYTES,
    ApnsPush,
    ApnsPushType,
    ApnsResult,
    GuildNotification,
    InterruptionLevel,
    LiveActivityEvent,
    PushEnvironment,
    PushTestKind,
    absolute_url,
    activity_deep_link,
    alert_payload,
    apns_topic,
    background_payload,
    live_activity_content_state,
    live_activity_payload,
    shorten,
    swift_reference_seconds,
    widget_payload,
)

BUNDLE = "com.xteo.lexi.chat"
INSTANCE = "56499f00-02b3-4d80-8137-09d6d3238056"
SESSION = "a2c32d25-603c-4e51-a54d-248c1384c155"
# 2026-10-02T06:40:14Z is 1_790_923_214 unix seconds, 812_616_014 Swift reference seconds.
CREATED = datetime(2026, 10, 2, 6, 40, 14, tzinfo=UTC)
CREATED_UNIX = 1_790_923_214
CREATED_SWIFT = 812_616_014.0


def feed_item(**overrides) -> dict:
    item = {
        "id": "1a97554b-96b1-5fac-baa4-5bf53ba35d92",
        "seq": 323,
        "session_id": SESSION,
        "session_name": "horde-coordinator",
        "owner_id": "dev-user",
        "tenant_id": "default",
        "kind": "decision",
        "severity": "success",
        "source": "agent",
        "title": "Final round green: merge now",
        "body": "Round 5 on the exact pair: 209/244",
        "turn_id": "nt_11c2f54c82f14538b4c717374fb9cdfb",
        "created_at": "2026-10-02T06:40:14Z",
        "image": None,
        "read": False,
        "instance_id": INSTANCE,
        "instance_name": "Thor",
    }
    item.update(overrides)
    return item


def notification(**overrides) -> GuildNotification:
    return GuildNotification.from_feed_item(feed_item(**overrides))


class TestSwiftDates:
    def test_reference_date_is_2001(self):
        assert swift_reference_seconds(datetime(2001, 1, 1, tzinfo=UTC)) == 0.0

    def test_unix_minus_978307200(self):
        assert CREATED.timestamp() == CREATED_UNIX
        assert swift_reference_seconds(CREATED) == CREATED_UNIX - 978_307_200 == CREATED_SWIFT

    def test_fractional_seconds_survive(self):
        assert swift_reference_seconds(CREATED + timedelta(milliseconds=500)) == CREATED_SWIFT + 0.5

    def test_naive_instant_is_refused(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            swift_reference_seconds(datetime(2026, 10, 2))


class TestFeedItem:
    def test_parses_the_guild_feed_shape(self):
        parsed = notification()
        assert parsed.id == "1a97554b-96b1-5fac-baa4-5bf53ba35d92"
        assert parsed.instance_id == INSTANCE
        assert parsed.seq == 323
        assert parsed.owner_id == "dev-user"
        assert parsed.created_at == CREATED
        assert parsed.turn_id == "nt_11c2f54c82f14538b4c717374fb9cdfb"
        assert parsed.read is False
        assert parsed.image_url is None

    def test_image_urls_come_from_the_ready_image(self):
        parsed = notification(
            image={"url": "/api/v1/forge/notifications/n/image?instance_id=i", "thumbnail_url": ""}
        )
        assert parsed.image_url == "/api/v1/forge/notifications/n/image?instance_id=i"
        assert parsed.image_thumbnail_url is None

    def test_an_image_still_being_copied_has_its_final_urls(self):
        parsed = notification(image=None, image_state="pending")
        base = "/api/v1/forge/notifications/1a97554b-96b1-5fac-baa4-5bf53ba35d92/image"
        assert parsed.image_url == f"{base}?instance_id={INSTANCE}"
        assert parsed.image_thumbnail_url == f"{base}?variant=thumb&instance_id={INSTANCE}"
        assert GuildNotification.from_feed_item(parsed.stored()) == parsed

    @pytest.mark.parametrize("state", ["failed", None])
    def test_a_failed_or_absent_image_has_no_url(self, state):
        parsed = notification(image=None, image_state=state)
        assert parsed.image_url is None and parsed.image_thumbnail_url is None

    def test_stored_form_round_trips(self):
        original = notification(
            image={"url": "/img", "thumbnail_url": "/img?variant=thumb"}, read=True
        )
        assert GuildNotification.from_feed_item(original.stored()) == original
        assert GuildNotification.from_feed_item(notification().stored()) == notification()

    @pytest.mark.parametrize("field", ["id", "instance_id", "owner_id", "seq", "kind", "title"])
    def test_an_item_missing_an_essential_field_is_refused(self, field):
        with pytest.raises(ValueError):
            GuildNotification.from_feed_item(feed_item(**{field: None}))

    def test_unparseable_time_and_naive_time(self):
        assert notification(created_at="yesterday").created_at is None
        assert notification(created_at="2026-10-02T06:40:14").created_at == CREATED


class TestDeepLink:
    def test_all_parts(self):
        link = activity_deep_link(
            "lexichat://activity", "n-1", instance_id="i-1", session_id="s-1", turn_id="t-1"
        )
        assert link == "lexichat://activity/n-1?inst=i-1&session=s-1&turn=t-1"

    def test_absent_parts_are_left_out(self):
        assert activity_deep_link("lexichat://activity", "n-1") == "lexichat://activity/n-1"
        assert (
            activity_deep_link("lexichat://activity", "n-1", session_id="s-1", turn_id="")
            == "lexichat://activity/n-1?session=s-1"
        )

    def test_ids_are_percent_encoded(self):
        link = activity_deep_link(
            "lexichat://activity/", "a/b c", instance_id="x&y", turn_id="t=1 2"
        )
        assert link == "lexichat://activity/a%2Fb%20c?inst=x%26y&turn=t%3D1%202"


class TestAbsoluteUrl:
    def test_relative_path_on_the_public_origin(self):
        assert (
            absolute_url("http://thor.tail737f2a.ts.net:8080/", "/api/v1/forge/x?instance_id=i")
            == "http://thor.tail737f2a.ts.net:8080/api/v1/forge/x?instance_id=i"
        )

    def test_without_an_origin_or_a_path_there_is_no_url(self):
        assert absolute_url("", "/api/x") is None
        assert absolute_url("http://host", None) is None
        assert absolute_url("http://host", "relative") is None

    def test_absolute_urls_pass_through(self):
        assert absolute_url("", "https://cdn.example/x.png") == "https://cdn.example/x.png"


class TestAlertPayload:
    def _payload(self, **overrides) -> dict:
        item = overrides.pop("notification", notification())
        kwargs = {
            "title": item.title,
            "body": item.body,
            "deep_link": "lexichat://activity/n?inst=i",
            "interruption_level": InterruptionLevel.TIME_SENSITIVE,
            "category": "FORGE_ACTIVITY",
            "sound": "default",
            "notification": item,
        }
        kwargs.update(overrides)
        return alert_payload(**kwargs)

    def test_exact_shape(self):
        assert self._payload(subtitle="horde-coordinator") == {
            "aps": {
                "alert": {
                    "title": "Final round green: merge now",
                    "body": "Round 5 on the exact pair: 209/244",
                    "subtitle": "horde-coordinator",
                },
                "interruption-level": "time-sensitive",
                "sound": "default",
                "category": "FORGE_ACTIVITY",
                "thread-id": SESSION,
            },
            "deepLink": "lexichat://activity/n?inst=i",
            "session_id": SESSION,
            "notification_id": "1a97554b-96b1-5fac-baa4-5bf53ba35d92",
            "instance_id": INSTANCE,
            "kind": "decision",
        }

    def test_image_adds_mutable_content_and_urls(self):
        payload = self._payload(
            image_url="http://thor:8080/img",
            image_thumbnail_url="http://thor:8080/img?variant=thumb",
        )
        assert payload["aps"]["mutable-content"] == 1
        assert payload["image_url"] == "http://thor:8080/img"
        assert payload["image_thumbnail_url"] == "http://thor:8080/img?variant=thumb"

    def test_no_image_no_mutable_content(self):
        payload = self._payload()
        assert "mutable-content" not in payload["aps"]
        assert "image_url" not in payload and "image_thumbnail_url" not in payload

    def test_sessionless_notification_has_no_thread(self):
        payload = self._payload(notification=notification(session_id=None))
        assert "thread-id" not in payload["aps"]
        assert "session_id" not in payload

    def test_active_level_silent_and_uncategorised(self):
        payload = self._payload(interruption_level=InterruptionLevel.ACTIVE, sound="", category="")
        assert payload["aps"]["interruption-level"] == "active"
        assert "sound" not in payload["aps"] and "category" not in payload["aps"]

    def test_a_test_alert_carries_only_the_link(self):
        payload = alert_payload(
            title="Forge test push",
            body="It works",
            deep_link="lexichat://activity",
            interruption_level=InterruptionLevel.ACTIVE,
            category="FORGE_ACTIVITY",
            sound="default",
        )
        assert payload == {
            "aps": {
                "alert": {"title": "Forge test push", "body": "It works"},
                "interruption-level": "active",
                "sound": "default",
                "category": "FORGE_ACTIVITY",
            },
            "deepLink": "lexichat://activity",
        }


class TestLiveActivityContentState:
    def test_exact_content_state_json(self):
        newest = notification()
        older = notification(
            id="26829b32-0296-5b9c-95a8-12403cb5bdac",
            kind="attention",
            title="horde-coordinator needs your input",
            turn_id=None,
            session_name=None,
            created_at="2026-10-02T00:51:18.500Z",
            read=True,
        )
        state = live_activity_content_state(
            [newest, older],
            unread_count=1,
            updated_at=CREATED + timedelta(seconds=2),
            origin="Thor",
        )
        # The bytes on the wire, as Swift's default JSONDecoder reads them.
        assert json.loads(json.dumps(state)) == {
            "items": [
                {
                    "id": "1a97554b-96b1-5fac-baa4-5bf53ba35d92",
                    "kind": "decision",
                    "title": "Final round green: merge now",
                    "sessionName": "horde-coordinator",
                    "instanceID": INSTANCE,
                    "sessionID": SESSION,
                    "turnID": "nt_11c2f54c82f14538b4c717374fb9cdfb",
                    "createdAt": 812_616_014.0,
                    "unread": True,
                },
                {
                    "id": "26829b32-0296-5b9c-95a8-12403cb5bdac",
                    "kind": "attention",
                    "title": "horde-coordinator needs your input",
                    "instanceID": INSTANCE,
                    "sessionID": SESSION,
                    "createdAt": 812_595_078.5,
                    "unread": False,
                },
            ],
            "unreadCount": 1,
            "updatedAt": 812_616_016.0,
            "origin": "Thor",
        }

    def test_dates_are_json_numbers_not_strings(self):
        state = live_activity_content_state([notification()], unread_count=0, updated_at=CREATED)
        encoded = json.dumps(state)
        assert '"updatedAt": 812616014.0' in encoded
        assert '"createdAt": 812616014.0' in encoded
        assert "origin" not in state

    def test_at_most_three_items_newest_first(self):
        items = [notification(id=f"n-{index}", seq=index) for index in (5, 4, 3, 2, 1)]
        state = live_activity_content_state(items, unread_count=9, updated_at=CREATED)
        assert [item["id"] for item in state["items"]] == ["n-5", "n-4", "n-3"]
        assert state["unreadCount"] == 9

    def test_titles_and_session_names_stay_within_the_swift_limits(self):
        state = live_activity_content_state(
            [notification(title="t" * 200, session_name="s" * 100)],
            unread_count=0,
            updated_at=CREATED,
        )
        assert state["items"][0]["title"] == "t" * 90
        assert state["items"][0]["sessionName"] == "s" * 40

    def test_negative_unread_count_is_clamped(self):
        state = live_activity_content_state([], unread_count=-3, updated_at=CREATED)
        assert state == {"items": [], "unreadCount": 0, "updatedAt": CREATED_SWIFT}

    def test_three_longest_items_fit_the_apns_limit(self):
        items = [
            notification(id=f"{index}" * 36, title="é" * 200, session_name="ü" * 100)
            for index in range(3)
        ]
        state = live_activity_content_state(
            items, unread_count=999, updated_at=CREATED, origin="A long server name"
        )
        push = ApnsPush(
            token="t",
            environment=PushEnvironment.PRODUCTION,
            topic="x",
            push_type=ApnsPushType.LIVE_ACTIVITY,
            priority=10,
            payload=live_activity_payload(
                LiveActivityEvent.START,
                state,
                timestamp=CREATED_UNIX,
                stale_date=CREATED_UNIX + 28800,
                relevance_score=10,
                alert_title="é" * 90,
                alert_body="ü" * 40,
            ),
        )
        assert len(push.body()) < APNS_MAX_PAYLOAD_BYTES


class TestLiveActivityPayload:
    STATE = {"items": [], "unreadCount": 0, "updatedAt": CREATED_SWIFT}

    def test_update(self):
        payload = live_activity_payload(
            LiveActivityEvent.UPDATE,
            self.STATE,
            timestamp=CREATED_UNIX,
            stale_date=CREATED_UNIX + 28800,
            relevance_score=10,
        )
        assert payload == {
            "aps": {
                "timestamp": 1_790_923_214,
                "event": "update",
                "content-state": self.STATE,
                "stale-date": 1_790_952_014,
                "relevance-score": 10,
            }
        }

    def test_start_names_the_attributes_and_carries_an_alert(self):
        payload = live_activity_payload(
            LiveActivityEvent.START,
            self.STATE,
            timestamp=CREATED_UNIX,
            stale_date=CREATED_UNIX + 28800,
            relevance_score=10,
            alert_title="Final round green",
            alert_body="horde-coordinator",
        )
        assert payload == {
            "aps": {
                "timestamp": 1_790_923_214,
                "event": "start",
                "content-state": self.STATE,
                "stale-date": 1_790_952_014,
                "relevance-score": 10,
                "attributes-type": "ForgeNotificationsActivityAttributes",
                "attributes": {},
                "alert": {"title": "Final round green", "body": "horde-coordinator"},
            }
        }


class TestLiveActivityRelevance:
    """LEX-1353: Forge's notifications activity ranks below a live call (scored 100 by
    the app), on push-to-start and on every update, so the call owns the Dynamic Island."""

    def test_config_default_is_the_notifications_level(self):
        from volundr.config import PushLiveActivityConfig

        assert PushLiveActivityConfig().relevance_score == 10
        assert PushLiveActivityConfig(relevance_score=0).relevance_score == 0

    def test_configured_score_reaches_every_push(self):
        state = {"items": [], "unreadCount": 0, "updatedAt": CREATED_SWIFT}
        for event in (LiveActivityEvent.START, LiveActivityEvent.UPDATE):
            payload = live_activity_payload(
                event,
                state,
                timestamp=CREATED_UNIX,
                stale_date=CREATED_UNIX + 1,
                relevance_score=25,
            )
            assert payload["aps"]["relevance-score"] == 25, event


class TestWidgetAndTopics:
    def test_widget_payload(self):
        assert widget_payload() == {"aps": {"content-changed": True}}
        assert json.dumps(widget_payload()) == '{"aps": {"content-changed": true}}'

    def test_background_payload(self):
        assert background_payload() == {
            "aps": {"content-available": 1},
            "forge": {"reason": "notification"},
        }
        assert (
            json.dumps(background_payload(), separators=(",", ":"))
            == '{"aps":{"content-available":1},"forge":{"reason":"notification"}}'
        )
        # A background push uses the app's own topic, like an alert.
        assert apns_topic(BUNDLE, ApnsPushType.BACKGROUND) == "com.xteo.lexi.chat"
        assert ApnsPushType.BACKGROUND.value == "background"

    def test_each_test_push_goes_to_one_kind_of_token(self):
        assert {kind.value: kind.token_kind.value for kind in PushTestKind} == {
            "alert": "alert",
            "background": "alert",
            "live_activity": "live_activity",
            "live_activity_start": "live_activity_start",
            "widget": "widget",
        }

    def test_topics(self):
        assert apns_topic(BUNDLE, ApnsPushType.ALERT) == "com.xteo.lexi.chat"
        assert (
            apns_topic(BUNDLE, ApnsPushType.LIVE_ACTIVITY)
            == "com.xteo.lexi.chat.push-type.liveactivity"
        )
        assert apns_topic(BUNDLE, ApnsPushType.WIDGETS) == "com.xteo.lexi.chat.push-type.widgets"


class TestApnsPushAndResult:
    def test_body_is_compact_utf8_json(self):
        push = ApnsPush(
            token="t",
            environment=PushEnvironment.SANDBOX,
            topic=BUNDLE,
            push_type=ApnsPushType.ALERT,
            priority=10,
            payload={"aps": {"alert": {"title": "é"}}},
        )
        assert push.body() == '{"aps":{"alert":{"title":"é"}}}'.encode()

    def test_oversized_payload_is_refused(self):
        push = ApnsPush(
            token="t",
            environment=PushEnvironment.SANDBOX,
            topic=BUNDLE,
            push_type=ApnsPushType.ALERT,
            priority=10,
            payload={"aps": {"alert": {"body": "x" * 5000}}},
        )
        with pytest.raises(ValueError, match="limit is 4096"):
            push.body()

    @pytest.mark.parametrize(
        ("result", "accepted", "dead", "retryable", "text"),
        [
            (ApnsResult(200, apns_id="id"), True, False, False, "accepted"),
            (ApnsResult(410, "Unregistered"), False, True, False, "HTTP 410 Unregistered"),
            (ApnsResult(410), False, True, False, "HTTP 410"),
            (ApnsResult(400, "BadDeviceToken"), False, True, False, "HTTP 400 BadDeviceToken"),
            (ApnsResult(400, "ExpiredToken"), False, True, False, "HTTP 400 ExpiredToken"),
            (ApnsResult(400, "BadTopic"), False, False, False, "HTTP 400 BadTopic"),
            (ApnsResult(403, "InvalidProviderToken"), False, False, False, None),
            (ApnsResult(429, "TooManyRequests"), False, False, True, None),
            (ApnsResult(503, "ServiceUnavailable"), False, False, True, None),
            (ApnsResult(None, "ConnectError"), False, False, True, "not reached (ConnectError)"),
            (ApnsResult(None), False, False, True, "not reached"),
        ],
    )
    def test_result_classification(self, result, accepted, dead, retryable, text):
        assert result.accepted is accepted
        assert result.token_is_dead is dead
        assert result.retryable is retryable
        if text is not None:
            assert result.describe() == text


def test_shorten_collapses_whitespace_and_marks_the_cut():
    assert shorten("  a\n\nb   c ", 20) == "a b c"
    assert shorten("abcdefghij", 5) == "abcd…"


class TestAttentionPayload:
    """LEX-1186: what the app's notification service extension reads on attention pushes."""

    def _payload(self, **overrides):
        notification = GuildNotification.from_feed_item(
            {
                "id": "n-1",
                "seq": 1,
                "kind": "attention",
                "title": "t",
                "body": "b",
                "instance_id": "inst-1",
                "session_id": overrides.pop("session_id", "sess-1"),
                "session_name": overrides.pop("session_name", "ios-fixes"),
                "engine": overrides.pop("engine", "Claude"),
                "owner_id": "u",
            }
        )
        return alert_payload(
            title="t",
            body="b",
            deep_link="lexichat://activity/n-1",
            interruption_level=InterruptionLevel.TIME_SENSITIVE,
            category="FORGE_ACTIVITY",
            sound="default",
            notification=notification,
            **overrides,
        )

    def test_an_attention_push_runs_the_extension_and_names_the_agent(self):
        payload = self._payload(attention=True)
        assert payload["aps"]["mutable-content"] == 1
        assert payload["attention"] is True
        assert payload["agent_id"] == "sess-1"
        assert payload["agent_name"] == "ios-fixes"
        assert payload["conversation_id"] == "inst-1:sess-1"
        assert payload["engine"] == "claude"

    def test_an_attention_push_without_a_session_names_no_agent(self):
        payload = self._payload(attention=True, session_id=None, session_name=None, engine=None)
        assert payload["attention"] is True
        assert not {"agent_id", "agent_name", "conversation_id", "engine"} & set(payload)

    def test_other_pushes_are_unchanged(self):
        payload = self._payload()
        assert "mutable-content" not in payload["aps"]
        assert not {"attention", "agent_id", "agent_name", "conversation_id", "engine"} & set(
            payload
        )
