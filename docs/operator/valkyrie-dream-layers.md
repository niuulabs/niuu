# Valkyrie Reactive and Dream Layers

A resident grows in three deliberately separate layers, each with its own
trigger, scope, and configuration. This doc exists so nobody re-merges them or
mistakes one for another. The reactive layer (the investigation loop) replaced
the retired classifier micro-dream and no longer publishes `*.dream.*` events;
the two scheduled layers below still do.

| Layer | Trigger | Scope | Config | Events |
| --- | --- | --- | --- | --- |
| **Investigation loop** | Reactive: a signal arrives with no installed capability | Escalate to an agent session that authors the instrument(s) it needs with `build_tool` (inline, or commissioned via a Forge/Ting backend), reviews, canaries, installs, proposes to flock | `resident_evolution` (reviewer adapter, autonomy, rollback, `tool_build_backend`) | `valkyrie.evolution.*`, `flock.learning.proposed`, session/drive-loop events |
| **Consolidation dream** | Scheduled: idle + interval gates in the wakefulness state machine | Reflective pass over the whole skill registry: mark stale, promote proven private skills, hold skills implicated by feedback | `resident_wakefulness` (intervals, idle gates, promotion thresholds) | `valkyrie.state.changed`, `valkyrie.dream.started/completed` (`dream_kind: consolidation`), `learning.promoted` |
| **Mimir curation** | Cron: the `dream_cycle` trigger fires the `mimir-curator` persona | Knowledge-base hygiene: enrich, lint, cross-reference Mimir pages | `dream_cycle` (cron expression, persona, token budget) | `mimir.dream.completed` |

## Where each lives

- Investigation loop: a missing capability returns
  `defer_to_investigation_with_build_tool` from
  `ResidentLearningRuntime.process_signal`
  (`src/ravn/valkyrie_evolution/resident_learning.py`); the signal escalates
  through the environment signal runtime into an agent session whose toolbox
  includes `build_tool` (`src/ravn/adapters/tools/build_tool.py`). The session
  authors, reviews, canaries, installs, and proposes the instrument.
- Consolidation dream: `src/ravn/valkyrie_evolution/wakefulness.py`
  (`ResidentWakefulness.dream`). Driven by the wakefulness tick; reads
  feedback episodes recorded by the feedback recorder.
- Mimir curation: `src/ravn/adapters/triggers/dream_cycle.py`. A drive-loop
  cron trigger; it enqueues an agent task, it does not touch resident skills.

## Configuration boundaries

- `resident_evolution` — how a resident reviews, canaries, and rolls back the
  tools its investigation sessions author (reviewer adapter selection, autonomy
  mode, canary timeout, rollback threshold, `tool_build_backend`). Consumed by
  the resident learning runtime.
- `resident_wakefulness` — when the resident is wakeful/watching/dreaming and
  when consolidation runs. Consumed by the wakefulness state machine.
- `dream_cycle` — the Mimir-curator cron only. Its `autonomy_mode` and
  `proposal_store_path` apply to knowledge-base improvement proposals, not to
  resident skill evolution.

## Autonomy modes (canonical)

`guarded | autonomous | yolo` — everywhere: the autonomy policy
(`src/ravn/context/autonomy.py`), the Environment domain model, the dashboard
API, and the UI. An investigation session authors the tool it needs via
`build_tool` in every mode; the modes differ only in what happens to the
finished tool. Guarded residents hold the install behind an ODIN review
request, autonomous residents install low-risk tools, and YOLO residents
install anything without blocking findings.

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
