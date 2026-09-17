-- Timestamp (UTC) of when a session's CURRENT turn started (the user's prompt
-- landing). Stable across intra-turn active/tool_executing flips (unlike
-- activity_state_since, which re-stamps on coarse-bucket changes). NULL when no
-- turn is in flight, or when an older broker doesn't report it. Clients anchor
-- the RUNNING elapsed to this, falling back to activity_state_since when NULL.
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS turn_started_at TIMESTAMPTZ;
