-- niuu#1282 removed the packaged "Ting Run Flow + Security + Memory Curation"
-- workflow (29393e53-5a29-5f37-aa87-1171aaf20726, listed in
-- ting.system_workflows.RETIRED_SYSTEM_WORKFLOW_IDS). Sagas and ticket
-- coordinators that named it as their workflow now name none, so nothing
-- dispatches a workflow that no longer exists. Executions and campaigns keep
-- the id as history.
UPDATE sagas
SET workflow_id = NULL
WHERE workflow_id = '29393e53-5a29-5f37-aa87-1171aaf20726';

UPDATE ticket_coordinators
SET requested_workflow_id = NULL
WHERE requested_workflow_id = '29393e53-5a29-5f37-aa87-1171aaf20726';
