"""Real log → numeric projection → status/list/live stream, isolated PostgreSQL."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI

from identity.adapters.identity import AllowAllIdentityAdapter
from tests.conftest import MockPodManager, make_session_participant_service
from tests.test_adapters.test_message_delivery_postgres_integration import (
    isolated_pool as isolated_pool,
)
from tests.test_domain.test_session_usage import claude, codex
from volundr.adapters.inbound.rest import create_router
from volundr.adapters.inbound.rest_session_log import create_session_log_router
from volundr.adapters.outbound.broadcaster import InMemoryEventBroadcaster
from volundr.adapters.outbound.pg_session_event_log import PostgresSessionEventLog
from volundr.adapters.outbound.postgres import PostgresSessionRepository
from volundr.adapters.outbound.startup_schema import apply_startup_migrations
from volundr.config import SessionUsageConfig
from volundr.domain.models import Session, SessionLogEntry, SessionStatus
from volundr.domain.services.session import SessionService

pytestmark = pytest.mark.integration
MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"
AT = datetime(2026, 10, 23, tzinfo=UTC)


async def seed(pool):
    async with pool.acquire() as conn:
        await apply_startup_migrations(conn, sorted(MIGRATIONS.glob("*.up.sql")))
        await conn.execute(
            "INSERT INTO users (id, email) VALUES ('dev-user', 'dev@example.invalid')"
        )
        await conn.execute(
            "INSERT INTO tenants (id, path, name) VALUES ('default', 'default', 'Test')"
        )
    repo = PostgresSessionRepository(pool)
    session = await repo.create(
        Session(
            name="usage-exercise",
            model="claude-opus-5-5",
            owner_id="dev-user",
            tenant_id="default",
            status=SessionStatus.RUNNING,
        )
    )
    log = PostgresSessionEventLog(pool, usage_model_windows=SessionUsageConfig().model_windows)
    return repo, session, log


def entry(session, seq, payload=None):
    payload = payload or claude()
    return SessionLogEntry(
        session_id=session.id,
        seq=seq,
        kind=payload["type"],
        payload=payload,
        ts=AT + timedelta(seconds=seq),
    )


async def test_retries_new_sequences_and_restart_preserve_exact_totals(isolated_pool):
    repo, session, log = await seed(isolated_pool)
    await log.append([entry(session, 1), entry(session, 2, claude("two", output=30))])
    before = (await repo.get(session.id)).session_usage
    await log.append([entry(session, 1), entry(session, 2, claude("two", output=30))])
    restarted = PostgresSessionEventLog(
        isolated_pool, usage_model_windows=SessionUsageConfig().model_windows
    )
    await restarted.append([entry(session, 10), entry(session, 11, claude("two", output=30))])
    after = (await repo.get(session.id)).session_usage
    assert after == before
    assert after["totals"] == {"input": 24, "cache_read": 14468, "cache_write": 600, "output": 40}
    stored = await log.read_after(session.id)
    assert stored[0].payload == claude()  # Derived columns never rewrite raw source.


async def test_conflicting_payload_never_changes_projection(isolated_pool):
    repo, session, log = await seed(isolated_pool)
    await log.append([entry(session, 1)])
    before = (await repo.get(session.id)).session_usage
    conflict = entry(session, 1, claude(input=99999))
    await log.append([conflict])
    assert await log.detect_conflicts([conflict]) == [1]
    assert (await repo.get(session.id)).session_usage == before


async def test_concurrent_distinct_and_duplicate_responses_are_atomic(isolated_pool):
    repo, session, log = await seed(isolated_pool)
    await asyncio.gather(
        *(log.append([entry(session, i, claude(f"message-{i % 4}"))]) for i in range(1, 13))
    )
    actual = (await repo.get(session.id)).session_usage
    assert actual["totals"]["input"] == 48
    assert actual["totals"]["output"] == 40
    # Updating an unrelated stale session must retain the projection.
    updated = await repo.update(session.model_copy(update={"name": "renamed"}))
    assert updated.session_usage == actual


async def test_late_lower_seq_increases_component_once_and_is_retained(isolated_pool):
    repo, session, log = await seed(isolated_pool)
    await log.append([entry(session, 20, claude(output=10))])
    await log.append([entry(session, 10, claude(output=30))])
    await log.append([entry(session, 30, claude(output=20))])
    assert (await repo.get(session.id)).session_usage["totals"]["output"] == 30


async def test_projection_failure_rolls_back_raw_append_and_retry_recovers(isolated_pool):
    repo, session, log = await seed(isolated_pool)
    with patch(
        "volundr.adapters.outbound.pg_session_event_log.fold_usage",
        side_effect=RuntimeError("projection fault"),
    ):
        with pytest.raises(RuntimeError, match="projection fault"):
            await log.append([entry(session, 1)])
    assert await log.latest_seq(session.id) == 0
    assert (await repo.get(session.id)).session_usage is None
    await log.append([entry(session, 1)])
    assert (await repo.get(session.id)).context.tokens == 7546


async def test_codex_same_turn_multiple_responses_and_compaction_replay(isolated_pool):
    repo, session, log = await seed(isolated_pool)
    await log.append(
        [
            entry(session, 1, codex()),
            entry(
                session, 2, codex(input=4483, total_input=13027, total_read=23424, total_output=313)
            ),
        ]
    )
    assert (await repo.get(session.id)).context.tokens == 12291
    boundary = {
        "type": "system",
        "subtype": "compact_boundary",
        "engine": "codex",
        "thread_id": "native-codex",
        "compaction_id": "native-item",
    }
    await log.append([entry(session, 3, boundary), entry(session, 4, boundary)])
    actual = (await repo.get(session.id)).session_usage
    assert actual["compactions"] == 1
    assert actual["context"]["tokens"] is None
    assert actual["totals"]["input"] == 13027


async def test_http_ingest_changes_status_list_and_existing_live_stream(isolated_pool):
    repo, session, log = await seed(isolated_pool)
    broadcaster = InMemoryEventBroadcaster()
    service = SessionService(repo, MockPodManager(), broadcaster=broadcaster)
    app = FastAPI()
    app.state.identity = AllowAllIdentityAdapter(user_repository=AsyncMock())
    app.include_router(
        create_router(
            service,
            session_status_host="thor-exp",
            session_participant_service=make_session_participant_service(service),
        )
    )
    app.include_router(create_session_log_router(log, session_service=service))
    stream = broadcaster.subscribe()
    waiting = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/api/v1/forge/sessions/{session.id}/log",
                json={
                    "entries": [
                        {"seq": 1, "kind": "assistant", "payload": claude(), "ts": AT.isoformat()}
                    ]
                },
            )
            assert response.status_code == 201, response.text
            event = await asyncio.wait_for(waiting, timeout=2)
            status = await client.get(f"/api/v1/forge/sessions/{session.id}/status")
            rows = await client.get("/api/v1/forge/sessions")
            assert status.status_code == rows.status_code == 200
            assert event.type.value == "session_updated"
            assert event.data["context"] == status.json()["context"] == rows.json()[0]["context"]
            assert status.json()["context"]["share"] == 0.007546
            assert "_state" not in json.dumps(status.json())
    finally:
        if not waiting.done():
            waiting.cancel()
        await stream.aclose()


async def test_migration_up_down_up_retains_raw_log(isolated_pool):
    repo, session, log = await seed(isolated_pool)
    await log.append([entry(session, 1)])
    for direction in ("down", "up", "up"):
        await isolated_pool.execute(
            (MIGRATIONS / f"000095_session_usage_status.{direction}.sql").read_text()
        )
    assert (await log.read_after(session.id))[0].payload == claude()
    assert (await repo.get(session.id)).session_usage is None


async def test_malformed_telemetry_cannot_drop_valid_opaque_log_batch(isolated_pool):
    repo, session, log = await seed(isolated_pool)
    bad = {"type": "result", "session_id": "native-claude", "modelUsage": ["invalid"]}
    huge = {"type": "result", "session_id": "native-claude", "total_cost_usd": 10**400}
    await log.append([entry(session, 1, bad), entry(session, 2, huge), entry(session, 3)])
    assert len(await log.read_after(session.id)) == 3
    assert (await repo.get(session.id)).context.tokens == 7546


async def test_noncontiguous_context_only_replay_survives_projector_restart(isolated_pool):
    repo, session, log = await seed(isolated_pool)
    first, second = codex(input=50), codex(input=100)
    for payload in (first, second):
        payload.pop("total")
    boundary = {"type": "system", "subtype": "compact_boundary", "uuid": "compact"}
    await log.append(
        [entry(session, 1, first), entry(session, 2, second), entry(session, 3, boundary)]
    )
    restarted = PostgresSessionEventLog(isolated_pool)
    await restarted.append([entry(session, 4, first), entry(session, 5, second)])
    assert (await repo.get(session.id)).context.tokens is None


async def test_session_model_follows_the_runtime_through_http_ingest(isolated_pool):
    """B1d: list, GET and session_updated carry the model in effect; launch stays stored."""
    repo, session, log = await seed(isolated_pool)
    await isolated_pool.execute(
        "UPDATE sessions SET model='claude-fable-5-1' WHERE id=$1", session.id
    )
    broadcaster = InMemoryEventBroadcaster()
    service = SessionService(repo, MockPodManager(), broadcaster=broadcaster)
    app = FastAPI()
    app.state.identity = AllowAllIdentityAdapter(user_repository=AsyncMock())
    app.include_router(
        create_router(
            service,
            session_status_host="thor-exp",
            session_participant_service=make_session_participant_service(service),
        )
    )
    app.include_router(create_session_log_router(log, session_service=service))
    fable, opus = claude("one"), claude("two")
    fable["message"]["model"] = "claude-fable-5-1"
    synthetic = claude("three")
    synthetic["message"]["model"] = "<synthetic>"
    picked = {
        "type": "runtime_options",
        "applied": True,
        "active_turn": None,
        "current": {"model": "claude-fable-5-1", "effort": "high", "service_tier": None},
    }
    stream = broadcaster.subscribe()

    async def post(client, seq, payload):
        waiting = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        response = await client.post(
            f"/api/v1/forge/sessions/{session.id}/log",
            json={
                "entries": [
                    {
                        "seq": seq,
                        "kind": payload["type"],
                        "payload": payload,
                        "ts": (AT + timedelta(seconds=seq)).isoformat(),
                    }
                ]
            },
        )
        assert response.status_code == 201, response.text
        return await asyncio.wait_for(waiting, timeout=2)

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            event = await post(client, 1, fable)
            assert event.data["model"] == "claude-fable-5-1"
            # /model typed in the terminal; the next response is on Opus 5.5.
            event = await post(client, 2, opus)
            assert event.type.value == "session_updated"
            assert event.data["model"] == "claude-opus-5-5"
            got = (await client.get(f"/api/v1/forge/sessions/{session.id}")).json()
            listed = (await client.get("/api/v1/forge/sessions")).json()[0]
            status = (await client.get(f"/api/v1/forge/sessions/{session.id}/status")).json()
            assert got["model"] == listed["model"] == status["model"] == "claude-opus-5-5"
            # A synthetic row names no model.
            event = await post(client, 3, synthetic)
            assert event.data["model"] == "claude-opus-5-5"
            # The app picked Fable 5.1 between turns; the acknowledgement moves it at once.
            event = await post(client, 4, picked)
            assert event.data["model"] == "claude-fable-5-1"
    finally:
        await stream.aclose()
    stored = await isolated_pool.fetchrow("SELECT model FROM sessions WHERE id=$1", session.id)
    assert stored["model"] == "claude-fable-5-1"  # the launch record, which restart reads
    assert (await repo.get(session.id)).effective_model == "claude-fable-5-1"
