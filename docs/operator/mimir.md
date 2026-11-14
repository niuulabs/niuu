# Mímir — the well of knowledge

Mímir is the platform's knowledge service. Agents read, write, and ingest
knowledge through it between sessions. Raw material flows in as immutable
**sources**; agents synthesise it into **pages**. Mímir does not store any of
this itself. It serves one configured **backend**, today a gbrain instance, over
HTTP and MCP.

This guide covers how it works, every way to use it, all configuration, and
the deployment options. Where memory is going next is in
[docs/design/memory/README.md](../design/memory/README.md).
In mini mode `niuu platform up` runs the gbrain and wires all of this itself; see
[local-memory.md](local-memory.md).

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
`session:<id>`, `run:<id>` when the session is a member of a workflow run,
`project:<id>` when it has a project, `attached:<name>` for each memory its
project or launch spec attaches, and `org`
([design](../design/memory/README.md), steps 1 and 2). It also builds the
launch context pack, lets people decide proposals and promote records, and
deletes run and session layers after their retention. It is off unless
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
      admin_token_file: /etc/mimir-memory/acme-brain/admin-token  # gbrain GBRAIN_ADMIN_BOOTSTRAP_TOKEN
      signing_key_file: /etc/mimir-memory/acme-brain/signing-key
  tenants:
    acme: acme-brain                         # tenant id -> system name
  service_auth:                              # the gateway as a service (Forge)
    adapter: niuu.adapters.outbound.http_auth.FileBearerTokenAuthAdapter
    token_file: /var/run/secrets/memory/token
  retention_days: 14
  retention_sweep_seconds: 3600
  org_admin_roles: [volundr:admin]
  connector_service_subjects: [ting]         # who may write connector sources
```

Each layer is its own gbrain source (`org`, and `p-`/`r-`/`s-` plus a hash of the
layer for projects, runs and sessions), written through its own gbrain client
(`mimir-layer-<layer>`, client credentials bound to that source). The gateway creates
both on a layer's first write with gbrain's owner bootstrap token and keeps no layer
secret: any replica recovers the same client from gbrain. Deleting a layer removes its
source and revokes its client. gbrain must allow `sources_add` over MCP (no managed
persistence). Search sends one `query` per layer source and caps nothing: gbrain's own
`search.searchLimit` decides how many hits each layer returns. People search with
`GET …/memory/search?q=…[&project_id=…]`.

In the chart these come from `config.memory` (`forgeBaseUrl`, `systems[]` with
`mcpUrl`, `adminTokenSecret`, `signingKeySecret`, `tenants`, `serviceAuth`,
`retentionDays`,
`retentionSweepSeconds`, `orgAdminRoles`, `connectorServiceSubjects` and the optional
timeouts); each
system's two secrets are mounted under `/etc/mimir-memory/<name>/`. The chart
renders `serviceAuth` as given and does not project a ServiceAccount token, so
mount whatever the chosen adapter reads. In mini mode without auth use
`niuu.adapters.outbound.http_auth.NoAuthHeaderAdapter`. Startup fails on a system
that cannot be built, a tenant naming a missing system, a system lacking a
capability its layers need, and a missing `service_auth`. A session whose org is not in
`tenants` gets a 503 naming `memory.tenants`, unless provisioning is on.

### Provisioned memory systems (`memory.provisioning`)

With `memory.provisioning` set, an org that is not in `memory.tenants` gets its own
gbrain on its first use, deployed through the knowledge deployment port. Static
`systems`/`tenants` stay the operator's override: a listed org always uses its system
and is never provisioned. Off (the default), nothing changes.

```yaml
memory:
  provisioning:
    deployment:                     # a KnowledgeDeploymentPort that provisions memory systems
      adapter: mimir.adapters.flux.FluxKnowledgeDeploymentAdapter
      namespace: memory             # brains only; the gateway may read its Secrets
      source_name: niuu             # Flux HelmRepository with the gbrain chart
      source_namespace: knowledge
      chart_versions: {gbrain: 2.0.0-dev.610}
      images: {gbrain: ghcr.io/niuulabs/gbrain:<tag>}
      cluster: ymir
      storage_class: harvester-data
      memory_values: {postgres: {instances: 1}}   # gbrain chart values over the defaults
    retry_after_seconds: 30         # what a request is told to wait while it is prepared
    backend: {timeout_seconds: 30}  # extra GBrainBackend arguments
```

On this computer use `mimir.adapters.local.LocalKnowledgeDeploymentAdapter` with
`root` and `gbrain_command` (and optionally `database` for an embedded PostgreSQL).

**Who causes it.** Any member of the org, on the org's first use: a session Forge
confirmed (its org comes from Forge), or a person through their own login (the people
routes, the graph). The org is never named by the caller on these paths, so nobody can
provision a brain for an org they are not in, and an org gets one brain however many
members ask. A connector (`POST …/memory/sources`) names the org in its request body,
so it never starts provisioning: an org without a system gets the 503 naming
`memory.tenants`. Operator commands (`mimir import-wiki`, `list-proposals`) read
provisioned systems and never start one.

**While it is prepared** every request for the org gets **503 with `Retry-After`**
(`retry_after_seconds`) and a detail that says memory for the org is being prepared,
never an empty result. A provisioning that failed is a **502** naming the cause and the
remedy. A not-configured org stays a 503 without `Retry-After`. Each outcome is counted
on `mimir.memory.provisioning` (attribute `outcome`: `started`, `preparing`, `ready`,
`failed`) and on `GET /health` under `memory.provisioning`.

**The registry is the deployment itself**, not a table. On Kubernetes each org's
gbrain is a HelmRelease `memory-<16 hex of sha256(tenant)>` labelled
`niuu.world/managed-by: mimir-memory` and annotated with the full tenant id; beside it
are the chart's `<release>-gbrain-admin` Secret (the owner bootstrap token) and
`<release>-memory-seal` (the signing key, created once by the gateway, not owned by the
release, so deleting the release keeps it). Creating either is atomic in the API
server, so replicas that ask at once deploy one brain: the loser's create is a 409 and
it reads the winner's. Ready means Flux's `Ready` condition is true for the current
generation; `Stalled` is failed (fix the cause, then `flux reconcile helmrelease`). A
release whose seal Secret is missing is failed and no new key is made: restore it, or
every record it sealed reads as unattested. The retention sweep covers every ready
provisioned system.

On this computer each org's gbrain lives in `<root>/.memory/<key>/`: `memory.json`
(the record), `admin-token` and `signing-key` (0600, created once), `runtime.json`, and
a launchd/systemd service `dev.niuu.memory.<key>` / `niuu-memory-<key>`. Provisioning
runs in the background under an exclusive lock on `<root>/.memory/<key>.lock`, so
processes on one computer provision once. gbrain is initialised `--db-only`
(`--pglite`, or `--non-interactive` with an embedded database) and has
`startup_timeout` seconds to answer. A failed or interrupted attempt is reported as
failed with its directory; remove the directory to provision again.

In the chart: `config.memory.provisioning` (`enabled`, `namespace`, `sourceName`,
`sourceNamespace`, `chartVersion`, `image`, `cluster`, `storageClass`, `values`,
`gbrainConfig`, `retryAfterSeconds`, `backend`). It renders the Flux target, a Role and
RoleBinding in `namespace` (HelmReleases get/list/create, Secrets get/create, Services
get) and mounts the pod's ServiceAccount token, and lets `systems` be empty. The
namespace must exist, be reconciled by Flux's helm-controller, and be reachable from
the gateway's pods; keep it for brains only, since the Role cannot name per-org
Secrets.

**Session routes.** `GET {base}/memory/tools` (instructions and tool
definitions), `POST {base}/memory/tools/{name}` (one call, an MCP
`CallToolResult`) and `GET {base}/memory/context-pack`, on `/mimir` and
`/api/v1/mimir`. The caller sends the session's `forge_session` credential; the
gateway reads `workload_session_id` from it and asks Forge
(`GET /api/v1/forge/sessions/{id}/memory-context`, scope `forge:memory`) with
the same token. Anything but Forge's 200 is a 401, so a person's login is
refused here. Mímir's own identity checks skip exactly these three routes and
nothing else.

**No model-written answers.** The gateway never writes an answer with a model.
Each memory system (gbrain) has its own search and synthesis; Niuu searches every
system that holds one of the reader's layers and collates the hits. The agent tool
`memory_search` and the people route `GET {base}/memory/search` (below) do that.

**Context pack.** Unbudgeted: it holds everything it selects, and the answer is
`{"text": "..."}`. That is the pages the session's objective and project brief
lead to, layer by layer, with their current facts, then the pages and facts
changed in the project and org layers since the owner's previous session in the
project.

**People routes.** A person's own login, through Mímir's identity adapter (in
mini mode without auth, the configured dev principal); a session or workload
credential is a 403. Forge is asked with the person's own `Authorization`.

- `GET {base}/memory/search?q=<text>[&project_id=<id>]` — searches the layers the
  person reads in every memory system that holds one: the org and every project they
  are on, or, with `project_id`, that project, the memories it attaches and the org (a
  project they are not on is a 404). The answer is `{"query": ..., "hits": [{"layer":
  "project:<id>", "layer_name": "...", "kind": "page"|"source", "ref": ..., "title": ...,
  "snippet": ...}]}` in the gateway's order (each system ranks its own hits; they are
  interleaved by rank). A source's `ref` is `source:<id>`. The route takes no limit and
  adds no cap: it returns every hit the memory systems return. A blank `q` is a 422.
- `GET {base}/memory/proposals?layer=project:<id>|org[&limit=n]` — pending
  proposals, for a project member with role `approver` or `owner` (Forge
  `GET /api/v1/forge/projects/{id}/role`; tenant admins are owners) or, for the
  org, a holder of one of `org_admin_roles`. Other members get 403, a non-member
  404. Every pending proposal is returned unless the caller names a `limit`.
- `GET {base}/memory/review[?layer=...][&limit=n]` — every proposal waiting on the person,
  oldest first, across the layers they decide (or one `layer`). With a `limit`, each layer
  is read up to it and `more` names the layers that reached it; without one, `more` is
  empty.
- `POST {base}/memory/proposals/{id}/approve` `{"layer": ...}` — applies the
  change at `operator` trust as the approver, with the proposal's evidence plus
  `proposal:<layer>/<id>`, whose record names the proposer. A change that no
  longer applies (the page moved on, a person-level fact) is a 409 and the
  proposal stays pending.
- `POST {base}/memory/proposals/{id}/reject` `{"layer": ..., "reason": ...}` —
  records the reason.
- `POST {base}/memory/promotions`
  `{"from": "session:<id>|run:<id>", "refs": [...], "to": "project:<id>|org"}` —
  proposes those records to the target; only the session's owner (Forge
  `GET /api/v1/forge/sessions/{id}`) or the run's owner
  (`GET /api/v1/forge/memory/runs/{id}`) may. Returns the proposals created.

Decided proposals move from `l/_proposals/<layer>/` to `l/_decided/<layer>/` and
stay there, so the evidence reference resolves.

**Knowledge graph.** `GET {base}/memory/graph[?layers=org,project:<id>]` is the
graph behind the 3D memory scene. It is a people route (a person's own login; session,
workload and connector credentials get 403), and it covers the layers that person can
read: the org layer and every project Forge lists for them (`GET /api/v1/forge/projects`
with their own `Authorization`, which returns member projects only). There is no person
layer yet. `?layers=` narrows to a subset of those and never widens: a layer they cannot
read is a 404. The answer is `{layers, nodes, edges, unresolved}`. Nodes are the pages of
those layers (`id` is `<layer>/<path>`, `kind` is `decision|person|service|ticket|runbook|page`
from the page type or first folder, `summary` the first sentence of the compiled truth,
`first_seen` / `changed_at` the oldest and newest timeline row) and the sources their
timelines cite (`<layer>/_sources/<id>`, kind `ticket` or `source`). Edges are read from
page text, never from gbrain's links: `[[slug]] — rel: <verb> — …` is a `<verb>` edge, any
other `[[slug]]` is `mentions`, and a timeline row citing `source:<id>` is a `cites` edge
dated by the row (`since`; a link's `since` is the oldest timeline row that links it, else
null). A link resolves to the nearest readable layer holding the page (its own layer, then
project, then org); a link to no readable page is listed under `unresolved`, not drawn.
A layer that cannot be read fails the whole response (502 naming the layer). Every page
and source of each layer is read; the graph has no record cap. Reads run `memory.graph_concurrency` (default 8) at a time,
and a graph is kept per org and layer set for `memory.graph_cache_seconds` (default 30,
the most a graph may be stale; 0 rebuilds every time). In the chart: `graphCacheSeconds`,
`graphConcurrency`.

**Connector route.** `POST {base}/memory/sources` lets an import service (Ting's
Linear import) keep what it fetched as an immutable source. Only a platform
service credential is admitted: a verified workload-identity token, unscoped,
whose actor (`act.sub`) is one of `memory.connector_service_subjects` (empty
admits nobody and the 403 names the setting). People, session credentials,
scoped tokens and other services get 403, and a connector credential is
refused on the session and people routes. With the allow-all identity of local
mini mode nothing verifies callers, so the local caller passes, still never
with a session or scoped credential. The body is `{tenant_id, layer, connector,
external_id, title, content, kind, origin_url, fetched_at, upstream_updated_at}`
(all required; the two times are ISO 8601). `layer` is `project:<id>` or `org`,
anything else is a 400, and the org must be in `memory.tenants` (503 naming it
otherwise). The source is written at `tool` trust, author `tool:<connector>`,
evidence `url:<origin_url>`. A source is named by its content, so the same
content already in that layer answers `new: false` and a ticket that changed
(a new comment) is a new source beside the old one. The answer is
`{"source_id", "layer", "new"}`. Each write is logged and counted as
`memory.connector.sources{connector,new}`; `external_id` and the two times are
logged, not stored.

**Attached layers.** Each attachment Forge names (`registry_entry_id`) is opened
from this gateway's own mount registry for the session's org: an entry of that
org, or a global entry when the org is the platform's own. A disabled or missing
entry fails the session's calls naming the layer. An attached store holds pages
only, reads as unattested (it has no sealed attribution), and takes writes only
as the attachment says: `read` refuses every write, `read_write` takes pages and
timeline rows under its write prefixes and refuses the rest. Nothing written to
an attached layer becomes a proposal. Every write adds a timeline row naming
its author, trust and evidence.

**Retention.** Every `retention_sweep_seconds` the gateway lists each system's
namespaces, asks Forge (`POST /api/v1/forge/memory/ended`, as the service) which
of their sessions and runs are active, ended (and when) or gone, and deletes the
run and session layers (with their proposal namespaces) that ended
`retention_days` ago or that Forge no longer knows. A sweep that cannot ask
Forge or list a system deletes nothing there, logs at error, is counted as
`memory.retention_sweep` on the degraded counter, and runs again next interval.

**Write rules.** An agent writes the session layer, and the run layer of a run
its session is a member of; writes project pages and timeline rows only under
the project's write prefixes; everything else for the project, and everything
for the org, is recorded as a proposal for the layer's owner. Attached layers
follow their attachment (above). A refused write (a layer the session does not have, tool output as a
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
# Pending proposals for a layer (decide them with the people routes above).
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
