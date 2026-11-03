"""The push gateway against a real PostgreSQL: its migration, its two stores and one
end-to-end tick (real SQL, an in-memory Guild feed and a recording APNs).

Each test runs in one transaction that is rolled back, so ``now()`` is the same
instant throughout a test: intervals are exercised by their length, not by waiting.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest

from tests.integration.pool_wrapper import TransactionalPool
from tests.support.push_fakes import (
    MINI,
    THOR,
    FakeApns,
    FakeGuildFeed,
    device,
    gateway_settings,
    guild_notification,
)
from volundr.adapters.outbound.postgres_device_tokens import PostgresDeviceTokenRepository
from volundr.adapters.outbound.postgres_push_gateway import PostgresPushDeliveryRepository
from volundr.domain.push import ApnsResult, PushEnvironment, PushTokenKind
from volundr.domain.push_ports import PushDeliveryStatus
from volundr.domain.services.push_gateway import BackgroundSettings, PushGateway

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

MIGRATIONS = Path(__file__).resolve().parents[3] / "migrations"
UP = (MIGRATIONS / "000099_push_gateway.up.sql").read_text()
DOWN = (MIGRATIONS / "000099_push_gateway.down.sql").read_text()


@pytest.fixture
def devices(txn_pool: TransactionalPool) -> PostgresDeviceTokenRepository:
    return PostgresDeviceTokenRepository(txn_pool)  # type: ignore[arg-type]


@pytest.fixture
def deliveries(txn_pool: TransactionalPool) -> PostgresPushDeliveryRepository:
    return PostgresPushDeliveryRepository(txn_pool, max_detail_chars=500)  # type: ignore[arg-type]


async def db_now(pool: TransactionalPool) -> datetime:
    return await pool.fetchval("SELECT now()")


class TestMigration:
    async def test_schema(self, txn_pool):
        columns = {
            row["column_name"]: (row["data_type"], row["is_nullable"], row["column_default"])
            for row in await txn_pool.fetch(
                """SELECT column_name, data_type, is_nullable, column_default
                   FROM information_schema.columns WHERE table_name = 'device_tokens'"""
            )
        }
        assert columns["kind"] == ("text", "NO", "'alert'::text")
        assert columns["environment"] == ("text", "NO", "'production'::text")
        assert columns["activity_id"][:2] == ("text", "YES")
        assert columns["last_push_at"][:2] == ("timestamp with time zone", "YES")
        assert columns["push_due"] == ("boolean", "NO", "false")
        constraints = await txn_pool.fetch(
            "SELECT conname FROM pg_constraint WHERE conrelid = 'device_tokens'::regclass"
        )
        assert "device_tokens_owner_id_token_key" not in {row["conname"] for row in constraints}
        for table in ("forge_push_gateway_state", "forge_push_deliveries"):
            assert await txn_pool.fetchval("SELECT to_regclass($1)", table) is not None

    async def test_up_is_idempotent(self, txn_pool):
        await txn_pool.execute(UP)
        await txn_pool.execute(UP)

    async def test_down_then_up_keeps_alert_tokens_and_merges_a_shared_token(
        self, txn_pool, devices
    ):
        await devices.upsert(device("alert-a", owner_id="owner-a"))
        await devices.upsert(device("widget-a", PushTokenKind.WIDGET, owner_id="owner-a"))

        await txn_pool.execute(DOWN)

        assert await txn_pool.fetchval("SELECT to_regclass('forge_push_deliveries')") is None
        assert [
            row["token"] for row in await txn_pool.fetch("SELECT token FROM device_tokens")
        ] == ["alert-a"]
        # The old shape again: one row per (owner, token), so two owners can hold a token.
        for owner, age in (("owner-a", 2), ("owner-b", 1)):
            await txn_pool.execute(
                """INSERT INTO device_tokens (id, owner_id, platform, token, updated_at)
                   VALUES (gen_random_uuid(), $1, 'ios', 'shared',
                           now() - make_interval(hours => $2))""",
                owner,
                age,
            )
        with pytest.raises(asyncpg.UniqueViolationError):
            async with txn_pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(
                        """INSERT INTO device_tokens (id, owner_id, platform, token)
                           VALUES (gen_random_uuid(), 'owner-a', 'ios', 'shared')"""
                    )

        await txn_pool.execute(UP)

        rows = await txn_pool.fetch(
            "SELECT owner_id, token, kind, environment FROM device_tokens ORDER BY token"
        )
        assert [tuple(row) for row in rows] == [
            ("owner-a", "alert-a", "alert", "production"),
            # The newest registration of the shared token wins.
            ("owner-b", "shared", "alert", "production"),
        ]


class TestDeviceTokens:
    async def test_register_every_kind_of_one_install(self, devices):
        await devices.upsert(device("alert-1", environment=PushEnvironment.SANDBOX))
        await devices.upsert(device("la-1", PushTokenKind.LIVE_ACTIVITY, activity_id="act-1"))
        await devices.upsert(device("start-1", PushTokenKind.LIVE_ACTIVITY_START))
        await devices.upsert(device("widget-1", PushTokenKind.WIDGET))

        everything = await devices.list_for_owner("dev-user")
        assert {(d.token, d.kind) for d in everything} == {
            ("alert-1", PushTokenKind.ALERT),
            ("la-1", PushTokenKind.LIVE_ACTIVITY),
            ("start-1", PushTokenKind.LIVE_ACTIVITY_START),
            ("widget-1", PushTokenKind.WIDGET),
        }
        alerts = await devices.list_for_owner("dev-user", kinds=[PushTokenKind.ALERT])
        assert [(d.token, d.environment) for d in alerts] == [("alert-1", PushEnvironment.SANDBOX)]
        live = await devices.list_for_owner(
            "dev-user", kinds=[PushTokenKind.LIVE_ACTIVITY, PushTokenKind.LIVE_ACTIVITY_START]
        )
        assert {d.token for d in live} == {"la-1", "start-1"}
        assert next(d for d in live if d.token == "la-1").activity_id == "act-1"

    async def test_upsert_refreshes_the_row_and_keeps_its_id(self, devices):
        first = await devices.upsert(device("alert-1", environment=PushEnvironment.SANDBOX))
        again = device("alert-1", app_bundle_id="com.other.app")

        stored = await devices.upsert(again)

        assert stored.id == first.id != again.id
        assert stored.environment is PushEnvironment.PRODUCTION
        assert stored.app_bundle_id == "com.other.app"
        assert len(await devices.list_for_owner("dev-user")) == 1

    async def test_a_token_moves_to_the_owner_who_registers_it_last(self, devices):
        await devices.upsert(device("alert-1", owner_id="owner-a"))
        await devices.upsert(device("alert-1", owner_id="owner-b"))

        assert await devices.list_for_owner("owner-a") == []
        assert [d.token for d in await devices.list_for_owner("owner-b")] == ["alert-1"]

    async def test_the_same_token_may_be_registered_under_two_kinds(self, devices):
        await devices.upsert(device("tok", PushTokenKind.ALERT))
        await devices.upsert(device("tok", PushTokenKind.WIDGET))

        assert len(await devices.list_for_owner("dev-user")) == 2
        assert await devices.delete("dev-user", "tok", kind=PushTokenKind.WIDGET) is True
        assert [d.kind for d in await devices.list_for_owner("dev-user")] == [PushTokenKind.ALERT]

    async def test_a_new_live_activity_token_replaces_the_activitys_earlier_one(self, devices):
        await devices.upsert(device("la-old", PushTokenKind.LIVE_ACTIVITY, activity_id="act-1"))
        await devices.upsert(device("la-other", PushTokenKind.LIVE_ACTIVITY, activity_id="act-2"))
        await devices.upsert(
            device("la-theirs", PushTokenKind.LIVE_ACTIVITY, activity_id="act-1", owner_id="other")
        )

        await devices.upsert(device("la-new", PushTokenKind.LIVE_ACTIVITY, activity_id="act-1"))

        mine = await devices.list_for_owner("dev-user", kinds=[PushTokenKind.LIVE_ACTIVITY])
        assert {d.token for d in mine} == {"la-new", "la-other"}
        assert [d.token for d in await devices.list_for_owner("other")] == ["la-theirs"]

    async def test_delete(self, devices):
        await devices.upsert(device("tok", PushTokenKind.ALERT))
        await devices.upsert(device("tok", PushTokenKind.WIDGET))
        stored = await devices.upsert(device("gone", PushTokenKind.LIVE_ACTIVITY))

        assert await devices.delete("someone-else", "tok") is False
        assert await devices.delete("dev-user", "tok") is True
        assert await devices.delete("dev-user", "tok") is False
        assert await devices.delete_registration(stored.id) is True
        assert await devices.delete_registration(stored.id) is False
        assert await devices.list_for_owner("dev-user") == []

    async def test_push_interval_claim_owed_and_release(self, devices, txn_pool):
        widget = await devices.upsert(device("widget-1", PushTokenKind.WIDGET))
        other = await devices.upsert(device("widget-2", PushTokenKind.WIDGET))
        start = await devices.upsert(device("start-1", PushTokenKind.LIVE_ACTIVITY_START))

        # Never pushed: the claim succeeds and records the time.
        assert await devices.claim_push(widget.id, min_interval_seconds=300) is True
        (claimed,) = [d for d in await devices.list_for_owner("dev-user") if d.id == widget.id]
        assert claimed.last_push_at == await db_now(txn_pool)

        # Inside the interval: refused, and the reload is owed.
        assert await devices.claim_push(widget.id, min_interval_seconds=300) is False
        assert await devices.claim_owed_pushes(PushTokenKind.WIDGET, min_interval_seconds=300) == []

        # The interval is over (a zero-length one here): the owed reload is claimed once.
        owed = await devices.claim_owed_pushes(PushTokenKind.WIDGET, min_interval_seconds=0)
        assert [d.id for d in owed] == [widget.id]
        assert await devices.claim_owed_pushes(PushTokenKind.WIDGET, min_interval_seconds=0) == []

        # A push APNs could not take is released: claimable at once, and owed.
        assert await devices.claim_push(other.id, min_interval_seconds=300) is True
        await devices.release_push(other.id)
        owed = await devices.claim_owed_pushes(PushTokenKind.WIDGET, min_interval_seconds=300)
        assert [d.id for d in owed] == [other.id]

        # Owed pushes are claimed per kind.
        await devices.release_push(start.id)
        assert await devices.claim_owed_pushes(PushTokenKind.WIDGET, min_interval_seconds=0) == []
        assert await devices.claim_push(start.id, min_interval_seconds=3600) is True
        assert await devices.claim_push(start.id, min_interval_seconds=3600) is False


class TestDeliveries:
    async def test_positions_merge_per_node(self, deliveries):
        assert await deliveries.load_positions() == {}
        await deliveries.save_positions({THOR: 323})
        await deliveries.save_positions({MINI: 7})
        await deliveries.save_positions({THOR: 330})
        await deliveries.save_positions({})
        assert await deliveries.load_positions() == {THOR: 330, MINI: 7}

    async def test_record_is_idempotent_and_advances_positions(self, deliveries):
        first = guild_notification("n-1", seq=1, image_url="/img", image_thumbnail_url="/thumb")
        same_id_other_node = guild_notification("n-1", instance_id=MINI, seq=4)

        assert await deliveries.record([first, same_id_other_node], {THOR: 1, MINI: 4}) == [
            first,
            same_id_other_node,
        ]
        assert await deliveries.record([first], {THOR: 1}) == []
        assert await deliveries.load_positions() == {THOR: 1, MINI: 4}

        claimed = await deliveries.claim_due(limit=10, lease_seconds=60)
        assert {(d.notification.instance_id, d.notification.id) for d in claimed} == {
            (THOR, "n-1"),
            (MINI, "n-1"),
        }
        restored = next(d.notification for d in claimed if d.notification.instance_id == THOR)
        assert restored == first

    async def test_text_postgres_cannot_store_is_scrubbed_not_fatal(self, deliveries):
        awkward = guild_notification("n-1", title="bad\x00title", body="emoji 🚀 stays")
        await deliveries.record([awkward], {THOR: 1})

        (claimed,) = await deliveries.claim_due(limit=1, lease_seconds=60)
        assert "\x00" not in claimed.notification.title
        assert claimed.notification.body == "emoji 🚀 stays"

    async def test_claim_is_a_lease_and_settle_is_fenced(self, deliveries, txn_pool):
        await deliveries.record([guild_notification("n-1"), guild_notification("n-2", seq=2)], {})

        (first,) = await deliveries.claim_due(limit=1, lease_seconds=60)
        (second,) = await deliveries.claim_due(limit=5, lease_seconds=60)
        assert {first.notification.id, second.notification.id} == {"n-1", "n-2"}
        assert first.attempts == 1
        # Both are leased: nothing is due.
        assert await deliveries.claim_due(limit=5, lease_seconds=60) == []

        # The lease of n-1 ran out and another worker claimed it again.
        await txn_pool.execute(
            "UPDATE forge_push_deliveries SET next_attempt_at = now() WHERE notification_id = $1",
            first.notification.id,
        )
        (reclaimed,) = await deliveries.claim_due(limit=5, lease_seconds=60)
        assert reclaimed.attempts == 2

        # The first worker's settle and retry are refused; the second worker's count.
        assert await deliveries.settle(first, PushDeliveryStatus.SENT, "late") is False
        assert await deliveries.retry(first, detail="late", retry_at=datetime.now(UTC)) is False
        assert (
            await deliveries.settle(reclaimed, PushDeliveryStatus.SENT, "APNs accepted 1") is True
        )
        assert await deliveries.settle(reclaimed, PushDeliveryStatus.SENT, "again") is False
        row = await txn_pool.fetchrow(
            """SELECT status, detail, settled_at FROM forge_push_deliveries
               WHERE notification_id = $1""",
            first.notification.id,
        )
        assert (row["status"], row["detail"]) == ("sent", "APNs accepted 1")
        assert row["settled_at"] is not None

    async def test_retry_keeps_the_row_pending_until_its_time(self, deliveries, txn_pool):
        await deliveries.record([guild_notification("n-1")], {})
        (claimed,) = await deliveries.claim_due(limit=1, lease_seconds=60)
        now = await db_now(txn_pool)

        assert await deliveries.retry(
            claimed, detail="APNs: HTTP 503", retry_at=now + timedelta(seconds=15)
        )
        assert await deliveries.claim_due(limit=1, lease_seconds=60) == []

        assert await deliveries.retry(claimed, detail="APNs: HTTP 503", retry_at=now)
        (again,) = await deliveries.claim_due(limit=1, lease_seconds=60)
        assert again.attempts == 2

    async def test_count_sent_since_is_per_owner_and_window(self, deliveries, txn_pool):
        mine = [guild_notification(f"n-{seq}", seq=seq) for seq in (1, 2, 3)]
        theirs = guild_notification("o-1", instance_id=MINI, owner_id="other")
        await deliveries.record([*mine, theirs], {})
        statuses = {
            "n-1": PushDeliveryStatus.SENT,
            "n-2": PushDeliveryStatus.SENT,
            "n-3": PushDeliveryStatus.SUPPRESSED,
            "o-1": PushDeliveryStatus.SENT,
        }
        for claimed in await deliveries.claim_due(limit=10, lease_seconds=60):
            await deliveries.settle(claimed, statuses[claimed.notification.id], "x")
        await txn_pool.execute(
            """UPDATE forge_push_deliveries SET settled_at = now() - interval '2 hours'
               WHERE notification_id = 'n-1'"""
        )
        now = await db_now(txn_pool)

        assert await deliveries.count_sent_since("dev-user", now - timedelta(hours=1)) == 1
        assert await deliveries.count_sent_since("dev-user", now - timedelta(hours=3)) == 2
        assert await deliveries.count_sent_since("other", now - timedelta(hours=1)) == 1
        assert await deliveries.count_sent_since("nobody", now - timedelta(hours=1)) == 0

    async def test_prune_keeps_pending_and_recent_rows(self, deliveries, txn_pool):
        await deliveries.record([guild_notification(f"n-{seq}", seq=seq) for seq in (1, 2, 3)], {})
        claimed = {
            d.notification.id: d for d in await deliveries.claim_due(limit=2, lease_seconds=60)
        }
        for delivery in claimed.values():
            await deliveries.settle(delivery, PushDeliveryStatus.SKIPPED, "no device")
        old = next(iter(claimed))
        await txn_pool.execute(
            """UPDATE forge_push_deliveries SET settled_at = now() - interval '8 days'
               WHERE notification_id = $1""",
            old,
        )
        now = await db_now(txn_pool)

        assert await deliveries.prune_settled(now - timedelta(days=7)) == 1
        left = await txn_pool.fetch("SELECT notification_id FROM forge_push_deliveries")
        assert len(left) == 2 and old not in {row["notification_id"] for row in left}

    async def test_a_settled_row_must_carry_its_settle_time(self, txn_pool):
        with pytest.raises(asyncpg.CheckViolationError):
            async with txn_pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(
                        """INSERT INTO forge_push_deliveries
                               (instance_id, notification_id, owner_id, payload, status)
                           VALUES ('i', 'n', 'o', '{}'::jsonb, 'sent')"""
                    )


class TestGatewayEndToEnd:
    """One gateway over the real stores; the clock is the database's ``now()``."""

    async def _gateway(self, txn_pool, devices, deliveries, **settings):
        now = await db_now(txn_pool)
        feed, apns = FakeGuildFeed(), FakeApns()
        feed.add_node(THOR)
        feed.add_node(MINI)
        gateway = PushGateway(
            feed, devices, deliveries, apns, gateway_settings(**settings), clock=lambda: now
        )
        await gateway.run_once()
        return gateway, feed, apns, now

    async def test_a_notification_on_another_node_reaches_every_surface(
        self, txn_pool, devices, deliveries
    ):
        await devices.upsert(device("alert-1", environment=PushEnvironment.SANDBOX))
        await devices.upsert(device("la-1", PushTokenKind.LIVE_ACTIVITY, activity_id="act-1"))
        await devices.upsert(device("widget-1", PushTokenKind.WIDGET))
        gateway, feed, apns, now = await self._gateway(
            txn_pool,
            devices,
            deliveries,
            background=BackgroundSettings(enabled=True, priority=5, min_interval_seconds=900.0),
        )
        feed.add(
            guild_notification(
                "1a97554b-96b1-5fac-baa4-5bf53ba35d92",
                instance_id=MINI,
                seq=1,
                kind="decision",
                created_at=now,
            )
        )

        assert await gateway.run_once() == 1

        assert [(push.push_type.value, push.token) for push in apns.sent] == [
            ("alert", "alert-1"),
            ("liveactivity", "la-1"),
            ("widgets", "widget-1"),
            ("background", "alert-1"),
        ]
        alert = apns.sent[0]
        assert alert.environment is PushEnvironment.SANDBOX
        assert alert.payload["deepLink"] == (
            f"lexichat://activity/1a97554b-96b1-5fac-baa4-5bf53ba35d92?inst={MINI}"
            "&session=sess-1&turn=nt_1"
        )
        assert alert.payload["aps"]["interruption-level"] == "time-sensitive"
        state = apns.sent[1].payload["aps"]["content-state"]
        assert [item["id"] for item in state["items"]] == ["1a97554b-96b1-5fac-baa4-5bf53ba35d92"]
        assert state["items"][0]["createdAt"] == now.timestamp() - 978_307_200
        row = await txn_pool.fetchrow("SELECT status, detail, attempts FROM forge_push_deliveries")
        assert tuple(row) == ("sent", "APNs accepted 1 of 1 devices", 1)
        assert await deliveries.load_positions() == {THOR: 0, MINI: 1}

        # Nothing new: no push is repeated; the widget and the background push stay
        # inside their intervals.
        assert await gateway.run_once() == 0
        assert len(apns.sent) == 4

    async def test_dead_tokens_are_deleted_from_the_table(self, txn_pool, devices, deliveries):
        for token, kind in (
            ("alert-gone", PushTokenKind.ALERT),
            ("alert-ok", PushTokenKind.ALERT),
            ("la-gone", PushTokenKind.LIVE_ACTIVITY),
            ("widget-gone", PushTokenKind.WIDGET),
        ):
            await devices.upsert(device(token, kind))
        gateway, feed, apns, now = await self._gateway(txn_pool, devices, deliveries)
        apns.answer("alert-gone", ApnsResult(410, "Unregistered"))
        apns.answer("la-gone", ApnsResult(400, "ExpiredToken"))
        apns.answer("widget-gone", ApnsResult(400, "BadDeviceToken"))
        feed.add(guild_notification("n-1", created_at=now))

        await gateway.run_once()

        left = await txn_pool.fetch("SELECT token FROM device_tokens ORDER BY token")
        assert [row["token"] for row in left] == ["alert-ok"]
        assert gateway.stats.tokens_pruned == 3
        assert await txn_pool.fetchval("SELECT status FROM forge_push_deliveries") == "sent"

    async def test_rate_limit_and_widget_interval_hold_in_the_database(
        self, txn_pool, devices, deliveries
    ):
        from tests.support.push_fakes import alert_settings
        from volundr.domain.notifications import NotificationRateLimit

        await devices.upsert(device("alert-1"))
        await devices.upsert(device("widget-1", PushTokenKind.WIDGET))
        gateway, feed, apns, now = await self._gateway(
            txn_pool,
            devices,
            deliveries,
            alerts=alert_settings(
                rate_limit=NotificationRateLimit(max_count=2, window_seconds=3600)
            ),
        )

        for seq in (1, 2, 3):
            feed.add(guild_notification(f"n-{seq}", seq=seq, created_at=now))
            await gateway.run_once()

        statuses = await txn_pool.fetch(
            """SELECT notification_id, status, detail FROM forge_push_deliveries
               ORDER BY notification_id"""
        )
        assert [(row["notification_id"], row["status"]) for row in statuses] == [
            ("n-1", "sent"),
            ("n-2", "sent"),
            ("n-3", "suppressed"),
        ]
        assert statuses[2]["detail"] == "Rate limit: at most 2 alerts per 3600s"
        # Three notifications, one widget reload: the other two are held back and owed.
        assert len(apns.of_type("widgets")) == 1
        assert await txn_pool.fetchval(
            "SELECT push_due FROM device_tokens WHERE token = 'widget-1'"
        )
