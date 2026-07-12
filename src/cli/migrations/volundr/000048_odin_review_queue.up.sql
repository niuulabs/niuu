-- Central ODIN review queue: every decision awaiting (or carrying) an
-- operator verdict, with the full ReviewItem envelope as JSONB.
CREATE TABLE IF NOT EXISTS odin_review_items (
    item_id        TEXT PRIMARY KEY,
    kind           TEXT NOT NULL,
    status         TEXT NOT NULL,
    environment_id TEXT NOT NULL DEFAULT '',
    valkyrie_id    TEXT NOT NULL DEFAULT '',
    requested_at   TIMESTAMPTZ NOT NULL,
    payload        JSONB NOT NULL,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_odin_review_items_status
    ON odin_review_items(status, requested_at DESC);

CREATE INDEX IF NOT EXISTS idx_odin_review_items_environment
    ON odin_review_items(environment_id, status);
