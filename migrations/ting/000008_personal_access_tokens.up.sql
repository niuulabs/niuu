-- Personal access tokens for Ting API callers (e.g. Telegram bot, ODIN/Móði)
-- owner_id is the IDP sub claim from Envoy headers, treated as an opaque string.
-- NOTE: Unlike Volundr's PAT table, there is no FK on owner_id because Ting does
-- not own a users table. owner_id is validated at the application layer via the
-- IDP subject claim propagated through Envoy.

CREATE TABLE IF NOT EXISTS personal_access_tokens (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id     TEXT        NOT NULL,
    name         TEXT        NOT NULL,
    token_hash   TEXT        NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at   TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_pats_owner_id ON personal_access_tokens(owner_id);
