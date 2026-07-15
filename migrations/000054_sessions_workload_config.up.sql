ALTER TABLE sessions
    ADD COLUMN IF NOT EXISTS workload_config JSONB NOT NULL DEFAULT '{}'::jsonb;
