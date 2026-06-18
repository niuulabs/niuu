"""File-backed resident workflow submission journal."""

from __future__ import annotations

import json
from dataclasses import MISSING, asdict
from pathlib import Path
from typing import Any

from ravn.ports.capability import WorkflowSubmissionRecord, WorkflowSubmissionStore


class FileWorkflowSubmissionStore(WorkflowSubmissionStore):
    """Durable JSON store for resident workflow submissions."""

    def __init__(self, path: str | Path = "~/.ravn/daemon/capability_submissions.json") -> None:
        self._path = Path(path).expanduser()

    async def upsert(self, record: WorkflowSubmissionRecord) -> WorkflowSubmissionRecord:
        records = self._read_all()
        records[record.submission_id] = record
        self._write_all(records)
        return record

    async def get(self, submission_id: str) -> WorkflowSubmissionRecord | None:
        return self._read_all().get(submission_id)

    async def list_submissions(
        self,
        *,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[WorkflowSubmissionRecord]:
        records = list(self._read_all().values())
        if status:
            records = [record for record in records if record.status == status]
        records.sort(key=lambda record: record.updated_at or record.created_at, reverse=True)
        if limit is not None:
            records = records[: max(limit, 0)]
        return records

    def _read_all(self) -> dict[str, WorkflowSubmissionRecord]:
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            return {}
        if not isinstance(raw, dict):
            return {}
        records: dict[str, WorkflowSubmissionRecord] = {}
        for key, value in raw.items():
            if not isinstance(value, dict):
                continue
            try:
                record = _record_from_dict(value)
            except (TypeError, ValueError):
                continue
            records[str(key)] = record
        return records

    def _write_all(self, records: dict[str, WorkflowSubmissionRecord]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {key: asdict(record) for key, record in sorted(records.items())}
        self._path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _record_from_dict(value: dict[str, Any]) -> WorkflowSubmissionRecord:
    fields = WorkflowSubmissionRecord.__dataclass_fields__
    data = {key: value.get(key) for key in fields}
    data["provenance"] = dict(value.get("provenance") or {})
    for key, field in fields.items():
        if data[key] is None:
            if field.default is not MISSING:
                data[key] = field.default
            elif field.default_factory is not MISSING:  # type: ignore[comparison-overlap]
                data[key] = field.default_factory()  # type: ignore[misc]
            else:
                data[key] = ""
    return WorkflowSubmissionRecord(**data)
