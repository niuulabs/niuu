-- Intentionally a no-op. 000047 replaced a recorded slug that no campaign's
-- session ever wrote under with the one it did; restoring the old value would
-- only make those campaigns' artifacts unreadable again.
SELECT 1;
