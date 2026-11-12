# Architecture

How the Niuu stack fits together: which service owns what, how they talk, and
which levers change their behaviour.

## In one minute

- Niuu is a set of Python services under `src/` plus a plugin-based web UI in
  `web-next/`. Each service is hexagonal: domain, ports, adapters, one
  composition root.
- One host process (`niuu platform up`) can mount every service behind a single
  port; Kubernetes runs the same services as separate charts.
- **Forge** owns sessions. It launches each one through a configured pod manager,
  and inside every session the **session gateway** runs the agent runtime and
  streams its output.
- The **model gateway** serves models, **Memory** holds knowledge, the **workflow
  engine** runs workflows, and **Platform** finds and aggregates Forge instances.
- Services talk over REST, SSE and WebSocket; events go over the **event bus**; agents and
  humans share **collaboration rooms**; flocks talk over the **mesh**; external agents
  over **A2A**.

## Areas

| Area | What it answers |
|---|---|
| [Stack map](stack/services.md) | Every service and library, what talks to what, one request end to end, product vs code names |
| [Design principles](design/index.md) | Hexagonal layout, ports and adapters, composition roots, dynamic adapters, fail loud, no hidden limits, config first, module boundaries |
| [Forge](forge/index.md) | Session lifecycle, Workspaces and projects, pod managers, compute pools, VMs, registered hosts, slots, event log |
| [Session gateway](session-gateway/index.md) | The runtime gateway: runtimes, process lifecycle, transcripts, channels, WebSocket, broker and Forge MCP |
| [Workflows](workflows/index.md) | The workflow engine: workflows, runs, steps, gates and reviews, trackers, personas in workflows |
| [Security](security/index.md) | Edge auth, identity adapters, every credential kind, machine identities, isolation, secrets |
| [Deployment modes](deployment/index.md) | Local mini mode, Docker, joined nodes and remote compute, Kubernetes, and the levers per mode |
| [Communication](communication/index.md) | Event bus, collaboration rooms, the flock mesh, A2A, SSE and WebSocket APIs |
| [Models](models/index.md) | Model gateway, model catalog, provider connections, routing |
| [Memory](memory/index.md) | Memory: sources, pages, retrieval, tenants, hosting |
