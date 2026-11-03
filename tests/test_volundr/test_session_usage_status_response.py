"""LEX-1421: persisted status uses the existing owner routes and scoped stream."""

import asyncio
import copy
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import jwt
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from niuu.domain.services.token_scope import FORGE_SESSION_READ_SCOPE
from niuu.ports.identity import IdentityPort
from tests.conftest import (
    InMemorySessionRepository,
    MockPodManager,
    make_session_participant_service,
)
from tests.test_niuu.test_rest_volundr import _client, _headers, _instance
from volundr.adapters.inbound.rest import SessionResponse, SessionStatusResponse, create_router
from volundr.adapters.outbound.broadcaster import InMemoryEventBroadcaster
from volundr.adapters.outbound.postgres import PostgresSessionRepository
from volundr.config import SessionUsageConfig
from volundr.domain.models import EventType, Session, SessionStatus
from volundr.domain.services.session import SessionAccessDeniedError, SessionService

NOW = datetime(2026, 10, 23, 12, tzinfo=UTC)
USAGE = {
    "context": {"tokens": 250, "window": 1000, "share": 0.25, "_private": "hidden"},
    "last_turn": {"input": 200, "cache_read": 50, "cache_write": 0, "output": 10},
    "totals": {"input": 400, "cache_read": 100, "cache_write": 0, "output": 30},
    "compactions": 0,
    "model": "observed-model",
    "engine": "codex",
    "started_at": "2026-10-23T11:00:00Z",
    "cost_estimate": 0.0,
    "usage_available": True,
    "totals_complete": True,
    "_state": {"native_thread": "private-thread-id"},
}


@pytest.mark.parametrize("invalid", [0, -1, True, False, 1000.0, "1000", 2**53, float("inf"), None])
def test_model_windows_reject_nonpositive_inexact_or_noninteger_configuration(invalid):
    with pytest.raises(ValidationError):
        SessionUsageConfig(model_windows={"custom-model": invalid})


def test_model_windows_preserve_explicit_positive_overrides_and_unknown_models():
    configured = SessionUsageConfig(model_windows={"custom-model": 4096, "large-model": 2**53 - 1})
    assert configured.model_windows == {"custom-model": 4096, "large-model": 2**53 - 1}
    assert SessionUsageConfig().model_windows == {
        "claude-opus-5-5": 1_000_000,
        "claude-fable-5-1": 1_000_000,
        "claude-sonnet-5-5": 1_000_000,
    }


def session(**changes):
    return Session(name="status", model="configured-model", status=SessionStatus.RUNNING, **changes)


def test_rest_status_list_and_existing_event_share_the_public_context():
    s = session(session_usage=copy.deepcopy(USAGE))
    status = SessionStatusResponse.from_session(s, host="served", now=NOW).model_dump(mode="json")
    row = SessionResponse.from_session(s).model_dump(mode="json")
    event = InMemoryEventBroadcaster().create_session_event(EventType.SESSION_UPDATED, s)
    assert "session_usage" not in s.model_dump(mode="json")
    assert s.model_copy(update={"name": "renamed"}).session_usage == s.session_usage
    assert (
        status["context"]
        == row["context"]
        == event.data["context"]
        == {"tokens": 250, "window": 1000, "share": 0.25}
    )
    assert status["last_turn"] == USAGE["last_turn"]
    assert status["totals"] == USAGE["totals"]
    assert status["model"] == "observed-model"
    assert status["engine"] == "codex"
    assert status["host"] == "served"
    assert status["uptime_s"] == 3600
    assert status["cost_estimate"] == 0
    assert status["compactions"] == 0
    assert status["usage_available"] is True
    assert status["totals_complete"] is True
    assert status["usage_reason"] is None
    for body in (status, row, event.data):
        assert "private-thread-id" not in json.dumps(body)
        assert "_private" not in json.dumps(body)
        assert "session_usage" not in body
        assert "_state" not in body


def test_unreported_fields_are_null_despite_legacy_tokens_and_creation_time():
    s = session(tokens_used=999999, created_at=NOW)
    status = SessionStatusResponse.from_session(s, now=NOW).model_dump()
    assert status["context"] == {"tokens": None, "window": None, "share": None}
    assert (
        status["last_turn"]
        == status["totals"]
        == {"input": None, "cache_read": None, "cache_write": None, "output": None}
    )
    assert status["model"] == "configured-model"
    for key in (
        "compactions",
        "engine",
        "host",
        "started_at",
        "uptime_s",
        "cost_estimate",
        "usage_available",
        "usage_reason",
        "totals_complete",
    ):
        assert status[key] is None


def test_partial_accounting_exposes_the_persisted_completeness_reason():
    usage = {
        **USAGE,
        "context": None,
        "usage_available": False,
        "totals_complete": False,
        "usage_reason": "transcript_replaced",
    }
    status = SessionStatusResponse.from_session(session(session_usage=usage)).model_dump()
    assert status["context"] == {"tokens": None, "window": None, "share": None}
    assert status["usage_available"] is False
    assert status["totals_complete"] is False
    assert status["usage_reason"] == "transcript_replaced"
    assert status["totals"] == USAGE["totals"]


@pytest.mark.parametrize(
    "state", [SessionStatus.STOPPED, SessionStatus.FAILED, SessionStatus.CREATED]
)
def test_inactive_uptime_is_unknown_without_a_measured_end(state):
    s = session(session_usage=USAGE).model_copy(update={"status": state})
    assert SessionStatusResponse.from_session(s, now=NOW).uptime_s is None


def test_future_start_does_not_fabricate_elapsed_time():
    usage = {**USAGE, "started_at": "2026-10-23T13:00:00Z"}
    assert (
        SessionStatusResponse.from_session(session(session_usage=usage), now=NOW).uptime_s is None
    )


def direct_app(s):
    repository = InMemorySessionRepository()
    asyncio.run(repository.create(s))
    service = SessionService(repository, MockPodManager())
    app = FastAPI()
    app.include_router(
        create_router(
            service,
            session_status_host="direct-host",
            session_participant_service=make_session_participant_service(service),
        )
    )
    return app


def test_status_reads_only_the_authorized_persisted_row_and_list_keeps_context():
    s = session(session_usage=USAGE)
    with (
        TestClient(direct_app(s)) as client,
        patch(
            "volundr.adapters.inbound.rest.httpx.AsyncClient",
            side_effect=AssertionError("no broker I/O"),
        ),
    ):
        response = client.get(f"/api/v1/forge/sessions/{s.id}/status")
        assert response.status_code == 200
        assert response.json()["host"] == "direct-host"
        assert response.json()["context"]["share"] == 0.25
        assert (
            client.get("/api/v1/forge/sessions").json()[0]["context"] == response.json()["context"]
        )
        assert client.get(f"/api/v1/forge/sessions/{uuid4()}/status").status_code == 404


def test_status_uses_the_existing_read_authorization():
    s = session(session_usage=USAGE)
    with (
        TestClient(direct_app(s)) as client,
        patch(
            "volundr.adapters.inbound.rest.ForgeService.ensure_access",
            new=AsyncMock(side_effect=SessionAccessDeniedError(s.id, "other-user")),
        ) as access,
    ):
        assert client.get(f"/api/v1/forge/sessions/{s.id}/status").status_code == 403
    assert access.call_args.args[2] == "read"


@pytest.mark.parametrize("scopes, expected", [([], 403), ([FORGE_SESSION_READ_SCOPE], 200)])
def test_status_requires_the_existing_session_read_workload_scope(scopes, expected):
    s = session()
    token = jwt.encode(
        {"token_use": "forge_session", "sub": "user-1", "scopes": scopes},
        "test-only-signing-key-32-bytes-long!",
        algorithm="HS256",
    )
    with TestClient(direct_app(s)) as client:
        response = client.get(
            f"/api/v1/forge/sessions/{s.id}/status",
            headers={"authorization": f"Bearer {token}"},
        )
        assert response.status_code == expected


@pytest.mark.parametrize("raw", [USAGE, json.dumps(USAGE), None])
async def test_postgres_stale_update_preserves_the_projection(raw):
    pool = MagicMock()
    pool.fetchrow = AsyncMock(
        return_value={
            "coordination": None,
            "coordination_revision": 0,
            "workload_config": {},
            "activity_state": None,
            "activity_metadata": {},
            "activity_state_since": None,
            "turn_started_at": None,
            "last_active": None,
            "session_usage": raw,
        }
    )
    repo = PostgresSessionRepository(pool)
    result = await repo.update(session(session_usage={"totals": {"input": 1}}))
    assert result.session_usage == (USAGE if raw is not None else None)
    sql = pool.fetchrow.call_args.args[0]
    assert "session_usage" not in sql.split("RETURNING")[0]
    assert "session_usage" in sql.split("RETURNING")[1]


async def test_postgres_create_persists_usage_json_without_exposing_it():
    pool = MagicMock()
    pool.execute = AsyncMock()
    repo = PostgresSessionRepository(pool)
    await repo.create(session(session_usage=USAGE))
    # session_usage precedes coordination and actor_id, which stay last.
    assert json.loads(pool.execute.call_args.args[-3]) == USAGE


async def test_postgres_read_decodes_usage_for_status_and_list():
    s = session(session_usage=USAGE)
    row = s.model_dump()
    row["session_usage"] = json.dumps(USAGE)
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=row)
    stored = await PostgresSessionRepository(pool).get(s.id)
    assert stored.session_usage == USAGE
    assert stored.context.share == 0.25


def test_facade_routes_status_to_owner_and_overrides_remote_host():
    instance = _instance("owner-host", base_url="https://forge.example.test", is_default=True)
    with _client([instance]) as client, respx.mock:
        respx.get("https://forge.example.test/api/v1/forge/sessions/s1").mock(
            return_value=httpx.Response(200, json={"id": "s1"})
        )
        route = respx.get("https://forge.example.test/api/v1/forge/sessions/s1/status").mock(
            return_value=httpx.Response(200, json={"host": "wrong", "context": {"share": 0.25}})
        )
        response = client.get("/api/v1/forge/sessions/s1/status", headers=_headers())
        assert response.status_code == 200
        assert response.json() == {"host": "owner-host", "context": {"share": 0.25}}
        assert route.calls[0].request.headers["authorization"] == "Bearer test-token"


def test_facade_requires_a_principal_before_owner_lookup():
    instance = _instance("owner-host", base_url="https://forge.example.test", is_default=True)
    with _client([instance]) as client, respx.mock as traffic:
        client.app.state.identity = MagicMock(spec=IdentityPort)
        assert client.get("/api/v1/forge/sessions/s1/status").status_code == 401
        assert len(traffic.calls) == 0


def test_facade_preserves_the_owner_status_read_refusal():
    instance = _instance("owner-host", base_url="https://forge.example.test", is_default=True)
    with _client([instance]) as client, respx.mock:
        respx.get("https://forge.example.test/api/v1/forge/sessions/s1").mock(
            return_value=httpx.Response(200, json={"id": "s1"})
        )
        respx.get("https://forge.example.test/api/v1/forge/sessions/s1/status").mock(
            return_value=httpx.Response(403, json={"detail": "read denied"})
        )
        response = client.get("/api/v1/forge/sessions/s1/status", headers=_headers())
        assert response.status_code == 403


def test_local_scope_refuses_another_host_before_any_network_call():
    local = _instance("local", base_url="embedded://local", config={"transport": "embedded"})
    remote = _instance("remote", base_url="https://forge.example.test")
    with _client([local, remote]) as client, respx.mock as traffic:
        response = client.get(
            "/api/v1/forge/sessions/s1/status?scope=local&instance_id=remote", headers=_headers()
        )
        assert response.status_code == 422
        assert len(traffic.calls) == 0


def test_usage_migration_is_single_source_and_follows_work_facts():
    root = Path(__file__).resolve().parents[2]
    # The chart and the packaged CLI read migrations/ itself (single source).
    numbers = sorted(p.name[:6] for p in (root / "migrations").glob("*.up.sql"))
    assert numbers.count("000095") == 1
    assert numbers[numbers.index("000095") - 1] == "000094"
    up = (root / "migrations" / "000095_session_usage_status.up.sql").read_text()
    down = (root / "migrations" / "000095_session_usage_status.down.sql").read_text()
    assert "ADD COLUMN IF NOT EXISTS session_usage JSONB;" in up
    assert "DROP COLUMN IF EXISTS session_usage;" in down
