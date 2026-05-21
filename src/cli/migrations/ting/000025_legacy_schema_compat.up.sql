-- Forward-compatibility migration for existing legacy Ting databases.
-- Fresh installs already create run-era tables; this heals older or partially
-- migrated databases before later DDL expects run-era names everywhere.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'integration_connections'
    ) THEN
        UPDATE integration_connections
        SET adapter = 'ting' || substr(adapter, 4)
        WHERE adapter LIKE concat(chr(116), chr(121), chr(114), '.%');
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'raids'
    ) THEN
        IF NOT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'runs'
        ) THEN
            ALTER TABLE raids RENAME TO runs;
        ELSE
            ALTER TABLE runs ADD COLUMN IF NOT EXISTS pr_url TEXT;
            ALTER TABLE runs ADD COLUMN IF NOT EXISTS pr_id TEXT;
            ALTER TABLE runs ADD COLUMN IF NOT EXISTS reason TEXT;
            ALTER TABLE runs ADD COLUMN IF NOT EXISTS description TEXT NOT NULL DEFAULT '';
            ALTER TABLE runs ADD COLUMN IF NOT EXISTS acceptance_criteria TEXT[] NOT NULL DEFAULT '{}';
            ALTER TABLE runs ADD COLUMN IF NOT EXISTS reviewer_session_id TEXT;
            ALTER TABLE runs ADD COLUMN IF NOT EXISTS review_round INTEGER NOT NULL DEFAULT 0;
            ALTER TABLE runs ADD COLUMN IF NOT EXISTS identifier TEXT NOT NULL DEFAULT '';
            ALTER TABLE runs ADD COLUMN IF NOT EXISTS url TEXT NOT NULL DEFAULT '';
            ALTER TABLE runs ADD COLUMN IF NOT EXISTS depends_on TEXT[] DEFAULT '{}';
            ALTER TABLE runs ADD COLUMN IF NOT EXISTS structured_outcome JSONB;
            ALTER TABLE runs ADD COLUMN IF NOT EXISTS outcome_event_type TEXT;

            INSERT INTO runs (
                id,
                phase_id,
                tracker_id,
                name,
                declared_files,
                estimate_hours,
                status,
                confidence,
                session_id,
                branch,
                chronicle_summary,
                retry_count,
                created_at,
                updated_at,
                pr_url,
                pr_id,
                reason,
                description,
                acceptance_criteria,
                reviewer_session_id,
                review_round,
                identifier,
                url,
                depends_on,
                structured_outcome,
                outcome_event_type
            )
            SELECT
                id,
                phase_id,
                tracker_id,
                name,
                declared_files,
                estimate_hours,
                status,
                confidence,
                session_id,
                branch,
                chronicle_summary,
                retry_count,
                created_at,
                updated_at,
                pr_url,
                pr_id,
                reason,
                description,
                acceptance_criteria,
                reviewer_session_id,
                review_round,
                identifier,
                url,
                depends_on,
                structured_outcome,
                outcome_event_type
            FROM raids
            ON CONFLICT (id) DO NOTHING;

            DROP TABLE raids;
        END IF;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'confidence_events' AND column_name = 'raid_id'
    ) THEN
        IF NOT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'confidence_events' AND column_name = 'run_id'
        ) THEN
            ALTER TABLE confidence_events RENAME COLUMN raid_id TO run_id;
        ELSE
            UPDATE confidence_events SET run_id = COALESCE(run_id, raid_id) WHERE raid_id IS NOT NULL;
            ALTER TABLE confidence_events DROP COLUMN raid_id;
        END IF;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'session_messages' AND column_name = 'raid_id'
    ) THEN
        IF NOT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'session_messages' AND column_name = 'run_id'
        ) THEN
            ALTER TABLE session_messages RENAME COLUMN raid_id TO run_id;
        ELSE
            UPDATE session_messages SET run_id = COALESCE(run_id, raid_id) WHERE raid_id IS NOT NULL;
            ALTER TABLE session_messages DROP COLUMN raid_id;
        END IF;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'raid_progress'
    ) THEN
        IF NOT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'run_progress'
        ) THEN
            ALTER TABLE raid_progress RENAME TO run_progress;
        ELSE
            ALTER TABLE run_progress ADD COLUMN IF NOT EXISTS reviewer_session_id TEXT;
            ALTER TABLE run_progress ADD COLUMN IF NOT EXISTS review_round INTEGER NOT NULL DEFAULT 0;
            ALTER TABLE run_progress ADD COLUMN IF NOT EXISTS depends_on TEXT[] DEFAULT '{}';

            INSERT INTO run_progress (
                tracker_id,
                run_id,
                owner_id,
                phase_tracker_id,
                saga_tracker_id,
                status,
                session_id,
                confidence,
                pr_url,
                pr_id,
                retry_count,
                reason,
                chronicle_summary,
                updated_at,
                reviewer_session_id,
                review_round,
                depends_on
            )
            SELECT
                tracker_id,
                raid_id,
                owner_id,
                phase_tracker_id,
                saga_tracker_id,
                status,
                session_id,
                confidence,
                pr_url,
                pr_id,
                retry_count,
                reason,
                chronicle_summary,
                updated_at,
                reviewer_session_id,
                review_round,
                depends_on
            FROM raid_progress
            ON CONFLICT (tracker_id) DO NOTHING;

            DROP TABLE raid_progress;
        END IF;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'run_progress' AND column_name = 'raid_id'
    ) THEN
        IF NOT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'run_progress' AND column_name = 'run_id'
        ) THEN
            ALTER TABLE run_progress RENAME COLUMN raid_id TO run_id;
        ELSE
            UPDATE run_progress SET run_id = COALESCE(run_id, raid_id) WHERE raid_id IS NOT NULL;
            ALTER TABLE run_progress DROP COLUMN raid_id;
        END IF;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'raid_confidence_events'
    ) THEN
        IF NOT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'run_confidence_events'
        ) THEN
            ALTER TABLE raid_confidence_events RENAME TO run_confidence_events;
        ELSE
            INSERT INTO run_confidence_events (
                id,
                run_id,
                tracker_id,
                event_type,
                delta,
                score_after,
                created_at
            )
            SELECT
                id,
                raid_id,
                tracker_id,
                event_type,
                delta,
                score_after,
                created_at
            FROM raid_confidence_events
            ON CONFLICT (id) DO NOTHING;

            DROP TABLE raid_confidence_events;
        END IF;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'run_confidence_events' AND column_name = 'raid_id'
    ) THEN
        IF NOT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'run_confidence_events' AND column_name = 'run_id'
        ) THEN
            ALTER TABLE run_confidence_events RENAME COLUMN raid_id TO run_id;
        ELSE
            UPDATE run_confidence_events SET run_id = COALESCE(run_id, raid_id) WHERE raid_id IS NOT NULL;
            ALTER TABLE run_confidence_events DROP COLUMN raid_id;
        END IF;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'raid_session_messages'
    ) THEN
        IF NOT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'run_session_messages'
        ) THEN
            ALTER TABLE raid_session_messages RENAME TO run_session_messages;
        ELSE
            INSERT INTO run_session_messages (
                id,
                run_id,
                tracker_id,
                session_id,
                content,
                sender,
                created_at
            )
            SELECT
                id,
                raid_id,
                tracker_id,
                session_id,
                content,
                sender,
                created_at
            FROM raid_session_messages
            ON CONFLICT (id) DO NOTHING;

            DROP TABLE raid_session_messages;
        END IF;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'run_session_messages' AND column_name = 'raid_id'
    ) THEN
        IF NOT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'run_session_messages' AND column_name = 'run_id'
        ) THEN
            ALTER TABLE run_session_messages RENAME COLUMN raid_id TO run_id;
        ELSE
            UPDATE run_session_messages SET run_id = COALESCE(run_id, raid_id) WHERE raid_id IS NOT NULL;
            ALTER TABLE run_session_messages DROP COLUMN raid_id;
        END IF;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'dispatcher_state' AND column_name = 'max_concurrent_raids'
    ) THEN
        IF NOT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'dispatcher_state' AND column_name = 'max_concurrent_runs'
        ) THEN
            ALTER TABLE dispatcher_state RENAME COLUMN max_concurrent_raids TO max_concurrent_runs;
        ELSE
            UPDATE dispatcher_state SET max_concurrent_runs = max_concurrent_raids;
            ALTER TABLE dispatcher_state DROP COLUMN max_concurrent_raids;
        END IF;
    END IF;
END $$;

DO $$
DECLARE
    old_title text := concat(chr(84), chr(121), chr(114));
    old_lower text := lower(old_title);
    old_run_title text := concat(chr(82), chr(97), chr(105), chr(100));
    old_run_lower text := lower(old_run_title);
    old_executor_name text := old_run_lower || '-executor';
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'ravn_personas'
    ) THEN
        UPDATE ravn_personas
        SET
            name = replace(name, old_executor_name, 'run-executor'),
            config_json = CASE
                WHEN config_json IS NULL THEN NULL
                ELSE replace(
                    replace(
                        replace(
                            replace(config_json::text, old_title, 'Ting'),
                            old_lower,
                            'ting'
                        ),
                        old_run_title,
                        'Run'
                    ),
                    old_run_lower,
                    'run'
                )::jsonb
            END,
            runtime_config_json = CASE
                WHEN runtime_config_json IS NULL THEN NULL
                ELSE replace(
                    replace(
                        replace(
                            replace(runtime_config_json::text, old_title, 'Ting'),
                            old_lower,
                            'ting'
                        ),
                        old_run_title,
                        'Run'
                    ),
                    old_run_lower,
                    'run'
                )::jsonb
            END
        WHERE
            name = old_executor_name
            OR config_json::text ILIKE '%' || old_lower || '%'
            OR config_json::text ILIKE '%' || old_run_lower || '%'
            OR runtime_config_json::text ILIKE '%' || old_lower || '%'
            OR runtime_config_json::text ILIKE '%' || old_run_lower || '%';
    END IF;
END $$;

DO $$
DECLARE
    old_title text := concat(chr(84), chr(121), chr(114));
    old_lower text := lower(old_title);
    old_run_title text := concat(chr(82), chr(97), chr(105), chr(100));
    old_run_lower text := lower(old_run_title);
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'workflows'
    ) THEN
        UPDATE workflows
        SET
            name = replace(replace(replace(replace(name, old_title, 'Ting'), old_lower, 'ting'), old_run_title, 'Run'), old_run_lower, 'run'),
            description = replace(replace(replace(replace(description, old_title, 'Ting'), old_lower, 'ting'), old_run_title, 'Run'), old_run_lower, 'run'),
            definition_yaml = replace(replace(replace(replace(definition_yaml, old_title, 'Ting'), old_lower, 'ting'), old_run_title, 'Run'), old_run_lower, 'run'),
            graph_json = replace(
                replace(
                    replace(
                        replace(graph_json::text, old_title, 'Ting'),
                        old_lower,
                        'ting'
                    ),
                    old_run_title,
                    'Run'
                ),
                old_run_lower,
                'run'
            )::jsonb
        WHERE
            name ILIKE '%' || old_lower || '%'
            OR description ILIKE '%' || old_lower || '%'
            OR definition_yaml ILIKE '%' || old_lower || '%'
            OR graph_json::text ILIKE '%' || old_lower || '%'
            OR name ILIKE '%' || old_run_lower || '%'
            OR description ILIKE '%' || old_run_lower || '%'
            OR definition_yaml ILIKE '%' || old_run_lower || '%'
            OR graph_json::text ILIKE '%' || old_run_lower || '%';
    END IF;
END $$;

DO $$
DECLARE
    old_title text := concat(chr(84), chr(121), chr(114));
    old_lower text := lower(old_title);
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'chronicles'
    ) THEN
        UPDATE chronicles
        SET
            branch = replace(replace(branch, old_title, 'Ting'), old_lower, 'ting'),
            config_snapshot = CASE
                WHEN config_snapshot IS NULL THEN NULL
                ELSE replace(
                    replace(config_snapshot::text, old_title, 'Ting'),
                    old_lower,
                    'ting'
                )::jsonb
            END
        WHERE
            branch ILIKE '%' || old_lower || '%'
            OR config_snapshot::text ILIKE '%' || old_lower || '%';
    END IF;
END $$;

ALTER INDEX IF EXISTS raids_pkey RENAME TO runs_pkey;
ALTER INDEX IF EXISTS idx_raids_phase_id RENAME TO idx_runs_phase_id;
ALTER INDEX IF EXISTS idx_raids_status RENAME TO idx_runs_status;
ALTER INDEX IF EXISTS idx_raids_session_id RENAME TO idx_runs_session_id;
ALTER INDEX IF EXISTS idx_raids_reviewer_session RENAME TO idx_runs_reviewer_session;
ALTER INDEX IF EXISTS idx_confidence_events_raid_id RENAME TO idx_confidence_events_run_id;
ALTER INDEX IF EXISTS idx_session_messages_raid_id RENAME TO idx_session_messages_run_id;
ALTER INDEX IF EXISTS raid_progress_pkey RENAME TO run_progress_pkey;
ALTER INDEX IF EXISTS idx_raid_progress_status RENAME TO idx_run_progress_status;
ALTER INDEX IF EXISTS idx_raid_progress_session RENAME TO idx_run_progress_session;
ALTER INDEX IF EXISTS idx_raid_progress_raid_id RENAME TO idx_run_progress_run_id;
ALTER INDEX IF EXISTS idx_raid_progress_phase RENAME TO idx_run_progress_phase;
ALTER INDEX IF EXISTS raid_confidence_events_pkey RENAME TO run_confidence_events_pkey;
ALTER INDEX IF EXISTS idx_raid_confidence_events_tracker RENAME TO idx_run_confidence_events_tracker;
ALTER INDEX IF EXISTS raid_session_messages_pkey RENAME TO run_session_messages_pkey;
ALTER INDEX IF EXISTS idx_raid_session_messages_tracker RENAME TO idx_run_session_messages_tracker;
ALTER INDEX IF EXISTS idx_raid_session_messages_session RENAME TO idx_run_session_messages_session;
