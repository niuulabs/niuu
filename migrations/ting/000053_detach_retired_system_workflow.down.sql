-- Irreversible by design: the up migration cleared references to a workflow
-- the package no longer ships, and which rows named it is not recorded.
-- Rolling back the schema leaves those rows without a workflow.
SELECT 1;
