ALTER TABLE ticket_coordinators
    DROP COLUMN IF EXISTS requested_at,
    DROP COLUMN IF EXISTS requested_workflow_id;

ALTER TABLE sagas
    DROP COLUMN IF EXISTS board_workflows;
