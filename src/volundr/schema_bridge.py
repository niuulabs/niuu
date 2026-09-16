"""Prepare the diverged Forge schema lineage before the numbered migrate runner."""

import argparse
import asyncio
import logging
from pathlib import Path

import asyncpg
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from niuu.adapters.postgres_schema import apply_startup_migrations

logger = logging.getLogger(__name__)

# Versions 49–55 and 62–66 diverged between Forge and upstream. Repair
# prerequisites skipped by the numeric cursor without rewriting either history.
_DIVERGED_FIRST = 49
_DIVERGED_LAST = 66
_PREREQUISITES = (
    "000049_valkyrie_history.up.sql",
    "000053_realms_trust_capabilities.up.sql",
    "000054_sessions_workload_config.up.sql",
    "000055_resident_runtimes.up.sql",
)

_LATER_PREREQUISITES = (
    "000062_pat_authority.up.sql",
    "000063_topology_source_authority.up.sql",
    "000064_instance_tenant_attribution.up.sql",
    "000065_admin_settings.up.sql",
    "000066_compute_leases.up.sql",
)


class MigrationDatabaseSettings(BaseSettings):
    """The DATABASE_* configuration shared with the chart's migrate container."""

    model_config = SettingsConfigDict(env_prefix="DATABASE_")
    host: str
    port: int = Field(default=5432, ge=1, le=65535)
    name: str
    user: str
    password: str = ""
    connect_timeout_seconds: float = Field(default=30.0, gt=0)


async def prepare_numbered_migrations(conn: asyncpg.Connection, migrations_dir: Path) -> int:
    """Ensure prerequisites without resetting or advancing migrate's version row.

    Fresh installations and versions outside the historical overlap need no
    preparation. Both known lineages may safely apply these idempotent prerequisites.
    A dirty numeric history is an interrupted migration and must be repaired
    explicitly; it is never force-reset by this bridge.
    """
    if not await conn.fetchval("SELECT to_regclass('schema_migrations') IS NOT NULL"):
        return 0
    row = await conn.fetchrow("SELECT version, dirty FROM schema_migrations")
    if row is None:
        return 0
    if row["dirty"]:
        raise RuntimeError("Numbered migration history is dirty; repair it before upgrading Forge")
    if not _DIVERGED_FIRST <= row["version"] <= _DIVERGED_LAST:
        return 0
    names = [
        *_PREREQUISITES,
        *(name for name in _LATER_PREREQUISITES if int(name[:6]) <= row["version"]),
    ]
    files = [migrations_dir / name for name in names]
    for path in files:
        if not path.is_file():
            raise RuntimeError(f"Required Forge lineage migration is missing: {path.name}")
    await apply_startup_migrations(conn, files)
    return len(files)


async def _run(migrations_dir: Path) -> None:
    config = MigrationDatabaseSettings()
    conn = await asyncpg.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=config.name,
        timeout=config.connect_timeout_seconds,
    )
    try:
        count = await prepare_numbered_migrations(conn, migrations_dir)
        logger.info("Verified %d Forge lineage prerequisites", count)
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--migrations-dir", type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run(args.migrations_dir))


if __name__ == "__main__":
    main()
