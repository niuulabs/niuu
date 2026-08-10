"""Persistence and validation for the Observatory entity-type registry."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import asyncpg

from observatory.data import REGISTRY as DEFAULT_REGISTRY

REGISTRY_KEY = "default"


class RegistryValidationError(ValueError):
    """Raised when a registry payload is structurally invalid."""


class RegistryNotFoundError(KeyError):
    """Raised when the requested registry document does not exist."""


class ObservatoryRegistryRepository(Protocol):
    """Persistence port for the Observatory registry document."""

    async def ensure_seeded(self) -> None:
        raise NotImplementedError

    async def get_registry(self) -> dict[str, Any]:
        raise NotImplementedError

    async def save_registry(self, registry: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    async def create_type(self, entity_type: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    async def update_type(self, type_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    async def delete_type(self, type_id: str) -> dict[str, Any]:
        raise NotImplementedError


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _to_iso(ts: datetime) -> str:
    return ts.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_timestamp(raw: str | datetime | None) -> datetime:
    if isinstance(raw, datetime):
        return raw.astimezone(UTC) if raw.tzinfo is not None else raw.replace(tzinfo=UTC)
    if isinstance(raw, str) and raw.strip():
        candidate = raw.strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(candidate)
        return parsed.astimezone(UTC) if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return _utc_now()


def _jsonable_payload(raw: object) -> dict[str, Any]:
    if isinstance(raw, dict):
        return deepcopy(raw)
    if isinstance(raw, str):
        return json.loads(raw)
    raise RegistryValidationError("Registry payload must be a JSON object.")


def _require_string(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise RegistryValidationError(f"{field} must be a string.")
    normalized = value.strip()
    if not normalized:
        raise RegistryValidationError(f"{field} must not be empty.")
    return normalized


def _require_int(value: object, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise RegistryValidationError(f"{field} must be an integer.")
    return value


def _require_string_list(value: object, *, field: str) -> list[str]:
    if not isinstance(value, list):
        raise RegistryValidationError(f"{field} must be a list of strings.")
    items: list[str] = []
    for item in value:
        items.append(_require_string(item, field=field))
    return items


def _normalize_field(field_payload: object) -> dict[str, Any]:
    if not isinstance(field_payload, dict):
        raise RegistryValidationError("type field entries must be objects.")
    field_type = _require_string(field_payload.get("type"), field="field.type")
    normalized: dict[str, Any] = {
        "key": _require_string(field_payload.get("key"), field="field.key"),
        "label": _require_string(field_payload.get("label"), field="field.label"),
        "type": field_type,
    }
    required = field_payload.get("required")
    if required is not None:
        if not isinstance(required, bool):
            raise RegistryValidationError("field.required must be a boolean.")
        normalized["required"] = required
    options = field_payload.get("options")
    if options is not None:
        normalized["options"] = _require_string_list(options, field="field.options")
    return normalized


def normalize_registry(registry: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a registry payload."""
    normalized = _jsonable_payload(registry)
    types_payload = normalized.get("types")
    if not isinstance(types_payload, list):
        raise RegistryValidationError("registry.types must be a list.")

    normalized_types: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw_type in types_payload:
        if not isinstance(raw_type, dict):
            raise RegistryValidationError("registry types must be objects.")
        type_id = _require_string(raw_type.get("id"), field="type.id")
        if type_id == "mimir_sub":
            continue
        if type_id in seen_ids:
            raise RegistryValidationError(f"Duplicate type id: {type_id}")
        seen_ids.add(type_id)
        normalized_type = {
            "id": type_id,
            "label": _require_string(raw_type.get("label"), field=f"{type_id}.label"),
            "rune": _require_string(raw_type.get("rune"), field=f"{type_id}.rune"),
            "icon": _require_string(raw_type.get("icon"), field=f"{type_id}.icon"),
            "shape": _require_string(raw_type.get("shape"), field=f"{type_id}.shape"),
            "color": _require_string(raw_type.get("color"), field=f"{type_id}.color"),
            "size": _require_int(raw_type.get("size"), field=f"{type_id}.size"),
            "border": _require_string(raw_type.get("border"), field=f"{type_id}.border"),
            "canContain": [
                child_id
                for child_id in _require_string_list(
                    raw_type.get("canContain", []), field=f"{type_id}.canContain"
                )
                if child_id != "mimir_sub"
            ],
            "parentTypes": [
                parent_id
                for parent_id in _require_string_list(
                    raw_type.get("parentTypes", []), field=f"{type_id}.parentTypes"
                )
                if parent_id != "mimir_sub"
            ],
            "category": _require_string(raw_type.get("category"), field=f"{type_id}.category"),
            "description": str(raw_type.get("description") or ""),
            "fields": [_normalize_field(item) for item in raw_type.get("fields", [])],
        }
        normalized_types.append(normalized_type)

    ids = {item["id"] for item in normalized_types}
    for item in normalized_types:
        for ref in item["parentTypes"]:
            if ref not in ids:
                raise RegistryValidationError(
                    f"{item['id']}.parentTypes references unknown type: {ref}"
                )
        for ref in item["canContain"]:
            if ref not in ids:
                raise RegistryValidationError(
                    f"{item['id']}.canContain references unknown type: {ref}"
                )

    version = normalized.get("version", 1)
    if not isinstance(version, int) or isinstance(version, bool):
        raise RegistryValidationError("registry.version must be an integer.")

    return {
        "version": version,
        "updatedAt": _to_iso(_parse_timestamp(normalized.get("updatedAt"))),
        "types": normalized_types,
    }


#: Fields the seed owns outright: the mark a type wears on the canvas.
#:
#: Everything else on a type — its label, description, containment rules and
#: field descriptors — is operator data and is never overwritten. `shape` is
#: not: it names one of a fixed set of glyphs the canvas knows how to draw, and
#: a stored value the vocabulary has dropped renders as a fallback box forever.
#: The seed is the only thing that knows the current vocabulary, so on a
#: version bump it wins. `size` and `icon` travel with it because the three
#: were chosen together — a rack at a dot's size is a smear, and an icon
#: naming a glyph the icon set no longer has renders as nothing at all.
_SEED_OWNED_KEYS = ("shape", "size", "icon")


def merge_seed_into(stored: dict[str, Any], seed: dict[str, Any]) -> dict[str, Any]:
    """Bring a stored registry up to the seed.

    Entity types are the configurable source of truth for what discovery may
    emit, so a registry frozen at first install silently caps the graph — any
    type added to the seed afterwards never reaches a deployed cluster.

    The merge is one-way: types the seed has and the store lacks are appended,
    field descriptors missing from an existing type are added, and the
    presentation keys the seed owns (`_SEED_OWNED_KEYS`) are refreshed on a
    version bump. Everything an operator can meaningfully edit — labels,
    descriptions, containment, their own types and fields — is left alone.
    Returns the stored registry unchanged when there is nothing to do, so
    callers can skip a pointless write.
    """
    stored_types = list(stored.get("types") or [])
    by_id = {str(entry.get("id", "")): entry for entry in stored_types}
    changed = False
    upgrading = int(stored.get("version") or 0) < int(seed.get("version") or 0)

    for seed_type in seed.get("types") or []:
        type_id = str(seed_type.get("id", ""))
        if not type_id:
            continue
        existing = by_id.get(type_id)
        if existing is None:
            stored_types.append(deepcopy(seed_type))
            changed = True
            continue

        if upgrading:
            for key in _SEED_OWNED_KEYS:
                if key in seed_type and existing.get(key) != seed_type[key]:
                    existing[key] = deepcopy(seed_type[key])
                    changed = True

        known_fields = {str(f.get("key", "")) for f in existing.get("fields") or []}
        for field in seed_type.get("fields") or []:
            if str(field.get("key", "")) not in known_fields:
                existing.setdefault("fields", []).append(deepcopy(field))
                changed = True

    seed_version = int(seed.get("version") or 0)
    if int(stored.get("version") or 0) < seed_version:
        changed = True

    if not changed:
        return stored

    merged = {
        **stored,
        "version": max(int(stored.get("version") or 0), seed_version),
        "types": stored_types,
    }
    return normalize_registry(merged)


def seed_registry_payload() -> dict[str, Any]:
    """Return a deep copy of the built-in Observatory registry seed."""
    return normalize_registry(deepcopy(DEFAULT_REGISTRY))


def _remove_references(registry: dict[str, Any], type_id: str) -> None:
    for entity_type in registry["types"]:
        entity_type["parentTypes"] = [
            parent_id for parent_id in entity_type["parentTypes"] if parent_id != type_id
        ]
        entity_type["canContain"] = [
            child_id for child_id in entity_type["canContain"] if child_id != type_id
        ]


def _rename_references(registry: dict[str, Any], old_id: str, new_id: str) -> None:
    for entity_type in registry["types"]:
        entity_type["parentTypes"] = [
            new_id if parent_id == old_id else parent_id for parent_id in entity_type["parentTypes"]
        ]
        entity_type["canContain"] = [
            new_id if child_id == old_id else child_id for child_id in entity_type["canContain"]
        ]


class PostgresObservatoryRegistryRepository:
    """PostgreSQL-backed registry document store."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def ensure_seeded(self) -> None:
        existing = await self._pool.fetchval(
            "SELECT COUNT(*) FROM observatory_registries WHERE registry_key = $1",
            REGISTRY_KEY,
        )
        if int(existing or 0) > 0:
            await self._upgrade_seeded()
            return

        registry = seed_registry_payload()
        await self._upsert_registry(registry)

    async def _upgrade_seeded(self) -> None:
        """Fold newly seeded types into an already-seeded registry."""
        row = await self._pool.fetchrow(
            "SELECT payload FROM observatory_registries WHERE registry_key = $1",
            REGISTRY_KEY,
        )
        if row is None:
            return
        stored = normalize_registry(json.loads(row["payload"]))
        merged = merge_seed_into(stored, seed_registry_payload())
        if merged is not stored:
            await self._upsert_registry(merged)

    async def get_registry(self) -> dict[str, Any]:
        row = await self._pool.fetchrow(
            """
            SELECT version, updated_at, payload
            FROM observatory_registries
            WHERE registry_key = $1
            """,
            REGISTRY_KEY,
        )
        if row is None:
            await self.ensure_seeded()
            row = await self._pool.fetchrow(
                """
                SELECT version, updated_at, payload
                FROM observatory_registries
                WHERE registry_key = $1
                """,
                REGISTRY_KEY,
            )
        if row is None:
            raise RegistryNotFoundError(REGISTRY_KEY)
        payload = normalize_registry(_jsonable_payload(row["payload"]))
        payload["version"] = int(row["version"])
        payload["updatedAt"] = _to_iso(row["updated_at"])
        return payload

    async def save_registry(self, registry: dict[str, Any]) -> dict[str, Any]:
        current = await self.get_registry()
        normalized = normalize_registry(registry)
        if normalized["types"] == current["types"]:
            return current

        normalized["version"] = current["version"] + 1
        normalized["updatedAt"] = _to_iso(_utc_now())
        await self._upsert_registry(normalized)
        return normalized

    async def create_type(self, entity_type: dict[str, Any]) -> dict[str, Any]:
        registry = await self.get_registry()
        registry["types"].append(entity_type)
        return await self.save_registry(registry)

    async def update_type(self, type_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        registry = await self.get_registry()
        for index, entity_type in enumerate(registry["types"]):
            if entity_type["id"] != type_id:
                continue
            new_id = patch.get("id", type_id)
            if isinstance(new_id, str):
                new_id = new_id.strip()
            if new_id != type_id:
                _rename_references(registry, type_id, str(new_id))
            updated_type = {**entity_type, **patch}
            registry["types"][index] = updated_type
            return await self.save_registry(registry)
        raise RegistryNotFoundError(type_id)

    async def delete_type(self, type_id: str) -> dict[str, Any]:
        registry = await self.get_registry()
        if not any(entity_type["id"] == type_id for entity_type in registry["types"]):
            raise RegistryNotFoundError(type_id)
        registry["types"] = [
            entity_type for entity_type in registry["types"] if entity_type["id"] != type_id
        ]
        _remove_references(registry, type_id)
        return await self.save_registry(registry)

    async def _upsert_registry(self, registry: dict[str, Any]) -> None:
        await self._pool.execute(
            """
            INSERT INTO observatory_registries (registry_key, version, updated_at, payload)
            VALUES ($1, $2, $3, $4::jsonb)
            ON CONFLICT (registry_key) DO UPDATE SET
                version = EXCLUDED.version,
                updated_at = EXCLUDED.updated_at,
                payload = EXCLUDED.payload
            """,
            REGISTRY_KEY,
            int(registry["version"]),
            _parse_timestamp(registry.get("updatedAt")),
            json.dumps(registry),
        )


@dataclass
class InMemoryObservatoryRegistryRepository:
    """Test-friendly in-memory implementation of the registry repository."""

    registry: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.registry is None:
            self.registry = seed_registry_payload()
        else:
            self.registry = normalize_registry(self.registry)

    async def ensure_seeded(self) -> None:
        if self.registry is None:
            self.registry = seed_registry_payload()
            return
        self.registry = merge_seed_into(self.registry, seed_registry_payload())

    async def get_registry(self) -> dict[str, Any]:
        await self.ensure_seeded()
        return deepcopy(self.registry)

    async def save_registry(self, registry: dict[str, Any]) -> dict[str, Any]:
        current = await self.get_registry()
        normalized = normalize_registry(registry)
        if normalized["types"] == current["types"]:
            return current
        normalized["version"] = current["version"] + 1
        normalized["updatedAt"] = _to_iso(_utc_now())
        self.registry = normalized
        return deepcopy(normalized)

    async def create_type(self, entity_type: dict[str, Any]) -> dict[str, Any]:
        registry = await self.get_registry()
        registry["types"].append(entity_type)
        return await self.save_registry(registry)

    async def update_type(self, type_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        registry = await self.get_registry()
        for index, entity_type in enumerate(registry["types"]):
            if entity_type["id"] != type_id:
                continue
            new_id = patch.get("id", type_id)
            if isinstance(new_id, str):
                new_id = new_id.strip()
            if new_id != type_id:
                _rename_references(registry, type_id, str(new_id))
            registry["types"][index] = {**entity_type, **patch}
            return await self.save_registry(registry)
        raise RegistryNotFoundError(type_id)

    async def delete_type(self, type_id: str) -> dict[str, Any]:
        registry = await self.get_registry()
        if not any(entity_type["id"] == type_id for entity_type in registry["types"]):
            raise RegistryNotFoundError(type_id)
        registry["types"] = [
            entity_type for entity_type in registry["types"] if entity_type["id"] != type_id
        ]
        _remove_references(registry, type_id)
        return await self.save_registry(registry)
