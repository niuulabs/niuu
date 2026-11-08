-- The Ravn resident runtime was removed, and with it everything that read or wrote
-- these tables: the Postgres episodic memory (ravn_episodes, ravn_sessions), the
-- crash-recovery checkpoints (ravn_checkpoints, ravn_checkpoint_snapshots), the API's
-- trigger store (ravn_triggers) and the fleet budget ledger (ravn_budget_ledger).
-- Their create migrations (000026, 000030, 000031, 000032, 000081, 000082) stay in the
-- history. ravn_personas (000034) belongs to the persona registry and is kept.
DROP TABLE IF EXISTS ravn_budget_ledger;
DROP TABLE IF EXISTS ravn_triggers;
DROP TABLE IF EXISTS ravn_checkpoint_snapshots;
DROP TABLE IF EXISTS ravn_checkpoints;
DROP TABLE IF EXISTS ravn_sessions;
DROP TABLE IF EXISTS ravn_episodes;
DROP FUNCTION IF EXISTS ravn_episode_tsv(TEXT, TEXT, TEXT[]);
