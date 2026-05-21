# Mimir Warden UI Plan

## Goal

Allow operators to create, launch, inspect, and dispatch a long-lived local
Ravn "warden" from the Mimir Wardens UI, while keeping `ravn` as the primary
runtime CLI.

The desired operator story is:

1. Open `/mimir/ravns`.
2. Create a warden with attached Mimir mounts and a default persona/profile.
3. Launch it locally so it survives restarts.
4. See it register in the same Wardens view with live status, dream-cycle
   history, wakefulness state, and dispatch controls.
5. Use `ravn warden ...` on the command line for the same lifecycle.

## What Exists Today

### Frontend

The current Mimir Wardens UI already exists:

- Route registration: `web-next/packages/plugin-mimir/src/index.tsx`
  - `/mimir/ravns`
  - tab label: `Wardens`
- Main page: `web-next/packages/plugin-mimir/src/ui/RavnsPage.tsx`
- Overview cards: `web-next/packages/plugin-mimir/src/ui/OverviewView.tsx`
- Topbar count: `web-next/packages/plugin-mimir/src/ui/MimirTopbar.tsx`
- Sidebar roster: `web-next/packages/plugin-mimir/src/ui/MimirSubnav.tsx`
- Query hook: `web-next/packages/plugin-mimir/src/application/useRavns.ts`

The frontend currently treats "wardens" as `RavnBinding[]`, not as a live
runtime resource.

### Mimir backend

The current backend contract is read-only:

- Response model: `src/mimir/router.py` → `RavnBindingResponse`
- Route: `GET /mimir/ravns/bindings`
- Implementation: returns `adapter._http_ravn_bindings` when present,
  otherwise `[]`

This means the current Wardens screen is a passive projection supplied by the
Mimir service itself. There is no create/start/stop/install path.

### Ravn backend

Ravn already exposes adjacent runtime surfaces:

- `GET /api/v1/ravn/ravens`
- `GET /api/v1/ravn/ravens/{ravn_id}`
- `GET /api/v1/ravn/sessions`
- `GET /api/v1/ravn/triggers`
- `POST /api/v1/ravn/triggers`
- `GET /api/v1/ravn/budget/*`
- persona CRUD under `/api/v1/ravn/personas`

Files:

- `src/ravn/api/__init__.py`
- `src/ravn/api/personas.py`
- `web-next/packages/plugin-ravn/src/adapters/http.ts`

These routes are currently stub/runtime-data driven, but they already define
the right service boundary: runtime lifecycle belongs on the Ravn side, not in
the Mimir page components.

### Long-lived runtime pieces already present

The autonomous daemon path already exists in Ravn:

- `ravn daemon` startup: `src/ravn/cli/commands.py`
- persistent initiative queue: `src/ravn/drive_loop.py`
- wakefulness trigger: `src/ravn/adapters/triggers/wakefulness.py`
- dream cycle trigger: `src/ravn/adapters/triggers/dream_cycle.py`
- thread queue / enrichment: `src/ravn/adapters/triggers/thread_queue.py`,
  `src/ravn/adapters/triggers/thread_enricher.py`
- persistent cron jobs: `src/ravn/adapters/triggers/cron.py`

This is the substrate a warden should launch, not a new parallel agent runner.

## Key Gap

Today the Mimir Wardens UI and the Ravn runtime are not the same thing:

- Mimir Wardens UI is a read-only binding list.
- Ravn runtime API is separate and not warden-aware.
- There is no canonical persisted `WardenSpec`.
- There is no local supervisor/install layer from the UI.
- There is no registration flow linking created wardens back into the Mimir
  Wardens page.

## Recommendation

Make **Ravn the system of record for warden lifecycle** and make **Mimir the
primary operator console for knowledge-centric wardens**.

In practice:

1. Add a first-class `WardenSpec` and `WardenStatus` to Ravn.
2. Add `ravn warden ...` CLI commands in the real Ravn CLI.
3. Add Ravn HTTP routes for warden CRUD and lifecycle.
4. Change the Mimir Wardens page to compose:
   - live wardens from the Ravn API
   - mount and dream context from Mimir
5. Keep `/mimir/ravns` as the operator-facing home, but stop treating
   `/mimir/ravns/bindings` as the canonical source of truth.

## Proposed Model

### WardenSpec

Persisted at:

- `~/.ravn/wardens/<warden_id>/warden.yaml`

Fields:

- `id`
- `name`
- `persona`
- `profile`
- `deployment`
  - `launchd` on macOS first
  - `systemd` later
- `runtime`
  - generated `ravn` config path
  - state dir
  - logs dir
- `mimir`
  - `mount_names`
  - `write_mount`
  - optional category scope
- `features`
  - `wakefulness.enabled`
  - `dream_cycle.enabled`
  - `thread_queue.enabled`
  - `thread_enricher.enabled`
  - `recap.enabled`
  - `source_trigger.enabled`
  - `staleness_trigger.enabled`
- `trust`
- `budget`
- `autostart`
- `created_at`
- `created_by`

### WardenStatus

Derived, not handwritten:

- `state`: `created | installed | running | stopped | failed`
- `pid` or supervisor status
- `last_started_at`
- `last_seen_at`
- `last_dream`
- `pages_touched`
- `mount_names`
- `write_mount`
- `runtime_health`
- `queue_depth`
- `wakefulness_summary`

### WardenBinding projection

The existing Mimir `RavnBinding` shape can remain the UI-facing summary model,
but it should become a projection derived from `WardenSpec + WardenStatus`,
instead of an adapter-local hardcoded list.

## Backend Plan

### 1. Add warden registry and compiler in Ravn

New module family:

- `src/ravn/warden/models.py`
- `src/ravn/warden/store.py`
- `src/ravn/warden/compiler.py`
- `src/ravn/warden/supervisor.py`
- `src/ravn/warden/status.py`

Responsibilities:

- persist/load `WardenSpec`
- compile `WardenSpec` to concrete `ravn` daemon config
- install/update/remove local supervisor units
- compute live status

### 2. Add `ravn warden ...` CLI

Primary CLI should live in:

- `src/ravn/cli/commands.py`

Command set:

- `ravn warden list`
- `ravn warden create`
- `ravn warden show <id>`
- `ravn warden install <id>`
- `ravn warden start <id>`
- `ravn warden stop <id>`
- `ravn warden restart <id>`
- `ravn warden dispatch <id> "<goal>"`
- `ravn warden delete <id>`
- `ravn warden logs <id>`

Important: `start` should always launch `ravn daemon --config <generated-config>`,
never `ravn run`.

### 3. Add Ravn HTTP routes for wardens

Extend `src/ravn/api/__init__.py` with:

- `GET /api/v1/ravn/wardens`
- `POST /api/v1/ravn/wardens`
- `GET /api/v1/ravn/wardens/{warden_id}`
- `PUT /api/v1/ravn/wardens/{warden_id}`
- `DELETE /api/v1/ravn/wardens/{warden_id}`
- `POST /api/v1/ravn/wardens/{warden_id}/install`
- `POST /api/v1/ravn/wardens/{warden_id}/start`
- `POST /api/v1/ravn/wardens/{warden_id}/stop`
- `POST /api/v1/ravn/wardens/{warden_id}/restart`
- `POST /api/v1/ravn/wardens/{warden_id}/dispatch`

Optional:

- `GET /api/v1/ravn/wardens/{warden_id}/logs`

### 4. Replace static binding projection

There are two acceptable implementation paths:

#### Preferred

Make the frontend fetch wardens from a new Ravn warden service and stop using
`GET /mimir/ravns/bindings` for canonical data.

Benefits:

- runtime ownership stays with Ravn
- Mimir UI can still present wardens without Mimir owning process lifecycle
- cleaner separation of concerns

#### Compatibility bridge

Keep `GET /mimir/ravns/bindings`, but make it derive from the Ravn warden
store or from a local shared file instead of `adapter._http_ravn_bindings`.

This is useful during transition so the existing Mimir page keeps working.

## Frontend Plan

### Current reality

The Mimir UI already has mutation patterns for CRUD pages:

- registry CRUD: `useRegistryMounts.ts`
- routing CRUD: `useRouting.ts`

The Wardens page can follow the same pattern.

### Proposed frontend service split

Add a dedicated service key:

- `ravn.wardens`

Files to extend:

- `web-next/apps/niuu/src/services.ts`
- `web-next/packages/plugin-ravn/src/ports.ts`
- `web-next/packages/plugin-ravn/src/adapters/http.ts`

Define a new port, for example:

- `IWardenStore`

Methods:

- `listWardens()`
- `getWarden(id)`
- `createWarden(input)`
- `updateWarden(id, input)`
- `installWarden(id)`
- `startWarden(id)`
- `stopWarden(id)`
- `restartWarden(id)`
- `dispatchToWarden(id, prompt)`
- `deleteWarden(id)`

### Mimir Wardens page changes

Update:

- `web-next/packages/plugin-mimir/src/ui/RavnsPage.tsx`
- `web-next/packages/plugin-mimir/src/application/useRavns.ts`

So the page becomes a real control plane:

- list wardens
- create warden drawer/modal
- start/stop/restart actions
- dispatch input
- link through to Ravn runtime detail when needed

Suggested layout:

1. Header row
   - `Create warden`
   - filter by mount / state / persona
2. Cards list
   - same visual language as today
   - add action buttons: `start`, `stop`, `dispatch`
3. Profile/detail pane
   - generated config summary
   - trigger toggles
   - trust/budget summary
   - last dream and runtime health

### Create Warden flow

The form should collect:

- `name`
- `persona`
- `primary mount(s)`
- `write mount`
- `preset`
  - `research`
  - `curator`
  - `general autonomous`
- toggles:
  - wakefulness
  - dream cycle
  - recap
  - research/source trigger
  - staleness review
- autostart

On submit:

1. `POST /api/v1/ravn/wardens`
2. optionally `POST /api/v1/ravn/wardens/{id}/install`
3. optionally `POST /api/v1/ravn/wardens/{id}/start`
4. invalidate wardens query

## Presets

Start with one excellent preset instead of many half-defined ones.

### Research Warden

Default persona chain:

- wakefulness -> `research-and-distill`
- dream cycle -> `mimir-curator`
- recap -> `produce-recap`
- thread drafting -> `draft-a-note`

Default features:

- `wakefulness.enabled = true`
- `dream_cycle.enabled = true`
- `thread.enabled = true`
- `recap.enabled = true`
- `mimir.source_trigger.enabled = true`
- `mimir.staleness_trigger.enabled = true`

This directly matches the user goal of dream cycles, validation, and research.

## Registration Rules

When a warden is created, it should automatically register in the Mimir UI if:

- it declares at least one Mimir mount, and
- the host profile exposes both the Mimir and Ravn services

Registration should not be a separate manual step.

Implementation rule:

- `WardenSpec` is canonical
- `RavnBinding`/`WardenCard` data is derived

Avoid a second handwritten registry for the same thing.

## Persistence and Restart Semantics

Must survive:

- app restarts
- machine restarts
- daemon crashes

Already present:

- task queue journal
- cron state
- dream cycle state
- wakefulness state

Still needed:

- persisted last-interaction / operator presence state
- explicit supervisor install/uninstall state
- recovery semantics for in-flight work

## Validation and Trust

For wardens to be safe, "always-on" must be paired with stronger validation.

Use existing patterns already in the repo:

- research provenance validation in `src/ravn/agent.py`
- research-page write validation in `src/ravn/adapters/tools/mimir_tools.py`
- trust gradient in `src/ravn/config.py`

Suggested warden rule:

- research wardens may write only to configured mount scopes
- outputs surfaced in the UI should show provenance and last validation result
- destructive tools remain constrained by trust policy

## Delivery Order

### Phase 1: make the model real

1. Add `WardenSpec` store and compiler in Ravn.
2. Add `ravn warden list/create/show`.
3. Add Ravn HTTP `GET/POST /wardens`.
4. Add frontend service and make `/mimir/ravns` read from live wardens.

### Phase 2: make it runnable

1. Add `install/start/stop/restart`.
2. Implement `launchd` supervisor adapter.
3. Add dispatch action from UI and CLI.
4. Show runtime status and logs.

### Phase 3: make it feel native

1. Add create-wizard presets in the Mimir UI.
2. Add per-warden dream/wakefulness summaries.
3. Add last validation state and provenance drilldown.
4. Add compatibility bridge for `/mimir/ravns/bindings` or retire it.

## Suggested First Slice

If we want the smallest end-to-end vertical slice:

1. Add `WardenSpec` file store in Ravn.
2. Add `ravn warden create/list/start`.
3. Add `GET/POST /api/v1/ravn/wardens`.
4. Add a `Create warden` button to `RavnsPage.tsx`.
5. Switch the Wardens page to use the new live Ravn wardens endpoint.

That gets us:

- created from Mimir UI
- launched locally
- visible in the same UI
- backed by the real `ravn` command line

without waiting for the full dream/validation dashboard polish.
