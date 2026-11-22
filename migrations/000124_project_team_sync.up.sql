-- How a project's team on this Forge relates to the Guild's: the version this Forge made
-- (at the project's home) or copied (elsewhere), whether the home still has to publish it,
-- and why the last exchange with the Guild failed.
CREATE TABLE IF NOT EXISTS project_team_sync (
    project_id UUID PRIMARY KEY REFERENCES forge_projects(id) ON DELETE CASCADE,
    version    BIGINT NOT NULL DEFAULT 0,
    pending    BOOLEAN NOT NULL DEFAULT FALSE,
    synced_at  TIMESTAMPTZ,
    error      TEXT NOT NULL DEFAULT ''
);
