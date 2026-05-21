-- Búri knowledge memory substrate (NIU-541).
-- Typed fact graph with temporal validity, proto-vMF embedding clusters,
-- typed relationship edges, and proto-RWKV session state.
--
-- Requires: pgvector extension (vector type + ivfflat index method).
-- The extension is enabled by migration 000026_ravn_episodes.up.sql;
-- if not available, VECTOR columns fall back to TEXT (embeddings stored as JSON).

-- ---------------------------------------------------------------------------
-- Proto-vMF embedding clusters — must be created before knowledge_facts
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS memory_clusters (
    cluster_id    TEXT PRIMARY KEY,
    centroid      TEXT,              -- unit-normalised JSON float array (cast to vector when available)
    radius        REAL,              -- cosine spread of cluster members (proto-κ)
    member_count  INT NOT NULL DEFAULT 1,
    dominant_type TEXT,
    label         TEXT
);

CREATE INDEX IF NOT EXISTS idx_clusters_centroid
    ON memory_clusters (cluster_id);

-- ---------------------------------------------------------------------------
-- Typed facts with temporal validity
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS knowledge_facts (
    fact_id        TEXT PRIMARY KEY,
    fact_type      TEXT NOT NULL CHECK (fact_type IN ('preference','decision','goal','directive','relationship','observation')),
    content        TEXT NOT NULL,
    entities       TEXT[] NOT NULL DEFAULT '{}',
    embedding      TEXT,             -- JSON float array; cast to vector when pgvector available
    confidence     REAL NOT NULL DEFAULT 1.0 CHECK (confidence BETWEEN 0 AND 1),
    valid_from     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    valid_until    TIMESTAMPTZ,
    superseded_by  TEXT REFERENCES knowledge_facts(fact_id),
    source         TEXT NOT NULL,    -- "session:<id>" or "manual"
    source_context TEXT NOT NULL DEFAULT '',
    cluster_id     TEXT REFERENCES memory_clusters(cluster_id),
    tags           TEXT[] NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_facts_entities
    ON knowledge_facts USING GIN (entities);

CREATE INDEX IF NOT EXISTS idx_facts_current
    ON knowledge_facts (fact_type, valid_until)
    WHERE valid_until IS NULL;

CREATE INDEX IF NOT EXISTS idx_facts_cluster
    ON knowledge_facts (cluster_id)
    WHERE cluster_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_facts_source
    ON knowledge_facts (source);

-- ---------------------------------------------------------------------------
-- Typed relationship edges
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS knowledge_relationships (
    rel_id      TEXT PRIMARY KEY,
    from_entity TEXT NOT NULL,
    relation    TEXT NOT NULL,   -- "works_at", "prefers", "owns", "lives_in", "decided"
    to_entity   TEXT NOT NULL,
    fact_id     TEXT REFERENCES knowledge_facts(fact_id),
    valid_from  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    valid_until TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_rels_entities
    ON knowledge_relationships (from_entity, to_entity)
    WHERE valid_until IS NULL;

CREATE INDEX IF NOT EXISTS idx_rels_from_entity
    ON knowledge_relationships (from_entity);

CREATE INDEX IF NOT EXISTS idx_rels_to_entity
    ON knowledge_relationships (to_entity);

-- ---------------------------------------------------------------------------
-- Session recurrent state (proto-RWKV)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS session_states (
    session_id      TEXT PRIMARY KEY,
    rolling_summary TEXT NOT NULL DEFAULT '',
    active_entities TEXT[] NOT NULL DEFAULT '{}',
    turn_count      INT NOT NULL DEFAULT 0,
    last_updated    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_session_states_updated
    ON session_states (last_updated DESC);
