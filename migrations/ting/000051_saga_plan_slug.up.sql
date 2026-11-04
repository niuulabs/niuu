-- The project plan a saga was committed from, so a ticket can reach its plan.
ALTER TABLE sagas ADD COLUMN IF NOT EXISTS plan_slug TEXT;
