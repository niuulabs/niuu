-- Drop Búri knowledge memory substrate tables (NIU-576).
--
-- These tables were created by migration 000029_buri_knowledge_facts.up.sql
-- and are no longer used after the Búri memory adapter was removed.
-- Inline fact detection now writes to Mímir compiled-truth pages instead.

DROP TABLE IF EXISTS session_states;
DROP TABLE IF EXISTS knowledge_relationships;
DROP TABLE IF EXISTS knowledge_facts;
DROP TABLE IF EXISTS memory_clusters;
