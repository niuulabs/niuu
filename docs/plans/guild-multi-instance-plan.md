# Guild Multi-Instance Plan

## Goal

Support multiple Volundr instances as first-class registered targets that can be:

- seeded from configuration
- registered through the UI
- filtered by user and tenant visibility
- selected explicitly from Ting dispatch
- extended later to remote clusters and Kubernetes-backed fleets

This branch implements the local proof slice with `start-guild` / `stop-guild` and a shared instance registry under `niuu`.

## Architecture

The shared control-plane concepts live in `niuu`, not `ting` or `volundr`.

- `niuu` owns the registry model, persistence, and visibility rules
- `ting` consumes visible targets and dispatches to a selected instance
- `volundr` remains the execution backend for sessions
- the web shell exposes a Guild page for registration and inspection

This keeps the import direction aligned with the project rules:

- `volundr -> niuu`
- `ting -> niuu`
- `ting !-> volundr`

### Route ownership modes

Guild owns the public Forge API facade whenever the system is running in an aggregate mode. That includes the `start-guild` proof environment and the normal `./start-dev` local platform host. In those modes, `/api/v1/forge` is a Guild route, and Guild is responsible for fan-out, target selection, visibility, and merged views across registered Volundr instances.

`./start-dev` uses local embedded Forge mode. The Niuu composition root builds the Volundr ASGI app first, passes it into Guild, and Guild seeds a system `Local Forge` instance with `baseUrl: embedded://local-forge`, `config.transport: embedded`, and the `local` tag. Calls from the Guild facade to that target use `httpx.ASGITransport`, so they are in-process ASGI calls rather than network calls to `localhost`.

Standalone Forge means Volundr runs without Guild as the front-door aggregator. In that mode, Volundr owns `/api/v1/forge` directly. This is the standalone `charts/volundr` shape, and the umbrella `charts/niuu` shape when `guild.enabled=false`; the umbrella ingress resolves its logical `forge-api` backend to Guild when Guild is enabled and to Volundr otherwise.

## Implemented Slice

### Instance registry

- first-class `RegisteredInstance` domain model
- `system`, `tenant`, and `user` visibility scopes
- persisted `niuu_instances` table
- config seeding for local Guild Alpha / Guild Beta workers
- REST endpoints under `/api/v1/niuu/instances`
- target listing endpoint under `/api/v1/niuu/targets/volundr`

### Dispatch integration

- Ting target discovery now prefers the shared registry
- target identity is exposed as `instance_id`
- dispatch can resolve targets for the current principal, not just the owner
- local dev dispatch no longer collapses to the single implicit Volundr shortcut

### UI

- new Guild page for instance registration, testing, and per-instance session inspection
- Ting dispatch selector now lists registered Volundr targets from the shared registry
- live config uses same-origin API paths so the browser and shell stay aligned

### Local proof environment

- `start-guild` launches:
  - central shell on `:8080`
  - Guild Alpha worker on `:8181`
  - Guild Beta worker on `:8282`
- `stop-guild` shuts down app processes and embedded Postgres cleanly
- `scripts/guild-e2e.py` proves:
  - tenant-scoped visibility
  - user-scoped visibility
  - explicit dispatch to different Volundr instances
  - per-instance session discovery through the central Guild API

## Acceptance Criteria

The local Guild slice is complete when all of the following hold:

1. `start-guild` brings up the central shell and both workers from a stopped state.
2. The Guild page shows multiple visible Volundr instances and their sessions.
3. The Ting dispatch UI lists those instances as explicit targets.
4. Tenant A can see and target its tenant-scoped instance, while Tenant B cannot.
5. User B can see and target its user-scoped instance, while User A cannot.
6. Dispatching through Ting creates sessions on the selected target instance.
7. `stop-guild` fully tears down the stack so a new `start-guild` run succeeds cleanly.

## Next Phases

### Remote instances

- add registry-backed credentials per instance
- introduce health checks and richer connection testing
- support central shell -> remote Volundr over stable API contracts

### Kubernetes

- add a discovery adapter for labeled Services or Ingresses
- project discovered instances into the same `niuu` registry model
- keep Guild as the same operator surface regardless of local or cluster origin

### Broader Niuu fleet

- extend the same registry pattern to Mimir, Bifrost, and future tools
- keep target selection and visibility rules consistent across services
