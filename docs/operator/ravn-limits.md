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
  model call: agent turns, outcome repair, compaction, entity extraction,
  the thread enricher, gate review, and the tool builder's question answer. A truncated tool call is
  never run.
- A character limit that cuts content a resident carries forward **logs at
  error level** (`cap hit [<knob>]: …`) and is still applied. Silently
  dropping the rest of the content was the actual problem.
- Every hit increments the `ravn.cap.hits` counter (attribute `ravn.cap.knob`;
  in Prometheus, `ravn_cap_hits_total`). `ravn.cap_telemetry.health_counts()`
  shapes the process counts for a component's health surface:
  `cap_hits_total` plus one `cap_hits.<knob>` per limit that was hit.
- Where raising would lose more than continuing, the degradation is an error
  log plus `ravn.degraded` (attribute `ravn.degraded.path`), and appears in
  the health counts as `degraded_total` / `degraded.<path>`. An installed
  learned tool that the execution backend refuses to load
  (`learned_tool.refused`, once per tool per process) is not offered to the
  model; `capability_list` names it under `unavailable_learned_tools`.
- A retry still inside its bound (a stream re-issued after a gateway restart, a
  model call retried after a refused connection) logs at warning and counts on
  `ravn.retries` (attribute `ravn.retry.path`); the health snapshot carries
  `retries_total` and `retries.<path>`. Running out of the bound is a cap hit
  on its knob, not a retry.
- `ravn.cap_telemetry` is the only implementation: `require_complete()` for
  model answers, `record_truncation()` / `record_cap_hit()` for everything else.

This follows `.claude/rules/no-hidden-limits.md`: every truncation is marked
in the output and counted. That includes excerpts the prompt announces
("first 3000 chars").

## Limits

"Real bound" is what limits the resource whether or not Ravn sets a cap.

| Cap | Default | Real bound | Decision | How a hit is surfaced |
|---|---|---|---|---|
| **Model output** | | | | |
| `llm.max_tokens` (agent turns, outcome repair; persona `llm.max_tokens` overrides) | `null`, was 8192 | Model max output / remaining context window; `llm.timeout` bounds time | Optional; `null` sends no cap. OpenAI-compatible and Responses omit the parameter. Anthropic reads the model's `max_tokens` from `GET /v1/models/{id}` (cached per model). Bifröst adapter omits it and Bifröst derives it upstream | Raises `OutputTruncatedError`; counted |
| `memory.reflection_max_tokens` (episode reflection, `ravn_reflect`) | `null`, was 4096 / 512 | Same | Removed | Raises; `ravn_reflect` returns a failed tool call; counted |
| `context_management.compression_max_tokens` | `null`, was 1024 | Same | Removed. A cut-off state document never replaces history | `ContextCompressionError`; counted |
| `mimir.ingest.entity_max_tokens` | `null`, was 1024 | Same | Removed | Ingest raises (it used to return no entities); counted |
| Thread-enricher classification | none, was hard-coded 256 | Same | Removed | Raises; sweep not advanced, retried; knob `thread.enricher` |
| Tool-build gate review / question answer | none, was hard-coded 400 / 700 | Same | Removed | Raises `OutputTruncatedError`; knobs `tool_build.gate_review` / `tool_build.question_answer` |
| `mimir.source_trigger.max_tokens`, `mimir.staleness_trigger.max_tokens`, `buri.session_summary_max_tokens`, `AgentTask.max_tokens` | 4096 / 4096 / 400 | — | **Removed**: nothing ever applied them | — |
| `extended_thinking.budget_tokens` | 8000 | Provider (legacy Anthropic models only; adaptive models ignore it) | Kept: an operator-chosen thinking budget, not an answer cap | Provider 400 if invalid |
| Bifröst inbound defaults (chat completions 1024, Responses 4096, Ollama 1024) | none | Model max output / remaining window | **Removed**: an uncapped request stays uncapped. Bifröst's Anthropic upstream derives the cap from the Models API; a `max_tokens` routing rule treats uncapped as unbounded | `ProviderError` if the limit cannot be read |
| **Prompt size** | | | | |
| `context_management.max_prompt_tokens` | 0 = derived from the window | Model context window | Kept as the hard ceiling only. Unset, it is the window minus `llm.max_tokens`, or the whole window when the answer is uncapped (an answer that then does not fit is cut off and raises). Residents used to set 48000 here as a prefill-latency target; that cut tool results to fit, so it moved to `compaction_target_tokens` | `PromptBudgetExceededError` (existing) |
| `context_management.compaction_target_tokens` | 0 = the ceiling | Prefill latency the operator accepts (self-hosted TTFT grows with prompt size) | New: compaction fires at `compression_threshold` × this. It only summarises older history; it never cuts a tool result or refuses a turn | — (not a cap) |
| `context_management.context_window_tokens` | 0 (unknown) | The model's window | Kept: the real bound the ceiling derives from | Startup error when inconsistent |
| `tools.max_result_chars` and the ceiling-derived tool-result limit | 100000 | Prompt ceiling (the window) | Kept. The derived limit now comes from the window, not the latency target, so a result is cut only where it alone would not fit. The model sees a marker and is told to narrow the query | Error log + counted (knob `tools.max_result_chars`, or `context_management.max_prompt_tokens` when the ceiling is what cut) |
| `initiative.task_queue_max` | was 50 | Nothing: a full queue discarded the new task with an info line (124 dropped on one resident in eight hours) | **Removed**. Nothing is dropped for room; a backlog shows as queue depth and each queued task's age. Home turns run at priority 1 so a signal backlog cannot starve operator messages and A2A updates. A config that sets the key is refused at load | Queue depth gauge, HUD |
| `budget.daily_cap_usd` | 1.0 | Operator's spend limit | Kept: the real bound on autonomous work | Error log + counted (it used to be an info log); the task waits for the UTC day to roll over |
| **Tool output** | | | | |
| `tools.bash.max_output_bytes` | 100 KiB | Prompt budget | Kept; output carries a notice | Error log + counted |
| `tools.web.fetch.content_budget` | 20000 | Prompt budget | Kept; output carries a notice | Error log + counted |
| `tools.kubernetes.max_configmap_value_chars` / `max_list_items` | 4000 / 100 | Prompt budget | Kept; `truncated_keys` / `truncated` in the result | Error log + counted |
| `gateway.platform.a2a_result_max_chars` | 12000 | Prompt budget | Kept; bounds `a2a_task` results including `find`, whose handles leave out the case mandate; the hard-coded 16-item list limit is removed (the character budget is the bound) | Marker + error log + counted |
| `mimir.source_trigger.max_content_chars` | 120000 | Synthesis prompt budget | Kept; context carries a notice | Error log + counted (synthesis worked from part of the source) |
| Thread-enricher page excerpt | was hard-coded 500 | Classifier model's window | **Removed**: the whole page is classified; the head alone misread pages by their preamble | Context errors raise |
| `mimir.ingest.entity_source_excerpt_chars` | 3000, was hard-coded | Extraction model's window (raw sources reach megabytes) | Made configurable; the prompt says "first N chars" | Error log + counted |
| **Delivery** | | | | |
| `gateway.channels.<telegram/discord/slack/matrix/whatsapp>.message_max_chars` | 4096 / 2000 / 3000 / 32000 / 4096 | Platform message limit | **Split** into several messages instead of cut with "..." | Nothing lost; Telegram 4xx now logged |
| Discord image caption | same knob | Platform limit | Kept | Error log + counted |
| Skuld Telegram `TELEGRAM_MAX_MESSAGE_LENGTH` | 4096 | Telegram: 4096 UTF-16 units after entity parsing | Measured in UTF-16 units; HTML chunks never split inside a tag or entity, and open tags are closed and reopened across a split | A failed chunk logs at error, and the event is not marked delivered, so a redelivery resends it |
| **Retries and time** | | | | |
| Skuld `workflow.gate_consumer_wait_s` (a gate decision waiting for the flock to reconnect after a restart) | 20 s | How long a caller holds the request open | Kept: the mesh keeps nothing for late subscribers, so publishing early would lose the decision | The decision fails with a 503 naming the knob and the gate stays pending; counted on `skuld.workflow.gate_consumer_wait.hits` |
| `llm.retry_base_delay` / `llm.timeout` | 1.0 s / 120 s | Backoff start; `timeout` is the bound for an uncapped answer | Kept | Retries are bounded by `llm.gateway_outage_window_seconds` below |
| `llm.gateway_outage_window_seconds` / `llm.gateway_outage_max_backoff_seconds` | 120 s / 10 s | How long a gateway rollout takes: the time one call may spend failing, from each failure until the gateway answers again (a response header, or the first event of a re-sent stream) | **Replaces** `llm.max_retries` (3 attempts, about 7 s) and the short-lived `llm.stream_resume_*`: any transient failure of a model call is retried under this one wall-clock bound, before the response header or mid-answer: refused or reset connection, 429/500/502/503/504, a stream cut (`RemoteProtocolError`, `ReadError`) or ended without its terminator (`message_stop`, `[DONE]`, `response.completed`). A cut answer is discarded (`STREAM_RESTART`), so a tool call never runs twice. `0` disables retrying. Other 4xx, truncation and malformed answers are never retried. Setting `llm.max_retries` or `llm.stream_resume_*` is refused at config load, naming the replacement | `LLMError` naming `llm.gateway_outage_window_seconds`; counted on `ravn.cap.hits`; each retry is a warning counted on `ravn.retries` (`retries.llm.<provider>`) |
| `agent.max_iterations` / `iteration_budget.total` | 20 / 90 | `budget.daily_cap_usd` bounds spend, but not how long one task holds its slot | Kept: a turn whose tool loop does not converge must end so the queue moves; `0` (max_iterations) is no cap | `MaxIterationsError` (checkpointed); counted |
| Gate review 2 attempts | 2 | — | Kept: one retry on an unparseable answer, never on truncation | Raises after the last attempt |
| `present_file.timeout_s` (a flock member's `present_file`, answered by the session broker) | 60 | The broker's staging copy plus its inline durable upload (Skuld `presented_durable.inline_upload_timeout_seconds`) | Kept as a wall-clock deadline on one mesh request; the broker answers a redelivered request once | The tool fails naming the knob (the file may still appear); counted (knob `present_file.timeout_s`) in the member's daemon, also when a CLI agent (Codex, Claude Code) presents through the daemon's relay |

## Remaining fallbacks (not changed in this sweep)

The inventory also found broad `except` handlers that log at warning or below
and continue around stateful work. The ones next to model calls and state
writes in the files above were removed: thread enricher, entity
extractor, and their state files. These remain and are tracked as follow-up:

- `ravn/drive_loop.py`: schema/outcome/artifact repair model calls, thread
  finalise, mesh and Sleipnir publishes. (The queue journal no longer falls
  back: an unreadable journal or entry stops startup naming the remedy, and a
  failed write is counted as degraded `queue_journal.persist`.)
- `ravn/agent.py`: checkpoint and snapshot saves (`1315`, `1405`), inline-fact
  memory write (`567`), outcome-block parse (`2226`).
- `ravn/adapters/triggers/condition_poll.py:74`: a failed sensor turn returns
  `"CLEAR"`.
- `ravn/adapters/triggers/cron.py:397`: an unparseable jobs file loads as no
  jobs, and the next save overwrites it.
- `ravn/adapters/tools/cascade_tools.py:699`: a failed remote fetch can report
  `{"status": "complete", "output": ""}`.
- `ravn/adapters/memory/inline_facts.py:159/173`: dropped Mímir fact upserts.
- Skuld: `ChannelRegistry.broadcast` removes a channel for good on one send
  error; the directed-message WebSocket send to a resident returns `False`
  with only a warning (`collaboration_adapter.py:641`); room-event and
  `ravn.help.needed` publishes log at warning.
- Hard-coded excerpts still without a config field: the agent's compact
  reflection (`errors[:5]`, `past_context[:800]`) and `mimir_query`'s
  2000-character page excerpt (no marker).
