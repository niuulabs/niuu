"""Reconcile both deployed migration histories without rewriting applied SQL."""

import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from niuu.adapters.postgres_schema import apply_startup_migrations
from tests.test_adapters.test_startup_schema import connection
from volundr.schema_bridge import prepare_numbered_migrations

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"


def alias_stream(tmp_path):
    path = tmp_path / "000063_claims.up.sql"
    path.write_text("CREATE TABLE claims (id INT PRIMARY KEY)")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    historical = "000053_claims.up.sql"
    (tmp_path / "lineage-aliases.json").write_text(
        json.dumps({path.name: [{"filename": historical, "sha256": digest}]})
    )
    return path, historical, digest


async def test_alias_adopts_checksum_without_reexecuting_ddl_or_rewriting_old_row(tmp_path):
    path, historical, digest = alias_stream(tmp_path)
    conn = connection()
    conn.fetchval.side_effect = lambda sql, filename: digest if filename == historical else None
    await apply_startup_migrations(conn, [path])
    calls = conn.execute.await_args_list
    assert not any(c.args[0] == path.read_text() for c in calls)
    writes = [c for c in calls if "INSERT INTO" in c.args[0]]
    assert len(writes) == 1
    assert writes[0].args[1:] == (path.name, digest)
    assert not any("UPDATE" in c.args[0] or "DELETE" in c.args[0] for c in calls)
    conn.transaction.return_value.__aexit__.assert_awaited_once_with(None, None, None)


@pytest.mark.parametrize("drift", ["ledger", "source"])
async def test_alias_drift_refuses_adoption_and_releases_lock(tmp_path, drift):
    path, historical, digest = alias_stream(tmp_path)
    if drift == "source":
        path.write_text("DROP TABLE claims")
    conn = connection()
    conn.fetchval.side_effect = lambda sql, filename: (
        ("f" * 64 if drift == "ledger" else digest) if filename == historical else None
    )
    with pytest.raises(RuntimeError, match="lineage checksum mismatch"):
        await apply_startup_migrations(conn, [path])
    calls = conn.execute.await_args_list
    assert not any(c.args[0] == path.read_text() or "INSERT INTO" in c.args[0] for c in calls)
    assert "pg_advisory_unlock" in calls[-1].args[0]


async def test_absent_historical_row_executes_canonical_migration(tmp_path):
    path, _, digest = alias_stream(tmp_path)
    conn = connection()
    await apply_startup_migrations(conn, [path])
    assert any(c.args[0] == path.read_text() for c in conn.execute.await_args_list)
    assert any(c.args[1:] == (path.name, digest) for c in conn.execute.await_args_list)


@pytest.mark.parametrize("payload", [[], {"file": "bad"}, {"file": [{}]}])
async def test_invalid_lineage_manifest_fails_before_database_mutation(tmp_path, payload):
    path, _, _ = alias_stream(tmp_path)
    (tmp_path / "lineage-aliases.json").write_text(json.dumps(payload))
    conn = connection()
    with pytest.raises(ValueError, match="Invalid migration"):
        await apply_startup_migrations(conn, [path])
    conn.execute.assert_not_awaited()


@pytest.mark.parametrize("version", [49, 50, 51, 52, 53, 54, 55])
async def test_numbered_overlap_applies_prerequisites_without_resetting_cursor(version):
    conn = connection()
    conn.fetchval.return_value = True
    conn.fetchrow.return_value = {"version": version, "dirty": False}
    with patch("volundr.schema_bridge.apply_startup_migrations", new_callable=AsyncMock) as apply:
        assert await prepare_numbered_migrations(conn, MIGRATIONS) == 4
    paths = apply.await_args.args[1]
    assert {p.name for p in paths} == {
        "000049_valkyrie_history.up.sql",
        "000053_realms_trust_capabilities.up.sql",
        "000054_sessions_workload_config.up.sql",
        "000055_resident_runtimes.up.sql",
    }
    conn.execute.assert_not_awaited()


@pytest.mark.parametrize(
    "row", [None, {"version": 48, "dirty": False}, {"version": 67, "dirty": False}]
)
async def test_numbered_other_versions_need_no_preparation(row):
    conn = connection()
    conn.fetchval.return_value = row is not None
    conn.fetchrow.return_value = row
    assert await prepare_numbered_migrations(conn, MIGRATIONS) == 0
    conn.execute.assert_not_awaited()


async def test_dirty_numbered_history_is_never_force_reset():
    conn = connection()
    conn.fetchval.return_value = True
    conn.fetchrow.return_value = {"version": 55, "dirty": True}
    with pytest.raises(RuntimeError, match="dirty"):
        await prepare_numbered_migrations(conn, MIGRATIONS)
    conn.execute.assert_not_awaited()


async def test_missing_prerequisite_fails_before_applying_any(tmp_path):
    conn = connection()
    conn.fetchval.return_value = True
    conn.fetchrow.return_value = {"version": 55, "dirty": False}
    with pytest.raises(RuntimeError, match="missing"):
        await prepare_numbered_migrations(conn, tmp_path)
    conn.execute.assert_not_awaited()


def test_aliases_are_identical_sql_in_source_package_and_chart():
    import yaml

    root = MIGRATIONS.parent
    packaged = root / "src/cli/migrations/volundr"
    template = (root / "charts/volundr/templates/migrations-configmap.yaml").read_text()
    # Only inspect literal data; Helm metadata is rendered by the separate chart gate.
    literal = template[template.index("\ndata:\n") + 1 : template.rindex("{{- end")]
    data = yaml.safe_load(literal)["data"]
    for path in [*MIGRATIONS.glob("*.sql"), MIGRATIONS / "lineage-aliases.json"]:
        assert (packaged / path.name).read_bytes() == path.read_bytes(), path.name
        assert data[path.name] == path.read_text(), path.name
    manifest = json.loads((MIGRATIONS / "lineage-aliases.json").read_text())
    for canonical, aliases in manifest.items():
        digest = hashlib.sha256((MIGRATIONS / canonical).read_bytes()).hexdigest()
        assert all(entry["sha256"] == digest for entry in aliases)


@pytest.mark.parametrize("version", range(62, 67))
async def test_later_forge_cursor_repairs_upstream_authority_and_compute(version):
    conn = connection()
    conn.fetchval.return_value = True
    conn.fetchrow.return_value = {"version": version, "dirty": False}
    with patch("volundr.schema_bridge.apply_startup_migrations", new_callable=AsyncMock) as apply:
        assert await prepare_numbered_migrations(conn, MIGRATIONS) == 4 + version - 61
    names = {p.name for p in apply.await_args.args[1]}
    assert "000062_pat_authority.up.sql" in names
    assert ("000066_compute_leases.up.sql" in names) == (version == 66)
    assert all(int(name[:6]) <= version for name in names)
    conn.execute.assert_not_awaited()
