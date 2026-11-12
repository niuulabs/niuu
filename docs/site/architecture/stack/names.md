# Product and implementation names

The UI and these docs use product names; code, routes, config keys, packages,
commands and Helm charts keep the implementation names. A rename never changes a
route or a key.

## In one minute

- Read a product name in the UI, look for the implementation name in the code.
- Implementation names are stable API: `/api/v1/forge/...` is Forge, `volundr` is
  its package and chart.
- Some names are older than the products: Forge's REST prefix is `forge`, but its
  package, chart and web routes still say `volundr`.
- Two terms overlap on purpose: a **session workspace** (a Forge session's files)
  and a **realm** in Observatory (a network zone) are not Workspaces.

## The mapping

| Product name (UI, docs) | What it is | Implementation name | Where you see the implementation name |
|---|---|---|---|
| **Platform** | Instance registry, discovery, aggregate Forge API | Guild | `src/guild/`, `charts/guild`, `/api/v1/niuu/instances` |
| **Forge** | Sessions, projects, compute, residents' runtimes | Völundr | `src/volundr/`, `charts/volundr`, `/api/v1/forge`, web `/volundr/*` |
| **Workspaces** | An environment a resident keeps, with its charter and trust | Realms | `niuu.domain.services.realm`, `/api/v1/realms`, web `plugin-realms` (`/realms/*`) |
| **Projects** (rail entry) | What you work on; holds Workspaces | Forge projects | `/api/v1/forge/projects`, `volundr.domain.services.projects` |
| **Work** | Workflows, runs, board, gates | Ting | `src/ting/`, `charts/ting`, `/api/v1/ting`, web `/ting/*` |
| **Agents** | Personas and agent runtime | Ravn (runtime), personas (registry) | `src/ravn/`, `src/personas/`, `/api/v1/personas`, web `/ravn/*` |
| **Memory** | Knowledge sources, pages, retrieval | Mímir | `src/mimir/`, `charts/mimir`, `/api/v1/mimir`, web `/mimir/*` |
| **AI models** | Model gateway, catalog, providers, usage | Bifröst | `src/bifrost/`, `charts/bifrost`, `/api/v1/bifrost` |
| **Observatory** | Live topology and events | Observatory | `src/observatory/`, `/api/v1/observatory` |
| *(no product name)* | Session gateway inside every session pod or process | Skuld | `src/skuld/`, `charts/skuld`, `/s/{session_id}/…` |
| *(no product name)* | Event bus | Sleipnir | `src/sleipnir/`, `sleipnir:` config blocks |
| **resident** | A Ravn kept running to look after a Workspace | Valkyrie (older code and docs) | `resident-runtimes` routes, `valkyrie_evolution/` |

## Terms that look alike

| Term | Means | Not to be confused with |
|---|---|---|
| Session workspace | The filesystem of one Forge session | Workspaces (Realms) |
| realm (Observatory, cluster views) | A network zone in the topology | Workspaces (Realms) |
| Flock | A group of residents and sessions that share a mesh (`flock:` memberships) | A collaboration room |
| `niuu` | The shared library **and** the CLI / platform host process | The Niuu product as a whole |

## Rules for writing

- Product name first, the implementation name once in parentheses: Forge
  (Völundr), Platform (Guild), Workspaces (Realms).
- User-facing copy says *resident*.
- Never rename a route, config key, package, command or chart to match a product
  name.

## Code map

| Concern | Where |
|---|---|
| Brand names, source of truth for the product column | [docs/design/brand/README.md](https://github.com/niuulabs/niuu/blob/dev/docs/design/brand/README.md) |
| Rail titles in the web UI | `title:` in each [web-next/packages/plugin-*/src/index.ts(x)](https://github.com/niuulabs/niuu/tree/dev/web-next/packages) and [web-next/apps/niuu/src/guild.tsx](https://github.com/niuulabs/niuu/blob/dev/web-next/apps/niuu/src/guild.tsx) |
| Public route prefixes per service | `api_route_domains()` in each `src/<package>/plugin.py`, e.g. [src/guild/plugin.py](https://github.com/niuulabs/niuu/blob/dev/src/guild/plugin.py) |
| Workspaces (Realms) domain | [src/niuu/domain/services/realm.py](https://github.com/niuulabs/niuu/blob/dev/src/niuu/domain/services/realm.py) |

## Related

- [Services and channels](services.md) — what each of these owns.
- [One request end to end](request-flow.md).
