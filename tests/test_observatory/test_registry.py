"""Unit tests for the Observatory registry repository helpers."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime

import pytest

from observatory.registry import (
    InMemoryObservatoryRegistryRepository,
    PostgresObservatoryRegistryRepository,
    RegistryNotFoundError,
    RegistryValidationError,
    _jsonable_payload,
    _parse_timestamp,
    normalize_registry,
    seed_registry_payload,
)


class _FakePool:
    def __init__(self, *, row: dict[str, object] | None = None, persist: bool = True) -> None:
        self.row = deepcopy(row)
        self.persist = persist
        self.execute_calls = 0

    async def fetchval(self, _query: str, _key: str) -> int:
        return 1 if self.row is not None else 0

    async def fetchrow(self, _query: str, _key: str) -> dict[str, object] | None:
        return deepcopy(self.row)

    async def execute(
        self,
        _query: str,
        _key: str,
        version: int,
        updated_at: datetime,
        payload: str,
    ) -> None:
        self.execute_calls += 1
        if self.persist:
            self.row = {
                "version": version,
                "updated_at": updated_at,
                "payload": payload,
            }


@pytest.mark.asyncio
async def test_save_registry_bumps_version() -> None:
    repo = InMemoryObservatoryRegistryRepository()
    registry = await repo.get_registry()
    registry["types"][0]["label"] = "Realm Prime"
    saved = await repo.save_registry(registry)
    # A save is one bump past whatever the seed ships, not a fixed number:
    # pinning the literal makes every seed edit a test edit.
    assert saved["version"] == seed_registry_payload()["version"] + 1
    assert saved["types"][0]["label"] == "Realm Prime"


@pytest.mark.asyncio
async def test_update_type_rename_updates_references() -> None:
    repo = InMemoryObservatoryRegistryRepository()
    saved = await repo.update_type("cluster", {"id": "cluster_prime"})
    realm = next(item for item in saved["types"] if item["id"] == "realm")
    volundr = next(item for item in saved["types"] if item["id"] == "volundr")
    assert "cluster_prime" in realm["canContain"]
    assert "cluster_prime" in volundr["parentTypes"]


@pytest.mark.asyncio
async def test_delete_type_removes_references() -> None:
    repo = InMemoryObservatoryRegistryRepository()
    saved = await repo.delete_type("cluster")
    realm = next(item for item in saved["types"] if item["id"] == "realm")
    volundr = next(item for item in saved["types"] if item["id"] == "volundr")
    assert "cluster" not in realm["canContain"]
    assert "cluster" not in volundr["parentTypes"]


@pytest.mark.asyncio
async def test_delete_missing_type_raises() -> None:
    repo = InMemoryObservatoryRegistryRepository()
    with pytest.raises(RegistryNotFoundError):
        await repo.delete_type("missing")


@pytest.mark.asyncio
async def test_in_memory_create_type_and_noop_save() -> None:
    repo = InMemoryObservatoryRegistryRepository()
    registry = await repo.get_registry()
    created = await repo.create_type(
        {
            "id": "watchtower",
            "label": "Watchtower",
            "rune": "W",
            "icon": "Radar",
            "shape": "diamond",
            "color": "#123456",
            "size": 96,
            "border": "dashed",
            "canContain": [],
            "parentTypes": ["cluster"],
            "category": "service",
            "description": "Synthetic test type",
            "fields": [],
        }
    )
    assert any(item["id"] == "watchtower" for item in created["types"])
    saved = await repo.save_registry(created)
    assert saved["version"] == created["version"]
    assert registry["version"] < created["version"]


@pytest.mark.asyncio
async def test_in_memory_update_missing_type_raises() -> None:
    repo = InMemoryObservatoryRegistryRepository()
    with pytest.raises(RegistryNotFoundError):
        await repo.update_type("missing", {"label": "Missing"})


@pytest.mark.asyncio
async def test_postgres_repo_seeds_and_loads_registry() -> None:
    pool = _FakePool()
    repo = PostgresObservatoryRegistryRepository(pool)  # type: ignore[arg-type]
    await repo.ensure_seeded()
    loaded = await repo.get_registry()
    assert pool.execute_calls == 1
    assert loaded["version"] == seed_registry_payload()["version"]
    assert any(item["id"] == "realm" for item in loaded["types"])
    assert any(item["id"] == "namespace" for item in loaded["types"])


@pytest.mark.asyncio
async def test_postgres_repo_save_noop_preserves_version() -> None:
    seeded = seed_registry_payload()
    pool = _FakePool(
        row={
            "version": seeded["version"],
            "updated_at": _parse_timestamp(seeded["updatedAt"]),
            "payload": seeded,
        }
    )
    repo = PostgresObservatoryRegistryRepository(pool)  # type: ignore[arg-type]
    saved = await repo.save_registry(seeded)
    assert saved["version"] == seeded["version"]
    assert pool.execute_calls == 0


@pytest.mark.asyncio
async def test_postgres_repo_create_update_delete_and_missing_paths() -> None:
    pool = _FakePool()
    repo = PostgresObservatoryRegistryRepository(pool)  # type: ignore[arg-type]

    created = await repo.create_type(
        {
            "id": "watchtower",
            "label": "Watchtower",
            "rune": "W",
            "icon": "Radar",
            "shape": "diamond",
            "color": "#123456",
            "size": 96,
            "border": "dashed",
            "canContain": [],
            "parentTypes": ["cluster"],
            "category": "service",
            "description": "Synthetic test type",
            "fields": [],
        }
    )
    assert any(item["id"] == "watchtower" for item in created["types"])

    updated = await repo.update_type("watchtower", {"id": "watchtower_prime"})
    assert any(item["id"] == "watchtower_prime" for item in updated["types"])

    deleted = await repo.delete_type("watchtower_prime")
    assert not any(item["id"] == "watchtower_prime" for item in deleted["types"])

    with pytest.raises(RegistryNotFoundError):
        await repo.update_type("missing", {"label": "Missing"})
    with pytest.raises(RegistryNotFoundError):
        await repo.delete_type("missing")


@pytest.mark.asyncio
async def test_postgres_repo_get_registry_raises_if_row_is_still_missing() -> None:
    pool = _FakePool(persist=False)
    repo = PostgresObservatoryRegistryRepository(pool)  # type: ignore[arg-type]
    with pytest.raises(RegistryNotFoundError):
        await repo.get_registry()


def test_jsonable_payload_accepts_json_string_and_rejects_non_object() -> None:
    loaded = _jsonable_payload('{"version": 1, "types": []}')
    assert loaded["version"] == 1
    with pytest.raises(RegistryValidationError):
        _jsonable_payload([])


def test_parse_timestamp_handles_strings_and_naive_datetimes() -> None:
    parsed = _parse_timestamp("2026-05-13T12:00:00Z")
    assert parsed.tzinfo is UTC
    naive = _parse_timestamp(datetime(2026, 5, 13, 12, 0, 0))
    assert naive.tzinfo is UTC


def test_normalize_registry_rejects_unknown_references() -> None:
    registry = seed_registry_payload()
    registry["types"][0]["canContain"].append("missing")
    with pytest.raises(RegistryValidationError):
        normalize_registry(registry)


def test_normalize_registry_rejects_duplicate_ids() -> None:
    registry = seed_registry_payload()
    registry["types"].append({**registry["types"][0]})
    with pytest.raises(RegistryValidationError):
        normalize_registry(registry)


def test_normalize_registry_strips_deprecated_mimir_sub_type() -> None:
    registry = seed_registry_payload()
    mimir = next(item for item in registry["types"] if item["id"] == "mimir")
    mimir["canContain"] = ["mimir_sub"]
    registry["types"].append(
        {
            "id": "mimir_sub",
            "label": "Sub-Mimir",
            "rune": "ᛗ",
            "icon": "book-marked",
            "shape": "mimir-small",
            "color": "ice-200",
            "size": 18,
            "border": "solid",
            "canContain": [],
            "parentTypes": ["mimir"],
            "category": "knowledge",
            "description": "Deprecated",
            "fields": [],
        }
    )

    normalized = normalize_registry(registry)
    assert not any(item["id"] == "mimir_sub" for item in normalized["types"])
    normalized_mimir = next(item for item in normalized["types"] if item["id"] == "mimir")
    assert "mimir_sub" not in normalized_mimir["canContain"]


def test_normalize_registry_rejects_invalid_shape_fields_and_version() -> None:
    missing_types = {"version": 1, "types": "nope"}
    with pytest.raises(RegistryValidationError):
        normalize_registry(missing_types)

    registry = seed_registry_payload()
    registry["types"][0]["fields"] = [{"key": "x", "label": "X", "type": "text", "required": "yes"}]
    with pytest.raises(RegistryValidationError):
        normalize_registry(registry)

    registry = seed_registry_payload()
    registry["types"][0]["fields"] = [{"key": "x", "label": "X", "type": "text", "options": "bad"}]
    with pytest.raises(RegistryValidationError):
        normalize_registry(registry)

    registry = seed_registry_payload()
    registry["version"] = True
    with pytest.raises(RegistryValidationError):
        normalize_registry(registry)
