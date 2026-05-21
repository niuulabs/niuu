-- Enforce one PAT name per owner
CREATE UNIQUE INDEX IF NOT EXISTS idx_pats_owner_name ON personal_access_tokens(owner_id, name);
