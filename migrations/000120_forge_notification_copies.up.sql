-- A notification that asks someone to decide or act (a workflow gate, a permission
-- prompt, a question) reaches everyone whose role may answer it, not only the session's
-- owner. Each of them gets their own row (so their own read state, delivery rules and
-- feed position, with owner_id the recipient); a copy names the original in
-- metadata.copy_of. Settling any row of the group settles all of them.
--
-- Looking up a group's copies by their original, and a session's copy recipients, must not
-- scan the feed.
CREATE INDEX IF NOT EXISTS idx_forge_notifications_copy_of
    ON forge_notifications ((metadata->>'copy_of'))
    WHERE metadata->>'copy_of' IS NOT NULL;
