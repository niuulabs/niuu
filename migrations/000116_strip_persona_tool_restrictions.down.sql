-- The up migration removed the `allowed_tools` and `forbidden_tools` keys from stored persona
-- documents. The tool lists are not recorded anywhere else, so they cannot be restored, and the
-- persona model no longer has the fields to read them back. This migration is deliberately a
-- no-op.
SELECT 1;
