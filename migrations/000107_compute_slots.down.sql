-- Older controllers cannot parse slot-based allocations or the new pool policy keys.
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM compute_leases WHERE state != 'released') THEN
        RAISE EXCEPTION 'Drain and release all compute allocations before downgrading';
    END IF;
END $$;
UPDATE compute_leases
SET data = (data - 'slots')
           || jsonb_build_object('session_id', session_id, 'tenant_id', '', 'owner_id', '')
WHERE NOT data ? 'session_id';
UPDATE compute_pools
SET data = data - ARRAY['slots_per_machine', 'max_sessions_per_machine', 'max_machine_age_seconds'];
DROP TABLE IF EXISTS compute_slots;
