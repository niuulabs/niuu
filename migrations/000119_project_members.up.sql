-- People of one org on a Forge project, each with a role on the ladder
-- viewer < contributor < approver < owner. The project's creator is always
-- an `owner` member: existing projects are backfilled here, and new ones get the
-- row in the same transaction that registers them.
CREATE TABLE IF NOT EXISTS project_members (
    project_id UUID NOT NULL REFERENCES forge_projects(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('viewer', 'contributor', 'approver', 'owner')),
    expertise TEXT NOT NULL DEFAULT '',
    skills JSONB NOT NULL DEFAULT '[]'::jsonb,
    added_by TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (project_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_project_members_user ON project_members(user_id);

-- A project registered without an identity (single-user mode, owner_id '') has no
-- user to be a member; it keeps being admitted by its empty owner scope.
INSERT INTO project_members (project_id, user_id, tenant_id, role, added_by)
SELECT id, owner_id, tenant_id, 'owner', owner_id
FROM forge_projects
WHERE owner_id <> ''
ON CONFLICT (project_id, user_id) DO NOTHING;
