"""File-backed trigger store for local development and mini mode.

Durable single-file store, atomic on every write (temp file + ``os.replace``)
so a crash mid-write cannot leave a half-written file. A corrupt file — bad
JSON, or a row missing a required field — raises at load time rather than
silently dropping the offending row: a trigger that quietly vanished from the
store is a data-loss bug that must be loud, not a "the store degrades
gracefully" feature (see ``.claude/rules/no-fallbacks.md``).
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from ravn.domain.trigger_record import TriggerRecord
from ravn.ports.trigger_store import TriggerStorePort

_REQUIRED_FIELDS = (
    "id",
    "kind",
    "persona_name",
    "spec",
    "enabled",
    "owner_id",
    "tenant_id",
    "created_at",
)


class FileTriggerStore(TriggerStorePort):
    """Durable single-file trigger store, keyed by trigger id."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path).expanduser()

    async def list_triggers(self, *, tenant_id: str) -> list[TriggerRecord]:
        items = self._load()
        matching = [item for item in items.values() if item.tenant_id == tenant_id]
        return sorted(matching, key=lambda item: item.created_at, reverse=True)

    async def get_trigger(self, trigger_id: str) -> TriggerRecord | None:
        return self._load().get(trigger_id)

    async def create_trigger(
        self,
        *,
        kind: str,
        persona_name: str,
        spec: str,
        enabled: bool,
        owner_id: str,
        tenant_id: str,
        repo: str = "",
    ) -> TriggerRecord:
        items = self._load()
        record = TriggerRecord(
            id=str(uuid4()),
            kind=kind,
            persona_name=persona_name,
            spec=spec,
            enabled=enabled,
            owner_id=owner_id,
            tenant_id=tenant_id,
            created_at=datetime.now(tz=UTC),
            repo=repo,
        )
        items[record.id] = record
        self._save(items)
        return record

    async def delete_trigger(self, trigger_id: str) -> bool:
        items = self._load()
        if trigger_id not in items:
            return False
        del items[trigger_id]
        self._save(items)
        return True

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load(self) -> dict[str, TriggerRecord]:
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"trigger store: {self._path} is not readable/valid JSON — "
                "fix or remove the file; it is not safe to guess its contents"
            ) from exc
        items: dict[str, TriggerRecord] = {}
        for entry in raw.get("items", []):
            missing = [field for field in _REQUIRED_FIELDS if field not in entry]
            if missing:
                raise ValueError(
                    f"trigger store: {self._path} has an entry missing {missing} — "
                    "fix or remove the corrupt row; it is not silently skipped"
                )
            items[entry["id"]] = TriggerRecord(
                id=entry["id"],
                kind=entry["kind"],
                persona_name=entry["persona_name"],
                spec=entry["spec"],
                enabled=bool(entry["enabled"]),
                owner_id=entry["owner_id"],
                tenant_id=entry["tenant_id"],
                created_at=datetime.fromisoformat(entry["created_at"]),
                repo=str(entry.get("repo") or ""),
            )
        return items

    def _save(self, items: dict[str, TriggerRecord]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "items": [
                {
                    "id": item.id,
                    "kind": item.kind,
                    "persona_name": item.persona_name,
                    "spec": item.spec,
                    "enabled": item.enabled,
                    "owner_id": item.owner_id,
                    "tenant_id": item.tenant_id,
                    "created_at": item.created_at.isoformat(),
                    "repo": item.repo,
                }
                for item in items.values()
            ]
        }
        data = json.dumps(payload, indent=2)
        fd, tmp_path = tempfile.mkstemp(dir=self._path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(data)
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, self._path)
        except BaseException:
            with suppress(OSError):
                os.unlink(tmp_path)
            raise
