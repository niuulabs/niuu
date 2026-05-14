# Control-Plane Refactor Status

This file is the short current-state view of the refactor, not the historical
gap-analysis document.

## What Landed On This Branch

- legacy `web/` is gone and `web-next` is the only browser UI
- public `/api/v1/volundr/*` routes are gone repo-wide
- public APIs are split by domain:
  - `/api/v1/forge/*`
  - `/api/v1/identity/*`
  - `/api/v1/features/*`
  - `/api/v1/credentials/*`
  - `/api/v1/integrations/*`
  - `/api/v1/tracker/*`
  - `/api/v1/audit/*`
  - `/api/v1/tokens/*`
  - Ting, Mimir, Ravn, and Bifrost stay in their own namespaces
- shared platform domains are extracted into top-level packages and plugins
- extracted domains are `niuu`-facing instead of importing `volundr` directly
- the Python control plane now ships as one `niuu` image with command-selected
  service entrypoints

## Boundary Snapshot

### Volundr

Volundr now means Forge.

It owns:

- sessions
- workspaces
- templates, presets, profiles, prompts, resources
- Forge events and chronicles
- Forge admin and repo/git flows

### Shared platform domains

These are now separate top-level domains:

- `identity`
- `features`
- `credentials`
- `integrations`
- `tracker`
- `audit`

### Intentional Ting-owned edge surfaces

These are the two remaining route groups that look shared but are still
intentionally Ting-owned:

- `tracker-intake-api`
  - `/api/v1/tracker/projects`
  - `/api/v1/tracker/import`
  - this is project intake for saga creation, not generic tracker state
- `ting-channel-api`
  - `/api/v1/ting/integrations`
  - `/api/v1/ting/telegram`
  - this is Ting operator-channel wiring, not generic platform integrations

## What Is Still Outstanding

### 1. Runtime verification

The charts now lint and template cleanly with the unified `niuu` image, but the
full end-to-end cluster smoke still needs to be run.

### 2. Forge truth path

The browser golden path still needs a harder truth pass:

- live data everywhere on the main flow
- fewer silent fallbacks
- real SSE/event behavior where the UI still leans on mock-like behavior

### 3. Ting / Volundr / Skuld / Flokk daily-driver path

The execution spine needs one dogfood-quality flow that feels like one system
instead of multiple tools stitched together.

### 4. Ravn completion pass

Ravn still needs a focused pass on:

- runtime/session truth
- CLI and hosted path verification
- wakefulness and long-lived behavior validation

### 5. Mimir parity pass

Mimir needs a fresh pass against the `web-next` contract and the daily-driver
story, especially where the browser expects broader memory/search/graph
capabilities.

### 6. Observatory

Observatory still needs a first real backend story rather than being mostly a
frontend concept.

## Recommended Order

1. End-to-end smoke on the unified `niuu` image.
2. Forge truth-path cleanup for the browser shell.
3. One real Ting -> Volundr -> Skuld -> Flokk dogfood flow.
4. Ravn and Mimir parity/verification passes.
5. Observatory and attention-surface work.

## Rule Of Thumb

If a change makes the system simpler for the operator but more explicit in the
code, it is probably the right direction.

If a change makes the code look more abstract while adding another visible seam
for the operator, it is probably the wrong direction.
