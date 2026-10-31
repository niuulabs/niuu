"""Notification SQL adapters with a mocked asyncpg boundary (no database)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import asyncpg
import pytest

from niuu.domain.notifications import NotificationKind, NotificationSeverity, NotificationSource
from volundr.adapters.outbound.pg_session_event_log import PostgresSessionEventLog
from volundr.adapters.outbound.postgres_notifications import (
    CLAIM_DUE_SQL,
    FEED_ORDER_LOCK_SQL,
    INSERT_DELIVERY_SQL,
    INSERT_NOTIFICATION_SQL,
    SELECT_RULES_FOR_OWNERS_SQL,
    PostgresNotificationDeliveryRepository,
    PostgresNotificationRepository,
    PostgresNotificationRuleRepository,
    feed_sql,
)
from volundr.domain.notifications import (
    DeliveryStatus,
    NotificationCandidate,
    NotificationDelivery,
    NotificationNotFoundError,
    NotificationQuery,
    NotificationReadStateConflictError,
    NotificationRule,
    NotificationRuleMatch,
    NotificationScope,
    NotificationStoreUnavailableError,
)

NOW = datetime(2026, 9, 23, 10, tzinfo=UTC)
OWNER = NotificationScope(user_id="u", tenant_id="t", is_admin=False)
ADMIN = NotificationScope(user_id="a", tenant_id="t", is_admin=True)


def _pool():
    pool, conn = MagicMock(), MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    conn.transaction.return_value.__aenter__ = AsyncMock()
    conn.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    for target in (pool, conn):
        target.execute = AsyncMock()
        target.executemany = AsyncMock()
        target.fetch = AsyncMock(return_value=[])
        target.fetchrow = AsyncMock(return_value=None)
        target.fetchval = AsyncMock()
    return pool, conn


def _candidate(key: str, **overrides) -> NotificationCandidate:
    values = {
        "dedupe_key": key,
        "owner_id": "u",
        "kind": "milestone",
        "severity": "info",
        "source": "agent",
        "title": "t\x00itle",
        "links": [{"label": "l", "url": "x"}],
        "metadata": {"turn_id": key},
    }
    values.update(overrides)
    return NotificationCandidate(**values)


def _notification_row(candidate: NotificationCandidate, seq: int, *, as_text: bool = True) -> dict:
    links = [link.model_dump(mode="json") for link in candidate.links]
    return {
        "id": candidate.id,
        "seq": seq,
        "dedupe_key": candidate.dedupe_key,
        "session_id": candidate.session_id,
        "session_seq": candidate.session_seq,
        "session_name": candidate.session_name,
        "owner_id": candidate.owner_id,
        "tenant_id": candidate.tenant_id,
        "project_id": candidate.project_id,
        "kind": candidate.kind.value,
        "severity": candidate.severity.value,
        "source": candidate.source.value,
        "title": "title",
        "body": candidate.body,
        "links": json.dumps(links) if as_text else links,
        "engine": None,
        "model": None,
        "correlation_id": None,
        "metadata": json.dumps(candidate.metadata) if as_text else None,
        "created_at": NOW,
    }


def _rule(**overrides) -> NotificationRule:
    values = {
        "id": uuid4(),
        "owner_id": "u",
        "name": "r",
        "sink": "ops",
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return NotificationRule(**values)


def _rule_row(rule: NotificationRule) -> dict:
    return {
        "id": rule.id,
        "owner_id": rule.owner_id,
        "name": rule.name,
        "enabled": rule.enabled,
        "match": json.dumps(rule.match.model_dump(mode="json")),
        "sink": rule.sink,
        "integration_connection_id": rule.integration_connection_id,
        "config": rule.config,
        "quiet_hours": (
            json.dumps(rule.quiet_hours.model_dump(mode="json")) if rule.quiet_hours else None
        ),
        "created_at": rule.created_at,
        "updated_at": rule.updated_at,
    }


def _delivery(**overrides) -> NotificationDelivery:
    values = {
        "id": uuid4(),
        "notification_id": uuid4(),
        "rule_id": uuid4(),
        "sink": "ops",
        "status": DeliveryStatus.CLAIMED,
        "attempts": 2,
        "next_attempt_at": NOW,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return NotificationDelivery(**values)


def _delivery_row(delivery: NotificationDelivery) -> dict:
    return {**delivery.model_dump(), "status": delivery.status.value}


class TestProject:
    async def test_locks_inserts_and_schedules_only_new_rows(self):
        pool, conn = _pool()
        a, b = _candidate("a"), _candidate("b", kind="error")
        matching = _rule(match=NotificationRuleMatch(kinds=["milestone"]))
        conn.fetchrow.side_effect = [_notification_row(a, 5), None]
        conn.fetch.return_value = [_rule_row(matching)]

        created = await PostgresNotificationRepository(pool).project([a, b])

        assert [n.dedupe_key for n in created] == ["a"]
        assert created[0].metadata == {"turn_id": "a"} and created[0].links[0].url == "x"
        assert conn.execute.await_args_list[0].args == (FEED_ORDER_LOCK_SQL,)
        insert = conn.fetchrow.await_args_list[0].args
        assert insert[0] == INSERT_NOTIFICATION_SQL and insert[1] == a.id
        assert insert[12] == "t�itle"  # NUL scrubbed from text columns
        assert conn.fetch.await_args.args == (SELECT_RULES_FOR_OWNERS_SQL, ["u"])
        [[sql, args]] = [call.args for call in conn.executemany.await_args_list]
        assert sql == INSERT_DELIVERY_SQL
        assert [(row[1], row[2], row[3]) for row in args] == [(a.id, matching.id, "ops")]

    async def test_replay_skips_rule_lookup(self):
        pool, conn = _pool()
        assert await PostgresNotificationRepository(pool).project([_candidate("a")]) == []
        conn.fetch.assert_not_awaited()
        conn.executemany.assert_not_awaited()

    async def test_no_matching_rules_inserts_no_deliveries(self):
        pool, conn = _pool()
        a = _candidate("a")
        conn.fetchrow.side_effect = [_notification_row(a, 1, as_text=False)]
        conn.fetch.return_value = [_rule_row(_rule(enabled=False))]
        [created] = await PostgresNotificationRepository(pool).project([a])
        assert created.metadata == {}
        conn.executemany.assert_not_awaited()

    async def test_empty_batch_never_opens_a_transaction(self):
        pool, _ = _pool()
        assert await PostgresNotificationRepository(pool).project([]) == []
        pool.acquire.assert_not_called()

    @pytest.mark.parametrize(
        "error",
        [
            ConnectionRefusedError("refused"),
            TimeoutError(),
            asyncpg.exceptions.CannotConnectNowError("starting up"),
            asyncpg.exceptions.TooManyConnectionsError("full"),
        ],
    )
    async def test_connection_failures_become_retryable(self, error):
        pool, conn = _pool()
        conn.execute.side_effect = error
        with pytest.raises(NotificationStoreUnavailableError):
            await PostgresNotificationRepository(pool).project([_candidate("a")])

    async def test_query_defects_are_not_masked_as_outages(self):
        pool, conn = _pool()
        conn.fetchrow.side_effect = asyncpg.exceptions.UndefinedColumnError("no column")
        with pytest.raises(asyncpg.exceptions.UndefinedColumnError):
            await PostgresNotificationRepository(pool).project([_candidate("a")])


class TestFeed:
    def test_owner_scope_and_all_filters(self):
        sid = uuid4()
        query = NotificationQuery(
            limit=5,
            before=9,
            kinds=(NotificationKind.MILESTONE,),
            min_severity=NotificationSeverity.WARNING,
            sources=(NotificationSource.AGENT,),
            session_id=sid,
            project_id="p",
            unread=True,
        )
        sql, params = feed_sql(OWNER, query, read_through_seq=4)
        assert sql.startswith("SELECT * FROM forge_notifications WHERE owner_id = $1")
        assert sql.endswith("ORDER BY seq DESC LIMIT $10")
        assert params == [
            "u",
            ["milestone"],
            ["warning", "critical"],
            ["agent"],
            sid,
            "p",
            4,
            "u",
            9,
            6,
        ]

    def test_admin_scope_ascending_and_read_filter(self):
        sql, params = feed_sql(
            ADMIN, NotificationQuery(limit=2, after=3, unread=False), read_through_seq=7
        )
        assert "(owner_id = $1 OR COALESCE(tenant_id, '') IN ('', $2))" in sql
        assert "seq <= $3" in sql and "seq > $5" in sql and "ORDER BY seq ASC" in sql
        assert params == ["a", "t", 7, "a", 3, 3]

    async def test_list_feed_trims_the_probe_row(self):
        pool, _ = _pool()
        rows = [_notification_row(_candidate(str(i)), 10 - i) for i in range(3)]
        pool.fetch.return_value = rows
        repo = PostgresNotificationRepository(pool)
        page = await repo.list_feed(OWNER, NotificationQuery(limit=2), read_through_seq=0)
        assert [n.seq for n in page.items] == [10, 9] and page.next_before == 9
        ascending = await repo.list_feed(
            OWNER, NotificationQuery(limit=2, after=0), read_through_seq=0
        )
        assert ascending.next_before is None
        pool.fetch.return_value = rows[:1]
        last = await repo.list_feed(OWNER, NotificationQuery(limit=2), read_through_seq=0)
        assert last.next_before is None

    async def test_get_session_list_and_counts(self):
        pool, _ = _pool()
        repo = PostgresNotificationRepository(pool)
        assert await repo.get(uuid4()) is None
        row = _notification_row(_candidate("a"), 1)
        pool.fetchrow.return_value = row
        assert (await repo.get(row["id"])).seq == 1
        pool.fetch.return_value = [row]
        sid = uuid4()
        assert len(await repo.list_for_session(sid, after=0, limit=5)) == 1
        assert pool.fetch.await_args.args[1:] == (sid, 0, 5)
        pool.fetchrow.return_value = {"head_seq": 9, "unread_count": 4}
        assert await repo.feed_counts(ADMIN, read_through_seq=5) == (9, 4)
        sql, *params = pool.fetchrow.await_args.args
        assert params == ["a", "t", 5, "a"] and "seq > $3" in sql
        assert "NOT EXISTS" in sql and "r.user_id = $4" in sql


class TestWatermark:
    async def test_missing_row_is_zero(self):
        pool, _ = _pool()
        watermark = await PostgresNotificationRepository(pool).get_watermark("u")
        assert (watermark.read_through_seq, watermark.revision) == (0, 0)
        pool.fetchrow.return_value = {"read_through_seq": 4, "revision": 2}
        assert (await PostgresNotificationRepository(pool).get_watermark("u")).revision == 2

    async def test_first_write_upserts_guarded_on_revision_zero(self):
        pool, _ = _pool()
        pool.fetchrow.return_value = {"read_through_seq": 3, "revision": 1}
        result = await PostgresNotificationRepository(pool).advance_watermark(
            "u", read_through_seq=3, expected_revision=0
        )
        sql = pool.fetchrow.await_args.args[0]
        assert "ON CONFLICT (user_id) DO UPDATE" in sql and "revision = 0" in sql
        assert result.revision == 1

    async def test_later_write_is_compare_and_swap(self):
        pool, _ = _pool()
        with pytest.raises(NotificationReadStateConflictError):
            await PostgresNotificationRepository(pool).advance_watermark(
                "u", read_through_seq=3, expected_revision=4
            )
        sql, *params = pool.fetchrow.await_args.args
        assert "GREATEST(read_through_seq, $2)" in sql and params == ["u", 3, 4]


class TestRules:
    async def test_crud(self):
        pool, _ = _pool()
        repo = PostgresNotificationRuleRepository(pool)
        rule = _rule(
            quiet_hours={"start": "22:00", "end": "07:00", "timezone": "UTC"},
            config={"rate_limit": {"max_count": 1, "window_seconds": 5}},
        )
        pool.fetchrow.return_value = _rule_row(rule)
        assert await repo.create(rule) == rule
        args = pool.fetchrow.await_args.args
        assert json.loads(args[5]) == rule.match.model_dump(mode="json")
        assert json.loads(args[9])["timezone"] == "UTC" and args[10] == NOW
        assert await repo.update(rule) == rule
        assert await repo.get("u", rule.id) == rule
        pool.fetchrow.return_value = None
        assert await repo.update(rule) is None
        assert await repo.get("u", rule.id) is None
        pool.fetch.return_value = [_rule_row(_rule())]
        assert len(await repo.list_for_owner("u")) == 1
        assert len(await repo.list_enabled_for_owner("u")) == 1
        assert "AND enabled" in pool.fetch.await_args.args[0]
        pool.execute.return_value = "DELETE 1"
        assert await repo.delete("u", rule.id)
        pool.execute.return_value = "DELETE 0"
        assert not await repo.delete("u", rule.id)

    async def test_create_if_absent(self):
        pool, _ = _pool()
        repo = PostgresNotificationRuleRepository(pool)
        rule = _rule()
        pool.fetchrow.return_value = _rule_row(rule)
        assert await repo.create_if_absent(rule) == rule
        assert "ON CONFLICT (id) DO NOTHING" in pool.fetchrow.await_args.args[0]
        pool.fetchrow.return_value = None
        assert await repo.create_if_absent(rule) is None


class TestOutbox:
    def test_rejects_invalid_configuration(self):
        pool, _ = _pool()
        with pytest.raises(ValueError, match="max_error_chars"):
            PostgresNotificationDeliveryRepository(pool, max_error_chars=0)

    @pytest.mark.parametrize(("limit", "lease"), [(0, 10.0), (1, 0.0)])
    async def test_claim_rejects_invalid_arguments(self, limit, lease):
        pool, _ = _pool()
        repo = PostgresNotificationDeliveryRepository(pool, max_error_chars=10)
        with pytest.raises(ValueError):
            await repo.claim_due(limit=limit, lease_seconds=lease)

    async def test_claim_joins_notification_and_rule(self):
        pool, conn = _pool()
        candidate = _candidate("a")
        rule = _rule()
        delivery = _delivery(notification_id=candidate.id, rule_id=rule.id)
        conn.fetch.side_effect = [
            [_delivery_row(delivery)],
            [_notification_row(candidate, 3)],
            [_rule_row(rule)],
        ]
        repo = PostgresNotificationDeliveryRepository(pool, max_error_chars=10)
        [claimed] = await repo.claim_due(limit=5, lease_seconds=30)
        assert conn.fetch.await_args_list[0].args == (CLAIM_DUE_SQL, 5, 30.0)
        assert claimed.delivery == delivery
        assert claimed.notification.seq == 3 and claimed.rule == rule

    async def test_nothing_due(self):
        pool, conn = _pool()
        repo = PostgresNotificationDeliveryRepository(pool, max_error_chars=10)
        assert await repo.claim_due(limit=5, lease_seconds=1) == []
        assert conn.fetch.await_count == 1

    async def test_settles_are_fenced_by_attempts(self):
        pool, _ = _pool()
        repo = PostgresNotificationDeliveryRepository(pool, max_error_chars=4)
        delivery = _delivery()
        pool.execute.return_value = "UPDATE 1"
        assert await repo.mark_delivered(delivery)
        assert pool.execute.await_args.args[1:] == (delivery.id, 2)
        retry_at = datetime.now(UTC)
        assert await repo.mark_failed(delivery, error="timeout!", retry_at=retry_at)
        assert pool.execute.await_args.args[3:] == ("failed", retry_at, "time")
        assert await repo.mark_failed(delivery, error="gone", retry_at=None)
        assert pool.execute.await_args.args[3] == "dead"
        pool.execute.return_value = "UPDATE 0"
        assert not await repo.mark_suppressed(delivery, reason="quiet hours")
        assert pool.execute.await_args.args[3] == "quie"
        assert "attempts = $2 AND status = 'claimed'" in pool.execute.await_args.args[0]

    async def test_list_for_notification(self):
        pool, _ = _pool()
        delivery = _delivery()
        pool.fetch.return_value = [_delivery_row(delivery)]
        repo = PostgresNotificationDeliveryRepository(pool, max_error_chars=4)
        assert await repo.list_for_notification(delivery.notification_id) == [delivery]

    async def test_count_delivered_since_counts_one_rules_recent_successes(self):
        pool, _ = _pool()
        pool.fetchval.return_value = 3
        repo = PostgresNotificationDeliveryRepository(pool, max_error_chars=4)
        rule_id = uuid4()
        assert await repo.count_delivered_since(rule_id, NOW) == 3
        sql = pool.fetchval.await_args.args[0]
        assert "status = 'delivered' AND delivered_at >= $2" in sql
        assert pool.fetchval.await_args.args[1:] == (rule_id, NOW)
        pool.fetchval.return_value = None
        assert await repo.count_delivered_since(rule_id, NOW) == 0


async def test_event_log_reads_exact_seqs_in_one_query():
    pool, _ = _pool()
    sid = uuid4()
    pool.fetch.return_value = [
        {
            "session_id": sid,
            "seq": 2,
            "kind": "k",
            "role": None,
            "request_id": None,
            "payload": '{"a": 1}',
            "ts": NOW,
        }
    ]
    log = PostgresSessionEventLog(pool)
    [entry] = await log.read_seqs(sid, [5, 2, 2])
    assert entry.payload == {"a": 1}
    assert "seq = ANY($2::bigint[])" in pool.fetch.await_args.args[0]
    assert pool.fetch.await_args.args[1:] == (sid, [2, 5])
    assert await log.read_seqs(sid, []) == []


class TestIndividualRead:
    async def test_read_ids_are_reader_and_page_scoped(self):
        pool, _ = _pool()
        repo = PostgresNotificationRepository(pool)
        assert await repo.read_ids("u", []) == set()
        pool.fetch.assert_not_awaited()
        ids = [uuid4(), uuid4()]
        pool.fetch.return_value = [{"notification_id": ids[1]}]
        assert await repo.read_ids("u", ids) == {ids[1]}
        sql, *params = pool.fetch.await_args.args
        assert "user_id = $1" in sql and "ANY($2::uuid[])" in sql
        assert params == ["u", ids]

    @pytest.mark.parametrize("inserted", [True, False])
    async def test_exact_id_idempotent_transaction_serializes_with_watermarks(self, inserted):
        pool, conn = _pool()
        nid = uuid4()
        conn.fetchrow.side_effect = [
            {"revision": 5},
            {"notification_id": nid} if inserted else None,
        ]
        await PostgresNotificationRepository(pool).mark_read("reader", nid)
        conn.transaction.assert_called_once()
        assert "FOR UPDATE" in conn.fetchrow.await_args_list[0].args[0]
        sql, *params = conn.fetchrow.await_args_list[1].args
        assert "ON CONFLICT DO NOTHING" in sql and params == ["reader", nid]
        assert conn.execute.await_count == (2 if inserted else 1)
        assert all("read_through_seq" not in c.args[0] for c in conn.execute.await_args_list)
        if inserted:
            assert "revision = revision + 1" in conn.execute.await_args_list[1].args[0]

    def test_read_filter_never_uses_another_readers_mark(self):
        sql, params = feed_sql(OWNER, NotificationQuery(limit=10, unread=True), read_through_seq=0)
        assert "seq > $2 AND NOT EXISTS" in sql and "r.user_id = $3" in sql
        assert params[:3] == ["u", 0, "u"]
        sql, params = feed_sql(ADMIN, NotificationQuery(limit=10, unread=False), read_through_seq=0)
        assert "seq <= $3 OR EXISTS" in sql and "r.user_id = $4" in sql
        assert params[:4] == ["a", "t", 0, "a"]


async def test_individual_read_deleted_during_acknowledgement_rolls_back():
    pool, conn = _pool()
    conn.fetchrow.side_effect = [
        {"revision": 0},
        asyncpg.exceptions.ForeignKeyViolationError("deleted"),
    ]
    with pytest.raises(NotificationNotFoundError):
        await PostgresNotificationRepository(pool).mark_read("u", uuid4())
    assert conn.execute.await_count == 1  # no revision increment
    assert conn.transaction.return_value.__aexit__.await_args.args[0] is NotificationNotFoundError
