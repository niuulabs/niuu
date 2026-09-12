ALTER TABLE niuu_instances DROP CONSTRAINT IF EXISTS niuu_instances_scope_check;
ALTER TABLE niuu_instances ADD CONSTRAINT niuu_instances_scope_check CHECK (
    (visibility = 'system' AND owner_id IS NULL)
    OR (visibility = 'tenant' AND owner_id IS NULL AND tenant_id IS NOT NULL)
    OR (visibility = 'user' AND owner_id IS NOT NULL)
);
