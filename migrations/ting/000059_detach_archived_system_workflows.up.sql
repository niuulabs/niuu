-- The packaged "Code & Review Flow" (03dba0d8-4f29-560a-a4e1-1fc7e5f9ee23),
-- "Infra Change Flow" (0b51b458-0aeb-5a1b-8f79-d805834491e3) and "Tracker
-- Delivery Flow" (3f444455-6b63-53c2-b057-3205345a56da) were archived out of
-- the shipped catalogue (ting.system_workflows.ARCHIVED_SYSTEM_WORKFLOW_IDS,
-- archive/README.md). Sagas, project boards and ticket coordinators that named
-- one now name none, so nothing dispatches a workflow Ting no longer serves.
-- Campaigns, executions and A2A launches keep the id and their snapshot as history.
UPDATE sagas
SET workflow_id = NULL,
    workflow_version = NULL,
    workflow_snapshot = NULL
WHERE workflow_id IN (
    '03dba0d8-4f29-560a-a4e1-1fc7e5f9ee23',
    '0b51b458-0aeb-5a1b-8f79-d805834491e3',
    '3f444455-6b63-53c2-b057-3205345a56da'
);

UPDATE sagas
SET board_workflows = COALESCE(
    (
        SELECT jsonb_agg(pin)
        FROM jsonb_array_elements(board_workflows) AS pin
        WHERE pin ->> 'workflow_id' NOT IN (
            '03dba0d8-4f29-560a-a4e1-1fc7e5f9ee23',
            '0b51b458-0aeb-5a1b-8f79-d805834491e3',
            '3f444455-6b63-53c2-b057-3205345a56da'
        )
    ),
    '[]'::jsonb
)
WHERE jsonb_typeof(board_workflows) = 'array'
  AND EXISTS (
    SELECT 1
    FROM jsonb_array_elements(board_workflows) AS pin
    WHERE pin ->> 'workflow_id' IN (
        '03dba0d8-4f29-560a-a4e1-1fc7e5f9ee23',
        '0b51b458-0aeb-5a1b-8f79-d805834491e3',
        '3f444455-6b63-53c2-b057-3205345a56da'
    )
);

UPDATE ticket_coordinators
SET requested_workflow_id = NULL
WHERE requested_workflow_id IN (
    '03dba0d8-4f29-560a-a4e1-1fc7e5f9ee23',
    '0b51b458-0aeb-5a1b-8f79-d805834491e3',
    '3f444455-6b63-53c2-b057-3205345a56da'
);
