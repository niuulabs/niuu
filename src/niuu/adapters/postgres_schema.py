"""Transactional, serialized schema bootstrap with an auditable checksum ledger."""

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import asyncpg

logger = logging.getLogger(__name__)
SCHEMA_LOCK = "volundr.startup-schema"
LEDGER_SQL = """CREATE TABLE IF NOT EXISTS volundr_schema_history (
    filename TEXT PRIMARY KEY,
    sha256 CHAR(64) NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)"""


@dataclass
class _LegacyPlan:
    sql: str
    columns: dict[str, set[str]] = field(default_factory=dict)
    indexes: list[tuple[str, str, list[str], bool]] = field(default_factory=list)


_PRESET_COLUMNS = {
    "id",
    "name",
    "description",
    "is_default",
    "cli_tool",
    "workload_type",
    "model",
    "system_prompt",
    "resource_config",
    "mcp_servers",
    "terminal_sidecar",
    "skills",
    "rules",
    "env_vars",
    "env_secret_refs",
    "workload_config",
    "created_at",
    "updated_at",
}
_PRESET_ADDITIONS = {"source", "integration_ids", "setup_scripts"}
_RENAME_DEPENDENT_FILES = {
    "000011_volundr_presets.up.sql",
    "000014_integration_connections.up.sql",
    "000017_preset_source_integrations_scripts.up.sql",
    "000024_rename_user_id_to_owner_id.up.sql",
    "000044_launch_specs.up.sql",
}


async def _columns(conn: asyncpg.Connection, table: str) -> set[str]:
    rows = await conn.fetch(
        """SELECT a.attname FROM pg_attribute a
           JOIN pg_class c ON c.oid = a.attrelid
           JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = current_schema() AND c.relname = $1
             AND a.attnum > 0 AND NOT a.attisdropped""",
        table,
    )
    return {row["attname"] for row in rows}


async def _check_preset_conflicts(conn: asyncpg.Connection) -> None:
    # Previous best-effort startup re-created the obsolete table and column.
    # Empty leftovers are harmless; never choose between two sets of user data.
    if await _columns(conn, "volundr_presets"):
        if await conn.fetchval("SELECT EXISTS (SELECT 1 FROM volundr_presets LIMIT 1)"):
            raise RuntimeError(
                "Both legacy volundr_presets and canonical volundr_launch_specs exist with "
                "legacy user rows; reconcile them explicitly before schema adoption"
            )
    columns = await _columns(conn, "sessions")
    if {"preset_id", "launch_spec_id"} <= columns:
        if await conn.fetchval(
            """SELECT EXISTS (SELECT 1 FROM sessions WHERE preset_id IS NOT NULL
               AND preset_id IS DISTINCT FROM launch_spec_id LIMIT 1)"""
        ):
            raise RuntimeError("Conflicting preset_id and launch_spec_id values; adoption refused")


def _preset_indexes(table: str) -> list[tuple[str, str, list[str], bool]]:
    return [
        (f"idx_{table}_name", table, ["name"], True),
        (f"idx_{table}_cli_tool", table, ["cli_tool"], False),
        (f"idx_{table}_is_default", table, ["is_default"], False),
    ]


async def _legacy_plan(conn: asyncpg.Connection, filename: str, sql: str) -> _LegacyPlan:
    """Execute old additive DDL against verified canonical names after historical renames.

    Original SQL/checksums remain immutable. Only these five known rename-dependent files
    need adaptation: all other unapplied migrations execute normally. Each plan checks its
    actual schema postconditions in the same transaction before recording the source checksum.
    """
    plan = _LegacyPlan(sql)
    if filename not in _RENAME_DEPENDENT_FILES:
        return plan
    if filename in {
        "000014_integration_connections.up.sql",
        "000024_rename_user_id_to_owner_id.up.sql",
    }:
        columns = await _columns(conn, "integration_connections")
        renamed = "owner_id" in columns
        if {"user_id", "owner_id"} <= columns and await conn.fetchval(
            """SELECT EXISTS (SELECT 1 FROM integration_connections WHERE user_id IS NOT NULL
               AND user_id IS DISTINCT FROM owner_id LIMIT 1)"""
        ):
            raise RuntimeError(
                "Conflicting integration user_id and owner_id values; adoption refused"
            )
        if filename.startswith("000014"):
            if renamed:
                plan.sql = sql.replace("user_id", "owner_id").replace(
                    "idx_integration_connections_user", "idx_integration_connections_owner"
                )
            owner = "owner_id" if renamed else "user_id"
            index = "owner" if renamed else "user"
            plan.columns = {
                "integration_connections": {
                    "id",
                    owner,
                    "integration_type",
                    "adapter",
                    "credential_name",
                    "config",
                    "enabled",
                    "created_at",
                    "updated_at",
                }
            }
        else:
            if renamed:
                plan.sql = sql.replace(
                    "ALTER TABLE integration_connections RENAME COLUMN user_id TO owner_id;", ""
                )
            owner, index = "owner_id", "owner"
            plan.columns = {"integration_connections": {owner, "integration_type"}}
        plan.indexes = [
            (
                f"idx_integration_connections_{index}",
                "integration_connections",
                [owner, "integration_type"],
                False,
            )
        ]
        return plan

    renamed = bool(await _columns(conn, "volundr_launch_specs"))
    table = "volundr_launch_specs" if renamed else "volundr_presets"
    if renamed:
        await _check_preset_conflicts(conn)
    if filename.startswith("000011"):
        plan.sql = sql.replace("volundr_presets", table)
        session_column = "preset_id"
        if "launch_spec_id" in await _columns(conn, "sessions"):
            plan.sql = plan.sql.replace("preset_id", "launch_spec_id")
            session_column = "launch_spec_id"
        plan.columns = {table: _PRESET_COLUMNS, "sessions": {session_column}}
        plan.indexes = _preset_indexes(table)
    elif filename.startswith("000017"):
        plan.sql = sql.replace("volundr_presets", table)
        plan.columns = {table: _PRESET_ADDITIONS}
    else:
        if renamed:
            plan.sql = sql.replace(
                "ALTER TABLE volundr_presets RENAME TO volundr_launch_specs;", ""
            )
            for suffix in ("name", "cli_tool", "is_default"):
                plan.sql = plan.sql.replace(
                    f"ALTER INDEX IF EXISTS idx_volundr_presets_{suffix} RENAME TO "
                    f"idx_volundr_launch_specs_{suffix};",
                    "",
                )
        if "launch_spec_id" in await _columns(conn, "sessions"):
            plan.sql = plan.sql.replace(
                "ALTER TABLE sessions RENAME COLUMN preset_id TO launch_spec_id;", ""
            )
        plan.columns = {
            "volundr_launch_specs": _PRESET_COLUMNS
            | _PRESET_ADDITIONS
            | {"session_definition", "repos", "workspace_layout"},
            "sessions": {"launch_spec_id"},
        }
        plan.indexes = _preset_indexes("volundr_launch_specs")
    return plan


async def _verify_legacy_plan(conn: asyncpg.Connection, plan: _LegacyPlan) -> None:
    for table, required in plan.columns.items():
        missing = required - await _columns(conn, table)
        if missing:
            raise RuntimeError(
                f"Schema adoption missing {table} columns: {', '.join(sorted(missing))}"
            )
    for index, table, columns, unique in plan.indexes:
        row = await conn.fetchrow(
            """SELECT t.relname AS table_name, x.indisunique,
                 ARRAY(SELECT a.attname FROM unnest(x.indkey) WITH ORDINALITY k(attnum, ord)
                       JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
                       ORDER BY k.ord) AS columns
               FROM pg_index x JOIN pg_class i ON i.oid = x.indexrelid
               JOIN pg_class t ON t.oid = x.indrelid
               JOIN pg_namespace n ON n.oid = i.relnamespace
               WHERE n.nspname = current_schema() AND i.relname = $1""",
            index,
        )
        if (
            row is None
            or row["table_name"] != table
            or list(row["columns"]) != columns
            or (unique and not row["indisunique"])
        ):
            raise RuntimeError(f"Schema adoption missing or incompatible index {index}")


def _lineage_aliases(sql_files: list[Path]) -> dict[str, list[dict[str, str]]]:
    """Load immutable filename aliases from the migration stream being applied."""
    aliases: dict[str, list[dict[str, str]]] = {}
    for directory in {path.parent for path in sql_files}:
        manifest = directory / "lineage-aliases.json"
        if not manifest.exists():
            continue
        payload = json.loads(manifest.read_text())
        if not isinstance(payload, dict):
            raise ValueError(f"Invalid migration lineage manifest: {manifest}")
        for canonical, entries in payload.items():
            if canonical in aliases or not isinstance(entries, list):
                raise ValueError(f"Invalid migration aliases for {canonical}")
            for entry in entries:
                if (
                    not isinstance(entry, dict)
                    or not isinstance(entry.get("filename"), str)
                    or not isinstance(entry.get("sha256"), str)
                    or len(entry["sha256"]) != 64
                ):
                    raise ValueError(f"Invalid migration alias for {canonical}")
            aliases[canonical] = entries
    return aliases


async def _already_applied_alias(
    conn: asyncpg.Connection, checksum: str, aliases: list[dict[str, str]]
) -> bool:
    """Recognize identical historical SQL without modifying its original ledger row."""
    adopted = False
    for alias in aliases:
        existing = await conn.fetchval(
            "SELECT sha256 FROM volundr_schema_history WHERE filename = $1", alias["filename"]
        )
        if existing is None:
            continue
        if existing != alias["sha256"] or checksum != alias["sha256"]:
            raise RuntimeError(
                f"Migration lineage checksum mismatch for {alias['filename']}; "
                "preserve the applied SQL and add a forward migration"
            )
        adopted = True
    return adopted


async def apply_startup_migrations(conn: asyncpg.Connection, sql_files: list[Path]) -> None:
    """Apply each file exactly once, recording its checksum in the same transaction.

    An installation without a ledger executes every migration once, adapting the five
    historical rename dependencies only after checking canonical schema postconditions.
    Ambiguous legacy user data blocks adoption without deleting or choosing a copy.
    The session advisory lock also serializes independent platform processes.
    """
    aliases = _lineage_aliases(sql_files)
    await conn.execute("SELECT pg_advisory_lock(hashtext($1))", SCHEMA_LOCK)
    try:
        await conn.execute(LEDGER_SQL)
        for sql_file in sql_files:
            sql = sql_file.read_text()
            checksum = hashlib.sha256(sql.encode()).hexdigest()
            try:
                async with conn.transaction():
                    existing = await conn.fetchval(
                        "SELECT sha256 FROM volundr_schema_history WHERE filename = $1",
                        sql_file.name,
                    )
                    if existing is not None:
                        if existing != checksum:
                            raise RuntimeError(
                                f"Applied migration {sql_file.name} changed; add a new migration"
                            )
                        continue
                    adopted = await _already_applied_alias(
                        conn, checksum, aliases.get(sql_file.name, [])
                    )
                    if not adopted:
                        plan = await _legacy_plan(conn, sql_file.name, sql)
                        await conn.execute(plan.sql)
                        await _verify_legacy_plan(conn, plan)
                    await conn.execute(
                        """INSERT INTO volundr_schema_history (filename, sha256)
                           VALUES ($1, $2)""",
                        sql_file.name,
                        checksum,
                    )
                logger.info("Applied startup migration %s (%s)", sql_file.name, checksum)
            except Exception:
                logger.exception("Startup migration %s failed; schema is not ready", sql_file.name)
                raise
    finally:
        await conn.execute("SELECT pg_advisory_unlock(hashtext($1))", SCHEMA_LOCK)
