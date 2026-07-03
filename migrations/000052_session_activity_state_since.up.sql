-- Timestamp (UTC) of when a session ENTERED its current activity_state. Stamped
-- by the Skuld broker only on a real state change, so clients can render an
-- accurate "active for Ns" elapsed without re-deriving it from event arrival.
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS activity_state_since TIMESTAMPTZ;
