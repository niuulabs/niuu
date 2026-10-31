"""The outbox dispatcher against real PostgreSQL and a real local webhook server.

Opt-in: set ``FORGE_HISTORY_TEST_DATABASE_URL`` to a disposable database. Each test
gets its own schema, migrated through the production startup runner, and drops it.
The webhook receiver is a threaded HTTP server on a free loopback port; nothing
leaves the machine.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import threading
from collections import deque
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from cli.resources import migration_dir, ordered_migration_files
from niuu.adapters.notifications.webhook import WebhookNotificationSink
from niuu.domain.notifications import (
    NotificationKind,
    NotificationSeverity,
    NotificationSource,
)
from volundr.adapters.outbound.notification_sinks import ConfiguredNotificationSinks
from volundr.adapters.outbound.postgres_notifications import (
    PostgresNotificationDeliveryRepository,
    PostgresNotificationRepository,
    PostgresNotificationRuleRepository,
)
from volundr.adapters.outbound.startup_schema import apply_startup_migrations
from volundr.domain.notifications import (
    NotificationCandidate,
    NotificationRule,
    NotificationRuleMatch,
)
from volundr.domain.services.notification_dispatcher import (
    DispatcherSettings,
    NotificationDispatcher,
    NotificationRenderer,
)

pytestmark = pytest.mark.integration
SECRET = "integration-secret"


@pytest_asyncio.fixture
async def pool():
    dsn = os.environ.get("FORGE_HISTORY_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Set FORGE_HISTORY_TEST_DATABASE_URL for isolated PostgreSQL checks")
    schema = "forge_notification_dispatch_test_" + uuid4().hex
    admin = await asyncpg.connect(dsn)
    await admin.execute(f'CREATE SCHEMA "{schema}"')
    created = None
    try:
        created = await asyncpg.create_pool(
            dsn, min_size=1, max_size=8, server_settings={"search_path": schema}
        )
        async with created.acquire() as conn:
            await apply_startup_migrations(conn, ordered_migration_files(migration_dir("volundr")))
        yield created
    finally:
        if created is not None:
            await created.close()
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


class Receiver:
    """A local webhook endpoint that records every request and replays scripted statuses."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.statuses: deque[int] = deque()
        self._lock = threading.Lock()
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 (http.server API)
                body = self.rfile.read(int(self.headers["Content-Length"]))
                with receiver._lock:
                    receiver.requests.append({"body": body, "headers": dict(self.headers)})
                    status = receiver.statuses.popleft() if receiver.statuses else 200
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args) -> None:
                return None

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/hooks/forge"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> Receiver:
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()

    def delivery_ids(self) -> list[str]:
        with self._lock:
            return [json.loads(r["body"])["delivery_id"] for r in self.requests]


def settings(**overrides) -> DispatcherSettings:
    values = {
        "poll_interval_seconds": 0.05,
        "batch_size": 5,
        "lease_seconds": 30.0,
        "send_timeout_seconds": 5.0,
        "max_concurrent_sends": 4,
        "max_attempts": 4,
        "backoff_base_seconds": 0.2,
        "backoff_max_seconds": 1.0,
        "backoff_jitter_ratio": 0.0,
        "default_rate_limit": None,
    }
    values.update(overrides)
    return DispatcherSettings(**values)


async def setup(pool, receiver: Receiver, *, count: int = 1, rule_config: dict | None = None):
    now = datetime.now(UTC)
    rule = NotificationRule(
        id=uuid4(),
        owner_id="owner-a",
        name="to webhook",
        match=NotificationRuleMatch(kinds=[NotificationKind.MILESTONE]),
        sink="ops-webhook",
        config=rule_config or {},
        created_at=now,
        updated_at=now,
    )
    await PostgresNotificationRuleRepository(pool).create(rule)
    created = await PostgresNotificationRepository(pool).project(
        [
            NotificationCandidate(
                dedupe_key=f"it-{uuid4().hex}",
                session_id=uuid4(),
                session_name="fix-auth",
                owner_id="owner-a",
                kind=NotificationKind.MILESTONE,
                severity=NotificationSeverity.SUCCESS,
                source=NotificationSource.AGENT,
                title=f"Milestone {index}",
                body="Tests are green",
            )
            for index in range(count)
        ]
    )
    sink = WebhookNotificationSink(receiver.url, name="ops-webhook", secret=SECRET)
    return rule, created, ConfiguredNotificationSinks({"ops-webhook": sink})


def dispatcher(pool, sinks, **overrides) -> NotificationDispatcher:
    return NotificationDispatcher(
        PostgresNotificationDeliveryRepository(pool, max_error_chars=500),
        sinks,
        NotificationRenderer(
            public_base_url="https://forge.example.com",
            session_link_path="/volundr/session/{session_id}#notification-{notification_id}",
            feed_link_path="/volundr/notifications",
            resident_link_path="/ravn?ravn={resident_id}&tab=chat",
            resident_session_link_path=(
                "/ravn?ravn={resident_id}&tab=chat&session={resident_session_id}"
            ),
            host_label="thor",
        ),
        settings(**overrides),
    )


async def statuses(pool) -> list[str]:
    rows = await pool.fetch("SELECT status FROM forge_notification_deliveries ORDER BY created_at")
    return [row["status"] for row in rows]


async def test_a_matching_rule_is_delivered_signed_to_the_webhook(pool):
    with Receiver() as receiver:
        _, (notification,), sinks = await setup(pool, receiver)
        worker = dispatcher(pool, sinks)
        assert await worker.run_once() == 1
        await sinks.close()

    assert await statuses(pool) == ["delivered"]
    (request,) = receiver.requests
    expected = hmac.new(SECRET.encode(), request["body"], hashlib.sha256).hexdigest()
    assert request["headers"]["X-Niuu-Signature"] == f"sha256={expected}"
    body = json.loads(request["body"])
    assert body["notification"]["id"] == str(notification.id)
    assert body["notification"]["url"].startswith("https://forge.example.com/volundr/session/")
    row = await pool.fetchrow("SELECT * FROM forge_notification_deliveries")
    assert row["delivered_at"] is not None and row["attempts"] == 1


async def test_a_failing_webhook_is_retried_with_backoff_then_delivered(pool):
    with Receiver() as receiver:
        receiver.statuses.extend([503, 500])
        _, _, sinks = await setup(pool, receiver)
        worker = dispatcher(pool, sinks)

        await worker.run_once()
        row = await pool.fetchrow("SELECT * FROM forge_notification_deliveries")
        assert row["status"] == "failed" and row["attempts"] == 1
        assert "returned HTTP 503" in row["last_error"]
        assert await worker.run_once() == 0  # backing off

        for _ in range(50):
            await asyncio.sleep(0.1)
            await worker.run_once()
            if await statuses(pool) == ["delivered"]:
                break
        await sinks.close()

    row = await pool.fetchrow("SELECT * FROM forge_notification_deliveries")
    assert row["status"] == "delivered" and row["attempts"] == 3
    assert len(receiver.requests) == 3
    assert worker.stats.retried == 2


async def test_a_permanently_rejected_delivery_is_dead(pool):
    with Receiver() as receiver:
        receiver.statuses.append(410)
        _, _, sinks = await setup(pool, receiver)
        await dispatcher(pool, sinks).run_once()
        await sinks.close()
    row = await pool.fetchrow("SELECT * FROM forge_notification_deliveries")
    assert row["status"] == "dead" and "HTTP 410" in row["last_error"]


async def test_concurrent_dispatchers_deliver_every_row_exactly_once(pool):
    with Receiver() as receiver:
        _, _, sinks = await setup(pool, receiver, count=24)
        workers = [dispatcher(pool, sinks) for _ in range(3)]
        for _ in range(40):
            await asyncio.gather(*(worker.run_once() for worker in workers))
            if set(await statuses(pool)) == {"delivered"}:
                break
        await sinks.close()

    ids = receiver.delivery_ids()
    assert len(ids) == 24 and len(set(ids)) == 24
    assert set(await statuses(pool)) == {"delivered"}
    assert sum(worker.stats.delivered for worker in workers) == 24
    assert sum(worker.stats.stale_settles for worker in workers) == 0


async def test_a_crashed_claim_is_re_claimed_after_its_lease(pool):
    with Receiver() as receiver:
        _, _, sinks = await setup(pool, receiver)
        repo = PostgresNotificationDeliveryRepository(pool, max_error_chars=500)
        (crashed,) = await repo.claim_due(limit=5, lease_seconds=0.3)
        worker = dispatcher(pool, sinks)
        assert await worker.run_once() == 0  # still leased
        await asyncio.sleep(0.4)
        assert await worker.run_once() == 1
        await sinks.close()

    row = await pool.fetchrow("SELECT * FROM forge_notification_deliveries")
    assert row["status"] == "delivered" and row["attempts"] == 2
    assert await repo.mark_delivered(crashed.delivery) is False
    assert len(receiver.requests) == 1


async def test_the_rate_limit_counts_real_deliveries(pool):
    with Receiver() as receiver:
        _, _, sinks = await setup(
            pool,
            receiver,
            count=4,
            rule_config={"rate_limit": {"max_count": 3, "window_seconds": 3600}},
        )
        await dispatcher(pool, sinks).run_once()
        await sinks.close()
    assert sorted(await statuses(pool)) == ["delivered", "delivered", "delivered", "suppressed"]
    assert len(receiver.requests) == 3


async def test_the_loop_runs_until_stopped(pool):
    with Receiver() as receiver:
        _, _, sinks = await setup(pool, receiver, count=7)
        worker = dispatcher(pool, sinks, batch_size=2)
        await worker.start()
        for _ in range(100):
            if worker.stats.delivered == 7:
                break
            await asyncio.sleep(0.05)
        await worker.stop()
        await sinks.close()
    assert set(await statuses(pool)) == {"delivered"}
