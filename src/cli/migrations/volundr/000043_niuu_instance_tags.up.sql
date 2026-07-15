-- Add freeform tags to registered instances for label-based targeting.

ALTER TABLE niuu_instances
    ADD COLUMN IF NOT EXISTS tags JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE INDEX IF NOT EXISTS idx_niuu_instances_tags
    ON niuu_instances USING GIN(tags);
