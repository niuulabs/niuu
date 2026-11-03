-- Durable presented files (F2) kept by PostgresPresentedFileStore: one row per copy.
-- The bytes live on a volume every Forge replica mounts; the row is the record every
-- replica agrees on, and the quota (presented_files.max_total_bytes) is their sum.
CREATE TABLE IF NOT EXISTS forge_presented_files (
    session_id UUID NOT NULL,
    file_id TEXT NOT NULL,
    name TEXT NOT NULL,
    mime TEXT NOT NULL,
    size BIGINT NOT NULL CHECK (size >= 0),
    sha256 TEXT NOT NULL,
    stored_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (session_id, file_id)
);
