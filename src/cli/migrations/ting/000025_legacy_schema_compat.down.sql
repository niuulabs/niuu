-- Best-effort rollback for the legacy Ting schema compatibility rename.

ALTER INDEX IF EXISTS idx_run_session_messages_session RENAME TO idx_raid_session_messages_session;
ALTER INDEX IF EXISTS idx_run_session_messages_tracker RENAME TO idx_raid_session_messages_tracker;
ALTER INDEX IF EXISTS idx_run_confidence_events_tracker RENAME TO idx_raid_confidence_events_tracker;
ALTER INDEX IF EXISTS idx_run_progress_phase RENAME TO idx_raid_progress_phase;
ALTER INDEX IF EXISTS idx_run_progress_run_id RENAME TO idx_raid_progress_raid_id;
ALTER INDEX IF EXISTS idx_run_progress_session RENAME TO idx_raid_progress_session;
ALTER INDEX IF EXISTS idx_run_progress_status RENAME TO idx_raid_progress_status;
ALTER INDEX IF EXISTS idx_session_messages_run_id RENAME TO idx_session_messages_raid_id;
ALTER INDEX IF EXISTS idx_confidence_events_run_id RENAME TO idx_confidence_events_raid_id;
ALTER INDEX IF EXISTS idx_runs_status RENAME TO idx_raids_status;
ALTER INDEX IF EXISTS idx_runs_phase_id RENAME TO idx_raids_phase_id;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'dispatcher_state' AND column_name = 'max_concurrent_runs'
    ) AND NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'dispatcher_state' AND column_name = 'max_concurrent_raids'
    ) THEN
        ALTER TABLE dispatcher_state RENAME COLUMN max_concurrent_runs TO max_concurrent_raids;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'run_session_messages' AND column_name = 'run_id'
    ) AND NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'run_session_messages' AND column_name = 'raid_id'
    ) THEN
        ALTER TABLE run_session_messages RENAME COLUMN run_id TO raid_id;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'run_session_messages'
    ) AND NOT EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'raid_session_messages'
    ) THEN
        ALTER TABLE run_session_messages RENAME TO raid_session_messages;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'run_confidence_events' AND column_name = 'run_id'
    ) AND NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'run_confidence_events' AND column_name = 'raid_id'
    ) THEN
        ALTER TABLE run_confidence_events RENAME COLUMN run_id TO raid_id;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'run_confidence_events'
    ) AND NOT EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'raid_confidence_events'
    ) THEN
        ALTER TABLE run_confidence_events RENAME TO raid_confidence_events;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'run_progress' AND column_name = 'run_id'
    ) AND NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'run_progress' AND column_name = 'raid_id'
    ) THEN
        ALTER TABLE run_progress RENAME COLUMN run_id TO raid_id;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'run_progress'
    ) AND NOT EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'raid_progress'
    ) THEN
        ALTER TABLE run_progress RENAME TO raid_progress;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'session_messages' AND column_name = 'run_id'
    ) AND NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'session_messages' AND column_name = 'raid_id'
    ) THEN
        ALTER TABLE session_messages RENAME COLUMN run_id TO raid_id;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'confidence_events' AND column_name = 'run_id'
    ) AND NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'confidence_events' AND column_name = 'raid_id'
    ) THEN
        ALTER TABLE confidence_events RENAME COLUMN run_id TO raid_id;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'runs'
    ) AND NOT EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'raids'
    ) THEN
        ALTER TABLE runs RENAME TO raids;
    END IF;
END $$;
