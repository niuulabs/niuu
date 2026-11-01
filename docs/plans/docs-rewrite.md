# Docs rewrite plan

Status: proposal, 2026-10-31. Nothing here has landed.

The landing page and `get-started/introduction.md` in this worktree are the
worked example. This plan covers the rest of `docs/site/`. The page's structure
and reasoning are in [homepage-narrative.md](homepage-narrative.md).

## Principles

1. **Lead with the environment, not the session.** Docs follow the landing page:
   responsibilities, collaboration and continuity first. Sessions stay well
   documented, as one execution detail among others.
2. **Order pages by what the reader wants to do.** Today's nav mixes guides under
   `get-started/` with reference. New readers should meet a short path, then
   product guides, then concepts.
3. **Every claim is demonstrable.** A page says what ran, what did not, and what
   is direction. Keep the docs' existing habit of separating the two.
4. **Public names in prose.** Forge, Work, Memory, Agents, Observatory, Platform,
   AI models, Workspaces. Implementation names appear only where the reader must
   type them: routes, config keys, commands, charts. "Niuu" in prose. "Resident",
   never "steward".
5. **Real screens only.** No screenshot from before the rebrand survives.
6. **One topic, one home.** Forwarding pages go, and old URLs redirect.

## Proposed nav

```text
Home
Start here
  What is Niuu?                 get-started/introduction.md  (done, sample)
  Install                       get-started/install.md
  Quick start                   get-started/first-local-stack.md
  Work on a repository          get-started/configure-project.md
Use Niuu
  Forge
    Review agent changes        concepts/git-and-review-flow.md (moved)
    Authenticate an agent       reference/credentials-and-secrets.md (moved)
  Work
    Run a workflow              get-started/workflows-and-teams-step.md
    Build a review              get-started/build-a-review.md
  Memory
    Add and find knowledge      get-started/durable-memory.md
  Agents
    Run an agent directly       get-started/direct-and-resident-assistants.md (split)
    Keep a resident             new, from the same page
  AI models
    Check models and providers  get-started/model-routing-step.md
  Platform and Observatory
    Connect machines, inspect topology  get-started/shared-discovery-and-topology.md
Understand Niuu
  How it fits together          concepts/platform-model.md
  Sessions and workspaces       concepts/sessions-and-workspaces.md
  Agents, personas and residents  concepts/agents-and-personas.md
  Workflows and teams           concepts/workflows-and-teams.md
  Memory and knowledge          concepts/memory-and-knowledge.md
  Models and routing            concepts/model-routing.md
  Direction: an OS for agents   concepts/operating-system.md
Operate
  Single-host Docker            operations/docker-mode.md
  Kubernetes                    operations/kubernetes-deployment.md
  OpenShell sandboxes           operations/openshell-runtime.md
  Integrations and MCP          operations/integrations-and-mcp.md
  Security and permissions      operations/security-and-permissions.md
  Diagnose an operation         operations/observability.md
  Upgrades and migrations       operations/migrations.md
  Accept a shared deployment    operations/production-checklist.md
Reference
  Commands                      reference/cli.md, cli-niuu.md, cli-ravn.md
  Configuration                 reference/configuration.md
  HTTP API                      reference/api.md, sessions.md
  Events                        reference/events.md
  Helm charts                   reference/helm-charts.md
  Identity                      reference/identity.md
Help
  Fix a failed launch           troubleshooting/common-issues.md
  FAQ                           troubleshooting/faq.md
  What has been verified        operations/quickstart-verification.md
Contribute
  Develop Niuu locally          operations/local-development.md
```

## Every page

Verdicts: **Keep** (light pass for names and voice), **Rewrite**, **Merge**,
**Drop**. Word counts are today's.

### Home and start

| Page | Words | Verdict | Why |
|---|---:|---|---|
| `index.md` | - | Rewritten | Landing page in this worktree, second pass around responsibility and continuity. |
| `overview.md` | 504 | Merge | Into `get-started/introduction.md`, done. The landing page now does its selling. Redirect. |
| `get-started/introduction.md` | 475 | Rewritten | Sample page. |
| `get-started/install.md` | 967 | Rewrite | Lead with the path that works today. Remove the `v1.4.0` example, which does not exist. Depends on decision 1. |
| `get-started/first-local-stack.md` | 1501 | Rewrite | Strong guide, stale UI. `+` opens Quick launch now, and the wizard sits behind Advanced launch. Recapture its three screenshots. Keep the `quickstart-check` markers. |
| `get-started/configure-project.md` | 1206 | Keep | Check against Quick launch, which now asks for a folder directly. |
| `get-started/path-from-small-to-autonomous.md` | 228 | Merge | Into the table in `introduction.md`. Redirect. |
| `get-started/first-ai-workspace.md` | 68 | Drop | Forwarding page. Redirect. |
| `get-started/one-local-platform.md` | 60 | Drop | Forwarding page. Redirect. |
| `get-started/one-workspace-session.md` | 53 | Drop | Forwarding page. Redirect. |
| `get-started/kubernetes-and-gitops-step.md` | 119 | Drop | Forwarding page. Move its one decision into the Kubernetes guide. Redirect. |

### Guides

| Page | Words | Verdict | Why |
|---|---:|---|---|
| `get-started/workflows-and-teams-step.md` | 712 | Rewrite | Today it inspects. It should run a shipped workflow through a real gate. The review surface went live recently. |
| `get-started/build-a-review.md` | 1149 | Keep | Move under Work. |
| `get-started/durable-memory.md` | 346 | Rewrite | Curl only. Add the browser path: add a source, read a page, ask. |
| `get-started/direct-and-resident-assistants.md` | 373 | Rewrite | Split into a direct-agent guide and a resident guide. This is the biggest gap: residents are the headline and have 373 shared words. |
| `get-started/model-routing-step.md` | 343 | Keep | Add the AI models screens. |
| `get-started/shared-discovery-and-topology.md` | 2041 | Rewrite | Long and Guild-named. Rewrite as connecting machines, with `niuu join`, then topology. |

### Concepts

| Page | Words | Verdict | Why |
|---|---:|---|---|
| `concepts/platform-model.md` | 789 | Keep | Keep the implementation-name table here, as the one place that maps them. Redraw the architecture diagram (brand). |
| `concepts/sessions-and-workspaces.md` | 338 | Keep | Light voice pass. |
| `concepts/agents-and-personas.md` | 474 | Rewrite | Replace "steward" wording. Add the mandate, authority and case model with one worked example. |
| `concepts/workflows-and-teams.md` | 535 | Keep | Replace "Guild" with Platform in prose. |
| `concepts/memory-and-knowledge.md` | 564 | Keep | Align with the UI's words: What we believe, Evidence. |
| `concepts/model-routing.md` | 214 | Keep | Light pass. |
| `concepts/git-and-review-flow.md` | 218 | Keep | Move to Use Niuu as a task page. |
| `concepts/operating-system.md` | 714 | Keep | Already separates today from direction. |

### Operate

| Page | Words | Verdict | Why |
|---|---:|---|---|
| `operations/docker-mode.md` | 4475 | Rewrite | Longest page. Split into a task guide and a reference. Needs a real run on a Docker host first. |
| `operations/kubernetes-deployment.md` | 474 | Keep | Add the decision from the dropped step page. |
| `operations/openshell-runtime.md` | 2354 | Keep | Replace internal names in prose. |
| `operations/integrations-and-mcp.md` | 1292 | Keep | Review the PAT mention. |
| `operations/security-and-permissions.md` | 3447 | Rewrite | Accurate, dense, heavy on internal names. Lead with a boundaries table. Review the PAT sections. |
| `operations/observability.md` | 1078 | Keep | Replace internal names in prose. |
| `operations/migrations.md` | 252 | Keep | Light pass. |
| `operations/production-checklist.md` | 247 | Keep | Light pass. |
| `operations/local-development.md` | 204 | Keep | Move to Contribute. |
| `operations/quickstart-verification.md` | 959 | Rewrite | Move to Help. Add a dated record for 2026-10-31 (below). Trim the old history. |

### Reference and help

| Page | Words | Verdict | Why |
|---|---:|---|---|
| `reference/cli.md` | 320 | Keep | Index page. |
| `reference/cli-niuu.md` | 3037 | Keep | Generated. Regenerate with `scripts/generate_cli_docs.py`. |
| `reference/cli-ravn.md` | 7748 | Keep | Generated. Regenerate. |
| `reference/configuration.md` | 808 | Keep | Internal names belong here. |
| `reference/api.md` | 380 | Keep | Internal names belong here. |
| `reference/sessions.md` | 228 | Merge | Into `reference/api.md`, or keep as its first subpage. |
| `reference/credentials-and-secrets.md` | 585 | Keep | Move to Use Niuu > Forge. It is a task page titled "Authenticate an agent". |
| `reference/events.md` | 203 | Keep | Light pass. |
| `reference/helm-charts.md` | 249 | Keep | Light pass. |
| `reference/identity.md` | 190 | Keep | Light pass. |
| `reference/integrations.md` | 256 | Merge | Into `operations/integrations-and-mcp.md`. |
| `reference/web-ui.md` | 470 | Merge | Into the "How it fits together" table in `introduction.md`. Redirect. |
| `troubleshooting/common-issues.md` | 486 | Keep | Check symptoms against the current UI. |
| `troubleshooting/faq.md` | 452 | Rewrite | Good content. Remove internal names from questions. |

### Images

- Delete 21 unused pre-rebrand PNGs: everything in `images/landing/`, plus
  `admin-users`, `chronicle-timeline`, `dashboard`, `launch-wizard`,
  `launch-wizard-config`, `login`, `niuu-architecture`, `session-diffs`,
  `session-workspace`, `settings-*`, and `ui-*`. Nothing references them.
- Recapture `quickstart-source`, `quickstart-runtime` and `quickstart-confirm`.
- Redraw `niuu-architecture-light` and `-dark` in the brand.

## Order of work

1. **Decide and unblock.** The decisions below, then a fresh Claude login for a
   real agent session capture.
2. **Landing, introduction, nav skeleton, redirects.** Add `mkdocs-redirects`
   with one rule per dropped or moved page, and land them together so no URL
   breaks. Delete the forwarding pages in the same change.
3. **Start here.** Install and Quick start, proven by a live
   `scripts/check_quickstart.py --live` and a manual click-through. Readers land
   here from the page, so this has to work first.
4. **Residents and workflows.** The two thinnest guides for the two biggest
   claims on the landing page.
5. **Remaining product guides.** Memory, AI models, machines and topology.
6. **Concepts pass.** Names and voice, one change.
7. **Operate.** Split Docker mode and rewrite security. Docker mode needs a real
   run on a Docker host.
8. **Reference and help.** Regenerate, merge, record verification.
9. **Close.** `mkdocs build --strict`, the release docs job's shell syntax check,
   and an updated `docs/README.md` and brand table.

## Where the detail goes

The landing page states each selling point in a line. The explanation lives here.

| Idea | Page |
|---|---|
| Why workflows: deterministic outcomes with agents working inside them, and why you stop driving sessions | `concepts/workflows-and-teams.md` |
| Splitting responsibility, per-role tools, model and budget, read-only reviewers | `concepts/workflows-and-teams.md`, `concepts/agents-and-personas.md` |
| How agents reach people and each other: gates, Review inbox, Telegram, rooms, flock, A2A | `concepts/workflows-and-teams.md`, `concepts/agents-and-personas.md` |
| Which gates a person must answer, and `automated_approval` | `concepts/workflows-and-teams.md` |
| The questions a person should be able to ask: what is happening, why, what came of it, when am I needed, what can it do without asking | `operations/observability.md` |
| Session telemetry and exporting traces, metrics and logs | `operations/observability.md` |
| OIDC, scoped credentials, sandboxes, what a local install does not protect | `operations/security-and-permissions.md` |
| Tenancy: what is scoped per tenant, and what is not one mechanism | `operations/security-and-permissions.md` |
| Which trigger types residents react to | `concepts/agents-and-personas.md` |
| What each integration does, and building on existing tools rather than replacing them | `operations/integrations-and-mcp.md` |
| How a Workspace is set up, and local limits | `concepts/sessions-and-workspaces.md` |
| The operating-system direction | `concepts/operating-system.md` |

## Decisions needed

1. **Which install does the landing page lead with?** `releases/latest/download/install.sh`
   is a 404 today because the latest stable release, v1.3.0, predates it. The
   v1.3.0 macOS binary cannot start its database. Source is the only path that
   works for a new reader today, so the page leads with it. A stable release
   would carry `install.sh` and allow one line instead.
2. **Workspaces is on.** `realms` is enabled in `config.live.json`, and the chart
   no longer hides it, reversing the hide from `f02339e94`. A fresh local install
   now creates a Blank workspace end to end and starts its resident. Templates
   with standing jobs stop at the jobs step, because resident triggers need the
   AMQP event bus and a local install has none. Each local workspace's Memory is a
   launchd or systemd service that outlives `niuu platform up`.
3. **Should the three-cluster story go back?** The current docs say a tool
   reached residents in three clusters, but no run record backs it, and
   `docs/ravn/flock-capability-gate.md` shows peer proposals do not reach every
   resident and 26 adoptions failed. The new copy removes the claim here and in
   `concepts/operating-system.md`. Restore it only with a dated run record.
4. **`NIUU` or `Niuu` in prose?** `docs/design/brand/README.md` says "Niuu" in
   prose. The homepage brief writes NIUU, so the landing page does too, while
   the docs keep "Niuu". One rule should win.
5. **Should `./start-dev` stay the documented path?** It binds `0.0.0.0`, and auth
   defaults to `none`, so anyone on the network reaches an admin agent launcher.
   The landing page therefore uses `niuu platform up` on loopback. `install.md`
   still sends readers to `./start-dev`. Change the default to loopback, or warn.

## Found on the way

Verified on 2026-10-31 against `dev` at `deb724632`, macOS ARM64.

- **Passed:** `scripts/check_quickstart.py --niuu .venv/bin/niuu`: documented
  commands, real PostgreSQL, web UI, session launch and stop, Ctrl+C.
- **Not tested:** the `--live` run stopped at the guide's own auth probe, because
  the host's Claude login has expired. The step that exists to catch that worked.
- **Not run:** `make build-postgres`. PostgreSQL came from an existing build of
  that target on this machine. `uv sync`, `make build-web`, `niuu platform init`
  and `niuu platform up` all ran. `./start-dev` was not run, because it stops the
  `~/.niuu/pgdata` database, which holds real local data.
- Some defaults name Niuu's own hosts: `keycloak.niuu.world` for `start-dev
  --openshell`, `matrix.niuu.world` in `src/ravn/config.py`, and
  `mimir.yggdrasil.niuu.world` in bundled workflows. A local process rewrites the
  Memory URLs, but a public install should not carry them.
- Telegram is the only channel with a platform integration. Slack, Discord, Matrix
  and WhatsApp are agent configuration, with no UI or site docs.
- `get.niuu.ai`, named in the header of `scripts/install.sh`, does not resolve.
- `/health` now returns more than the `{"status":"ok"}` the quick start says to
  expect.
- Loading `/mimir` directly returns a JSON 404 from the server. In-app navigation
  works, so a reload or shared link on that route fails.
- The Docs workflow deploys on every `v*` tag, including `v2.0.0-dev.*`
  (`.github/workflows/docs.yaml`), and those runs are succeeding. A merge to `dev`
  therefore goes live at the next dev tag, so step 2 and step 3 should land close
  together.
- Workspace trust levels other than `build` are stored but not enforced, while
  the UI says "asks before deploy". `automated_approval` gates can be answered by
  an agent. Resident decision history is in memory without a DSN, and no repo
  chart sets one. These matter more now that the page leads with trust.
- The new-session page says each agent works "in its own sandbox". A local-process
  session is not sandboxed, which the docs state plainly.
