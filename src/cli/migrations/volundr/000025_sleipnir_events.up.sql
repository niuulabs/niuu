-- Sleipnir audit log — persistent record of every event on the bus.
-- Partitioned by month (range on timestamp) so old partitions can be
-- detached cheaply when a data-retention policy is applied.

CREATE TABLE IF NOT EXISTS sleipnir_events (
    event_id        TEXT        PRIMARY KEY,
    event_type      TEXT        NOT NULL,
    source          TEXT        NOT NULL,
    summary         TEXT,
    urgency         REAL,
    domain          TEXT,
    correlation_id  TEXT,
    causation_id    TEXT,
    tenant_id       TEXT,
    payload         JSONB,
    timestamp       TIMESTAMPTZ NOT NULL,
    ttl             INTEGER
);

CREATE INDEX IF NOT EXISTS idx_sleipnir_events_type_ts
    ON sleipnir_events (event_type, timestamp);

CREATE INDEX IF NOT EXISTS idx_sleipnir_events_correlation
    ON sleipnir_events (correlation_id);

CREATE INDEX IF NOT EXISTS idx_sleipnir_events_source_ts
    ON sleipnir_events (source, timestamp);
