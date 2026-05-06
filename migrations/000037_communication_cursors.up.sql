CREATE TABLE IF NOT EXISTS communication_cursors (
    platform TEXT NOT NULL,
    consumer_key TEXT NOT NULL,
    cursor TEXT NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    PRIMARY KEY (platform, consumer_key)
);

CREATE INDEX IF NOT EXISTS idx_comm_cursors_updated_at
    ON communication_cursors(updated_at);
