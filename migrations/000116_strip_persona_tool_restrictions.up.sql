-- Restricting a persona's tools per harness is not available yet: allowed_tools and
-- forbidden_tools are gone from the persona model, and the registry refuses a stored or
-- submitted document that still carries either. Every stored registry row (written by the
-- API, including rows forked from built-in personas) holds both keys in config_json, and
-- runtime_config_json holds them when the persona listed a tool, so the keys are removed from
-- both before the new code reads them.
UPDATE ravn_personas
SET config_json = config_json - 'allowed_tools' - 'forbidden_tools',
    runtime_config_json = runtime_config_json - 'allowed_tools' - 'forbidden_tools'
WHERE config_json ?| array['allowed_tools', 'forbidden_tools']
   OR runtime_config_json ?| array['allowed_tools', 'forbidden_tools'];
