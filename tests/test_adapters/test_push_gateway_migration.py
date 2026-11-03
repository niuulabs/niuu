"""The push gateway migration: its own number, idempotent, and adopted under its branch name."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NAME = "000099_push_gateway"


def test_no_other_volundr_migration_shares_its_number():
    names = sorted(path.name for path in (ROOT / "migrations").glob("*.up.sql"))
    number = NAME.split("_", 1)[0]
    assert [name for name in names if name.startswith(f"{number}_")] == [f"{NAME}.up.sql"]


def test_the_branch_name_of_the_same_sql_is_adopted():
    """forge/apns-push wrote this SQL as 000073; a ledger row of that name is adopted."""
    sql = (ROOT / "migrations" / f"{NAME}.up.sql").read_bytes()
    aliases = json.loads((ROOT / "migrations" / "lineage-aliases.json").read_text())
    assert aliases[f"{NAME}.up.sql"] == [
        {"filename": name, "sha256": hashlib.sha256(sql).hexdigest()}
        for name in ("000073_push_gateway.up.sql", "000096_push_gateway.up.sql")
    ]


def test_up_migration_only_uses_idempotent_ddl():
    sql = (ROOT / "migrations" / f"{NAME}.up.sql").read_text()
    assert re.findall(r"CREATE (?:TABLE|(?:UNIQUE )?INDEX)\b(?! IF NOT EXISTS)", sql) == []
    assert re.findall(r"ADD COLUMN\b(?! IF NOT EXISTS)", sql) == []
    assert re.findall(r"DROP CONSTRAINT\b(?! IF EXISTS)", sql) == []
    for column in ("kind", "environment", "activity_id", "last_push_at", "push_due"):
        assert f"ADD COLUMN IF NOT EXISTS {column} " in sql
    for table in ("forge_push_gateway_state", "forge_push_deliveries"):
        assert f"CREATE TABLE IF NOT EXISTS {table} (" in sql


def test_a_token_is_unique_per_kind_not_per_owner():
    sql = (ROOT / "migrations" / f"{NAME}.up.sql").read_text()
    assert "DROP CONSTRAINT IF EXISTS device_tokens_owner_id_token_key" in sql
    assert "CREATE UNIQUE INDEX IF NOT EXISTS idx_device_tokens_token_kind" in sql
    assert "ON device_tokens (token, kind)" in sql
    # Rows two owners registered for one token are reduced to the newest first.
    assert sql.index("DELETE FROM device_tokens older") < sql.index("CREATE UNIQUE INDEX")


def test_existing_tokens_become_production_alert_tokens():
    sql = (ROOT / "migrations" / f"{NAME}.up.sql").read_text()
    assert "kind TEXT NOT NULL DEFAULT 'alert'" in sql
    assert "environment TEXT NOT NULL DEFAULT 'production'" in sql


def test_down_migration_restores_the_old_shape():
    down = (ROOT / "migrations" / f"{NAME}.down.sql").read_text()
    assert down.count("DROP TABLE IF EXISTS") == 2
    assert "DELETE FROM device_tokens WHERE kind <> 'alert'" in down
    assert "ADD CONSTRAINT device_tokens_owner_id_token_key UNIQUE (owner_id, token)" in down
    # Other kinds go before the per-owner constraint returns and the column leaves.
    assert down.index("DELETE FROM device_tokens") < down.index("ADD CONSTRAINT")
    assert down.index("ADD CONSTRAINT") < down.index("DROP COLUMN IF EXISTS kind")
