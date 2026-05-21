ALTER TABLE workflows
    ADD COLUMN IF NOT EXISTS definition_yaml TEXT;
