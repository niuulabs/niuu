"""Tests for the dispatcher state REST API endpoints.

Tests GET /api/v1/tyr/dispatcher and PATCH /api/v1/tyr/dispatcher by
overriding the DispatcherRepository dependency with an in-memory mock.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tyr.adapters.memory_event_bus import InMemoryEventBus
from tyr.api.dispatcher import (
    create_dispatcher_router,
    resolve_dispatcher_repo,
    resolve_event_bus,
)
from tyr.domain.models import DispatcherState
from tyr.ports.dispatcher_repository import DispatcherRepository
from tyr.ports.event_bus import TyrEvent

# -------------------------------------------------------------------
# In-memory mock
# -------------------------------------------------------------------

_DEFAULT_RUNNING = True
_DEFAULT_THRESHOLD = 0.75
_DEFAULT_MAX_CONCURRENT_RAIDS = 3
_DEFAULT_AUTO_CONTINUE = False


class MockDispatcherRepo(DispatcherRepository):
    """In-memory dispatcher repository for tests."""

    def __init__(self) -> None:
        self.states: dict[str, DispatcherState] = {}

    async def get_or_create(self, owner_id: str) -> DispatcherState:
        if owner_id in self.states:
            return self.states[owner_id]
        state = DispatcherState(
            id=uuid4(),
            owner_id=owner_id,
            running=_DEFAULT_RUNNING,
            threshold=_DEFAULT_THRESHOLD,
            max_concurrent_raids=_DEFAULT_MAX_CONCURRENT_RAIDS,
            auto_continue=_DEFAULT_AUTO_CONTINUE,
            updated_at=datetime.now(UTC),
        )
        self.states[owner_id] = state
        return state

    async def update(self, owner_id: str, **fields: object) -> DispatcherState:
        current = await self.get_or_create(owner_id)
        allowed = {"running", "threshold", "max_concurrent_raids", "auto_continue"}
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            return current
        new_state = DispatcherState(
            id=current.id,
            owner_id=current.owner_id,
            running=updates.get("running", current.running),
            threshold=updates.get("threshold", current.threshold),
            max_concurrent_raids=updates.get("max_concurrent_raids", current.max_concurrent_raids),
            auto_continue=updates.get("auto_continue", current.auto_continue),
            updated_at=datetime.now(UTC),
        )
        self.states[owner_id] = new_state
        return new_state

    async def list_active_owner_ids(self) -> list[str]:
        return [oid for oid, s in self.states.items() if s.running]


# -------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------


@pytest.fixture
def mock_repo() -> MockDispatcherRepo:
    return MockDispatcherRepo()


@pytest.fixture
def event_bus() -> InMemoryEventBus:
    return InMemoryEventBus(max_clients=5, log_size=100)


@pytest.fixture
def client(mock_repo: MockDispatcherRepo, event_bus: InMemoryEventBus) -> TestClient:
    app = FastAPI()
    app.include_router(create_dispatcher_router())
    app.dependency_overrides[resolve_dispatcher_repo] = lambda: mock_repo
    app.dependency_overrides[resolve_event_bus] = lambda: event_bus
    return TestClient(app)


def _auth_headers(user_id: str = "user-1") -> dict[str, str]:
    return {"x-auth-user-id": user_id}


# -------------------------------------------------------------------
# GET /api/v1/tyr/dispatcher
# -------------------------------------------------------------------


class TestGetDispatcherState:
    def test_creates_default_when_none_exists(self, client: TestClient):
        resp = client.get("/api/v1/tyr/dispatcher", headers=_auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["running"] is True
        assert data["threshold"] == 0.75
        assert data["max_concurrent_raids"] == 3
        assert data["auto_continue"] is False
        assert "id" in data
        assert "updated_at" in data

    def test_returns_existing_state(self, client: TestClient, mock_repo: MockDispatcherRepo):
        # Pre-populate a state
        existing = DispatcherState(
            id=uuid4(),
            owner_id="user-1",
            running=False,
            threshold=0.5,
            max_concurrent_raids=5,
            auto_continue=True,
            updated_at=datetime.now(UTC),
        )
        mock_repo.states["user-1"] = existing

        resp = client.get("/api/v1/tyr/dispatcher", headers=_auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["running"] is False
        assert data["threshold"] == 0.5
        assert data["max_concurrent_raids"] == 5
        assert data["id"] == str(existing.id)

    def test_scoped_to_user(self, client: TestClient, mock_repo: MockDispatcherRepo):
        # User-1 gets state
        client.get("/api/v1/tyr/dispatcher", headers=_auth_headers("user-1"))
        # User-2 gets their own state
        client.get("/api/v1/tyr/dispatcher", headers=_auth_headers("user-2"))
        assert len(mock_repo.states) == 2
        assert "user-1" in mock_repo.states
        assert "user-2" in mock_repo.states

    def test_returns_401_when_no_auth_headers(self, client: TestClient):
        resp = client.get("/api/v1/tyr/dispatcher")
        assert resp.status_code == 401


# -------------------------------------------------------------------
# PATCH /api/v1/tyr/dispatcher
# -------------------------------------------------------------------


class TestPatchDispatcherState:
    def test_updates_running_flag(self, client: TestClient):
        # First GET to create default
        client.get("/api/v1/tyr/dispatcher", headers=_auth_headers())

        resp = client.patch(
            "/api/v1/tyr/dispatcher",
            json={"running": False},
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["running"] is False
        assert data["threshold"] == 0.75  # unchanged

    def test_updates_threshold(self, client: TestClient):
        client.get("/api/v1/tyr/dispatcher", headers=_auth_headers())

        resp = client.patch(
            "/api/v1/tyr/dispatcher",
            json={"threshold": 0.9},
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["threshold"] == 0.9
        assert data["running"] is True  # unchanged

    def test_updates_threshold_boundary(self, client: TestClient):
        client.get("/api/v1/tyr/dispatcher", headers=_auth_headers())

        resp = client.patch(
            "/api/v1/tyr/dispatcher",
            json={"threshold": 1.0},
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        assert resp.json()["threshold"] == 1.0

    def test_updates_max_concurrent_raids(self, client: TestClient):
        client.get("/api/v1/tyr/dispatcher", headers=_auth_headers())

        resp = client.patch(
            "/api/v1/tyr/dispatcher",
            json={"max_concurrent_raids": 10},
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        assert resp.json()["max_concurrent_raids"] == 10

    def test_updates_auto_continue(self, client: TestClient):
        client.get("/api/v1/tyr/dispatcher", headers=_auth_headers())

        resp = client.patch(
            "/api/v1/tyr/dispatcher",
            json={"auto_continue": True},
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["auto_continue"] is True
        assert data["running"] is True  # unchanged

    def test_validates_threshold_range_too_high(self, client: TestClient):
        resp = client.patch(
            "/api/v1/tyr/dispatcher",
            json={"threshold": 1.5},
            headers=_auth_headers(),
        )
        assert resp.status_code == 422

    def test_validates_threshold_range_too_low(self, client: TestClient):
        resp = client.patch(
            "/api/v1/tyr/dispatcher",
            json={"threshold": -0.1},
            headers=_auth_headers(),
        )
        assert resp.status_code == 422

    def test_validates_max_concurrent_raids_too_low(self, client: TestClient):
        resp = client.patch(
            "/api/v1/tyr/dispatcher",
            json={"max_concurrent_raids": 0},
            headers=_auth_headers(),
        )
        assert resp.status_code == 422

    def test_validates_max_concurrent_raids_too_high(self, client: TestClient):
        resp = client.patch(
            "/api/v1/tyr/dispatcher",
            json={"max_concurrent_raids": 21},
            headers=_auth_headers(),
        )
        assert resp.status_code == 422

    def test_empty_body_returns_current_state(self, client: TestClient):
        client.get("/api/v1/tyr/dispatcher", headers=_auth_headers())

        resp = client.patch(
            "/api/v1/tyr/dispatcher",
            json={},
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["running"] is True
        assert data["threshold"] == 0.75
        assert data["max_concurrent_raids"] == 3
        assert data["auto_continue"] is False

    def test_multiple_fields_at_once(self, client: TestClient):
        resp = client.patch(
            "/api/v1/tyr/dispatcher",
            json={
                "running": False,
                "threshold": 0.6,
                "max_concurrent_raids": 7,
                "auto_continue": True,
            },
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["running"] is False
        assert data["threshold"] == 0.6
        assert data["max_concurrent_raids"] == 7
        assert data["auto_continue"] is True


# -------------------------------------------------------------------
# PostgresDispatcherRepository unit tests
# -------------------------------------------------------------------


class _FakeRow(dict):
    """Dict subclass that mimics asyncpg.Record subscript access."""

    def __getitem__(self, key: str) -> object:
        return super().__getitem__(key)


def _make_row(
    owner_id: str = "owner-1",
    running: bool = True,
    threshold: float = 0.8,
    max_concurrent_raids: int = 5,
    auto_continue: bool = False,
    updated_at: datetime | None = None,
) -> _FakeRow:
    return _FakeRow(
        id=uuid4(),
        owner_id=owner_id,
        running=running,
        threshold=threshold,
        max_concurrent_raids=max_concurrent_raids,
        auto_continue=auto_continue,
        updated_at=updated_at or datetime.now(UTC),
    )


class TestPostgresDispatcherRepository:
    def test_row_to_state(self):
        from tyr.adapters.postgres_dispatcher import PostgresDispatcherRepository

        row = _make_row()
        state = PostgresDispatcherRepository._row_to_state(row)
        assert state.id == row["id"]
        assert state.owner_id == "owner-1"
        assert state.running is True
        assert state.threshold == 0.8
        assert state.max_concurrent_raids == 5
        assert state.auto_continue is False

    def test_row_to_state_null_updated_at(self):
        from tyr.adapters.postgres_dispatcher import PostgresDispatcherRepository

        row = _make_row(updated_at=None)
        row["updated_at"] = None
        state = PostgresDispatcherRepository._row_to_state(row)
        assert state.updated_at is not None

    @pytest.mark.asyncio
    async def test_get_or_create(self):
        from unittest.mock import AsyncMock

        from tyr.adapters.postgres_dispatcher import PostgresDispatcherRepository

        row = _make_row(owner_id="user-x", running=True, threshold=0.75, max_concurrent_raids=3)
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=row)

        repo = PostgresDispatcherRepository(pool)
        state = await repo.get_or_create("user-x")

        pool.fetchrow.assert_called_once()
        assert state.owner_id == "user-x"
        assert state.running is True
        assert state.threshold == 0.75
        assert state.max_concurrent_raids == 3

    @pytest.mark.asyncio
    async def test_update_with_fields(self):
        from unittest.mock import AsyncMock

        from tyr.adapters.postgres_dispatcher import PostgresDispatcherRepository

        row = _make_row(owner_id="user-x", running=False, threshold=0.9, max_concurrent_raids=5)
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=row)

        repo = PostgresDispatcherRepository(pool)
        state = await repo.update("user-x", running=False, threshold=0.9)

        pool.fetchrow.assert_called_once()
        sql_arg = pool.fetchrow.call_args[0][0]
        assert "UPDATE dispatcher_state SET" in sql_arg
        assert state.running is False
        assert state.threshold == 0.9

    @pytest.mark.asyncio
    async def test_update_empty_fields_delegates_to_get_or_create(self):
        from unittest.mock import AsyncMock

        from tyr.adapters.postgres_dispatcher import PostgresDispatcherRepository

        row = _make_row(owner_id="user-x")
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=row)

        repo = PostgresDispatcherRepository(pool)
        state = await repo.update("user-x")

        # Should call get_or_create path (INSERT ... ON CONFLICT)
        sql_arg = pool.fetchrow.call_args[0][0]
        assert "INSERT INTO dispatcher_state" in sql_arg
        assert state.owner_id == "user-x"

    @pytest.mark.asyncio
    async def test_update_row_none_falls_back_to_get_or_create(self):
        from unittest.mock import AsyncMock

        from tyr.adapters.postgres_dispatcher import PostgresDispatcherRepository

        row = _make_row(owner_id="user-x")
        pool = AsyncMock()
        # First call (UPDATE) returns None, second call (INSERT) returns row
        pool.fetchrow = AsyncMock(side_effect=[None, row])

        repo = PostgresDispatcherRepository(pool)
        state = await repo.update("user-x", running=False)

        assert pool.fetchrow.call_count == 2
        assert state.owner_id == "user-x"

    @pytest.mark.asyncio
    async def test_update_ignores_disallowed_fields(self):
        from unittest.mock import AsyncMock

        from tyr.adapters.postgres_dispatcher import PostgresDispatcherRepository

        row = _make_row(owner_id="user-x")
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=row)

        repo = PostgresDispatcherRepository(pool)
        # Pass only disallowed fields — should delegate to get_or_create
        state = await repo.update("user-x", bogus_field="nope")

        sql_arg = pool.fetchrow.call_args[0][0]
        assert "INSERT INTO dispatcher_state" in sql_arg
        assert state.owner_id == "user-x"


# -------------------------------------------------------------------
# GET /api/v1/tyr/dispatcher/log
# -------------------------------------------------------------------


class TestGetActivityLog:
    def test_returns_empty_log_when_no_events(self, client: TestClient):
        resp = client.get("/api/v1/tyr/dispatcher/log", headers=_auth_headers())
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_all_emitted_events(self, client: TestClient, event_bus: InMemoryEventBus):
        import asyncio

        events = [
            TyrEvent(event="session.spawned", data={"session_id": "s1"}, owner_id="user-1"),
            TyrEvent(event="raid.state_changed", data={"raid_id": "r1"}, owner_id="user-1"),
            TyrEvent(event="session.stopped", data={"session_id": "s1"}, owner_id="user-1"),
        ]
        for e in events:
            asyncio.run(event_bus.emit(e))

        resp = client.get("/api/v1/tyr/dispatcher/log", headers=_auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3
        assert "session.spawned" in data[0]
        assert "raid.state_changed" in data[1]
        assert "session.stopped" in data[2]

    def test_n_param_limits_results(self, client: TestClient, event_bus: InMemoryEventBus):
        import asyncio

        for i in range(10):
            asyncio.run(event_bus.emit(TyrEvent(event="session.spawned", data={"i": i})))

        resp = client.get("/api/v1/tyr/dispatcher/log?n=3", headers=_auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3
        assert '"i": 9' in data[-1]  # newest last

    def test_limit_param_preserves_legacy_event_shape(
        self, client: TestClient, event_bus: InMemoryEventBus
    ):
        import asyncio

        for i in range(3):
            asyncio.run(event_bus.emit(TyrEvent(event="session.spawned", data={"i": i})))

        resp = client.get("/api/v1/tyr/dispatcher/log?limit=2", headers=_auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["events"]) == 2
        assert data["events"][-1]["data"]["i"] == 2

    def test_event_fields_present_in_response(
        self, client: TestClient, event_bus: InMemoryEventBus
    ):
        import asyncio

        e = TyrEvent(
            id="fixed-id",
            event="dispatcher.log",
            data={"msg": "dispatched"},
            owner_id="user-1",
        )
        asyncio.run(event_bus.emit(e))

        resp = client.get("/api/v1/tyr/dispatcher/log", headers=_auth_headers())
        assert resp.status_code == 200
        entry = resp.json()[0]
        assert "dispatcher.log" in entry
        assert '"msg": "dispatched"' in entry

    def test_limit_param_returns_legacy_event_fields(
        self, client: TestClient, event_bus: InMemoryEventBus
    ):
        import asyncio

        e = TyrEvent(
            id="fixed-id",
            event="dispatcher.log",
            data={"msg": "dispatched"},
            owner_id="user-1",
        )
        asyncio.run(event_bus.emit(e))

        resp = client.get("/api/v1/tyr/dispatcher/log?limit=1", headers=_auth_headers())
        assert resp.status_code == 200
        entry = resp.json()["events"][0]
        assert entry["id"] == "fixed-id"
        assert entry["event"] == "dispatcher.log"
        assert entry["data"] == {"msg": "dispatched"}
        assert entry["owner_id"] == "user-1"
        assert "timestamp" in entry

    def test_returns_401_when_no_auth(self, client: TestClient):
        resp = client.get("/api/v1/tyr/dispatcher/log")
        assert resp.status_code == 401

    def test_n_param_validates_minimum(self, client: TestClient):
        resp = client.get("/api/v1/tyr/dispatcher/log?n=0", headers=_auth_headers())
        assert resp.status_code == 422

    def test_n_param_validates_maximum(self, client: TestClient):
        resp = client.get("/api/v1/tyr/dispatcher/log?n=1001", headers=_auth_headers())
        assert resp.status_code == 422

    def test_n_defaults_to_100(self, client: TestClient, event_bus: InMemoryEventBus):
        import asyncio

        # Emit 50 events — all should be returned with default n=100
        for i in range(50):
            asyncio.run(event_bus.emit(TyrEvent(event="session.spawned", data={"i": i})))

        resp = client.get("/api/v1/tyr/dispatcher/log", headers=_auth_headers())
        assert resp.status_code == 200
        assert len(resp.json()) == 50
