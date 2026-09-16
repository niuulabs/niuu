-- Profile revisions are stored in the existing JSONB lease payload.
-- Empty revisions on older leases remain readable and cannot enter the warm pool.
UPDATE compute_leases SET data = data || '{"profile_revision": ""}'::jsonb
WHERE NOT data ? 'profile_revision';
