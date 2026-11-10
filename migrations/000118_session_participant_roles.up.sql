-- Session participant roles use the same words as the room roles that enforce them:
-- `viewer` and `approver`. `observer` becomes `viewer`. `teacher` and `debugger`
-- are removed: they never had permissions of their own and carried exactly the
-- `observer` grant, so they become `viewer` too and nobody's access changes.
ALTER TABLE session_participants DROP CONSTRAINT IF EXISTS session_participants_role_check;

UPDATE session_participants
SET role = 'viewer', updated_at = NOW()
WHERE role IN ('observer', 'teacher', 'debugger');

ALTER TABLE session_participants
    ADD CONSTRAINT session_participants_role_check
    CHECK (role IN ('viewer', 'approver'));
