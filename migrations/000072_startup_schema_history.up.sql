-- Standalone bootstrap uses this same table before applying the numbered set.
CREATE TABLE IF NOT EXISTS volundr_schema_history (
    filename TEXT PRIMARY KEY,
    sha256 CHAR(64) NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
