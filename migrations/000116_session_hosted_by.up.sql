-- A workflow run is one unit of compute (a pod, VM, VM slot, sandbox or process group). Its
-- member sessions have no runtime of their own: each is hosted by the unit-root session.
-- hosted_by is that root; hosted_port is the port of the member's broker inside the unit,
-- unique within one unit. A member stays its own session (transcript, log, credential).
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS hosted_by UUID REFERENCES sessions(id) ON DELETE RESTRICT;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS hosted_port INTEGER;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'sessions_hosted_pair_check'
    ) THEN
        ALTER TABLE sessions ADD CONSTRAINT sessions_hosted_pair_check
            CHECK ((hosted_by IS NULL) = (hosted_port IS NULL)
                   AND (hosted_port IS NULL OR hosted_port BETWEEN 1 AND 65535)
                   AND (hosted_by IS NULL OR hosted_by <> id));
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_hosted_port
    ON sessions (hosted_by, hosted_port) WHERE hosted_by IS NOT NULL;
