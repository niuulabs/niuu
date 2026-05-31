CREATE TABLE IF NOT EXISTS observatory_registries (
    registry_key TEXT PRIMARY KEY,
    version BIGINT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload JSONB NOT NULL
);
