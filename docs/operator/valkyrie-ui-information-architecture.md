# Valkyrie UI Information Architecture

This document defines an Environment-first, Flock-aware operator console for
resident Valkyries. The console is not a chat dashboard. It is a control surface
for long-running autonomous agents that live inside Environments, watch signals,
maintain operational state, learn locally, and share reliable learnings through
existing Flocks.

## Reuse Boundaries

Do not create a new plugin runtime, topology runtime, chat substrate, event bus,
or Flock model.

Reuse these existing surfaces:

| Concern | Existing surface |
| --- | --- |
| Plugin registration | `web-next/apps/niuu/src/plugins.ts` |
| Service injection | `web-next/apps/niuu/src/services.ts` |
| Warden-style page patterns | `web-next/packages/plugin-mimir/src/ui/RavnsPage.tsx` |
| Warden data hooks | `web-next/packages/plugin-mimir/src/application/useRavns.ts` |
| Mimir Warden IA | `docs/operator/mimir-warden-ui-plan.md` |
| Observatory topology/events | `src/observatory/app.py`, `src/observatory/contracts.py` |
| Collaboration rooms and human join | `src/niuu/collaboration/`, `src/skuld/collaboration_adapter.py`, `web-next` `useSkuldChat` usage in Mimir UI |
| Environment event taxonomy | `docs/operator/environment-event-taxonomy.md`, `src/sleipnir/domain/registry.py` |
| Environment NATS mesh | `docs/operator/environment-nats-mesh.md` |
| Existing Flock config | `docs/operator/flock-composition.md`, `src/ting/flock_flows.yaml`, `src/ting/api/flock_config.py` |
| Learning promotion | `src/ravn/adapters/reflection/learning_promotion.py` |
| Local learning injection | `src/ravn/adapters/reflection/post_session.py` |

The UI should be implemented as a web-next plugin or an extension of the Ravn
plugin, registered the same way Mimir, Ravn, Ting, Volundr, Observatory, and
Guild are registered today.

## Primary Navigation Model

The operator must be able to start from either an Environment or an existing
Flock.

Proposed top-level plugin:

```text
Valkyries
  Environments
  Flocks
  Learning
  Huddles
  Autonomy
```

Proposed routes:

```text
/valkyries
/valkyries/environments
/valkyries/environments/:environmentId
/valkyries/environments/:environmentId/signals
/valkyries/environments/:environmentId/state
/valkyries/environments/:environmentId/huddles
/valkyries/environments/:environmentId/learning
/valkyries/flocks
/valkyries/flocks/:flockId
/valkyries/flocks/:flockId/learning
/valkyries/learning
/valkyries/autonomy
```

The first screen should be an operations index, not a landing page:

- left column: Environment list grouped by configured labels or ownership
  metadata; the UI must not assume a closed set of environment types
- center: selected Environment health/state/signals
- right: Flock learning and active huddles relevant to the selected context

## Core Objects

### Environment

An Environment is the deployment boundary where residents live and where Ravn
interprets signals. Environment metadata is config-driven rather than a closed
runtime enum.

Examples:

- `cluster-prod-a` (configured labels may describe it as Kubernetes)
- `host-jozef-mail` (configured labels may describe its mailbox scope)
- `printer-cell-basement` (configured labels may describe its printer cell)

Primary data sources:

- `environment.*` Sleipnir events
- `signal.*` Sleipnir events
- `valkyrie.state.*`, `valkyrie.judgment.*`, `valkyrie.action.*`
- Skuld room roster and participants
- Observatory topology snapshots

### Valkyrie

A Valkyrie is a long-running resident Ravn persona in an Environment. The UI
should show it as an operator-facing agent with state and authority, not as a
chat identity.

Primary fields:

- `valkyrie_id`
- `persona`
- `environment_id`
- `wakefulness`: sleeping, watching, wakeful, dreaming
- `operational_state`
- `autonomy_mode`: guarded, autonomous, yolo
- `authority_boundary`
- current tools/skills
- last signal handled
- current huddle memberships

### Flock

A Flock is an existing Niuu/Ravn/Ting cohort, not a new Valkyrie-specific peer
system. Flock identities should use existing naming such as:

- `flock:k8s`
- `flock:k8s.production`
- `flock:printer.pi`
- `flock:inbox.host`

Primary data sources:

- Ting Flock config and flow settings
- Ravn mesh/NATS peer state
- `learning.promoted`
- `learning.adoption.recorded`
- learning promotion store snapshots
- Composite Mimir promoted learning pages

## Environment Views

### Environment Overview

Purpose: answer "what lives here, what is happening, and does a human need to
join?"

Layout:

- header: Environment name, type, lifecycle state, Flock membership
- topology band: Observatory nodes/edges filtered to the Environment
- Valkyrie roster: resident agents, wakefulness, authority, current task
- signal strip: recent normalized signals by source and severity
- current decisions: judgments, ODIN court decisions, attention outcomes
- action ledger: requested, executed, failed, suppressed
- huddle panel: active huddles and join controls
- learning panel: local learnings and promoted Flock candidates

Sources and fallbacks:

| Panel | Source | Fallback |
| --- | --- | --- |
| topology | Observatory `/api/v1/observatory/topology/stream` | last snapshot or empty topology |
| signals | Sleipnir/NATS `signal.*` replay/SSE adapter | filtered recent log |
| Valkyrie roster | `valkyrie.state.changed`, room participants | stale roster with timestamp |
| huddles | Skuld room APIs and `room.*` events | read-only transcript list |
| actions | `valkyrie.action.*`, `odin.court.decided` | audit-only timeline |
| learning | Mimir `learnings/*`, promotion store | no promoted learnings |

### Signal Stream

Purpose: triage what the Valkyrie sees without forcing the operator into chat.

Rows:

- timestamp
- source adapter (`k8s`, `inbox`, `printer`, `host`)
- canonical event type
- severity/attention tier
- related state change
- Valkyrie judgment
- action taken or suppressed
- learning candidate flag

Interactions:

- filter by source, severity, handled/unhandled, signal type
- open correlated huddle
- inspect evidence and raw payload
- mark feedback: useful, dismissed, wrong tier, bad action, good action

### Operational State

Purpose: show whether the Valkyrie is maintaining the intended state of the
Environment.

Sections:

- current observed state
- desired state or policy boundary
- drift, anomalies, suppressed noise
- recent actions and effect
- wakefulness timeline
- autonomy boundary changes

Do not hide "silent" decisions. A suppressed event is useful operational data.

### Huddles

Purpose: let a human join and leave the Environment mesh.

Reuse:

- Skuld room bridge participant roster
- `room.opened`, `room.message.recorded`, `room.context.snapshot.recorded`,
  `room.transcript.recorded`, `room.closed`
- Skuld endpoints: `/api/room/join`, `/api/room/heartbeat`,
  `/api/room/leave`, `/api/room/message`, `/api/room/direct`,
  `/api/room/participants`

UI behavior:

- join Environment with a per-request participant id, action intent, and
  target Flock id; never rely on a global operator role
- send message to huddle
- direct message to a specific Valkyrie
- view context snapshots
- replay transcript
- leave Environment

Join contract:

- `participantId` identifies the human or service user for this huddle action.
- `action` maps to Skuld authority roles (`observe`, `teach`, `approve`,
  `debug`, `own`) and is validated by the server before Skuld join.
- `targetFlockId` must match the huddle's Flock when the huddle is
  Flock-scoped, so k8s/printer/inbox authority cannot bleed across Flocks.
- Environment action authorities are passed to Skuld on join; approval powers
  are stripped when the Environment has no reviewable actions.
- `valkyrie.room.url` is the canonical Skuld room service setting; it does not
  select the user, role, action, or Flock authority.
  `RAVN_VALKYRIE_SKULD_ROOM_URL` remains a validated legacy environment alias.

The huddle is a side panel or tab, not the primary product metaphor.

## Flock Views

### Flock Overview

Purpose: inspect all Valkyries in an existing cohort and compare what they are
learning.

Example: `flock:k8s`

Layout:

- cohort header: Flock ID, domain, Environment count, active Valkyries
- membership table: Environment, Valkyrie, state, autonomy mode, last signal
- learning status matrix:
  - candidates
  - canarying
  - adopted
  - rejected
  - overridden
  - rolled back or demoted
- negative-transfer strip: where a Flock learning failed
- shared tool evolution: promoted skills/tools from `skill_manage`

Sources:

- Ting Flock config for named cohort membership and defaults
- Ravn mesh/NATS peer roster
- learning promotion store
- `learning.promoted`, `learning.adoption.recorded`
- Composite Mimir paths:
  - `learnings/flock/<flock-id>/...`
  - `learnings/domain/<domain>/...`
  - `learnings/shared/...`

### Flock Learning Detail

Purpose: compare one promoted learning against a local Environment baseline.

Required sections:

- summary and promoted scope
- source Environment and source Valkyrie
- source episode IDs
- confidence, repetition count, successful reuse count
- redaction status
- canary status by Environment
- adoption/rejection/override table
- negative-transfer notes
- local baseline comparison
- promoted tool/skill references
- rollback/demote state

Actions:

- canary in selected Environment
- adopt locally
- reject for this Environment
- override locally
- demote/archive promotion
- open source huddle/transcript/evidence

Autonomy rules:

- guarded: record candidate, require review
- autonomous: allow eligible Environment promotion only
- yolo: allow eligible Environment/domain/Flock promotion
- shared: require curation/review even in YOLO

## Learning Views

The Learning top-level page aggregates across Environments and Flocks.

Tabs:

- Local: private/session/dream learnings not promoted
- Environment: baselines per Environment
- Flock: candidates and adopted learnings by cohort
- Domain: cross-Flock domain learnings
- Shared: curated ODIN/Mimir knowledge
- Negative Transfer: rejected, overridden, regressed, demoted

Each row must show:

- learning ID/title
- scope
- source Environment
- source Valkyrie
- confidence
- redaction status
- promotion mode
- adoption state
- last feedback
- linked Mimir page

## Autonomy Controls

Autonomy controls are scoped. The UI must not offer a single global YOLO switch
without showing boundaries.

Control hierarchy:

- Valkyrie private scope
- Environment scope
- domain scope
- Flock scope
- shared/global doctrine scope

Controls:

- mode: guarded, autonomous, yolo
- allowed scopes
- delegated capabilities
- gated boundaries:
  - authority expansion
  - credentials
  - spending
  - destructive actions
  - external sends
  - global doctrine
- proposal store/audit link
- rollback and demotion affordances

Sources:

- `skill_manage` proposal store
- `LearningPromotionStore`
- Ravn autonomy policy (`src/ravn/context/autonomy.py`)
- learning promotion policy
- Sleipnir audit/event streams

## States

Every screen needs these states:

| State | Behavior |
| --- | --- |
| empty | Show the relevant creation/connect action, not marketing copy |
| loading | Preserve layout dimensions and use skeleton rows |
| stale | Keep last data visible with timestamp and reconnect affordance |
| error | Show source system, failed endpoint/stream, retry action |
| unauthorized | Show missing capability and route to access/settings |
| degraded | Show partial data by source, not a full-page failure |

Fallback behavior must be visible per panel. Example: topology can be stale
while huddles and signal stream remain live.

## Mock Scenarios

### Kubernetes Flock

Flock: `flock:k8s`

Environments:

- `cluster-prod-a`: high confidence OOM restart baseline
- `cluster-prod-b`: canarying that learning
- `cluster-staging-a`: adopted with local override

Expected UI proof:

- Flock overview shows three k8s Valkyries as one cohort
- learning matrix shows one promoted candidate, one canary, one override
- detail compares `cluster-prod-b` local baseline against Flock recommendation
- negative-transfer panel stays empty until rejection/regression

### Inbox Host

Environment: `host-jozef-mail`

Expected UI proof:

- signal stream shows new message signals and importance judgments
- actions show draft creation but not external send unless delegated
- huddle lets the operator join and ask why an email was flagged
- learning shows private preference learning until repeated accepted drafts

### Printer/Pi

Environment: `printer-cell-basement`

Expected UI proof:

- signal stream shows print done, resin low, printer idle
- operational state distinguishes idle, printing, needs attention
- learning shows local resin/print patterns
- Flock view can compare with `flock:printer.pi` peers

## Follow-Up UI Test Map

NIU-1029 should derive tests directly from this IA:

- route renders Environment index with empty/loading/error states
- selecting Environment filters topology, signals, roster, and learning
- joining a huddle calls Skuld room join and renders participants
- Flock page renders cohort membership from existing Flock config
- Flock learning table renders candidate/canary/adopted/rejected/overridden/demoted states
- learning detail shows provenance and negative transfer
- autonomy panel gates shared/global changes even when mode is YOLO
- promoted shared/Flock learnings appear in local injection preview within token budget

## Implementation Notes For NIU-1029

- Start with mock adapters in `web-next/apps/niuu/src/services.ts`, matching the
  current Mimir/Observatory/Ting service pattern.
- Add live adapters only where backend endpoints already exist.
- Prefer Observatory streams for topology and Sleipnir-derived event streams for
  signal/judgment/action timelines.
- Use Skuld room APIs for huddles instead of introducing a Valkyrie chat API.
- Reuse Mimir page links for learning detail bodies instead of duplicating a
  knowledge viewer.
- Reuse Ting Flock config for cohort identity and labels.
