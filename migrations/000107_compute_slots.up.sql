-- A machine (compute_leases) serves sessions through slot bindings (compute_slots).
-- Every machine that held a session becomes slot 0 of itself, and the session fields leave
-- the machine record. Statement order makes a second run a no-op: the insert skips
-- allocations that already have a slot, the failed-machine remap matches on the
-- session_id key that the last statement removes.
CREATE TABLE IF NOT EXISTS compute_slots (
    id UUID PRIMARY KEY,
    allocation_id UUID NOT NULL REFERENCES compute_leases (id) ON DELETE CASCADE,
    slot INTEGER NOT NULL CHECK (slot >= 0),
    pool_id TEXT NOT NULL,
    session_id UUID NOT NULL,
    state TEXT NOT NULL CHECK (state IN
        ('starting', 'busy', 'failed', 'draining', 'retired', 'released')),
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (data->>'id' = id::text),
    CHECK (data->>'allocation_id' = allocation_id::text),
    CHECK ((data->>'slot')::integer = slot),
    CHECK (data->>'pool_id' = pool_id),
    CHECK (data->>'session_id' = session_id::text),
    CHECK (data->>'state' = state)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_compute_slots_occupied
    ON compute_slots (allocation_id, slot) WHERE state != 'released';
CREATE UNIQUE INDEX IF NOT EXISTS idx_compute_slots_live_session
    ON compute_slots (pool_id, session_id) WHERE state IN ('starting', 'busy', 'failed', 'draining');
CREATE INDEX IF NOT EXISTS idx_compute_slots_allocation ON compute_slots (allocation_id);

WITH bound AS (
    SELECT gen_random_uuid() AS slot_id, l.id AS allocation_id, l.pool_id, l.state AS lease_state,
           l.data,
           CASE l.state WHEN 'busy' THEN 'busy' WHEN 'draining' THEN 'draining'
                        WHEN 'failed' THEN 'failed' ELSE 'starting' END AS slot_state
    FROM compute_leases l
    WHERE l.state IN ('provisioning', 'ready', 'busy', 'draining', 'failed')
      AND l.data->>'session_id' IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM compute_slots s WHERE s.allocation_id = l.id)
)
INSERT INTO compute_slots (id, allocation_id, slot, pool_id, session_id, state, data)
SELECT slot_id, allocation_id, 0, pool_id, (data->>'session_id')::uuid, slot_state,
       jsonb_build_object(
           'id', slot_id, 'allocation_id', allocation_id, 'slot', 0, 'pool_id', pool_id,
           'session_id', data->'session_id', 'tenant_id', data->'tenant_id',
           'owner_id', data->'owner_id',
           'execution_plan', COALESCE(data->'execution_plan', 'null'::jsonb),
           'session_bootstrap_ref', COALESCE(data->'session_bootstrap_ref', 'null'::jsonb),
           'created_at', to_jsonb(NOW()),
           'start_deadline', COALESCE(data->'provision_deadline', 'null'::jsonb),
           'runtime_data_started', COALESCE(data->'runtime_data_started', 'false'::jsonb),
           'runtime_started', COALESCE(data->'runtime_started', 'false'::jsonb),
           'stop_requested', COALESCE(data->'stop_requested', 'false'::jsonb),
           'state', slot_state,
           'error', CASE WHEN lease_state = 'failed'
                         THEN COALESCE(data->'error', 'null'::jsonb) ELSE 'null'::jsonb END)
FROM bound;

-- A machine in service is idle; a failed runtime start fails its slot, not the machine.
UPDATE compute_leases SET state = 'idle', data = jsonb_set(data, '{state}', '"idle"')
WHERE state = 'busy';
UPDATE compute_leases
SET state = 'ready', data = jsonb_set(data, '{state}', '"ready"') || '{"error": null}'::jsonb
WHERE state = 'failed' AND data->>'session_id' IS NOT NULL
  AND data->>'error' LIKE 'Runtime startup failed%';
UPDATE compute_leases
SET data = (data - ARRAY['session_id', 'tenant_id', 'owner_id', 'session_bootstrap_ref',
                         'runtime_data_started', 'runtime_started', 'stop_requested'])
           || jsonb_build_object('slots', COALESCE(data->'slots', '1'::jsonb)),
    session_id = CASE WHEN state = 'released' THEN session_id ELSE NULL END
WHERE data ? 'session_id' OR NOT data ? 'slots';
