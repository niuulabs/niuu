-- Standby guests have no session or user binding. Keep capacity until confirmed deletion.
ALTER TABLE compute_leases ALTER COLUMN session_id DROP NOT NULL;
ALTER TABLE compute_leases DROP CONSTRAINT IF EXISTS compute_leases_state_check;
ALTER TABLE compute_leases ADD CONSTRAINT compute_leases_state_check CHECK (state IN (
    'provisioning', 'idle', 'quarantined', 'ready', 'busy', 'draining', 'released', 'failed'
));
CREATE TABLE IF NOT EXISTS compute_pools (
    pool_id TEXT PRIMARY KEY,
    data JSONB NOT NULL
);
UPDATE compute_leases SET data = data || jsonb_build_object('created_at', created_at)
WHERE NOT data ? 'created_at';
