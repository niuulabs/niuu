-- Durable fleet-wide budget ledger for the Ravn API's /budget routes
-- (GET /ravn/budget/fleet and /ravn/budget/{ravnId} were returning 503
-- because no store was wired). budget_ledger is a dynamic adapter
-- (budget_ledger.adapter); only used when it is set to
-- ravn.adapters.budget_ledger.postgres_store.LazyPostgresBudgetLedger — the
-- default (ravn.adapters.budget_ledger.FileBudgetLedger) is a local file.
-- One row per (tenant, ravn) per
-- UTC day. tenant_id is part of the primary key, not a bare index, so a
-- ravn_id collision across tenants cannot upsert into another tenant's row.
-- ravn_id is TEXT, not UUID: standalone/discovered residents are not
-- guaranteed to carry a UUID id, and this table must never 500 on a
-- caller-supplied id the way an earlier draft's UUID trigger id did.
-- Written synchronously by the Ravn API's POST /budget/spend, called by each
-- resident's DailyBudgetTracker (ravn.drive_loop._record_task_cost) as real
-- spend happens, with ravn_id/tenant_id derived server-side from the
-- caller's workload principal — never client-supplied.
CREATE TABLE IF NOT EXISTS ravn_budget_ledger (
    tenant_id  TEXT        NOT NULL,
    ravn_id    TEXT        NOT NULL,
    day        DATE        NOT NULL,
    spent_usd  NUMERIC     NOT NULL DEFAULT 0,
    cap_usd    NUMERIC     NOT NULL DEFAULT 0,
    warn_at    DOUBLE PRECISION NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, ravn_id, day)
);

CREATE INDEX IF NOT EXISTS idx_ravn_budget_ledger_tenant_day
    ON ravn_budget_ledger (tenant_id, day);
