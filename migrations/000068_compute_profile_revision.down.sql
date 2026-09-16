-- Older controllers cannot parse the additional payload field.
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM compute_leases WHERE state != 'released') THEN
        RAISE EXCEPTION 'Drain and release all compute allocations before downgrading';
    END IF;
END $$;
UPDATE compute_leases SET data = data - 'profile_revision';
