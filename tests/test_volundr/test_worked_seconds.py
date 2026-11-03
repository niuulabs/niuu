"""LEX-1465: per-session ``worked_seconds_24h`` on ``GET /sessions``.

The SQL (union of assistant-turn spans, clipped to the window, open spans bounded by
evidence) was checked on a scratch PostgreSQL with crafted spans; see the LEX-1465
ticket. These tests cover the adapter's parameters, the service window and the REST
projection.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import (
    InMemorySessionRepository,
    MockPodManager,
    make_session_participant_service,
)
from tests.test_volundr.test_conversation_tail import _archive_service, _CountingEventLog
from volundr.adapters.inbound.rest import create_router
from volundr.adapters.outbound.postgres_spans import WORKED_SECONDS_SQL, PostgresSpanRepository
from volundr.domain.models import Session, SessionStatus
from volundr.domain.services.session import WORKED_WINDOW, SessionService


async def test_adapter_binds_ids_and_window() -> None:
    pool = MagicMock()
    sid = uuid4()
    pool.fetch = AsyncMock(return_value=[{"session_id": sid, "seconds": 5400}])
    repo = PostgresSpanRepository(pool)
    since, until = MagicMock(), MagicMock()

    assert await repo.worked_seconds([sid], since=since, until=until) == {sid: 5400}
    sql, ids, bound_since, bound_until = pool.fetch.call_args.args
    assert sql == WORKED_SECONDS_SQL and ids == [sid]
    assert (bound_since, bound_until) == (since, until)
    assert "kind = 'turn.assistant'" in sql and "lead(sp.started_at)" in sql


async def test_adapter_skips_the_query_without_sessions() -> None:
    pool = MagicMock()
    pool.fetch = AsyncMock()
    assert await PostgresSpanRepository(pool).worked_seconds([], since=None, until=None) == {}
    pool.fetch.assert_not_awaited()


def _service(span_repository) -> SessionService:
    return SessionService(
        repository=InMemorySessionRepository(),
        pod_manager=MagicMock(),
        validate_repos=False,
        span_repository=span_repository,
    )


async def test_service_asks_for_the_last_24_hours_and_fills_zeros() -> None:
    spans = MagicMock()
    busy, quiet = uuid4(), uuid4()
    spans.worked_seconds = AsyncMock(return_value={busy: 120})

    worked = await _service(spans).worked_seconds_24h([busy, quiet])

    assert worked == {busy: 120, quiet: 0}
    kwargs = spans.worked_seconds.call_args.kwargs
    assert kwargs["until"] - kwargs["since"] == WORKED_WINDOW == timedelta(hours=24)


async def test_service_without_spans_does_not_know() -> None:
    assert await _service(None).worked_seconds_24h([uuid4()]) is None


def _list_client(repository: InMemorySessionRepository, spans) -> TestClient:
    service = SessionService(
        repository=repository,
        pod_manager=MockPodManager(),
        validate_repos=False,
        span_repository=spans,
    )
    app = FastAPI()
    app.include_router(
        create_router(
            service,
            archive_service=_archive_service(_CountingEventLog([]), repository),
            session_participant_service=make_session_participant_service(service),
        )
    )
    return TestClient(app)


@pytest.mark.parametrize("fields", ["full", "summary"])
async def test_list_carries_worked_seconds(fields: str) -> None:
    repository = InMemorySessionRepository()
    worked = await repository.create(Session(name="w", model="m", status=SessionStatus.STOPPED))
    idle = await repository.create(Session(name="i", model="m", status=SessionStatus.STOPPED))
    spans = MagicMock()
    spans.worked_seconds = AsyncMock(return_value={worked.id: 8040})

    rows = (
        _list_client(repository, spans)
        .get("/api/v1/forge/sessions", params={"fields": fields})
        .json()
    )

    by_id = {row["id"]: row["worked_seconds_24h"] for row in rows}
    assert by_id == {str(worked.id): 8040, str(idle.id): 0}
