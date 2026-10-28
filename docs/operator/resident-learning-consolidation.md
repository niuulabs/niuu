# Resident Learning and Self-Improvement

A resident learns from its own sessions and acts on its own friction without
anyone editing its persona. These are two halves of one loop. The **learning
loop** decides what the resident has learned. After every session, reflection
records a candidate lesson, which is matched by meaning, promoted on evidence,
curated in the dream, and injected within a budget. The **self-improvement
loop** uses that to act. It records the friction the resident keeps hitting,
chooses a few improvements with an explicit expected benefit, checks whether
they helped, and stops the resident from asking the operator about walls it
has already dealt with.

## Why

Regin found the same lesson four times on 2026-10-23/24 and kept none of
them: "Use github.com/.diff when api.github.com checks return 403", "Probe
distinct hosts before declaring a capability gap", "Test alternate GitHub web
routes before declaring a read-route capability gap". Word-overlap matching
saw three unrelated candidates, each with one observation. None reached the
three observations needed for promotion. An older candidate, "all routes 403",
contradicted them and was never resolved. Candidates are never injected, so
the next turn still reported that it could not inspect the patch.

## What happens now

1. **Matching by meaning** (`reflection.matching.mode: semantic`). A new
   observation is judged against its *neighbourhood*: the nearest
   `max_related` learnings and, separately, the nearest `max_related`
   candidates above `similarity_threshold`. Learnings get their own share so
   a crowd of paraphrased candidates cannot push the trusted learning out of
   view. One judge call names every lesson that says the same thing and every
   one that contradicts it. All matches this resident writes fold into the
   strongest one, which is a promoted learning if there is one: the new
   observation's session, evidence note and wording (under
   `## Related observations`), any verification, and the other matches.
   The other matches are retired as `merged`. A learning that absorbs an
   observation gets `last_confirmed`. With no match, the observation becomes a
   new candidate. An evidence note that embeds at or above
   `evidence_duplicate_similarity` against one already recorded counts as the
   same fact read twice, not a second observation.

   Before 2026-10-27 the judge returned only the *first* match, and candidates
   and learnings competed for one top-5. A paraphrase often fed a sibling
   candidate until that candidate promoted beside the learning it duplicated.
   On noatun that happened in 13 of the 21 promotions in the first day of
   semantic matching.
2. **Evidence-weighted promotion**. The agent puts *route recoveries* on
   `ravn.session.ended`. A route recovery is a tool call that failed, followed
   in the same turn by a successful call to the same tool with different
   input. These come from the tool results, not from the model. When the
   reflection says a lesson restates one of them (`verified_by_recovery`), the
   candidate is marked `verified: true`, gets a `## Verification` section, and
   promotes on its first observation (reason `verified_route_recovery`). Set
   `verified_promotion.route_recovery_promotes: false` to turn this off.
   Speculative lessons still need `candidate_min_repetitions` observations.
3. **Curation in the consolidation dream** (`reflection.curation.enabled`).
   This runs in every consolidation dream, or at most once per
   `min_interval_seconds` if that is set. It is incremental. Each lesson not
   yet marked `curated` has its neighbourhood judged once, strongest lesson
   first; the lesson is then marked `curated: <time>` and never judged again.
   The work grows with new lessons, not with all pairs of lessons, so nothing
   is capped. The old fixed budget of 40 pairs a day only let the backlog
   grow. The pass then:
   - **merges** the lessons the judge calls the same. The survivor is a
     promoted learning if there is one, otherwise the lesson with the
     strongest evidence. If the merged evidence is enough, the survivor
     promotes.
   - **supersedes** one side of a contradiction. The stronger side stands. In
     order, strength is verified evidence, then being a promoted learning,
     then more observations, then newer evidence.
   - **prunes** candidates seen once, never verified, and not confirmed again
     within `prune_after_days`.
   - **rewrites** every promoted learning without one into a short
     `When X, do Y.` rule in a `## Procedure` section, once per learning.

   Nothing is deleted. A retired page keeps its content and gets `status:
   merged|superseded|pruned`, a `merged_into` or `superseded_by` field, and a
   `curation_reason`. Every pass that changes anything writes an audit page
   under `learning-curation/`. Learnings under `learnings/{environment,domain,
   flock,shared,private}/` belong to cross-scope promotion and are never
   rewritten here.
4. **Injection**. At session start, active learnings go into the prompt.
   Merged, superseded and pruned learnings are skipped. With semantic matching
   on, learnings are ranked by similarity to the first input; otherwise the
   most recently updated or confirmed come first. A learning with a procedure
   is injected as its rule. Any other learning is injected as its claim,
   capped at `learning_entry_max_chars`, all within `learning_token_budget`
   and `max_learnings_injected`.

## The self-improvement loop

5. **Friction capture** (`reflection.friction.enabled`). Every session's
   friction is read from its own record, never from the model's account of it:
   - from tool results: `tool_failure`, `timeout`, `retry`,
     `budget_exhausted`, and `route_recovery`, which is a workaround (a failed
     route that was bypassed);
   - from the outcome contract: `escalation` (`continuation: ask_operator` or
     `decision: escalate`), `capability_gap` (`working_state.capability_gaps`)
     and `blocked`.

   Signals of the same kind that embed at or above `similarity_threshold` are
   occurrences of one friction record at `friction/<kind>/<slug>.md`. Reflection
   lists the session's friction to the model, and the model says which items the
   lesson would prevent (`addresses_friction`). Those friction records are
   linked to the lesson.
6. **Self-improvement pass** (`reflection.self_improvement.enabled`). This runs
   in the consolidation dream, right after curation:
   - **Judge earlier actions.** An action is *ineffective* when its friction
     comes back `recurrence_threshold` times inside
     `evaluation_window_seconds`. For escalations this means the operator was
     asked anyway after the resident was redirected. Ineffective learnings are
     demoted (`status: demoted`), and the same action is never retried for that
     friction. An action whose friction stays quiet for the whole window is
     *effective*. That is recorded as evidence on the learning it relied on, and
     the friction is `resolved`.
   - **Adopt learnings.** A friction linked to a promoted learning is now
     addressed by that learning (action `learned`).
   - **Choose at most `max_actions`** for recurring friction (`min_occurrences`)
     that nothing addresses yet. Each choice needs an expected benefit:
     - `promote_learning` and `rewrite_learning`;
     - `retire_belief`, for a stale belief or capability-gap claim;
     - `commission_tool`, for a recurring need no capability covers, via
       `build_tool`;
     - `file_defect`, when the platform itself is at fault (`tracker_issue`,
       team `defect_team`, labels `defect_labels`).

     Proposals the model gets wrong are rejected and listed, not acted on: an
     unknown friction, a disallowed action, a missing benefit, or an action
     already tried.
7. **No new privileged path.** Learning actions go through Ravn's own evidence
   gates. A tool commission or a ticket is dispatched to the drive loop as an
   ordinary resident task. It runs with the persona's tool permissions, through
   `build_tool`'s autonomy, review and canary policy, and under the persona's
   branch rule, like any other turn.
8. **No repeat escalations.** Before a question reaches the operator, the
   resident runtime asks the friction ledger whether the resident already
   addressed that friction. If so, the case sleeps for `redirect_delay_seconds`
   and wakes with the resolution: the learned procedure, the commissioned
   tool's task, or the filed defect. This happens once per case. A second ask
   in the same case reaches the operator, and it counts against the
   resolution.
9. **Audit and telemetry.** Every pass that does anything writes
   `self-improvement/<ts>.md`, a "what I improved and why" brief. It has four
   parts: did earlier improvements help, what changed and the expected benefit,
   proposals not acted on, and recurring friction still open. The following
   counters are emitted, each with a matching span event:
   - `ravn.friction.recorded`
   - `ravn.self_improvement.{chosen,acted,rejected,evaluated}`
   - `ravn.resident.escalations_redirected`
   - `ravn.resident.escalations_despite_resolution`

Ownership stays in Ravn (`.claude/rules/ravn-niuu-boundary.md`). Ravn makes
the friction judgment, picks the improvements, and decides the learning
policy. Niuu only supplies the Mímir and embedding adapters.

## Configuration

```yaml
embedding:            # required by semantic matching and curation
  enabled: true
  adapter: ravn.adapters.embedding.openai.OpenAIEmbeddingAdapter
  kwargs: {base_url: https://…/v1, model: Qwen/Qwen3-Embedding-0.6B}

reflection:
  enabled: true
  llm_alias: Qwen/Qwen3.8-27B
  candidate_min_repetitions: 3
  learning_token_budget: 500
  max_learnings_injected: 5
  learning_entry_max_chars: 600
  matching:
    mode: semantic                    # lexical | semantic
    similarity_threshold: 0.65          # Qwen3-Embedding-0.6B scores paraphrases 0.72-0.75
    max_related: 5
    embed_batch_size: 64
    evidence_duplicate_similarity: 0.9
    judge_llm_alias: ""               # empty = reflection.llm_alias
    judge_max_tokens: 512
    judge_attempts: 2
  verified_promotion:
    route_recovery_promotes: true
    max_route_recoveries: 5
    excerpt_chars: 240
  friction:
    enabled: true
    max_signals: 10                   # tool-level signals per session
    similarity_threshold: 0.8
    max_occurrences_kept: 50
    ledger_prefix: friction
  self_improvement:
    enabled: true                     # needs friction + curation
    max_actions: 2
    min_occurrences: 2
    max_candidates: 8
    allowed_actions: [promote_learning, rewrite_learning, retire_belief,
                      commission_tool, file_defect]
    evaluation_window_seconds: 259200 # 3 days
    recurrence_threshold: 2
    redirect_delay_seconds: 60
    task_persona: ""                  # empty = the resident's default persona
    task_priority: 10
    defect_team: NIU
    defect_labels: [platform-defect]
    audit_prefix: self-improvement
  curation:
    enabled: true                     # needs resident_wakefulness.enabled
    min_interval_seconds: 0           # 0 = every consolidation dream
    prune_after_days: 14
    audit_prefix: learning-curation

resident_wakefulness:
  enabled: true
```

These are startup errors, never a silent fall back to lexical matching:

- `matching.mode: semantic` or `curation.enabled: true` without
  `embedding.enabled: true`;
- either one on a runtime with no native LLM for the judge;
- `curation.enabled: true` when resident wakefulness is not running;
- `friction.enabled` without embeddings;
- `self_improvement.enabled` without `friction` and `curation`;
- `self_improvement.enabled` with `commission_tool` or `file_defect` allowed
  but no drive loop to dispatch them.

When the judge returns malformed output after `judge_attempts` tries, the
reflection or curation pass raises instead of recording a duplicate. A failed
curation pass still writes its audit page, with an `## Error` section, before
it raises.

## Backfilling existing candidates

Lessons recorded before this existed are consolidated by curation: the next
consolidation dream judges every lesson not yet `curated`, one call each. To
do it at once rather than waiting for the dream, run the same pass inside the
resident pod:

```bash
KUBECONFIG=~/.kube/kubeconfigs/valhalla.yaml kubectl -n nats exec deploy/regin -c ravn -- ravn learnings-curate
```

It prints counts for reviewed lessons, neighbourhoods judged, and merged,
superseded, pruned, promoted and rewritten lessons. The audit page under
`learning-curation/` lists every change and its reason. It is safe to rerun:
a second run only judges lessons recorded since.

Before trusting the result, check the audit page for supersessions. Each line
says which evidence decided the contradiction.

The friction ledger starts empty. Friction is recorded from sessions that end
after `friction.enabled` is turned on; past escalations are not replayed into
it. Once the backfill has promoted a procedure, its friction is linked to that
procedure as soon as the resident hits it again, and the next dream adopts it.

