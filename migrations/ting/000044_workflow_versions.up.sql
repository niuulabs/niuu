-- Preserve complete pinned workflow aggregates while the catalog row remains the head.
ALTER TABLE workflows ADD COLUMN IF NOT EXISTS version_origin TEXT NOT NULL DEFAULT 'authored';
ALTER TABLE workflows ADD COLUMN IF NOT EXISTS based_on_revision TEXT;

CREATE TABLE IF NOT EXISTS workflow_versions (
    workflow_id UUID NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    version TEXT NOT NULL,
    document_revision TEXT NOT NULL,
    snapshot JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (workflow_id, version),
    UNIQUE (workflow_id, document_revision)
);
CREATE INDEX IF NOT EXISTS idx_workflow_versions_created
    ON workflow_versions (workflow_id, created_at DESC);
