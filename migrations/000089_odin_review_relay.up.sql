-- What Forge's ODIN review relay already announced to each owner, shared by every
-- replica. The watermark splits the questions that were already waiting when the
-- relay first followed the queue (announced by one digest, never pushed one by one)
-- from the ones asked after it.
CREATE TABLE IF NOT EXISTS odin_review_relay_owners (
    owner_id TEXT PRIMARY KEY,
    watermark_at TIMESTAMPTZ NOT NULL,
    digest_recorded_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per review item the owner has a notification for; retired_at is set once
-- the item left the pending state and the notification was marked read.
CREATE TABLE IF NOT EXISTS odin_review_relay_items (
    owner_id TEXT NOT NULL,
    review_id TEXT NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    retired_at TIMESTAMPTZ,
    PRIMARY KEY (owner_id, review_id)
);
CREATE INDEX IF NOT EXISTS idx_odin_review_relay_items_open
    ON odin_review_relay_items (owner_id)
    WHERE retired_at IS NULL;
