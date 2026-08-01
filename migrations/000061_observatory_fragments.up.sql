-- Push inbox for topology fragments.
--
-- A source that cannot be reached — a resident on a bare-metal Spark, a Docker
-- container behind NAT — publishes its own partial view here on a heartbeat.
-- Keyed on the source, so a heartbeat is an idempotent "this is my current
-- state" and aggregation never needs dedupe logic.
CREATE TABLE IF NOT EXISTS observatory_fragments (
    source_id TEXT PRIMARY KEY,
    source_kind TEXT NOT NULL DEFAULT '',
    source_name TEXT NOT NULL DEFAULT '',
    realm_id TEXT NOT NULL DEFAULT '',
    cluster_id TEXT NOT NULL DEFAULT '',
    host_id TEXT NOT NULL DEFAULT '',
    revision TEXT NOT NULL DEFAULT '',
    payload JSONB NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Staleness is read on every aggregation: a source past its TTL is reported
-- as stale with a last-seen time rather than vanishing from the graph.
CREATE INDEX IF NOT EXISTS observatory_fragments_received_at_idx
    ON observatory_fragments (received_at DESC);
