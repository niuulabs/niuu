"""File-backed registry for known Mimir mounts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

RegistryMountKind = Literal["local", "remote"]
RegistryMountLifecycle = Literal["registered", "ephemeral"]
RegistryMountHealth = Literal["healthy", "degraded", "down", "unknown"]


class MimirRegistryEntry(BaseModel):
    """A known Mimir instance that can be referenced by workflows or operators."""

    id: str = Field(default_factory=lambda: f"mimir-{uuid4().hex[:12]}")
    name: str
    kind: RegistryMountKind = "remote"
    lifecycle: RegistryMountLifecycle = "registered"
    role: str = "shared"
    url: str = ""
    path: str = ""
    categories: list[str] | None = None
    auth_ref: str | None = None
    default_read_priority: int = 10
    enabled: bool = True
    health_status: RegistryMountHealth = "unknown"
    health_message: str = ""
    desc: str = ""


class MimirRegistryStore:
    """Persist registry entries to a JSON file when a path is configured."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._entries: list[MimirRegistryEntry] = []
        self._load()

    def list_entries(self) -> list[MimirRegistryEntry]:
        return list(self._entries)

    def get_entry(self, entry_id: str) -> MimirRegistryEntry | None:
        for entry in self._entries:
            if entry.id == entry_id:
                return entry
        return None

    def save_entry(self, entry: MimirRegistryEntry) -> MimirRegistryEntry:
        for index, existing in enumerate(self._entries):
            if existing.id != entry.id:
                continue
            self._entries[index] = entry
            self._save()
            return entry

        self._entries.append(entry)
        self._save()
        return entry

    def delete_entry(self, entry_id: str) -> None:
        self._entries = [entry for entry in self._entries if entry.id != entry_id]
        self._save()

    def ensure_entry(
        self,
        *,
        name: str,
        role: str,
        kind: RegistryMountKind,
        lifecycle: RegistryMountLifecycle = "registered",
        url: str = "",
        path: str = "",
        categories: list[str] | None = None,
        default_read_priority: int = 10,
        desc: str = "",
    ) -> MimirRegistryEntry:
        existing = self._find_by_name(name)
        if existing is not None:
            return existing

        entry = MimirRegistryEntry(
            name=name,
            role=role,
            kind=kind,
            lifecycle=lifecycle,
            url=url,
            path=path,
            categories=categories,
            default_read_priority=default_read_priority,
            desc=desc,
        )
        self._entries.append(entry)
        self._save()
        return entry

    def _find_by_name(self, name: str) -> MimirRegistryEntry | None:
        for entry in self._entries:
            if entry.name == name:
                return entry
        return None

    def _load(self) -> None:
        if self._path is None:
            return

        if not self._path.is_file():
            return

        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return

        if not isinstance(raw, list):
            return

        entries: list[MimirRegistryEntry] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                entries.append(MimirRegistryEntry.model_validate(item))
            except Exception:
                continue
        self._entries = entries

    def _save(self) -> None:
        if self._path is None:
            return

        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = [entry.model_dump(mode="json") for entry in self._entries]
        self._path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
