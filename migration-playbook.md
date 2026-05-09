# Migration Playbook

This playbook now starts after the canonical route cutover and package/plugin
extraction.

The old migration phases are done:

- public `/api/v1/volundr/*` routes are removed
- shared platform domains are extracted
- `web-next` is the only browser UI
- the Python control plane is consolidated onto the `niuu` image

What remains is not another naming migration. It is a truth, runtime, and
usability pass.

## Guardrails

### 1. Change one axis at a time

Do not combine all of these in one PR:

- domain ownership changes
- runtime behavior changes
- deployment topology changes
- UI behavior changes

If we are fixing route ownership, avoid also redesigning runtime behavior in the
same pass.

### 2. No new compatibility namespaces

Do not reintroduce:

- `/api/v1/volundr/*`
- duplicate public surfaces for the same domain
- silent route fallbacks that hide drift

If a canonical public route is wrong, fix that route. Do not add a second one.

### 3. Shared helpers move to `niuu`

When multiple extracted domains need the same behavior:

- runtime bootstrapping
- settings loading
- database wiring
- integration repository wiring

move that code into `niuu`, not back into `volundr`.

### 4. Keep the image story simple

The current default is one Python control-plane image and multiple services that
select their command at runtime.

Do not add more Python images unless there is a clear operational reason.

### 5. Package extraction before process extraction

The default escalation path is:

1. separate the code into its own package/plugin
2. keep it co-hosted in `niuu`
3. only then decide whether it needs a dedicated process or deployment

## Recommended Next Waves

### Wave 1: Runtime truth

Goal:

- prove the new boundary model actually boots and behaves correctly

Work:

- run end-to-end cluster smoke with the unified `niuu` image
- exercise `volundr`, `tyr`, `mimir`, and `bifrost` command selection in-chart
- verify auth, route inventory, and canonical browser flows

Success criteria:

- one deploy path works without special-case image logic
- no service depends on deleted per-domain Python images

### Wave 2: Forge daily-driver truth

Goal:

- make the main work surface feel real instead of demo-shaped

Work:

- remove or tighten any remaining silent mock/live fallbacks on the golden path
- finish live Forge SSE and event surfaces where the UI still fakes them
- verify session/workspace/catalog/resource flows against real data

Success criteria:

- the default browser flow uses live APIs only
- subscriptions and refresh behavior are honest

### Wave 3: Tyr -> Volundr -> Skuld -> Flokk daily-driver pass

Goal:

- make the execution spine feel like one system

Work:

- verify one real task path from intent to running flock
- tighten the approval and escalation surfaces
- keep orchestration ownership clear between Tyr, Volundr, Skuld, and Ravn

Success criteria:

- one real workflow is dogfood-ready without switching between multiple tools

### Wave 4: Mimir and Ravn completion

Goal:

- bring the standing cognition layers closer to the product vision

Work:

- finish Mimir parity against the `web-next` contract
- re-run the Ravn CLI and hosted-runtime path
- verify wakefulness, recap, and long-lived Ravn behaviors on the current stack

Success criteria:

- memory and agent surfaces are no longer the obvious weak links in the demo

### Wave 5: Observatory and attention surfaces

Goal:

- start making the ecosystem feel like a living operating environment

Work:

- add the first real observatory/topology/event surfaces
- decide what attention, recap, and escalation look like in the main shell

Success criteria:

- the system can surface what matters without requiring the user to drive every
  subsystem manually

## When To Split A Deployment

Giving a domain its own process or deployment is justified when at least one of
these is true:

- it needs a different scaling profile
- it needs a different security boundary
- it has materially different uptime or rollout needs
- co-hosting is making local reasoning worse rather than better

It is not justified solely because the code is in its own package.

## What To Avoid

- turning every router into a separate container
- reintroducing “temporary” legacy URLs
- using adapter layers to paper over unclear ownership
- mixing large naming refactors with behavior rewrites
- leaving the repo docs describing a migration that has already happened
