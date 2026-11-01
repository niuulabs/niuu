# Homepage narrative

Status: fifth pass, implemented in this worktree as an uncommitted proposal, 2026-11-01.
The third pass is live on `dev`.

The fourth pass found the story. This pass makes it feel like a product rather than
documentation: the narrative is unchanged, and each step now has its own composition,
sized to how much it has to show, so the page grows as you scroll.

The page states selling points in a line each. Detail goes to the docs;
`docs-rewrite.md` lists where.

## Structure

| # | Section | Heading | Job |
|---|---|---|---|
| - | Hero | A place where intelligent systems do real work. | What NIUU is. Headline and supporting copy unchanged. |
| 1 | Gallery | One agent is a starting point. Not the limit. | The centre of the page: five steps, each with its own composition. |
| 2 | What NIUU builds on | We extend the best tools. We don't rebuild them. | Harnesses, models, connections and runtimes. |
| 3 | Use cases | Not just for code. | The range of work, ending on Add your own. |
| 4 | Get started | Try it on your own machine. | Docker first, from source second, in tabs. |
| 5 | Closing | None | Where to go next, in large links: the four docs areas and GitHub. |

### Gallery steps

A sticky rail names the steps and fills as you read. Nothing important sits behind a
tab any more: every screen is on the page, and clicking one enlarges it. Phones get
focused crops from `images/home/gallery/*-phone.webp`.

| Step | Message | Composition |
|---|---|---|
| One agent | This is the session you know. | Modest: the session runs off the right edge, with Forge as a small tile under the text. |
| A process | Work follows a path. | The first full-width moment: a real graph with a run moving through it, then gates and the board as smaller tiles. |
| A team | Agents delegate and review each other. | The room runs off the left edge, with its four handoffs read beside it, then the coordinator and a decision. |
| Continuity | What is learned stays. | A mosaic of Memory, evidence, the inbox, a resident and Workspaces, then how a resident commissions work. |
| An environment | It all fits together. | The largest heading and the drawing of the six parts across organizations and hosts, then four screens, what is in place today and what is planned. |

The step order follows the fourth-pass brief, which puts workflows before multi-agent
collaboration. Jozef's earlier order put multi-agent first.

### The graph

`partials/niuu-graph-flow.html` draws over the real builder screenshot of Run Flow +
Security + Memory Curation, a bundled workflow. Its paths and boxes were measured
from the builder, so the points follow the real edges. The timing shows one run:
review and security in parallel, a failed security check sent back along the real
dashed edge, then both passing and converging, which `joinMode: all` on Capture
post-mortem requires. It is an illustration of how a run moves, and the caption says
so. It stops with `prefers-reduced-motion`, pauses off-screen, and is hidden on
phones, which get a static crop. If the workflow or its layout changes, regenerate the
screenshot and the partial together.

### The environment drawing

Every box ships. Organizations are tenants on one installation, each seeing what it
owns (`src/niuu/domain/tenancy.py`). Forge is `system` and scopes by caller, Observatory
filters per caller, and shared Memory gives an organization its own mounts. Hosts are
registered in Platform. What is planned stays out of the drawing and is listed below it
as "Planned, not shipped". It replaces the earlier six-part drawing and keeps all six
product names and their links.

## What changed from the fourth pass

| Fourth pass | Decision | Why |
|---|---|---|
| One layout for every step, with tabs | A composition per step, and tiles instead of tabs | Heading, paragraph, screenshot repeated five times read as documentation. |
| The idea, then See NIUU at work | One opening: One agent is a starting point. Not the limit. | Two introductions in a row. |
| Bridge lines between steps | Removed | The brief asks for no extra slogans, and the titles already carry the progression. |
| Graph as one tab | A full-width stage with motion | Workflows are the turning point of the page. |
| Six-part drawing in its own section | The environment drawing at the end of the story | It now shows organizations, shared parts and hosts together. |
| In place today as long paragraphs | Four short columns | The drawing explains the shape, so the text only has to name it. |
| Direction wording | Planned, not shipped | Plainer about what is roadmap. |
| The Activity tile and the Decision view's place | Decision joins the coordinator in step three, Inbox moves to step four | Each sits with the story it demonstrates. |

## What changed from the third pass

| Third pass | Decision | Why |
|---|---|---|
| Four stages in The idea, then seven gallery chapters | One five-step gallery; the stage wording now opens each step | The stages and the chapters were the same story told twice. |
| Workflows as one short strip | Real graphs lead step two, one branching and converging, one with gates and loops | Workflows are what separates this from a session tool, and they were the smallest thing on the page. |
| Continuity and evolution as a section after the gallery | Step four | Continuity is explained once. The four kept items went, because the step shows them. |
| For teams as a section near the end | The end of step five, labelled In place today | Scale and tenancy are the conclusion of the story, not an appendix. |
| Add a machine inside the environment drawing | Step five | Same reason. |
| Activity and One Workspace views | Removed | Each step keeps the views that teach it. One Workspace also implied trust levels that are not enforced. |
| Chapters alternating left and right | One act layout, with tall screens set beside their caption | Screens are larger and the page reads as one thing. |
| Sentence bridges | One short line between steps | The steps did not connect. |

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
| Security | Ships, with limits | OIDC, Cedar policies, scoped workload tokens, OpenShell egress policy and isolated learned tools. Vault or OpenBao as the credential store with session-scoped injection (`openbao_credential_store.py`, `openbao_secret_injection.py`, `charts/niuu/values-openbao.yaml`), and SPIFFE JWT-SVIDs from SPIRE for OpenShell sandbox grants (`openshell-runtime.md`). OpenBao is what runs on ymir; plain Vault rests on the API being compatible. NetworkPolicy templates are off by default, and scoped credentials cover named entry points only. A local install has no login, and the page says so. |
| Multi-tenancy | Ships, with limits | Sessions, Realms, Memory and credentials carry a tenant (`src/niuu/domain/tenancy.py`, migration 000090, `tenant_quotas` in `src/bifrost/config.py`). One organization per user. Isolation is not one single mechanism across all stores. |
| Unrelated customers on one installation | **Direction** | Whether the event bus is tenant-scoped is unverified. The page lists it as direction. |
| Team basics: login, roles, organization-owned residents and Memory | Ships | `src/identity/`, `charts/volundr/values.yaml` `roleMapping`, `resident_runtimes` tenant, Memory `visibility = 'tenant'`. |
| Docker install | Ships, not run end to end here | `scripts/install.sh`, docker mode. The published release has no `install.sh`, and `niuu:latest` dates from July, so the page uses the script on `dev` with `NIUU_IMAGE_TAG=dev`. Script and images checked as public, amd64 and arm64. Docker mode has no login and binds `0.0.0.0` by default, and the page says so. |
| Inviting a colleague into a session | **Direction on the page** | The API ships (`rest_session_participants.py`, viewer and approver), with no app screen. Kubernetes needs `room_role_source: remote`, unproven on a live cluster and incompatible with `enforce_ownership`, which ymir sets. |
| Shared projects, workflows and Workspaces; provider-sourced members | **Direction** | Organizations design phases 1 and 2. Everything a person makes is private to them today, apart from Memory marked for the organization. |
| Adding a machine or cluster | Ships | Platform's Register a new instance wizard (`web-next/apps/niuu/src/GuildPage.tsx`, `POST /api/v1/niuu/instances`): endpoint, TLS pin, tags and auth, probed on register and afterwards. The launch wizard picks a host or tags, and Work dispatch targets tags (`select_adapter_by_tags`). A remote host needs https, a pinned certificate or an explicit plaintext opt-in. Sessions across clusters are proved on the live clusters. `niuu join` is the terminal route, not on the page: it needs `workload_identity.enabled`, off by default, and its own heartbeat supervisor. Registering a second fresh machine was not run here. |
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
| Workflow graphs with branching, loops and gates | Ships | The Branch and converge and Gates views are bundled workflows (`ting-run-flow-security-memory-curation.yaml`, `ticket-planning-flow.yaml`) in the real builder on a local install. Their names had internal words swapped at capture. |
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
- **Real page, sample hosts:** Platform and its register dialog, with sample hosts
  served at capture time, because the demo build has no shared API.
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

A real execution graph, with each stage's state during a run, is the best missing
shot for step two. `WorkflowExecutionGraph` exists, but `workflow_execution.enabled`
is off by default and no agent could run here, so it could not be captured. The
motion on the stage illustrates a run over the real workflow until then.

Still worth recapturing from a real system: all of the demo-build screens, and
Room and Session from a live conversation. The Memory map has no real answer
screen because search over the demo data returns nothing useful, so a search
result is the best missing shot.

Sources for the captures are not kept in the repo. Regenerating them needs the
demo build (`public/config.json`, `demoMode: true`) on a Vite server.
