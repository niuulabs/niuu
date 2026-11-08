# Limits shared across services

The limits that the shared helpers and the services outside Forge enforce, what
actually bounds each resource, and what happens when the limit is reached.
Forge's own limits are in [forge-limits.md](forge-limits.md). These follow
`.claude/rules/no-hidden-limits.md`: a cap that remains has a named real
bound, and a hit is loud and counted.

A reasoning model spends part of any output cap thinking, so a cap guessed for
a model that answers without reasoning ends in the middle of its thinking, the
empty or half answer is read as "nothing to do", and nothing counts it. The
model calls below therefore send no cap.

## How a hit is surfaced

`niuu.cap_telemetry` (`src/niuu/cap_telemetry.py`) is the only implementation.

- A model answer cut off by a cap (`stop_reason` `max_tokens`) **raises**
  `OutputTruncatedError` from `require_complete()`, naming the knob and the
  remedy. A truncated tool call is never run.
- A character limit that cuts content which is carried forward **logs at error
  level** (`cap hit [<knob>]: ...`) through `record_truncation()` /
  `record_cap_hit()` and is still applied. Silently dropping the rest of the
  content was the actual problem.
- Every hit increments the `ravn.cap.hits` counter (attribute `ravn.cap.knob`;
  in Prometheus, `ravn_cap_hits_total`). `niuu.cap_telemetry.health_counts()`
  shapes the process counts for a component's health surface: `cap_hits_total`
  plus one `cap_hits.<knob>` per limit that was hit.
- Where raising would lose more than continuing, `record_degraded()` writes an
  error log and counts `ravn.degraded` (attribute `ravn.degraded.path`); the
  health counts carry `degraded_total` / `degraded.<path>`. An installed
  learned tool that the execution backend refuses to load
  (`learned_tool.refused`, once per tool per process) is not offered to the
  model.
- A retry still inside its bound logs at warning and counts on `ravn.retries`
  (attribute `ravn.retry.path`) through `record_retry()`. Running out of the
  bound is a cap hit on its knob, not a retry.

The counter names keep their `ravn.` prefix so existing dashboards and alerts
continue to read them.

## Limits

"Real bound" is what limits the resource whether or not a cap is set.

| Cap | Default | Real bound | Decision | How a hit is surfaced |
|---|---|---|---|---|
| Tool-build gate review / question answer (`tool_build.gate_review` / `tool_build.question_answer`) | none, was hard-coded 400 / 700 | Model max output / remaining context window | Removed: no `max_tokens` is sent | Raises `OutputTruncatedError`; counted |
| Tool-build gate review attempts | 2 | An unparseable verdict is worth one retry | Kept: one retry on an unparseable answer, never on truncation | Raises `ToolBuildError` after the last attempt |
| A2A task result (`result_max_chars` of the A2A gateway kwargs) | 12000 | The consumer's prompt budget | Kept; bounds `a2a_task` results including `find`, whose handles leave out the case mandate. The character budget is the bound, not an item count | Marker in the result + error log + counted |
| A2A outbound message (`message_max_chars` of the A2A gateway kwargs) | 12000 | The peer's request limit | Kept | The call is refused with an error naming the limit |
| Bifröst inbound defaults (chat completions 1024, Responses 4096, Ollama 1024) | none | Model max output / remaining window | **Removed**: an uncapped request stays uncapped. Bifröst's Anthropic upstream derives the cap from the Models API; a `max_tokens` routing rule treats uncapped as unbounded | `ProviderError` if the limit cannot be read |
| Skuld Telegram `TELEGRAM_MAX_MESSAGE_LENGTH` | 4096 | Telegram: 4096 UTF-16 units after entity parsing | Measured in UTF-16 units; HTML chunks never split inside a tag or entity, and open tags are closed and reopened across a split | A failed chunk logs at error, and the event is not marked delivered, so a redelivery resends it |
| Learned-tool Kubernetes Job (`job_pod_start_timeout_seconds` of the `k8s_job` backend) | 120 s | How long a node takes to start a pod | Kept: a wall-clock deadline on one run | The run fails as an infrastructure error (`LearnedToolInfrastructureError`) rather than as a failing tool |

## Remaining fallbacks

These broad `except` handlers log at warning or below and continue around
stateful work. They are tracked as follow-up:

- Skuld: `ChannelRegistry.broadcast` removes a channel for good on one send
  error; the directed-message WebSocket send to a resident returns `False`
  with only a warning (`collaboration_adapter.py`); room-event and
  `ravn.help.needed` publishes log at warning.
