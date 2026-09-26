"""Integration tests for Forge-owned Volundr runtime endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
]

API = "/api/v1/forge"
MIGRATIONS = Path(__file__).resolve().parents[3] / "migrations"


async def _tenant(txn_pool, tenant_id: str) -> None:
    await txn_pool.execute(
        "INSERT INTO tenants (id, path, name) VALUES ($1, $1, $1)",
        tenant_id,
    )


async def _session(client, headers) -> UUID:
    payload = {
        "name": f"stats-{uuid4().hex[:8]}",
        "model": "claude-sonnet-4-6",
        "source": {"type": "git", "repo": "github.com/acme/demo", "branch": "main"},
    }
    resp = await client.post(f"{API}/sessions", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return UUID(resp.json()["id"])


async def _usage(
    txn_pool, session_id: UUID, tokens: int, recorded_at: datetime | None = None
) -> None:
    await txn_pool.execute(
        "INSERT INTO token_usage (id, session_id, recorded_at, tokens, provider, model, cost) "
        "VALUES ($1, $2, $3, $4, 'cloud', 'claude-sonnet-4-6', 0)",
        uuid4(),
        session_id,
        recorded_at or datetime.now(UTC),
        tokens,
    )


async def _stats(client, headers) -> dict:
    resp = await client.get(f"{API}/stats", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_stats_empty_db(volundr_client, auth_headers):
    """GET /api/stats on a fresh (rolled-back) DB returns zero counters."""
    headers = auth_headers()
    resp = await volundr_client.get(f"{API}/stats", headers=headers)
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["active_sessions"] == 0
    assert body["tokens_today"] == 0
    assert body["cost_today"] == 0.0


async def test_forge_does_not_serve_model_catalog(volundr_client, auth_headers):
    """Model catalog endpoints are owned by Bifrost, not Forge."""
    headers = auth_headers()
    resp = await volundr_client.get(f"{API}/models", headers=headers)
    assert resp.status_code == 404, resp.text


async def test_stats_cover_only_what_the_caller_may_list(volundr_client, auth_headers, txn_pool):
    """Figures follow GET /sessions bounds: own sessions, or the tenant for an admin."""
    await _tenant(txn_pool, "stats-t1")
    await _tenant(txn_pool, "stats-t2")
    alice = auth_headers("stats-alice", "stats-alice@test.com", "stats-t1")
    bob = auth_headers("stats-bob", "stats-bob@test.com", "stats-t1")
    carol = auth_headers("stats-carol", "stats-carol@test.com", "stats-t2")
    t1_admin = auth_headers("stats-root", "stats-root@test.com", "stats-t1", ["volundr:admin"])
    t2_admin = auth_headers("stats-root2", "stats-root2@test.com", "stats-t2", ["volundr:admin"])

    await _usage(txn_pool, await _session(volundr_client, alice), 100)
    await _usage(txn_pool, await _session(volundr_client, alice), 10)
    await _usage(txn_pool, await _session(volundr_client, bob), 200)
    await _usage(txn_pool, await _session(volundr_client, carol), 400)
    # History of alice's since-deleted session still counts for whoever could list it.
    await txn_pool.execute(
        "INSERT INTO chronicles (id, session_id, project, repo, branch, model, "
        "token_usage, owner_id, tenant_id) "
        "VALUES ($1, $2, 'demo', 'github.com/acme/demo', 'main', 'claude-sonnet-4-6', "
        "50, 'stats-alice', 'stats-t1')",
        uuid4(),
        uuid4(),
    )

    figures = {
        name: await _stats(volundr_client, headers)
        for name, headers in {
            "alice": alice,
            "bob": bob,
            "carol": carol,
            "t1_admin": t1_admin,
            "t2_admin": t2_admin,
        }.items()
    }

    def totals(name: str) -> tuple[int, int, int]:
        body = figures[name]
        return body["total_sessions"], body["sessions_today"], body["tokens_today"]

    assert totals("alice") == (3, 3, 160)
    assert totals("bob") == (1, 1, 200)
    assert totals("carol") == (1, 1, 400)
    assert totals("t1_admin") == (4, 4, 360)
    assert totals("t2_admin") == (1, 1, 400)
    assert sum(figures["alice"]["sparklines"]["sessionsToday"]) == 3.0
    assert sum(figures["t1_admin"]["sparklines"]["sessionsToday"]) == 4.0
    assert sum(figures["t2_admin"]["sparklines"]["sessionsToday"]) == 1.0


async def test_stats_days_are_utc_days_whatever_the_session_timezone(
    volundr_client, auth_headers, txn_pool
):
    """Today's figures and the sparkline's days are UTC days, not the database's local ones."""
    now = datetime.now(UTC)
    # Pick a zone whose calendar date differs from UTC's right now: UTC-12 is
    # still on the previous day before 12:00 UTC, UTC+14 is on the next one
    # from 10:00 UTC. POSIX ``Etc/`` names invert the sign.
    zone = "Etc/GMT+12" if now.hour < 11 else "Etc/GMT-14"
    await txn_pool.execute(f"SET LOCAL TIME ZONE '{zone}'")
    assert await txn_pool.fetchval("SELECT CURRENT_DATE") != now.date()

    await _tenant(txn_pool, "stats-tz")
    headers = auth_headers("stats-erin", "stats-erin@test.com", "stats-tz")
    yesterday = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=1)

    await _usage(txn_pool, await _session(volundr_client, headers), 100)
    earlier = await _session(volundr_client, headers)
    await txn_pool.execute("UPDATE sessions SET created_at = $2 WHERE id = $1", earlier, yesterday)
    await _usage(txn_pool, earlier, 10, recorded_at=yesterday)
    # A since-deleted session whose chronicle was last written yesterday.
    await txn_pool.execute(
        "INSERT INTO chronicles (id, session_id, project, repo, branch, model, "
        "token_usage, owner_id, tenant_id, created_at, updated_at) "
        "VALUES ($1, $2, 'demo', 'github.com/acme/demo', 'main', 'claude-sonnet-4-6', "
        "50, 'stats-erin', 'stats-tz', $3, $3)",
        uuid4(),
        uuid4(),
        yesterday,
    )

    body = await _stats(volundr_client, headers)

    assert body["total_sessions"] == 3
    assert body["sessions_today"] == 1
    assert body["tokens_today"] == 100
    sparkline = body["sparklines"]["sessionsToday"]
    assert len(sparkline) == 30
    assert sparkline[-2:] == [2.0, 1.0]
    assert sum(sparkline) == 3.0


async def test_migration_attributes_existing_chronicles(volundr_client, auth_headers, txn_pool):
    """Chronicles written before attribution take their live session's owner and tenant."""
    await _tenant(txn_pool, "stats-backfill")
    headers = auth_headers("stats-dave", "stats-dave@test.com", "stats-backfill")
    session_id = await _session(volundr_client, headers)
    chronicle_id = uuid4()
    await txn_pool.execute(
        "INSERT INTO chronicles (id, session_id, project, repo, branch, model) "
        "VALUES ($1, $2, 'demo', 'github.com/acme/demo', 'main', 'claude-sonnet-4-6')",
        chronicle_id,
        session_id,
    )

    await txn_pool.execute((MIGRATIONS / "000076_chronicle_attribution.up.sql").read_text())

    row = await txn_pool.fetchrow(
        "SELECT owner_id, tenant_id FROM chronicles WHERE id = $1", chronicle_id
    )
    assert (row["owner_id"], row["tenant_id"]) == ("stats-dave", "stats-backfill")
