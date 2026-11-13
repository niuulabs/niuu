from __future__ import annotations

from pydantic import BaseModel

from niuu.service_databases import (
    apply_service_database_settings,
    bootstrap_sql_for_service,
    local_service_database_names,
)


class _DummyDatabase(BaseModel):
    name: str = "postgres"


class _DummySettings(BaseModel):
    database: _DummyDatabase


def test_apply_service_database_settings_uses_service_override(monkeypatch) -> None:
    monkeypatch.setenv("NIUU_DATABASE_NAME_GUILD", "guild_local")

    settings = _DummySettings(database=_DummyDatabase(name="postgres"))
    updated = apply_service_database_settings(settings, "guild")

    assert updated.database.name == "guild_local"
    assert settings.database.name == "postgres"


def test_local_service_database_names_are_distinct(monkeypatch) -> None:
    monkeypatch.setenv("NIUU_DATABASE_NAME_NIUU_SHARED", "shared_local")
    monkeypatch.setenv("NIUU_DATABASE_NAME_GUILD", "shared_local")
    monkeypatch.setenv("NIUU_DATABASE_NAME_OBSERVATORY", "shared_local")

    names = local_service_database_names()

    assert names == ("volundr", "shared_local", "ting", "bifrost", "mimir")


def test_bootstrap_sql_is_defined_for_split_services() -> None:
    guild_sql = bootstrap_sql_for_service("guild")

    assert guild_sql
    assert bootstrap_sql_for_service("observatory")
    assert bootstrap_sql_for_service("volundr") == ()
    assert all(
        "CREATE EXTENSION IF NOT EXISTS pgcrypto" not in statement for statement in guild_sql
    )
    shared_sql = "\n".join(bootstrap_sql_for_service("niuu-shared"))
    for table in ("realms", "trust_grants", "capabilities"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in shared_sql


def test_the_shared_database_carries_team_integrations() -> None:
    """Migration 000121 in mini mode's shared database too: the integrations service there
    writes project_id and connected_by, and refused to start without them."""
    shared_sql = "\n".join(bootstrap_sql_for_service("niuu-shared"))
    assert "ADD COLUMN IF NOT EXISTS project_id UUID" in shared_sql
    assert "ADD COLUMN IF NOT EXISTS connected_by" in shared_sql
    assert "CREATE TABLE IF NOT EXISTS integration_usage" in shared_sql
