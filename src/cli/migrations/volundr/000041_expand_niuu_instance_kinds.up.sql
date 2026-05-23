ALTER TABLE niuu_instances
    DROP CONSTRAINT IF EXISTS niuu_instances_kind_check;

ALTER TABLE niuu_instances
    ADD CONSTRAINT niuu_instances_kind_check
    CHECK (kind IN ('volundr', 'ting', 'mimir', 'bifrost', 'ravn', 'observatory', 'generic'));
