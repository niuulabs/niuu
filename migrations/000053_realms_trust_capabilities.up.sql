-- Realm governance foundation: a realm is a Valkyrie's domain, carrying its
-- build capability, trust level, and per-Valkyrie build config in the shared
-- volundr/niuu postgres so ravn can read it over HTTP (no ravn-local database).
CREATE TABLE IF NOT EXISTS realms (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    slug             TEXT        NOT NULL UNIQUE,
    name             TEXT        NOT NULL,
    sleipnir_domain  TEXT,
    owner_id         TEXT,
    instance_id      TEXT,
    autonomy_profile TEXT        NOT NULL DEFAULT 'balanced',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Trust grants: what a realm's Valkyrie is allowed to do, per action class.
-- The 'build' grant (limits like {"workflow":"tool-builder"}) is what P3/P4 read
-- to decide which Ting workflow a Valkyrie may commission, at what autonomy level.
CREATE TABLE IF NOT EXISTS trust_grants (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    realm_id     UUID        NOT NULL REFERENCES realms(id) ON DELETE CASCADE,
    action_class TEXT        NOT NULL,
    target       TEXT        NOT NULL DEFAULT '*',
    level        INTEGER     NOT NULL DEFAULT 0,
    limits       JSONB       NOT NULL DEFAULT '{}',
    granted_by   TEXT,
    granted_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_trust_grants_realm_action
    ON trust_grants(realm_id, action_class);

-- Capabilities: the tools/skills/personas/integrations a realm has (or lacks).
-- status 'gap' means the Valkyrie may commission a build to fill it.
CREATE TABLE IF NOT EXISTS capabilities (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    realm_id        UUID        NOT NULL REFERENCES realms(id) ON DELETE CASCADE,
    name            TEXT        NOT NULL,
    kind            TEXT        NOT NULL,
    status          TEXT        NOT NULL DEFAULT 'gap',
    trust_level     INTEGER     NOT NULL DEFAULT 0,
    mimir_page_path TEXT,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (realm_id, name)
);

CREATE INDEX IF NOT EXISTS idx_capabilities_realm ON capabilities(realm_id);
