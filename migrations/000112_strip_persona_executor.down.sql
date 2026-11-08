-- The up migration removed the `executor` key from stored persona documents. The harness a
-- persona named is not recorded anywhere else, so it cannot be restored, and the persona
-- model no longer has the field to read it back. This migration is deliberately a no-op.
SELECT 1;
