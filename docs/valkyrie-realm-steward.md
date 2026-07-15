# Valkyrie Realm Steward — Architecture & Implementation Plan

> Status: design proposal (v0.1)
> Author: drafted with Claude, 2026-06-19
> Scope: turn the Valkyrie from a task-runner into a **resident operator-founder for a
> domain** — what we call a **Realm Steward** — built by composing primitives that
> already exist in Volundr, plus a small number of new ones.

---

## 1. The reframe

We are not building "an agent that completes tasks." We are building a **resident
steward of a Realm** that, given a tiny human seed, grows its own understanding of the
domain and continuously asks:

```
Given what I care about,
given what I know,
given what I cannot yet do,
given what changed recently,
what would make this Realm better?
```

Two non-negotiable product constraints from the operator:

1. **The charter is a seed, not a cage.** The only human-authored artifact is a few
   sentences. Everything else (beliefs, questions, opportunities, capabilities,
   projects, experiments) is machine-authored and machine-maintained. The human answers
   the occasional high-leverage question and approves gated actions. No giant YAML
   soul-file to babysit.

2. **Tools include embodied capabilities.** A 3D printer is not `print(file.stl)`. It is
   a *managed relationship*: known state, known failure modes, autonomy limits, and a
   feedback loop the steward learns from. The steward must be able to design → slice →
   (gate) → print → inspect → improve → reprint.

**Naming.** A **Realm** is the specific stewarded thing (`Kanuck Valley Models`, `Home`).
A **Valkyrie** is the resident steward agent that keeps a Realm. (`domain` is *already
taken* as a coarse Sleipnir enum — `code|infrastructure|home|business|personal` — so we
deliberately use *Realm* for the specific instance. A Realm declares which Sleipnir
`domain` it belongs to.)

**The crux.** What makes this an *organism* and not a *tool* is a single missing primitive:
an intrinsic **drive** (§4.4). It is the one thing Volundr does not have today — verified, not
assumed — and the one the operator named first: *"not a better scheduler — a domain drive."*
Everything else in this doc is the **body** that drive acts through. The system today is
poked, scheduled, turn-based; the drive is what makes it self-directed and keeps the human
out of the loop.

---

## 2. Key finding: the **body** exists, the **drive** does not

The exploration (ravn, ting, volundr/skuld, mimir/sleipnir/bifrost, plus a buri/regions and
a continuous-drive verification pass) shows the *body* maps onto existing code with high
fidelity — **reuse, don't rebuild** the parts below. But the *will* — the continuous,
intrinsic drive — is genuinely absent. The table maps the **body**; §4.4 builds the
**drive**.

| Vision concept | Existing primitive | Where | Reuse posture |
|---|---|---|---|
| **Execution substrate** (runs an action once decided) | `DriveLoop` daemon — but today it is **trigger-poked**: triggers enqueue tasks, the executor drains them; idle-waits otherwise. It is *not* an intrinsic drive (verified). | `src/ravn/drive_loop.py` | **Reuse the daemon+executor as the body's runner — do NOT mistake it for the drive** |
| **World Model / beliefs / journal** | Mimir wiki: typed pages, `Compiled Truth` (rewritable, typed edges `[[slug]] — rel: x —`) + `Timeline` (append-only evidence), confidence frontmatter, `FactEvidence` proof-count + freshness trend, `POST /mimir/page/revise` (records belief change forever) | `src/mimir/`, `src/mimir/FORMAT.md`, `src/mimir/learning.py` | **Reuse — this is the substrate** |
| **World-model → agent injection** | Retrieval **reflex**: zero-LLM entity-pointer injection into live turns | `src/ravn/reflex.py`, `src/skuld/config.py` (`ReflexConfig`) | **Reuse** |
| **Capability hunger / learned tools** | `valkyrie_evolution/` — wakefulness (WAKEFUL/WATCHING/DREAMING/SLEEPING), `resident_learning.py` (capability-gap → micro-dream → author a learned tool), `OperationalSignal` | `src/ravn/valkyrie_evolution/` (currently **dormant**) | **Wire it up** |
| **Pattern → skill / belief updates** | `PatternExtractor` over episodic memory | `src/ravn/context/evolution.py` (**dormant**) | **Wire it up** |
| **Emergent execution loop** (reason→act→observe→adapt) | Ravn **agent turn loop** — `RavnAgent.run_turn` (ReAct-style: each step emerges from the last), wrapped by `DriveLoop` for autonomy | `src/ravn/agent.py`, `src/ravn/drive_loop.py` | **Reuse — this IS the steward** |
| **Inline approval / trust gate** | Ravn `PermissionEnforcer` (`allow` / `deny` / **`ask`**) + `ApprovalMemory` + `ask_user`, checked at tool-call time per action | `src/ravn/adapters/permission/`, `src/ravn/config.py` | **Reuse — gate the action, not a graph** |
| **Optional pipeline backend** (repeatable code campaigns) | Ting **workflows** (`gate`/`end` nodes), launched via REST only when the steward delegates a discrete, pre-shaped job | `src/ting/api/workflows.py` | **Reuse — only where work is genuinely a pipeline** |
| **Cost / budget gate** | Bifrost `BudgetGuardrailConfig` (warn 80% → route cheaper, reject 100%, degradation chain), `QuotaConfig`, `UsageStore` | `src/bifrost/config.py` | **Reuse as the `spend` gate** |
| **Attention tiers** | `OutputMode` = `SILENT/AMBIENT/PRESENT/URGENT/SURFACE`; Sleipnir `urgency` float + `domain` enum | `src/ravn/domain/models.py`, `src/sleipnir/domain/events.py` | **Reuse** |
| **Event-driven wake-ups (domain-aware)** | Sleipnir `SleipnirEvent` (type `namespace.domain.action`, urgency, domain, correlation/causation); namespaces already include `valkyrie`, `signal`, `attention`, `environment`, `learning` | `src/sleipnir/domain/events.py`, `catalog.py` | **Reuse + add event types** |
| **Delegation / sub-agents / mesh** | `RavnIdentity`/`RavnPeer`, `MeshPort` (publish/subscribe/send-RPC), discovery (mdns/sleipnir/k8s), cascade tools (`task_create`/`task_status`/`task_collect`) | `src/ravn/ports/mesh.py`, `discovery.py`, `adapters/tools/cascade_tools.py` | **Reuse for execution fan-out** |
| **Triggers / cron / recurring** | `TriggerPort` + cron / ting-queue / mimir-staleness / sleipnir-event / dream-cycle / wakefulness adapters | `src/ravn/ports/trigger.py`, `src/ravn/adapters/triggers/` | **Reuse + add `StewardDriveTrigger`** |
| **External tools (MCP)** | `MCPManager`/`MCPTool` (stdio/sse/http), per-session MCP config in launch spec | `src/ravn/adapters/mcp/manager.py`, `src/skuld/` | **Reuse as transport for managed tools** |

**Conclusion:** the **body** is present — memory (Mimir), the agent turn loop, tools, inline
permission gates, the budget gate (Bifrost), the attention-tier vocabulary (OutputMode +
Sleipnir), the daemon shell + executor (DriveLoop), even the dormant capability-hunger
machinery (valkyrie_evolution). But the **will is not.** There is no intrinsic drive: today
every action is externally poked (cron / event / human / mesh), and the system *executes
autonomously when told to, but never decides on its own whether to* (verified by code
exploration). The missing primitive is exactly the one the operator named first — **not a
better scheduler, a domain drive.** That is the heart of this plan (§4.4), and it is net-new.

> **The steward is an agent loop, not a workflow.** Its behavior is *emergent* — the
> operator's own primitive: `Charter → Curiosity → Belief Formation → Verification →
> Action`, explicitly **not** `Charter → Fixed Config → Agent Behavior`. A pre-authored DAG
> (a Ting workflow graph) *is* "fixed config," so the steward's cognition and its
> craft/experiment loops are **not** modeled as workflows. The substrate is the **Ravn
> agent turn loop** (`agent.py:run_turn`): reason → act (tool) → observe → update beliefs →
> decide the next step from what just happened — wrapped by the DriveLoop for autonomy.
> *Each step emerges from the previous one; the path is not planned in advance.* Ting
> workflows remain useful only as an **optional backend** for discrete, genuinely-repeatable
> code campaigns the steward chooses to delegate (and there, workflow `gate`/`end` nodes —
> not the legacy ReviewEngine — are the gate). Gating for the steward itself is **inline,
> per-action** (§4.7).

---

## 3. The genuine gaps (what is actually new)

**The biggest gap, and the heart of the whole thing: there is no intrinsic *drive* (§4.4).**
Today the system is a poked executor — it acts only when a trigger, event, or human tells it
to (verified). Building the continuous appraisal-and-motivation loop *is* the project;
everything below is in service of it.

1. **Realm + Charter as first-class primitives.** No structured "this is a stewarded
   domain with a sacred seed + a learned model." Today the closest is a persona
   `system_prompt`.
2. **Domain Discovery Loop.** No "seed → infer likely map → inspect → research →
   hypotheses → verify cheaply → ask only high-leverage questions → update model"
   bootstrap. The DriveLoop is *reactive* (cron/event/mesh), not *generative*.
3. **Opportunity Ledger + scoring.** Nothing generates opportunities and scores them on
   `desirability / feasibility / cost / risk / evidence / permission`. Ting only executes
   externally-given tickets.
4. **Project Portfolio.** Self-authored projects (proposed/active/dormant/done) born from
   opportunities. Ting Sagas are human-created and code-only.
5. **Self Model + unified Trust/Autonomy Policy.** Capabilities are reconstructed at
   startup (ephemeral); gates are scattered across three services. No single ladder
   (`observe → draft → build → test → deploy → mutate → spend → purchase`) per
   action-class/tool that *ratchets up with demonstrated trust*.
6. **Managed Tools (embodied) + experiment loop.** No abstraction for a stateful machine
   with constraints, health, per-action autonomy, feedback channels, a skill model, a
   safety contract, and an Experiment/Inspection/Improvement loop.

Everything new is **additive** and composes the existing primitives. No rewrites — same
philosophy as the autonomy-ladder plan (each level additive).

---

## 4. Architecture

```
                          ┌─────────────────────────────────────────────┐
                          │                  REALM                       │
                          │  (Kanuck Valley Models · domain=business)     │
                          │  charter seed (sacred) + autonomy profile     │
                          └───────────────┬─────────────────────────────┘
                                          │ kept by
                          ┌───────────────▼─────────────────────────────┐
                          │            VALKYRIE (resident steward)        │
                          │   runs as a ravn daemon w/ a steward persona  │
                          └───────────────┬─────────────────────────────┘
                                          │ engine
              ┌───────────────────────────▼───────────────────────────────┐
              │                      DRIVE LOOP  (ravn/drive_loop.py)        │
              │  StewardDriveTrigger ──► enqueue stewardship reflection tasks │
              │  cycles stewardship MODES: steward · entrepreneur · craft ·   │
              │           imagine · capability-hunger                         │
              └───┬───────────────┬───────────────┬───────────────┬──────────┘
                  │ reads/writes   │ proposes       │ executes       │ surfaces
        ┌─────────▼──────┐ ┌──────▼───────┐ ┌──────▼─────────┐ ┌────▼─────────────┐
        │  WORLD + SELF  │ │ OPPORTUNITY  │ │   PROJECT       │ │  ATTENTION /     │
        │  MODEL (Mimir) │ │ LEDGER (new) │ │ PORTFOLIO (new) │ │  MORNING BRIEF   │
        │ charter/beliefs│ │ scored ideas │ │ proposed/active │ │ OutputMode tiers │
        │ questions/     │ │ d/f/c/r/e/p  │ │ /dormant/done   │ │ + Sleipnir       │
        │ journal/edges  │ └──────┬───────┘ └──────┬─────────┘ │ urgency          │
        └────────────────┘        │                │            └──────────────────┘
                                  │ gated by        │ delegates to
                         ┌────────▼─────────┐  ┌────▼─────────────────────────────────┐
                         │ TRUST / AUTONOMY │  │  EXECUTION (reuse)                    │
                         │ POLICY (new)     │  │  • code  → Ting Saga/Run/ReviewEngine │
                         │ ladder per       │  │  • general → Volundr session / flock  │
                         │ action-class:    │  │  • physical → MANAGED TOOL (new)      │
                         │ observe/draft/   │  └────┬──────────────────────────────────┘
                         │ build/test/      │       │ cybernetic loop
                         │ deploy/mutate/   │  ┌────▼──────────────────────────────────┐
                         │ spend/purchase   │  │ MANAGED TOOL (printer): plan→dry-check │
                         │  spend→Bifrost   │  │ →estimate→[gate]→act→observe→inspect→  │
                         │  deploy→Ting conf │  │ EXPERIMENT LEDGER→update beliefs→retry │
                         └──────────────────┘  └───────────────────────────────────────┘
```

### 4.1 The Realm aggregate (new)

A long-lived stewardship context. Minimal structured row; the *soul* lives in Mimir.

```
realms(
  id uuid, slug text, name text,
  sleipnir_domain text,         -- code|infrastructure|home|business|personal
  charter_page_path text,       -- e.g. realms/kanuck-valley-models/charter.md
  mesh_realm_id text,           -- unify with RavnIdentity.realm_id (shared trust boundary)
  autonomy_profile text,        -- conservative|balanced|trusted (defaults for the ladder)
  steward_persona text,         -- which ravn persona runs the loop
  owner_id text, instance_id text, created_at timestamptz
)
```

A Realm wires together: a **charter** (Mimir), a **Mimir namespace** (`realms/<slug>/…`),
a **Sleipnir domain** for event filtering, a **mesh realm_id** (so co-stewards share a
trust boundary), an **autonomy profile**, and a **steward persona**.

### 4.2 The Charter — seed, not cage (Mimir page, new `type: charter`)

One Mimir page with two zones, reusing the existing Compiled-Truth/Timeline format:

- **Explicit zone (sacred / human-only / locked).** The 3-line seed. The steward may
  *never* edit it. Enforced by a Mimir lint rule (new `L13: charter explicit zone
  immutable`, hash-checked like the existing `L09` timeline-tamper guard).
- **Compiled Truth (machine-authored).** The inferred domain model: cared-about
  outcomes, current capabilities, current gaps, working hypotheses — all with
  `confidence` and typed edges to evidence pages.
- **Timeline (machine-authored, append-only).** Why the model changed — *the journal of
  belief changes*, which Mimir already gives us for free via `POST /mimir/page/revise`.

```markdown
---
type: charter
realm: kanuck-valley-models
domain: business
confidence: high
---
## Explicit  <!-- SACRED · operator-authored · steward MUST NOT edit -->
You are the keeper of Kanuck Valley Models, my small 3D printing company.
Help me make it easier to run, more creative, and more successful.
Ask before spending money or starting physical machines.

## Compiled Truth  <!-- machine-authored, evidence-backed -->
### Cared-about outcomes
- reliable inventory  [[obs-operator-asked-inventory]] — rel: evidenced_by
- printability & quality  [[obs-operator-mentioned-printers]] — rel: evidenced_by
### Current capabilities
- code, research, image-gen, deployment, docs  [[cap-*]] — rel: has_capability
### Current gaps (→ opportunities)
- 3D modeling workflow  [[gap-3d-modeling]] — rel: lacks_capability
- printer telemetry  [[gap-printer-telemetry]] — rel: lacks_capability

## Timeline
- 2026-06-19: seeded by operator. [Source: @jozef, chat, 2026-06-19]
```

### 4.3 World Model & Self Model (Mimir + a thin Self registry)

- **World Model** = Mimir pages under `realms/<slug>/` using existing + new `type`s:
  `entity`, `observation`, `goal`, `decision`, plus new `opportunity`, `capability`,
  `open_question`, `experiment`, `managed_tool`. We get hybrid retrieval (P@5 ≈ 0.98),
  typed-edge traversal (`GET /mimir/related`), confidence, evidence/freshness, and the
  reflex injection **for free**.
- **Self Model** = the **Capability Ledger** (§4.7): a persisted, queryable projection of
  "what I can do / cannot do / may do / must ask," replacing today's ephemeral
  startup-time reconstruction. Each capability links to its Mimir narrative page and to a
  trust-grant row.

### 4.4 The Drive — the missing primitive (NEW · the heart)

This is the one thing that does not exist today and the reason the system would otherwise
stay a poked tool. The drive is a **continuous control loop** — always running, never
waiting to be invoked — that turns the gap between *what the realm should be* and *what it
is* into self-initiated action. It is the operator's `Curiosity → Belief → Verification →
Action`, **not** `Fixed Config → Behavior`, and emphatically **not a scheduler**.

The loop runs forever, modulated by arousal/wakefulness — never gated by a clock:

1. **Perceive (streaming).** Subscribe to the world — Sleipnir events (push, not polled),
   Mimir belief changes, managed-tool state — and keep a live picture of the realm's current
   state. The world pushes; the drive is always listening.
2. **Appraise → tension.** For each cared-about outcome (charter + learned model),
   continuously estimate satisfaction and compute the **discrepancy** (desired − perceived).
   That discrepancy is a standing *tension* signal — a homeostatic error, like a thermostat's
   — present whether or not anything just happened. Detect opportunities (a change that could
   advance a concern) and threats (one that endangers it).
3. **Attend → salience.** Aggregate tension × opportunity × novelty into a salience
   landscape: *what matters most right now*. This is the live blackboard (wire the dormant
   `SharedContext`), and it is also what decides whether anything ever reaches the human.
4. **Act when the pull is strong enough.** When a concern's salience crosses an
   action-readiness threshold *and* resources/budget/trust permit, the drive **forms an
   intention on its own** and pursues it through the body (§4.6) — the agent loop, tools,
   managed tools, gated inline (§4.7). No external poke; the pull is internal.
5. **Rest / dream / imagine when quiet.** Low external salience does **not** mean
   idle-waiting. The drive turns inward: consolidate beliefs, and run **imagination** —
   counterfactual generation against the charter ("what could exist here that doesn't yet?")
   — to manufacture its own opportunities. It explores when the world is quiet, the way a
   person does. Self-chosen from internal state, not a `dream_cycle` cron.

**The inversion from today.** The existing `DriveLoop` is a long-running daemon whose
*action* is sourced by triggers enqueuing tasks (verified). We keep the daemon shell and its
executor as the **body's runner**, but invert the source of action: **triggers/events become
perception inputs to appraisal, not commands to execute.** A Sleipnir event no longer
enqueues "do task X"; it updates the world-model, which changes the tension landscape, which
*the drive* may or may not act on. The decision to act is intrinsic.

**Stewardship modes** (steward / entrepreneur / craft / imagine / capability-hunger) are not
pipeline stages and not personas — they are **biases on the appraise + imagine steps** (which
concerns to weight, what kinds of opportunity to generate), shifted by a config weight
schedule and by arousal state. The dormant `valkyrie_evolution` (capability-hunger → learned
tools) and `PatternExtractor` become *inputs to appraisal*, not cron jobs.

**Layering — continuous will, discrete body.** The *will* (this loop) is continuous and
self-driven. The *body* it acts through (an LLM agent turn, a tool call) is necessarily
discrete — you cannot make an LLM "continuous," and that's fine: the drive is the organism;
an agent turn is how it moves a muscle. The continuity lives in the drive, not the model call.

Output of a drive cycle is **not** code — it's an updated tension/salience landscape,
**scored opportunities written to the ledger + Mimir**, and (when salience + trust cross the
bar) self-initiated projects.

### 4.5 The Opportunity Ledger (new)

Structured + queryable (Postgres) for ranking; narrative + evidence in Mimir.

```
opportunities(
  id uuid, realm_id uuid, title text, mode text,     -- steward|entrepreneur|craft|imagine|capability
  desirability real, feasibility real, cost_estimate real, risk real,
  evidence_refs text[],          -- mimir page paths / source ids
  permission_level text,         -- act|propose (from Trust Policy)
  score real,                    -- derived; weights in config (no magic numbers)
  status text,                   -- proposed|accepted|dismissed|promoted|stale
  mimir_page_path text, created_at timestamptz, updated_at timestamptz
)
```

Every imagined idea passes the **grounding filter** before it survives:
`desirability` (helps the realm?), `feasibility` (do I have the tools? → checked against
the Self Model), `cost` (→ Bifrost estimate), `risk` (can it break things?), `evidence`
(why do I believe this — links to beliefs), `permission` (→ Trust Policy: act vs
propose). Low-scoring ideas are dismissed, not spammed. This is "imagination, but
grounded."

### 4.6 The Project Portfolio (new) + execution delegation (reuse)

A **Project** is a promoted opportunity with a lifecycle. The steward *creates its own
projects* — the core "projects born from care, not given as tasks" capability.

```
projects(
  id uuid, realm_id uuid, opportunity_id uuid, title text, intent text,
  status text,                   -- proposed|active|dormant|done|abandoned
  executor_kind text,            -- ting_saga|volundr_session|ravn_flock|managed_tool|manual
  executor_ref text,             -- saga id / session id / experiment thread
  mimir_page_path text, created_at timestamptz, updated_at timestamptz
)
```

A Project is **pursued by the agent loop**, not run through a pre-authored pipeline. The
steward works a project the way the operator described the inventory chain — *"I built v1…
then I noticed X… so I researched Y… so I built Z"* — each step emerging from the last. An
`ExecutorPort` only chooses **where that loop runs** and what tools it has:

- `SessionExecutor` (default) — the steward runs the work in a Ravn/Volundr **agent
  session** with the right tools (code, web, image-gen, Mimir, managed tools) and inline
  permission gates. This covers code, research, docs, and craft alike — the loop adapts as
  it goes.
- `TingWorkflowExecutor` (optional) — *only* when a slice of work is genuinely a
  repeatable, pre-shaped pipeline (e.g. "spec → coder → reviewer → merge") does the steward
  delegate it to a Ting **workflow** via REST/PAT and await the outcome event. A backend
  choice for one bounded job, **not** the shape of the project.
- `ManagedToolExecutor` — physical/embodied work (§4.8): the agent loop closing on real
  hardware feedback. No DAG — the agent calls slice/print/observe/inspect tools and adapts.

### 4.7 The Capability Ledger + unified Trust/Autonomy Policy (new)

**Capability Ledger** — persisted Self Model:

```
capabilities(
  id uuid, realm_id uuid, name text,
  kind text,                     -- tool|skill|managed_tool|persona|integration
  status text,                   -- present|gap|building
  trust_level int,               -- current ladder rung for this capability
  mimir_page_path text, notes text
)
```

A `status='gap'` capability auto-spawns a `capability-hunger` opportunity ("I keep
wanting to inspect STL files → build an STL analysis tool"). This is the dormant
`resident_learning` flow, now connected to the ledger.

**Trust / Autonomy Policy** — one ladder, consulted before any gated action, generalizing
the autonomy-ladder note (L0 observe → L5 self-modify):

```
trust_grants(
  id uuid, realm_id uuid,
  action_class text,             -- observe|draft|build|test|deploy|mutate|spend|purchase
  target text,                   -- '*' | tool name | managed_tool id | sleipnir domain
  level int,                     -- 0..5
  limits jsonb,                  -- e.g. {max_print_time_hours:3, max_material_grams:80, allowed_materials:[PLA]}
  granted_by text, granted_at timestamptz
)
```

The gate is enforced **inline, at the moment the agent tries an action** — reusing Ravn's
`PermissionEnforcer` (`allow` / `deny` / **`ask`**) + `ApprovalMemory` + the `ask_user`
tool. There is no pre-placed gate node; the action itself is intercepted. The policy maps
each action-class to a permission verdict that **graduates with earned trust**:
- `observe` / `draft` → `allow` (always autonomous).
- `build` / `mutate` (filesystem, code) → `allow` within the workspace via permission mode.
- `deploy` (merge / publish) → `ask` until trusted, then `allow`.
- `spend` / `purchase` → `ask` **and** a hard **Bifrost** `BudgetGuardrail`/`Quota` cap
  checked at call time (the cap holds even when the verdict is `allow`).
- physical actions (`start_print`) → `ask` until trusted; **auto-cancel** stays autonomous
  on failure detectors (§4.8).

Autonomy **graduates** per capability: a run of clean outcomes (and, for managed tools,
successful experiment verdicts) flips an action from `ask` to `allow`; a failure or human
rejection flips it back. The `autonomy_profile` on the Realm sets the starting verdicts.
*The legacy Ting `ReviewEngine` confidence machinery is intentionally **not** in this path.*

### 4.8 Managed Tools — the 3D printer (new, the biggest piece)

A `ManagedTool` is a *relationship*, not a function call. New port + dynamic adapters
(per `.claude/rules/dynamic-adapters.md`: config carries `adapter:` + kwargs).

```
managed_tools(
  id uuid, realm_id uuid, name text,
  kind text,                     -- physical_machine|service|sensor
  adapter text,                  -- e.g. volundr.adapters.printers.BambuAdapter
  config jsonb,                  -- kwargs (host, serial, api key ref)
  capabilities jsonb,            -- [print PLA, report telemetry, stream camera]
  constraints jsonb,             -- build_volume, nozzle, filament_loaded, temp_range
  state jsonb, health text, last_seen timestamptz,
  safety_contract jsonb          -- per-action autonomy + auto_cancel_on triggers
)
```

```python
# src/ravn/ports/managed_tool.py  (sketch)
class ManagedToolPort(ABC):
    async def describe(self) -> ManagedToolSpec: ...        # capabilities + constraints
    async def get_state(self) -> ManagedToolState: ...      # idle/printing/error, temps, filament
    async def prepare(self, job: Job) -> PreparedJob: ...   # slice/estimate — autonomous
    async def estimate(self, job: PreparedJob) -> Estimate: ... # time/material/cost/risk
    async def start(self, job: PreparedJob) -> RunHandle: ...   # GATED
    async def observe(self, run: RunHandle) -> Feedback: ...    # telemetry + camera frame
    async def cancel(self, run: RunHandle) -> None: ...         # autonomous on failure
```

**Cybernetic loop** (the magic = feedback):

```
design/variant → validate mesh → slice → estimate(cost/time/risk)
  → [Trust gate: start_print] → start → observe(camera+telemetry)
  → inspect(expected vs actual)  → record Experiment → update beliefs(Mimir)
  → improve(orientation/temp/retraction) → retry
```

Supporting new records:

```
experiments(
  id uuid, realm_id uuid, project_id uuid, managed_tool_id uuid,
  hypothesis text, params jsonb, expected jsonb, observed jsonb,
  verdict text,                  -- success|partial|fail
  mimir_page_path text, created_at timestamptz
)
```

- **Experiment Ledger** = `experiments` rows + a Mimir `type: experiment` page (narrative
  + photos + what changed). "I lowered temp 5°C, increased retraction; stringing gone" is
  a *belief revision* on the model's printable-variant page.
- **Inspection Loop** = `observe()` feedback compared to `estimate()` expectation;
  failure detectors (`spaghetti_detected`, `thermal_anomaly`, `layer_shift`,
  `bed_adhesion_failure`) come from the adapter and trip `cancel()` autonomously.
- **Improvement Loop** = adjust params/orientation/process or, if a capability is missing
  (e.g. "I have telemetry but no visual QA"), emit a `capability-hunger` opportunity to
  *build the missing tool* — same drive loop, grounded in a real affordance.

**It's the agent loop closing on real feedback — not a DAG.** The steward runs this as one
continuous agent loop: it calls the design/slice/estimate tools, the permission layer gates
`start_print` (`ask` until trusted), it `observe`s telemetry + a camera frame, `inspect`s
expected-vs-actual, writes the result to the Experiment Ledger + revises beliefs in Mimir,
then **decides** — adjust orientation/temp/retraction and call the tools again, or stop.
"Try again" is just the next turn of the loop with new parameters; there is no graph and no
re-launch. This is the operator's *"I can practice."* Failure detectors
(`spaghetti_detected`, `thermal_anomaly`, `layer_shift`, `bed_adhesion_failure`) trip
`cancel()` autonomously regardless of trust level.

**Transport reuse:** managed-tool adapters can wrap **MCP servers** (`MCPManager` already
supports stdio/sse/http) where a printer exposes one (e.g. an OctoPrint/Bambu MCP), so
"connect a printer" is often "register an MCP server + a thin adapter," not bespoke
plumbing.

### 4.9 Domain Discovery Loop (bootstrap, new)

On realm creation from the seed, the steward runs a one-time discovery pass (then the
ongoing drive loop maintains it):

```
seed → infer likely domain map (LLM, grounded in "common patterns for a {domain}")
     → inspect available systems (repos, MCP servers, managed tools, files)
     → research adjacent patterns (web search)
     → form hypotheses (write low-confidence beliefs to Mimir)
     → verify cheaply (read-only inspections)
     → write open_questions, surface only the top few at PRESENT tier
     → update the charter Compiled-Truth zone
```

Hypotheses are written at `confidence: low` and marked clearly as inferred, never as
commandments — exactly "rough frame, look around, revise."

### 4.10 Surfacing & the Morning Brief (reuse OutputMode + Sleipnir)

- Routine work runs at `SILENT`/`AMBIENT`. Proposals needing a human run at `PRESENT`.
  Safety/budget stops run at `URGENT`. (All already in `OutputMode` + Sleipnir
  `urgency`.)
- A scheduled `morning-brief` steward task summarizes the journal: *what I did, what I
  learned, what I want to try next, and the ≤3 questions I actually need answered*. This
  is the felt "I am becoming the keeper of this company" surface.

---

### 4.11 Addressable agents and capability discovery

The steward must discover callable agents through the principal-aware Observatory Agent
Directory, not from a static agent list. Directory entries project the existing topology and
link back through `topologyNodeId`; skills and tags provide capability search, while owner,
tenant, Environment membership, and visibility constrain what the steward may see. Cross-realm
or cross-cluster discovery uses Guild's partial-result aggregate and retains source provenance.
Only equivalent, signed Agent Cards may collapse into one canonical identity. Operational and
protocol details live in [A2A Agent Directory](operator/a2a-agent-directory.md).

## 5. Data, events, config, boundaries

- **Postgres** (raw SQL + asyncpg, **no ORM** — `.claude/rules/database.md`):
  `realms`, `opportunities`, `projects`, `capabilities`, `managed_tools`, `experiments`,
  `trust_grants`. Migrations in **both** `migrations/` and the Helm configmap
  (`.claude/rules/migrations.md`), idempotent, `NNNNNN_*.up/down.sql`.
- **Mimir** new page types: `charter`, `opportunity`, `capability`, `open_question`,
  `experiment`, `managed_tool`; new namespace `realms/<slug>/`; new lint rule `L13`
  (charter explicit-zone immutable). Belief revision + evidence/freshness already exist.
- **Sleipnir** new event types under existing namespaces (`namespace.domain.action`):
  `valkyrie.<domain>.realm_created`, `valkyrie.<domain>.opportunity_scored`,
  `valkyrie.<domain>.project_proposed`, `valkyrie.<domain>.experiment_completed`,
  `valkyrie.<domain>.belief_revised`, `valkyrie.<domain>.capability_gap_detected`. Add
  typed payloads + factories in `src/sleipnir/domain/catalog.py`.
- **Config** (no magic numbers — `.claude/rules/no-magic-numbers.md`): drive cadence,
  mode weights, max opportunities/cycle, score weights, autonomy-profile default rungs,
  managed-tool limits, morning-brief schedule — all in config with sensible defaults.
- **Module boundaries** (`.claude/rules/module-boundaries.md`): steward domain lives in
  **ravn** (it *is* a resident ravn daemon; extends `valkyrie_evolution/`). Cross-package
  needs go through **niuu** shared models + **Sleipnir** events. The steward reaches Ting
  via Ting's **REST API (PAT)**, never a Python import — and *only* when delegating a
  discrete, repeatable code campaign as a Ting **workflow**; the steward's own loop runs
  in-process (`RavnAgent`) and is **not** a workflow. Persistence behind a port
  (`StewardStorePort`) with Postgres (prod) + SQLite (local) adapters — mirrors the
  existing memory-adapter pattern.
- **Testing** (`.claude/rules/testing.md`): 85% gate, test against ports, mock asyncpg,
  zero pytest warnings. New ports get fake adapters for unit tests.

---

## 6. Phased plan (aligned to the autonomy ladder)

Each phase is independently shippable and additive — no rewrites. Maps to the existing
autonomy-ladder note (L0 observe … L5 self-modify).

### Phase 0 — Realm, Charter & a minimal **drive** · *L0: perceive / appraise / surface only*
- `realms` table + `Realm` model + `StewardStorePort`; `type: charter` Mimir page +
  `realms/<slug>/` namespace + `L13` immutability lint.
- The first slice of the **drive** (§4.4): a continuously-running loop that perceives realm
  state (Mimir + the Sleipnir stream), appraises it against the charter's cared-about
  outcomes, holds a live tension/salience landscape, and surfaces only the top concern.
  Discovery (infer the initial model; write low-confidence beliefs + ranked open questions)
  is the drive's **first act**, not a one-shot script.
- No execution, no autonomy — it can perceive, appraise, and surface, nothing else.
- **Deliverable:** operator gives a 3-line seed and the steward *comes alive* — it builds an
  inferred model, holds its own sense of what's most off in the realm, and brings you the
  highest-tension concern + a brief. The point is that it is **self-driven from second one**,
  not that it ran a task. *This is "you name the realm, and it wakes up in the territory."*

### Phase 1 — Drive Loop & Opportunity Ledger · *L0–L1: propose / draft*
- `StewardDriveTrigger` + stewardship modes; subscribe to `signal.*`/`environment.*`.
- `opportunities` table + scoring (d/f/c/r/e/p) + grounding filter.
- Wire dormant `PatternExtractor` and `resident_learning` capability-hunger.
- **Deliverable:** the steward continuously proposes **scored** opportunities and refines
  beliefs; daily brief shows "what changed, what I'd try, what I need from you." Still
  propose-only.

### Phase 2 — Project Portfolio & agent-loop execution · *L1–L2: build/test auto, deploy gated*
- `projects` table + `ExecutorPort`; the default `SessionExecutor` runs a project as an
  **emergent Ravn agent loop** with tools + Mimir + inline permission gates.
  `TingWorkflowExecutor` is available only for the rare pre-shaped pipeline.
- The deploy gate is **inline**: the agent's merge/publish action is intercepted by the
  permission layer (`ask`) — not a graph node, not a confidence threshold.
- **Deliverable:** "build inventory v1" goes idea → PR via the agent loop (each step
  emerging from the last); **merge** pauses to ask at the action. The first real "executed
  because it cared" loop.

### Phase 3 — Trust/Autonomy Policy engine · *inline gates, enable graduation*
- `trust_grants` + `capabilities` (Self Model). The policy maps each action-class to an
  **inline permission verdict** (`allow`/`ask`/`deny`) enforced by Ravn's
  `PermissionEnforcer` at tool-call time. `spend`/`purchase` also honor a hard **Bifrost**
  budget cap; `build`/`mutate` honor **Ravn** permission modes.
- Autonomy **graduates** per action: clean outcomes + experiment verdicts flip
  `ask`→`allow`; a failure or rejection flips it back. `autonomy_profile` presets the
  starting verdicts.
- **Deliverable:** one coherent trust ladder enforced inline at the action; the steward
  acts freely where it has *earned* trust, pauses to ask where it hasn't.

### Phase 4 — Managed Tools (embodied) · *L3–L4: prepare auto, start gated → unattended-in-policy*
- `ManagedToolPort` + `managed_tools` + first adapter (OctoPrint/Bambu, MCP-backed where
  possible) + `experiments` + Inspection/Improvement loops + auto-cancel detectors.
- **Deliverable:** design → slice → estimate → (approve first print) → print → inspect →
  iterate; later, overnight test prints within `safety_contract` limits.

### Phase 5 — Capability hunger & self-extension · *L5: modify the toolchain*
- Capability gaps (incl. managed-tool gaps like "no visual QA") auto-become tool-building
  projects; learned tools persist; the steward expands its own affordances.
- **Deliverable:** "I keep wanting visual QA → I built an inspection pipeline" end-to-end.

---

## 7. The smallest first slice (build this first to feel the magic)

Within Phase 0, the minimum that produces the *resident-keeper* feeling:

1. `realms` row + `charter` Mimir page (seed in the sacred zone).
2. A one-shot **Domain Discovery** run (a single steward `AgentTask`) that, given the
   seed, writes ~6–10 low-confidence belief pages + a ranked `open_questions` page under
   `realms/<slug>/`, using existing `mimir_write` and web search.
3. A **morning brief** rendered from those pages at `PRESENT` tier.

No new execution, no managed tools, no autonomy. Just: *name the realm → it inspects,
infers, and reports back what it now believes and what it needs to know.* That validates
the whole reframe before we invest in execution and embodiment.

---

## 8. Design principles / risks

- **Seed stays tiny; everything else self-authored.** The only files a human touches are
  the charter explicit zone and (rarely) the autonomy profile.
- **Imagination must be grounded.** The 6-axis scoring filter is the anti-spam mechanism;
  ideas that fail feasibility/evidence are dismissed, not surfaced.
- **Reuse the gates we trust.** Don't reimplement budget/review — delegate `spend` to
  Bifrost and `deploy` to Ting so safety is enforced by code already in production.
- **Belief humility.** Inferred beliefs are `confidence: low` and clearly inferred;
  revisions are journaled forever (Mimir), so the steward can be wrong and learn.
- **Physical safety is first-class.** `start_print` is gated until trust is earned;
  failure detectors cancel autonomously; `safety_contract` limits are hard caps.
- **Additive only.** Every phase composes existing primitives; nothing above requires
  rewriting ravn/ting/volundr/mimir.
```
