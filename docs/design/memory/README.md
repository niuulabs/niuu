# Memory for Niuu

Status: design, 2026-11-12, against `dev` at `1f4f9622b`. Nothing here is built.
It reworks Mímir. Today's flows are not kept for their own sake: what stays is
what this design needs.

## 1. What we are building

Agents and people in Niuu share **memory the way a well-run team shares
knowledge**: pages about the things the team works on, the raw sources those
pages came from, specific facts with who said them, and the ability to ask a
question and get a cited answer.

Memory comes in layers. Some of it is **short-lived**: what one session found
while working, or what the sessions of one workflow run hand each other. Some
of it is **long-lived and shared**: a project's memory, used by everyone on the
project and every agent working for them, and the org's memory above it.

It is **multi-user and multi-tenant**. Every person and agent sees exactly the
memory their role gives them, every write is attributed, and no org ever sees
another's memory.

It is **pluggable**. gbrain is the default memory system. Another system can be
plugged in behind the same contract, without agents or the UI changing.

### What it is not

- Not a record of what agents did. Session summaries, reflections and lessons
  an agent distils from its own runs fed its beliefs back to it as evidence,
  and nothing else used them. Their writers were removed in #1282 and #1283.
  No part of Niuu writes to memory on its own at the end of a turn or a session.
- Not a log or a transcript store. Transcripts stay in Forge; memory cites them.
- Not a second agent brain. Deciding what to remember is the agent's or the
  person's choice, made with a tool call, and it can be seen.

## 2. What goes in, and what people ask

Memory is worth having only if it answers the questions people and agents
actually have. These are the ones it is built for.

| Who | Puts in | Asks |
|---|---|---|
| A developer on a project | A runbook, a decision with its reasons, a correction ("the p99 budget is 300 ms, not 500") | "Why did we drop the queue for the booking API?" "Who do I ask about payments?" |
| An agent in a project session | What it found: "the staging DB is pinned to Postgres 15 by the infra chart", with the file as evidence | "What do we know about `booking-api` before I change it?" "Has anyone hit this migration error before?" |
| The sessions of one workflow run | The planner's plan, the implementer's notes, the reviewer's findings | "What did the planner decide about the schema?" |
| A connector | Linear tickets, GitHub PRs, Slack threads of the project's team integrations | — |
| A project owner | Approves what agents propose for the project's memory; curates pages | "What changed in the project's memory this week?" |
| An org admin | The handbook, glossary, customer pages, who's who | "Which customers use the legacy API?" |
| A person, privately | Their own notes and preferences ("I review in the morning; summarise overnight work first") | "What was I doing on Tuesday?" |
| A resident | What it learns stewarding its environment, with evidence | "What did I last see on this host?" |

Every one of these is a page, a source, a fact or a question. None of them is
"what happened in my last turn".

## 3. What memory holds

Five kinds of record. This is gbrain's model; most of it already exists in Niuu.

| Record | What it is | How it changes | Example |
|---|---|---|---|
| **Source** | Raw material, kept as it arrived: a ticket, PR, Slack thread, document, transcript excerpt, web page. Tool output is not a source; records cite it by tool call id, and ingest keeps refusing it (`OPERATIONAL_SOURCE_TYPES`). | Never edited. A newer version is a new source. | `LIN-482` as fetched on 2026-11-10 |
| **Page** | What is known about one subject. Its *compiled truth* (the current state, rewritten as things change) sits above an append-only *timeline* whose every row cites a source or other evidence. A page has a type: service, person, customer, project, decision, runbook, concept, meeting. | Compiled truth is rewritten under a revision check. Timeline rows are only appended. | `services/booking-api` |
| **Fact** | One typed claim about a subject (event, preference, commitment, belief, fact), with when it holds, its evidence and its trust level. | Never deleted. Retired with a reason, or replaced by a newer fact. | "booking-api p99 budget is 300 ms", from `LIN-482`, by a person |
| **Entity** | The subject itself: a stable slug, its aliases, typed links to other entities. Pages and facts hang off entities. | Aliases and links are added; duplicates are merged. | `people/ines-moreau`, alias `@ines` |
| **Answer** | A cited answer to a question, with what memory could not answer. | Derived on demand and never stored as evidence. A person can save one as a page. | "Who owns the booking cut-over?" |

Where each stands today:

| Record | Today |
|---|---|
| Source | `MimirSource` (`niuu/domain/mimir.py:92`), never changed after ingest. |
| Page | The two-zone page format (`src/mimir/FORMAT.md`) is gbrain's compiled truth + timeline. Pages carry no author or trust, and timeline provenance is free text. |
| Fact | A complete typed-fact layer with trust (`niuu/domain/mimir_facts.py`, `MimirFactPort`, `mimir/facts.py`, `gbrain_facts.py`). Off by default, only on the hosted MCP, no screen. |
| Entity | Pages under `wiki/entities/` and `[[slug]]` links. No aliases. |
| Answer | None. The UI's Ask is search plus quoted Key Facts. gbrain's `think` is the only cited-answer path, and nothing calls it. |

### Trust and evidence

Every source, timeline row and fact names its **evidence** as a reference an
operator can open: a source id, a Forge session event, a tool call id, a room
message, a URL, a Git commit, or a person's statement in the UI. Never free
text.

Every write gets a **trust level** from who made the call, never from what the
writer claims. The levels and rules are the ones the fact layer already
enforces (`niuu/domain/mimir_facts.py:9-97`); this design applies them to
pages, timeline rows and sources too.

| Level | Who |
|---|---|
| `person` | A person, in the UI or with their own login |
| `operator` | An owner of the layer confirming, correcting or approving a proposal. Today this comes from one global role, `mimir:curator` (`mimir/config.py:187`); it becomes ownership of the layer. |
| `tool` | A connector importing from an integration. A token acting for someone else is at most `tool`, as today. |
| `agent` | Any harness or resident working in a session |

1. An agent's conclusion never raises confidence. Saying it again in another
   session is not new evidence.
2. Anything derived (a page rewrite, an answer, a summary) gets the lowest
   level of what it was derived from.
3. A lower level never retires or replaces a higher-level record. The attempt
   becomes a proposal.
4. Unattended jobs may add records, never remove them.
5. An answer is never evidence.

## 4. The hierarchy

Memory is a hierarchy of **layers**. A layer is one body of memory with an
owner, a lifetime and a memory system behind it.

```text
org                          long-lived   owned by org admins
└─ project (its members      long-lived   owned by project owners
   │        are the team)
   └─ run                    ephemeral    owned by the run's owner
      └─ session             ephemeral    owned by the session's owner

beside the tree:
person                       long-lived   one person, only their own sessions
resident                     long-lived   a resident's operator
attached                     as its owner says   extra memories a project or launch spec adds
```

A project's members are its team, so team memory is project memory. A session
with no project has `org` and `session`; a session outside a workflow run has
no `run` layer.

| Layer | Exists when | Lives until | Example content |
|---|---|---|---|
| `org` | the org has memory turned on | removed | handbook, glossary, customers, people |
| `project` | the project is created | the project is removed | services, decisions, runbooks, project people |
| `run` | a workflow run starts | the run ends + retention window | plan, implementation notes, review findings |
| `session` | a session launches | the session ends + retention window | what this session found |
| `person` | the person first writes | removed by the person | private notes, preferences |
| `resident` | a resident is created | the resident is removed | what it learns about its environment |
| `attached` | an owner attaches it | detached | another project's memory read-only, a vendor knowledge base |

`attached` is today's project memory mounts (#1303): a project owner, or a launch
spec, adds a registered memory beyond the project's own. They keep their rules:
one name, one target, `read` or `read_write` with write prefixes.

### Reading: one view of every layer

A session reads all its layers at once. Every hit says which layer it came
from. When records disagree:

1. the higher trust level wins;
2. at equal trust, the record that holds more recently wins;
3. the disagreement is shown, not hidden: an answer names both and lists it as
   a gap.

An entity is the same subject in every layer: `people/ines-moreau` in the org
and in a project is one person. Its card merges the layers' pages and facts,
nearest layer first, each part labelled.

### Writing: strict, and narrow by default

A write goes to the **session** layer unless it names another. Writing further
up is governed by rules the agent cannot change:

| Layer | Agent in a session | Project member | Owner of the layer |
|---|---|---|---|
| `session` | writes | — (the session owner promotes) | — |
| `run` | writes, if the session is a member of the run | — | — |
| `project` | writes only under the write prefixes the owners set; anything else becomes a proposal | proposes | writes, approves proposals |
| `org` | proposes | proposes | writes, approves proposals |
| `person` | proposes, and only in that person's own sessions | — | the person writes and approves |
| `resident` | the resident writes | — | the operator curates |
| `attached` | as the attachment says (`read`, or `read_write` with prefixes) | as the attachment says | the memory's own owner |

A **proposal** is a pending change that lands in the target layer only when
its owner approves it, at `operator` level with the original evidence kept.
**Promotion** is a person proposing records from a session or run layer up the
tree. Nothing moves up the hierarchy on its own.

### Ephemeral layers

Forge creates the `run` and `session` layers at launch and deletes them after
a retention window once the run or session ends. During that window the
session's Memory tab shows what it wrote and offers to promote it.

This grows from what exists: workflow resource nodes with
`bindingMode: ephemeral_local` already give a run a local memory
(`ting/domain/workflow_snapshot.py:536`, `session_mcp.py:181-213`), but only on
the pod's or workspace's disk, with no retention and no seeding
(`seed_from_registry_id` is carried and never used).

## 5. What an agent sees

**One memory server per session**, `memory`, over all its layers, the same for
Claude Code, Codex and Ravn. The agent never chooses its layers; Forge derives
them at every launch from the session's org, project, run, owner and launch
spec, as it already derives project mounts (`with_project_memory`,
`volundr/domain/services/session.py:1475`).

| Tool | Does |
|---|---|
| `memory_ask(question)` | Answers from every layer with citations (layer + record) and gaps. |
| `memory_search(query, layers?)` | Ranked hits across layers, each labelled. |
| `memory_read(ref)` | A page, source or fact by reference. |
| `memory_entity(name)` | The merged card: summary, aliases, links, open threads, facts that hold now. |
| `memory_recall(about, kinds?)` | Facts about a subject, highest trust first. |
| `memory_add_source(content or url, kind, layer?)` | Stores raw material. |
| `memory_write_page(ref, compiled_truth, expected_revision, layer?)` | Rewrites a page's current state. |
| `memory_add_timeline(ref, entry, evidence, layer?)` | Appends a cited row to a page's timeline. |
| `memory_remember(fact, about, kind, evidence, layer?)` | Records one fact. |
| `memory_retire(fact, reason)` | Retires a fact the caller is allowed to retire. |
| `memory_propose(layer, change)` | Proposes a change to a layer it may not write. |

Every write result says which layer it went to, or that it became a proposal
and for whom. A write the rules refuse is an error naming the rule, counted,
never a silent downgrade.

The instructions every harness gets, in full:

> Your memory has layers: this session, this run, project X, the org, and your
> owner's private notes. `memory_ask` and `memory_search` read all of them.
> Before working on a subject, read its entity card. Write what you find to
> the session layer with evidence. Write to the project only under its write
> prefixes; otherwise propose. Never store your own conclusions as facts about
> the world without evidence.

**Context at launch.** Forge asks the memory gateway for a context pack: the
entities in the project brief and the session's objective, their open
threads, the facts that hold now, and what changed in the project since the
session owner's last session. It goes to the harness with its instructions.
Its size comes from the model's context window in the Bifröst catalog. What is
left out is listed and counted, never dropped silently.

## 6. The memory gateway

The Mímir service becomes the **memory gateway**: the only thing that talks to
memory systems, and the one place the rules above are enforced. It already
does part of this. Managed instances point sessions at the Mímir API with a
mount selector, and that API resolves tenant and backend for gbrain and Mímir
(`mimir/connections.py:25`, `docs/operator/knowledge-instances.md`).

The gateway:

- resolves a session's layers from Forge when it connects;
- holds every memory system's credentials, so no harness sees them;
- enforces the read and write rules, trust levels and evidence on every call;
- fans reads out across layers and merges them; one unreachable layer fails
  the call with its name, never a partial answer shown as complete;
- answers questions and builds context packs itself, because both cross
  layers that may live in different memory systems; answer model calls go
  through Bifröst and are charged to the session's owner;
- keeps proposals and their approvals.

This closes today's holes, where limits hold only in a per-mount server Forge
starts from a local path:

- A mount reached by URL cannot be limited, so Forge refuses limits on it
  (`volundr/domain/project_memory.py:295`).
- Codex and shell sessions get `mimir_*` commands for the first mount only,
  and the bridge opens the store directly, skipping `--read-only` and
  `--write-prefix` (`mimir/bridge.py:54-66`).
- `PUT` and `DELETE /mimir/routing/rules` have no write check
  (`mimir/router.py:1698-1719`).

Sessions authenticate with their Forge session credential and a new scope,
`forge:memory`, added to `FORGE_SESSION_SCOPES` and the Forge session route
allow-list, with `require_scope("forge:memory")` on the gateway's routes. The
gateway checks the credential against the live session, as Forge does today,
so a stopped session's memory access ends with it. People use their own login.

## 7. Memory systems behind the gateway

A layer lives in a **memory system**: gbrain, or another one plugged in. The
gateway talks to every system through one contract, `MemoryBackend`, which
replaces `MimirPort` (a port shaped around one wiki: `ingest`, `query`, `lint`,
threads).

| Capability | Methods |
|---|---|
| `sources` | `put_source`, `get_source`, `list_sources` |
| `pages` | `get_page`, `put_page(expected_revision)`, `append_timeline`, `list_pages`, `backlinks` |
| `facts` | `remember`, `recall`, `retire`, `entity`: today's `MimirFactPort` |
| `search` | `search(query, kinds)`, returning record references and snippets |
| `changes` | `delta(cursor)`, for "what changed since" |
| `namespaces` | `create_namespace`, `delete_namespace`, so ephemeral layers can be made and removed |

A system declares the capabilities it has. A layer needs all of them except
where its kind says otherwise. Placing a layer on a system that lacks one fails
when the layer is created, never at the first call. Answers, context packs,
merging and the rules live in the gateway, so a system does not need them.

Systems are configured with the dynamic adapter rule:

```yaml
memory:
  systems:
    - adapter: mimir.backends.gbrain.GBrainBackend
      name: acme-brain
      url: http://acme-brain.niuu.svc:3131/mcp
      protocol_version: 1
```

**gbrain is the default** for every layer. It is native for every capability
above, and it publishes its interface as a frozen protocol (memory verbs v1)
with a conformance runner. Pin its version; run its conformance suite and our
eval before each upgrade. Today's `GBrainMimirAdapter` and `GBrainFactAdapter`
are the starting point. Phase 1 must prove that gbrain can remove a whole
source, which ephemeral layers need; if it cannot, ephemeral layers run on a
short-lived gbrain of their own.

**Another system** plugs in by implementing `MemoryBackend` and passing the
same contract tests. Hindsight is the first candidate, on one real job. The
Markdown wiki adapter is retired; its content imports into gbrain.

### Tenancy

- **One gbrain per org**, with its own database and credentials, deployed
  through the existing knowledge deployment port (local and Flux adapters).
  Two orgs never share a memory system instance.
- **Each layer is a namespace** inside its org's system: a gbrain source per
  layer (`org`, `project/<id>`, `run/<id>`, `session/<id>`, `person/<id>`,
  `resident/<id>`). gbrain warns that sources share credentials and are not
  hard isolation; the gateway is the only caller, so its rules are the
  boundary inside an org.
- An `attached` memory may live elsewhere: another project's layer, or a
  separate system an operator registered on the Platform.

## 8. Who may do what

| Person | Org layer | Project layer | Run / session layers | Person layer |
|---|---|---|---|---|
| Org admin | edits, approves, sets connectors | reads only as a project member | — | — |
| Project owner | reads, proposes | edits, approves, sets write prefixes, attaches memories, sets connectors | reads, promotes from their own | their own only |
| Project member | reads, proposes | reads, proposes | reads, promotes from their own | their own only |
| Anyone else | reads, if in the org | nothing: the project reads as not found | nothing | nothing |

These use the project roles that exist today (viewer, contributor, approver,
owner; migration `000119_project_members`) and the same rules as project
memory and team integrations: a non-member learns nothing, not even that the
project exists.

## 9. Getting things in

- **People** write and correct pages and facts in the UI, at `person` level, or
  `operator` level when they own the layer.
- **Connectors** import sources from the project's team integrations (Linear,
  GitHub, Slack) at `tool` level, each linked to its original.
- **Agents** write what they find into the session layer with evidence, at
  `agent` level, and propose upwards.
- **Compiling** turns new sources into page updates. It is a Ting workflow the
  layer's owners schedule, not a background pass inside the memory system. Its
  writes are `agent` level, cite their sources, and only add. gbrain's own
  dream cycle stays off for Niuu layers.

## 10. Screens

Mockups come before any of these is built.

- **Project page, Memory section**: the project's layer, its attached memories,
  write prefixes, connectors, and the proposals waiting for an owner. The first
  project memory mockup shows only attached memories; it needs the project
  layer and proposals added.
- **Session, Memory tab**: what the session wrote, and Promote.
- **Memory browser**: pages, sources, facts and Ask across the layers a person
  can see. It replaces today's Explore and Library views; it is the first
  screen with facts.
- **Proposals**: one inbox per owner, across their layers.

## 11. What happens to today's Mímir

**Kept:**

- the typed-fact domain, its trust rules and its gbrain adapter, switched on;
- the two-zone page format;
- the gbrain page adapter, as the start of the gbrain backend;
- project memory mounts (#1303) as `attached` memories, and the
  `workload_config.mimir` shape that carries them;
- the registry and the knowledge deployment port with its local and Flux
  adapters;
- loud fan-out across mounts (`CompositeMimirAdapter`), moved into the gateway;
- the eval harness, and ranking with its debug breakdown.

**Reshaped:**

- `MimirPort` becomes `MemoryBackend`. Ting's readers (`research.py`,
  `specs.py`, `a2a.py`) and the personas' `deliveries/` pages move to the run
  layer.
- Per-mount `mimir-<name>` servers, the `mimir_*` shims and the two variants of
  `with_memory_instructions` become the one `memory` server and its
  instructions.
- `ephemeral_locals` become run and session layers.
- Reflex reads entity cards from the gateway, scoped to the session.
- The UI's Explore and Library become the memory browser.

**Deleted:**

- the Markdown wiki adapter, its search index, lint and doctor;
- threads on the port and adapters;
- `/dreams`, the dream entries in `/activity`, `/ravns/bindings`;
- `build_mimir_tools`, `MimirQueryTool`, and the unused graph, usage and
  entity-extractor ports;
- the zero-LLM "learning": evidence counted from shared words, the micro-dream
  on ingest, `/evidence` and `/page/revise`;
- `routing/rules`;
- composite's ingest into every mount;
- unused telemetry functions, the unwired `mimir.page.written` event and
  `transcript_targets: ["mimir"]`.

## 12. Order of work

Each step ends with a live proof, not only tests.

1. **Gateway, `MemoryBackend`, gbrain backend, org and project layers, the
   `memory` server.** Proof: a Claude Code session and a Codex session in one
   project both answer "who owns the booking cut-over?" from the project layer
   with a citation; a write outside the write prefixes becomes a proposal for
   both; a session of another org cannot see any of it.
2. **Run and session layers, proposals, promotion.** Proof: the implementer of
   a workflow run reads the planner's decision from the run layer; a person
   promotes one finding to the project, an owner approves it, and it lands at
   `operator` level with its evidence; the run layer is gone after retention.
3. **Connectors and compiling.** Proof: a Linear ticket arrives as a source,
   the compile workflow updates its page, and the timeline row links to the
   ticket.
4. **Person and resident layers, and the screens.** Mockups first.
5. **Delete what section 11 lists.**

## 13. Open questions

1. How long do run and session layers live after they end?
2. Who may promote from a session layer: only its owner, or any project member
   who can see the session?
3. Should a project member's own sessions be able to read their `person` layer
   in a project session, or only outside projects?

## Background

- [Project memory](../../operator/project-memory.md): the mount model the
  `attached` layer keeps.
- [Tenant-owned knowledge instances](../../operator/knowledge-instances.md):
  the gateway and deployment pieces that exist.
- The memory-systems survey of 2026-11-07 (about 90 systems) recommended gbrain
  and set the evidence rules in section 3.
- gbrain: [memory verbs v1](https://github.com/garrytan/gbrain/blob/master/docs/protocol/MEMORY_VERBS_v1.md),
  [recommended schema](https://github.com/garrytan/gbrain/blob/master/docs/GBRAIN_RECOMMENDED_SCHEMA.md).
