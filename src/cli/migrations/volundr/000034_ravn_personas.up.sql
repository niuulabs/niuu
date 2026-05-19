-- Add per-user persona registry for the Volundr-hosted Ravn API

CREATE TABLE IF NOT EXISTS ravn_personas (
    owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(128) NOT NULL,
    config_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    runtime_config_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    PRIMARY KEY (owner_id, name)
);

CREATE INDEX IF NOT EXISTS idx_ravn_personas_owner_updated
    ON ravn_personas(owner_id, updated_at DESC);
