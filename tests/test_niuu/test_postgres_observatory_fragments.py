"""Tests for the PostgreSQL-backed Observatory fragment inbox."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from niuu.adapters.postgres_observatory_fragments import (
    PostgresObservatoryFragmentRepository,
)
from niuu.domain.observatory import FragmentMeta, ObservatoryFragment, TopologyNode

RECEIVED_AT = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)


class FakePool:
    def __init__(self) -> None:
        self.fetch_result: list[dict] = []
        self.fetchrow_result: dict | None = None
        self.execute_result = "DELETE 1"
        self.fetch_calls: list[tuple[str, tuple[object, ...]]] = []
        self.fetchrow_calls: list[tuple[str, tuple[object, ...]]] = []
        self.execute_calls: list[tuple[str, tuple[object, ...]]] = []

    async def fetch(self, query: str, *args: object) -> list[dict]:
        self.fetch_calls.append((query, args))
        return self.fetch_result

    async def fetchrow(self, query: str, *args: object) -> dict | None:
        self.fetchrow_calls.append((query, args))
        return self.fetchrow_result

    async def execute(self, query: str, *args: object) -> str:
        self.execute_calls.append((query, args))
        return self.execute_result


def _fragment() -> ObservatoryFragment:
    return ObservatoryFragment(
        nodes=[
            TopologyNode(
                id="ravn-ivaldi",
                type_id="ravn_long",
                label="ivaldi",
                host_id="saehrimnir",
                persona="workshop steward",
            )
        ],
        meta=FragmentMeta(
            source_id="spark-1",
            source_kind="resident",
            source_name="ivaldi",
            realm_id="sparks",
            host_id="saehrimnir",
            revision="rev-1",
        ),
    )


def _row(payload: str | dict) -> dict:
    return {
        "source_id": "spark-1",
        "payload": payload,
        "received_at": RECEIVED_AT,
    }


@pytest.mark.asyncio
async def test_put_upserts_so_a_heartbeat_does_not_accumulate_rows() -> None:
    pool = FakePool()
    pool.fetchrow_result = _row(json.dumps(_fragment().model_dump(by_alias=True, mode="json")))
    repository = PostgresObservatoryFragmentRepository(pool)  # type: ignore[arg-type]

    await repository.put("spark-1", _fragment(), received_at=RECEIVED_AT)

    query, args = pool.fetchrow_calls[-1]
    assert "ON CONFLICT (source_id) DO UPDATE" in query
    assert args[0] == "spark-1"


@pytest.mark.asyncio
async def test_put_denormalizes_meta_for_querying() -> None:
    pool = FakePool()
    pool.fetchrow_result = _row(json.dumps(_fragment().model_dump(by_alias=True, mode="json")))
    repository = PostgresObservatoryFragmentRepository(pool)  # type: ignore[arg-type]

    await repository.put("spark-1", _fragment(), received_at=RECEIVED_AT)

    _query, args = pool.fetchrow_calls[-1]
    assert "resident" in args
    assert "sparks" in args
    assert "rev-1" in args


@pytest.mark.asyncio
async def test_put_accepts_a_fragment_with_no_meta() -> None:
    """Meta is optional on the contract, so persistence must not require it."""
    pool = FakePool()
    pool.fetchrow_result = _row(json.dumps({"nodes": [], "edges": []}))
    repository = PostgresObservatoryFragmentRepository(pool)  # type: ignore[arg-type]

    stored = await repository.put("anonymous", ObservatoryFragment(), received_at=RECEIVED_AT)

    assert stored.fragment.nodes == []


@pytest.mark.asyncio
async def test_round_trip_preserves_kind_specific_node_fields() -> None:
    """Adapters carry detail the contract does not name; it must survive the
    trip through JSONB."""
    pool = FakePool()
    pool.fetchrow_result = _row(json.dumps(_fragment().model_dump(by_alias=True, mode="json")))
    repository = PostgresObservatoryFragmentRepository(pool)  # type: ignore[arg-type]

    stored = await repository.put("spark-1", _fragment(), received_at=RECEIVED_AT)

    assert stored.fragment.nodes[0].model_dump()["persona"] == "workshop steward"


@pytest.mark.asyncio
async def test_reads_payload_whether_the_driver_decodes_jsonb_or_not() -> None:
    pool = FakePool()
    decoded = _fragment().model_dump(by_alias=True, mode="json")
    pool.fetch_result = [_row(decoded), _row(json.dumps(decoded))]
    repository = PostgresObservatoryFragmentRepository(pool)  # type: ignore[arg-type]

    fragments = await repository.list_fragments()

    assert len(fragments) == 2
    assert all(item.fragment.nodes[0].id == "ravn-ivaldi" for item in fragments)


@pytest.mark.asyncio
async def test_delete_reports_whether_a_row_was_removed() -> None:
    pool = FakePool()
    repository = PostgresObservatoryFragmentRepository(pool)  # type: ignore[arg-type]

    pool.execute_result = "DELETE 1"
    assert await repository.delete("spark-1") is True

    pool.execute_result = "DELETE 0"
    assert await repository.delete("never-seen") is False
