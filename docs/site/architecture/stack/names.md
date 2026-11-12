# Product and code names

The UI uses product names. Code, routes, config keys, packages,
commands and Helm charts keep older code names. A product rename never changes a
route or a key.

## In one minute

- Code names are stable API: `/api/v1/forge/...` is Forge, and `volundr` is its
  package and chart.
- Some code names are older than the products: Forge's REST prefix is `forge`, but
  its package, chart and web routes still say `volundr`.
- Two terms overlap on purpose: a **session workspace** (a Forge session's files)
  and a **realm** in Observatory (a network zone) are not Workspaces.

## The mapping

| Product name (UI, docs) | What it is | Where the code is |
|---|---|---|
| **Platform** | Instance registry, discovery, aggregate Forge API | `src/guild/`, `charts/guild`, `/api/v1/niuu/instances` |
| **Forge** | Sessions, projects, compute, residents' runtimes | `src/volundr/`, `charts/volundr`, `/api/v1/forge`, web `/volundr/*` |
| **Workspaces** | An environment a resident keeps, with its charter and trust | `niuu.domain.services.realm`, `/api/v1/realms`, web `plugin-realms` (`/realms/*`) |
| **Projects** (rail entry) | What you work on; holds Workspaces | `/api/v1/forge/projects`, `volundr.domain.services.projects` |
| **Work** (the workflow engine) | Workflows, runs, board, gates | `src/ting/`, `charts/ting`, `/api/v1/ting`, web `/ting/*` |
| **Agents** | Personas and the agent runtime | `src/personas/`, `src/ravn/`, `/api/v1/personas`, web `/ravn/*` |
| **Memory** | Knowledge sources, pages, retrieval | `src/mimir/`, `charts/mimir`, `/api/v1/mimir`, web `/mimir/*` |
| **AI models** (the model gateway) | Model gateway, catalog, providers, usage | `src/bifrost/`, `charts/bifrost`, `/api/v1/bifrost` |
| **Observatory** | Live topology and events | `src/observatory/`, `/api/v1/observatory` |
| Session gateway | Runs the runtime inside every session pod or process | `src/skuld/`, `charts/skuld`, `/s/{session_id}/…` |
| Event bus | Platform events between services | `src/sleipnir/`, `sleipnir:` config blocks |
| **resident** | An agent kept running to look after a Workspace | `resident-runtimes` routes, `valkyrie_evolution/` |

## Terms that look alike

| Term | Means | Not to be confused with |
|---|---|---|
| Session workspace | The filesystem of one Forge session | Workspaces |
| realm (Observatory, cluster views) | A network zone in the topology | Workspaces |
| Flock | A group of residents and sessions that share a mesh (`flock:` memberships) | A collaboration room |
| `niuu` | The shared library **and** the CLI / platform host process | The Niuu product as a whole |

## Rules for writing

- User-facing copy says *resident*.
- Never rename a route, config key, package, command or chart to match a product
  name.

## Code map

| Concern | Where |
|---|---|
| Brand names, source of truth for the product column | [`docs/design/brand/README.md`](https://github.com/niuulabs/niuu/blob/dev/docs/design/brand/README.md) |
| Rail titles in the web UI | `title:` in each [`web-next/packages/plugin-*/src/index.ts(x)`](https://github.com/niuulabs/niuu/tree/dev/web-next/packages) and [`web-next/apps/niuu/src/guild.tsx`](https://github.com/niuulabs/niuu/blob/dev/web-next/apps/niuu/src/guild.tsx) |
| Public route prefixes per service | `api_route_domains()` in each `src/<package>/plugin.py`, e.g. [`src/guild/plugin.py`](https://github.com/niuulabs/niuu/blob/dev/src/guild/plugin.py) |
| Workspaces domain | [`src/niuu/domain/services/realm.py`](https://github.com/niuulabs/niuu/blob/dev/src/niuu/domain/services/realm.py) |

## Related

- [Services and channels](services.md) — what each of these owns.
- [One request end to end](request-flow.md).
