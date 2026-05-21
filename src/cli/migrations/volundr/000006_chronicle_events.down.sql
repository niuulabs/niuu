-- Rollback chronicle_events table

DROP INDEX IF EXISTS idx_chronicle_events_t;
DROP INDEX IF EXISTS idx_chronicle_events_session_id;
DROP INDEX IF EXISTS idx_chronicle_events_chronicle_id;
DROP TABLE IF EXISTS chronicle_events;
