-- Rollback is safe only after admission is paused and every allocation is released.
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM compute_leases WHERE state != 'released') THEN
        RAISE EXCEPTION 'Drain and release all compute allocations before downgrading';
    END IF;
END $$;
DELETE FROM compute_leases WHERE session_id IS NULL;
UPDATE compute_leases SET data = data - ARRAY[
    'session_bootstrap_ref', 'created_at', 'provision_deadline', 'idle_since',
    'retry_after', 'failures', 'stop_requested', 'bootstrap_owner'
];
ALTER TABLE compute_leases ALTER COLUMN session_id SET NOT NULL;
ALTER TABLE compute_leases DROP CONSTRAINT IF EXISTS compute_leases_state_check;
ALTER TABLE compute_leases ADD CONSTRAINT compute_leases_state_check CHECK (state IN (
    'provisioning', 'ready', 'busy', 'draining', 'released', 'failed'
));
DROP TABLE IF EXISTS compute_pools;
