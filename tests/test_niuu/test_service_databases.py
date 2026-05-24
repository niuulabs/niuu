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

    assert names == ("volundr", "shared_local", "ting", "bifrost", "ravn", "mimir")


def test_bootstrap_sql_is_defined_for_split_services() -> None:
    guild_sql = bootstrap_sql_for_service("guild")

    assert guild_sql
    assert bootstrap_sql_for_service("observatory")
    assert bootstrap_sql_for_service("volundr") == ()
    assert all("CREATE EXTENSION IF NOT EXISTS pgcrypto" not in statement for statement in guild_sql)
