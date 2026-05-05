# Service Boundaries And API Consolidation Plan

## Why this exists

The current backend shape is drifting toward a monolith of large named services (`volundr`, `tyr`) that own routes far outside their natural domain. At the same time, `web-next` is pushing toward a cleaner plugin-oriented API surface.

We need to restore domain boundaries without turning every router into its own container or its own `main.py`.

This document proposes:

- a domain-first route ownership model
- a small number of deployable processes
- extraction of overlapping capabilities into standalone route modules
- a concrete mapping from current backend routes to the APIs `web-next` expects
- a path for future service separation without forcing it now

## Design principles

### 1. Separate domain ownership from deployment

A route can belong to the `identity` domain without requiring a dedicated `identity` container today.

Target rule:

- each bounded context owns its own router, service layer, and persistence contracts
- multiple bounded contexts may be mounted into the same FastAPI process
- a bounded context only becomes its own deployable when there is a real operational reason

### 2. Optimize for a modular monolith first

Preferred shape:

- few deployables
- many explicit routers
- no ambiguous ownership

This gives us:

- cleaner APIs
- fewer cross-domain leaks
- easier future extraction
- lower operational overhead than full microservices

### 3. Treat route modules as the unit of future extraction

Every extracted domain should be packaged so it can be:

- mounted into a shared `platform-api` process now
- moved into its own process later with minimal code movement

That means each domain should expose something like:

- `create_router(...)`
- `register_background_tasks(...)` if needed
- domain services and ports that do not depend on a specific host app

## The core problem in the current repo

### What we have now

- `volundr` owns sessions, templates, clusters, secrets, credentials, tenants, users, features, PATs, integrations, tracker mappings, admin settings, git operations, audit, and more
- `tyr` owns saga and dispatch concerns, but `web-next` expects additional settings, session approval, and audit surfaces that are partly missing or overlap with Volundr
- `ravn` currently exposes personas plus a small stub session surface
- `mimir` is a smaller real service, but its HTTP surface is behind `web-next`
- `observatory` exists in `web-next` but not as a backend service yet

### Why this is bad

- service names no longer reflect bounded contexts
- route ownership is unclear
- duplication is growing around identity, features, tokens, audit, sessions, integrations, and tracker APIs
- `web-next` is being forced to adapt to historical route placement instead of stable domain APIs

## Proposed target bounded contexts

These are domain boundaries, not mandatory deployables.

### 1. `identity`

Owns:

- current user identity
- users
- tenants and membership
- PATs / access tokens
- feature catalog and user feature preferences
- auth-adjacent runtime metadata if any remains

Canonical routes:

- `/api/v1/identity/me`
- `/api/v1/identity/users`
- `/api/v1/identity/tenants`
- `/api/v1/identity/tenants/{tenant_id}/members`
- `/api/v1/identity/tokens`
- `/api/v1/identity/features/modules`
- `/api/v1/identity/features/preferences`

Current overlap to extract:

- `src/volundr/adapters/inbound/rest_tenants.py`
- `src/volundr/adapters/inbound/rest_features.py`
- PAT routing currently split between Volundr and `src/niuu/adapters/inbound/rest_pats.py`

Why this should be its own route domain:

- `web-next/packages/plugin-sdk` already wants identity and feature catalog as platform capabilities, not Volundr-only concerns
- PATs, tenants, and users are not session-forge concerns

### 2. `forge`

This is the actual Völundr core.

Owns:

- workspaces and sessions
- templates and presets
- session messages, logs, chronicle, live stats
- cluster resources
- MCP server attachment to sessions
- code-server access
- workspace cleanup / restore

Canonical routes:

- `/api/v1/forge/sessions`
- `/api/v1/forge/sessions/{id}`
- `/api/v1/forge/sessions/{id}/messages`
- `/api/v1/forge/sessions/{id}/logs`
- `/api/v1/forge/sessions/{id}/chronicle`
- `/api/v1/forge/sessions/{id}/code-server-url`
- `/api/v1/forge/sessions/{id}/mcp-servers`
- `/api/v1/forge/templates`
- `/api/v1/forge/presets`
- `/api/v1/forge/workspaces`
- `/api/v1/forge/cluster/resources`
- `/api/v1/forge/stats`
- `/api/v1/forge/models`
- `/api/v1/forge/repos`

Current overlap to keep inside forge:

- most of `src/volundr/adapters/inbound/rest.py`
- `rest_presets.py`
- `rest_profiles.py`
- `rest_resources.py`
- parts of `rest_secrets.py` related to forge attachment and cluster use

Current overlap to remove from forge:

- identity, tenants, users, feature catalog, tokens
- generic audit
- tracker catalog if we centralize tracker integration later

### 3. `integrations`

Owns:

- integration catalog
- connection CRUD
- connection tests
- OAuth callbacks and setup flows
- credential-backed external connector wiring

Canonical routes:

- `/api/v1/integrations/catalog`
- `/api/v1/integrations`
- `/api/v1/integrations/{id}`
- `/api/v1/integrations/{id}/test`
- `/api/v1/integrations/oauth/*`

Current overlap to extract:

- `src/volundr/adapters/inbound/rest_integrations.py`
- `src/volundr/adapters/inbound/rest_oauth.py`

Why extract as its own route domain:

- both Tyr and Forge need integrations
- connector management should not be owned by session orchestration

### 4. `credentials`

Owns:

- user and tenant credential stores
- pluggable secret store surfaces
- secret types
- cluster-available secret definitions if they are generic

Canonical routes:

- `/api/v1/credentials/user`
- `/api/v1/credentials/tenant`
- `/api/v1/credentials/store`
- `/api/v1/credentials/store/{name}`
- `/api/v1/credentials/types`
- `/api/v1/credentials/mcp-servers`

Current overlap to extract:

- `src/volundr/adapters/inbound/rest_credentials.py`
- `src/volundr/adapters/inbound/rest_secrets.py`

Why this may stay in the same deployable for now:

- low operational need to separate
- high conceptual value in separating ownership from Forge

### 5. `tracker`

Owns:

- tracker project and issue browsing
- project-to-repo mappings
- generic issue search/update
- import flows from tracker to saga or forge

Canonical routes:

- `/api/v1/tracker/projects`
- `/api/v1/tracker/projects/{id}`
- `/api/v1/tracker/projects/{id}/milestones`
- `/api/v1/tracker/projects/{id}/issues`
- `/api/v1/tracker/issues`
- `/api/v1/tracker/issues/{id}`
- `/api/v1/tracker/repo-mappings`
- `/api/v1/tracker/import`

Current overlap to extract:

- `src/tyr/api/tracker.py`
- `src/volundr/adapters/inbound/rest_tracker.py`
- `src/volundr/adapters/inbound/rest_issues.py`

Why extract:

- tracker browsing is shared infrastructure
- it should not be split between Tyr and Forge by accident

### 6. `tyr`

Owns:

- sagas
- phases
- raids
- planning
- dispatch queue orchestration
- dispatcher runtime state
- Tyr-specific settings and execution policy
- Tyr event stream

Canonical routes:

- `/api/v1/tyr/sagas`
- `/api/v1/tyr/sagas/{id}`
- `/api/v1/tyr/sagas/{id}/phases`
- `/api/v1/tyr/sagas/decompose`
- `/api/v1/tyr/sagas/plan`
- `/api/v1/tyr/sagas/commit`
- `/api/v1/tyr/sagas/extract-structure`
- `/api/v1/tyr/dispatcher`
- `/api/v1/tyr/dispatcher/log`
- `/api/v1/tyr/dispatch/{raid_id}`
- `/api/v1/tyr/dispatch/batch`
- `/api/v1/tyr/settings/flock`
- `/api/v1/tyr/settings/dispatch`
- `/api/v1/tyr/settings/notifications`
- `/api/v1/tyr/events`

What Tyr should stop owning:

- generic integrations CRUD if moved to `integrations`
- tracker browsing if moved to `tracker`
- generic audit if moved to `audit`
- non-Tyr sessions if those are really Forge-owned workspaces

### 7. `ravn`

Owns:

- personas
- ravn process / fleet state
- ravn sessions and transcripts
- triggers
- budget projections for ravns

Canonical routes:

- `/api/v1/ravn/personas`
- `/api/v1/ravn/ravens`
- `/api/v1/ravn/sessions`
- `/api/v1/ravn/sessions/{id}`
- `/api/v1/ravn/sessions/{id}/messages`
- `/api/v1/ravn/triggers`
- `/api/v1/ravn/budget/{ravn_id}`
- `/api/v1/ravn/budget/fleet`

Current state:

- personas are real
- sessions exist only as a small stub
- ravens, triggers, budget, transcript APIs are not implemented

### 8. `mimir`

Owns:

- pages and sources
- ingest
- search
- embeddings
- lint
- entities
- mount registry and write routing
- activity and dream-cycle logs

Canonical routes:

- `/api/v1/mimir/stats`
- `/api/v1/mimir/pages`
- `/api/v1/mimir/page`
- `/api/v1/mimir/page/sources`
- `/api/v1/mimir/search`
- `/api/v1/mimir/graph`
- `/api/v1/mimir/entities`
- `/api/v1/mimir/sources`
- `/api/v1/mimir/sources/ingest/url`
- `/api/v1/mimir/sources/ingest/file`
- `/api/v1/mimir/mounts`
- `/api/v1/mimir/mounts/recent-writes`
- `/api/v1/mimir/routing/rules`
- `/api/v1/mimir/ravns/bindings`
- `/api/v1/mimir/embeddings/search`
- `/api/v1/mimir/lint`
- `/api/v1/mimir/lint/fix`
- `/api/v1/mimir/lint/reassign`
- `/api/v1/mimir/dreams`
- `/api/v1/mimir/activity`

### 9. `observatory`

Owns:

- topology snapshots
- registry of observable entities/types
- observatory event firehose

Canonical routes:

- `/api/v1/observatory/registry`
- `/api/v1/observatory/topology/stream`
- `/api/v1/observatory/events/stream`

Likely implementation home:

- route module can live in a new `observatory` package
- event projection may reuse Skuld or Sleipnir subscribers

### 10. `audit`

Owns:

- audit log queries
- service-scoped audit filtering

Canonical routes:

- `/api/v1/audit`
- or `/api/v1/audit/events`

Current overlap to extract:

- `src/volundr/adapters/inbound/rest_audit.py`
- Tyr audit expectations in `web-next` currently have no matching router

Recommendation:

- centralize audit as shared infrastructure
- allow `service=tyr|forge|identity|ravn|mimir` filtering

## What `web-next` expects and where the gaps are

## Identity and feature catalog

Expected by `plugin-sdk`:

- `IIdentityService`
- `IFeatureCatalogService`

Current reality:

- frontend SDK still uses Volundr-backed `/me` and `/features*` adapters
- backend currently exposes `/api/v1/volundr/me`
- feature toggles live under Volundr as well
- PATs are split and not aligned to the frontend model

Required consolidation:

- move identity, tenants, users, features, and tokens under one `identity` route module
- keep old Volundr routes as temporary compatibility shims

## Völundr / Forge

Expected by `plugin-volundr`:

- sessions, templates, presets, workspaces, cluster resources, logs, messages, chronicle, PR/CI, admin settings, credentials, tokens, feature modules, identity

Current reality:

- core CRUD mostly exists
- live subscriptions are mostly mocked in the frontend adapter
- identity / features / tokens are mixed into the same service interface even though they are not Forge concerns

Required consolidation:

- shrink `IVolundrService` over time or keep it as a facade backed by multiple route domains
- real Forge ownership should focus on sessions/workspaces/templates/cluster/stats

Required missing APIs:

- real SSE for session list, stats, messages, logs, chronicle
- cleaner archived session route handling

## Tyr

Expected by `plugin-tyr`:

- sagas
- saga phases
- dispatcher control
- dispatch bus endpoints
- settings for flock, dispatch, notifications
- audit
- tracker browsing
- session approval

Current reality:

- sagas and dispatcher are strong
- SSE exists
- `GET /sagas/{id}/phases` is missing
- dispatch bus REST endpoints are missing
- settings coverage is incomplete
- audit endpoint is missing
- tracker exists but is a candidate for extraction
- session approval ownership is muddy

Required consolidation:

- Tyr keeps saga/dispatch ownership
- tracker and audit should move out
- session approval should be explicitly either Tyr-native or backed by Forge

## Ravn

Expected by `plugin-ravn`:

- personas
- ravens
- sessions
- messages
- triggers
- budget

Current reality:

- personas are real
- sessions are stubbed
- rest is missing

Required work:

- create a proper `ravn` route domain
- make Ravn state and session ownership explicit rather than leaking through other services

## Mimir

Expected by `plugin-mimir`:

- a much broader API than the current backend exposes

Current reality:

- current Mimir backend is smaller and mounted at `/mimir`

Required work:

- expand Mimir route surface to match the plugin
- remount under `/api/v1/mimir`
- keep `/mimir` as a compatibility alias during migration

## Observatory

Expected by `plugin-observatory`:

- registry plus two SSE streams

Current reality:

- backend missing

Required work:

- greenfield route domain

## Proposed deployment model

We should not create a separate container per route domain by default.

### Near-term deployables

Recommended target:

- `platform-api`
- `tyr-api`
- `mimir-api`

Optional later:

- `ravn-api`
- `observatory-api`

### What lives in `platform-api`

Mount these route domains in one process:

- `identity`
- `forge`
- `integrations`
- `credentials`
- `tracker`
- `audit`
- maybe `ravn` initially
- maybe `observatory` initially

This keeps operational count low while still restoring code boundaries.

### What stays separate first

- `tyr`
- `mimir`

Reasons:

- clearer independent domain behavior
- Tyr has its own lifecycle and event stream
- Mimir has its own storage and search concerns

## Refactor shape in code

Use route modules as extraction seams.

Suggested package layout:

- `src/platform_api/app.py`
- `src/identity/api.py`
- `src/forge/api.py`
- `src/integrations/api.py`
- `src/credentials/api.py`
- `src/tracker/api.py`
- `src/audit/api.py`
- `src/ravn/api.py`
- `src/mimir/api.py`
- `src/observatory/api.py`
- `src/tyr/api.py`

Each domain should expose:

- a router factory
- dependency wiring
- domain service composition

Do not couple domain routers to a giant host application module.

## Consolidation map from current routes

### Move out of Volundr

Move logically into `identity`:

- `/me`
- `/users`
- `/tenants*`
- feature catalog routes
- PAT/token routes

Move logically into `integrations`:

- integration catalog / CRUD / test
- OAuth flows

Move logically into `credentials`:

- user/tenant credentials
- secret store
- secret types
- MCP server catalog if shared

Move logically into `tracker`:

- tracker issue browsing
- repo mappings
- generic issue APIs

Move logically into `audit`:

- audit query routes

Keep in `forge`:

- sessions
- workspaces
- templates
- presets
- stats
- models
- repos
- chronicle/log/message streams
- cluster resources
- PR/CI flows

### Move out of Tyr

Move logically into `tracker`:

- project browsing
- milestones
- issue import inputs

Move logically into `audit`:

- audit page backing APIs

Keep in `tyr`:

- saga, phase, raid, dispatch, runtime policy, Tyr events

### Keep inside Mimir

Do not split Mimir by feature yet. It is already a coherent domain, just incomplete.

### Build Ravn as a proper bounded context

Do not push Ravn sessions or budgets into Forge or Tyr. Those are Ravn concepts.

## Compatibility strategy

We do not need a flag day.

### Phase 1

- create new route modules with canonical ownership
- mount them in existing processes
- keep legacy Volundr and Tyr paths as compatibility shims

### Phase 2

- update `web-next` service config and adapters to use canonical routes
- stop adding new functionality to legacy route placements

### Phase 3

- remove or deprecate old paths
- split deployables only where runtime pressure justifies it

## Priority implementation order

### Priority 1: boundary repair

- create `identity` route domain
- carve `forge` out of the current Volundr surface conceptually
- create `integrations`, `credentials`, `tracker`, and `audit` route modules even if they remain mounted in the same process

### Priority 2: meet `web-next` contracts

- complete Mimir API surface
- complete Tyr missing endpoints
- build Observatory backend
- build Ravn beyond personas
- add real Forge SSE surfaces

### Priority 3: simplify frontend contracts

- stop teaching `web-next` that identity and tokens are Volundr concerns
- either slim `IVolundrService` or make it an explicit facade over multiple backend domains

## Recommended final stance

We should move from:

- "large named services that happen to own many unrelated routes"

to:

- "clear route domains with a small number of host processes"

That gives us a stable architecture:

- modular in code
- cheap in operations
- compatible with `web-next`
- ready for future extraction where it actually matters
