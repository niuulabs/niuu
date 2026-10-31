DROP INDEX IF EXISTS ticket_coordinators_session_idx;

ALTER TABLE ticket_coordinators
    DROP COLUMN IF EXISTS advanced_from,
    DROP COLUMN IF EXISTS start_error,
    DROP COLUMN IF EXISTS pending_brief;
