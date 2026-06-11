# Valkyrie Dream Layers

Three distinct mechanisms publish `*.dream.*` events. They are deliberately
separate layers, each with its own trigger, scope, and configuration. This doc
exists so nobody re-merges them or mistakes one for another.

| Layer | Trigger | Scope | Config | Events |
| --- | --- | --- | --- | --- |
| **Micro-dream** | Reactive: a signal arrives with no installed capability | Build one skill + executable probe, review, canary, install, propose to flock | `resident_evolution` (builder/reviewer adapters, autonomy, rollback) | `valkyrie.dream.started/completed`, `valkyrie.evolution.*`, `flock.learning.proposed` |
| **Consolidation dream** | Scheduled: idle + interval gates in the wakefulness state machine | Reflective pass over the whole skill registry: mark stale, promote proven private skills, hold skills implicated by feedback, reopen deferred capability gaps | `resident_wakefulness` (intervals, idle gates, promotion thresholds) | `valkyrie.state.changed`, `valkyrie.dream.started/completed` (`dream_kind: consolidation`), `learning.promoted` |
| **Mimir curation** | Cron: the `dream_cycle` trigger fires the `mimir-curator` persona | Knowledge-base hygiene: enrich, lint, cross-reference Mimir pages | `dream_cycle` (cron expression, persona, token budget) | `mimir.dream.completed` |

## Where each lives

- Micro-dream: `src/ravn/valkyrie_evolution/resident_learning.py`
  (`_evolve_missing_capability`). Runs inside `ResidentLearningRuntime`,
  wired in the daemon's resident block.
- Consolidation dream: `src/ravn/valkyrie_evolution/wakefulness.py`
  (`ResidentWakefulness.dream`). Driven by the wakefulness tick; reads
  feedback episodes recorded by the feedback recorder.
- Mimir curation: `src/ravn/adapters/triggers/dream_cycle.py`. A drive-loop
  cron trigger; it enqueues an agent task, it does not touch resident skills.

## Configuration boundaries

- `resident_evolution` — how a resident builds, reviews, and rolls back its
  own tools (builder/reviewer adapter selection, autonomy mode, canary
  timeout, rollback threshold). Consumed by the resident learning runtime.
- `resident_wakefulness` — when the resident is wakeful/watching/dreaming and
  when consolidation runs. Consumed by the wakefulness state machine.
- `dream_cycle` — the Mimir-curator cron only. Its `autonomy_mode` and
  `proposal_store_path` apply to knowledge-base improvement proposals, not to
  resident skill evolution.

## Autonomy modes (canonical)

`guarded | autonomous | yolo` — everywhere: the autonomy policy
(`src/ravn/context/autonomy.py`), the Environment domain model, the dashboard
API, and the UI. Every mode builds when it sees a capability gap; the modes
differ only in what happens to the finished artifact. Guarded residents hold
the install behind an ODIN review request, autonomous residents install
low-risk builds, and YOLO residents install anything without blocking
findings.

## The unified ODIN review path

Everything that needs a human decision rides one envelope
(`src/ravn/odin/review.py`): a `ReviewItem` published as
`odin.review.requested`, decided by an operator as `odin.review.decided`,
applied by the target resident, and confirmed as `odin.review.resolved`.
Held builds, guarded skill promotions, peer-learning verdicts, court
draft-for-review escalations, and operator autonomy changes are all kinds of
the same item — there is no separate command channel per type. Operators
change a resident's mode through `POST /api/v1/ravn/valkyrie/autonomy`, which
publishes an already-decided `autonomy_change` ReviewItem; the resident
applies it and confirms with `valkyrie.state.updated`. Residents persist
pending requests in `.ravn/review_outbox.json` and re-announce them on
restart.
