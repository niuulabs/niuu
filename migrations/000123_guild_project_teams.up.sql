-- The team (members, team integrations, team routes) of each project a Guild's Forges
-- share, as its home Forge last published it. The Guild hands it to every other Forge
-- that has the project. No secret is in it: a connection names its credential.
CREATE TABLE IF NOT EXISTS guild_project_teams (
    project_id       UUID PRIMARY KEY,
    home_instance_id TEXT NOT NULL,
    version          BIGINT NOT NULL,
    team             JSONB NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
