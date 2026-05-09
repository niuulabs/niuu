# Service Boundaries

This document describes the boundary model that is now in the repo after the
canonical route cutover and control-plane extraction work.

## Current State

As of May 8, 2026:

- the legacy public `/api/v1/volundr/*` surface is gone
- `web/` is gone and `web-next` is the only browser UI
- `niuu` is the composition root and shared host
- Forge remains the public face of Volundr
- shared platform domains are extracted as their own top-level packages and
  plugins
- the Python control plane ships as one `niuu` image, even though domains are
  still mounted as separate services/plugins

That means we now have:

- namespace separation
- plugin/package separation
- shared-host deployment by default

We do not yet require separate processes or separate images for each domain.

## Boundary Model

### `niuu`

`niuu` is the host and composition root.

It owns:

- plugin discovery and host profiles
- shared service startup/runtime helpers
- shared route inventory and mounting
- co-hosting multiple FastAPI apps in one local/dev control plane

Shared code that multiple domains need should move here rather than back into
`volundr`.

### `volundr`

`volundr` is now the Forge domain, not the junk drawer.

It owns:

- `/api/v1/forge/sessions`
- `/api/v1/forge/workspaces`
- `/api/v1/forge/templates`
- `/api/v1/forge/presets`
- `/api/v1/forge/profiles`
- `/api/v1/forge/resources`
- `/api/v1/forge/prompts`
- `/api/v1/forge/events`
- `/api/v1/forge/chronicles`
- `/api/v1/forge/admin`
- Forge git and repo flows

Rule:

- if a public route exists because it is part of the Forge experience, it may
  belong here
- if it exists only because Volundr happened to host it, it should move out

### `identity`

`identity` owns identity and tenant context.

It owns:

- `/api/v1/identity/me`
- `/api/v1/identity/auth/config`
- `/api/v1/identity/users`
- `/api/v1/identity/tenants`
- `/api/v1/tokens`

### `features`

`features` owns feature catalog and user feature preferences.

It owns:

- `/api/v1/features`
- `/api/v1/features/modules`
- `/api/v1/features/preferences`

### `credentials`

`credentials` owns generic credential and secret-store capabilities.

It owns:

- `/api/v1/credentials/*`

This is the place for shared credential and MCP-server metadata, not Forge.

### `integrations`

`integrations` owns generic connection management and OAuth flows.

It owns:

- `/api/v1/integrations/*`

This is the domain for reusable external connector setup, not Tyr- or
Forge-specific orchestration.

### `tracker`

`tracker` owns generic tracker state and mappings.

It owns:

- `/api/v1/tracker/status`
- `/api/v1/tracker/issues`
- `/api/v1/tracker/repo-mappings`

### `audit`

`audit` owns shared audit-query surfaces.

It owns:

- `/api/v1/audit`
- `/api/v1/audit/events`

### `tyr`

`tyr` owns planning, review, dispatch, workflow execution, and operator control.

It owns:

- `/api/v1/tyr/sagas`
- `/api/v1/tyr/raids`
- `/api/v1/tyr/sessions`
- `/api/v1/tyr/dispatch`
- `/api/v1/tyr/dispatcher`
- `/api/v1/tyr/events`
- `/api/v1/tyr/flock`
- `/api/v1/tyr/flock_flows`
- `/api/v1/tyr/pipelines`
- `/api/v1/tyr/settings`

### `mimir`

`mimir` owns memory, search, sources, graph, and linting.

It should continue to be treated as its own domain even when it is co-hosted in
the same image.

### `ravn`

`ravn` owns personas, runtime sessions, triggers, and agent-facing execution
surfaces.

### `skuld`

`skuld` remains the room/session mediation layer, not a generic REST domain.

## Intentional Tyr-Owned Edge Surfaces

Two remaining public route groups can look like boundary leakage if you only
read the path names. They are intentional Tyr edges.

### `tracker-intake-api`

Prefixes:

- `/api/v1/tracker/projects`
- `/api/v1/tracker/import`

Why Tyr owns them:

- they are not generic tracker state
- they are intake routes for browsing external tracker projects in order to
  create Tyr saga state
- `/import` writes into Tyr-owned workflow objects

Rule:

- generic tracker browsing/search/mapping stays in `tracker`
- project intake that exists to create or shape Tyr work may stay in `tyr`

### `tyr-channel-api`

Prefixes:

- `/api/v1/tyr/integrations`
- `/api/v1/tyr/telegram`

Why Tyr owns them:

- they are operator-channel routes for Tyr-specific control and notification
  flows
- Telegram webhook/setup is a Tyr ingress channel, not a generic platform
  integration

Rule:

- reusable OAuth and connector management stays in `integrations`
- channels whose purpose is to steer or notify Tyr may stay in `tyr`

## Rules Going Forward

### 1. No new public `volundr` namespace

Do not introduce any new `/api/v1/volundr/*` routes.

### 2. Shared code belongs in `niuu`

If `identity`, `credentials`, `integrations`, `tracker`, `features`, `audit`,
and `volundr` all need the same startup or storage helpers, those helpers move
to `niuu`.

### 3. Domain packages should not import `volundr`

Extracted packages should depend on `niuu` shared helpers and their own domain
code, not on `volundr` internals.

### 4. Route-domain names should describe intent

Names like `tracker-intake-api` are preferable to overloaded names like
`tracker-project-api` because they tell the reader why a surface exists.

### 5. Package split first, deployment split later

The default path is:

- extract boundary into its own package/plugin
- keep it co-hosted in `niuu`
- only give it a dedicated runtime if there is a real operational reason

### 6. Avoid image proliferation

Separate code ownership does not imply separate container images. The current
default is one Python control-plane image and multiple command-selected entry
surfaces.

## What Is Still Outstanding

These are the real remaining gaps after the boundary cleanup:

- Forge still needs a more truthful live-data and SSE story for the daily-driver
  path.
- Mimir still needs a broader parity pass against the `web-next` contract.
- Ravn still needs a tighter runtime/persona/session pass for daily-driver use.
- Observatory still needs a first-class backend story.
- The unified `niuu` image path still needs full end-to-end runtime smoke in a
  cluster.
- The daily-driver experience still needs a stronger single front door on top
  of Tyr -> Volundr -> Skuld -> Flokk.
