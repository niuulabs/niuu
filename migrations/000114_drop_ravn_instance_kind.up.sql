-- The 'ravn' instance kind was removed: Guild no longer registers, joins or probes a Ravn
-- instance. A niuu_instances row of that kind can no longer be loaded (it is not a valid
-- InstanceKind), which would make every instance listing and health pass fail, so those rows
-- go. Nothing references niuu_instances, so no dependent rows need deleting first.
DELETE FROM niuu_instances
WHERE kind = 'ravn';

-- A split-service instance carried the address of its Ravn service in config. Nothing reads
-- the key any more, and the transport policy no longer dials it.
UPDATE niuu_instances
SET config = config - 'ravn_base_url' - 'ravnBaseUrl'
WHERE config ? 'ravn_base_url'
   OR config ? 'ravnBaseUrl';

ALTER TABLE niuu_instances
    DROP CONSTRAINT IF EXISTS niuu_instances_kind_check;

ALTER TABLE niuu_instances
    ADD CONSTRAINT niuu_instances_kind_check
    CHECK (kind IN ('volundr', 'ting', 'mimir', 'bifrost', 'observatory', 'generic'));
