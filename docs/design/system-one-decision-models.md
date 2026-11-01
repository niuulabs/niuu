# System One decision models in the Niuu stack

Status: analysis, 2026-10-28. Step 1 of §5 (port, adapter, telemetry), the
observe-only §3.1 opinion per inbox slot, the §5 step 2 calibration command and
the §3.1 gate are implemented — see §6. The gate is off; nothing acts on a
decision-model answer yet.

Question: where can a fast decision model — TypeSafe **Jev**, Cloudflare
**Clef / Clef-flash**, or the other `/v1/systemone` models — replace or
pre-filter decisions that today cost a big-model turn, or that today are made
by heuristics standing in for judgment?

Short answer: there are about a dozen good sites. The biggest one is not the
ODIN court itself but the step *before* it — the resident turn that produces
the judgment the court resolves. Most of the rest are heuristics (regex tables,
word-overlap dedupe, substring capability search) that a decision model would
make semantic without adding a big-model call.

---

## 1. What these models are

A System One model takes `state` plus a set of typed questions and returns, in
a single prefill-only pass, a probability for every allowed answer. It does not
generate text.

| Primitive | Returns | Use |
|---|---|---|
| `noul` | P(yes) | one condition |
| `choice` | chosen option, per-option probabilities, confidence (1–255 options) | one of a set |
| `score` | expected level, per-level probabilities, confidence (1–10 levels) | degree on a rubric |

Many independent questions can share one request over the same state, and they
run in parallel. The wire protocol is shared: `POST /v1/systemone`, the same
request and response shape for hosted Jev, Clef (SGLang/vLLM), Ollama ≥ 0.35
and the community servers. **One adapter covers every backend.**

### The models

| | Jev 1.14 | Clef | Clef-flash | Nimble (Ollama) |
|---|---|---|---|---|
| Weights | closed, hosted only | Apache-2.0, Qwen3.8-27B post-trained | Apache-2.0, Qwen3.5-9B post-trained | open, 9B |
| Context | 64k request / 32k per question (262k "danger zone") | 64k | 64k | — |
| Modalities | text | text, image, video | text, image, video | text |
| Price | $0.042 / M input tokens, output free | $0.24 / M on Workers AI, or self-host | self-host | self-host |
| Median latency | 524 ms (Cloudflare's table) | 209 ms (same table) | ~60–75 ms FP8 on a 16 GB GPU (community) | 91 ms on an M5 Max (Ollama demo) |

Cloudflare's self-reported Decision Index results for Clef vs Jev (not yet
reproduced independently):

- **Clef wins on routing and intent work.** BFCL 98.5 vs 95.8, BANKING77 94.2 vs
  79.7, CLINC150+OOS 97.4 vs 89.3.
- **Jev wins clearly on reasoning and knowledge.** GPQA 78.3 vs 48.0, MMLU-Pro
  82.7 vs 65.9, BBH 92.9 vs 73.7.

That split should drive placement. Clef suits "which handler / which route /
which candidate". Jev suits judgments that need world knowledge or a couple of
inferential steps.

### GPU requirements for running Clef locally

| Setup | Memory | Source |
|---|---|---|
| Clef, BF16, 64k context, single request | ~85 GB VRAM | Cloudflare, via The Register |
| Clef-flash, BF16, 64k context | ~41 GB VRAM | Cloudflare, via The Register |
| Clef weights alone, BF16 | ~54 GB (Clef) / ~18 GB (Clef-flash) | third-party estimate |
| Clef / Clef-flash, NVFP4 on Blackwell (vLLM) | ≥ 24 GB | openjev |
| Clef-flash, FP8 + `torch.compile`, micro-batched | 16 GB card, ~11.5 GB used, ~24 req/s at 16 concurrent | clef-flash-local (RTX 5080) |
| Ollama GGUF downloads | 18.0 GB (`clef`), 10.9 GB (`clef-flash`) | Ollama library |

What this means for our hardware:

- **DGX Spark (GB10, 128 GB unified, Blackwell).** Full BF16 Clef fits on one
  Spark, and NVFP4 is native there. These models do prefill only, with no
  decode loop, so they depend on compute rather than memory bandwidth — the
  Spark's weak spot is bandwidth, which makes it a better host for these than
  for chat models.
  - The valaskjalf Sparks already serve Qwen3.8-27B, which is Clef's backbone.
  - saehrimnir runs Nemotron 3 Super for Ivaldi. Clef-flash at FP8 (~12 GB)
    probably fits beside it. Measure free unified memory before relying on
    that.
- **Apple Silicon Macs.** Clef or Clef-flash in GGUF form through Ollama runs
  on any 32 GB or larger M-series machine. That matters for Fulla, the
  Mac-native home steward, which would then never send home signals off the
  machine.
- **Consumer cards.** Clef-flash at FP8 needs a 16 GB card. Clef needs NVFP4 on
  Blackwell (24 GB or more) or a larger card.

### Known weaknesses that constrain placement

These come from TypeSafe's published Jev 1.13 "jaggedness" list. Assume they
apply to every model in this class until measured.

1. **Adversarial state shifts answers.** The model does not treat input as
   hostile. Never make it the only gate on anything security-relevant.
2. **Counting, dates, durations and arithmetic are unreliable.** Do these in
   code and pass the result in as state.
3. **Irrelevant state is a distractor.** Filter before asking. The coalescing
   inbox slot is the right input; 50 raw signals are not.
4. **It reads questions literally**, and accuracy drops with double negatives
   or multi-hop reasoning.
5. **Choice has a first-option bias.** Shuffle options, or put "none" first
   deliberately.
6. **Confidence measures how concentrated the distribution is.** It is not
   calibrated correctness and it is not permission to act. Thresholds have to
   be fit on our own data.

---

## 2. Where it belongs architecturally

### The court is not the place for it; the step before the court is

`OdinCourt` ([court.py](../../src/ravn/odin/court.py)) contains no model. It is a
deterministic fan-in that resolves the decision from fixed tables:

- `_highest_tier` takes the highest tier the residents proposed.
- `_action_authorization` reduces their authority claims to one value.
- `_decision_for` maps tier and authorization to a decision: autonomous action,
  draft for review, open huddle, notify, record only, or no-op.

That is correct and should stay deterministic: it is the auditable authority
boundary. The *semantic* work already happened upstream, in every resident
turn's outcome block. `resident_outcome_template` in
[valkyrie_contracts.py](../../src/ravn/domain/valkyrie_contracts.py) asks for
`decision` (ignore/watch/investigate/propose_action/escalate/learn/blocked),
`tier`, `confidence`, `action_authority` and so on.

So "routing court decisions through Jev" really means: **let a System One
model produce the first-opinion judgment, and only spend a big-model turn
when it is unsure or when the stakes require one.** The court keeps resolving
exactly as it does now.

### The decision model belongs to Ravn, and is a port

Per [ravn-niuu-boundary.md](../../.claude/rules/ravn-niuu-boundary.md), judgment
belongs to Ravn. A System One model *is* judgment — a cheaper tier of Ravn's
own judgment — so:

- **`ravn/ports/decision.py` — `DecisionPort`.** It takes `state` and a dict of
  typed questions and returns typed answers. No implementation outside Ravn
  decides what an observation means.
- **One adapter: `ravn.adapters.decision.systemone.SystemOneDecisionAdapter`,**
  selected with `adapter:` plus kwargs (`base_url`, `model`, `timeout`, `long_context`). It
  serves Jev, Clef, Ollama and the self-hosted servers unchanged. Choosing
  hosted or local is a values-file change.
- **No decision-making in Niuu, Skuld or `niuu.collaboration`.** For example,
  the observation relay (§3.2) must not gain a salience filter. Salience is
  decided where the message is received, in Ravn.
- **Bifrost can proxy the protocol (optional):** a `/v1/systemone` passthrough
  route with the existing keys, accounting and audit. Bifrost already proxies
  Ollama. Doing this keeps cost and usage in one place, but it is plumbing,
  not a precondition.

### Escalating on low confidence is not a fallback; carrying on after a failure is

[no-fallbacks.md](../../.claude/rules/no-fallbacks.md) needs one sentence of
interpretation here, because these two cases look alike and are opposites:

- **Low confidence → full big-model turn.** This is the designed path. "I am
  unsure" is an *answer*, and the policy says what to do with it.
- **Decision backend unreachable or erroring → raise.** It must not quietly
  run every case through the big model, and must not quietly skip the gate.
  Either would bring back the silent-degradation bug the rule exists for. If an
  operator wants the big model alone, the decision model is not configured;
  that is the legitimate escape.

The attention gate should follow the inbox's existing fail-open rules
(§5.4 of [ravn-resident-attention.md](../ravn/ravn-resident-attention.md)):
**uncertain means wake.** Fail-open applies to the *answer*. It never applies
to the *service* failing.

---

## 3. The opportunity map

Ranked by leverage, meaning frequency × cost avoided × quality gained. File
references are to current `src/`.

### Tier 1 — big, direct wins

#### 3.1 Pre-attentive gate on resident turns (the "court input" lever)

**Where:** `ResidentRuntime.next_home_task`
([resident_runtime.py:1383](../../src/ravn/resident_runtime.py)), `next_stewardship_task`
(`:1492`), and `EnvironmentSignalRuntime._triage_task`
([environment_signal_runtime.py:462](../../src/ravn/environment_signal_runtime.py)).

**Today:**

- Any new inbox slot wakes a full thinking-enabled turn.
- Stewardship wakes run on a timer, and their own prompt treats "nothing
  warrants action" as the expected answer.
- Signal wake is `signal.severity in signal_task_severities` without the inbox; with
  `resident_inbox.environment_signals_enabled` every observation reaches a home
  turn through a coalescing slot and only `critical` wakes it early.

**With a decision model:** one request per slot at selection time (the
evaluation point §5.2 of the attention doc already chose). Its state is the
slot aggregate (ranges, counts, novel payloads), the resident's charter and
mandate, its last `working_state`, and the last few judgments on this shape.
That state stays small: a request is one judgment, not a turn. Each part has its
own bound under `decision.resident_first_opinion` (`max_observation_chars` 4000,
`max_mandate_chars` 2000, `max_working_state_chars` 2000), so a request stays
near 8000 characters, about 3600 tokens on Kubernetes evidence. A cut keeps the
head (identity, counts, ranges) and is reported as a cap hit. Routing state
(`_resident` on an A2A update) is never part of it, or of a home prompt.
Questions:

- `choice` **tier**: silent / ambient / present / urgent. This is the same
  vocabulary the court consumes.
- `choice` **decision**: ignore / watch / investigate / escalate.
- `noul`: "Does this contradict or extend anything in the working state?"
- `noul`: "Does this need tools or investigation to judge?"

**Policy, in code:**

- Confidently silent or ambient, with no investigation needed → record the
  outcome (`record_only`) and carry the slot in the *next* turn's receipt block
  (§5.1 of the attention doc). Nothing is hidden.
- Anything else, or confidence below the threshold → the existing big-model
  turn, with the System One opinion attached as evidence.

**Why it matters:**

- **Cost.** It is the highest-volume decision in the stack: Ivaldi had 84,066
  observations, and 50 signals per wake.
- **It closes the documented gap.** §8 of the attention doc says a young slot
  "can learn an anomaly as normal" because structural novelty "knows only how
  much a field has varied, never what the field *is*". Closing that "means
  judging meaning rather than variance". A decision model judges meaning on
  every slot, from its first observation.
- **The design already allows for it.** §7 of the attention doc lists "a
  nano-tier triage model … more in the spirit of 'Ravn owns judgment' than a
  frozen matcher" as deferred. This is that idea, with calibrated outputs. It
  may also make the stage-3 matcher language unnecessary.

**Evaluation data already exists, so there is no need for a shadow-mode
build:**

- Replay the `raw/` NDJSON archive and the `processed/` slots, and compare the
  System One tier and decision against the tier and decision the big model
  actually produced.
- Calibrate thresholds on the human labels in
  [feedback/recorder.py](../../src/ravn/feedback/recorder.py): `wrong_tier`,
  `should_have_shown`, `too_late`, `dismissed`.

#### 3.2 Bifrost Switchyard judge

**Where:** `SwitchyardModelSelection.route`
([switchyard_models.py:156](../../src/bifrost/adapters/switchyard_models.py)).

**Today:** a full LLM call, with a JSON-schema enum of `route.targets`, on
**every proxied request** to a judged route. It adds a model round-trip plus
tokens in front of the real request.

**With a decision model:** this is a literal `choice` over `route.targets`, with
the route's prompt as instructions — exactly the "replace a prompt that returns
JSON" case. Clef's BFCL and CLINC numbers are the relevant benchmarks. It cuts
latency on every judged request, and confidence lets the route take an
explicit high-capability default when unsure, in place of `default_target =
targets[0]`.

Related: the regex `content_matches` / `system_prompt_matches` rules in
[yaml_engine.py:224](../../src/bifrost/adapters/rules/yaml_engine.py) stand in for
intent or difficulty classification.

#### 3.3 "Does an installed capability already do this?" before a build

**Where:** `_already_serves_request` → `find_installed_capability`
([build_tool.py:778](../../src/ravn/adapters/tools/build_tool.py)).

**Today:** exact `capability_id` or name match. A miss commissions a Ting
tool-build that runs for tens of minutes. The other catalog searches are no
better: `capability_catalog.py:84` `matches_query`,
`skill/file_registry.py:132`, `workflow_tools.py:634` are all casefolded
substring search.

**With a decision model:** use TypeSafe's two-request skill-suggestion
pattern.

1. A `choice` over all installed capabilities, plus "none", using their
   descriptions.
2. A re-check of the top three against their full contracts, with one `noul`
   per candidate: "Does this tool satisfy this request?"

In the cookbook this cut wrong skill loads by 2.3× and needless loads by 2.4×.
Every true hit saves a whole build workflow, and it is the main defence
against learned-tool sprawl (see the curation memory: repair over variant).

#### 3.4 Unroutable workflow verdict repair

**Where:** `_maybe_repair_unroutable_workflow_outcome`
([drive_loop.py:3514](../../src/ravn/drive_loop.py)), prompt at `:326`.

**Today:** when a node's verdict cannot be routed, Ravn re-runs the full agent
*with tools* so that it emits one of `allowed_topics` or `help_needed`.

**With a decision model:** a `choice` over `allowed_topics ∪ {help_needed}`,
with the node's actual output as state. High confidence routes it; low
confidence keeps the existing repair turn. The question is bounded, it happens
per failure, and the repair is expensive.

### Tier 2 — heuristics that should become semantic

None of these add a big-model call. They replace code that pretends to
understand.

| Site | Today | Decision-model shape |
|---|---|---|
| Inbox classification, [classify.py:104](../../src/ravn/resident_inbox/classify.py) | Ordered regex table; 11 classes with hard-coded "confidences" 0.55–0.9 | One `choice` over the 11 classes plus "other", with real probabilities. The attention doc says not to *extend* the table; replacing it is the alternative. Today its output gates nothing, so wire it into §3.1's state, or leave it alone. |
| Thread enricher, [thread_enricher.py:216](../../src/ravn/adapters/triggers/thread_enricher.py) | `fast` LLM returns `{is_thread, confidence}`, threshold 0.7 | `noul` "is this an open thread?", plus a `choice` for the next action. A direct replacement; runs per Mímir page. |
| Thread persona selection, [thread_queue.py:182](../../src/ravn/adapters/triggers/thread_queue.py) | `draft`/`note` keyword → persona | `choice` over the configured personas |
| Post-session reflection gate, [post_session.py:278](../../src/ravn/adapters/reflection/post_session.py) | `fast` LLM generates a learning or `null`; `_looks_like_no_learning_response` (`:595`) is a phrase list | First a `noul`: "Did this session produce a reusable, non-obvious learning?" Generate only on yes. |
| Learning dedupe, `post_session.py:491/1126/1157` | Keyword search plus Jaccard ≥ 0.5 | `score` "same learning?" over the top-k candidates (entity-alignment pattern) |
| Turn-memory gate, [agent.py:1710](../../src/ravn/agent.py) `_is_worth_remembering` | "Used a tool, or the answer is longer than 12 characters" | `noul` "worth remembering?" (the TypeSafe basic-memory cookbook) |
| Inline facts and retractions, [inline_facts.py:69/84](../../src/ravn/adapters/memory/inline_facts.py) | Regexes such as "I prefer" and "scratch that", run on every user turn, writing Mímir pages | `choice` for fact type plus `noul` "retracts an earlier statement?" |
| Cron restatement, [cron_tools.py:333](../../src/ravn/adapters/tools/cron_tools.py) | Word overlap ≥ 0.5 refuses to create the job | `noul` "same intent as existing job X?" |
| Flock learning dedupe, `flock_learning.py:55` | Normalised-title fingerprint | Same as learning dedupe |
| Entity identity, [entity_extractor.py:131](../../src/ravn/adapters/tools/entity_extractor.py) | Haiku extracts entities; identity is slug equality | Keep Haiku for extraction. Add a `score` "same entity as page P?" over slug and FTS candidates. |
| Mímir evidence support, `mimir/learning.py:79/115/239` | ≥ 2 shared tokens means "supports" | `choice` supports / contradicts / unrelated, per (entry, fact). This is also what lint L02 contradiction detection lacks — today L02 only finds a manually written `[CONTRADICTION]` marker. |
| Mímir link repair, `markdown.py:1596/1862` (L05) | `difflib` closest slug at 0.6 | `choice` over the difflib top-5 plus "none" |
| Reranking, `RerankerPort` ([ports/reranker.py](../../src/ravn/ports/reranker.py)) | `QwenRerankerAdapter`, off by default, SQLite memory only; Mímir `ranking.py` has no cross-encoder | A `SystemOneRerankerAdapter` implementing the existing port: one `noul` per (query, doc) in one request. The reranking cookbook took top-10 accuracy from 38% to 62%. |
| Learning injection, `post_session.py:694` | Path prefix decides which learnings reach the prompt | Score relevance of each candidate learning to the task |
| Skuld reply routing, [broker.py:4605](../../src/skuld/broker.py) | Telegram reply → pending question only with ForceReply or a single candidate; with more than one, it falls through | `choice` over pending questions plus "new message". This routes; it is not judgment, so a Skuld adapter calling a Ravn-independent port is acceptable. Alternatively, keep Skuld dumb and let the room's Ravn decide. |
| Court dissent, `court.py` `_append_value_dissent` | Free-text `recommended_action` compared by string equality, so "restart the pod" and "restart pod" count as dissent | `noul` "same action?" over the distinct values. This only affects dissent reporting; the decision stays deterministic. |
| Review queue triage, `court.py` `_file_escalation_review` and the other `ReviewItem` producers | `urgency` is `{"urgent": 0.9, "present": 0.7}`, otherwise 0.5 | `score` for urgency and risk against a rubric, used to order the operator queue only. It never decides. |
| Ting notification urgency, `ting/domain/services/notification.py:32` | Static map by event type | `score` urgency, for presentation |

### Tier 3 — advisory only, never the gate

| Site | Why advisory |
|---|---|
| Permission auto-approval (volundr `permission_auto_approval.py:93`, regex allow/deny lists), shell intent `enforcer.py:171`, prompt-injection regexes (`context/__init__.py:37`, `web_fetch.py:26`) | Weakness #1: adversarial state moves answers. A decision model can add a risk score next to the deterministic lists, the way the LLM-guardrails cookbook does, and must never be the thing that says yes. |
| Ting `human_approval` gates (`skuld/workflow_runtime.py:67`) | Pre-screen and annotate for the human ("the evidence covers 4/5 acceptance criteria"). The human, or the `evidence` gate, still decides. |
| `LearningPromotionPolicy.decide` (`learning_promotion.py:~162`) | It consumes a `confidence` nobody calibrates. A System One `score` would give it a calibrated input, but promotion is a policy and review decision; keep it so. |

### Where not to use it

- **Code review and tool-build gate review** (`gate_review.py:65`, the reviewer
  and critic personas). These are System Two: Jev's BBH and GPQA gap and the
  multi-hop weakness both apply. Keep the agent model.
- **Coordinators' "what next" choice** (`developer-coordinator`,
  `research-coordinator`). The output looks like an enum, but choosing it needs
  the whole tool-loop context. Revisit only once §3.1 has data.
- **Generation of any kind:** planning (`ting decompose_spec`), synthesis
  (gbrain `think`), compression, reflection text.
- **Anything about counts, dates or windows.** Compute these in code.

---

## 4. Bugs found while tracing

These came up while mapping decision sites. They are independent of any
decision-model work.

1. **The wakefulness reflection has no conversation in its prompt.**
   `WakefulnessTrigger._reflect`
   ([wakefulness.py:200](../../src/ravn/adapters/triggers/wakefulness.py)) calls
   `_SHALLOW_PROMPT.format(max_intents=...)` and nothing else. The prompt says
   "reflect on a conversation" but contains none, so the model invents intents
   from nothing. Its exception handling also logs and continues, which violates
   the no-fallbacks rule.
2. **Wakefulness is gated by one config section and run with another.**
   Registration checks `settings.resident_wakefulness.enabled`, but the trigger
   runs with `settings.wakefulness`
   ([trigger_wiring.py:92](../../src/ravn/cli/trigger_wiring.py)).
3. **`ConditionPollTrigger` is dead code.** Nothing outside its own module
   constructs it, and it reads a `SilentChannel` the sensor never writes to, so
   it would always return `CLEAR`.
4. **Unused Búri config.** `extraction_model`, `min_confidence`,
   `cluster_merge_threshold` and `supersession_cosine_threshold` in
   `ravn/config.py` (~2354–2380) are never read.

---

## 5. Recommended path

1. **Port and adapter.** Add `DecisionPort` and `SystemOneDecisionAdapter`
   (dynamic `adapter:` config). Test against the port with a fake.
   - Run Clef-flash on a Spark (NVFP4 or FP8) as the default local backend.
   - Keep hosted Jev config-selectable for comparison.
2. **Offline evaluation of §3.1 on Ivaldi's archive.** No production change
   yet. Replay archived slots; measure System One against the big-model
   tier/decision agreement, calibration per threshold, and the token and
   seconds avoided. Report Jev and Clef side by side; this answers
   "which model" with our own data, not with vendor benchmarks. Use
   RavnBench-style discipline: no rollout without the delta.
3. **§3.2 Switchyard judge and §3.3 capability matching.** Bounded, frequent,
   easy to evaluate.
4. **Tier 2 replacements, one per change**, each with a small labelled
   fixture.
5. **Turn on the §3.1 gate per resident** with receipts, starting with the
   resident whose archive was evaluated.

## 6. What is implemented

| Piece | Where |
|---|---|
| Port and typed questions/answers | [ports/decision.py](../../src/ravn/ports/decision.py) |
| `/v1/systemone` adapter (Jev, Clef, Ollama), strict response validation | [adapters/decision/systemone.py](../../src/ravn/adapters/decision/systemone.py) |
| Telemetry wrapper applied to every configured adapter | [decision_observability.py](../../src/ravn/decision_observability.py) |
| Observe-only first opinion on resident turns other than home turns | [resident_first_opinion.py](../../src/ravn/resident_first_opinion.py), hooked in `DriveLoop._run_task_observed` |
| Observe-only opinion per inbox slot, taken when a home turn selects it, compared with the turn after | [resident_attention.py](../../src/ravn/resident_attention.py), called from `ResidentRuntime.next_home_task` / `handle_completed_turn` |
| §3.1 attention gate (built, **off**) | `ResidentSlotAttention.select` and `decision.resident_first_opinion.gate` |
| §5 step 2 offline calibration | `ravn resident attention-calibrate`, [resident_attention_calibration.py](../../src/ravn/resident_attention_calibration.py) |
| Learned-tool matching for `build_tool` and curation (`decision.capability_matching`, off by default): an embedding shortlist, then one Noul per candidate | `SystemOneCapabilityMatcher` in [capability_matching.py](../../src/ravn/adapters/reflection/capability_matching.py), `_build_capability_matcher` in [runtime_builders.py](../../src/ravn/cli/runtime_builders.py) |
| Evolution headline (`decision.evolution_headline`, off by default): code writes one true sentence per change and a Choice picks one | [evolution_report.py](../../src/ravn/valkyrie_evolution/evolution_report.py) |
| Config (`decision:` in ravn `Settings`) and composition | `DecisionConfig` in [config.py](../../src/ravn/config.py), `build_decision_port` in [runtime_builders.py](../../src/ravn/cli/runtime_builders.py) |

### Turning it on

```yaml
decision:
  enabled: true
  # adapter defaults to ravn.adapters.decision.systemone.SystemOneDecisionAdapter;
  # any class implementing DecisionPort can be named here instead.
  kwargs:
    base_url: https://api.typesafe.ai   # or http://ollama:11434 for local Clef
    model: jev-latest                   # or clef-flash / clef / nimble
  secret_kwargs_env:
    api_key: TYPESAFE_API_KEY           # omit for keyless local servers
  resident_first_opinion:
    enabled: true
  capability_matching:
    enabled: true
    min_same_probability: 0.6           # the default
    peer_min_same_probability: 0.6      # adopt a flock peer's tool instead of building
    peer_prior_art_probability: 0.4     # hand a close peer tool to the builder
```

All of these are startup errors rather than silent degradation: a mapped
secret that is unset, an adapter that cannot be built, and
`resident_first_opinion` enabled while `decision` is off or no resident runtime
exists. `capability_matching` also fails startup while `decision` is off, or
without `reflection.matching.mode: semantic` and embeddings, because it keeps
the embedding shortlist. While running, a failed decision request fails that
resident task before its model turn starts, with the error on the
`ravn.decision` span. A failed decision about learned tools fails that build or
curation pass instead, and nothing is built or retired.

### Per slot, not per turn

A home turn covers up to `resident_inbox.max_signals_per_wake` slots. Judging
its whole prompt is the distractor-laden state §1 warns about, so home turns are
judged one slot at a time, when the slot is selected. The state is the slot
itself (count, span, ranges and distinct values computed in code, newest
payload), the charter and personality, and the resident's last working state.
Options are ordered wake-first (`urgent` and `escalate` first), so the
first-option bias leans toward attention.

The turn reports one tier and decision for all its slots, so the comparison
label is conservative: a routine conclusion (`silent`/`ambient` with
`ignore`/`watch`) means every slot in that turn could have been skipped, and any
other conclusion means every slot needed the turn. Errors in that labelling
only ever push toward waking.

A decision request that fails raises. In a home wake that ends the resident
process, the same as its own model failing; there is no path that carries on
without the opinion.

### The gate (off until calibrated)

`skip_probability` is the model's probability mass on the routine side of all
three questions at once: routine tier, routine decision, and no investigation
needed. With `gate.enabled`, an eligible slot at or above
`gate.min_skip_probability` gets no turn of its own. It stays **pending** and
appears in a "since you last thought" receipt (count, span, ranges, the
opinion) on the next turn that runs, which acknowledges it. Nothing is
acknowledged without a turn having seen it, and a wholly routine inbox yields no
home turn while stewardship wakes still fire. Never eligible: critical
observations (including any folded into the slot), operator messages, A2A
updates, `-novel` slots, and slots whose severity is high-cardinality.
`min_skip_probability` has no default; enabling the gate without it is a startup
error.

### Calibrating (§5 step 2)

Run inside the resident's pod, so it reads the live inbox and state through the
daemon's own builders:

```bash
ravn resident attention-calibrate --out /tmp/attention.json
```

Each judged slot in `processed/` names the turn that acknowledged it. The
command replays that slot through the same state the live opinion uses, with
the working state the resident held before that turn. At every skip
probability present in the data, it reports coverage (eligible slots skipped)
and misses (skipped slots whose turn was not routine), plus the lowest
zero-miss threshold. It spends one decision request per slot. Run it once with
Jev and once with a Clef config to compare them on our own data.

### What you can see

Per request, for every purpose:

- Span `ravn.decision`, carrying the purpose, model, question count, input
  tokens and any error.
- Metrics `ravn.decision.requests`, `ravn.decision.failures` and
  `ravn.decision.input_tokens`, plus the `ravn.decision.latency` histogram.
- Per answer: `ravn.decision.answers`, counted by question and chosen option;
  the `ravn.decision.confidence`, `ravn.decision.probability` and
  `ravn.decision.score` histograms; and a `ravn.decision.answer` span event
  with the full distribution.
- One INFO log line per request, for example
  `decision[resident.first_opinion] model=jev-1.14.0 212ms input_tokens=1583 tier=ambient(0.81) decision=watch(0.64) needs_investigation=0.22`,
  and one ERROR line per failed request
  (`decision[resident.first_opinion] failed 30012ms error=ReadTimeout: …`).
  These lines are the exact per-event record, and the Resident Decisions
  dashboard counts from them. A resident makes about one decision an hour and
  restarts on every rollout, so each counter reaches only 1–2 per pod.
  Prometheus `increase()` drops a new series' first sample, and misses a
  restart whose counter climbs back to the same value. Counts taken from the
  metrics therefore under-report.

For the resident first opinion specifically:

- `ravn.decision.first_opinion.comparisons`, counted by question, first
  opinion, the resident's answer and `ravn.decision.agreed`. This is the
  agreement matrix §5 step 2 needs.
- `ravn.decision.first_opinion.tier_delta`: the first opinion's tier minus the
  resident's. A negative value means the cheap model under-attended, which is
  the direction that matters before any gating.
- A `ravn.decision.first_opinion` span event holding both full judgments.

When the resident's own outcome is missing or invalid, its answer is recorded
as `unavailable` with no `agreed` attribute, so a broken turn never counts as
a disagreement.

Per slot:

- `ravn.decision.slot_opinion.comparisons`, counted by question, first
  opinion, resident answer, `agreed`, `ravn.decision.turn_needed`,
  `ravn.decision.gate.eligible`, and `ravn.decision.gate.would_skip` once a
  threshold is set.
- `ravn.decision.slot_opinion.skip_probability`, a histogram by
  `turn_needed`: the live calibration curve.
- A `ravn.decision.slot_opinion` span event per slot with the full opinion.
- With the gate on, `ravn.decision.attention_gate.slots` by `turn` or
  `receipt`.

## Sources

- TypeSafe docs: [models](https://docs.typesafe.ai/models.md), [API](https://docs.typesafe.ai/api/system-one.md),
  [Jev 1.13 jaggedness](https://docs.typesafe.ai/model-jaggedness/jev-1.13.md),
  [confidence routing](https://docs.typesafe.ai/patterns/confidence-routing.md),
  [skill suggestion](https://docs.typesafe.ai/cookbooks/skill_suggestion.md),
  [re-ranking](https://docs.typesafe.ai/cookbooks/rerank_typesafe.md)
- [Cloudflare/clef model card](https://huggingface.co/Cloudflare/clef)
- [The Register: Cloudflare's open-weight Clef models](https://www.theregister.com/ai-and-ml/2026/10/01/cloudflare-tries-to-outplay-jev-with-open-weight-clef-models/5300649)
- [clef-flash-local (16 GB GPU server)](https://github.com/turlockmike/clef-flash-local)
- [openjev](https://github.com/razorback16/openjev)
- [Local decision models on Ollama](https://llmconfigurator.com/en/guides/decision-models)
