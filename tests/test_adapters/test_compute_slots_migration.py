"""Migration 000107 splits sessions out of machine rows; its text is checked here statically.

Running it against legacy rows of every state needs PostgreSQL and lives in
``tests/integration/volundr/test_compute_slots_migration.py``.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UP = (ROOT / "migrations/000107_compute_slots.up.sql").read_text()
DOWN = (ROOT / "migrations/000107_compute_slots.down.sql").read_text()


def test_the_migration_follows_the_last_one_and_has_both_directions():
    numbers = sorted(
        int(path.name.split("_", 1)[0]) for path in (ROOT / "migrations").glob("*.up.sql")
    )

    assert 107 in numbers
    assert len(numbers) == len(set(numbers))
    assert (ROOT / "migrations/000107_compute_slots.down.sql").exists()


def test_up_is_idempotent_and_slots_follow_their_machine():
    assert "CREATE TABLE IF NOT EXISTS compute_slots" in UP
    assert "CREATE UNIQUE INDEX IF NOT EXISTS idx_compute_slots_occupied" in UP
    assert "CREATE UNIQUE INDEX IF NOT EXISTS idx_compute_slots_live_session" in UP
    assert "CREATE INDEX IF NOT EXISTS idx_compute_slots_allocation" in UP
    assert "allocation_id UUID NOT NULL REFERENCES compute_leases (id) ON DELETE CASCADE" in UP
    # A slot is occupied in every state but released; a session holds one live slot.
    assert "WHERE state != 'released'" in UP
    assert "WHERE state IN ('starting', 'busy', 'failed', 'draining')" in UP
    # A second run adds nothing: allocations that already have a slot are skipped.
    assert "NOT EXISTS (SELECT 1 FROM compute_slots s WHERE s.allocation_id = l.id)" in UP
    assert not re.search(r"^\s*CREATE (UNIQUE )?(INDEX|TABLE) (?!IF NOT EXISTS)", UP, re.M)


def test_up_keeps_json_state_and_columns_in_step():
    for check in (
        "CHECK (data->>'id' = id::text)",
        "CHECK (data->>'allocation_id' = allocation_id::text)",
        "CHECK ((data->>'slot')::integer = slot)",
        "CHECK (data->>'pool_id' = pool_id)",
        "CHECK (data->>'session_id' = session_id::text)",
        "CHECK (data->>'state' = state)",
    ):
        assert check in UP


def test_up_strips_exactly_the_session_fields_from_machines_and_adds_slots():
    stripped = re.search(r"data - ARRAY\[(.*?)\]", UP, re.S).group(1)

    assert sorted(re.findall(r"'(\w+)'", stripped)) == sorted(
        [
            "session_id",
            "tenant_id",
            "owner_id",
            "session_bootstrap_ref",
            "runtime_data_started",
            "runtime_started",
            "stop_requested",
        ]
    )
    assert "jsonb_build_object('slots', COALESCE(data->'slots', '1'::jsonb))" in UP


def test_up_maps_each_legacy_state_to_a_slot_state_and_idles_busy_machines():
    assert "WHEN 'busy' THEN 'busy' WHEN 'draining' THEN 'draining'" in UP
    assert "WHEN 'failed' THEN 'failed' ELSE 'starting'" in UP
    assert "UPDATE compute_leases SET state = 'idle'" in UP and "WHERE state = 'busy'" in UP


def test_a_failed_runtime_start_fails_the_slot_and_returns_the_machine_to_ready():
    prefix = re.search(r"LIKE '([^%']+)%'", UP).group(1)
    source = (ROOT / "src/volundr/adapters/outbound/vm_pod_manager.py").read_text()

    # Every message the pod manager records for a failed start begins with the migrated prefix.
    messages = re.findall(r'"(Runtime startup failed[^"]*)"', source) + re.findall(
        r'f"(Runtime startup failed[^"]*)"', source
    )
    assert messages
    assert all(message.startswith(prefix) for message in messages)
    assert "SET state = 'ready'" in UP
    assert "|| '{\"error\": null}'::jsonb" in UP


def test_down_refuses_while_any_machine_is_not_released():
    assert "Drain and release all compute allocations before downgrading" in DOWN
    assert "IF EXISTS (SELECT 1 FROM compute_leases WHERE state != 'released')" in DOWN
    assert DOWN.index("RAISE EXCEPTION") < DOWN.index("DROP TABLE")


def test_down_restores_machine_rows_and_strips_the_new_pool_policy_keys():
    assert "data - 'slots'" in DOWN
    assert "jsonb_build_object('session_id', session_id, 'tenant_id', '', 'owner_id', '')" in DOWN
    keys = re.search(r"data - ARRAY\[(.*?)\]", DOWN, re.S).group(1)
    assert re.findall(r"'(\w+)'", keys) == [
        "slots_per_machine",
        "max_sessions_per_machine",
        "max_machine_age_seconds",
    ]
    assert "UPDATE compute_pools" in DOWN
    assert DOWN.rstrip().endswith("DROP TABLE IF EXISTS compute_slots;")


def test_the_session_columns_older_controllers_read_survive_for_released_machines_only():
    assert "session_id = CASE WHEN state = 'released' THEN session_id ELSE NULL END" in UP
