API Error: Stream idle timeout - partial response received

Understood — delivering in chat. Here it is.

---

# Niuu Platform API SDD — Gap Analysis & Implementation Plan

**Source of truth:** `web-next/packages/plugin-*` (required contracts) vs. `src/*` (existing backend).
**Goal:** turn Volundr's monolithic REST into a plugin-aligned, multi-service API surface that the Niuu web shell consumes.
**Status of this doc:** plugin-grouped gap analysis + per-plugin SDD skeleton. Flesh out each plugin section into a full SDD ticket.

---

## 0. Cross-cutting foundations

Applies to every plugin. Fix once, not per plugin.

### 0.1 Base URL routing

web-next expects each service mounted at `/api/v1/<service>`:

| Service | Expected prefix | Current prefix |
|---|---|---|
| tyr | `/api/v1/tyr` | `/api/v1/tyr` ✅ |
| volundr | `/api/v1/volundr` | `/api/v1/volundr` ✅ |
| ravn | `/api/v1/ravn` | `/api/v1/ravn` (only `personas.py`) ⚠️ |
| mimir | `/api/v1/mimir` | `/mimir` ❌ |
| observatory | `/api/v1/observatory` | — (no service) ❌ |
| hello | `/api/v1/hello` | — (no service) ❌ |

**Gap:** Mimir prefix mismatch. No observatory/hello service. No edge gateway: services must be individually reachable (or a BFF/Envoy route map must be added). Bifrost is an LLM proxy, not a niuu gateway — do **not** repurpose it.

**Action:**
- Rewrite Mimir mount from `/mimir` → `/api/v1/mimir` (`src/mimir/app.py:128`, `router.py:11`).
- Decide gateway strategy: either (a) Envoy `route_config` maps `/api/v1/<svc>` → upstream, or (b) a thin niuu BFF. Pick (a); document in an infra SDD.

### 0.2 Wire format: snake_case

Every plugin adapter transforms snake_case ↔ camelCase at the adapter boundary. Servers MUST emit snake_case JSON; responses MUST be stable field names. No nested `dict[str, Any]` without a Pydantic model. Already consistent in existing backends — lock it in a style rule.

### 0.3 Auth

web-next expects OIDC bearer token injected via `@niuulabs/query`'s `setTokenProvider`; `auth` package uses `oidc-client-ts` against `config.auth.issuer` + `config.auth.clientId`.

Backend reality:
- Envoy sidecar JWT validation (OIDC) — primary path.
- PAT via `niuu.adapters.inbound.rest_pats` mounted inside services.
- No backend endpoint exposes `/.well-known/openid-configuration`; the IDP does that.

**Gaps:**
- No `GET /identity` endpoint for the SDK's `IIdentityService` contract — web needs `{userId, email, displayName, roles, tenants}`. Volundr has `/me` (`rest_tenants.py:163`) — promote to canonical identity endpoint mounted at a stable path the shell can hit *without* knowing which service owns it.
- PAT management currently routed via tyr. For Niuu this should be per-service mount OR centralised on whichever service is "the identity service" (likely Volundr).

**Action:** define one canonical `GET /api/v1/identity/me` endpoint (ownership TBD — proposal: Volundr, since it already owns tenants/users), and pull PATs under `/api/v1/identity/tokens`.

### 0.4 Streaming: SSE only

web-next `query.openEventStream` is pure SSE (text/event-stream). Every real-time surface MUST be SSE, no WS.

| Stream | Expected | Current |
|---|---|---|
| tyr raid/phase/saga events | `GET /api/v1/tyr/events` | ✅ (`src/tyr/api/events.py:69`) |
| volundr sessions/stats/messages/logs/chronicle | per-resource SSE | ❌ HTTP adapter currently **mocks** subscriptions; only `sessions/stream` exists (`rest.py`) |
| observatory topology + events | `GET /api/v1/observatory/topology/stream`, `/events/stream` | ❌ |
| ravn ravens/sessions/budget | SSE | ❌ |
| mimir activity/dreams | SSE (activity log) | ❌ |

### 0.5 Error contract

web-next `ApiClientError` expects `{status, detail}` body on non-2xx. Confirmed compatible with FastAPI defaults. Add a shared `ErrorResponse` model (`detail: str`, optional `code: str`, optional `fields: dict`) and ensure every service uses `HTTPException(detail=...)` consistently.

### 0.6 Feature Catalog

`plugin-sdk.IFeatureCatalogService` hits `/features/modules`, `/features/preferences`, `/features/modules/{key}/toggle`. Volundr already has these (`rest_features.py:92–152`). They are volundr-local; the web SDK expects them as platform-level. Either:

- **Option A**: promote the feature-catalog out of `volundr` into a shared mount (part of the identity service).
- **Option B**: point the SDK adapter at volundr's base URL; document it.

Pick A for the SDD; it keeps the SDK plugin-agnostic.

---

## 1. `plugin-login` + `@niuulabs/auth`

### UI routes
`/login`, `/login/callback`.

### Expected contracts
OIDC discovery only. Nothing custom backend-side. Tokens are fetched directly from the IDP (Keycloak/Entra/Okta); backend only validates them.

### Current state
- Envoy validates. Niuu has PAT (`src/niuu/`).
- Volundr has a legacy `/auth/config` (`rest.py:908`) that exposes OIDC discovery URL / client id — **this is essentially runtime config** and overlaps with `public/config.json`.

### Gaps
- None **strictly backend-side** for OIDC itself.
- `auth/config` endpoint is duplicated with `config.json` — pick one. Recommend keeping `config.json` (zero-backend, browser-fetchable) and deprecating `/auth/config`.

### Action items
- [ ] Confirm Envoy config has the correct JWKS URL + audience for the niuu client.
- [ ] Remove `/auth/config` or reduce it to a health-only probe.
- [ ] Wire `query.setTokenProvider(() => oidc.getAccessToken())` in `apps/niuu/src/services.ts` on boot.

---

## 2. `@niuulabs/plugin-sdk` (Identity + Feature Catalog)

### Expected contracts

**`IIdentityService`**

| Method | HTTP |
|---|---|
| `getIdentity()` | `GET /api/v1/identity/me` |

**`IFeatureCatalogService`**

| Method | HTTP |
|---|---|
| `getFeatureModules(scope?)` | `GET /api/v1/identity/features/modules?scope={scope}` |
| `getUserFeaturePreferences()` | `GET /api/v1/identity/features/preferences` |
| `updateUserFeaturePreferences(prefs)` | `PUT /api/v1/identity/features/preferences` |
| `toggleFeature(key, enabled)` | `POST /api/v1/identity/features/modules/{key}/toggle` |

### Current state
- `/me` exists in volundr (`rest_tenants.py:163`).
- `/features*` exist in volundr (`rest_features.py:92–152`) but under `/api/v1/volundr/features/...`.

### Gaps
| Gap | Fix |
|---|---|
| No canonical `/api/v1/identity` prefix | Add identity router (move `/me`, features, PATs under it). |
| `toggleFeature` uses `PUT` in backend, `POST` in web-next port | Align: standardise on `POST /features/modules/{key}/toggle`. |
| `preferences` shapes may differ | Verify Pydantic model matches `UserFeaturePreference` fields; add schema contract test. |

### SDD work items
- [ ] **NIU-IDP-001** Create `src/volundr/adapters/inbound/rest_identity.py` with `APIRouter(prefix="/api/v1/identity")`. Move `/me`, PATs, feature routes; keep old routes as deprecated shims for one release.
- [ ] **NIU-IDP-002** Align `toggleFeature` HTTP verb.
- [ ] **NIU-IDP-003** Pydantic → TS contract test (schemathesis or hand-rolled) to catch snake_case drift.

---

## 3. `plugin-hello`

### UI routes
`/hello`, `/hello/status-showcase`, `/hello/form-showcase`, `/overlays`.

### Expected contracts
| Method | HTTP |
|---|---|
| `IHelloService.listGreetings()` | `GET /api/v1/hello/greetings` |

### Current state
No backend. Plugin runs against a mock adapter in the app.

### Gaps / Decision
Smoke-test plugin. No production backend needed. Keep mock-only; document in plugin README. **No SDD ticket.**

---

## 4. `plugin-observatory`

### UI routes
`/observatory`, `/observatory/registry`.

### Expected contracts

| Port method | HTTP |
|---|---|
| `IRegistryRepository.getRegistry()` | `GET /api/v1/observatory/registry` |
| `ILiveTopologyStream.subscribe()` | `GET /api/v1/observatory/topology/stream` (SSE) |
| `IEventStream.subscribe()` | `GET /api/v1/observatory/events/stream` (SSE) |

### Domain (must round-trip)
`Registry`, `Topology{nodes,edges,timestamp}`, `TopologyNode{id,title,category,status,activity,...}`, `TopologyEdge{source,target,kind,strength}`, `ObservatoryEvent`.

### Current state
**No backend service exists.** Skuld (`src/skuld/`) already consumes the sleipnir bus and has event mappers — it is the natural owner.

### Gaps
Everything. This is the biggest greenfield in the plan.

### SDD work items
- [ ] **NIU-OBS-001** New service `src/observatory/` (FastAPI). Ownership: extends Skuld or stands alone and subscribes to sleipnir.
- [ ] **NIU-OBS-002** Registry store — on-disk YAML or Postgres table. CRUD later; start with `GET`.
- [ ] **NIU-OBS-003** Topology projector — subscribes to bus events (raid/phase/saga lifecycle, ravn heartbeats, bifrost cost), folds into a `Topology` snapshot in memory, broadcasts deltas via SSE.
- [ ] **NIU-OBS-004** Events stream — firehose of `ObservatoryEvent`s (filterable later).
- [ ] **NIU-OBS-005** Define node/edge taxonomy with Mimir (entity types).
- [ ] **NIU-OBS-006** Helm chart + migrations configmap.

### Open questions
- Should topology be authoritative or derived? (Start derived from bus + registry.)
- Per-tenant scope? (Yes — filter events by `tenant_id` claim.)

---

## 5. `plugin-mimir`

### UI routes
`/mimir`, `/mimir/pages`, `/mimir/sources`, `/mimir/search`, `/mimir/graph`, `/mimir/ravns`, `/mimir/ingest`, `/mimir/lint`, `/mimir/dreams`.

### Expected contracts (grouped)

**IPageStore**
| Method | HTTP |
|---|---|
| `getStats()` | `GET /stats` |
| `listPages({mount?,category?})` | `GET /pages?mount&category` |
| `getPage(path, mount?)` | `GET /page?path&mount` |
| `upsertPage(path,content,mount?)` | `PUT /page` |
| `search(q, mode)` | `GET /search?q&mode=fts|semantic|hybrid` |
| `listSources({origin_type?,mount?})` | `GET /sources?origin_type&mount` |
| `getPageSources(path)` | `GET /page/sources?path` |
| `ingestUrl(url)` | `POST /sources/ingest/url` |
| `ingestFile(file)` | `POST /sources/ingest/file` (multipart) |
| `getGraph({mount?})` | `GET /graph?mount` |
| `listEntities({kind?})` | `GET /entities?kind` |

**IMountAdapter**
| Method | HTTP |
|---|---|
| `listMounts()` | `GET /mounts` |
| `listRoutingRules()` | `GET /routing/rules` |
| `upsertRoutingRule(r)` | `PUT /routing/rules/{id}` |
| `deleteRoutingRule(id)` | `DELETE /routing/rules/{id}` |
| `listRavnBindings()` | `GET /ravns/bindings` |
| `getRecentWrites(limit?)` | `GET /mounts/recent-writes?limit` |

**IEmbeddingStore**
| Method | HTTP |
|---|---|
| `semanticSearch(q, topK, mount?)` | `GET /embeddings/search?q&top_k&mount` |

**ILintEngine**
| Method | HTTP |
|---|---|
| `getLintReport(mount?)` | `GET /lint?mount` |
| `runAutoFix(issueIds?)` | `POST /lint/fix` |
| `reassignIssues(ids, assignee)` | `POST /lint/reassign` |
| `getDreamCycles(limit?)` | `GET /dreams?limit` |
| `getActivityLog(limit?)` | `GET /activity?limit` |

### Current state (`src/mimir/router.py`)
Has: `GET /stats`, `GET /pages`, `GET /page`, `GET /search`, `GET /log`, `GET /lint`, `POST /lint/fix`, `GET /graph`, `GET /source`, `GET /sources`, `PUT /page`, `POST /ingest`.

Mounted at `/mimir`, not `/api/v1/mimir`. No mount concept exposed. No semantic-search route. No multi-mount routing rules. No activity/dream cycles. No `/entities`. No `recent-writes`. No `/ravns/bindings`. Ingest is one-shot (`POST /ingest`) not url/file split.

### Gap table

| Contract | Status | Action |
|---|---|---|
| Prefix `/api/v1/mimir` | ❌ | Rewrite mount; deprecate `/mimir`. |
| `GET /stats`, `/page`, `/pages`, `/search`, `/graph`, `/lint`, `POST /lint/fix`, `PUT /page` | ✅ | Verify response schemas match `PageMeta`, `Page`, `SearchResult`, `LintReport` field-for-field. |
| `search?mode=semantic|hybrid` | ❌ (FTS-only) | Add mode param, wire embedding backend or hybrid fan-out. |
| `GET /embeddings/search` | ❌ | New endpoint. |
| `POST /sources/ingest/url`, `POST /sources/ingest/file` | ❌ (one `/ingest`) | Split into url/file + multipart handler. |
| `GET /page/sources?path` | ❌ | New — reverse lookup sources for a page. |
| `GET /sources?origin_type&mount` filter | partial | Add query param support. |
| `GET /entities?kind` | ❌ | New — extract from page frontmatter. |
| `GET /mounts`, `GET /mounts/recent-writes` | ❌ | Need a mount-registry domain model. |
| `GET|PUT|DELETE /routing/rules` | ❌ | Write-routing subsystem; greenfield. |
| `GET /ravns/bindings` | ❌ | New — ravn↔mount binding inspector. |
| `POST /lint/reassign` | ❌ | New. |
| `GET /dreams`, `GET /activity` | ❌ | Activity stream + dream cycle log. |

### SDD work items
- [ ] **NIU-MIM-001** Mount rewrite + `/api/v1/mimir`.
- [ ] **NIU-MIM-002** Mount registry domain + `GET /mounts`, `recent-writes`.
- [ ] **NIU-MIM-003** Write-routing rules CRUD + executor.
- [ ] **NIU-MIM-004** Semantic/hybrid search + `/embeddings/search` (embedding adapter port).
- [ ] **NIU-MIM-005** Ingest split (url/file multipart). Migrate callers.
- [ ] **NIU-MIM-006** Entities endpoint.
- [ ] **NIU-MIM-007** Lint reassign + dream cycle + activity log.
- [ ] **NIU-MIM-008** Schema contract tests against `plugin-mimir/src/domain/*`.

---

## 6. `plugin-ravn`

### UI routes
`/ravn`, `/ravn/ravens`, `/ravn/personas`, `/ravn/sessions`, `/ravn/budget`.

### Expected contracts

**IPersonaStore**
| Method | HTTP |
|---|---|
| `listPersonas({source?})` | `GET /personas?source=all|builtin|custom` |
| `getPersona(name)` | `GET /personas/{name}` |
| `getPersonaYaml(name)` | `GET /personas/{name}/yaml` |
| `createPersona(req)` | `POST /personas` |
| `updatePersona(name, req)` | `PUT /personas/{name}` |
| `deletePersona(name)` | `DELETE /personas/{name}` |
| `forkPersona(name, req)` | `POST /personas/{name}/fork` |

**IRavenStream**
| Method | HTTP |
|---|---|
| `listRavens()` | `GET /ravens` |
| `getRaven(id)` | `GET /ravens/{id}` |

**ISessionStream**
| Method | HTTP |
|---|---|
| `listSessions()` | `GET /sessions` |
| `getSession(id)` | `GET /sessions/{id}` |
| `getMessages(sessionId)` | `GET /sessions/{id}/messages` |

**ITriggerStore**
| Method | HTTP |
|---|---|
| `listTriggers()` | `GET /triggers` |
| `createTrigger(t)` | `POST /triggers` |
| `deleteTrigger(id)` | `DELETE /triggers/{id}` |

**IBudgetStream**
| Method | HTTP |
|---|---|
| `getBudget(ravnId)` | `GET /budget/{ravnId}` |
| `getFleetBudget()` | `GET /budget/fleet` |

### Current state (`src/ravn/api/personas.py`)
Has personas CRUD + fork (`GET list`, `GET {name}`, `GET {name}/yaml`, `POST`, `PUT`, `DELETE`, `POST fork`). No fleet, sessions, messages, triggers, or budget endpoints. No SSE. Ravn today is a TUI-first CLI.

### Gap table

| Contract | Status | Action |
|---|---|---|
| `/personas*` full CRUD + fork + yaml | ✅ | Verify response shape vs `PersonaDetail` (llm, produces, consumes, fan_in fields). |
| `GET /ravens`, `GET /ravens/{id}` | ❌ | New — expose fleet registry. Requires ravn process registry. |
| `GET /sessions`, `{id}`, `{id}/messages` | ❌ | New — session store (likely Postgres). |
| `GET /triggers`, `POST`, `DELETE` | ❌ | Trigger subsystem. |
| `GET /budget/{ravnId}`, `/budget/fleet` | ❌ | Bifrost already tracks usage per agent — expose as ravn budget projection. |
| SSE for raven/session/budget | ❌ | Add once REST shapes are frozen. |

### SDD work items
- [ ] **NIU-RAVN-001** Ravn fleet registry (Postgres `ravens` table; rows = ravn process instances). Add `GET /ravens{,/{id}}`.
- [ ] **NIU-RAVN-002** Session store (if not already persisted in tyr). Add `GET /sessions{,/{id}/messages}`.
- [ ] **NIU-RAVN-003** Trigger store + evaluator.
- [ ] **NIU-RAVN-004** Budget projection from Bifrost usage (`/v1/usage` upstream) — aggregated per ravn/fleet.
- [ ] **NIU-RAVN-005** Persona response schema audit (`llm.temperature?`, `fan_in.params`, etc.).

### Open questions
- Is "session" in plugin-ravn the same entity as volundr/tyr session, or a ravn-specific chat session? **Likely the latter** (persona-driven chats); needs a ravn-owned table. Confirm with UX.

---

## 7. `plugin-volundr`

### UI routes
`/volundr`, `/sessions`, `/history`, `/clusters`, `/templates`, `/forge`, `/credentials`.

### Expected contracts (summary; ~90 methods grouped)

- **Features & models:** `/features`, `/models`, `/repos`, `/stats`
- **Sessions:** `/sessions`, `/sessions/{id}`, `/sessions/archived`, `/sessions/{id}/{stop,resume,archive,restore,messages,logs,chronicle,code-server-url,pr}`, `/sessions/connect`
- **Templates/Presets:** `/templates{,/{name}}`, `/presets{,/{id}}`
- **Cluster/MCP:** `/mcp-servers`, `/secrets`, `/cluster/resources`, `/sessions/{id}/mcp-servers`
- **PRs:** `/repos/prs`, `/repos/prs/{n}/{merge,ci}`
- **Tracker:** `/tracker/issues`, `/tracker/repo-mappings`, `PATCH /tracker/issues/{id}`
- **Identity/Users:** `/identity`, `/admin/users`
- **Tenants:** `/tenants{,/{id}{,/members,/reprovision}}`
- **Credentials:** `/credentials/{user,tenant}{,/{name}}`, `/secrets/store{,/{name}}`, `/secrets/types`
- **Integrations:** `/integrations/catalog`, `/integrations{,/{id}{,/test}}`
- **Workspaces:** `/workspaces{,/{id}{,/restore}}`, `/admin/workspaces`, `/workspaces/bulk-delete`
- **Admin:** `/admin/settings`
- **Features modules:** `/features/modules{,/{key}/toggle}`, `/features/preferences`
- **Tokens:** `/tokens{,/{id}}`
- **SSE:** per-resource streams (sessions/stats/messages/logs/chronicle) — currently mocked client-side

### Current state (`src/volundr/adapters/inbound/rest*.py`)
**Most extensive backend we have.** The existing `web/` consumes it already. Per-file breakdown (all under `/api/v1/volundr` unless noted):

- `rest.py` — sessions CRUD, messages, models, stats, chronicles, PRs, feature-flags, auth/config.
- `rest_tenants.py` — `/me`, `/users`, `/tenants*`, member management, reprovision.
- `rest_admin_settings.py` — `/admin/settings`.
- `rest_features.py` — feature modules + preferences (verb mismatch, see §2).
- `rest_credentials.py` — user + tenant credentials.
- `rest_secrets.py` — secret store.
- `rest_integrations.py` — integration connections + test.
- `rest_oauth.py` — `/integrations/oauth/*`.
- `rest_tracker.py` — tracker projects/issues/mappings.
- `rest_issues.py` — issues CRUD (scoped `/issues`).
- `rest_pats.py` — PATs (via niuu).
- `rest_presets.py`, `rest_profiles.py`, `rest_prompts.py`, `rest_resources.py`, `rest_templates` — supporting stores.
- `rest_events.py` — event ingest + `/events/health`.
- `rest_webhooks.py` — webhooks at `/api/v1/webhooks`.
- `rest_audit.py` — `/audit/*`.
- `rest_local_git.py`, `rest_git.py` — git ops.

### Gap table

| Contract | Status | Action |
|---|---|---|
| Sessions CRUD core | ✅ | Field-for-field audit vs `VolundrSession`. |
| `POST /sessions/connect` | ❓ | Confirm exists (connect to external cluster flow). |
| `GET /sessions/{id}/code-server-url` | ❓ | Confirm — used by workspace forge. |
| `GET /sessions/archived` | ❓ | Currently `GET /sessions?archived=true`? Align. |
| `POST /sessions/{id}/archive`, `/restore` | ❓ | Verify (web uses them). |
| `POST /workspaces/bulk-delete` | ✅ | In `rest.py` + admin/workspaces. |
| `GET /templates` CRUD | ✅ | Verify `saveTemplate` POST path. |
| `GET /presets` CRUD | ✅ | Verify. |
| `GET /cluster/resources` | ❓ | May be under `/resources` — align path to `/cluster/resources`. |
| `GET /mcp-servers`, `/sessions/{id}/mcp-servers` | ✅ | Verify. |
| `/secrets*` (types, store) | ✅ | Verify. |
| `/credentials/{user,tenant}` | ✅ | Verify. |
| `/integrations/catalog`, `/integrations`, `/test` | ✅ | Verify. |
| `/tenants*` full lifecycle | ✅ | Verify reprovision return type. |
| `/admin/settings` | ✅ | Verify payload. |
| `/features/modules` etc. | ⚠️ | Verb alignment; see §2. |
| `/tokens*` (PATs) | ✅ (via niuu) | Path: plugin expects `/tokens`, backend exposes `/api/v1/pats`. **Gap.** |
| SSE per-resource streams | ❌ | Add `GET /sessions/stream`, `/sessions/{id}/messages/stream`, `/sessions/{id}/logs/stream`, `/sessions/{id}/chronicle/stream`, `/stats/stream`. Currently web adapter mocks these. |
| `GET /repos/prs/{n}/ci?url&branch` | ✅ | Verify response shape `CIStatusValue`. |
| `POST /repos/prs/{n}/merge` body `{repoUrl, mergeMethod}` | ✅ | Verify body fields snake_case. |
| `PATCH /tracker/issues/{id}` status | ✅ | Verify. |
| `/identity` | ⚠️ | Web expects `/identity`; backend has `/me`. See §2 (promote to `/api/v1/identity/me`). |

### SDD work items
- [ ] **NIU-VOL-001** Path reconciliation pass: one PR per path mismatch (tokens, identity, archived, code-server-url, cluster-resources).
- [ ] **NIU-VOL-002** SSE endpoints for sessions/messages/logs/stats/chronicle. Replace mock subscriber with real `EventSource`.
- [ ] **NIU-VOL-003** Feature module verb + payload alignment.
- [ ] **NIU-VOL-004** Integration OAuth flow docs — `/integrations/oauth/{github,callback}` surface to plugin catalog.
- [ ] **NIU-VOL-005** Audit router — not currently referenced by plugin-volundr, but `tyr` audit is. Unify?
- [ ] **NIU-VOL-006** Contract tests: generate OpenAPI, diff vs `plugin-volundr/src/ports/*`.

### Open questions
- Do `/api/v1/volundr/tokens` and `/api/v1/pats` co-exist or merge? Decide per §2.
- Do we keep volundr as the identity/tenants owner long-term, or spin `identity-service` out?

---

## 8. `plugin-tyr`

### UI routes
`/tyr`, `/sagas{,/$sagaId}`, `/dispatch`, `/plan`, `/workflows`, `/settings/{general,dispatch,integrations,personas,gates,flock,notifications,advanced,audit}`.

### Expected contracts

**ITyrService**
| Method | HTTP |
|---|---|
| `getSagas` | `GET /sagas` |
| `getSaga(id)` | `GET /sagas/{id}` |
| `getPhases(sagaId)` | `GET /sagas/{id}/phases` |
| `createSaga({spec,repo})` | `POST /sagas` |
| `commitSaga(req)` | `POST /sagas/commit` |
| `decompose({spec,repo})` | `POST /sagas/decompose` |
| `spawnPlanSession({spec,repo})` | `POST /sagas/plan` |
| `extractStructure({text})` | `POST /sagas/extract-structure` |

**IDispatcherService**
| Method | HTTP |
|---|---|
| `getState` | `GET /dispatcher` |
| `setRunning`, `setThreshold`, `setAutoContinue` | `PATCH /dispatcher` |
| `getLog` | `GET /dispatcher/log` |

**ITyrSessionService** → `GET /sessions{,/{id}}`, `POST /sessions/{id}/approve`
**ITrackerBrowserService** → `GET /tracker/projects{,/{id}{,/milestones,/issues}}`, `POST /tracker/import`
**ITyrIntegrationService** → `GET/POST/DELETE/PATCH /integrations{,/{id}{,/test}}`, `GET /integrations/telegram/setup`
**IDispatchBus** (sleipnir flavour) → `POST /dispatch/{raidId}`, `POST /dispatch/batch`
**ITyrSettingsService** → `GET/PATCH /settings/{flock,dispatch,notifications}`
**IAuditLogService** → `GET /audit?kinds&actor&since&until&limit`

### Current state (`src/tyr/api/*`)
- `sagas.py` — `GET`, `GET /{id}` (`SagaDetailResponse`), `POST /decompose`, `POST /commit`, `POST /plan`, `POST /extract-structure`, `PATCH /{id}`, `DELETE /{id}`, `GET /plan/config`. **Missing: `POST /sagas` ({spec,repo}), `GET /sagas/{id}/phases`.** The web port's `createSaga(spec,repo)` maps to `decompose`+`commit`; may or may not want a direct POST.
- `raids.py` — summary, active, review, approve, reject, retry, message, messages. (Not in the web port's surface directly; used by dispatch/review flows and possibly consumed indirectly.)
- `dispatch.py` — `/config`, `/queue`, `/approve`, `/clusters`. **`/dispatch/batch` and `/dispatch/{raidId}` not present — these are the IDispatchBus (sleipnir) contract.**
- `dispatcher.py` — `GET`, `PATCH`, `/log`. ✅
- `events.py` — SSE. ✅
- `tracker.py` — projects, milestones, issues, import. ✅
- `flock_config.py`, `flock_flows.py` — ✅ (settings coverage).
- `pipelines.py` — pipeline creation (not in web port surface; keep).
- `integrations` mounted via niuu — ✅
- `telegram` setup/webhook — ✅

### Gap table

| Contract | Status | Action |
|---|---|---|
| `GET /sagas/{id}/phases` | ❌ | Add dedicated endpoint (currently phases only via `/sagas/{id}` detail). |
| `POST /sagas` with `{spec, repo}` | ❌ | Decide: thin wrapper calling decompose→commit, or defer to web doing it. |
| `ITyrSessionService` (sessions + approve) | ❌ | Tyr has raid-level approve but no session-list endpoint. Either (a) alias to raids, (b) add `GET /sessions` projecting active sessions. |
| `IDispatchBus` `/dispatch/{raidId}`, `/dispatch/batch` | ❌ | These target sleipnir. Needs a thin REST front or relocation into tyr dispatch API. Decide ownership. |
| `GET/PATCH /settings/{flock,dispatch,notifications}` | ⚠️ | Flock config present (`/flock/config`). **Dispatch defaults and notifications not persisted** — dispatcher state covers runtime flags but not "defaults" config. Notifications channel settings absent. |
| `GET /audit` | ❌ (in tyr) | Volundr has `/audit`; tyr UI audit page wants tyr-scoped audit. Unify or proxy. |
| `POST /sessions/{id}/approve` | ❌ | Currently raid-approve; session-approve semantics differ. Clarify UX → either alias or add. |

### SDD work items
- [ ] **NIU-TYR-001** Add `GET /sagas/{id}/phases`.
- [ ] **NIU-TYR-002** Decide `POST /sagas` semantics; implement or remove from port.
- [ ] **NIU-TYR-003** Settings surface: add `dispatch` and `notifications` persisted configs (new tables), expose under `/settings/{dispatch,notifications}`. Alias existing flock routes under `/settings/flock`.
- [ ] **NIU-TYR-004** Dispatch bus front: add `/dispatch/{raidId}` and `/dispatch/batch` to tyr (REST facade over sleipnir publisher) or document that web will call sleipnir directly via its own service.
- [ ] **NIU-TYR-005** Audit: choose between shared audit store or per-service. Expose `/audit?kinds&actor&since&until&limit` on tyr (or on identity service with service filter).
- [ ] **NIU-TYR-006** Session service: resolve whether `ITyrSessionService` is tyr-native or should live on volundr (volundr owns sessions today). **Recommend**: repoint port to volundr and delete from tyr surface.
- [ ] **NIU-TYR-007** Schema contract test generation.

### Open questions
- `ITyrSessionService` ownership — keep in tyr port or move to volundr? Moving is cleaner; tyr already exposes raid-level approval.
- `IDispatchBus` — is this a UI trigger (nice) or an ops-only surface (move to CLI)?

---

## 9. Service ownership matrix (proposed target state)

| Plugin | Service owner | Base URL |
|---|---|---|
| login | (IDP, external) | — |
| sdk (identity) | identity-service (host=volundr) | `/api/v1/identity` |
| hello | mock only | — |
| observatory | new `observatory` (or skuld-hosted) | `/api/v1/observatory` |
| mimir | mimir | `/api/v1/mimir` |
| ravn | ravn (new HTTP surface) | `/api/v1/ravn` |
| volundr | volundr | `/api/v1/volundr` |
| tyr | tyr | `/api/v1/tyr` |

---

## 10. Phased rollout

**Phase 1 — Alignment (low risk, high leverage)**
Goal: ship web-next against existing backends where shape is 90% right.
1. Mimir mount rewrite `/api/v1/mimir`.
2. Identity facade + `/me` move (§2).
3. Feature-catalog verb alignment.
4. PAT path alignment (`/tokens` vs `/pats`).
5. Tyr `GET /sagas/{id}/phases`.
6. Contract-test scaffolding (OpenAPI-diff against plugin ports).

**Phase 2 — SSE everywhere**
1. Volundr per-resource SSE (§7 NIU-VOL-002).
2. Observatory SSE endpoints (§4 NIU-OBS-003).
3. Ravn/mimir activity streams.

**Phase 3 — Greenfield services**
1. Observatory service + registry (§4).
2. Ravn HTTP surface (fleet, sessions, triggers, budget) (§6).
3. Mimir mounts/routing/entities/activity (§5 NIU-MIM-002–007).

**Phase 4 — Settings & audit consolidation**
1. Tyr settings persistence (dispatch/notifications).
2. Unified audit approach.

**Phase 5 — Contract hardening**
1. Auto-generate TS ports from OpenAPI in CI; fail build on drift.
2. Delete `/mimir` and `/me` deprecations.

---

## 11. Conventions to lock in (style rules)

- **Wire format:** snake_case JSON, always.
- **Errors:** `HTTPException(detail=str)`; shared `ErrorResponse` model optional.
- **Pagination:** cursor-based (`?cursor=&limit=`) for any list > 200 rows; not needed yet.
- **Streaming:** SSE only; event framing `event: <kind>\ndata: <json>\n\n`; heartbeat `: keepalive\n\n` every 15s.
- **IDs:** ULID strings (already used); never raw integers over the wire.
- **Routers:** one file per resource; prefix defined at router creation; `tags=[...]` required.
- **Tests:** every new endpoint ships with (a) Pydantic round-trip test, (b) TS port contract test.

---

## 12. Immediate next actions (≤1 week)

1. Agree on the identity-facade location (§2) — unblocks almost everything.
2. Land the Mimir prefix rewrite — cheap and removes a whole-class of drift.
3. Decide observatory service ownership (new service vs skuld extension).
4. Set up `web-next` ↔ backend OpenAPI contract test harness so subsequent changes self-police.
5. Walk through each `## N` section above, promote to dedicated SDDs (one per plugin).

---

That's the analysis. Copy/paste into a Linear ticket or drop it into `docs/plans/niuu-api-sdd.md`; each `### SDD work items` block is a ready-to-split ticket list. Let me know which plugin you want to expand first into a full SDD.