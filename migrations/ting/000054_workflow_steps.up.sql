-- The Ting step engine's durable ledger (NIU-1179).
--
-- A workflow run (a workflow_campaigns row) advances one step activation at a time:
-- Ting activates a step, a harness session closes it, and the engine decides what
-- activates next. workflow_steps holds one row per activation, so the run's whole
-- state survives a restart; workflow_run_events is the append-only log of every
-- outcome event and engine transition, so a run can be replayed and audited.
--
-- These tables belong to Ting's migration set, with workflow_campaigns and
-- workflow_executions: the engine is Ting's, run_id is a campaign id, and Forge
-- and Guild never read the ledger.

CREATE TABLE IF NOT EXISTS workflow_steps (
    -- The activation id: deterministic in (run, node, member, attempt), so a repeated
    -- activation of the same step cannot create a second row.
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES workflow_campaigns(id) ON DELETE CASCADE,
    node_id TEXT NOT NULL CHECK (node_id <> ''),
    -- The persona alias of a stage member; NULL for gate, wait and end steps.
    member TEXT,
    kind TEXT NOT NULL CHECK (kind IN ('stage_member', 'gate', 'wait', 'end')),
    -- Per (run, node, member): 1 for the first activation, +1 for each later one.
    attempt INTEGER NOT NULL CHECK (attempt >= 1),
    -- The run's rework generation that produced this activation.
    generation INTEGER NOT NULL CHECK (generation >= 0),
    state TEXT NOT NULL
        CHECK (state IN ('pending', 'active', 'waiting', 'done', 'failed', 'stale', 'canceled')),
    session_id TEXT,
    -- The events this activation consumed: [{seq, event_type, edge_id, node_id, member}].
    inputs JSONB NOT NULL DEFAULT '[]'::jsonb,
    verdict TEXT,
    outcome JSONB NOT NULL DEFAULT '{}'::jsonb,
    note TEXT NOT NULL DEFAULT '',
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- {kind: person|session|event|time|agent, ref, since}, set while the step is waiting.
    waiting_on JSONB,
    deadline_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    activated_at TIMESTAMPTZ,
    closed_at TIMESTAMPTZ,
    CONSTRAINT workflow_steps_member_check CHECK ((kind = 'stage_member') = (member IS NOT NULL)),
    CONSTRAINT workflow_steps_waiting_check CHECK ((state = 'waiting') = (waiting_on IS NOT NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_steps_attempt
    ON workflow_steps (run_id, node_id, (COALESCE(member, '')), attempt);

CREATE INDEX IF NOT EXISTS idx_workflow_steps_run_state
    ON workflow_steps (run_id, state);

-- Stuck detection (NIU-1181) reads the steps whose deadline has passed.
CREATE INDEX IF NOT EXISTS idx_workflow_steps_deadline
    ON workflow_steps (deadline_at)
    WHERE state = 'active' AND deadline_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS workflow_run_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES workflow_campaigns(id) ON DELETE CASCADE,
    -- 1, 2, 3 ... per run, with no gaps: the order the engine saw the events in.
    seq BIGINT NOT NULL CHECK (seq >= 1),
    -- A workflow event type, or an engine transition ("engine." prefix).
    event_type TEXT NOT NULL CHECK (event_type <> ''),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_activation_id UUID,
    generation INTEGER NOT NULL CHECK (generation >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT workflow_run_events_run_seq UNIQUE (run_id, seq)
);

-- The ledger is a log: a row, once written, is never changed. Deleting a run
-- deletes its rows through the foreign key, which is not an UPDATE.
CREATE OR REPLACE FUNCTION workflow_run_events_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'workflow_run_events is append-only: event % of run % cannot be changed',
        OLD.seq, OLD.run_id;
END;
$$;

DROP TRIGGER IF EXISTS workflow_run_events_no_update ON workflow_run_events;
CREATE TRIGGER workflow_run_events_no_update
    BEFORE UPDATE ON workflow_run_events
    FOR EACH ROW EXECUTE FUNCTION workflow_run_events_immutable();
