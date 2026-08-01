CREATE TABLE IF NOT EXISTS a2a_push_notification_configs (
    task_id TEXT NOT NULL,
    config_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    config_data BYTEA NOT NULL,
    pending_event JSONB,
    delivery_version TEXT,
    next_attempt_at TIMESTAMPTZ,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    delivered_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (task_id, config_id, owner_id),
    CONSTRAINT a2a_push_notification_task_fk
        FOREIGN KEY (task_id) REFERENCES workflow_campaigns(slug) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_a2a_push_notification_due
    ON a2a_push_notification_configs (next_attempt_at)
    WHERE pending_event IS NOT NULL;
