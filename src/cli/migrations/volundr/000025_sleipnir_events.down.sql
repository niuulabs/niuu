-- Rollback: drop Sleipnir audit log table and indexes.
DROP INDEX IF EXISTS idx_sleipnir_events_source_ts;
DROP INDEX IF EXISTS idx_sleipnir_events_correlation;
DROP INDEX IF EXISTS idx_sleipnir_events_type_ts;
DROP TABLE IF EXISTS sleipnir_events;
