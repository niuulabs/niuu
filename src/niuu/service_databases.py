"""Service-owned database helpers for local and shared runtime wiring."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from contextlib import asynccontextmanager
from typing import TypeVar

import asyncpg
from pydantic import BaseModel

from volundr.config import DatabaseConfig

SettingsT = TypeVar("SettingsT", bound=BaseModel)

_DB_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

LOCAL_SERVICE_DATABASES: dict[str, str] = {
    "volundr": "volundr",
    "niuu-shared": "niuu_shared",
    "guild": "guild",
    "observatory": "observatory",
    "ting": "ting",
    "bifrost": "bifrost",
    "ravn": "ravn",
    "mimir": "mimir",
}

NIUU_SHARED_BOOTSTRAP_SQL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS tenants (
        id              TEXT PRIMARY KEY,
        path            TEXT NOT NULL UNIQUE,
        name            TEXT NOT NULL,
        parent_id       TEXT REFERENCES tenants(id),
        tier            TEXT NOT NULL DEFAULT 'developer',
        max_sessions    INT NOT NULL DEFAULT 5,
        max_storage_gb  INT NOT NULL DEFAULT 50,
        created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_tenants_path ON tenants(path);",
    "CREATE INDEX IF NOT EXISTS idx_tenants_parent_id ON tenants(parent_id);",
    """
    CREATE TABLE IF NOT EXISTS users (
        id              TEXT PRIMARY KEY,
        email           TEXT NOT NULL,
        display_name    TEXT NOT NULL DEFAULT '',
        status          TEXT NOT NULL DEFAULT 'active',
        home_pvc        TEXT,
        created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
        updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
    );
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email);",
    "CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);",
    """
    CREATE TABLE IF NOT EXISTS tenant_memberships (
        user_id         TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        role            TEXT NOT NULL DEFAULT 'volundr:developer',
        granted_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
        PRIMARY KEY (user_id, tenant_id)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_tenant_memberships_tenant_id ON tenant_memberships(tenant_id);",
    "CREATE INDEX IF NOT EXISTS idx_tenant_memberships_user_id ON tenant_memberships(user_id);",
    """
    CREATE TABLE IF NOT EXISTS feature_toggles (
        feature_key     TEXT PRIMARY KEY,
        enabled         BOOLEAN NOT NULL DEFAULT true,
        updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS user_feature_preferences (
        user_id         TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        feature_key     TEXT NOT NULL,
        visible         BOOLEAN NOT NULL DEFAULT true,
        sort_order      INT NOT NULL DEFAULT 0,
        PRIMARY KEY (user_id, feature_key)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_user_feature_prefs_user ON user_feature_preferences(user_id);",
    """
    CREATE TABLE IF NOT EXISTS personal_access_tokens (
        id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_id     TEXT        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name         TEXT        NOT NULL,
        token_hash   TEXT        NOT NULL,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        expires_at   TIMESTAMPTZ,
        last_used_at TIMESTAMPTZ
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_pats_owner_id ON personal_access_tokens(owner_id);",
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_pats_owner_name
        ON personal_access_tokens(owner_id, name);
    """,
    """
    CREATE TABLE IF NOT EXISTS integration_connections (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_id VARCHAR(255) NOT NULL,
        integration_type VARCHAR(50) NOT NULL,
        adapter VARCHAR(500) NOT NULL,
        credential_name VARCHAR(253) NOT NULL,
        slug VARCHAR(100) NOT NULL DEFAULT '',
        config JSONB NOT NULL DEFAULT '{}',
        enabled BOOLEAN NOT NULL DEFAULT true,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_integration_connections_owner
        ON integration_connections(owner_id, integration_type);
    """,
    """
    CREATE TABLE IF NOT EXISTS project_mappings (
        id              UUID PRIMARY KEY,
        repo_url        VARCHAR(500) NOT NULL,
        project_id      VARCHAR(255) NOT NULL,
        project_name    VARCHAR(255) NOT NULL DEFAULT '',
        created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
    );
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_project_mappings_repo_url
        ON project_mappings(repo_url);
    """,
    "CREATE INDEX IF NOT EXISTS idx_project_mappings_project_id ON project_mappings(project_id);",
    """
    CREATE TABLE IF NOT EXISTS credential_metadata (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        name VARCHAR(253) NOT NULL,
        secret_type VARCHAR(50) NOT NULL,
        keys TEXT[] NOT NULL DEFAULT '{}',
        metadata JSONB NOT NULL DEFAULT '{}',
        owner_id VARCHAR(255) NOT NULL,
        owner_type VARCHAR(50) NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE(owner_type, owner_id, name)
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_credential_metadata_owner
        ON credential_metadata(owner_type, owner_id);
    """,
    """
    CREATE TABLE IF NOT EXISTS ravn_personas (
        owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name VARCHAR(128) NOT NULL,
        config_json JSONB NOT NULL DEFAULT '{}'::jsonb,
        runtime_config_json JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
        PRIMARY KEY (owner_id, name)
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_ravn_personas_owner_updated
        ON ravn_personas(owner_id, updated_at DESC);
    """,
)

GUILD_BOOTSTRAP_SQL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS personal_access_tokens (
        id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_id     TEXT        NOT NULL,
        name         TEXT        NOT NULL,
        token_hash   TEXT        NOT NULL,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        expires_at   TIMESTAMPTZ,
        last_used_at TIMESTAMPTZ
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_pats_owner_id ON personal_access_tokens(owner_id);",
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_pats_owner_name
        ON personal_access_tokens(owner_id, name);
    """,
    """
    CREATE TABLE IF NOT EXISTS niuu_instances (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        kind TEXT NOT NULL,
        slug TEXT NOT NULL,
        name TEXT NOT NULL,
        base_url TEXT NOT NULL,
        visibility TEXT NOT NULL DEFAULT 'system',
        owner_id TEXT,
        tenant_id TEXT,
        enabled BOOLEAN NOT NULL DEFAULT true,
        is_default BOOLEAN NOT NULL DEFAULT false,
        config JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT niuu_instances_kind_check CHECK (kind IN ('volundr')),
        CONSTRAINT niuu_instances_visibility_check CHECK (
            visibility IN ('system', 'tenant', 'user')
        ),
        CONSTRAINT niuu_instances_scope_check CHECK (
            (visibility = 'system' AND owner_id IS NULL)
            OR (visibility = 'tenant' AND owner_id IS NULL AND tenant_id IS NOT NULL)
            OR (visibility = 'user' AND owner_id IS NOT NULL AND tenant_id IS NULL)
        )
    );
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_niuu_instances_scope_slug
        ON niuu_instances(kind, slug, COALESCE(owner_id, ''), COALESCE(tenant_id, ''));
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_niuu_instances_kind_enabled
        ON niuu_instances(kind, enabled);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_niuu_instances_visibility
        ON niuu_instances(visibility, owner_id, tenant_id);
    """,
)

OBSERVATORY_BOOTSTRAP_SQL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS observatory_registries (
        registry_key TEXT PRIMARY KEY,
        version BIGINT NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        payload JSONB NOT NULL
    );
    """,
)


def service_database_env_var(service_name: str) -> str:
    """Return the env var that overrides the database name for *service_name*."""
    normalized = service_name.strip().upper().replace("-", "_")
    return f"NIUU_DATABASE_NAME_{normalized}"


def database_name_for_service(service_name: str) -> str:
    """Return the configured local database name for a deployable service."""
    default_name = LOCAL_SERVICE_DATABASES.get(service_name, service_name.replace("-", "_"))
    return os.environ.get(service_database_env_var(service_name), default_name)


def local_service_database_names() -> tuple[str, ...]:
    """Return the distinct database names that local embedded PG should create."""
    names: list[str] = []
    for service_name in LOCAL_SERVICE_DATABASES:
        db_name = database_name_for_service(service_name)
        if db_name not in names:
            names.append(db_name)
    return tuple(names)


def bootstrap_sql_for_service(service_name: str) -> tuple[str, ...]:
    """Return local bootstrap SQL statements for a service-owned database."""
    match service_name:
        case "niuu-shared":
            return NIUU_SHARED_BOOTSTRAP_SQL
        case "guild":
            return GUILD_BOOTSTRAP_SQL
        case "observatory":
            return OBSERVATORY_BOOTSTRAP_SQL
        case _:
            return ()
    raise AssertionError("Unreachable bootstrap_sql_for_service fallthrough")


def validate_database_name(name: str) -> str:
    """Validate and normalize a local service database name."""
    normalized = name.strip()
    if not _DB_NAME_PATTERN.fullmatch(normalized):
        raise ValueError(f"Invalid PostgreSQL database name: {name!r}")
    return normalized


def apply_service_database_settings(settings: SettingsT, service_name: str) -> SettingsT:
    """Return a settings copy using the configured database for *service_name*."""
    database_config = getattr(settings, "database", None)
    if database_config is None or not hasattr(database_config, "model_copy"):
        return settings

    target_name = database_name_for_service(service_name)
    current_name = getattr(database_config, "name", "")
    if current_name == target_name:
        return settings

    updated_database = database_config.model_copy(update={"name": target_name})
    return settings.model_copy(update={"database": updated_database})


async def create_pool(config: DatabaseConfig) -> asyncpg.Pool:
    """Create an asyncpg connection pool without implicit schema side effects."""
    return await asyncpg.create_pool(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=config.name,
        min_size=config.min_pool_size,
        max_size=config.max_pool_size,
    )


@asynccontextmanager
async def database_pool(config: DatabaseConfig):
    """Context manager for a clean asyncpg pool lifecycle."""
    pool = await create_pool(config)
    try:
        yield pool
    finally:
        await pool.close()


async def bootstrap_database(
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
    statements: Iterable[str],
) -> None:
    """Run local bootstrap SQL statements against one logical database."""
    conn = await asyncpg.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
    )
    try:
        for statement in statements:
            await conn.execute(statement)
    finally:
        await conn.close()
