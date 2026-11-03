"""Notification migrations are idempotent SQL."""

from __future__ import annotations

import hashlib
import json
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


def test_odin_review_relay_migration_is_idempotent_and_reversible():
    up = (ROOT / "migrations/000089_odin_review_relay.up.sql").read_text()
    assert re.findall(r"CREATE (?:TABLE|INDEX)\b(?! IF NOT EXISTS)", up) == []
    assert "CREATE TABLE IF NOT EXISTS odin_review_relay_owners (" in up
    assert "CREATE TABLE IF NOT EXISTS odin_review_relay_items (" in up
    assert "PRIMARY KEY (owner_id, review_id)" in up
    assert "WHERE retired_at IS NULL" in up
    down = (ROOT / "migrations/000089_odin_review_relay.down.sql").read_text()
    assert down.count("IF EXISTS") == 3
    assert "DROP TABLE" in down and "CREATE" not in down


IMAGES = "000093_forge_notification_images"
# Thor production applied this SQL as 000072 before upstream renumbered the notification
# migrations; migrations/lineage-aliases.json adopts that ledger row.
IMAGES_APPLIED_AS = "000072_forge_notification_images.up.sql"
IMAGES_APPLIED_SHA256 = "cd39abfca6355c64df73f10a1b5ae9c0933cc8cfebf43a6962993a2b2e5565e4"


def test_images_migration_adopts_the_applied_filename():
    aliases = json.loads((ROOT / "migrations" / "lineage-aliases.json").read_text())
    up = (ROOT / "migrations" / f"{IMAGES}.up.sql").read_bytes()
    assert aliases[f"{IMAGES}.up.sql"] == [
        {"filename": IMAGES_APPLIED_AS, "sha256": IMAGES_APPLIED_SHA256},
        {"filename": "000090_forge_notification_images.up.sql", "sha256": IMAGES_APPLIED_SHA256},
    ]
    assert hashlib.sha256(up).hexdigest() == IMAGES_APPLIED_SHA256


def test_images_migration_is_additive_idempotent_and_goes_with_its_notification():
    up = (ROOT / "migrations" / f"{IMAGES}.up.sql").read_text()
    assert re.findall(r"CREATE (?:TABLE|INDEX)\b(?! IF NOT EXISTS)", up) == []
    assert "CREATE TABLE IF NOT EXISTS forge_notification_images (" in up
    assert (
        "notification_id UUID PRIMARY KEY REFERENCES forge_notifications(id) ON DELETE CASCADE"
        in up
    )
    assert "CHECK (state IN ('pending', 'ready', 'failed'))" in up
    assert "CHECK (source_kind IN ('file_id', 'url'))" in up
    assert "WHERE state = 'pending'" in up
    assert not re.search(r"\bALTER\s+TABLE\s+forge_notifications\b", up)
    down = (ROOT / "migrations" / f"{IMAGES}.down.sql").read_text()
    assert down.strip() == "DROP TABLE IF EXISTS forge_notification_images;"


def test_images_migration_is_followed_only_by_the_speak_and_resolved_migrations():
    names = sorted(path.name for path in (ROOT / "migrations").glob("*.up.sql"))
    notifications = [name for name in names if "_forge_notification" in name]
    assert notifications[-3:] == [
        f"{IMAGES}.up.sql",
        "000097_forge_notification_speak.up.sql",
        "000098_forge_notification_resolved.up.sql",
    ]
