-- Workflow steps over the mesh: offers, pickups and who orchestrates.
--
-- A stage node is no longer activated member by member through an HTTP call. What a delivery
-- produces for a stage is one OFFER row (kind 'offer', no member), published on the run's mesh;
-- every member that takes it up reports a pickup, and each pickup is a 'stage_member' row of its
-- own, fenced on its session, that carries the offer it took up (offer_id) and when
-- (picked_up_at). A flag can sit on an offer, so an offer nobody takes up is flagged 'unclaimed'.
--
-- orchestration records who decides what is offered: the engine (Ting) or, in session mode, a
-- coordinator agent.
--
-- delivered_at goes with the delivery it recorded; the 'not_started' and 'undelivered' flags go
-- with the conditions they described (a step has no session of its own to be delivered to). Runs
-- still open at upgrade are canceled by Ting, so no live row depends on any of them.

ALTER TABLE workflow_steps
    ADD COLUMN IF NOT EXISTS offer_id TEXT,
    ADD COLUMN IF NOT EXISTS picked_up_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS orchestration TEXT NOT NULL DEFAULT 'engine';

ALTER TABLE workflow_steps
    DROP CONSTRAINT IF EXISTS workflow_steps_orchestration_check;
ALTER TABLE workflow_steps
    ADD CONSTRAINT workflow_steps_orchestration_check
    CHECK (orchestration IN ('engine', 'session'));

ALTER TABLE workflow_steps
    DROP CONSTRAINT IF EXISTS workflow_steps_kind_check;
ALTER TABLE workflow_steps
    ADD CONSTRAINT workflow_steps_kind_check
    CHECK (kind IN ('stage_member', 'offer', 'gate', 'wait', 'end'));

CREATE INDEX IF NOT EXISTS idx_workflow_steps_offer
    ON workflow_steps (run_id, offer_id)
    WHERE offer_id IS NOT NULL;

DROP INDEX IF EXISTS idx_workflow_steps_undelivered;
ALTER TABLE workflow_steps DROP COLUMN IF EXISTS delivered_at;

DELETE FROM workflow_step_flags WHERE kind IN ('not_started', 'undelivered');
ALTER TABLE workflow_step_flags
    DROP CONSTRAINT IF EXISTS workflow_step_flags_kind_check;
ALTER TABLE workflow_step_flags
    ADD CONSTRAINT workflow_step_flags_kind_check
    CHECK (kind IN ('overdue', 'unclaimed', 'unclosed', 'session_gone'));
