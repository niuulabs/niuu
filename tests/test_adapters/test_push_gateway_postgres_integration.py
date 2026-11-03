"""Real PostgreSQL checks for the push gateway: its migration through the production
startup runner, and what only separate connections can show (concurrent claims).

Opt-in: set ``FORGE_HISTORY_TEST_DATABASE_URL`` to a disposable database. Each test
gets its own schema, migrated through the production startup runner, and drops it.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from cli.resources import ordered_migration_files
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
from volundr.adapters.outbound.startup_schema import apply_startup_migrations
from volundr.domain.push import ApnsResult, PushTokenKind
from volundr.domain.push_ports import PushDeliveryStatus
from volundr.domain.services.push_gateway import PushGateway

pytestmark = pytest.mark.integration

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"
MIGRATION = "000099_push_gateway.up.sql"


@pytest_asyncio.fixture
async def pool():
    dsn = os.environ.get("FORGE_HISTORY_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Set FORGE_HISTORY_TEST_DATABASE_URL for isolated PostgreSQL checks")
    schema = "forge_push_test_" + uuid4().hex
    admin = await asyncpg.connect(dsn)
    await admin.execute(f'CREATE SCHEMA "{schema}"')
    created = None
    try:
        created = await asyncpg.create_pool(
            dsn, min_size=1, max_size=8, server_settings={"search_path": schema}
        )
        async with created.acquire() as conn:
            await apply_startup_migrations(conn, ordered_migration_files(MIGRATIONS))
        yield created
    finally:
        if created is not None:
            await created.close()
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


async def test_the_startup_runner_applies_the_migration_once(pool):
    files = ordered_migration_files(MIGRATIONS)
    assert MIGRATION in [path.name for path in files]
    async with pool.acquire() as conn:
        # A second start verifies the ledger and changes nothing.
        await apply_startup_migrations(conn, files)
        indexes = {
            row["indexname"]
            for row in await conn.fetch(
                "SELECT indexname FROM pg_indexes WHERE schemaname = current_schema()"
            )
        }
    assert {
        "idx_device_tokens_token_kind",
        "idx_device_tokens_owner_kind",
        "idx_device_tokens_push_due",
        "idx_forge_push_deliveries_due",
        "idx_forge_push_deliveries_sent",
        "idx_forge_push_deliveries_settled",
    } <= indexes


async def test_concurrent_workers_never_claim_the_same_alert(pool):
    deliveries = PostgresPushDeliveryRepository(pool, max_detail_chars=200)
    notifications = [guild_notification(f"n-{seq}", seq=seq) for seq in range(1, 41)]
    await deliveries.record(notifications, {THOR: 40})

    batches = await asyncio.gather(
        *(deliveries.claim_due(limit=10, lease_seconds=60) for _ in range(6))
    )

    claimed = [item.notification.id for batch in batches for item in batch]
    assert len(claimed) == 40 and len(set(claimed)) == 40
    assert all(item.attempts == 1 for batch in batches for item in batch)
    assert await deliveries.claim_due(limit=10, lease_seconds=60) == []


async def test_concurrent_ingest_of_one_page_records_each_notification_once(pool):
    deliveries = PostgresPushDeliveryRepository(pool, max_detail_chars=200)
    page = [guild_notification(f"n-{seq}", seq=seq) for seq in range(1, 21)]

    results = await asyncio.gather(*(deliveries.record(page, {THOR: 20}) for _ in range(5)))

    fresh = [item.id for result in results for item in result]
    assert sorted(fresh) == sorted(item.id for item in page)
    assert await pool.fetchval("SELECT COUNT(*) FROM forge_push_deliveries") == 20
    assert await deliveries.load_positions() == {THOR: 20}


async def test_only_one_of_many_concurrent_claims_wins_a_throttled_push(pool):
    devices = PostgresDeviceTokenRepository(pool)
    widget = await devices.upsert(device("widget-1", PushTokenKind.WIDGET))

    results = await asyncio.gather(
        *(devices.claim_push(widget.id, min_interval_seconds=300) for _ in range(8))
    )

    assert sorted(results) == [False] * 7 + [True]
    # The refused claims left the reload owed; it is handed out once the interval is over.
    owed = await asyncio.gather(
        *(devices.claim_owed_pushes(PushTokenKind.WIDGET, min_interval_seconds=0) for _ in range(4))
    )
    assert sorted(len(batch) for batch in owed) == [0, 0, 0, 1]


async def test_concurrent_registrations_of_one_token_leave_one_row(pool):
    devices = PostgresDeviceTokenRepository(pool)

    stored = await asyncio.gather(
        *(devices.upsert(device("alert-1", owner_id=f"owner-{index % 2}")) for index in range(8))
    )

    assert len({item.id for item in stored}) == 1
    assert await pool.fetchval("SELECT COUNT(*) FROM device_tokens") == 1


async def test_two_gateways_on_one_database_alert_once(pool):
    """Two processes (or two hosts) sharing the store: every notification alerts once."""
    devices = PostgresDeviceTokenRepository(pool)
    deliveries = PostgresPushDeliveryRepository(pool, max_detail_chars=200)
    await devices.upsert(device("alert-1"))
    await devices.upsert(device("widget-1", PushTokenKind.WIDGET))
    feed, apns = FakeGuildFeed(), FakeApns()
    feed.add_node(THOR)
    feed.add_node(MINI)
    gateways = [PushGateway(feed, devices, deliveries, apns, gateway_settings()) for _ in range(2)]
    await asyncio.gather(*(gateway.run_once() for gateway in gateways))

    now = datetime.now(UTC)
    for seq in range(1, 13):
        feed.add(
            guild_notification(f"n-{seq}", seq=seq, created_at=now),
            guild_notification(f"m-{seq}", instance_id=MINI, seq=seq, created_at=now),
        )
    await asyncio.gather(*(gateway.run_once() for gateway in gateways))

    alerts = [push.payload["notification_id"] for push in apns.of_type("alert")]
    assert len(alerts) == 24 and len(set(alerts)) == 24
    assert len(apns.of_type("widgets")) == 1
    rows = await pool.fetch("SELECT status, attempts FROM forge_push_deliveries")
    assert {(row["status"], row["attempts"]) for row in rows} == {("sent", 1)}
    assert await deliveries.load_positions() == {THOR: 12, MINI: 12}


async def test_a_dead_token_is_deleted_and_a_retry_is_claimed_when_due(pool):
    devices = PostgresDeviceTokenRepository(pool)
    deliveries = PostgresPushDeliveryRepository(pool, max_detail_chars=200)
    await devices.upsert(device("alert-gone"))
    await devices.upsert(device("alert-flaky", owner_id="other"))
    feed, apns = FakeGuildFeed(), FakeApns()
    feed.add_node(THOR)
    gateway = PushGateway(feed, devices, deliveries, apns, gateway_settings())
    await gateway.run_once()
    apns.answer("alert-gone", ApnsResult(410, "Unregistered"))
    apns.answer("alert-flaky", ApnsResult(503, "ServiceUnavailable"))
    now = datetime.now(UTC)
    feed.add(
        guild_notification("n-1", seq=1, created_at=now),
        guild_notification("n-2", seq=2, owner_id="other", created_at=now),
    )

    await gateway.run_once()

    assert [row["token"] for row in await pool.fetch("SELECT token FROM device_tokens")] == [
        "alert-flaky"
    ]
    rows = {
        row["notification_id"]: row
        for row in await pool.fetch("SELECT * FROM forge_push_deliveries")
    }
    assert rows["n-1"]["status"] == PushDeliveryStatus.FAILED.value
    assert rows["n-1"]["detail"] == "APNs: HTTP 410 Unregistered"
    assert rows["n-2"]["status"] == PushDeliveryStatus.PENDING.value
    assert rows["n-2"]["detail"] == "APNs: HTTP 503 ServiceUnavailable"
    assert rows["n-2"]["next_attempt_at"] > now

    # Not due yet: another tick does not send it again.
    await gateway.run_once()
    assert len(apns.of_type("alert")) == 2

    await pool.execute(
        "UPDATE forge_push_deliveries SET next_attempt_at = now() WHERE notification_id = 'n-2'"
    )
    await gateway.run_once()
    sent = await pool.fetchrow(
        "SELECT status, attempts FROM forge_push_deliveries WHERE notification_id = 'n-2'"
    )
    assert tuple(sent) == ("sent", 2)
