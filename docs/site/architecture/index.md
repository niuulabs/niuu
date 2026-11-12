# Architecture

How the Niuu stack fits together: which service owns what, how they talk, and
which levers change their behaviour. Every page is checked against the code and
links to it.

## In one minute

- Niuu is a set of Python services under `src/` plus a plugin-based web UI in
  `web-next/`. Each service is hexagonal: domain, ports, adapters, one
  composition root.
- One host process (`niuu platform up`) can mount every service behind a single
  port; Kubernetes runs the same services as separate charts. The code is the same.
- **Forge** (Völundr) owns sessions. It launches each one through a configured pod
  manager, and inside every session **Skuld** runs the agent runtime and streams
  its output.
- Models are reached through **Bifröst**, memory through **Mímir**, workflows run in
  **Ting**, and **Platform** (Guild) finds and aggregates Forge instances.
- Services talk over REST, SSE and WebSocket; events go over **Sleipnir**; agents and
  humans share **collaboration rooms**; flocks talk over the **mesh**; external agents
  over **A2A**.

## Reading order

1. **Stack map** (this area) — start with [services and channels](stack/services.md),
   then follow [one request end to end](stack/request-flow.md). Keep
   [product and implementation names](stack/names.md) open while reading the rest.
2. [Design principles](design/index.md) — the rules every service follows; read before changing code.
3. **Forge**, then **Skuld** — the session path, which most other areas plug into.
4. **Communication** and [Security](security/index.md) — how services and machines talk and prove who
   they are.
5. **Models**, **Memory**, **Workflows** — the services sessions and residents use.
6. **Deployment modes** — how the same services run locally, in Docker and on
   Kubernetes.

## Areas

| Area | What it answers |
|---|---|
| [Stack map](stack/services.md) | Every service and library, what talks to what, one request end to end, product vs implementation names |
| [Design principles](design/index.md) | Hexagonal layout, ports and adapters, composition roots, dynamic adapters, fail loud, no hidden limits, config first, module boundaries |
| Forge | Session lifecycle, Workspaces and projects, pod managers, compute pools, VMs, registered hosts, slots, event log |
| Skuld | The runtime gateway: runtimes, process lifecycle, transcripts, channels, WebSocket, broker and Forge MCP |
| Workflows | Ting: workflows, runs, steps, gates and reviews, trackers, personas in workflows |
| [Security](security/index.md) | Edge auth, identity adapters, every credential kind, machine identities, isolation, secrets |
| Deployment modes | Local mini mode, Docker, joined nodes and remote compute, Kubernetes, and the levers per mode |
| Communication | Sleipnir events, collaboration rooms, the flock mesh, A2A, SSE and WebSocket APIs |
| Models | Bifröst gateway, model catalog, provider connections, routing |
| Memory | Mímir: sources, pages, retrieval, tenants, hosting |

Ravn, the agent runtime, is being rebuilt; it gets its own area once the new
runtime lands.

## How these pages are written

| Section | What it holds |
|---|---|
| In one minute | The answer, in a few bullets |
| How it fits | One diagram |
| Mechanics | Tables and short lists |
| Levers | Every knob that exists in code, its default and where it is set |
| Tradeoffs | What a choice gains and costs |
| Code map | Where each concern lives on `dev` |
