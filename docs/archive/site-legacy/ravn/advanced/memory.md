# Memory & Knowledge Systems

Ravn has three complementary memory systems: episodic memory for raw recall,
Búri for structured knowledge, and Mímir for curated wiki articles.

## Episodic Memory

Episodic memory records every agent turn as an `Episode` — a summary of what
happened, which tools were used, the outcome, and optional embeddings for
semantic search.

### Backends

| Backend | Description |
|---------|-------------|
| `sqlite` (default) | Local SQLite with FTS5 full-text search. Suitable for single-agent. |
| `postgres` | PostgreSQL with FTS and optional embeddings. For shared deployments. |
| `buri` | Búri knowledge substrate (see below). Includes episodic + typed facts. |

### Prefetch

Before each agent turn, Ravn prefetches relevant past episodes and injects
them into the system prompt. This gives the agent context from previous sessions.

| Config | Default | Description |
|--------|---------|-------------|
| `prefetch_budget` | 2000 | Max approximate tokens of past context. |
| `prefetch_limit` | 5 | Max episodes retrieved. |
| `prefetch_min_relevance` | 0.3 | Min relevance score (0–1). |

### Relevance Scoring

Episodes are scored by combining:

- **Keyword relevance**: FTS match score or cosine similarity (with embeddings)
- **Recency decay**: Exponential decay with configurable half-life (default: 14 days)
- **Outcome weight**: SUCCESS=1.0, PARTIAL=0.5, FAILURE=0.0

### Semantic Search

Enable embeddings for semantic (meaning-based) search alongside keyword search:

```yaml
embedding:
  enabled: true
  adapter: "ravn.adapters.embedding.sentence_transformer.SentenceTransformerEmbeddingAdapter"
  rrf_k: 60
  semantic_candidate_limit: 50
```

Available embedding adapters:

| Adapter | Description |
|---------|-------------|
| `SentenceTransformerEmbeddingAdapter` | Local sentence-transformers model. |
| `OpenAIEmbeddingAdapter` | OpenAI embeddings API. |
| `OllamaEmbeddingAdapter` | Ollama local embeddings. |

When enabled, search uses Reciprocal Rank Fusion (RRF) to combine FTS and
semantic results.

## Outcome Recording & Reflection

After each completed task, Ravn records a `TaskOutcome` with:

- Task summary
- Outcome status (SUCCESS, FAILURE, PARTIAL, INTERRUPTED)
- Tools used
- Token usage and estimated cost
- Lessons learned (via reflection)

The reflection step calls a lightweight model (default: `claude-haiku`) to
extract 1–3 lessons from the interaction. These lessons are stored and
surfaced in future prefetch.

```yaml
agent:
  outcome:
    enabled: true
    reflection_model: "claude-haiku-4-5-20251001"
    reflection_max_tokens: 512
    lessons_limit: 3
```

## Búri — Typed Fact Graph

Búri is a structured knowledge memory that maintains a graph of typed facts
with confidence scores, temporal validity, and embedding-based clustering.

### Fact Types

| Type | Description |
|------|-------------|
| `PREFERENCE` | User preferences (e.g., "prefers dark mode") |
| `DECISION` | Decisions made (e.g., "chose PostgreSQL over MySQL") |
| `GOAL` | Active goals (e.g., "migrate to Python 3.12") |
| `DIRECTIVE` | Standing instructions (e.g., "always run tests before committing") |
| `RELATIONSHIP` | Entity relationships (e.g., "auth-service depends on user-db") |
| `OBSERVATION` | Observed patterns (e.g., "tests fail on Mondays after deploys") |

### How Facts Are Extracted

1. **Inline detection**: Regex patterns match phrases like "remember that...",
   "I prefer...", "from now on..." during conversation
2. **LLM extraction**: After qualifying sessions, an LLM call extracts
   structured facts from the conversation
3. **Supersession**: New facts with high cosine similarity to existing facts
   replace the old version (threshold: 0.85)

### Proto-vMF Clustering

Facts are grouped into clusters using proto-von Mises-Fisher (vMF) clustering
based on embedding similarity. Clusters represent thematic groups (e.g., all
facts about "database preferences").

Retrieval is two-stage:
1. Find relevant clusters by centroid similarity
2. Within clusters, find specific facts
3. Expand via 2-hop graph traversal (relationships)

### Configuration

```yaml
buri:
  enabled: true
  cluster_merge_threshold: 0.15
  extraction_model: ""          # default: reflection model
  min_confidence: 0.6
  session_summary_max_tokens: 400
  supersession_cosine_threshold: 0.85
```

## Session Search

The `session_search` tool searches across all past sessions:

1. FTS keyword match across all episodes
2. Group results by session
3. Return session summaries with match highlights

Useful for finding "that conversation last week about the auth refactor."

## How the Three Systems Complement Each Other

| System | What It Stores | How It's Updated | When to Use |
|--------|---------------|-----------------|-------------|
| **Episodic Memory** | Raw turns, tool calls, outcomes | Automatic after every turn | Recalling what happened in past sessions |
| **Búri** | Typed facts, preferences, decisions | Auto-extracted + inline detection | Understanding user preferences, standing instructions |
| **Mímir** | Curated wiki articles | Explicit write + auto-distill | Looking up documented knowledge, architecture |

Together, they provide a complete memory architecture:
- **Búri** knows what the agent believes
- **Episodic memory** knows what the agent did
- **Mímir** knows what the agent has documented

See also: [Mímir documentation](../platform/mimir.md)
