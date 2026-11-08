DROP TRIGGER IF EXISTS workflow_run_events_no_update ON workflow_run_events;
DROP FUNCTION IF EXISTS workflow_run_events_immutable();
DROP TABLE IF EXISTS workflow_run_events;
DROP TABLE IF EXISTS workflow_steps;
