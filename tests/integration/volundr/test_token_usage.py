"""A session whose token total passes 2^31 - 1 keeps accepting usage (it answered 500)."""

from __future__ import annotations

import json
from dataclasses import asdict
from uuid import UUID, uuid4

import pytest

from tests.integration.volundr.conftest import NoOpPodManager
from volundr.adapters.outbound.pg_event_sink import PostgresEventSink
from volundr.adapters.outbound.postgres_stats import PostgresStatsRepository
from volundr.adapters.outbound.postgres_tokens import PostgresTokenTracker
from volundr.domain.models import SessionStatus

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
]

API = "/api/v1/forge"
INT32_MAX = 2**31 - 1


@pytest.fixture(autouse=True)
def _live_pod(monkeypatch):
    """The session's pod answers as running, so a read does not reconcile it to stopped."""

    async def status(self, session):
        return SessionStatus.RUNNING

    monkeypatch.setattr(NoOpPodManager, "status", status)


async def _live_session(client, txn_pool, headers) -> UUID:
    created = await client.post(
        f"{API}/sessions",
        json={
            "name": f"usage-{uuid4().hex[:8]}",
            "model": "claude-sonnet-4-6",
            "source": {"type": "git", "repo": "github.com/acme/demo", "branch": "main"},
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    session_id = UUID(created.json()["id"])
    await txn_pool.execute(
        "UPDATE sessions SET status = 'running', tokens_used = $2 WHERE id = $1",
        session_id,
        INT32_MAX - 10,
    )
    return session_id


async def _report(client, session_id, headers, tokens: int):
    return await client.post(
        f"{API}/sessions/{session_id}/usage",
        json={"tokens": tokens, "provider": "cloud", "model": "claude-sonnet-4-6"},
        headers=headers,
    )


async def test_usage_past_the_int32_range_is_recorded(volundr_client, txn_pool, auth_headers):
    headers = auth_headers()
    session_id = await _live_session(volundr_client, txn_pool, headers)

    crossing = await _report(volundr_client, session_id, headers, 5_000_000)
    single = await _report(volundr_client, session_id, headers, INT32_MAX + 1)

    assert crossing.status_code == 201, crossing.text
    assert single.status_code == 201, single.text
    total = INT32_MAX - 10 + 5_000_000 + INT32_MAX + 1
    session = await volundr_client.get(f"{API}/sessions/{session_id}", headers=headers)
    assert session.json()["tokens_used"] == total
    recorded = await txn_pool.fetchval(
        "SELECT SUM(tokens) FROM token_usage WHERE session_id = $1", session_id
    )
    assert int(recorded) == 5_000_000 + INT32_MAX + 1


async def test_a_chronicle_holds_that_total(txn_pool):
    chronicle_id = uuid4()
    await txn_pool.execute(
        "INSERT INTO chronicles (id, project, repo, branch, model, token_usage) "
        "VALUES ($1, 'p', 'r', 'main', 'm', $2)",
        chronicle_id,
        INT32_MAX + 1,
    )

    stored = await txn_pool.fetchval(
        "SELECT token_usage FROM chronicles WHERE id = $1", chronicle_id
    )
    assert stored == INT32_MAX + 1


async def test_the_token_timeline_sums_past_the_int32_range(volundr_client, txn_pool, auth_headers):
    session_id = await _live_session(volundr_client, txn_pool, auth_headers())
    for sequence in (1, 2):
        await txn_pool.execute(
            "INSERT INTO session_events "
            "(id, session_id, event_type, timestamp, tokens_in, tokens_out, sequence) "
            "VALUES ($1, $2, 'token_usage', NOW(), $3, $3, $4)",
            uuid4(),
            session_id,
            INT32_MAX,
            sequence,
        )

    timeline = await PostgresEventSink(txn_pool).get_token_timeline(session_id, 10**9)

    assert timeline[0]["tokens_in"] == timeline[0]["tokens_out"] == 2 * INT32_MAX


async def test_token_totals_are_integers(volundr_client, txn_pool, auth_headers):
    """SUM of a BIGINT is numeric: uncast it reads as Decimal, which no JSON encoder takes."""
    headers = auth_headers()
    session_id = await _live_session(volundr_client, txn_pool, headers)
    for provider, tokens in (("cloud", INT32_MAX + 1), ("local", 7)):
        reported = await volundr_client.post(
            f"{API}/sessions/{session_id}/usage",
            json={"tokens": tokens, "provider": provider, "model": "claude-sonnet-4-6"},
            headers=headers,
        )
        assert reported.status_code == 201, reported.text
    # A session without usage rows today counts by its chronicle.
    chronicled = await _live_session(volundr_client, txn_pool, headers)
    await txn_pool.execute(
        "INSERT INTO chronicles (id, session_id, project, repo, branch, model, token_usage) "
        "VALUES ($1, $2, 'p', 'r', 'main', 'm', $3)",
        uuid4(),
        chronicled,
        INT32_MAX + 2,
    )

    stats = await PostgresStatsRepository(txn_pool).get_stats(tenant_id=None, owner_id=None)
    used = await PostgresTokenTracker(txn_pool).get_session_usage(session_id)

    totals = {
        key: value for key, value in asdict(stats).items() if key.endswith(("_tokens", "_today"))
    }
    del totals["cost_today"]
    assert totals == {
        "tokens_today": 2 * INT32_MAX + 10,
        "local_tokens": 7,
        "cloud_tokens": 2 * INT32_MAX + 3,
        "sessions_today": 2,
    }
    assert {type(value) for value in totals.values()} == {int}
    assert json.dumps(totals)
    assert (type(used), used) == (int, INT32_MAX + 8)
