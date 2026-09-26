-- Durable storage for the Ravn API's /triggers routes (POST /ravn/triggers
-- was returning 503 because no store was wired). trigger_store is a dynamic
-- adapter (trigger_store.adapter); only used when it is set to
-- ravn.adapters.trigger_store.postgres_store.LazyPostgresTriggerStore — the
-- default (ravn.adapters.trigger_store.FileTriggerStore) is a local file.
-- Scoped by owner_id/tenant_id so one tenant cannot list or delete another
-- tenant's triggers. repo scopes an event-kind trigger to one repository's
-- events (ravn.adapters.triggers.api_source filters on it before
-- enqueueing) so a QA resident does not fire on every tenant's pull
-- requests; empty for cron-kind triggers, which have no event payload.
CREATE TABLE IF NOT EXISTS ravn_triggers (
    id           UUID        PRIMARY KEY,
    kind         TEXT        NOT NULL,
    persona_name TEXT        NOT NULL,
    spec         TEXT        NOT NULL,
    repo         TEXT        NOT NULL DEFAULT '',
    enabled      BOOLEAN     NOT NULL DEFAULT TRUE,
    owner_id     TEXT        NOT NULL,
    tenant_id    TEXT        NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ravn_triggers_tenant_created
    ON ravn_triggers (tenant_id, created_at DESC);
