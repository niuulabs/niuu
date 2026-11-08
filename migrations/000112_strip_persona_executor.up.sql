-- A persona no longer names the harness that runs it: the executor block is gone from the
-- persona model, and the registry refuses a stored or submitted document that still carries
-- one. Every stored registry row (written by the API, including rows forked from built-in
-- personas) holds an `executor` key in config_json, and runtime_config_json holds one when
-- the persona set an adapter, so the key is removed from both before the new code reads them.
UPDATE ravn_personas
SET config_json = config_json - 'executor',
    runtime_config_json = runtime_config_json - 'executor'
WHERE config_json ? 'executor'
   OR runtime_config_json ? 'executor';
