-- The workflows a project's board runs in order, each pinned to the version the
-- board shows. Empty means the board is the project's one assigned workflow.
ALTER TABLE sagas
    ADD COLUMN IF NOT EXISTS board_workflows JSONB NOT NULL DEFAULT '[]'::jsonb;

-- The workflow a coordinator was last asked to start, so a ticket shows as
-- starting there until the run appears.
ALTER TABLE ticket_coordinators
    ADD COLUMN IF NOT EXISTS requested_workflow_id TEXT,
    ADD COLUMN IF NOT EXISTS requested_at TIMESTAMPTZ;
