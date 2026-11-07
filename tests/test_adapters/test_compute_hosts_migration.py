"""Migration 000108 creates the registered-host tables; its text is checked here statically.

Running it against PostgreSQL belongs to the integration suite.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UP = (ROOT / "migrations/000108_compute_hosts.up.sql").read_text()
DOWN = (ROOT / "migrations/000108_compute_hosts.down.sql").read_text()


def table(name: str, sql: str = UP) -> str:
    return re.search(rf"CREATE TABLE IF NOT EXISTS {name} \((.*?)\n\);", sql, re.S).group(1)


def test_the_migration_follows_a_unique_number_and_has_both_directions():
    numbers = sorted(
        int(path.name.split("_", 1)[0]) for path in (ROOT / "migrations").glob("*.up.sql")
    )

    assert 108 in numbers
    assert len(numbers) == len(set(numbers))
    assert (ROOT / "migrations/000108_compute_hosts.down.sql").exists()
    assert (ROOT / "migrations/000108_compute_hosts.up.sql").exists()


def test_up_is_idempotent_everywhere():
    assert "CREATE TABLE IF NOT EXISTS compute_hosts (" in UP
    assert "CREATE TABLE IF NOT EXISTS compute_host_pairing_codes (" in UP
    assert not re.search(r"^\s*CREATE (UNIQUE )?(INDEX|TABLE) (?!IF NOT EXISTS)", UP, re.M)
    assert not re.search(r"\bALTER TABLE\b|\bDROP\b", UP)


def test_a_host_name_is_unique_within_its_pool_and_its_key_across_all():
    assert (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_compute_hosts_name ON compute_hosts (pool_id, name)"
        in UP
    )
    assert (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_compute_hosts_public_key "
        "ON compute_hosts (host_public_key)"
    ) in UP


def test_a_machine_allocation_belongs_to_at_most_one_host():
    assert re.search(
        r"CREATE UNIQUE INDEX IF NOT EXISTS idx_compute_hosts_allocation\s+"
        r"ON compute_hosts \(allocation_id\) WHERE allocation_id IS NOT NULL;",
        UP,
    )


def test_hosts_are_listed_by_pool():
    assert "CREATE INDEX IF NOT EXISTS idx_compute_hosts_pool ON compute_hosts (pool_id)" in UP
    assert re.search(
        r"CREATE INDEX IF NOT EXISTS idx_compute_host_pairing_codes_pool\s+"
        r"ON compute_host_pairing_codes \(pool_id\);",
        UP,
    )


def test_a_pairing_code_is_stored_only_as_a_unique_hash():
    codes = table("compute_host_pairing_codes")

    assert "code_hash TEXT NOT NULL UNIQUE" in codes
    assert not re.search(r"\bcode TEXT\b", codes)
    assert "consumed_at TIMESTAMPTZ" in codes
    assert "revoked_at TIMESTAMPTZ" in codes
    assert "expires_at TIMESTAMPTZ NOT NULL" in codes


def test_host_health_and_service_are_closed_sets():
    hosts = table("compute_hosts")

    assert "CHECK (health IN ('pending', 'healthy', 'quarantined', 'unreachable'))" in hosts
    assert "CHECK (service IN ('active', 'draining', 'out', 'removing'))" in hosts


def test_up_keeps_json_state_and_columns_in_step():
    hosts = table("compute_hosts")
    for check in (
        "CHECK (data->>'id' = id::text)",
        "CHECK (data->>'pool_id' = pool_id)",
        "CHECK (data->>'name' = name)",
        "CHECK (data->>'host_public_key' = host_public_key)",
        "CHECK (data->>'health' = health)",
        "CHECK (data->>'service' = service)",
        "CHECK (data->>'allocation_id' IS NOT DISTINCT FROM allocation_id::text)",
    ):
        assert check in hosts
    codes = table("compute_host_pairing_codes")
    for check in (
        "CHECK (data->>'id' = id::text)",
        "CHECK (data->>'code_hash' = code_hash)",
        "CHECK (data->>'pool_id' = pool_id)",
    ):
        assert check in codes


def test_both_tables_keep_their_record_as_jsonb_with_a_creation_time():
    for name in ("compute_hosts", "compute_host_pairing_codes"):
        body = table(name)
        assert "data JSONB NOT NULL" in body
        assert "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()" in body
        assert "id UUID PRIMARY KEY" in body


def test_down_drops_both_tables_and_only_those():
    drops = re.findall(r"^DROP TABLE IF EXISTS (\w+);", DOWN, re.M)

    assert sorted(drops) == ["compute_host_pairing_codes", "compute_hosts"]
    assert len(re.findall(r"\bDROP\b", DOWN)) == 2
    assert not re.search(r"\bALTER\b|\bDELETE\b|\bTRUNCATE\b", DOWN)


def test_down_drops_the_pairing_codes_before_the_hosts_and_is_idempotent():
    assert DOWN.index("compute_host_pairing_codes") < DOWN.index(
        "DROP TABLE IF EXISTS compute_hosts"
    )
    assert not re.search(r"DROP TABLE (?!IF EXISTS)", DOWN)
