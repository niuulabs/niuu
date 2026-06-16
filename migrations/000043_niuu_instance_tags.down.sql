DROP INDEX IF EXISTS idx_niuu_instances_tags;

ALTER TABLE niuu_instances
    DROP COLUMN IF EXISTS tags;
