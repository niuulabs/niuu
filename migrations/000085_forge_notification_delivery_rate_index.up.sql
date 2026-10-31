-- Per-rule rate limiting counts a rule's recent successful deliveries. A partial
-- index keeps that count proportional to the window, not the rule's history.
CREATE INDEX IF NOT EXISTS idx_forge_notification_deliveries_rule_delivered
    ON forge_notification_deliveries (rule_id, delivered_at)
    WHERE status = 'delivered';
