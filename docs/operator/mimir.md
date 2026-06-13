# Mímir — the well of knowledge

Mímir is the platform's compounding knowledge base: a markdown wiki that agents
read, write, and learn from between sessions. Raw material flows in as
immutable **sources**; agents synthesise it into **pages** whose format
separates rewritable belief from append-only evidence; retrieval, ranking, a
link graph, and a write-time learning loop make that knowledge findable and
self-correcting.

This guide covers how it works, every way to use it, all configuration, and
the deployment options.

---

## 1. The data model

A Mímir store is a plain directory (default `~/.ravn/mimir`) — no database is
the system of record, and it is deliberately **not** git-backed (history lives
in the format instead; `git init` it yourself if you want snapshots):

```
~/.ravn/mimir/
  wiki/                 # the knowledge itself, one markdown file per page
    index.md            # content catalog (maintained automatically)
    log.md              # append-only activity log
    entities/…          # people / projects / orgs / tech (wikilink targets)
    technical/… etc.    # other categories
  raw/                  # immutable ingested sources (JSON, content-hashed)
  threads/              # work-thread state (YAML + notes)
  evals/                # captured search queries + latest eval report
  search.db             # disposable hybrid search index (rebuilt on startup)
  .mimir-registry.json  # known mounts (multi-Mímir federation)
```

### Page format (`src/mimir/FORMAT.md` is the full spec)

Every page has YAML frontmatter (`type`, `confidence`, `entity_type`,
`related_entities`, `source_ids`) and two zones:

- **`## Compiled Truth`** — rewritable synthesis: `### Key Facts`,
  `### Relationships` (wikilinks, optionally typed:
  `- [[acme-corp]] — rel: works_at — lead engineer`), `### Assessment`.
- **`## Timeline`** — append-only evidence:
  `- YYYY-MM-DD: what happened. [Source: who, where, when]`. Entries are never
  edited or deleted; the linter (L09) detects tampering.

This split is the core idea: beliefs can be rewritten as understanding
matures, but the evidence trail — including *belief revisions* — is permanent.

---

## 2. Retrieval: what happens on a search

A query to `GET /mimir/search?q=…` (or `mimir_search`) runs this pipeline:

1. **Hybrid index** — SQLite FTS5 (BM25) and, when an embedding model is
   configured, a sqlite-vec KNN arm over chunk embeddings; the two rankings
   merge via reciprocal rank fusion. Pages are chunked by `##` heading; the
   index over-fetches `limit × overfetch_factor` candidates.
2. **Relational arm** — entity names recognised in the query inject that
   entity page and its 1-hop link-graph neighbourhood as candidates, even when
   no keywords match ("who uses Mimir?" works).
3. **Ranking boosts** (all in `RankingConfig`, all eval-validated): recency
   decay, title match, zone weighting (compiled-truth chunks above timeline
   evidence), page-type weights, graph entity boost. Confidence and backlink
   boosts exist but default to neutral — the ablation showed they hurt
   precision under hybrid retrieval.
4. **Debug** — add `debug=true` and every result carries `score` plus a
   per-factor `score_breakdown`.

Quality is measured, not assumed: a 62-query golden set
(`tests/test_mimir/evals/`) gates CI. Current numbers — FTS-only
P@5 ≈ 0.46; hybrid (all-MiniLM-L6-v2) **P@5 0.976 / MRR 0.884 /
recall@10 0.984**.

```bash
uv sync --extra embeddings        # hybrid needs sentence-transformers
uv run python -m mimir eval                      # golden-set eval (FTS-only)
uv run python -m mimir eval --embedding-model all-MiniLM-L6-v2
uv run python -m mimir eval --against baseline.json --fail-on-regression
uv run python -m mimir eval replay --capture ~/.ravn/mimir/evals/queries-2026-W24.jsonl
```

Without the embeddings extra everything degrades gracefully to FTS-only.

---

## 3. The learning loop

Modeled on Hindsight's retain/reflect, fully deterministic (zero LLM calls):

- **Evidence-counted beliefs** — every Key Fact is scored against the page's
  timeline entries and raw sources: a proof count and a freshness trend
  (`new / strengthening / stable / weakening / stale`). Surface via
  `GET /mimir/evidence?path=…`. Thresholds live in `EvidenceConfig`.
- **Write-time consolidation ("micro-dream")** — every `ingest` finds the
  pages the new source bears on and recomputes their evidence immediately
  (`consolidate_on_ingest`, on by default). The nightly dream cycle remains
  for full-wiki passes.
- **Belief revision with journey** — `POST /mimir/page/revise` rewrites a fact
  in Compiled Truth *and* appends
  `belief revised: "old" → "new". [Source: …]` to the Timeline, so "what did
  we used to believe?" stays answerable.

---

## 4. The Retrieval Reflex (on by default)

A zero-LLM scanner runs on agent turns: capitalised names and `@handles` in
the incoming message are matched against the Mímir entity index
(`GET /mimir/entities/index`) and up to `max_pointers` compact pointers are
prefixed to the turn — never page bodies:

```
Mimir knows (pointers only — use mimir_read for details):
[[mimir]] (technology, high confidence, updated 2026-06-12) — mimir_read entities/mimir.md
```

Hook points: the Ravn drive loop (persona turns) and the Skuld broker
(messages forwarded to CLI agents). Per-session dedupe, ~1 ms per turn at
1k entities, fail-open (a dead Mímir never blocks a turn). Each injection
logs `mimir.reflex.injected session=<id> slugs=[…]` for follow-up-rate
measurement.

**Base URL resolution**: explicit `reflex.base_url` → first HTTP mimir
instance (Ravn) → derived from `volundr_api_url` as `{platform}/api/v1`
(Skuld brokers get this automatically). If nothing resolves, the reflex stays
inert with a single warning. Disable with `reflex.enabled: false`
(`SKULD__REFLEX__ENABLED=false` for brokers).

---

## 5. Using Mímir

### From agents

| Surface | Tools |
|---|---|
| **MCP** (`python -m mimir mcp --path ~/.ravn/mimir`) | `mimir_search`, `mimir_read`, `mimir_write`, `mimir_ingest`, `mimir_read_source`, `mimir_lint`, `mimir_stats`, `mimir_related` |
| **Shell shims** (generated per Skuld session in `.skuld-tools/bin/`) | same eight commands, backed by `ravn.cli.mimir_bridge`; work against a local path or an HTTP URL via `RAVN_MIMIR_PATH` |
| **Ravn tools** | `MimirIngestTool`, `MimirReadTool`, `MimirWriteTool`, `MimirSearchTool`, `MimirLintTool` (permission-gated: `mimir:read` / `mimir:write`) |

`mimir_related --path entities/alice.md --depth 2 [--rel works_at]` traverses
the wikilink graph in both directions — use it for relationship questions
instead of keyword search.

### Over HTTP

Mounted at `/mimir` and `/api/v1/mimir`. Highlights:

| Endpoint | Purpose |
|---|---|
| `GET /search?q=&debug=` · `GET /page?path=` · `PUT /page` · `POST /ingest` | core read/write |
| `GET /related?path=&depth=&rel=` · `GET /entities/index` · `GET /graph` | link graph (traversal, reflex feed, visualiser) |
| `GET /evidence?path=` · `POST /page/revise` | learning loop |
| `GET /lint` · `POST /lint/fix` · `GET /doctor` · `POST /doctor/fix` | health |
| `GET /eval/latest` · `GET /eval/queries` | analytics (eval report + live query traffic) |
| `GET /stats` · `GET /pages` · `GET /sources` · registry endpoints | catalog & federation |

### From the web UI

Nine tabs under the Mímir rune: **Overview** (KPIs, mounts, live activity —
writes appear within 5 s) · **Pages** (tree + zone editor) · **Sources**
(ingest form + raw records) · **Search** (FTS/semantic/hybrid toggle + debug
score breakdown) · **Graph** (force-directed, degree-scaled nodes, pan/zoom) ·
**Registry** (mount CRUD) · **Wardens** (ravn bindings lifecycle) · **Health**
(doctor checklist + lint detail with fixes) · **Analytics** (eval metrics,
query traffic, dream-cycle history).

---

## 6. Health: lint and doctor

- **Lint (L01–L12)** — wiki hygiene: orphans, contradictions, concept gaps,
  broken wikilinks (fuzzy auto-fix), missing attribution, thin pages, stale
  content, timeline tampering, index drift, invalid frontmatter. Auto-fixable:
  L05, L11, L12.
- **Doctor (D01–D08)** — instance health: root/writability, index sync,
  search-index consistency, embedding stack, registry mount reachability, lint
  summary, orphaned raw sources, end-to-end smoke search.

```bash
uv run python -m mimir doctor [--path …] [--fix] [--json]   # exit 0/1/2
```

`--fix` applies only the safe subset (rebuild search index, lint auto-fixes).
Exit codes make it cron-friendly.

---

## 7. Configuration reference

Every setting below is reachable in every deployment mode, sourced in this
order (first wins):

1. **Constructor / CLI flags** — `python -m mimir serve --embedding-model …
   --no-eval-capture …` (flags not given fall through)
2. **Environment** — `MIMIR__`-prefixed with `__` nesting:
   `MIMIR__EMBEDDING_MODEL=all-MiniLM-L6-v2`,
   `MIMIR__RANKING__TITLE_MATCH_BOOST=1.5`, `MIMIR__EVAL_CAPTURE=false`
3. **YAML file** — `$MIMIR_CONFIG` if set, else `./mimir.yaml` or
   `/etc/mimir/config.yaml`:

```yaml
embedding_model: all-MiniLM-L6-v2
eval_capture: true
ranking:
  overfetch_factor: 4
  confidence_boosts: { high: 1.15, low: 0.85 }   # opt back in if you want it
evidence:
  stale_after_days: 90
```

The platform plugin constructs the config with no arguments, so env vars and
the YAML file are how you configure Mímir inside `./start-dev` / Kubernetes.

### `MimirServiceConfig` (standalone service / platform plugin)

| Field | Default | Notes |
|---|---|---|
| `path` | `~/.ravn/mimir` | store root |
| `host` / `port` | `0.0.0.0` / `7477` | standalone bind |
| `name` / `role` | `local` / `local` | role ∈ `local` \| `shared` \| `domain` |
| `categories` | `None` | category filter for domain-scoped instances |
| `announce_url` | `None` | set to announce on Sleipnir for discovery |
| `search_db` | `<path>/search.db` | disposable; safe to delete |
| `embedding_model` | `None` | e.g. `all-MiniLM-L6-v2`; None = FTS-only |
| `eval_capture` | **`True`** | search queries → `evals/*.jsonl` (Analytics + replay); disable for privacy-sensitive deployments |
| `ranking` | see below | |
| `evidence` | see below | |

### `RankingConfig`

| Field | Default | Field | Default |
|---|---|---|---|
| `enabled` | `True` | `zone_weights` | `{timeline: 0.9}` |
| `overfetch_factor` | `4` | `graph_injection_base` | `0.3` |
| `recency_half_life_days` | `90` | `graph_entity_boost` | `1.5` |
| `recency_floor` | `0.5` | `graph_neighbor_boost` | `1.0` (neutral by ablation) |
| `title_match_boost` | `1.25` | `confidence_boosts` | `{}` (neutral by ablation) |
| `page_type_weights` | `{directive: 1.1, decision: 1.1}` | `backlink_alpha` | `0.0` (neutral by ablation) |

Change a boost → run `uv run python scripts/mimir_ranking_ablation.py
[--embedding-model …]` and keep only what moves P@5/MRR.

### `EvidenceConfig`

`min_token_overlap=2`, `weakening_after_days=60`, `stale_after_days=90`,
`strengthening_min_proofs=3`, `consolidate_on_ingest=True`.

### Reflex (Ravn `mimir.reflex` / Skuld `reflex`, env `SKULD__REFLEX__*`)

`enabled=True`, `max_pointers=5`, `cache_ttl_seconds=300`,
`timeout_seconds=5`, `base_url=""` (auto-derived; see §4).

### Ravn-side `MimirConfig` (agent host)

`enabled`, `path`, `auto_distill` (session knowledge capture),
`idle_lint_threshold_minutes`, `instances` (HTTP mounts),
`write_routing` (prefix → mount rules), `reflex`, and the
`dream_cycle` trigger (nightly curator persona; disabled by default).

---

## 8. Deployment options

1. **Inside the platform (default)** — the `mimir` plugin mounts on the niuu
   host at `/api/v1/mimir`; `./start-dev` gives you everything locally.
2. **Standalone service** — `python -m mimir serve --path … --port 7477
   [--name shared --role shared --announce-url https://…]`; announces itself
   on Sleipnir for discovery when a URL is given.
3. **MCP stdio** — `python -m mimir mcp` for Claude Code / Codex without any
   running service (configure in `.mcp.json`).
4. **Kubernetes** — Helm chart at `charts/niuu/charts/mimir-*.tgz`
   (Deployment, PVC, Ingress, HPA).
5. **Federation (multi-mount)** — several Mímirs (e.g. `local` + team
   `shared` + a `domain` instance scoped to categories) registered in
   `.mimir-registry.json`; reads merge across mounts by priority, writes route
   by path prefix (`write_routing`), discovery via the registry API or
   Sleipnir announce. Auth: bearer token in dev, SPIFFE-ready (`auth_ref`) for
   production.

**Backup** = copy the directory. `search.db` and the link graph are derived
state; the markdown + `raw/` JSON are the only things that matter.

---

## 9. Operational runbook

| Situation | Do |
|---|---|
| Search results look wrong | `GET /search?q=…&debug=true` and read the breakdown; check Analytics zero-result list |
| Anything feels off | `python -m mimir doctor` → `--fix` for the safe repairs |
| Changed ranking/search code | `python -m mimir eval --against tests/test_mimir/evals/baseline.json --fail-on-regression` (CI enforces this) |
| Improved retrieval intentionally | refresh the baseline: `python -m mimir eval --json --out tests/test_mimir/evals/baseline.json` |
| Index corruption suspected | delete `search.db`; it rebuilds on startup |
| Want reflex off for a session | `SKULD__REFLEX__ENABLED=false` (broker) / `mimir.reflex.enabled: false` (ravn) |
| Measuring reflex usefulness | grep logs for `mimir.reflex.injected`, correlate with subsequent `mimir_read` calls |
