"""Notification migrations are idempotent SQL."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NAME = "000084_forge_notifications"


def test_up_migration_only_uses_idempotent_ddl():
    sql = (ROOT / "migrations" / f"{NAME}.up.sql").read_text()
    creates = re.findall(r"CREATE (?:TABLE|INDEX)\b(?! IF NOT EXISTS)", sql)
    assert creates == []
    for table in (
        "forge_notifications",
        "forge_notification_read_states",
        "forge_notification_rules",
        "forge_notification_deliveries",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table} (" in sql
    down = (ROOT / "migrations" / f"{NAME}.down.sql").read_text()
    assert down.count("DROP TABLE IF EXISTS") == 4
    # Children first, so the down migration never trips a foreign key.
    assert down.index("forge_notification_deliveries") < down.index("forge_notifications;")


RATE_INDEX = "000085_forge_notification_delivery_rate_index"


def test_rate_index_is_partial_and_idempotent():
    up = (ROOT / "migrations" / f"{RATE_INDEX}.up.sql").read_text()
    assert "CREATE INDEX IF NOT EXISTS idx_forge_notification_deliveries_rule_delivered" in up
    assert "(rule_id, delivered_at)" in up and "WHERE status = 'delivered'" in up
    down = (ROOT / "migrations" / f"{RATE_INDEX}.down.sql").read_text()
    assert "DROP INDEX IF EXISTS idx_forge_notification_deliveries_rule_delivered" in down


def test_individual_reads_migration_is_additive_idempotent_and_reader_scoped():
    up = (ROOT / "migrations/000086_forge_notification_reads.up.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS forge_notification_reads" in up
    assert "PRIMARY KEY (user_id, notification_id)" in up
    assert "REFERENCES forge_notifications(id) ON DELETE CASCADE" in up
    assert "CREATE INDEX IF NOT EXISTS" in up
    assert not re.search(r"\b(DELETE|UPDATE|ALTER)\s+(FROM\s+)?forge_notification_read_states", up)
    down = (ROOT / "migrations/000086_forge_notification_reads.down.sql").read_text()
    assert down.strip() == "DROP TABLE IF EXISTS forge_notification_reads;"
