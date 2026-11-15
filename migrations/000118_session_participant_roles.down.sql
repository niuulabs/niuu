-- Back to the 000080 role names. `viewer` becomes `observer`; which rows were
-- `teacher` or `debugger` before the up migration is not recorded, and they had
-- the same access as `observer` either way.
ALTER TABLE session_participants DROP CONSTRAINT IF EXISTS session_participants_role_check;

UPDATE session_participants
SET role = 'observer', updated_at = NOW()
WHERE role = 'viewer';

ALTER TABLE session_participants
    ADD CONSTRAINT session_participants_role_check
    CHECK (role IN ('observer', 'teacher', 'debugger', 'approver'));
