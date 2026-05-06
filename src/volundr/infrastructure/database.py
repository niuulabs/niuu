"""Database infrastructure for PostgreSQL."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import asyncpg

from volundr.config import DatabaseConfig

SESSIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    id UUID PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    model VARCHAR(100) NOT NULL,
    source JSONB NOT NULL DEFAULT '{"type": "git", "repo": "", "branch": "main"}'::jsonb,
    status VARCHAR(20) NOT NULL DEFAULT 'created',
    chat_endpoint TEXT,
    code_endpoint TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    last_active TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    message_count INTEGER NOT NULL DEFAULT 0,
    tokens_used INTEGER NOT NULL DEFAULT 0,
    pod_name VARCHAR(255),
    error TEXT,
    tracker_issue_id TEXT,
    issue_tracker_url TEXT,
    preset_id TEXT,
    archived_at TIMESTAMP WITH TIME ZONE,
    activity_state TEXT DEFAULT 'idle',
    activity_metadata JSONB DEFAULT '{}'
);
"""

CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_sessions_created_at ON sessions(created_at);
CREATE INDEX IF NOT EXISTS idx_sessions_last_active ON sessions(last_active);
CREATE INDEX IF NOT EXISTS idx_sessions_archived_at ON sessions(archived_at);
"""

TOKEN_USAGE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS token_usage (
    id UUID PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    recorded_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    tokens INTEGER NOT NULL,
    provider VARCHAR(20) NOT NULL,
    model VARCHAR(100) NOT NULL,
    cost NUMERIC(10, 6)
);
"""

TOKEN_USAGE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_token_usage_recorded_at ON token_usage(recorded_at);
CREATE INDEX IF NOT EXISTS idx_token_usage_session_id ON token_usage(session_id);
CREATE INDEX IF NOT EXISTS idx_token_usage_provider ON token_usage(provider);
"""

CHRONICLES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS chronicles (
    id UUID PRIMARY KEY,
    session_id UUID REFERENCES sessions(id) ON DELETE SET NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'draft',
    project VARCHAR(255) NOT NULL,
    repo VARCHAR(500) NOT NULL,
    branch VARCHAR(255) NOT NULL,
    model VARCHAR(100) NOT NULL,
    config_snapshot JSONB NOT NULL DEFAULT '{}',
    summary TEXT,
    key_changes JSONB NOT NULL DEFAULT '[]',
    unfinished_work TEXT,
    token_usage INTEGER NOT NULL DEFAULT 0,
    cost NUMERIC(10, 6),
    duration_seconds INTEGER,
    tags TEXT[] NOT NULL DEFAULT '{}',
    parent_chronicle_id UUID REFERENCES chronicles(id) ON DELETE SET NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
"""

CHRONICLES_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_chronicles_session_id ON chronicles(session_id);
CREATE INDEX IF NOT EXISTS idx_chronicles_project ON chronicles(project);
CREATE INDEX IF NOT EXISTS idx_chronicles_repo ON chronicles(repo);
CREATE INDEX IF NOT EXISTS idx_chronicles_created_at ON chronicles(created_at);
CREATE INDEX IF NOT EXISTS idx_chronicles_tags ON chronicles USING GIN(tags);
CREATE INDEX IF NOT EXISTS idx_chronicles_parent_id ON chronicles(parent_chronicle_id);
"""

CHRONICLE_EVENTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS chronicle_events (
    id UUID PRIMARY KEY,
    chronicle_id UUID NOT NULL REFERENCES chronicles(id) ON DELETE CASCADE,
    session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    t INTEGER NOT NULL,
    type VARCHAR(20) NOT NULL,
    label TEXT NOT NULL,
    tokens INTEGER,
    action VARCHAR(20),
    ins INTEGER,
    del INTEGER,
    hash VARCHAR(40),
    exit_code INTEGER,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
"""

CHRONICLE_EVENTS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_chronicle_events_chronicle_id ON chronicle_events(chronicle_id);
CREATE INDEX IF NOT EXISTS idx_chronicle_events_session_id ON chronicle_events(session_id);
CREATE INDEX IF NOT EXISTS idx_chronicle_events_t ON chronicle_events(t);
"""

SESSION_EVENTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS session_events (
    id              UUID PRIMARY KEY,
    session_id      UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    event_type      VARCHAR(30) NOT NULL,
    timestamp       TIMESTAMP WITH TIME ZONE NOT NULL,
    data            JSONB NOT NULL DEFAULT '{}',
    tokens_in       INTEGER,
    tokens_out      INTEGER,
    cost            NUMERIC(10, 6),
    duration_ms     INTEGER,
    model           VARCHAR(100),
    sequence        INTEGER NOT NULL,
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
"""

SESSION_EVENTS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_session_events_session_id
    ON session_events(session_id);
CREATE INDEX IF NOT EXISTS idx_session_events_session_type
    ON session_events(session_id, event_type);
CREATE INDEX IF NOT EXISTS idx_session_events_session_ts
    ON session_events(session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_session_events_sequence
    ON session_events(session_id, sequence);
"""

TENANTS_TABLE_SQL = """
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
"""

TENANTS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_tenants_path ON tenants(path);
CREATE INDEX IF NOT EXISTS idx_tenants_parent_id ON tenants(parent_id);
"""

USERS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id              TEXT PRIMARY KEY,
    email           TEXT NOT NULL,
    display_name    TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'active',
    home_pvc        TEXT,
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
"""

USERS_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);
"""

TENANT_MEMBERSHIPS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS tenant_memberships (
    user_id         TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    role            TEXT NOT NULL DEFAULT 'volundr:developer',
    granted_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, tenant_id)
);
"""

TENANT_MEMBERSHIPS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_tenant_memberships_tenant_id ON tenant_memberships(tenant_id);
CREATE INDEX IF NOT EXISTS idx_tenant_memberships_user_id ON tenant_memberships(user_id);
"""

SESSIONS_IDENTITY_COLUMNS_SQL = """
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS owner_id TEXT;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS tenant_id TEXT;
"""

SESSIONS_WORKLOAD_TYPE_COLUMN_SQL = """
ALTER TABLE sessions
    ADD COLUMN IF NOT EXISTS workload_type VARCHAR(100) NOT NULL DEFAULT 'session';
"""

COMMUNICATION_ROUTES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS communication_routes (
    id UUID PRIMARY KEY,
    platform TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    thread_id TEXT,
    session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    owner_id TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'room',
    default_target TEXT,
    active BOOLEAN NOT NULL DEFAULT true,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
"""

COMMUNICATION_ROUTES_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_comm_routes_session_id
    ON communication_routes(session_id);
CREATE INDEX IF NOT EXISTS idx_comm_routes_owner_id
    ON communication_routes(owner_id);
CREATE INDEX IF NOT EXISTS idx_comm_routes_active_lookup
    ON communication_routes(platform, conversation_id, thread_id, active);
"""

COMMUNICATION_CURSORS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS communication_cursors (
    platform TEXT NOT NULL,
    consumer_key TEXT NOT NULL,
    cursor TEXT NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    PRIMARY KEY (platform, consumer_key)
);
"""

COMMUNICATION_CURSORS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_comm_cursors_updated_at
    ON communication_cursors(updated_at);
"""

SESSIONS_IDENTITY_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_sessions_owner_id ON sessions(owner_id);
CREATE INDEX IF NOT EXISTS idx_sessions_tenant_id ON sessions(tenant_id);
"""

SAVED_PROMPTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS saved_prompts (
    id              UUID PRIMARY KEY,
    name            VARCHAR(255) NOT NULL,
    content         TEXT NOT NULL,
    scope           VARCHAR(20) NOT NULL DEFAULT 'global',
    project_repo    VARCHAR(500),
    tags            TEXT[] NOT NULL DEFAULT '{}',
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
"""

SAVED_PROMPTS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_saved_prompts_scope ON saved_prompts(scope);
CREATE INDEX IF NOT EXISTS idx_saved_prompts_project_repo ON saved_prompts(project_repo);
CREATE INDEX IF NOT EXISTS idx_saved_prompts_tags ON saved_prompts USING GIN(tags);
"""

PROJECT_MAPPINGS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS project_mappings (
    id              UUID PRIMARY KEY,
    repo_url        VARCHAR(500) NOT NULL,
    project_id      VARCHAR(255) NOT NULL,
    project_name    VARCHAR(255) NOT NULL DEFAULT '',
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
"""

PROJECT_MAPPINGS_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_project_mappings_repo_url ON project_mappings(repo_url);
CREATE INDEX IF NOT EXISTS idx_project_mappings_project_id ON project_mappings(project_id);
"""

VOLUNDR_PRESETS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS volundr_presets (
    id                UUID PRIMARY KEY,
    name              VARCHAR(255) NOT NULL,
    description       TEXT NOT NULL DEFAULT '',
    is_default        BOOLEAN NOT NULL DEFAULT FALSE,
    cli_tool          VARCHAR(100) NOT NULL DEFAULT '',
    workload_type     VARCHAR(100) NOT NULL DEFAULT 'session',
    model             VARCHAR(100),
    system_prompt     TEXT,
    resource_config   JSONB NOT NULL DEFAULT '{}',
    mcp_servers       JSONB NOT NULL DEFAULT '[]',
    terminal_sidecar  JSONB NOT NULL DEFAULT '{}',
    skills            JSONB NOT NULL DEFAULT '[]',
    rules             JSONB NOT NULL DEFAULT '[]',
    env_vars          JSONB NOT NULL DEFAULT '{}',
    env_secret_refs   JSONB NOT NULL DEFAULT '[]',
    workload_config   JSONB NOT NULL DEFAULT '{}',
    source            JSONB,
    integration_ids   JSONB NOT NULL DEFAULT '[]',
    setup_scripts     JSONB NOT NULL DEFAULT '[]',
    created_at        TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
"""

VOLUNDR_PRESETS_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_volundr_presets_name ON volundr_presets(name);
CREATE INDEX IF NOT EXISTS idx_volundr_presets_cli_tool ON volundr_presets(cli_tool);
CREATE INDEX IF NOT EXISTS idx_volundr_presets_is_default ON volundr_presets(is_default);
"""

CREDENTIAL_METADATA_TABLE_SQL = """
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
"""

CREDENTIAL_METADATA_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_credential_metadata_owner
    ON credential_metadata(owner_type, owner_id);
"""

INTEGRATION_CONNECTIONS_TABLE_SQL = """
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
"""

INTEGRATION_CONNECTIONS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_integration_connections_owner
    ON integration_connections(owner_id, integration_type);
"""

WORKSPACES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS workspaces (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID NOT NULL REFERENCES sessions(id),
    user_id         TEXT NOT NULL REFERENCES users(id),
    tenant_id       TEXT NOT NULL REFERENCES tenants(id),
    pvc_name        TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active',
    size_gb         INT NOT NULL DEFAULT 1,
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    archived_at     TIMESTAMP WITH TIME ZONE,
    deleted_at      TIMESTAMP WITH TIME ZONE
);
"""

WORKSPACES_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_workspaces_session_id ON workspaces(session_id);
CREATE INDEX IF NOT EXISTS idx_workspaces_user_id ON workspaces(user_id);
CREATE INDEX IF NOT EXISTS idx_workspaces_tenant_id ON workspaces(tenant_id);
CREATE INDEX IF NOT EXISTS idx_workspaces_status ON workspaces(status);
"""

FEATURE_TOGGLES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS feature_toggles (
    feature_key     TEXT PRIMARY KEY,
    enabled         BOOLEAN NOT NULL DEFAULT true,
    updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
"""

USER_FEATURE_PREFERENCES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS user_feature_preferences (
    user_id         TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    feature_key     TEXT NOT NULL,
    visible         BOOLEAN NOT NULL DEFAULT true,
    sort_order      INT NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, feature_key)
);
"""

USER_FEATURE_PREFERENCES_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_user_feature_prefs_user
    ON user_feature_preferences(user_id);
"""

PERSONAL_ACCESS_TOKENS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS personal_access_tokens (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id     TEXT        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name         TEXT        NOT NULL,
    token_hash   TEXT        NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at   TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ
);
"""

PERSONAL_ACCESS_TOKENS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_pats_owner_id ON personal_access_tokens(owner_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_pats_owner_name ON personal_access_tokens(owner_id, name);
"""

SLEIPNIR_EVENTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS sleipnir_events (
    event_id        TEXT        PRIMARY KEY,
    event_type      TEXT        NOT NULL,
    source          TEXT        NOT NULL,
    summary         TEXT,
    urgency         REAL,
    domain          TEXT,
    correlation_id  TEXT,
    causation_id    TEXT,
    tenant_id       TEXT,
    payload         JSONB,
    timestamp       TIMESTAMPTZ NOT NULL,
    ttl             INTEGER
);
"""

SLEIPNIR_EVENTS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_sleipnir_events_type_ts
    ON sleipnir_events (event_type, timestamp);
CREATE INDEX IF NOT EXISTS idx_sleipnir_events_correlation
    ON sleipnir_events (correlation_id);
CREATE INDEX IF NOT EXISTS idx_sleipnir_events_source_ts
    ON sleipnir_events (source, timestamp);
"""


async def create_pool(config: DatabaseConfig) -> asyncpg.Pool:
    """Create an asyncpg connection pool."""
    return await asyncpg.create_pool(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=config.name,
        min_size=config.min_pool_size,
        max_size=config.max_pool_size,
    )


async def init_db(pool: asyncpg.Pool) -> None:
    """Initialize database schema (create tables if not exist).

    Note: Schema migrations are handled by the migrate init container.
    This function only creates tables if they don't exist (for development).
    """
    async with pool.acquire() as conn:
        await conn.execute(SESSIONS_TABLE_SQL)
        await conn.execute(CREATE_INDEX_SQL)
        await conn.execute(TOKEN_USAGE_TABLE_SQL)
        await conn.execute(TOKEN_USAGE_INDEX_SQL)
        await conn.execute(CHRONICLES_TABLE_SQL)
        await conn.execute(CHRONICLES_INDEX_SQL)
        await conn.execute(CHRONICLE_EVENTS_TABLE_SQL)
        await conn.execute(CHRONICLE_EVENTS_INDEX_SQL)
        await conn.execute(SESSION_EVENTS_TABLE_SQL)
        await conn.execute(SESSION_EVENTS_INDEX_SQL)
        # Identity tables (must come before FK columns on sessions)
        await conn.execute(TENANTS_TABLE_SQL)
        await conn.execute(TENANTS_INDEX_SQL)
        await conn.execute(USERS_TABLE_SQL)
        await conn.execute(USERS_INDEX_SQL)
        await conn.execute(TENANT_MEMBERSHIPS_TABLE_SQL)
        await conn.execute(TENANT_MEMBERSHIPS_INDEX_SQL)
        await conn.execute(SESSIONS_IDENTITY_COLUMNS_SQL)
        await conn.execute(SESSIONS_WORKLOAD_TYPE_COLUMN_SQL)
        await conn.execute(SESSIONS_IDENTITY_INDEX_SQL)
        await conn.execute(COMMUNICATION_ROUTES_TABLE_SQL)
        await conn.execute(COMMUNICATION_ROUTES_INDEX_SQL)
        await conn.execute(COMMUNICATION_CURSORS_TABLE_SQL)
        await conn.execute(COMMUNICATION_CURSORS_INDEX_SQL)
        # Additional tables from migrations
        await conn.execute(SAVED_PROMPTS_TABLE_SQL)
        await conn.execute(SAVED_PROMPTS_INDEX_SQL)
        await conn.execute(PROJECT_MAPPINGS_TABLE_SQL)
        await conn.execute(PROJECT_MAPPINGS_INDEX_SQL)
        await conn.execute(VOLUNDR_PRESETS_TABLE_SQL)
        await conn.execute(VOLUNDR_PRESETS_INDEX_SQL)
        await conn.execute(CREDENTIAL_METADATA_TABLE_SQL)
        await conn.execute(CREDENTIAL_METADATA_INDEX_SQL)
        await conn.execute(INTEGRATION_CONNECTIONS_TABLE_SQL)
        await conn.execute(INTEGRATION_CONNECTIONS_INDEX_SQL)
        await conn.execute(WORKSPACES_TABLE_SQL)
        await conn.execute(WORKSPACES_INDEX_SQL)
        await conn.execute(FEATURE_TOGGLES_TABLE_SQL)
        await conn.execute(USER_FEATURE_PREFERENCES_TABLE_SQL)
        await conn.execute(USER_FEATURE_PREFERENCES_INDEX_SQL)
        await conn.execute(PERSONAL_ACCESS_TOKENS_TABLE_SQL)
        await conn.execute(PERSONAL_ACCESS_TOKENS_INDEX_SQL)
        await conn.execute(SLEIPNIR_EVENTS_TABLE_SQL)
        await conn.execute(SLEIPNIR_EVENTS_INDEX_SQL)


@asynccontextmanager
async def database_pool(config: DatabaseConfig) -> AsyncGenerator[asyncpg.Pool, None]:
    """Context manager for database pool lifecycle."""
    pool = await create_pool(config)
    try:
        await init_db(pool)
        yield pool
    finally:
        await pool.close()
