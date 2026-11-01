# Homepage narrative

Status: third pass, implemented in this worktree as an uncommitted proposal, 2026-11-01.
The second pass is live on `dev`.

The second pass was accurate but read like an essay: fourteen sections, with
several saying the same thing. This pass shows the product instead. A visitor
should learn what NIUU is, what they can do with it, and why it is more than a
coding-agent orchestrator, by scrolling through real screens.

The page states selling points in a line each. Detail goes to the docs;
`docs-rewrite.md` lists where.

## Structure

| # | Section | Heading | Job |
|---|---|---|---|
| - | Hero | A place where intelligent systems do real work. | What NIUU is. Headline and supporting copy unchanged. |
| 1 | The idea | Agents that work for you, not agents you have to drive. | Why autonomous work needs more than sessions. Session, workflow, team, resident. |
| 2 | Gallery | See NIUU at work. | The visual centre. Seven chapters of real screens, in the order work grows: one agent to a whole environment. |
| 3 | Continuity and evolution | Work doesn't start over. It evolves. | Residents, how a resident commissions what it needs, and Workspaces. |
| 4 | The NIUU environment | Six parts of one system. | One drawing of how the parts fit, then what NIUU builds on. |
| 5 | Use cases | Not just for code. | The range of work, ending on Add your own. |
| 6 | For teams | From one person to an organization. | Teams, multi-tenancy, scaling and security in one place, for someone evaluating NIUU for a company, with what is direction marked. |
| 7 | Get started | Try it on your own machine. | One clear path for developers. |
| 8 | Closing | None | Where to go next, in large links: the four docs areas and GitHub. |

### Gallery chapters

Jozef set the order: Forge, multi-agent, workflow, ticketing, coordinator, Memory,
Observatory.

| Chapter | Layout | Views |
|---|---|---|
| Forge | Text left, screen bleeds right | Running now, Session |
| Multi-agent | Screen bleeds left, text right | Room |
| Workflows | Wide | Workflow |
| Tickets | Text left, screen bleeds right | Board, Gate |
| Coordinator | Screen bleeds left, text right | Coordinator |
| Memory | Text left, screen bleeds right | Map, Page |
| Observatory | Wide | Trace, Live map |

Residents and Workspaces are not in that order, so they open and close Continuity
and evolution, using the same chapter layout. The section moved directly after the
gallery, because the drawing of the six parts reads better once a visitor has met
them.

Every chapter scrolls, so a visitor sees them all without clicking. Tabs show more
of a chapter, and clicking a screen enlarges it. Phones get focused crops from
`images/home/gallery/*-phone.webp`, because a full window is unreadable at that
width.

## What changed from the second pass

| Second pass | Decision | Why |
|---|---|---|
| Hero label, six feature words, tagline | Removed | They restated the headline. |
| The idea, Where it starts, Why NIUU | Merged into The idea | Three sections made one argument. The four session stages are kept word for word. |
| The chat screenshot beside the argument | Moved to Forge | Sessions stop being the centre of the story. |
| The environment | Became the Workspaces chapter | Same point, with a screen. |
| In practice, six steps | Removed, its screens moved into the gallery | It repeated the gallery, and the one thing only it said is now the example in Continuity. |
| Visibility and trust | Telemetry moved into the Observatory chapter, security and multi-tenancy into For teams | Telemetry and accountability are the same subject. A team evaluating NIUU asks about the rest together. |
| What carries forward | Moved to Continuity | It belongs with the conclusion, not the opening. |
| Six product cards | Replaced by one drawing | Cards listed six products; the drawing shows one system. |
| Built on what you have, seven rows | Moved under the environment, four rows | Harnesses, models, connections and runtimes. Every named product stays. |
| Direction section | Removed | Jozef asked to take it off the page. The operating-system direction stays in the docs. |
| One worked example of a tool build | A four-step commissioning story, with the workflow | Jozef asked for the evolution story: a resident can commission work. |
| No ticket screens | Tickets and Coordinator chapters | Jozef asked to see a workflow applied to a ticket, with the coordinator beside it. |
| Use cases | Kept, without cards | Asked for near the bottom. |
| Closing headline and docs map | Headline removed, the map became the closing section in large links, with GitHub added | The headline echoed the hero, and a reader at the end wants somewhere to go. |

## Why this is different from agent orchestration

An orchestrator starts with a task and ends with an output. This page starts with
a responsibility and ends with a system that can do more than it could before.

- People **stop driving sessions**: agents work inside a defined process and reach out when they need someone.
- Access is **limited by role**: a shipped reviewer is read-only, with its write and shell tools forbidden.
- The unit of the story is a **responsibility**, not a run.
- Agents are **the main participants**, and a person joins at decisions.
- The result of work is **a better environment**, not only a finished task.

The sixth step is the one an orchestrator does not have: a resident picks up a
tool from a peer and retires a duplicate, and the next problem starts further
along.

## What ships and what does not

| Idea | State | Evidence |
|---|---|---|
| Workspace with charter, trust, budget and log | **On by default** | `plugin-realms`, `rest_realms.py`. A fresh local install creates a Blank workspace and starts its resident. Templates with standing jobs need the event bus, so they stop at the jobs step locally. |
| Session telemetry | **On by default** | The Telemetry tab is now a default session tab. Spans are stored by Forge. |
| OpenTelemetry export | **Off until configured** | `observability.enabled` needs a collector's endpoints, so it cannot default on. The page says "export to", not that it is on. |
| Security | Ships, with limits | OIDC, Cedar policies, scoped workload tokens, OpenShell egress policy and isolated learned tools. NetworkPolicy templates are off by default, and scoped credentials cover named entry points only. A local install has no login, and the page says so. |
| Multi-tenancy | Ships, with limits | Sessions, Realms, Memory and credentials carry a tenant (`src/niuu/domain/tenancy.py`, migration 000090, `tenant_quotas` in `src/bifrost/config.py`). One organization per user. Isolation is not one single mechanism across all stores. |
| Unrelated customers on one installation | **Direction** | Whether the event bus is tenant-scoped is unverified. The page lists it as direction. |
| Team basics: login, roles, organization-owned residents and Memory | Ships | `src/identity/`, `charts/volundr/values.yaml` `roleMapping`, `resident_runtimes` tenant, Memory `visibility = 'tenant'`. |
| Inviting a colleague into a session | **Direction on the page** | The API ships (`rest_session_participants.py`, viewer and approver), with no app screen. Kubernetes needs `room_role_source: remote`, unproven on a live cluster and incompatible with `enforce_ownership`, which ymir sets. |
| Shared projects, workflows and Workspaces; provider-sourced members | **Direction** | Organizations design phases 1 and 2. Everything a person makes is private to them today, apart from Memory marked for the organization. |
| Scaling | Ships | Session backends in `src/volundr/adapters/outbound/`, HPA and PDB templates in the charts, `docs/operator/joining-machines.md`. The page says services "can autoscale", because the values default off. No numbers are claimed. |
| Residents react to inputs | Ships | `src/ravn/adapters/triggers/`: schedules, bus events, polled conditions, Memory changes and the ticket queue. Messages arrive through rooms and channels. |
| Workspace trust levels | **Mostly stored, not enforced** | Agents read only the `build` grant (`src/ravn/adapters/realm/client.py`). The One workspace screen shows "asks before deploy", but nothing enforces it. The copy makes no claim about it. |
| Workspace setup from a sentence | Ships, simple | A regex in the browser (`plugin-realms/src/domain/sentence.ts`), not the resident. The page no longer claims it. |
| Workspaces outside code | **Limited** | Every template requires a repository. The page's example is a resident, not a Workspace. |
| Projects that hold Workspaces | **Direction** | Built but uncommitted on `feat/projects-without-repo`, tested on mock data only, never run on real sessions. Not on the page: Jozef removed the line. See "Projects and Workspaces" in `docs-rewrite.md`. |
| Residents with mandates, Activity and Inbox | Ships | `plugin-valkyrie`, resident runtime |
| Resident self-improvement | Ships | `src/ravn/adapters/reflection/self_improvement.py`: in its dream a resident judges past fixes, drops one that did not help for good, and acts on recurring friction by changing a learning, retiring a belief, commissioning a tool or filing a defect ticket. |
| A resident commissions work | Ships | `build_tool` starts a Work workflow over A2A (`src/ravn/adapters/tool_build/a2a.py`). The resident answers builder questions and reviews gates within its `build` grant (`gate_review.py`), verifies the result itself, and three failures in a row roll a tool back (`docs/operator/valkyrie-self-improvement.md`). |
| Ticket board | Ships, with limits | PR 1209 and fixes. Columns come from `graph.board`, several workflows can sit on one board, and moving a ticket starts its workflow. Proved end to end on a cluster at dev.431 and dev.435. Only system workflows can go on a board, and PR links fail on clusters. The page does not name a tracker for the board, because Linear is the one proved live. |
| Ticket coordinator | Ships | One long-lived session per ticket (`ticket_coordinators`, persona `ticket-coordinator`). It starts workflows, hands over between them, hears gates through its room, tells the person and closes with the ticket. Proved on the same cluster runs. |
| Teams through workflows, with review and gates | Ships | 18 bundled workflows, `src/ting/workflows/` |
| Per-role tools, model and budget | Ships | Workflow stages set persona, model and budget (`src/ting/workflows/*.yaml`). Personas set tools (`src/ravn/personas/developer-security-plan-reviewer.yaml`). |
| Rooms shared by people and agents | Ships | `niuu.collaboration`, browser and Telegram. `LiveSessionDetailPage` passes participants and mesh events to `SessionChat`, the component in the Room screen. |
| Direct peer communication, A2A | Ships | Flock mesh, `src/ravn` |
| Tool built, verified and adopted | Ships | `build_tool.py`, `tool-skill-builder.yaml` |
| Peer adoption | Ships, partial | Tested tools only, and they re-verify. `docs/ravn/flock-capability-gate.md` shows proposals do not reach every resident. The copy says "can adopt a tested tool". |
| Use cases | Ships | `developer-delivery`, `infra-change-flow`, `research-campaign` and `specification-stack` in `src/ting/workflows/`. Non-code residents use the Blank template. |
| Duplicate tools retired | Ships | Evolution report. Unused and unrunnable tools are also retired, so the page says a tool stays "while it stays useful". |
| Resident decision history | Ships, needs a database | In memory without a DSN (`src/ravn/adapters/valkyrie_history/__init__.py`), and no repo chart sets one. The page says Activity "shows" decisions, not that they persist. |
| Memory with beliefs and evidence | Ships | `plugin-mimir` |
| Observatory live map | Ships | `plugin-observatory`. It shows what is connected to it, so a small install has a small map. The copy says "you have connected". |
| Forge overview | Ships | `plugin-volundr`. Sessions run on connected hosts. Copy does not claim every agent runs through it. |
| One goal and actor model across everything | **Direction** | `concepts/operating-system.md`. In the docs only, not on the page. |
| A resident's intrinsic drive | **Not claimed** | The June design names it as missing. The page says residents observe and judge, not that they set their own goals. |

## About the screens

Every screen is the real interface. None is a mockup or an edited picture of one.

- **From a fresh install of current `dev`:** Workflow, Tool builder and the
  Memory page. That page was written from these docs.
- **Real component, sample conversation:** Room, Session and the Coordinator view
  are the product's `SessionChat` component with a written conversation, because
  no live agent could run here. The captions say so.
- **Real board, sample tickets:** Board, Gate and Coordinator are the real
  `ProjectBoard` and `TicketPanel` on the demo build, with extra sample tickets so
  every stage shows.
- **Real tab, sample spans:** Trace is the real Telemetry tab on a real session,
  with spans posted through Forge's span API.
- **Real interface, sample data:** Residents, Workspaces, the Memory map, Forge
  overview and Observatory come from the demo build, because a fresh install has
  no residents. The gallery introduction says so.
- **Changed at capture time:** names in the sample data were swapped for neutral
  ones (cluster names, the sample user, internal service names, the word
  "steward"), and the clock was set so timestamps read naturally. Nothing else
  was touched.

Still worth recapturing from a real system: all of the demo-build screens, and
Room and Session from a live conversation. The Memory map has no real answer
screen because search over the demo data returns nothing useful, so a search
result is the best missing shot.

Sources for the captures are not kept in the repo. Regenerating them needs the
demo build (`public/config.json`, `demoMode: true`) on a Vite server.
