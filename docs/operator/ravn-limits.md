# Ravn limits

Every limit Ravn enforces on a resident, what actually bounds that resource,
and what happens when the limit is reached. This replaces caps that were
guessed for older non-reasoning models: a reasoning model spends part of any
output cap thinking, so 256, 512 or 1024 tokens ended in the middle of its
thinking, the empty or half answer was read as "nothing to do", and nothing
counted it.

## How a hit is surfaced

- A model answer cut off by a cap (`StopReason.MAX_TOKENS`, Responses
  `incomplete: max_output_tokens`, Anthropic `max_tokens` or
  `model_context_window_exceeded`) **raises** `OutputTruncatedError`, a
  subclass of `LLMError`, naming the knob and the remedy. That holds for every
  model call: agent turns, outcome repair, reflection, the lesson judge,
  compaction, entity extraction, wakefulness, the thread enricher, gate
  review, and the tool builder's question answer. A truncated tool call is
  never run.
- A character limit that cuts content a resident carries forward **logs at
  error level** (`cap hit [<knob>]: …`) and is still applied. Silently
  dropping the rest of the content was the actual problem.
- Every hit increments the `ravn.cap.hits` counter (attribute `ravn.cap.knob`;
  in Prometheus, `ravn_cap_hits_total`). The resident health snapshot (the
  HUD's `health` block, `ResidentRuntime.health_snapshot()`) carries
  `cap_hits_total` plus one `cap_hits.<knob>` per limit that was hit since the
  resident started. The `ravn.resident.cap_hits` gauge restates the total next
  to the other scorecard gauges.
- Where raising would lose more than continuing, the degradation is an error
  log plus `ravn.degraded` (attribute `ravn.degraded.path`), and appears in
  the health snapshot as `degraded_total` / `degraded.<path>` with the
  `ravn.resident.degraded` gauge. That covers three paths. An operator reply
  that could not be saved as an answer (`operator_reply.persist`) is
  delivered as an ordinary task, and its question stays open. A reply naming
  a closed question (`operator_reply.unbound`) and a directed message not
  written to the durable inbox (`directed_message.persist`) are also counted.
- `ravn.cap_telemetry` is the only implementation: `require_complete()` for
  model answers, `record_truncation()` / `record_cap_hit()` for everything else.

This follows `.claude/rules/no-hidden-limits.md`: every truncation is marked
in the output and counted. That includes excerpts the prompt announces
("first 3000 chars", a raw payload labelled "bounded" next to its archive
ref).

## Limits

"Real bound" is what limits the resource whether or not Ravn sets a cap.

| Cap | Default | Real bound | Decision | How a hit is surfaced |
|---|---|---|---|---|
| **Model output** | | | | |
| `llm.max_tokens` (agent turns, outcome repair; persona `llm.max_tokens` overrides) | `null`, was 8192 | Model max output / remaining context window; `llm.timeout` bounds time | Optional; `null` sends no cap. OpenAI-compatible and Responses omit the parameter. Anthropic reads the model's `max_tokens` from `GET /v1/models/{id}` (cached per model). Bifröst adapter omits it and Bifröst derives it upstream | Raises `OutputTruncatedError`; counted |
| `memory.reflection_max_tokens` (episode reflection, `ravn_reflect`) | `null`, was 4096 / 512 | Same | Removed | Raises; `ravn_reflect` returns a failed tool call; counted |
| `reflection.max_tokens` (post-session reflection) | `null`, was 1024 | Same | Removed | `ReflectionError`; counted |
| `reflection.matching.judge_max_tokens` | `null`, was 4096 | Same | Removed | `LessonJudgeError`; counted |
| `context_management.compression_max_tokens` | `null`, was 1024 | Same | Removed. A cut-off state document never replaces history | `ContextCompressionError`; counted |
| `mimir.ingest.entity_max_tokens` | `null`, was 1024 | Same | Removed | Ingest raises (it used to return no entities); counted |
| Wakefulness intent call | none, was hard-coded 512 | Same | Removed | Raises; poll loop logs at error and retries; knob `wakefulness` |
| Thread-enricher classification | none, was hard-coded 256 | Same | Removed | Raises; sweep not advanced, retried; knob `thread.enricher` |
| Tool-build gate review / question answer | none, was hard-coded 400 / 700 | Same | Removed | Raises `OutputTruncatedError`; knobs `tool_build.gate_review` / `tool_build.question_answer` |
| `mimir.source_trigger.max_tokens`, `mimir.staleness_trigger.max_tokens`, `buri.session_summary_max_tokens`, `AgentTask.max_tokens` | 4096 / 4096 / 400 | — | **Removed**: nothing ever applied them | — |
| `extended_thinking.budget_tokens` | 8000 | Provider (legacy Anthropic models only; adaptive models ignore it) | Kept: an operator-chosen thinking budget, not an answer cap | Provider 400 if invalid |
| Bifröst inbound defaults (chat completions 1024, Responses 4096, Ollama 1024) | none | Model max output / remaining window | **Removed**: an uncapped request stays uncapped. Bifröst's Anthropic upstream derives the cap from the Models API; a `max_tokens` routing rule treats uncapped as unbounded | `ProviderError` if the limit cannot be read |
| **Prompt size** | | | | |
| `context_management.max_prompt_tokens` | 0 (off) | Model context window | Kept: the hard prompt budget. With `llm.max_tokens: null` and `context_window_tokens` set, it is **required** (the window no longer implies the prompt's share): config load fails without it | `PromptBudgetExceededError` (existing) |
| `context_management.context_window_tokens` | 0 (unknown) | The model's window | Kept. Prompt budget = window − `llm.max_tokens` only when a cap is set | Startup error when inconsistent |
| `tools.max_result_chars` and the prompt-budget-derived tool-result limit | 100000 | Prompt budget | Kept. The model sees a marker and is told to narrow the query | Error log + counted (knob `tools.max_result_chars`, or `context_management.max_prompt_tokens` when the budget is what cut) |
| Resident schema repair: previous response | was hard-coded 4000-char head | Repair call's prompt budget | **Removed**: the head cut off the outcome block at the end, which was the part being repaired | `PromptBudgetExceededError` if too large |
| `reflection.context_max_chars` (post-session recorded context) | 12000, was hard-coded | Reflection model's window | Made configurable | Error log + counted |
| **Resident continuation** | | | | |
| `resident_state.continuation_context_max_chars` (working state, A2A handles, open questions, turn handoff) | 12000 | Prompt budget | Kept | Error log + counted |
| `resident_state.continuation_tool_result_max_chars` | 2000 | Durable turn record size | Kept | Error log + counted |
| `resident_state.directed_message_context_max_chars` | 4000 | Prompt budget | Kept | Error log + counted |
| Mandate in turn, operator-needed and wake records | was hard-coded 4000 | Input that created it (already stored whole in the turn's prompt) | **Removed**: the mandate was read back shortened on resume | — |
| Operator question in the operator-needed record | was hard-coded 1000 | Notification body (`niuu.notifications.MAX_BODY_CHARS`, 4000) | Bound by the notification limit instead | Error log + counted |
| `resident_state.continuation_max_turns` / `continuation_max_tokens` | 3 / 0 | `budget.daily_cap_usd` (on by default; flat pricing counts self-hosted tokens too), one turn per scheduled wake, `repeated_decision_escalate_after` | **Removed**. The turn cap stopped a case on the turn it asked the operator a question (fixed separately in `272116c4d`) and still cut scheduled rechecks short. A config that sets either key is refused at load. The unused `ResidentRunBudget` is removed too | — |
| `budget.daily_cap_usd` | 1.0 | Operator's spend limit | Kept: the real bound on autonomous work | Error log + counted (it used to be an info log); the task waits for the UTC day to roll over |
| `repeated_decision_escalate_after` | 5 | — | Kept: escalates a loop to the operator rather than stopping it | Operator question; `repeated_decision_streak` on the health snapshot |
| **Tool output** | | | | |
| `tools.bash.max_output_bytes` | 100 KiB | Prompt budget | Kept; output carries a notice | Error log + counted |
| `tools.web.fetch.content_budget` | 20000 | Prompt budget | Kept; output carries a notice | Error log + counted |
| `tools.kubernetes.max_configmap_value_chars` / `max_list_items` | 4000 / 100 | Prompt budget | Kept; `truncated_keys` / `truncated` in the result | Error log + counted |
| `gateway.platform.a2a_result_max_chars` | 12000 | Prompt budget | Kept; the hard-coded 16-item list limit is removed (the character budget is the bound) | Marker + error log + counted |
| `mimir.source_trigger.max_content_chars` | 120000 | Synthesis prompt budget | Kept; context carries a notice | Error log + counted (synthesis worked from part of the source) |
| Thread-enricher page excerpt | was hard-coded 500 | Classifier model's window | **Removed**: the whole page is classified; the head alone misread pages by their preamble | Context errors raise |
| `mimir.ingest.entity_source_excerpt_chars` | 3000, was hard-coded | Extraction model's window (raw sources reach megabytes) | Made configurable; the prompt says "first N chars" | Error log + counted |
| Resident home-turn raw payload excerpts | derived from `resident_state.continuation_context_max_chars` per observation | Prompt budget | Kept; labelled "bounded", with the archive ref | Error log + counted |
| **Delivery** | | | | |
| `gateway.channels.<telegram/discord/slack/matrix/whatsapp>.message_max_chars` | 4096 / 2000 / 3000 / 32000 / 4096 | Platform message limit | **Split** into several messages instead of cut with "..." | Nothing lost; Telegram 4xx now logged |
| Discord image caption | same knob | Platform limit | Kept | Error log + counted |
| Skuld Telegram `TELEGRAM_MAX_MESSAGE_LENGTH` | 4096 | Telegram: 4096 UTF-16 units after entity parsing | Measured in UTF-16 units; HTML chunks never split inside a tag or entity, and open tags are closed and reopened across a split | A failed chunk logs at error, and the event is not marked delivered, so a redelivery resends it |
| **Retries and time** | | | | |
| `llm.max_retries` / `llm.retry_base_delay` / `llm.timeout` | 3 / 1.0 s / 120 s | Provider availability | Kept; `timeout` is the bound for an uncapped answer | `LLMError` after the retries |
| `agent.max_iterations` / `iteration_budget.total` | 20 / 90 | `budget.daily_cap_usd` bounds spend, but not how long one task holds its slot | Kept: a turn whose tool loop does not converge must end so the queue moves; `0` (max_iterations) is no cap | `MaxIterationsError` (checkpointed); counted |
| `reflection.matching.judge_attempts`, post-session reflection 2 attempts, gate review 2 attempts | 2 | — | Kept: one retry on an unparseable answer, never on truncation | Raises after the last attempt |

## Remaining fallbacks (not changed in this sweep)

The inventory also found broad `except` handlers that log at warning or below
and continue around stateful work. The ones next to model calls and state
writes in the files above were removed: thread enricher, wakefulness, entity
extractor, and their state files. These remain and are tracked as follow-up:

- `ravn/drive_loop.py`: queue-journal persist/load (`5256`, `5321`, `5341`,
  `5391` start with an empty queue, `5515`), schema/outcome/artifact repair
  model calls (`3663`, `3753`, `3826`, `3888`), thread finalise (`5207`), mesh
  and Sleipnir publishes.
- `ravn/agent.py`: checkpoint and snapshot saves (`1315`, `1405`), inline-fact
  memory write (`567`), outcome-block parse (`2226`).
- `ravn/adapters/triggers/condition_poll.py:74`: a failed sensor turn returns
  `"CLEAR"`.
- `ravn/adapters/triggers/cron.py:397`: an unparseable jobs file loads as no
  jobs, and the next save overwrites it.
- `ravn/adapters/tools/cascade_tools.py:699`: a failed remote fetch can report
  `{"status": "complete", "output": ""}`.
- `ravn/adapters/memory/inline_facts.py:159/173`: dropped Mímir fact upserts.
- `ravn/valkyrie_evolution/resident_learning.py`: inventory publishes,
  artifact reads, superseded-version restore.
- Skuld: `ChannelRegistry.broadcast` removes a channel for good on one send
  error; the directed-message WebSocket send to a resident returns `False`
  with only a warning (`collaboration_adapter.py:641`); room-event and
  `ravn.help.needed` publishes log at warning.
- Hard-coded excerpts still without a config field: the agent's compact
  reflection (`errors[:5]`, `past_context[:800]`) and `mimir_query`'s
  2000-character page excerpt (no marker).
