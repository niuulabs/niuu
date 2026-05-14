DO $$
DECLARE
    old_title text := concat(chr(84), chr(121), chr(114));
    old_lower text := lower(old_title);
    old_accented text := convert_from(decode('54c3bd72', 'hex'), 'UTF8');
    old_run_title text := concat(chr(82), chr(97), chr(105), chr(100));
    old_run_lower text := lower(old_run_title);
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'chronicles'
    ) THEN
        UPDATE chronicles
        SET
            branch = replace(replace(replace(replace(replace(branch, old_accented, 'Ting'), old_title, 'Ting'), old_lower, 'ting'), old_run_title, 'Run'), old_run_lower, 'run'),
            summary = CASE
                WHEN summary IS NULL THEN NULL
                ELSE replace(replace(replace(replace(replace(summary, old_accented, 'Ting'), old_title, 'Ting'), old_lower, 'ting'), old_run_title, 'Run'), old_run_lower, 'run')
            END,
            unfinished_work = CASE
                WHEN unfinished_work IS NULL THEN NULL
                ELSE replace(replace(replace(replace(replace(unfinished_work, old_accented, 'Ting'), old_title, 'Ting'), old_lower, 'ting'), old_run_title, 'Run'), old_run_lower, 'run')
            END,
            key_changes = CASE
                WHEN key_changes IS NULL THEN NULL
                ELSE replace(replace(replace(replace(replace(key_changes::text, old_accented, 'Ting'), old_title, 'Ting'), old_lower, 'ting'), old_run_title, 'Run'), old_run_lower, 'run')::jsonb
            END,
            config_snapshot = CASE
                WHEN config_snapshot IS NULL THEN NULL
                ELSE replace(replace(replace(replace(replace(config_snapshot::text, old_accented, 'Ting'), old_title, 'Ting'), old_lower, 'ting'), old_run_title, 'Run'), old_run_lower, 'run')::jsonb
            END
        WHERE
            branch LIKE '%' || old_accented || '%'
            OR branch ILIKE '%' || old_lower || '%'
            OR coalesce(summary, '') LIKE '%' || old_accented || '%'
            OR coalesce(summary, '') ILIKE '%' || old_lower || '%'
            OR coalesce(unfinished_work, '') LIKE '%' || old_accented || '%'
            OR coalesce(unfinished_work, '') ILIKE '%' || old_lower || '%'
            OR coalesce(key_changes::text, '') LIKE '%' || old_accented || '%'
            OR coalesce(key_changes::text, '') ILIKE '%' || old_lower || '%'
            OR config_snapshot::text LIKE '%' || old_accented || '%'
            OR config_snapshot::text ILIKE '%' || old_lower || '%'
            OR branch ILIKE '%' || old_run_lower || '%'
            OR coalesce(summary, '') ILIKE '%' || old_run_lower || '%'
            OR coalesce(unfinished_work, '') ILIKE '%' || old_run_lower || '%'
            OR coalesce(key_changes::text, '') ILIKE '%' || old_run_lower || '%'
            OR config_snapshot::text ILIKE '%' || old_run_lower || '%';
    END IF;
END $$;

DO $$
DECLARE
    old_title text := concat(chr(84), chr(121), chr(114));
    old_lower text := lower(old_title);
    old_rune text := convert_from(decode('e19b83', 'hex'), 'UTF8');
    old_accented text := convert_from(decode('54c3bd72', 'hex'), 'UTF8');
    old_run_title text := concat(chr(82), chr(97), chr(105), chr(100));
    old_run_lower text := lower(old_run_title);
    old_pending_runs text := 'pending' || old_run_title || 's';
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'observatory_registries'
    ) THEN
        UPDATE observatory_registries
        SET
            version = GREATEST(version, 8),
            updated_at = now(),
            payload = jsonb_set(
                jsonb_set(
                    replace(
                        replace(
                            replace(
                                replace(
                                    replace(
                                        replace(
                                            replace(payload::text, old_rune, '✦'),
                                            old_accented,
                                            'Ting'
                                        ),
                                        old_title,
                                        'Ting'
                                    ),
                                    old_lower,
                                    'ting'
                                ),
                                old_pending_runs,
                                'pendingRuns'
                            ),
                            old_run_title,
                            'Run'
                        ),
                        old_run_lower,
                        'run'
                    )::jsonb,
                    '{version}',
                    to_jsonb(8),
                    true
                ),
                '{updatedAt}',
                to_jsonb(to_char(timezone('UTC', now()), 'YYYY-MM-DD"T"HH24:MI:SS"Z"')),
                true
            )
        WHERE
            payload::text LIKE '%' || old_accented || '%'
            OR payload::text ILIKE '%' || old_lower || '%'
            OR payload::text ILIKE '%' || old_run_lower || '%'
            OR payload::text LIKE '%' || old_rune || '%';
    END IF;
END $$;

DO $$
DECLARE
    old_title text := concat(chr(84), chr(121), chr(114));
    old_lower text := lower(old_title);
    old_accented text := convert_from(decode('54c3bd72', 'hex'), 'UTF8');
    old_run_title text := concat(chr(82), chr(97), chr(105), chr(100));
    old_run_lower text := lower(old_run_title);
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'sagas'
    ) THEN
        UPDATE sagas
        SET workflow_snapshot = replace(
            replace(
                replace(
                    replace(
                        replace(workflow_snapshot::text, old_accented, 'Ting'),
                        old_title,
                        'Ting'
                    ),
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
            workflow_snapshot::text LIKE '%' || old_accented || '%'
            OR workflow_snapshot::text ILIKE '%' || old_lower || '%'
            OR workflow_snapshot::text ILIKE '%' || old_run_lower || '%';
    END IF;
END $$;

DO $$
DECLARE
    old_title text := concat(chr(84), chr(121), chr(114));
    old_lower text := lower(old_title);
    old_accented text := convert_from(decode('54c3bd72', 'hex'), 'UTF8');
    old_run_title text := concat(chr(82), chr(97), chr(105), chr(100));
    old_run_lower text := lower(old_run_title);
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'chronicle_events'
    ) THEN
        UPDATE chronicle_events
        SET label = replace(
            replace(
                replace(
                    replace(
                        replace(label, old_accented, 'Ting'),
                        old_title,
                        'Ting'
                    ),
                    old_lower,
                    'ting'
                ),
                old_run_title,
                'Run'
            ),
            old_run_lower,
            'run'
        )
        WHERE
            label LIKE '%' || old_accented || '%'
            OR label ILIKE '%' || old_lower || '%'
            OR label ILIKE '%' || old_run_lower || '%';
    END IF;
END $$;
