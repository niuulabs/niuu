-- Code delivery's own extension of the generic workflow execution ledger:
-- repository/base identity, the merge receipt, and the integration
-- candidate/review trail. A delivery child's own repository/base/workspace
-- fields have no columns of their own; they live in the generic child's
-- `input` JSON (see ting.delivery.domain.validate_expansion).

CREATE TABLE IF NOT EXISTS delivery_executions (
    execution_id UUID PRIMARY KEY REFERENCES workflow_executions(id) ON DELETE CASCADE,
    repository TEXT NOT NULL,
    base_ref TEXT NOT NULL,
    base_sha TEXT NOT NULL,
    workflow_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    merge_receipt JSONB,
    integration_receipts JSONB NOT NULL DEFAULT '[]'::jsonb,
    integration_allocation JSONB,
    integration_candidate JSONB,
    integration_review_receipt JSONB,
    integration_review_event_id TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
