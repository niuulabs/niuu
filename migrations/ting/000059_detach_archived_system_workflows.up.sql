-- Every packaged system workflow except "Tool & Skill Builder" was archived out
-- of the shipped catalogue (ting.system_workflows.ARCHIVED_SYSTEM_WORKFLOW_IDS,
-- archive/README.md). Sagas, project boards and ticket coordinators that named
-- one now name none, so nothing dispatches a workflow Ting no longer serves.
-- Campaigns, executions and A2A launches keep the id and their snapshot as history.
UPDATE sagas
SET workflow_id = NULL,
    workflow_version = NULL,
    workflow_snapshot = NULL
WHERE workflow_id IN (
    '03dba0d8-4f29-560a-a4e1-1fc7e5f9ee23',
    'ca01b422-7ab7-4614-901b-8784ef65c76e',
    'f776d181-ed63-4e3a-b5c4-7b128e4d2094',
    '195d5026-7adb-4ce7-97ee-fb1e18ea4c3a',
    '98133eab-cd89-44d1-b32d-26c82f99dddc',
    '0b51b458-0aeb-5a1b-8f79-d805834491e3',
    '1bfc01b1-4d01-5d20-86e1-2956dff11464',
    '42951d75-dd82-4596-92ea-1c0a6ef4b965',
    '5777251e-7ff1-47f7-b182-d040ed9a7a2f',
    'bf5f97dd-d89b-4f3e-a8fc-ea61a42685a5',
    '4e7ebec4-0cfa-4567-98dd-2ee4ce0bc308',
    '7c1f7d52-6a0e-5f0a-9d3e-2b7d9a4e8c11',
    'a4c7067a-ac22-5b64-bde1-ec8ad1507e73',
    '96ecf5df-18a0-542b-9df6-aef6aef6a5db',
    '3f0e6a14-9b2d-5c47-8e61-4d7a2c91b5e3',
    '9a41d7c6-3e52-5b08-a7f4-6c1e0d2b8f37',
    '3f444455-6b63-53c2-b057-3205345a56da'
);

UPDATE sagas
SET board_workflows = COALESCE(
    (
        SELECT jsonb_agg(pin)
        FROM jsonb_array_elements(board_workflows) AS pin
        WHERE pin ->> 'workflow_id' NOT IN (
            '03dba0d8-4f29-560a-a4e1-1fc7e5f9ee23',
            'ca01b422-7ab7-4614-901b-8784ef65c76e',
            'f776d181-ed63-4e3a-b5c4-7b128e4d2094',
            '195d5026-7adb-4ce7-97ee-fb1e18ea4c3a',
            '98133eab-cd89-44d1-b32d-26c82f99dddc',
            '0b51b458-0aeb-5a1b-8f79-d805834491e3',
            '1bfc01b1-4d01-5d20-86e1-2956dff11464',
            '42951d75-dd82-4596-92ea-1c0a6ef4b965',
            '5777251e-7ff1-47f7-b182-d040ed9a7a2f',
            'bf5f97dd-d89b-4f3e-a8fc-ea61a42685a5',
            '4e7ebec4-0cfa-4567-98dd-2ee4ce0bc308',
            '7c1f7d52-6a0e-5f0a-9d3e-2b7d9a4e8c11',
            'a4c7067a-ac22-5b64-bde1-ec8ad1507e73',
            '96ecf5df-18a0-542b-9df6-aef6aef6a5db',
            '3f0e6a14-9b2d-5c47-8e61-4d7a2c91b5e3',
            '9a41d7c6-3e52-5b08-a7f4-6c1e0d2b8f37',
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
        'ca01b422-7ab7-4614-901b-8784ef65c76e',
        'f776d181-ed63-4e3a-b5c4-7b128e4d2094',
        '195d5026-7adb-4ce7-97ee-fb1e18ea4c3a',
        '98133eab-cd89-44d1-b32d-26c82f99dddc',
        '0b51b458-0aeb-5a1b-8f79-d805834491e3',
        '1bfc01b1-4d01-5d20-86e1-2956dff11464',
        '42951d75-dd82-4596-92ea-1c0a6ef4b965',
        '5777251e-7ff1-47f7-b182-d040ed9a7a2f',
        'bf5f97dd-d89b-4f3e-a8fc-ea61a42685a5',
        '4e7ebec4-0cfa-4567-98dd-2ee4ce0bc308',
        '7c1f7d52-6a0e-5f0a-9d3e-2b7d9a4e8c11',
        'a4c7067a-ac22-5b64-bde1-ec8ad1507e73',
        '96ecf5df-18a0-542b-9df6-aef6aef6a5db',
        '3f0e6a14-9b2d-5c47-8e61-4d7a2c91b5e3',
        '9a41d7c6-3e52-5b08-a7f4-6c1e0d2b8f37',
        '3f444455-6b63-53c2-b057-3205345a56da'
    )
);

UPDATE ticket_coordinators
SET requested_workflow_id = NULL
WHERE requested_workflow_id IN (
    '03dba0d8-4f29-560a-a4e1-1fc7e5f9ee23',
    'ca01b422-7ab7-4614-901b-8784ef65c76e',
    'f776d181-ed63-4e3a-b5c4-7b128e4d2094',
    '195d5026-7adb-4ce7-97ee-fb1e18ea4c3a',
    '98133eab-cd89-44d1-b32d-26c82f99dddc',
    '0b51b458-0aeb-5a1b-8f79-d805834491e3',
    '1bfc01b1-4d01-5d20-86e1-2956dff11464',
    '42951d75-dd82-4596-92ea-1c0a6ef4b965',
    '5777251e-7ff1-47f7-b182-d040ed9a7a2f',
    'bf5f97dd-d89b-4f3e-a8fc-ea61a42685a5',
    '4e7ebec4-0cfa-4567-98dd-2ee4ce0bc308',
    '7c1f7d52-6a0e-5f0a-9d3e-2b7d9a4e8c11',
    'a4c7067a-ac22-5b64-bde1-ec8ad1507e73',
    '96ecf5df-18a0-542b-9df6-aef6aef6a5db',
    '3f0e6a14-9b2d-5c47-8e61-4d7a2c91b5e3',
    '9a41d7c6-3e52-5b08-a7f4-6c1e0d2b8f37',
    '3f444455-6b63-53c2-b057-3205345a56da'
);
