-- Irreversible by design: the up migration cleared references to workflows the
-- package no longer ships, and which rows named them is not recorded.
-- Rolling back the schema leaves those rows without a workflow.
SELECT 1;
