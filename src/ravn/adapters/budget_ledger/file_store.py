"""File-backed budget ledger for local development and mini mode.

Same durability contract as ``ravn.adapters.trigger_store.file_store``: atomic
writes (temp file + ``os.replace``), and a corrupt file raises at load time
instead of silently starting from an empty ledger — silently losing recorded
spend would make the daily cap meaningless without anyone noticing.
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from datetime import date
from pathlib import Path
from typing import Any

from ravn.domain.budget_totals import EMPTY_BUDGET_TOTALS, BudgetTotals
from ravn.ports.budget_ledger import BudgetLedgerPort

_REQUIRED_FIELDS = ("ravn_id", "tenant_id", "day", "spent_usd", "cap_usd", "warn_at")


def _key(tenant_id: str, ravn_id: str, day: date) -> str:
    """Row key. tenant_id comes first so two tenants can never collide on
    the same ravn_id/day the way a ravn_id-only key would."""
    return f"{tenant_id}\u0000{ravn_id}\u0000{day.isoformat()}"


class FileBudgetLedger(BudgetLedgerPort):
    """Durable single-file ledger, keyed by ``(tenant_id, ravn_id, day)``."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path).expanduser()

    async def record_spend(
        self,
        ravn_id: str,
        *,
        tenant_id: str,
        day: date,
        cost_usd: float,
        cap_usd: float,
        warn_at: float,
    ) -> None:
        rows = self._load()
        key = _key(tenant_id, ravn_id, day)
        existing = rows.get(key, {"ravn_id": ravn_id, "tenant_id": tenant_id, "spent_usd": 0.0})
        existing["spent_usd"] = existing["spent_usd"] + cost_usd
        existing["cap_usd"] = cap_usd
        existing["warn_at"] = warn_at
        rows[key] = existing
        self._save(rows)

    async def totals_for(self, ravn_id: str, *, tenant_id: str, day: date) -> BudgetTotals:
        row = self._load().get(_key(tenant_id, ravn_id, day))
        if row is None:
            return EMPTY_BUDGET_TOTALS
        return _totals_from_row(row)

    async def fleet_totals(self, *, day: date, tenant_id: str) -> BudgetTotals:
        prefix = f"{tenant_id}\u0000"
        suffix = f"\u0000{day.isoformat()}"
        rows = [
            row
            for key, row in self._load().items()
            if key.startswith(prefix) and key.endswith(suffix)
        ]
        return _aggregate(rows)

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"budget ledger: {self._path} is not readable/valid JSON — "
                "fix or remove the file; it is not safe to guess its contents"
            ) from exc
        rows: dict[str, dict[str, Any]] = {}
        for entry in raw.get("rows", []):
            missing = [field for field in _REQUIRED_FIELDS if field not in entry]
            if missing:
                raise ValueError(
                    f"budget ledger: {self._path} has a row missing {missing} — "
                    "fix or remove the corrupt row; it is not silently skipped"
                )
            key = _key(entry["tenant_id"], entry["ravn_id"], date.fromisoformat(entry["day"]))
            rows[key] = {
                "ravn_id": entry["ravn_id"],
                "tenant_id": entry["tenant_id"],
                "spent_usd": float(entry["spent_usd"]),
                "cap_usd": float(entry["cap_usd"]),
                "warn_at": float(entry["warn_at"]),
            }
        return rows

    def _save(self, rows: dict[str, dict[str, Any]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "rows": [
                {
                    "ravn_id": row["ravn_id"],
                    "tenant_id": row["tenant_id"],
                    "day": key.split("\u0000", 2)[2],
                    "spent_usd": row["spent_usd"],
                    "cap_usd": row["cap_usd"],
                    "warn_at": row["warn_at"],
                }
                for key, row in rows.items()
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


def _totals_from_row(row: dict[str, Any]) -> BudgetTotals:
    return BudgetTotals(
        spent_usd=row["spent_usd"],
        cap_usd=row["cap_usd"],
        warn_at=row["warn_at"],
    )


def _aggregate(rows: list[dict[str, Any]]) -> BudgetTotals:
    if not rows:
        return EMPTY_BUDGET_TOTALS
    return BudgetTotals(
        spent_usd=sum(row["spent_usd"] for row in rows),
        cap_usd=sum(row["cap_usd"] for row in rows),
        warn_at=min(row["warn_at"] for row in rows),
    )
