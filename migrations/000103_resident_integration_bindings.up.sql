-- Which of the owner's credentials each integration a resident's profile declares
-- (deployment.credentials: [{integration: <slug>}]) was bound to, {slug: credential}.
-- Resolved at create, while the owner's request bearer can read their connections,
-- so the background deploy and the reconciler render the resident without one.
ALTER TABLE resident_runtimes
    ADD COLUMN IF NOT EXISTS integration_bindings JSONB NOT NULL DEFAULT '{}'::jsonb;
