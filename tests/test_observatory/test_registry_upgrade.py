"""The registry must keep up with the seed after first install.

`ensure_seeded` used to return early whenever a row existed, so a deployed
Observatory stayed frozen at whatever it seeded on day one and every entity type
added afterwards never reached it.
"""

from __future__ import annotations

from typing import Any

import pytest

from observatory.registry import (
    InMemoryObservatoryRegistryRepository,
    merge_seed_into,
    seed_registry_payload,
)


def _type(type_id: str, *, label: str = "", fields: list[dict[str, Any]] | None = None) -> dict:
    return {
        "id": type_id,
        "label": label or type_id,
        "rune": "x",
        "icon": "circle",
        "shape": "dot",
        "color": "ice-300",
        "size": 6,
        "border": "solid",
        "canContain": [],
        "parentTypes": [],
        "category": "infrastructure",
        "description": "",
        "fields": fields or [],
    }


def _stale_registry(types: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "version": 1,
        "updatedAt": "2026-01-01T00:00:00Z",
        "types": types if types is not None else [_type("service")],
    }


class TestMergeSeedInto:
    def test_adds_types_the_store_never_had(self) -> None:
        merged = merge_seed_into(_stale_registry(), seed_registry_payload())

        ids = {entry["id"] for entry in merged["types"]}
        assert {"realm", "model", "run", "ravn_run"} <= ids

    def test_preserves_an_operator_edit(self) -> None:
        stored = _stale_registry([_type("service", label="Renamed By Operator")])

        merged = merge_seed_into(stored, seed_registry_payload())

        service = next(entry for entry in merged["types"] if entry["id"] == "service")
        assert service["label"] == "Renamed By Operator"

    def test_does_not_resurrect_a_type_the_operator_kept(self) -> None:
        """The merge is additive, so an edited type is never overwritten."""
        stored = _stale_registry([_type("mimir", label="Our Mimir")])

        merged = merge_seed_into(stored, seed_registry_payload())

        mimir = next(entry for entry in merged["types"] if entry["id"] == "mimir")
        assert mimir["label"] == "Our Mimir"

    def test_refreshes_the_glyph_a_type_wears(self) -> None:
        """A shape the canvas no longer draws has to be replaced, not kept.

        `shape` names one of a fixed set of marks. A registry frozen on a name
        the vocabulary has dropped renders every one of those entities as a
        fallback box, which looks like a bug and cannot be fixed by the
        operator without hand-editing the registry.
        """
        stored = _stale_registry([_type("host")])

        merged = merge_seed_into(stored, seed_registry_payload())

        host = next(entry for entry in merged["types"] if entry["id"] == "host")
        seed_host = next(
            entry for entry in seed_registry_payload()["types"] if entry["id"] == "host"
        )
        assert host["shape"] == seed_host["shape"]
        assert host["size"] == seed_host["size"]

    def test_leaves_the_glyph_alone_once_the_version_matches(self) -> None:
        """Only an upgrade refreshes presentation; a steady state does not."""
        current = seed_registry_payload()
        current["types"][0]["shape"] = "ring-dashed"

        merged = merge_seed_into(current, seed_registry_payload())

        assert merged["types"][0]["shape"] == "ring-dashed"

    def test_adds_missing_field_descriptors_to_an_existing_type(self) -> None:
        stored = _stale_registry(
            [_type("mimir", fields=[{"key": "pages", "label": "Pages", "type": "number"}])]
        )

        merged = merge_seed_into(stored, seed_registry_payload())

        mimir = next(entry for entry in merged["types"] if entry["id"] == "mimir")
        keys = {field["key"] for field in mimir["fields"]}
        assert "pages" in keys
        assert len(keys) > 1

    def test_bumps_the_stored_version(self) -> None:
        merged = merge_seed_into(_stale_registry(), seed_registry_payload())

        assert merged["version"] >= seed_registry_payload()["version"]

    def test_returns_the_input_untouched_when_already_current(self) -> None:
        """No change means no write."""
        current = seed_registry_payload()

        assert merge_seed_into(current, seed_registry_payload()) is current


class TestInMemoryEnsureSeeded:
    @pytest.mark.asyncio
    async def test_upgrades_an_already_seeded_registry(self) -> None:
        repository = InMemoryObservatoryRegistryRepository(registry=_stale_registry())

        await repository.ensure_seeded()

        ids = {entry["id"] for entry in (await repository.get_registry())["types"]}
        assert "realm" in ids

    @pytest.mark.asyncio
    async def test_seeds_an_empty_registry(self) -> None:
        repository = InMemoryObservatoryRegistryRepository()

        await repository.ensure_seeded()

        assert (await repository.get_registry())["types"]

    @pytest.mark.asyncio
    async def test_is_idempotent(self) -> None:
        repository = InMemoryObservatoryRegistryRepository(registry=_stale_registry())

        await repository.ensure_seeded()
        first = await repository.get_registry()
        await repository.ensure_seeded()

        assert (await repository.get_registry())["types"] == first["types"]
