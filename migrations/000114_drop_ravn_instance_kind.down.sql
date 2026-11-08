-- Restores the kind constraint 000041 defined, which admits 'ravn' again. The 'ravn' rows
-- the up migration deleted, and the ravn_base_url keys it removed from other rows' config,
-- cannot be restored, and the application no longer has the kind to load them.
ALTER TABLE niuu_instances
    DROP CONSTRAINT IF EXISTS niuu_instances_kind_check;

ALTER TABLE niuu_instances
    ADD CONSTRAINT niuu_instances_kind_check
    CHECK (kind IN ('volundr', 'ting', 'mimir', 'bifrost', 'ravn', 'observatory', 'generic'));
