-- Memory a Forge project owns: Mimir mounts every session and workflow run in the project
-- inherits at launch. A mount points at one entry of the Mimir registry, with the access the
-- project grants (read, or read_write for the listed page prefixes) and who added it.
-- `name` is the mount's handle inside the project: it becomes the session's mimir-<name>
-- server, so it is unique per project.
CREATE TABLE IF NOT EXISTS project_memory_mounts (
    project_id UUID NOT NULL REFERENCES forge_projects(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    registry_entry_id TEXT NOT NULL,
    access TEXT NOT NULL CHECK (access IN ('read', 'read_write')),
    write_prefixes JSONB NOT NULL DEFAULT '[]'::jsonb,
    added_by TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (project_id, name)
);
