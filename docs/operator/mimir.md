# Mímir — the well of knowledge

Mímir is the platform's knowledge service. Agents read, write, and ingest
knowledge through it between sessions. Raw material flows in as immutable
**sources**; agents synthesise it into **pages**. Mímir does not store any of
this itself. It serves one configured **backend**, today a gbrain instance, over
HTTP and MCP.

This guide covers how it works, every way to use it, all configuration, and
the deployment options. Where memory is going next is in
[docs/design/memory/README.md](../design/memory/README.md).

---

## 1. The data model

Mímir holds no knowledge on disk. The backend holds the pages and sources.
Mímir's own state directory (`path`, default `~/.ravn/mimir`) has two things:

```
~/.ravn/mimir/
  evals/                # captured search queries and the latest eval report
  .mimir-registry.json  # known mounts (multi-Mímir federation)
```

### Page format (`src/mimir/FORMAT.md` is the full spec)

Every page has YAML frontmatter (`type`, `confidence`, `entity_type`,
`related_entities`, `source_ids`) and two zones:

- **`## Compiled Truth`** — rewritable synthesis: `### Key Facts`,
  `### Relationships` (wikilinks, optionally typed:
  `- [[acme-corp]] — rel: works_at — lead engineer`), `### Assessment`.
- **`## Timeline`** — append-only evidence:
  `- YYYY-MM-DD: what happened. [Source: who, where, when]`.

Beliefs can be rewritten as understanding matures. The evidence trail is not
edited.

---

## 2. Retrieval

A query to `GET /mimir/search?q=…` (or `mimir_search`) goes to the backend, and
the backend ranks. Mímir adds no ranking of its own. With `debug=true` each
result carries the `score` and `score_breakdown` the backend reports.

Quality is measured against a golden set of 62 queries in
`tests/test_mimir/evals/` (a corpus and `golden.yaml`). The eval scores a store
that is already populated with that corpus:

```bash
uv run python -m mimir eval --backend '{"adapter": "mimir.adapters.gbrain.GBrainMimirAdapter", "mcp_url": "…", "api_token_file": "…"}'
uv run python -m mimir eval --backend '…' --against baseline.json --fail-on-regression
uv run python -m mimir eval replay --backend '…' --capture ~/.ravn/mimir/evals/queries-2026-W24.jsonl
```

`--backend` is required. There is no default store to score. The eval does not
load the corpus into the store.

---

## 3. The Retrieval Reflex

A zero-LLM scanner runs on agent turns: capitalised names and `@handles` in
the incoming message are matched against the Mímir entity index
(`GET /mimir/entities/index`) and up to `max_pointers` compact pointers are
prefixed to the turn, never page bodies:

```
Mimir knows (pointers only — use mimir_read for details):
[[mimir]] (technology, high confidence, updated 2026-06-12) — mimir_read entities/mimir.md
```

Hook point: the Skuld broker (messages forwarded to CLI agents). Per-session dedupe.
Each injection logs `mimir.reflex.injected session=<id> slugs=[…]` for
follow-up-rate measurement.

**Base URL resolution**: explicit `reflex.base_url` → first HTTP mimir
instance → derived from `volundr_api_url` as `{platform}/api/v1`
(Skuld brokers get this automatically). Disable with `reflex.enabled: false`
(`SKULD__REFLEX__ENABLED=false` for brokers).

---

## 4. Using Mímir

### From agents

| Surface | Tools |
|---|---|
| **MCP** (`python -m mimir mcp`) | `mimir_search`, `mimir_read`, `mimir_write`, `mimir_ingest`, `mimir_read_source`, `mimir_stats`, plus the `mimir_fact_*` tools |
| **Shell shims** (generated per Skuld session in `.skuld-tools/bin/`) | `ingest`, `search`, `read`, `read-source`, `write`, `publish-artifact`, `publish-files`, `list`, backed by `mimir.bridge`; work against the Mímir named by the session's `RAVN_CONFIG` (`mimir.instances`) |

`python -m mimir mcp` serves the Mímir named by `RAVN_CONFIG` (`mimir.instances`),
or the one in `--adapter-config <instance.yaml>`. It takes no `--path`.

### Over HTTP

Mounted at `/mimir` and `/api/v1/mimir`. Highlights:

| Endpoint | Purpose |
|---|---|
| `GET /search?q=&debug=` · `GET /page?path=` · `PUT /page` · `DELETE /page` · `POST /ingest` | core read/write |
| `GET /sources` · `GET /source` · `GET /page/sources` · `POST /sources/ingest/url` · `POST /sources/ingest/file` | sources |
| `GET /entities/index` · `GET /entities` · `GET /graph` | entities (reflex feed) and the link graph (visualiser) |
| `GET /eval/latest` · `GET /eval/queries` | analytics (eval report + live query traffic) |
| `GET /stats` · `GET /pages` · registry endpoints | catalog & federation |
| `GET /instances/inspect` · `/deployments` | instance inspection and knowledge deployments |
| `GET /activity` · `GET /activity/live` | durable activity log and live read/write presence |

### From the web UI

Tabs under the Mímir rune: **Overview** (KPIs, mounts, live activity) ·
**Pages** (tree + zone editor) · **Sources** (ingest form + raw records) ·
**Search** (with debug score breakdown) · **Graph** (force-directed, degree-scaled
nodes, pan/zoom) · **Registry** (mount CRUD) · **Analytics** (eval metrics,
query traffic).

---

## 5. Configuration reference

Every setting below is reachable in every deployment mode, sourced in this
order (first wins):

1. **Constructor / CLI flags** — `python -m mimir serve --backend '{…}'
   --no-eval-capture …` (flags not given fall through)
2. **Environment** — `MIMIR__`-prefixed with `__` nesting:
   `MIMIR__BACKEND='{"adapter": "…", "mcp_url": "…"}'`, `MIMIR__EVAL_CAPTURE=false`
3. **YAML file** — `$MIMIR_CONFIG` if set, else `./mimir.yaml` or
   `/etc/mimir/config.yaml`:

```yaml
backend:
  adapter: mimir.adapters.gbrain.GBrainMimirAdapter
  mcp_url: http://gbrain.niuu.svc.cluster.local:3131/mcp
  api_token_file: /run/secrets/gbrain/token
eval_capture: true
```

The platform plugin constructs the config with no arguments, so env vars and
the YAML file are how you configure Mímir inside `./start-dev` / Kubernetes.

### `MimirServiceConfig` (standalone service / platform plugin)

| Field | Default | Notes |
|---|---|---|
| `backend` | **required, no default** | the store this service serves: `adapter` (a fully-qualified `MimirPort` class) plus its constructor kwargs. A service with none fails at startup naming this setting |
| `path` | `~/.ravn/mimir` | state directory: the mount registry and `evals/`. Holds no knowledge |
| `host` / `port` | `0.0.0.0` / `7477` | standalone bind |
| `name` / `role` | `local` / `local` | role ∈ `local` \| `shared` \| `domain` |
| `categories` | `None` | category filter for domain-scoped instances |
| `announce_url` | `None` | public URL of this instance; the mount registry lists it as a remote mount there |
| `eval_capture` | **`True`** | search queries → `evals/*.jsonl` (Analytics + replay); disable for privacy-sensitive deployments |
| `deployment` | `None` | dynamic knowledge deployment adapter (see §7) |
| `facts` | `None` | typed facts (see below) |

`backend` for gbrain takes `mcp_url`, `api_token` or `api_token_file`,
`ingest_url`, `timeout_seconds`, `search_limit`, `think_model` and
`query_expansion`. Read `GBrainMimirAdapter.__init__` for their meaning.

### Reflex (`mimir.reflex` / Skuld `reflex`, env `SKULD__REFLEX__*`)

`enabled`, `max_pointers=5`, `cache_ttl_seconds=300`, `timeout_seconds=5`,
`base_url=""` (auto-derived; see §3). The client `mimir.reflex` block is off
unless you enable it. Skuld's `reflex` is on by default.

### Client-side `MimirConfig` (a host that mounts Mímir)

`enabled`, `instances` (each a `MimirPort` adapter with kwargs, or an HTTP `url`),
`write_routing` (prefix → mount rules), `read_retry`, and `reflex`.
`enabled: true` with no `instances` is a configuration error, and there is no
implicit local store.

---

## Typed facts (`mimir_fact_*`)

Pages are prose a model maintains. A *fact* is one claim about one subject with
who wrote it, from what evidence, how far it can be trusted, and when it holds.
Residents that kept their beliefs as prose read their own opinions back as
established truth; facts keep provenance and trust apart from that.

**Trust levels**, highest first: `person` (confirmed by a person), `operator`
(curated by an operator), `tool` (observed by a tool), `agent` (written by an
agent). They are assigned by the platform from the caller's authenticated
identity (`facts.trust.role_levels`, `scope_levels`). A request cannot name one:
`trust`, `author` and `confidence` in a request are refused. A resident, or any
workload acting for a user, is an `agent` (or a `tool` when a configured scope
says so) and never higher, whatever roles its token carries. A fact derived
from other facts takes the lowest level among them. A caller can retire or
replace only facts at or below its own level.

**Tools** (MCP, and `build_mimir_fact_tools` for in-process agents):
`mimir_fact_remember`, `_recall` (`min_trust`, `as_of`), `_forget` (who and why
recorded, history kept), `_entity`, `_context_pack` (ranked, token-budgeted,
lists everything it left out and why), `_share` and `_receive`. A fact received
from a peer keeps its original author and evidence, records who brought it in,
and arrives at no more than the importing caller's own level, because the
peer's claim of trust cannot be checked.

**Backend: gbrain.** Facts are stored in gbrain's own fact table through its
memory verbs (`remember`, `recall`, `entity`, `forget`), so they live where the
rest of the brain lives: embedded (PGlite) on a local install, PostgreSQL on a
cluster (`charts/gbrain`, `engine: postgres`). Mímir adds what gbrain lacks by
sealing it into the fact's provenance text with an HMAC (`signing_key_file`),
so a row written to gbrain directly cannot claim a trust level: it reads back
as an unattested `external` fact at the lowest level.

```yaml
facts:
  adapter: mimir.adapters.gbrain_facts.GBrainFactAdapter   # the default
  kwargs:
    mcp_url: http://brain.niuu.svc.cluster.local:3131/mcp
    api_token_file: /run/secrets/gbrain/token               # read + write scope
    signing_key_file: /run/secrets/mimir/fact-signing-key   # long random secret, Mímir only
  trust:
    role_levels: {volundr:admin: person, mimir:curator: operator}
```

Omit `facts` to serve no fact tools. With `facts` set, a backend that cannot be
built stops startup. Limits that apply are in [limits.md](limits.md).

## Memory gateway (`memory:`)

The memory gateway serves a Forge session's `memory` tools over its layers:
`org`, `project:<id>` when the session has a project, and `session:<id>`
([design](../design/memory/README.md), step 1). It is off unless
`memory.enabled`.

```yaml
memory:
  enabled: true
  forge_base_url: http://volundr:8080        # asked about the session on every call
  forge_timeout_seconds: 10
  systems:                                   # one gbrain per org
    - name: acme-brain
      adapter: mimir.backends.gbrain.GBrainBackend
      mcp_url: http://acme-brain:3131/mcp
      api_token_file: /etc/mimir-memory/acme-brain/token
      signing_key_file: /etc/mimir-memory/acme-brain/signing-key
  tenants:
    acme: acme-brain                         # tenant id -> system name
```

In the chart these come from `config.memory` (`forgeBaseUrl`, `systems[]` with
`mcpUrl`, `apiTokenSecret`, `signingKeySecret`, and `tenants`); each system's
two secrets are mounted under `/etc/mimir-memory/<name>/`. Startup fails on a
system that cannot be built, a tenant naming a missing system, or a system
lacking a capability its layers need. A session whose org is not in `tenants`
gets a 503 naming `memory.tenants`.

**Routes.** `GET {base}/memory/tools` (instructions and tool definitions) and
`POST {base}/memory/tools/{name}` (one call, an MCP `CallToolResult`), on
`/mimir` and `/api/v1/mimir`. The caller sends the session's `forge_session`
credential; the gateway reads `workload_session_id` from it and asks Forge
(`GET /api/v1/forge/sessions/{id}/memory-context`, scope `forge:memory`) with
the same token. Anything but Forge's 200 is a 401. Mímir's own identity checks
skip exactly these two routes and nothing else.

**Write rules.** An agent writes the session layer; writes project pages and
timeline rows only under the project's write prefixes; everything else for the
project, and everything for the org, is recorded as a proposal for the layer's
owner. A refused write (a layer the session does not have, tool output as a
source, a proposal for something it may write, a fact replacing one in another
layer) is a tool error naming the rule and is counted as
`memory.write_rules.<rule>` on the cap counter. Sessions write at `agent` trust.

**On gbrain**, each layer is a slug namespace in the org's one source
(`l/org/…`, `l/project-<id>/…`, `l/session-<id>/…`), proposals are pages under
`l/_proposals/<layer>/<id>`, and every page, timeline row, source and fact
carries its author, trust, evidence and layer sealed with `signing_key_file`. A
record written to gbrain directly reads as unattested, at the lowest trust; a
fact without a seal belongs to no layer and is never returned. `think` is never
called. Reads page through gbrain with `offset` until the caller's count is met;
`recall_limit` (gbrain's 100 rows per call) and `timeline_limit` are reported
and counted when reached (`memory.systems[].recall_limit`, `.timeline_limit`).

**Operator commands.**

```bash
# Import a retired Markdown wiki (wiki/, raw/) into a layer at operator trust.
python -m mimir import-wiki --root /data/old-wiki --tenant acme --layer project:<id> --operator jozef
# Pending proposals for a layer (approval comes in step 2).
python -m mimir list-proposals --tenant acme --layer org --limit 50
```

The import stops at the first page it cannot import, naming the file; files
that are not pages and tool-output sources are listed as not imported. Running
it again skips pages already imported unchanged.

## 6. Deployment options

1. **Inside the platform (default)** — the `mimir` plugin mounts on the niuu
   host at `/api/v1/mimir`. It needs `MIMIR__BACKEND` (or `backend:` in the
   config file) like any other Mímir.
2. **Standalone service** — `python -m mimir serve --backend '{…}' --port 7477
   [--name shared --role shared --announce-url https://…]`; announces itself
   on Sleipnir for discovery when a URL is given.
3. **MCP stdio** — `python -m mimir mcp` for Claude Code / Codex without any
   running service (configure in `.mcp.json`).
4. **Kubernetes** — Helm chart at `charts/mimir` (also a dependency of
   `charts/niuu`). It renders the required backend from
   `config.backend.mcpUrl` and `config.backend.apiTokenSecret`, and refuses to
   render without them. The chart's PVC holds only the state directory.
5. **Federation (multi-mount)** — several Mímirs (e.g. `local` + team
   `shared` + a `domain` instance scoped to categories) registered in
   `.mimir-registry.json`; reads merge across mounts by priority, writes route
   by path prefix (`write_routing`), discovery via the registry API. Auth:
   bearer token in dev, SPIFFE-ready (`auth_ref`) for production.

**Backup** = back up the backend (gbrain's PGlite directory or PostgreSQL
database). Mímir's state directory holds only the registry and eval captures.

---

## 7. Operational runbook

| Situation | Do |
|---|---|
| Search results look wrong | `GET /search?q=…&debug=true` and read the breakdown; check Analytics zero-result list |
| Service will not start | Read the error: it names `MIMIR__BACKEND` (or `--backend`) when no store is set, or the adapter that could not be built |
| Changed the backend or its retrieval settings | `python -m mimir eval --backend '{…}' --against tests/test_mimir/evals/baseline.json` against a store loaded with the golden corpus |
| Want reflex off for a session | `SKULD__REFLEX__ENABLED=false` (broker) / `mimir.reflex.enabled: false` (client config) |
| Measuring reflex usefulness | grep logs for `mimir.reflex.injected`, correlate with subsequent `mimir_read` calls |

### Exploring the knowledge graph

The Graph view projects stored pages into a shared contract via `MimirPort.get_graph()`.
Nodes carry an opaque ID, original page path, mount, title, summary, category, and kind.
Kind uses the stored entity subtype or page type (otherwise `page`);
category retains organizational labels such as `research`, `concepts`, or `notes`.
The all-mounts view keeps pages with identical paths in separate mounts distinct.

Use the category legend to toggle categories; its counts describe the loaded corpus.
The kind selector and search intersect those filters. Search matches titles, paths,
summaries, categories, kinds, and mount names, not the full page body. Select a node
or search result to center it, inspect its metadata, open its original page, or narrow
to its immediate neighbors. Fit frames the visible nodes. Drag or use arrow keys to
orbit, Shift-drag to pan, and scroll or use +/- to zoom. Idle rotation pauses during
interaction and selection, respects reduced-motion preferences, and can be disabled.

Edges represent explicit wikilinks, internal Markdown links, `related_entities`,
typed `rel:` relationships, and shared `source_ids`. External URLs, unresolved links,
and ambiguous bare names do not become page connections. No semantic-similarity or
inferred relationships are manufactured by the renderer.

The default graph projection reads listed page bodies. Gbrain uses its existing
`list_pages` and `get_page` MCP calls, including metadata preserved in Markdown
frontmatter. This exposes stored links rather than a native gbrain vector/graph
index. Indexed adapters can override `get_graph()` with the same contract. Large
remote corpora incur one page read per listed page; the current browser layout uses
quadratic pairwise repulsion. Bulk extraction and an indexed worker layout are the
upgrade paths when corpus size makes those costs material.

### Instance inspection and Flux deployment

Analytics includes a mount-scoped inspection section. gbrain reports its
HTTP health, version, storage engine, and page/category counts. Unreported fields
are displayed as unavailable; the MCP adapter does not currently retrieve
embedding coverage or synthesis history. This does not imply
those activities have never happened. An adapter without an inspection
implementation reports the capability as unsupported.

Registry can request new `gbrain` Helm releases through
`mimir.adapters.flux.FluxKnowledgeDeploymentAdapter`. Configure the dynamic
`deployment` map in Mimir service configuration (or `knowledgeDeployment` in the
Mimir chart) with these constructor arguments:

- `adapter`: `mimir.adapters.flux.FluxKnowledgeDeploymentAdapter`
- `namespace`: the pre-existing namespace receiving HelmRelease objects
- `source_name` and optional `source_namespace`: the existing Flux HelmRepository
- `chart_versions`: mapping from `gbrain` to its published chart version
- `images`: mapping from `gbrain` to its full image reference with tag
- `cluster`: display name for the configured cluster
- `in_cluster`: defaults to true; local operators can explicitly set false and
  supply `kube_context` to select a configured kubeconfig context

The target and image/chart catalog are operator-controlled, not arbitrary values
supplied by the browser. The chart grants get/list/create on HelmRelease objects
only in the configured namespace. Flux and its source must already exist.
The control-plane endpoints require `volundr:admin` through the existing trusted
identity headers. Put this service behind the authenticated Niuu/Envoy gateway,
which must strip client-supplied identity headers. Do not expose the service's
port directly to untrusted clients when deployment is enabled.

The form creates a release and polls its actual Flux conditions. It does not
claim readiness while the observed generation is stale. gbrain is always created
with external PostgreSQL; the operator-provisioned Secret must contain
`GBRAIN_DATABASE_URL` and `GBRAIN_ADMIN_BOOTSTRAP_TOKEN`. PostgreSQL itself is not
provisioned by this flow. `gbrain` is the only backend a deployment target
creates.

Deployment and attachment are separate: after readiness, connect the new backend
as a mount using its appropriate adapter and scoped credentials. The flow does
not automatically create gbrain API tokens or attach a native MCP URL as a Mimir
HTTP endpoint. It currently creates and inspects releases; upgrades and deletion
remain under the existing Flux operational workflow.

### Deployments

Registry owns deployment; Analytics owns inspection. Select the instance in
Analytics to find its runtime and service logs.

A local host can configure the existing dynamic deployment port:

```yaml
deployment:
  adapter: mimir.adapters.local.LocalKnowledgeDeploymentAdapter
  root: ~/.local/share/niuu/knowledge
  gbrain_command: [gbrain]
  database:
    adapter: niuu.adapters.embedded_postgres.EmbeddedPostgresDatabase
```

The host needs the installed gbrain executable, the Niuu Python runtime, and the
bundled PostgreSQL build (`make build-postgres`). That build includes pgvector,
pg_trgm, and pgcrypto; its build dependencies include OpenSSL development files
and pkg-config. Local instances use launchd on macOS or systemd user services on
Linux, survive the management API exiting, and keep their data in separate named
directories. PostgreSQL listens on a loopback port with a generated SCRAM password. gbrain receives its
own scoped read/write token; credentials stay in owner-only files. Deployment
registers its mount automatically.

Omit `database` only to explicitly choose PGLite.

The local management API uses the host's configured authentication. Production
requests still require an authenticated administrator through the trusted gateway;
this adapter does not grant or simulate administrator identity.

For multiple targets, configure
`mimir.adapters.deployment_targets.KnowledgeDeploymentTargets` with a `targets`
mapping. Each key is a target ID, and each value is an ordinary `adapter` plus
kwargs configuration. Local and Flux adapters share this deployment port. An
unreachable target reports its error without hiding available targets.

Flux targets accept operator-owned `secrets` (backend to Secret name, optionally
using `{name}` for separately provisioned instances). The UI asks for the engine,
name, and target; images and credentials belong to the target configuration.
Flux inspection reads actual pod/container logs. The deployment role needs
HelmRelease get/list/create/patch and pod/log get/list.


The Registry displays one list of instances, joining configured mounts, saved
connections, and managed deployments by mount name (the routing identity).
Deploying an instance adds management metadata to that entry; connecting an
existing service does not transfer ownership of its deployment. The two actions
are available above the same list.

Remote registry connections use the existing HTTP Mimir adapter and become
available to Pages, Graph, and Analytics immediately. The URL is the service base
URL (before `/mimir`); native gbrain MCP endpoints still use their configured
gbrain adapter. Connections requiring authentication must use an operator-configured
mount adapter: registry `auth_ref` resolution is not available. An older remote
Mimir service without `/mimir/instances/inspect` reports that capability as
unavailable; its pages and graph remain accessible through their existing APIs.

Registry deployment and connection forms open in the shared modal component.
Locally managed instances expose a Delete action with confirmation: the local
service is uninstalled before the instance's owned data
folder is removed. A failed shutdown preserves data and reports an error.
External connections expose Remove (tooltip: Remove connection), which only
removes the saved connection. Remote deployment deletion is not exposed.


For direct Helm deployment, evaluation capture is a chart value:

```yaml
config:
  evalCapture: true
```

Searches made through `/search` are captured when evaluation capture is enabled.
`/eval/queries` exposes real captured traffic; `/eval/latest` reads the latest
benchmark report. Aggregate hosts use their configured evaluation capture directory
for both endpoints, without requiring a filesystem-backed aggregate adapter.
A benchmark must actually be run and its report saved as `eval-latest.json` there;
missing reports are reported as unavailable, never generated scores.
