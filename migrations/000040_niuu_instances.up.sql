-- Shared runtime instance registry for multi-instance Niuu deployments.

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
    CONSTRAINT niuu_instances_visibility_check CHECK (visibility IN ('system', 'tenant', 'user')),
    CONSTRAINT niuu_instances_scope_check CHECK (
        (visibility = 'system' AND owner_id IS NULL)
        OR (visibility = 'tenant' AND owner_id IS NULL AND tenant_id IS NOT NULL)
        OR (visibility = 'user' AND owner_id IS NOT NULL AND tenant_id IS NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_niuu_instances_scope_slug
    ON niuu_instances(kind, slug, COALESCE(owner_id, ''), COALESCE(tenant_id, ''));

CREATE INDEX IF NOT EXISTS idx_niuu_instances_kind_enabled
    ON niuu_instances(kind, enabled);

CREATE INDEX IF NOT EXISTS idx_niuu_instances_visibility
    ON niuu_instances(visibility, owner_id, tenant_id);
