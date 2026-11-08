-- Stuck detection for workflow steps (NIU-1181).
--
-- A sweep over the open steps of running workflows flags the ones that are stuck
-- (overdue, never started, never delivered, unclosed, or whose session is gone). A flag
-- is a durable fact about one step, kept in its own table rather than as a column of
-- workflow_steps or only as run events:
--
--   * (activation_id, kind) is the primary key, so a flag is raised once per step and
--     kind however many sweeps, or Ting replicas, see the same condition: the insert
--     either creates the row or it does not;
--   * the notification and the resolution are state of the flag, not of the step, and
--     a step can carry several kinds at once;
--   * the run's event log still records each raise (engine.step.flagged), so the audit
--     trail stays in one append-only place.
--
-- The table belongs to Ting's migration set with workflow_steps.

CREATE TABLE IF NOT EXISTS workflow_step_flags (
    activation_id UUID NOT NULL REFERENCES workflow_steps(id) ON DELETE CASCADE,
    kind TEXT NOT NULL
        CHECK (kind IN ('overdue', 'not_started', 'undelivered', 'unclosed', 'session_gone')),
    run_id UUID NOT NULL REFERENCES workflow_campaigns(id) ON DELETE CASCADE,
    -- When the condition began as far as Ting can tell (the deadline, the activation, the
    -- end of the turn); raised_at is when a sweep first saw it.
    since TIMESTAMPTZ NOT NULL,
    raised_at TIMESTAMPTZ NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    -- When a person was told; NULL until the notification went out.
    notified_at TIMESTAMPTZ,
    -- Set when the step closed or the condition cleared, with how it ended.
    resolved_at TIMESTAMPTZ,
    resolution TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (activation_id, kind),
    CONSTRAINT workflow_step_flags_resolution_check
        CHECK ((resolved_at IS NULL) = (resolution = ''))
);

CREATE INDEX IF NOT EXISTS idx_workflow_step_flags_run
    ON workflow_step_flags (run_id, raised_at);

CREATE INDEX IF NOT EXISTS idx_workflow_step_flags_open
    ON workflow_step_flags (run_id)
    WHERE resolved_at IS NULL;
