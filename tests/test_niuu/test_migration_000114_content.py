"""Migration 000114 removes the 'ravn' instance kind on every path that holds niuu_instances."""

from __future__ import annotations

from pathlib import Path

from niuu.domain.models import InstanceKind
from niuu.service_databases import GUILD_BOOTSTRAP_SQL

ROOT = Path(__file__).resolve().parents[2]
UP = (ROOT / "migrations" / "000114_drop_ravn_instance_kind.up.sql").read_text()
DOWN = (ROOT / "migrations" / "000114_drop_ravn_instance_kind.down.sql").read_text()

REMAINING_KINDS = ("volundr", "ting", "mimir", "bifrost", "observatory", "generic")


def test_the_enum_has_exactly_the_kinds_the_constraint_admits() -> None:
    assert {kind.value for kind in InstanceKind} == set(REMAINING_KINDS)


def test_up_deletes_ravn_rows_before_tightening_the_constraint() -> None:
    delete = UP.index("DELETE FROM niuu_instances")
    add = UP.index("ADD CONSTRAINT niuu_instances_kind_check")

    assert "WHERE kind = 'ravn'" in UP
    assert delete < add


def test_up_admits_only_the_remaining_kinds() -> None:
    allowed = ", ".join(f"'{kind}'" for kind in REMAINING_KINDS)

    assert f"CHECK (kind IN ({allowed}))" in UP
    assert "'ravn'" not in UP.split("ADD CONSTRAINT", maxsplit=1)[1]


def test_up_strips_the_ravn_base_url_config_keys() -> None:
    assert "config - 'ravn_base_url' - 'ravnBaseUrl'" in UP


def test_down_restores_the_constraint_000041_defined() -> None:
    assert (
        "CHECK (kind IN ('volundr', 'ting', 'mimir', 'bifrost', 'ravn', 'observatory', 'generic'))"
        in DOWN
    )


def test_guild_bootstrap_mirrors_the_migration() -> None:
    statements = [" ".join(statement.split()) for statement in GUILD_BOOTSTRAP_SQL]
    allowed = ", ".join(f"'{kind}'" for kind in REMAINING_KINDS)

    delete = statements.index("DELETE FROM niuu_instances WHERE kind = 'ravn';")
    constraint = next(
        index
        for index, statement in enumerate(statements)
        if statement.endswith(f"CHECK (kind IN ({allowed}));")
    )
    assert delete < constraint
    assert not any("'ravn', 'observatory'" in statement for statement in statements)
