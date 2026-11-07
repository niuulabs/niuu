-- The ODIN review queue, its Forge relay and the Valkyrie history store belonged to
-- the retired resident runtime; nothing reads or writes these tables any more.
-- Their create migrations (000048, 000049, 000089) stay in the history.
DROP INDEX IF EXISTS idx_odin_review_relay_items_open;
DROP TABLE IF EXISTS odin_review_relay_items;
DROP TABLE IF EXISTS odin_review_relay_owners;

DROP INDEX IF EXISTS idx_odin_review_items_environment;
DROP INDEX IF EXISTS idx_odin_review_items_status;
DROP TABLE IF EXISTS odin_review_items;

DROP INDEX IF EXISTS idx_valkyrie_signals_severity;
DROP INDEX IF EXISTS idx_valkyrie_signals_env_received;
DROP TABLE IF EXISTS valkyrie_signals;

DROP INDEX IF EXISTS idx_valkyrie_actions_env_observed;
DROP INDEX IF EXISTS idx_valkyrie_actions_correlation;
DROP TABLE IF EXISTS valkyrie_actions;

DROP INDEX IF EXISTS idx_valkyrie_decisions_correlation;
DROP INDEX IF EXISTS idx_valkyrie_decisions_env_decided;
DROP TABLE IF EXISTS valkyrie_decisions;
