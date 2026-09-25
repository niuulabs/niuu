"""The Guild chart's migration subset must keep up with the tables Guild owns.

A Guild deployed with its own database (for example ``DATABASE__NAME=guild``)
only runs the migrations embedded in ``charts/guild``. A migration that alters a
Guild-owned table but is missing from that chart leaves the Guild schema behind
the code, which then fails at startup.
"""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "migrations"
GUILD_CONFIGMAP = ROOT / "charts/guild/templates/migrations-configmap.yaml"

# Tables the Guild service reads and writes in its own database.
GUILD_TABLES = ("niuu_instances",)


def _guild_migrations() -> dict[str, str]:
    template = GUILD_CONFIGMAP.read_text()
    literal = template[template.index("\ndata:\n") + 1 : template.rindex("{{- end")]
    return yaml.safe_load(literal)["data"]


def _migrations_touching_guild_tables() -> list[Path]:
    touching = []
    for up in sorted(MIGRATIONS.glob("*.up.sql")):
        if any(table in up.read_text() for table in GUILD_TABLES):
            touching.append(up)
            touching.append(up.with_name(up.name.replace(".up.sql", ".down.sql")))
    return touching


@pytest.mark.parametrize("migration", _migrations_touching_guild_tables(), ids=lambda p: p.name)
def test_guild_chart_embeds_every_migration_for_its_tables(migration: Path) -> None:
    data = _guild_migrations()
    assert migration.name in data, (
        f"{migration.name} alters a Guild-owned table but is missing from "
        "charts/guild/templates/migrations-configmap.yaml"
    )
    assert data[migration.name].rstrip("\n") == migration.read_text().rstrip("\n")
