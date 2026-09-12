-- Preserve recorded tenant attribution on rollback; older code may ignore it.
-- The relaxed constraint is additive and also accepts the old user-row shape.
SELECT 1;
