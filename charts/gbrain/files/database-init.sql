-- gbrain migration v35 requires this administrator-owned event trigger.
-- Keep its behavior aligned with upstream src/core/migrate.ts.
DO $init$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE p.proname = 'auto_enable_rls' AND n.nspname = 'public'
  ) THEN
    EXECUTE $fn$
      CREATE FUNCTION public.auto_enable_rls()
      RETURNS event_trigger AS $body$
      DECLARE obj record;
      BEGIN
        FOR obj IN SELECT * FROM pg_event_trigger_ddl_commands()
          WHERE object_type = 'table' AND schema_name = 'public'
        LOOP
          EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', obj.object_identity);
        END LOOP;
      END;
      $body$ LANGUAGE plpgsql
    $fn$;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_event_trigger WHERE evtname = 'auto_rls_on_create_table') THEN
    CREATE EVENT TRIGGER auto_rls_on_create_table ON ddl_command_end
      WHEN TAG IN ('CREATE TABLE', 'CREATE TABLE AS', 'SELECT INTO')
      EXECUTE FUNCTION public.auto_enable_rls();
  END IF;
END $init$;
-- Migration v120 hardens this function's search_path as the application role.
-- The event trigger remains administrator-owned; no superuser grant is needed.
ALTER FUNCTION public.auto_enable_rls() OWNER TO gbrain;
