-- Persist which Volundr connection a campaign's session was launched on.
-- Read paths (status projector, A2A pending questions/gates/artifacts/cancel)
-- must target the same Volundr instance the session lives on; resolving the
-- owner's primary connection breaks for sessions launched on a non-default
-- cluster.
ALTER TABLE workflow_campaigns ADD COLUMN IF NOT EXISTS connection_id TEXT;
