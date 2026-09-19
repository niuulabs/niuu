# Research Center — Implementation Guide

A hand-off doc for shipping the Reading-first Research Center prototype (`ting/Research Center.html`) as a real Ting tab inside the Niuu web shell.

> **There is no `tyr` service.** What used to be Tyr is now `ting` — `volundr/src/ting/`. This document targets that namespace.

The product opinion that frames everything below:

> **Ting is the operator console for research campaigns.**
> **Mímir is the durable knowledge store.**
> **Völundr is the live execution surface.**

A research campaign should **not** be modeled as a tracker-backed saga by default.

The cleaner fit is:

- launch the existing `Research Campaign` workflow through Ting's existing
  workflow-launch path
- persist a lightweight campaign record around that launched workflow session
- drive the UI from generic Ting events plus campaign/workflow state plus Mímir
  artifacts

The existing workflow YAML, workflow launch API, and `research-*` personas under
`ravn/personas/` already provide most of the runtime substrate. Most of this
build is a new **view** in the web shell plus a light campaign record /
read-model layer, not a new bounded context.

---

## Contents

1. [Mapping the prototype to existing concepts](#1-mapping-the-prototype-to-existing-concepts)
2. [What already exists](#2-what-already-exists)
3. [What's missing — the actual build list](#3-whats-missing--the-actual-build-list)
4. [Campaign metadata extension](#4-campaign-metadata-extension)
5. [API additions](#5-api-additions)
6. [Mímir conventions + needs](#6-mímir-conventions--needs)
7. [Workflow + personas (already in place)](#7-workflow--personas-already-in-place)
8. [Event flow](#8-event-flow)
9. [Web frontend (plugin-ting additions)](#9-web-frontend-plugin-ting-additions)
10. [Authorization (already in place)](#10-authorization-already-in-place)
11. [Open questions](#11-open-questions)
12. [Sequenced build plan](#12-sequenced-build-plan)

---

## 1. Mapping the prototype to existing concepts

The prototype invented some vocabulary. Translate it back to what Ting already calls things, and the surface area collapses:

| Prototype | Existing concept | Lives in |
|---|---|---|
| Research campaign | lightweight persisted record around a launched workflow session | new thin Ting record / read model |
| Stage (one of 7) | `Phase` | `ting.domain.models.Phase` |
| Persona running on a stage | `Run` | `ting.domain.models.Run` |
| Live activity ticker | Ting event bus activity log + `ting.api.events` SSE, filtered/projected by saga | already there; needs campaign-specific UI composition only |
| "Open notebook" / artifact view | Mímir page at `research/campaigns/{slug}/...` | `mimir` service |
| Sources / critiques / learnings / followups | Sections inside Mímir pages | parsed at read time |
| Confidence | campaign rollup derived from workflow state, `Run.confidence`, and `Phase.confidence` | partly already there; needs campaign projection |
| Demo states (running / blocked / failed / review / published) | projected from campaign record + phase/run state + artifacts | partly already there; needs campaign projection |
| The 7 stages | nodes in `system_workflows.yaml > "Research Campaign"` | already there |
| The broader research persona set | `ravn/personas/research-*.yaml` | partly already there; only 6 are bound today |
| "Run" id (`run/c1-glitnir`) | The Volundr session group spawned for this saga's runs | already there |

**The only genuinely new domain data** is a handful of campaign-level fields the
prototype shows in the wizard (mode, audience, deliverable, success criteria,
constraints) plus a lightweight persisted campaign record that points at the
launched workflow session.

Two other things are new, but they are **projection/UI work**, not new domain entities:

- a research-focused read model that composes workflow/campaign state with Mímir artifacts
- a campaign-centric UI that presents those artifacts as one coherent object

---

## 2. What already exists

Audit before writing any code. Confirm each of these:

### Backend (Python)

```
volundr/src/ting/
├── domain/
│   ├── models.py          ✓  Saga, Phase, Run, RunStatus, PhaseStatus, SagaStatus,
│   │                         ConfidenceEvent, WorkflowDefinition, validate_transition()
│   ├── exceptions.py      ✓  InvalidStateTransitionError, RunNotFoundError, …
│   └── services/
│       ├── run_review.py  ✓  approve/reject/retry/message logic
│       ├── dispatch_service.py
│       ├── notification.py
│       └── …
├── api/
│   ├── sagas.py           ✓  GET/POST/PATCH/DELETE /sagas, SagaListItem,
│   │                         SagaDetailResponse, /sagas/decompose, /sagas/plan
│   ├── workflows.py       ✓  workflows CRUD + direct workflow launch
│   ├── runs.py            ✓  /runs/active, /runs/{id}/review, /approve, /reject,
│   │                         /retry, /message, /messages, /summary
│   ├── phases.py          ✓  GET /sagas/{id}/phases
│   ├── dispatch.py        ✓  /dispatch/queue, /approve, /batch, /{run_id}
│   ├── dispatcher.py      ✓  dispatcher auto-state get/patch
│   ├── events.py          ✓  SSE event stream at /events
│   ├── sessions.py        ✓  Volundr session info
│   └── …
├── adapters/
│   ├── postgres_sagas.py        ✓
│   ├── postgres_workflows.py    ✓
│   ├── postgres_dispatcher.py   ✓
│   ├── sleipnir_event_bridge.py ✓  Sleipnir ↔ Ting domain events
│   ├── volundr_http.py          ✓  spawn Volundr sessions for runs
│   ├── ravn_dispatcher.py       ✓
│   ├── ravn_outcome_handler.py  ✓  consumes ravn.task.completed
│   └── …
├── ports/                       ✓  SagaRepository, WorkflowRepository,
│                                   VolundrPort, GitPort, TrackerPort, LLMPort,
│                                   EventBus, NotificationChannel, …
├── infrastructure/database.py   ✓
└── system_workflows.yaml        ✓  ← contains "Research Campaign" workflow
```

Important implementation state:

- Ting already has a direct workflow launch path at
  `POST /api/v1/ting/workflows/{workflow_id}/launch`. That path launches a
  workflow-backed flock session in Volundr without going through
  `commit_saga()` or tracker project creation.
- `Saga` persistence already includes `workflow_id`, `workflow_version`,
  `workflow_snapshot`, and `instance_id`.
- `Saga` is still tracker-shaped in the core model and create/import paths.
  That makes it a poor default container for trackerless research work unless
  we first decouple tracker linkage from saga identity.
- `Saga` persistence does **not** yet include a generic `metadata` / JSONB bag.
- `SagaRepository.list_sagas()` currently only filters by `owner_id`; there is
  no workflow/status/mode filtering, no pagination, and no cursor support.
- `SagaRepository.get_saga_by_slug()` exists, but is currently a global slug
  lookup rather than an owner-scoped lookup.
- `slug` is globally unique in the current `sagas` table schema. That is useful
  for `/research/{slug}` URLs, but it also means slug semantics are currently
  global, not tenant-local.

### Workflow

`volundr/src/ting/system_workflows.yaml` already contains the `Research Campaign` workflow at version 1.0.0 with all 7 stages, the event edges, and the `binding-research-memory` resource binding with `writePrefixes: [research/, learnings/, followups/]`.

Important reality check: the current workflow only binds these 6 personas directly:

```text
research-framer
research-explorer
research-skeptic
research-synthesist
research-curator
research-publisher
```

Additional persona files such as `research-explorer-breadth.yaml`,
`research-explorer-depth.yaml`, `research-explorer-contrarian.yaml`, and
`research-analyst.yaml` exist in the repo, but they are **not currently wired
into the live `Research Campaign` workflow**.

### Personas

The repo contains a broader research persona set under `volundr/src/ravn/personas/`:

```
research-framer.yaml            ✓  writes brief.md, plan.md
research-explorer.yaml          ✓  writes notes/exploration.md, sources.md
research-explorer-breadth.yaml  ✓  writes notes/breadth.md
research-explorer-depth.yaml    ✓  writes notes/depth.md
research-explorer-contrarian.yaml ✓ writes notes/contrarian.md
research-analyst.yaml           ✓  writes analysis.md, sources.md
research-skeptic.yaml           ✓  writes critique.md
research-synthesist.yaml        ✓  writes final.md
research-curator.yaml           ✓  writes learnings/research/{slug}.md, followups/research/{slug}.md
research-publisher.yaml         ✓  writes manifest.md, promotes pages durable
```

Treat these as **available building blocks**, not as proof that the current
workflow already produces every listed artifact. Today, the bound workflow path
is the 6-persona sequence listed above. The breadth/depth/contrarian/analyst
artifacts become real only if we explicitly extend the workflow to use them.

### Mímir

`volundr/src/mimir/` is the durable store. Conventions you'll use:

- **`research/`** is an established top-level collection (`mimir/adapters/markdown.py:95`).
- **Page format** is documented in `mimir/FORMAT.md` v1.0: YAML frontmatter (type, confidence, related_entities, source_ids) + `## Compiled Truth` zone + `## Timeline` zone.
- Existing `type` values include `topic`, `observation`, and `decision` — all are valid fits for research outputs. The publisher's final pages should usually use `type: topic` or `type: decision` for evaluative-mode outputs.
- Research page publication already has a **tool-level provenance guard**: `mimir_write` / `mimir_publish_files` require non-empty `source_ids` backed by real ingested sources for most `research/...` pages. The current exceptions are `brief.md`, `plan.md`, `manifest.md`, and `sources.md`.

### Web frontend (TypeScript / React in `volundr/web-next/`)

```
packages/plugin-ting/src/
├── domain/                      ✓  saga, plan, workflow, settings, session, dispatcher, …
│   ├── saga.ts                  ✓  domain types matching the backend
│   ├── workflow.ts              ✓
│   └── …
├── adapters/
│   ├── http.ts                  ✓  REST client
│   └── mock.ts                  ✓  in-memory fixture adapter for stories/tests
├── ui/
│   ├── SagasPage.tsx            ✓  the existing sagas list (we'll keep it)
│   ├── SagaDetailPage.tsx       ✓  the existing saga detail (we'll keep it)
│   ├── PlanWizard.tsx           ✓  5-step wizard; **mirror this** for research
│   ├── StageProgressRail.tsx    ✓  **reuse**
│   ├── ConfidenceDriftCard.tsx  ✓  **reuse** (for per-stage confidence drift)
│   ├── StepDots.tsx             ✓  **reuse** in wizard
│   ├── RunMeshCanvas.tsx        ✓  visualization (reference only)
│   ├── DashboardPage.tsx        ✓
│   ├── DispatchView.tsx         ✓
│   ├── WorkflowBuilder/         ✓
│   ├── useSagas.ts              ✓  TanStack hook → GET /sagas
│   ├── useSaga.ts               ✓  TanStack hook → GET /sagas/{id}
│   ├── usePhases.ts             ✓
│   ├── useWorkflows.ts          ✓
│   ├── useRunMessages.ts        ✓
│   ├── TingTopbar.tsx           ✓  dispatcher status chips only
│   ├── TingSubnav.tsx           ✓  currently empty (`return null`)
│   └── TingFooter.tsx           ✓
└── ports.ts                     ✓
```

The new research surfaces should follow the same patterns: domain types in
`domain/research.ts`, hooks in `ui/useResearchCampaigns.ts`, http calls
extending `adapters/http.ts`, UI in `ui/research/*`.

Important reality check: top-level Ting tabs are currently declared in
`packages/plugin-ting/src/index.ts`, not in `TingTopbar.tsx`. `TingTopbar.tsx`
renders dispatcher chips.

---

## 3. What's missing — the actual build list

This is the actual delta. Nothing else is new.

### Backend

- [ ] **Campaign record** — add a lightweight persisted record for launched research workflows: slug, name, workflow_id, workflow_version, session_id, owner_id, mode, audience, deliverable, success, constraints, timestamps, and optional status rollups.
- [ ] **Research-list view endpoint** — `GET /api/v1/ting/research/campaigns` returns campaign records plus rollups (artifact counts, last activity).
- [ ] **Research-detail view endpoint** — `GET /api/v1/ting/research/campaigns/{slug}` composes campaign record + workflow/phases/runs + artifact list from Mímir, in one response the UI can render.
- [ ] **Wizard endpoint** — `POST /api/v1/ting/research/campaigns` accepts the wizard payload, launches the selected workflow via the existing workflow-launch path, then persists the campaign record.
- [ ] **Artifact listing** — `GET /api/v1/ting/research/campaigns/{slug}/artifacts` lists Mímir pages under the campaign prefix. This is a thin Ting endpoint, but it depends on Mímir exposing enough listing/filtering support.
- [ ] **Structured-block parsers** — small library that reads `sources.md`, `critique.md`, `learnings/research/{slug}.md`, `followups/research/{slug}.md` and returns typed arrays for the UI.
- [ ] **Activity history projection** — seed the campaign detail page from the generic `GET /api/v1/ting/dispatcher/log?limit=...` ring buffer plus client/server-side saga filtering. Do not add a research-specific activity endpoint unless the generic event model proves insufficient.
- [ ] **Monitoring scheduler** — if `mode=monitoring`, re-dispatch on cadence. Sleipnir delayed messages are the best fit; avoid inventing a parallel scheduler in Ting unless forced.

### Mímir

- [ ] **Prefix-listing or equivalent listing filter** — Mímir currently exposes `GET /mimir/pages` and `GET /mimir/page`, but not an obvious prefix filter in the current router. Ting needs some way to enumerate pages for one campaign without fetching the entire mount.
- [ ] **Provenance fields** — confirm `mimir/FORMAT.md` frontmatter supports `committed_by`, `committed_at`, `source_campaign` (or equivalent). Add if missing. The Sources/Memory tabs depend on these.
- [ ] **Research publish contract alignment** — Ravn currently validates `research.completed` outcomes against a `page_path` plus page-level provenance checks, but `research-publisher` currently reports `manifest_path`. Align those contracts before relying on a clean automated "published" terminal state.
- [ ] **Side artifact update semantics** — `learnings/research/{slug}.md` and `followups/research/{slug}.md` may accumulate across runs in monitoring mode. Confirm whether the curator appends, merges, or replaces.

### Personas

- [ ] **Working-thesis field** — explorer should emit a structured "working thesis (one paragraph)" at the top of `notes/exploration.md` so the running-state hero can render it. Update `research-explorer.yaml` template.
- [ ] **Mode interpretation** — verify the framer respects `mode` from the trigger payload (exploratory / evaluative / investigative / monitoring) and shapes the brief accordingly. Today the framer doesn't get mode as input — needs to.
- [ ] **Publisher outcome contract** — align `research-publisher.yaml` with Ravn's current `research.completed` validation path (`page_path`, `source_ids`, and `produced_by_thread` expectations).

### Web frontend (plugin-ting)

- [ ] **Routes** — `/ting/research`, `/ting/research/new`, `/ting/research/:slug` in plugin-ting's router. (Add a tab in `packages/plugin-ting/src/index.ts`.)
- [ ] **Domain types** — `domain/research.ts` extending Saga.
- [ ] **Service contract** — either extend `ITingService` in `ports.ts` with research methods or introduce a dedicated `IResearchService`. Right now there is no frontend service seam for research-specific endpoints.
- [ ] **HTTP adapter** — `adapters/http.ts` add `getResearchCampaigns`, `getResearchCampaign`, `createResearchCampaign`, `getArtifact`.
- [ ] **Hooks** — `useResearchCampaigns`, `useResearchCampaign`, `useArtifact`, `useSources`, `useCritiques`.
- [ ] **UI components** — `ui/research/`:
  - `IndexPage.tsx` + `MetricsStrip.tsx` + `CampaignCard.tsx` + `CampaignRow.tsx`
  - `NewWizard/` mirroring `PlanWizard.tsx` (5 steps: Question / Mode / Scope / Constraints / Confirm)
  - `CampaignDetail.tsx` — Reading layout (the prototype's `ConceptReading`)
  - `StateStrip.tsx`, `HeroAnswer.tsx`, `ProseAnnotated.tsx`, `Collapsible.tsx`
  - `SideDrawer/` — `FilesView.tsx`, `SourcesView.tsx`, `CritiquesView.tsx`, `OperatorView.tsx`
  - `MemoryView.tsx`
- [ ] **Live updates** — wire to existing `/api/v1/ting/events` SSE for activity ticker.
- [ ] **Drawer URL state** — `?drawer=files&file=brief.md` for deep links.

That's it. Compare to the prototype's file list; nothing else gets ported.

---

## 4. Campaign metadata extension

The wizard collects five values the existing workflow launch payload doesn't have:

```
mode:        'exploratory' | 'evaluative' | 'investigative' | 'monitoring'
audience:    string
deliverable: string
success:     string
constraints: string
```

**Option A:** add a lightweight `research_campaigns` table keyed by campaign id
or slug, with a `metadata JSONB` bag plus the workflow/session linkage.

Example shape:

```sql
CREATE TABLE research_campaigns (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  owner_id TEXT NOT NULL,
  workflow_id UUID NOT NULL,
  workflow_version TEXT,
  session_id TEXT,
  session_name TEXT,
  status TEXT NOT NULL DEFAULT 'ACTIVE',
  metadata JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

This keeps research persistence separate from the tracker-shaped saga table and
lets us reuse workflow launch without pretending there is a tracker project
behind the work.

The research API reads/writes `ResearchCampaign.metadata = { mode, audience, deliverable, success, constraints, monitoring_cadence? }`.

Cleanest because:
- It does not force trackerless research into the saga table.
- It points cleanly at the launched workflow session.
- The wizard's payload becomes one write after launch succeeds.

**Option B:** add a generic `workflow_runs` / `workflow_campaigns` table instead
of a research-specific one if you want the same pattern reusable for other
trackerless workflow products.

The frontend's `domain/research.ts` defines:

```ts
export interface ResearchMetadata {
  mode: 'exploratory' | 'evaluative' | 'investigative' | 'monitoring';
  audience: string;
  deliverable: string;
  success: string;
  constraints: string;
  monitoringCadence?: 'hourly' | 'daily' | 'weekly';
}

export interface ResearchCampaign {
  id: string;
  slug: string;
  name: string;
  ownerId: string;
  workflowId: string;
  workflowVersion?: string | null;
  sessionId?: string | null;
  sessionName?: string | null;
  status: string;
  createdAt: string;
  research: ResearchMetadata;
  // computed:
  artifactCounts: { local: number; reviewReady: number; published: number };
  lastActivityAt: string | null;
}
```

---

## 5. API additions

Land these under `ting.api.research` (new module). Some can be thin wrappers
over existing services + Mímir reads; others need real projection/composition
logic.

```python
# volundr/src/ting/api/research.py
from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel
from ting.ports.saga_repository import SagaRepository
from ting.ports.workflow_repository import WorkflowRepository
from ting.api.sagas import _resolve_selected_workflow  # reuse private helper
# … etc
```

Reality check:

- Reusing `_resolve_selected_workflow()` is sensible.
- Reusing the workflow repository and workflow launch path is sensible.
- Reusing the existing saga detail response as-is is **not** sufficient for the
  research page because the existing saga detail endpoint is tracker-hydrated,
  and research should not depend on tracker-backed saga shape.
- Slug-based campaign routes still need careful owner scoping.

### Routes

```
GET    /api/v1/ting/research/campaigns
       ?state=ACTIVE,COMPLETE&owner=me&mode=evaluative&q=...&limit=50&cursor=...
       → { campaigns: ResearchCampaign[], nextCursor }

POST   /api/v1/ting/research/campaigns
       body: { question, mode, audience, deliverable, success, constraints, tools?, budgets? }
       → ResearchCampaign  (workflow launched + campaign record created)

GET    /api/v1/ting/research/campaigns/{slug}
       → ResearchCampaign (saga + phases + runs + artifact summaries)

PATCH  /api/v1/ting/research/campaigns/{slug}
       body: Partial<ResearchMetadata>
       → ResearchCampaign

DELETE /api/v1/ting/research/campaigns/{slug}
       → 204  (delegates to existing saga delete)

POST   /api/v1/ting/research/campaigns/{slug}/dispatch
POST   /api/v1/ting/research/campaigns/{slug}/pause
POST   /api/v1/ting/research/campaigns/{slug}/resume
POST   /api/v1/ting/research/campaigns/{slug}/skip-stage
POST   /api/v1/ting/research/campaigns/{slug}/publish
POST   /api/v1/ting/research/campaigns/{slug}/retry
POST   /api/v1/ting/research/campaigns/{slug}/archive

# These mostly forward to existing ting endpoints. They exist as research/* aliases
# so the URL/permissions are scoped and you don't surface saga internals.
# Important: some of these are not true "thin aliases" yet because pause/resume/
# publish/archive are not first-class saga operations in the current model, and
# skip-stage has no existing generic API analogue.

GET    /api/v1/ting/research/campaigns/{slug}/artifacts
       → Artifact[]   (lists Mímir pages under research/campaigns/{slug}/)

GET    /api/v1/ting/research/campaigns/{slug}/artifact
       ?path=notes/exploration.md&format=markdown|html|json
       → file content via Mímir read

# Prefer a query-param path for nested artifact paths unless you deliberately
# use FastAPI's catch-all `{path:path}` route syntax.

GET    /api/v1/ting/research/campaigns/{slug}/sources    → Source[]   (parsed from sources.md)
GET    /api/v1/ting/research/campaigns/{slug}/critiques  → Critique[] (parsed from critique.md)
GET    /api/v1/ting/research/campaigns/{slug}/learnings  → Learning[] (parsed from learnings/research/{slug}.md)
GET    /api/v1/ting/research/campaigns/{slug}/followups  → Followup[] (parsed from followups/research/{slug}.md)
```

### What lives in the response

`GET /api/v1/ting/research/campaigns/{slug}` shape (one request, the whole page):

```ts
{
  campaign: ResearchCampaign,      // persisted campaign record
  research: ResearchMetadata,      // from saga.metadata.research
  phases: Phase[],                 // existing shape or workflow-derived equivalent
  artifacts: Artifact[],           // listing from Mímir under research/campaigns/{slug}/
  rollups: {
    confidence: number | null,
    confidenceLabel: 'low' | 'med' | 'high' | null,
    sources: number,
    critiques: number,
    learnings: number,
    followups: number,
    lastActivityAt: string | null,
    elapsedMs: number,
  },
  links: {
    volundr: string | null,        // e.g. volundr://runs/{run_id}
    mimirRoot: string,             // mimir://research/campaigns/{slug}/
  },
}
```

If this endpoint is too heavy or too coupled, a pragmatic fallback is:

- one research summary endpoint for the page shell
- separate artifact/source/critique endpoints
- client-side composition via TanStack Query

### Live updates

**Reuse the existing `GET /api/v1/ting/events` SSE for live updates.** It already streams Ting domain events; the frontend should subscribe and filter/project by saga id rather than adding research-specific SSE topics.

Important reality check:

- Ting's public SSE/event-bus layer already emits the **generic** events we
  should build on: `saga.created`, `saga.completed`, `run.state_changed`,
  dispatcher state, and help-needed signals.
- The in-memory event bus only snapshots `dispatcher.state`; everything else is
  activity-log / live-stream only.
- For this feature, keep the event model generic. The Research Center should
  interpret existing saga/run events plus REST data; it should not require a
  parallel research-specific event surface.

For the prototype's activity ticker:

- use `GET /api/v1/ting/dispatcher/log?limit=...` as the seed history if you
  want a recent event tail quickly
- then subscribe to `GET /api/v1/ting/events` for live updates
- filter/project those generic Ting events by saga id and current page context
- derive stage movement from persisted phase/run state when needed instead of
  inventing research-only event aliases

No new WebSocket needed.

### Reused vs new code

| Concern | Source |
|---|---|
| Workflow launch | `ting.api.workflows` (already exists) |
| Run review (approve/reject/retry/message) | `ting.api.runs` (already exists) |
| Phase / run state | existing Ting phase/run shapes are reusable, but the campaign container should not assume tracker-backed saga identity |
| Workflow lookup | `ting.api.workflows` (already exists) |
| Dispatch | `ting.api.dispatch` + `ting.api.dispatcher` (already exists) |
| Event stream | `ting.api.events` (SSE; already exists) |
| Volundr session spawn | `ting.adapters.volundr_http` (already exists) |
| Sleipnir event bus | `ting.adapters.sleipnir_event_bridge` (already exists) |
| Mímir read | new thin client (Mímir REST already supports it) |
| Mímir prefix list | new (Mímir REST may need an endpoint added) |
| Source/critique parsers | new (small markdown-parser, ~150 lines) |

---

## 6. Mímir conventions + needs

### Path conventions (already established by the personas)

```
research/campaigns/{slug}/brief.md
research/campaigns/{slug}/plan.md
research/campaigns/{slug}/notes/exploration.md
research/campaigns/{slug}/sources.md            (normalized source register)
research/campaigns/{slug}/critique.md           (skeptic)
research/campaigns/{slug}/final.md              (synthesist)
research/campaigns/{slug}/manifest.md           (publisher inventory)

learnings/research/{slug}.md                    (curator-managed side artifact)
followups/research/{slug}.md                    (curator-managed side artifact)
```

The following are **candidate v2 artifacts**, not guaranteed by the current
workflow wiring:

```text
research/campaigns/{slug}/notes/breadth.md
research/campaigns/{slug}/notes/depth.md
research/campaigns/{slug}/notes/contrarian.md
research/campaigns/{slug}/analysis.md
```

### Publish state mapping

The prototype distinguishes `local | review-ready | published`. Mímir, today, just has "exists / doesn't exist". For the UI to show three states, surface page presence + a flag.

Options:
- Use Mímir frontmatter `publish_state` field on each page. The publisher sets to `published` on commit, the explorer leaves at `local` or `review-ready`.
- Or: derive in Ting. Anything written but not in the publisher's `manifest.md` is `review-ready`. Anything in the manifest is `published`. Anything matching an expected artifact path that has no entry yet is `local` (notebook-only).

Recommend: **derive in Ting**, no schema change to Mímir. The publisher's manifest is the source of truth for "what's durable".

Reality check: this is a UI/read-model convention, not a current Mímir concept.
Mímir today gives you pages, frontmatter, zones, and raw source references. It
does not natively track "local vs review-ready vs published" for research pages.

### Provenance fields

The Sources tab and Memory tab show "who committed this, when, from which campaign". `mimir/FORMAT.md` v1.0 formally supports `type`, `confidence`, `entity_type`, `related_entities`, and `source_ids`. It does **not** currently document `campaign_id`, `committed_by`, or `committed_at` as standard fields. Add them explicitly if we want to rely on them:

```yaml
---
type: topic
confidence: high
source_ids: [src_abc123]
campaign_id: rc-2026-04-29-local-model-serving  # ← new
committed_by: research-publisher                 # ← new
committed_at: 2026-04-22T17:09:00Z               # ← new (or rely on Mímir's own timestamp)
---
```

Also note two current implementation details that matter:

- `produced_by_thread` exists on `MimirPageMeta` as runtime metadata, but it is
  **not** one of the documented standard frontmatter fields in
  `mimir/FORMAT.md`.
- The current Markdown Mímir adapter does not obviously project
  `produced_by_thread` out of ordinary page frontmatter during `list_pages()` /
  `get_page()` metadata construction. If research completion depends on that
  flag, the write/read contract needs explicit alignment.

### Mímir read API

Ting needs:

- **`GET /mimir/page?path=...`** — already supported.
- **Campaign-scoped listing** — likely new work. Today the router clearly exposes `GET /mimir/pages` with `category` and `mount` filters, but not an obvious `prefix` query in the current implementation. If we want efficient artifact enumeration, add either `prefix` support or a dedicated campaign listing endpoint in `mimir/router.py`.

The underlying `MimirPort.list_pages()` contract is also category-oriented
today, not prefix-oriented.

### Quotas

The prototype's "blocked" demo state is a Mímir write-quota exhaustion (the Edge Power Budget campaign). I did **not** verify a dedicated quota subsystem in the code paths reviewed here, so treat this as a deployment/runtime concern to validate explicitly. If quota limits are enabled, ensure publisher persona runs fail gracefully and emit a structured blocked reason.

---

## 7. Workflow + personas (already in place)

### Workflow

`Research Campaign` lives in `volundr/src/ting/system_workflows.yaml`. Re-confirm:

- 7 stages in this exact order: `frame → explore → challenge → synthesis → curation → publish → complete`
- Each stage is a sequential `kind: stage` node with `joinMode: any`
- Edges fire on the corresponding `research.{event}` workflow outcome topics
- Memory binding `binding-research-memory` allows writes to `research/`, `learnings/`, `followups/` prefixes

Also be explicit that this is the **current 6-persona version** of the
workflow. If you want breadth/depth/contrarian/analyst artifacts, that is a
workflow expansion, not just a UI read model.

If you change stage order or add a stage:
1. Bump `version` (major).
2. Migrate active sagas by re-projecting their `Phase` rows; mark the old version's sagas as legacy.
3. Update the prototype's UI stage count assumption (currently hardcoded `7/7`).

### Personas

All exist under `volundr/src/ravn/personas/research-*.yaml`. No new personas. Two small updates:

- **`research-framer.yaml`** — the prompt already describes a suggested `mode` in the initiative context. The missing piece is making sure the dispatch payload actually supplies it consistently.
- **`research-explorer.yaml`** — currently writes `notes/exploration.md` plus `sources.md` and requires YAML `source_ids`, but it does **not** currently emit a dedicated structured "working thesis" block for the hero.
- **`research-publisher.yaml`** — currently writes / reports `manifest_path`, but the Ravn-side validation path for `research.completed` is page-centric (`page_path` plus provenance checks). That contract should be aligned before treating publish completion as fully production-ready.

Optional v2:
- **`research-monitor.yaml`** — a thin re-dispatcher persona that schedules monitoring-mode re-runs. Or do this in the framer with cron via Sleipnir delayed messages.

---

## 8. Event flow

The workflow YAML already defines the **workflow-internal** happy-path stage
sequence. Reproduced for reference:

```
research.requested       ← workflow trigger / run kickoff
  → research.framed      ← framer wrote brief.md + plan.md
  → research.explored    ← explorer wrote notes/exploration.md + sources.md
  → research.challenged  ← skeptic wrote critique.md
  → research.synthesized ← synthesist wrote final.md
  → research.curated     ← curator wrote learnings/* + followups/*
  → research.completed   ← publisher finished durable publication work
```

Side events:
- `research.blocked` — workflow-level blocked outcome; proposed handling is to
  keep saga `ACTIVE` but mark the active phase `GATED` with a reason.
- `research.failed` — fatal workflow failure; saga transitions to FAILED.

Reality check:

- The `research.*` events above are real in the workflow graph and persona
  outcome maps.
- Ting's **public** event bus and SSE surface today are generic:
  `saga.created`, `saga.completed`, `run.state_changed`, dispatcher events,
  help-needed events.
- `ting.adapters.sleipnir_event_bridge.py` is a generic Ting-to-Sleipnir
  mirror, which is exactly the direction we should preserve.
- `RavnOutcomeHandler` and `ActivitySubscriber` already normalize flock
  completion into Ting's generic review/completion pipeline.

So the Research Center should be modeled through the existing generic Ting
event stream plus REST reads of saga/phases/runs/artifacts. Avoid introducing a
research-specific SSE contract unless we later prove the generic model cannot
carry the UX cleanly.

---

## 9. Web frontend (plugin-ting additions)

The prototype is React + Babel-standalone (one HTML file). For production it lives inside `volundr/web-next/packages/plugin-ting/`.

### Routes

Add to plugin-ting's router in `packages/plugin-ting/src/index.ts`:

```
/ting/research                       → IndexPage
/ting/research/new                   → NewWizard
/ting/research/{slug}                → CampaignDetail (Reading)
/ting/research/{slug}?drawer=files&file=brief.md
                                     → CampaignDetail, drawer open at file
/ting/research/{slug}?drawer=sources&n=s1
                                     → CampaignDetail, drawer at source
```

### Top bar

Add a Research tab to the plugin descriptor in `packages/plugin-ting/src/index.ts`, peer of the existing Dashboard / Sagas / Dispatch / Plan / Workflows tabs. `TingTopbar.tsx` should stay focused on dispatcher status unless we explicitly want research chips there too.

`TingSubnav.tsx` is currently empty, so this feature cannot assume there is an
existing Ting sidebar shell to extend.

### Component map

| Prototype file | Production location | Notes |
|---|---|---|
| `research-data.jsx` | n/a | replaced by hooks + http |
| `research-components.jsx` | split across `ui/research/` | StatePip, ModeChip, MimirPip, ConfidenceBadge stay near where they're used. The `StageProgressRail` already exists — **reuse it**, don't duplicate. |
| `research-index.jsx` (IndexPage) | `ui/research/IndexPage.tsx` + `MetricsStrip.tsx` + `CampaignCard.tsx` | Lifts from prototype 1:1. |
| `research-index.jsx` (ResearchNew wizard) | `ui/research/NewWizard/*` | Mirror `PlanWizard.tsx` structure. Reuse `StepDots`. |
| `concept-reading.jsx` (`ConceptReading`) | `ui/research/CampaignDetail.tsx` | **The hero. The only detail layout.** |
| `concept-reading.jsx` (`HeroAnswer`) | `ui/research/HeroAnswer.tsx` | State-aware switch. |
| `concept-reading.jsx` (`StateStrip`) | `ui/research/StateStrip.tsx` | Reuse `StageProgressRail`'s tick logic if possible. |
| `concept-reading.jsx` (`ProseAnnotated`) | `ui/prose/ProseAnnotated.tsx` | Promote to shared lib — useful elsewhere. |
| `concept-reading.jsx` (`Collapsible`) | `@niuulabs/ui` | Promote to shared lib. |
| `concept-reading.jsx` (`MemoryViewLinked`) | `ui/research/MemoryView.tsx` | |
| `side-drawer.jsx` | `ui/research/SideDrawer/*` | `index.tsx`, `FilesView.tsx`, `SourcesView.tsx`, `CritiquesView.tsx`, `OperatorView.tsx`. |
| `concepts-ab.jsx`, `concepts-cd.jsx` | **do not port** | Alt layouts. Prototype only. |

### Data flow

```
URL (TanStack Router)
  → useResearchCampaign(slug)  ← new hook
  → http.getResearchCampaign(slug)  ← new
  → GET /api/v1/ting/research/campaigns/{slug}
        ↓
  TanStack Query cache
        ↓
  SSE /api/v1/ting/events  ← invalidates cache on relevant generic Ting events
```

The existing `useSaga`, `useSagas`, and `usePhases` hooks are very small and
are good composition points, but there is no existing campaign-specific hook or
cache key structure yet.

For the activity panel specifically, there is already a generic
`GET /api/v1/ting/dispatcher/log?limit=...` endpoint backed by the Ting event
bus ring buffer. It is useful as a starting point, but it is not
campaign-filtered. The right move is to filter/project it by saga in the client
or a thin generic server projection, not to add a research-only event channel.

Drawer state lives in URL (`?drawer=files&file=brief.md`) so deep links work. A small Zustand store can mirror it for derived UI.

### Reading-mode UX rules (carry forward from prototype)

- **Clean** mode default. Citation chips inline, hover popover for preview.
- **Annotated** mode opt-in. Right gutter renders source/critique cards aligned to citing paragraph.
- **Drawer overlays, never pushes.** Position absolute over right ~560px; underlying page does not reflow.
- **`Esc` closes drawer.** `←/→` step through items within the active tab. (Optional.)

### Reused-component checklist

| Existing plugin-ting component | Use for research |
|---|---|
| `StageProgressRail.tsx` | The 7-tick state strip. **Reuse, do not duplicate.** |
| `ConfidenceDriftCard.tsx` | Optional per-stage confidence drift in detail. |
| `StepDots.tsx` | New wizard. |
| `useSaga.ts` / `useSagas.ts` | Underlying queries (research hooks wrap these). |
| `usePhases.ts` | Phase data for the stage rail. |
| `useRunMessages.ts` | If we ever expose per-run chat (probably not v1). |
| `WorkflowLaunchModal.tsx` | Reference for how direct workflow launch is currently wired. |

---

## 10. Authorization (already in place)

Existing Ting ownership/policy patterns are still relevant, but research
campaigns should not be forced through saga semantics just to get auth.
Cerbos policies live under `cerbos/policies/`.

Current explicit reality:

- `saga.yaml` grants owners `*`
- `saga.yaml` grants owners and tenant members `read` / `list`
- it does **not** currently spell out a separate tenant-admin mutation role in
  this file

So for research endpoints:

- `read`: tenant member is already a plausible baseline reuse
- owner mutations are already plausible via a campaign policy that mirrors saga
  ownership semantics
- broader mutation rights (`publish`, `archive`, admin-driven overrides, etc.)
  should be treated as policy design work, not as something already granted
- `delete` should continue to mean "delete the campaign record / coordination state",
  not "delete durable Mímir pages", unless we intentionally add a destructive
  memory cleanup flow

---

## 11. Open questions

1. **Auto-publish or human gate?** Prototype assumes auto-publish after curation. The current workflow has no explicit human review gate. If a gate is wanted, add a `review-required` flag in campaign metadata and an explicit projection rule for how that maps onto run/phase state.

2. **Confidence rollup vs drift.** Per-phase confidence is already present in `Phase.confidence`, and runs also carry confidence. Decide whether the campaign record carries its own rollup or whether the UI derives it.

3. **Working-thesis emission.** The running-state hero needs the explorer to emit a structured "tentative answer" block. Update `research-explorer.yaml` template; agree on the format (YAML frontmatter field or `## Working thesis` H2 section).

4. **Monitoring cadence.** Where does the cron live — in framer-as-scheduler, in a separate persona, or in Ting? Recommend: Ting's research module schedules via Sleipnir delayed messages and re-dispatches a fresh workflow run on cadence. Doesn't fight the existing workflow.

5. **Diff view for monitoring runs.** Monitoring's whole value is the diff vs last run. Not in the prototype. v2 design.

6. **Citation stability.** `[sN]` tokens — if a source is added/removed mid-run, do existing citations shift? Recommend: never renumber; tombstone removed sources. Enforce in the publisher's normalizer.

7. **Cross-campaign citations.** Can `final.md` of campaign A cite a source from campaign B? Via Mímir paths, yes. The UI's citation chip resolver assumes intra-campaign. Out of scope for v1.

8. **Mímir prefix-list endpoint.** Verify it exists. If not, add it. Without it, Ting can't enumerate artifacts.

9. **Side-artifact append semantics.** `learnings/research/{slug}.md` and `followups/research/{slug}.md` grow with each monitoring run. Does the curator merge or replace? Recommend: append section with timestamp heading; never overwrite.

10. **Plugin/router seam.** Adding a Research tab means adding a route and tab declaration in `packages/plugin-ting/src/index.ts`. The shell already uses TanStack Router; this is mostly a plugin integration check, not a router-framework unknown.

11. **Slug scope.** If research gets its own campaign table, decide whether
slugs are globally unique or owner-scoped. Prefer making that decision
explicitly rather than inheriting `sagas.slug` semantics accidentally.

12. **Publish validation contract.** `research-publisher` currently emits
`manifest_path`, while Ravn's `research.completed` validation currently expects
`page_path` and checks `produced_by_thread` plus `source_ids` provenance on that
page. Align this before treating automated publish completion as settled.

13. **Frontend service seam.** Research endpoints do not currently fit into an
existing `ITingService` method set. Decide whether to extend `ITingService` or
add a dedicated `IResearchService`.

14. **Event modeling discipline.** Keep the Research Center on top of generic
Ting SSE/activity primitives. Resist introducing research-only SSE/event types
unless a concrete UX need cannot be satisfied by saga/run events plus REST
state.

---

## 12. Sequenced build plan

A pragmatic order. Estimate is still roughly right, but only if we are honest
about the missing projection/API work and do not assume the richer 8-persona
artifact tree already exists in production.

### Phase 1 — Backend wiring (2 days)

1. Add the lightweight `research_campaigns` table (or a generic workflow-run/campaign table).
2. New module `ting/api/research.py` with the seven routes from §5.
3. Add a repository for campaign records with status filtering, owner-safe slug resolution, and any pagination you want on the research index.
4. Reuse `WorkflowRepository`, the existing workflow launch path, and the workflow launch patterns from `ting/api/workflows.py`.
5. Wire research endpoints in `ting/plugin.py`.

**Ship gate:** `curl POST /api/v1/ting/research/campaigns -d '{question, mode, ...}'` launches the right workflow and persists a campaign record with the right metadata.

### Phase 2 — Mímir read path (2 days)

1. Confirm or add Mímir's prefix-list endpoint.
2. Add small Mímir HTTP client in `ting/adapters/mimir_http.py` (mirrors `volundr_http.py`).
3. Add structured-block parsers (sources / critiques / learnings / followups) — `~150` lines total.
4. Reuse `GET /api/v1/ting/dispatcher/log?limit=...` plus `GET /api/v1/ting/events`, with saga-based filtering/projection.
5. Wire `GET /artifacts`, `GET /artifacts/{path}`, `/sources`, `/critiques`, `/learnings`, `/followups`.

**Ship gate:** Hitting `/artifacts` for a real campaign returns the list of pages currently in Mímir.

### Phase 3 — Persona small fixes (1 day)

1. Update `research-framer.yaml` to accept `mode` from trigger payload + branch on it.
2. Update `research-explorer.yaml` to emit a structured working-thesis section.
3. Align `research-publisher.yaml` with the current `research.completed` validation contract (`page_path` / provenance expectations), or relax the validator deliberately.
4. Keep the UI on generic Ting events + read-model invalidation; do not add research-specific SSE mirroring unless a later concrete gap demands it.

**Ship gate:** Dispatching a test saga moves through all 7 stages in dev env and writes the expected pages.

### Phase 4 — Web shell tab + index (2 days)

1. Add tab in `packages/plugin-ting/src/index.ts`.
2. New routes in plugin-ting.
3. Extend `ITingService` or add `IResearchService`; then wire `domain/research.ts`, `adapters/http.ts`, and `useResearchCampaigns`.
4. `IndexPage.tsx`, `MetricsStrip.tsx`, `CampaignCard.tsx`, `CampaignRow.tsx`.

**Ship gate:** The Research tab route lists real campaigns from the backend.

### Phase 5 — Wizard (1 day)

1. Mirror `PlanWizard.tsx` → `NewWizard/`.
2. `usePlanWizard.ts` clone → `useResearchWizard.ts`.
3. Reuse `StepDots`.
4. POST hook + redirect to detail page.

**Ship gate:** A user can dispatch a real campaign from the web UI.

### Phase 6 — Detail reading view (3 days)

1. `CampaignDetail.tsx`, `StateStrip.tsx`, `HeroAnswer.tsx` (state machine on `SagaStatus + active Phase`).
2. `ProseAnnotated.tsx` with markdown → JSX + inline citation chips.
3. Section collapsibles: Evidence (sources table), Skeptic's pass (critique cards), Learnings & follow-ups, Durable memory.
4. Clean / Annotated toggle.

**Ship gate:** A published campaign reads end-to-end with citations resolving.

### Phase 7 — SideDrawer + URL state (2 days)

1. Drawer shell with 4 tabs.
2. All "open" affordances on the page route here.
3. URL-encoded `?drawer=…&file=…` for deep links.
4. SSE-backed Activity tab.

**Ship gate:** Every "open" link in the prototype works in production. Reload preserves drawer.

### Phase 8 — States + polish (1.5 days)

1. Blocked / failed / draft hero variants.
2. Retry / Resume / Skip / Pause actions wired to real backend behavior, not just assumed aliases. Some of these may require new projection or orchestration logic rather than direct passthrough.
3. First-run empty state on index.
4. Visual regression snapshots via Storybook (existing infra in `web-next/.storybook`).

**Ship gate:** Every demo state in the prototype's Tweaks panel is reachable with real data.

---

## Appendix A — Prototype file map (porting reference)

```
ting/                                  (prototype, not production)
├── Research Center.html
├── tokens.css                         ← already exists in @niuulabs/design-tokens
├── styles.css                         ← rewrite as CSS modules / Tailwind in plugin-ting
├── research-data.jsx                  ← drop entirely; replaced by hooks + http
├── research-components.jsx            ← split into plugin-ting/ui/research/*
├── research-index.jsx                 ← IndexPage + Wizard
├── concept-reading.jsx                ← CampaignDetail (the one that ships)
├── side-drawer.jsx                    ← SideDrawer/
├── concepts-ab.jsx                    ← DO NOT PORT (alt layouts)
├── concepts-cd.jsx                    ← DO NOT PORT (alt layouts)
├── app.jsx                            ← drop; replaced by plugin-ting router + shell
└── tweaks-panel.jsx                   ← drop; Tweaks is a prototype-only host feature
```

The "alt layouts" (Mission Control, Chronicle, Studio, Atlas) stay in the prototype as visual reference but are **NOT** part of the production build.

---

## Appendix B — Smallest possible v1

If 3 weeks is still too long, ship in two phases:

**v1.0 — read-only (~1 week)**
- Index page (list sagas filtered by workflow=research-campaign)
- Detail page in Clean mode only
- Sources tab in the side drawer
- Skip: wizard, annotated mode, operator drawer, critiques/learnings parsing
- A user can browse research that completed via CLI

**v1.1 — full ship (~2 more weeks)**
- Wizard
- Operator drawer (deep-links to Völundr)
- Annotated mode + citation popover
- Critiques + Learnings tabs

The shape of the API + the metadata schema stay identical between the two — those are the things expensive to change later.

---

## Appendix C — Concrete reuse checklist

If you find yourself writing any of the following, **stop and reuse the existing thing instead**:

| Tempted to write | Use this instead |
|---|---|
| New `Campaign` entity + table | `Saga` with `metadata.research = {…}` |
| New CRUD endpoints | Wrap `ting.api.sagas` + `ting.api.phases` + `ting.api.runs` |
| New state machine | Start from `SagaStatus` + `PhaseStatus` + `RunStatus`, then add a research-specific projection layer only where the prototype needs richer campaign states |
| New dispatcher | `ting.api.dispatcher` + `dispatch_service` |
| New event subscription | `ting.api.events` SSE + `sleipnir_event_bridge` |
| New Volundr session spawn | `ting.adapters.volundr_http` |
| New cron / scheduler | Sleipnir delayed messages |
| New stage-progress visualization | `plugin-ting/src/ui/StageProgressRail.tsx` |
| New step-dots in wizard | `plugin-ting/src/ui/StepDots.tsx` |
| New confidence bar | `plugin-ting/src/ui/ConfidenceDriftCard.tsx` |
| New saga hook | `plugin-ting/src/ui/useSaga.ts` |
| New design tokens | `@niuulabs/design-tokens` |
| Authentication / Cerbos policy | Existing `saga` policy |
