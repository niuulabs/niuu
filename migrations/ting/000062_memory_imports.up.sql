-- Memory imports: a connector that brings a project's tickets into its memory as sources.
--
-- One row per import: which project's memory it feeds, which team integration of that
-- project it reads with, and which tracker project it reads. `cursor` is the upstream
-- updatedAt of the newest ticket the memory gateway has accepted; the next run reads
-- tickets updated after it, and it only moves once the gateway accepted every ticket up to
-- it. `pending_source_ids` holds the sources a run created that the compile workflow has not
-- been started for yet, so a compile that could not start is retried, not forgotten.

CREATE TABLE IF NOT EXISTS memory_imports (
    id UUID PRIMARY KEY,
    project_id UUID NOT NULL,
    tenant_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    connector TEXT NOT NULL CHECK (connector IN ('linear')),
    connection_id TEXT NOT NULL,
    tracker_project_id TEXT NOT NULL,
    compile BOOLEAN NOT NULL,
    cursor TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL,
    last_run_at TIMESTAMPTZ,
    last_error TEXT NOT NULL DEFAULT '',
    pending_source_ids TEXT[] NOT NULL DEFAULT '{}'
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_imports_source
    ON memory_imports (project_id, connector, connection_id, tracker_project_id);
