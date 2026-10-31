"""Real PostgreSQL checks for the notification migration and adapters.

Opt-in: set ``FORGE_HISTORY_TEST_DATABASE_URL`` to a disposable database. Each test
gets its own schema, migrated through the production startup runner, and drops it.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from cli.resources import ordered_migration_files
from niuu.domain.notifications import (
    NotificationKind,
    NotificationLink,
    NotificationSeverity,
    NotificationSource,
)
from volundr.adapters.outbound.pg_session_event_log import PostgresSessionEventLog
from volundr.adapters.outbound.postgres_notifications import (
    PostgresNotificationDeliveryRepository,
    PostgresNotificationRepository,
    PostgresNotificationRuleRepository,
)
from volundr.adapters.outbound.startup_schema import apply_startup_migrations
from volundr.domain.models import SessionLogEntry
from volundr.domain.notifications import (
    DeliveryStatus,
    NotificationCandidate,
    NotificationQuery,
    NotificationQuietHours,
    NotificationReadStateConflictError,
    NotificationRule,
    NotificationRuleMatch,
    NotificationScope,
)

pytestmark = pytest.mark.integration
MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"
MIGRATION = "000084_forge_notifications"


@pytest_asyncio.fixture
async def pool():
    dsn = os.environ.get("FORGE_HISTORY_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Set FORGE_HISTORY_TEST_DATABASE_URL for isolated PostgreSQL checks")
    schema = "forge_notifications_test_" + uuid4().hex
    admin = await asyncpg.connect(dsn)
    await admin.execute(f'CREATE SCHEMA "{schema}"')
    created = None
    try:
        created = await asyncpg.create_pool(
            dsn, min_size=1, max_size=6, server_settings={"search_path": schema}
        )
        async with created.acquire() as conn:
            await apply_startup_migrations(conn, ordered_migration_files(MIGRATIONS))
        yield created
    finally:
        if created is not None:
            await created.close()
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


def candidate(key: str, **overrides) -> NotificationCandidate:
    values = {
        "dedupe_key": key,
        "owner_id": "owner-a",
        "tenant_id": "tenant-a",
        "kind": NotificationKind.MILESTONE,
        "severity": NotificationSeverity.INFO,
        "source": NotificationSource.AGENT,
        "title": f"title {key}",
        "body": "body",
        "links": [NotificationLink(label="PR", url="https://example.com/pr/1")],
        "metadata": {"turn_id": key},
    }
    values.update(overrides)
    return NotificationCandidate(**values)


def rule(owner: str = "owner-a", **overrides) -> NotificationRule:
    now = datetime.now(UTC)
    values = {
        "id": uuid4(),
        "owner_id": owner,
        "name": "ops",
        "sink": "ops-webhook",
        "match": NotificationRuleMatch(),
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return NotificationRule(**values)


OWNER = NotificationScope(user_id="owner-a", tenant_id="tenant-a", is_admin=False)
ADMIN = NotificationScope(user_id="admin", tenant_id="tenant-a", is_admin=True)


async def _tables(pool) -> set[str]:
    rows = await pool.fetch(
        """SELECT table_name FROM information_schema.tables
           WHERE table_schema = current_schema() AND table_name LIKE 'forge_notification%'"""
    )
    return {row["table_name"] for row in rows}


async def test_migration_is_idempotent_and_reversible(pool):
    expected = {
        "forge_notifications",
        "forge_notification_reads",
        "forge_notification_read_states",
        "forge_notification_rules",
        "forge_notification_deliveries",
    }
    assert await _tables(pool) == expected
    up = (MIGRATIONS / f"{MIGRATION}.up.sql").read_text()
    down = (MIGRATIONS / f"{MIGRATION}.down.sql").read_text()
    await pool.execute(up)  # re-applying is a no-op
    item_up = (MIGRATIONS / "000086_forge_notification_reads.up.sql").read_text()
    item_down = (MIGRATIONS / "000086_forge_notification_reads.down.sql").read_text()
    await pool.execute(item_up)  # both migrations are idempotent
    await pool.execute(item_down)  # children before the parent migration
    await pool.execute(item_down)
    await pool.execute(down)
    assert await _tables(pool) == set()
    await pool.execute(down)  # dropping twice is a no-op
    await pool.execute(up)
    await pool.execute(up)
    await pool.execute(item_up)
    assert await _tables(pool) == expected


async def test_projection_dedupes_orders_and_schedules_matching_rules(pool):
    repo = PostgresNotificationRepository(pool)
    rules = PostgresNotificationRuleRepository(pool)
    deliveries = PostgresNotificationDeliveryRepository(pool, max_error_chars=50)
    matching = await rules.create(rule(match=NotificationRuleMatch(kinds=["milestone"])))
    await rules.create(rule(name="off", enabled=False))
    await rules.create(rule(name="errors", match=NotificationRuleMatch(kinds=["error"])))
    await rules.create(rule(owner="owner-b", name="other owner"))

    created = await repo.project([candidate("a"), candidate("b"), candidate("a")])
    assert [n.dedupe_key for n in created] == ["a", "b"]
    assert created[0].seq < created[1].seq
    assert created[0].created_at <= created[1].created_at
    assert created[0].links[0].url == "https://example.com/pr/1"
    assert created[0].metadata == {"turn_id": "a"}

    assert await repo.project([candidate("a"), candidate("b")]) == []
    rows = await deliveries.list_for_notification(created[0].id)
    assert [(row.rule_id, row.status, row.sink) for row in rows] == [
        (matching.id, DeliveryStatus.PENDING, "ops-webhook")
    ]
    count = await pool.fetchval("SELECT COUNT(*) FROM forge_notification_deliveries")
    assert count == 2  # one per new notification, none on the replay


async def test_concurrent_projection_commits_in_seq_order(pool):
    repo = PostgresNotificationRepository(pool)
    results = await asyncio.gather(
        *(repo.project([candidate(f"c{i}"), candidate("shared")]) for i in range(8))
    )
    created = [n for batch in results for n in batch]
    assert sorted(n.dedupe_key for n in created) == sorted([f"c{i}" for i in range(8)] + ["shared"])
    ordered = sorted(created, key=lambda n: n.seq)
    assert [n.created_at for n in ordered] == sorted(n.created_at for n in ordered)


async def test_feed_paging_filters_scope_and_counts(pool):
    repo = PostgresNotificationRepository(pool)
    sid = uuid4()
    await repo.project(
        [
            candidate("1", session_id=sid, project_id="p1"),
            candidate("2", severity=NotificationSeverity.WARNING, kind=NotificationKind.ERROR),
            candidate("3", source=NotificationSource.SYSTEM, kind=NotificationKind.REPLY_READY),
            candidate("4", owner_id="owner-b"),
            candidate("5", owner_id="owner-c", tenant_id="tenant-z"),
            candidate("6", owner_id="owner-d", tenant_id=None),
        ]
    )
    first = await repo.list_feed(OWNER, NotificationQuery(limit=2), read_through_seq=0)
    assert [n.dedupe_key for n in first.items] == ["3", "2"]
    assert first.next_before == first.items[-1].seq
    second = await repo.list_feed(
        OWNER, NotificationQuery(limit=2, before=first.next_before), read_through_seq=0
    )
    assert [n.dedupe_key for n in second.items] == ["1"]
    assert second.next_before is None
    ascending = await repo.list_feed(
        OWNER, NotificationQuery(limit=10, after=0), read_through_seq=0
    )
    assert [n.dedupe_key for n in ascending.items] == ["1", "2", "3"]
    assert ascending.next_before is None

    admin = await repo.list_feed(ADMIN, NotificationQuery(limit=10), read_through_seq=0)
    assert [n.dedupe_key for n in admin.items] == ["6", "4", "3", "2", "1"]

    async def keys(**filters) -> list[str]:
        page = await repo.list_feed(
            OWNER, NotificationQuery(limit=10, **filters), read_through_seq=second.items[0].seq
        )
        return [n.dedupe_key for n in page.items]

    assert await keys(kinds=(NotificationKind.ERROR,)) == ["2"]
    assert await keys(min_severity=NotificationSeverity.WARNING) == ["2"]
    assert await keys(sources=(NotificationSource.SYSTEM,)) == ["3"]
    assert await keys(session_id=sid) == ["1"]
    assert await keys(project_id="p1") == ["1"]
    assert await keys(unread=True) == ["3", "2"]
    assert await keys(unread=False) == ["1"]

    head, unread = await repo.feed_counts(OWNER, read_through_seq=first.items[1].seq)
    assert head == first.items[0].seq and unread == 1
    admin_head, admin_unread = await repo.feed_counts(ADMIN, read_through_seq=0)
    assert admin_unread == 5 and admin_head > head
    session_rows = await repo.list_for_session(sid, after=0, limit=10)
    assert [n.dedupe_key for n in session_rows] == ["1"]
    assert await repo.get(session_rows[0].id) == session_rows[0]
    assert await repo.get(uuid4()) is None


async def test_watermark_compare_and_swap_moves_forward_only(pool):
    repo = PostgresNotificationRepository(pool)
    assert (await repo.get_watermark("reader")).revision == 0
    first = await repo.advance_watermark("reader", read_through_seq=5, expected_revision=0)
    assert (first.read_through_seq, first.revision) == (5, 1)
    with pytest.raises(NotificationReadStateConflictError):
        await repo.advance_watermark("reader", read_through_seq=9, expected_revision=0)
    back = await repo.advance_watermark("reader", read_through_seq=2, expected_revision=1)
    assert (back.read_through_seq, back.revision) == (5, 2)
    with pytest.raises(NotificationReadStateConflictError):
        await repo.advance_watermark("reader", read_through_seq=9, expected_revision=1)
    assert await repo.get_watermark("reader") == back


async def test_rule_crud_round_trips_json_columns(pool):
    rules = PostgresNotificationRuleRepository(pool)
    quiet = NotificationQuietHours(start="22:00", end="07:00", timezone="Europe/London")
    stored = await rules.create(
        rule(
            match=NotificationRuleMatch(kinds=["decision"], min_severity="warning"),
            quiet_hours=quiet,
            config={"rate_limit": {"max_count": 3, "window_seconds": 60}},
            integration_connection_id="conn-1",
        )
    )
    assert stored.quiet_hours == quiet
    assert stored.rate_limit.max_count == 3
    assert await rules.get("owner-a", stored.id) == stored
    assert await rules.get("owner-b", stored.id) is None
    changed = stored.model_copy(update={"enabled": False, "name": "renamed"})
    updated = await rules.update(changed)
    assert updated.name == "renamed" and not updated.enabled
    assert await rules.update(changed.model_copy(update={"owner_id": "owner-b"})) is None
    assert await rules.list_enabled_for_owner("owner-a") == []
    assert [r.id for r in await rules.list_for_owner("owner-a")] == [stored.id]
    assert not await rules.delete("owner-b", stored.id)
    assert await rules.delete("owner-a", stored.id)
    assert await rules.list_for_owner("owner-a") == []


async def test_create_if_absent_keeps_the_first_rule(pool):
    rules = PostgresNotificationRuleRepository(pool)
    first = rule()
    assert (await rules.create_if_absent(first)).id == first.id
    assert await rules.create_if_absent(first.model_copy(update={"name": "second"})) is None
    assert [r.name for r in await rules.list_for_owner("owner-a")] == [first.name]


async def test_outbox_claim_lease_fence_and_settle(pool):
    repo = PostgresNotificationRepository(pool)
    rules = PostgresNotificationRuleRepository(pool)
    outbox = PostgresNotificationDeliveryRepository(pool, max_error_chars=10)
    await rules.create(rule())
    await repo.project([candidate(f"n{i}") for i in range(4)])

    first, second = await asyncio.gather(
        outbox.claim_due(limit=2, lease_seconds=30),
        outbox.claim_due(limit=2, lease_seconds=30),
    )
    claimed = first + second
    assert len({item.delivery.id for item in claimed}) == 4  # SKIP LOCKED: no overlap
    assert all(item.delivery.status is DeliveryStatus.CLAIMED for item in claimed)
    assert all(item.delivery.attempts == 1 for item in claimed)
    assert all(item.rule.sink == "ops-webhook" for item in claimed)
    assert await outbox.claim_due(limit=10, lease_seconds=30) == []

    done, retry, dead, quiet = (item.delivery for item in claimed)
    assert await outbox.mark_delivered(done)
    assert not await outbox.mark_delivered(done)  # already settled
    past = datetime.now(UTC) - timedelta(seconds=1)
    assert await outbox.mark_failed(retry, error="x" * 50, retry_at=past)
    assert await outbox.mark_failed(dead, error="boom", retry_at=None)
    assert await outbox.mark_suppressed(quiet, reason="quiet hours")

    again = await outbox.claim_due(limit=10, lease_seconds=30)
    assert [item.delivery.id for item in again] == [retry.id]
    assert again[0].delivery.attempts == 2 and again[0].delivery.last_error == "x" * 10
    assert not await outbox.mark_delivered(retry)  # stale claim is fenced out
    assert await outbox.mark_delivered(again[0].delivery)

    rows = {row.id: row for row in await outbox.list_for_notification(done.notification_id)}
    assert rows[done.id].status is DeliveryStatus.DELIVERED and rows[done.id].delivered_at
    statuses = await pool.fetch("SELECT id, status FROM forge_notification_deliveries")
    by_id = {row["id"]: row["status"] for row in statuses}
    assert by_id[dead.id] == "dead" and by_id[quiet.id] == "suppressed"


async def test_expired_lease_is_reclaimed(pool):
    repo = PostgresNotificationRepository(pool)
    await PostgresNotificationRuleRepository(pool).create(rule())
    await repo.project([candidate("lease")])
    outbox = PostgresNotificationDeliveryRepository(pool, max_error_chars=10)
    [claim] = await outbox.claim_due(limit=1, lease_seconds=30)
    await pool.execute(
        "UPDATE forge_notification_deliveries SET next_attempt_at = now() - interval '1 second'"
    )
    [reclaim] = await outbox.claim_due(limit=1, lease_seconds=30)
    assert reclaim.delivery.id == claim.delivery.id and reclaim.delivery.attempts == 2
    assert not await outbox.mark_suppressed(claim.delivery, reason="late")


async def test_deleting_a_rule_cascades_its_deliveries(pool):
    repo = PostgresNotificationRepository(pool)
    rules = PostgresNotificationRuleRepository(pool)
    stored = await rules.create(rule())
    [notification] = await repo.project([candidate("cascade")])
    await rules.delete("owner-a", stored.id)
    outbox = PostgresNotificationDeliveryRepository(pool, max_error_chars=10)
    assert await outbox.list_for_notification(notification.id) == []


async def test_event_log_reads_back_exact_seqs(pool):
    log = PostgresSessionEventLog(pool)
    sid = uuid4()
    now = datetime.now(UTC)
    await log.append(
        [
            SessionLogEntry(session_id=sid, seq=seq, kind="k", payload={"n": seq}, ts=now)
            for seq in (1, 2, 5, 9)
        ]
    )
    rows = await log.read_seqs(sid, [9, 2, 7])
    assert [row.seq for row in rows] == [2, 9]
    assert await log.read_seqs(sid, []) == []


async def test_individual_reads_concurrent_retry_isolation_and_watermark_cas(pool):
    repo = PostgresNotificationRepository(pool)
    older, opened, newer = await repo.project(
        [candidate("read-a"), candidate("read-b"), candidate("read-c")]
    )
    await asyncio.gather(*(repo.mark_read(OWNER.user_id, opened.id) for _ in range(6)))
    watermark = await repo.get_watermark(OWNER.user_id)
    assert watermark.revision == 1 and watermark.read_through_seq == 0
    assert await repo.read_ids(OWNER.user_id, [older.id, opened.id, newer.id]) == {opened.id}
    assert await repo.read_ids(ADMIN.user_id, [opened.id]) == set()
    assert await repo.feed_counts(OWNER, read_through_seq=0) == (newer.seq, 2)
    assert await repo.feed_counts(ADMIN, read_through_seq=0) == (newer.seq, 3)
    page = await repo.list_feed(OWNER, NotificationQuery(limit=10, unread=True), read_through_seq=0)
    assert [n.id for n in page.items] == [newer.id, older.id]
    with pytest.raises(NotificationReadStateConflictError):
        await repo.advance_watermark(OWNER.user_id, read_through_seq=newer.seq, expected_revision=0)
    await repo.advance_watermark(OWNER.user_id, read_through_seq=newer.seq, expected_revision=1)
    assert await repo.feed_counts(OWNER, read_through_seq=newer.seq) == (newer.seq, 0)
    await pool.execute("DELETE FROM forge_notifications WHERE id = $1", opened.id)
    assert await repo.read_ids(OWNER.user_id, [opened.id]) == set()
