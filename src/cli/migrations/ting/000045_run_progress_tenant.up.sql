-- Bind operational session/PR links to the same tenant as the authorized saga.
-- Legacy rows remain attributable only to the empty/default tenant.
ALTER TABLE run_progress
    ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_run_progress_work_scope
    ON run_progress (
        owner_id,
        tenant_id,
        tracker_connection_id,
        saga_tracker_id
    );
