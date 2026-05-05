# Migration Playbook

## Goal

Move from large overlapping service surfaces to explicit route domains without:

- breaking existing clients
- freezing development for days
- requiring a big-bang deployment
- splitting into too many containers too early

This playbook assumes the target architecture described in
[service-boundaries.md](/Users/jozefvaneenbergen/git/niuu/software/volundr/service-boundaries.md:1).

## Non-negotiable rules

### 1. No flag day

Never combine all of these in one move:

- internal service extraction
- route path changes
- frontend adapter changes
- deployment topology changes

Each migration step should change one axis only.

### 2. Old and new routes must coexist during migration

When a canonical route is introduced, keep the legacy route alive as a shim until:

- `web-next` has switched
- CLI/TUI callers have switched
- logs show the old route is unused

### 3. One implementation, multiple route surfaces

Do not duplicate business logic.

Both of these should call the same service underneath:

- old route
- new canonical route

Example:

- `/api/v1/volundr/me`
- `/api/v1/identity/me`

### 4. Compatibility first, cleanup second

If forced to choose between:

- ideal naming now
- stable rollout now

pick stable rollout now, then clean up after traffic is moved.

## Safety rails

These should land before or alongside the first extraction PRs.

### Contract safety

- Add route-level tests for all currently used JSON shapes.
- Add compatibility tests asserting legacy and canonical routes return identical payloads.
- Add frontend adapter tests for every moved surface.
- Add smoke tests for `niuu` CLI commands that hit migrated APIs.

### Operational safety

- Add deprecation logging on legacy routes.
- Add request counters by route path.
- Add startup logging showing which routers are mounted.
- Add a config flag in `web-next` service config so one domain can be switched independently.

### Change safety

- Keep migrations small enough to review in one sitting.
- Avoid mixing formatting, renaming, and route extraction in the same PR.
- Do not split deployables until the in-process route module is already stable.

## Migration strategy

Use a strangler pattern in four layers.

### Layer 1: internal ownership

Extract service logic into domain-owned modules while keeping existing URLs intact.

This is invisible to clients.

### Layer 2: dual routes

Add canonical routes owned by the new domain router.

Keep the old routes as shims to the same implementation.

### Layer 3: client cutover

Move one client surface at a time:

- `web-next`
- `niuu` CLI
- `niuu` TUI
- any internal automation or plugin clients

### Layer 4: deployment cutover

Only after the route domain is stable:

- optionally split it into its own process
- otherwise keep it mounted in `niuu`

## Recommended execution order

Start with low-risk shared infrastructure, then move into higher-risk realtime and orchestration surfaces.

### Wave 0: prep

This wave creates the safety rails and composition structure.

### Wave 1: identity-adjacent shared domains

- `identity`
- `integrations`
- `credentials`

Why first:

- high boundary value
- relatively low runtime risk
- strong overlap today

### Wave 2: shared operational domains

- `tracker`
- `audit`

Why second:

- clears more overlap between Volundr and Tyr
- still lower risk than session streaming or orchestration

### Wave 3: forge boundary cleanup

- carve `forge` out of Volundr conceptually
- add real SSE surfaces

Why third:

- large impact
- higher compatibility risk
- important to do after shared concerns are removed

### Wave 4: Tyr completion

- fill missing endpoints
- separate Tyr-only settings from shared concerns

### Wave 5: Mimir completion

- expand current backend to match `web-next`

### Wave 6: Ravn and Observatory

- build missing route domains

These are more additive than extractive and can proceed once the host shape is stable.

## Safe first PRs

These are good initial changes because they reduce ambiguity without changing live behavior much.

## PR 1: `niuu` composition root

Goal:

- make `niuu` the explicit host and composition root

Changes:

- create or normalize `src/niuu/app.py`
- keep it thin
- centralize router mounting there
- do not change route paths

Success criteria:

- existing app still boots
- router mounting is easier to reason about
- no client-facing behavior changes

## PR 2: route inventory and deprecation utilities

Goal:

- prepare for dual-route operation

Changes:

- add a small helper for deprecation logging
- add route metrics counters if available
- add test helpers to compare old/new route payloads

Success criteria:

- every future shim can emit a consistent warning
- compatibility tests are easy to write

## PR 3: `identity` service layer extraction

Goal:

- extract shared identity logic from Volundr without changing URLs yet

Changes:

- create `src/identity/` domain service layer
- move shared logic for:
  - `me`
  - tenants
  - users
  - feature catalog
  - tokens
- keep existing Volundr and Niuu routes calling that layer

Success criteria:

- no path changes yet
- tests prove existing routes still behave the same

## PR 4: canonical `identity` routes

Goal:

- introduce new canonical paths

Changes:

- add `/api/v1/identity/*`
- keep legacy routes as shims
- add compatibility tests proving equivalent responses

Success criteria:

- new and old routes both work
- no frontend switch yet

## PR 5: switch `plugin-sdk` and CLI identity callers

Goal:

- move the easiest clients first

Changes:

- point SDK identity adapter to canonical routes
- point feature catalog adapter to canonical routes
- update any `niuu` connection tests or control-plane checks

Success criteria:

- `web-next` works against new paths
- old paths still exist for rollback

## PR 6: `integrations` extraction

Goal:

- separate connector management from Forge

Changes:

- extract service layer
- add canonical `/api/v1/integrations/*`
- keep old Volundr paths as shims

Success criteria:

- no connector behavior changes
- one implementation powers both surfaces

## PR 7: `credentials` extraction

Goal:

- separate credentials and secret store concerns from Forge

Changes:

- extract service layer
- add canonical `/api/v1/credentials/*`
- keep old routes as shims

Success criteria:

- secret and credential flows remain unchanged

## PR 8: `tracker` extraction

Goal:

- stop splitting tracker concerns across Tyr and Volundr

Changes:

- create shared tracker service layer
- canonical `/api/v1/tracker/*`
- shims from both existing route surfaces if needed

Success criteria:

- same tracker payloads
- same import behavior

## PR 9: `audit` extraction

Goal:

- centralize audit queries

Changes:

- create `src/audit/`
- canonical `/api/v1/audit`
- keep existing Volundr audit path as a shim
- later add service filtering for Tyr/Forge/etc.

Success criteria:

- audit queries work from one place
- no UI breakage

## PR 10: Forge service layer cleanup

Goal:

- make the remaining Volundr surface actually be Forge

Changes:

- extract `src/forge/`
- keep existing Volundr paths first
- identify which endpoints remain true Forge responsibilities

Success criteria:

- Volundr no longer feels like a junk drawer internally

## PR 11: Forge canonical routes

Goal:

- add `/api/v1/forge/*`

Changes:

- mount canonical Forge router
- keep `/api/v1/volundr/*` compatibility paths

Success criteria:

- both route families work
- adapters can switch gradually

## PR 12: real Forge SSE surfaces

Goal:

- replace mocked subscriptions with live APIs

Changes:

- session list stream
- stats stream
- message stream
- logs stream
- chronicle stream

Success criteria:

- `web-next` live views work without mocks
- fallback behavior is still safe if stream is unavailable

## PR 13: Tyr completion

Goal:

- close the remaining `web-next` gaps in Tyr

Changes:

- `GET /sagas/{id}/phases`
- dispatch bus facade endpoints
- settings completion
- explicit decision on session approval ownership
- audit hookup if not already routed centrally

Success criteria:

- Tyr plugin no longer relies on missing or ambiguous APIs

## PR 14: Mimir completion

Goal:

- align backend Mimir with plugin expectations

Changes:

- canonical `/api/v1/mimir/*`
- mount registry
- routing rules
- ravn bindings
- entities
- embeddings
- dreams/activity
- ingest split

Success criteria:

- Mimir plugin can run live against real backend routes

## PR 15: Ravn completion

Goal:

- turn Ravn into a real bounded context

Changes:

- ravens
- sessions
- transcripts/messages
- triggers
- budget

Success criteria:

- plugin-ravn can run live, not just personas

## PR 16: Observatory backend

Goal:

- create the missing backend route domain

Changes:

- registry endpoint
- topology SSE
- event SSE

Success criteria:

- plugin-observatory can run live

## How to cut over each domain safely

Use the same pattern every time.

### Step A: extract service layer

- no route changes
- no client changes

### Step B: mount canonical router

- add new path
- keep old path

### Step C: prove equivalence

- route compatibility tests
- client adapter tests

### Step D: switch one client

- update `web-next` config or adapter
- keep rollback easy

### Step E: observe

- watch logs
- watch error rate
- check old-route usage

### Step F: deprecate

- mark old path deprecated
- remove only after no traffic remains

## What not to do

- Do not rename every route family in one PR.
- Do not move files and redesign payloads at the same time.
- Do not switch `web-next` and `niuu` CLI in the same commit unless the change is trivial.
- Do not split a deployable because the code boundary changed.
- Do not delete legacy paths before adding compatibility metrics.

## Suggested test matrix

For each migrated domain:

- legacy path returns expected payload
- canonical path returns expected payload
- legacy path payload equals canonical path payload
- `web-next` adapter works against canonical path
- `niuu` CLI/TUI smoke checks still pass

For realtime domains:

- SSE connects successfully
- initial snapshot behavior is preserved where required
- malformed frames fail safely
- disconnect and reconnect behavior is acceptable

## Rollback plan

Every migration wave should be reversible.

### Route rollback

- if canonical routes fail, switch clients back to legacy paths
- leave shared implementation in place

### Client rollback

- revert only service config or adapter wiring
- keep server dual-routes intact

### Deployment rollback

- keep the route domain mounted in `niuu`
- postpone process separation

## Recommended first 30 days

### Week 1

- PR 1: `niuu` composition root
- PR 2: deprecation and compatibility test utilities

### Week 2

- PR 3: identity service extraction
- PR 4: canonical identity routes

### Week 3

- PR 5: switch SDK and CLI identity callers
- PR 6: integrations extraction

### Week 4

- PR 7: credentials extraction
- PR 8: tracker extraction

This yields a lot of architectural benefit without touching the highest-risk streaming and orchestration paths yet.

## Definition of done

A route domain is only considered migrated when:

- ownership is explicit in code
- canonical routes exist
- legacy routes are shims, not separate implementations
- at least one client has moved
- compatibility tests exist
- rollback is straightforward

That is how we avoid "break everything for days and hope."
