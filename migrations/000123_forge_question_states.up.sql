-- Question state (LEX-1932). A question (a notice with question=true, or the system
-- attention item a runtime question raises) is open until a row exists here. The first
-- answer wins: the row keeps it (who answered, from which device, and the exact text sent
-- to the asking session), so a second device is refused with the first answer. A
-- question closed by its asking session (about_question resolved|withdrawn) keeps state
-- resolved/withdrawn. No FK, like the notifications themselves.
CREATE TABLE IF NOT EXISTS forge_question_states (
    notification_id UUID PRIMARY KEY,
    owner_id TEXT NOT NULL,
    tenant_id TEXT NULL,
    session_id UUID NULL,
    state VARCHAR(16) NOT NULL CHECK (state IN ('answered', 'resolved', 'withdrawn')),
    answer_request_id TEXT NULL,
    answered_device_id TEXT NULL,
    answered_device_label TEXT NULL,
    answer_content TEXT NULL,
    answers JSONB NULL,
    answered_at TIMESTAMPTZ NULL,
    closed_at TIMESTAMPTZ NULL,
    closed_by_notification_id UUID NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Every state change, in order: the Guild push gateway pages it per node (after seq)
-- and sends the owner's iOS devices a silent push for each.
CREATE TABLE IF NOT EXISTS forge_question_events (
    seq BIGSERIAL PRIMARY KEY,
    notification_id UUID NOT NULL,
    owner_id TEXT NOT NULL,
    tenant_id TEXT NULL,
    state VARCHAR(16) NOT NULL,
    answered_device_id TEXT NULL,
    answered_device_label TEXT NULL,
    at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_forge_question_events_owner_seq
    ON forge_question_events (owner_id, seq);
