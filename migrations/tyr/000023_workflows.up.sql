-- Persisted workflow catalog for Tyr workflow definitions.

CREATE TABLE IF NOT EXISTS workflows (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    version TEXT NOT NULL DEFAULT 'draft',
    scope TEXT NOT NULL DEFAULT 'user',
    owner_id TEXT,
    definition_yaml TEXT,
    graph_json JSONB NOT NULL DEFAULT '{"nodes": [], "edges": []}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT workflows_scope_check CHECK (scope IN ('system', 'user')),
    CONSTRAINT workflows_owner_check CHECK (
        (scope = 'system' AND owner_id IS NULL)
        OR (scope = 'user' AND owner_id IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_workflows_scope_updated_at
    ON workflows(scope, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_workflows_owner_updated_at
    ON workflows(owner_id, updated_at DESC)
    WHERE owner_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_workflows_name
    ON workflows(name);
