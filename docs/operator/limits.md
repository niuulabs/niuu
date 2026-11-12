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
| Bifröst Forge session confirmation (`forge_session.timeout_seconds`) | 10 s | How long Forge takes to answer one session lookup | Kept: a wall-clock deadline on one call; nothing is cached, so a stopped session loses access with its next request | The model call is refused 503 naming Forge and the cause; it is never admitted unconfirmed |
| Skuld Telegram `TELEGRAM_MAX_MESSAGE_LENGTH` | 4096 | Telegram: 4096 UTF-16 units after entity parsing | Measured in UTF-16 units; HTML chunks never split inside a tag or entity, and open tags are closed and reopened across a split | A failed chunk logs at error, and the event is not marked delivered, so a redelivery resends it |
| Learned-tool Kubernetes Job (`job_pod_start_timeout_seconds` of the `k8s_job` backend) | 120 s | How long a node takes to start a pod | Kept: a wall-clock deadline on one run | The run fails as an infrastructure error (`LearnedToolInfrastructureError`) rather than as a failing tool |
| Skuld delivery attempt (`delivery.attempt_timeout_seconds`) | 10 s | How long a transport takes to accept input; a wedged send lock never does | Kept for transports whose send returns on acceptance. **Not applied** to a transport that declares `capabilities.send_runs_turn` (SDK, `claude -p`): its send returns when the turn is over, so the transport's own turn timeout bounds it | Retried up to `delivery.max_attempts`, then the user turn is marked failed and `user_delivery_failed` is sent |
| Skuld peer inbox (messages from other agents) | none | Nothing bounds a conversation between agents; a cap would drop or refuse work | Loops are made visible, not capped: the `skuld.peer.messages` counter (sender, recipient, channel, outcome) and per-pair counts in the session's activity metadata (`peer_messages`) | A message Forge does not confirm in `forge_mcp.notify_confirm_timeout_s` is reported to the sender as not confirmed and possibly delivered (Forge `pending`, mesh `unconfirmed`), naming that knob and telling it to retry with the same `request_id`; a turn the harness never accepts counts on `ravn.degraded` (`skuld.peer_inbox.delivery`) and its messages wait for the next turn boundary; an inbox the session could not restore from its log refuses peers with the reason and counts on `ravn.degraded` (`skuld.peer_inbox.restore`) |
| Skuld ask wait (`ask.wait_max_s`, also the ceiling for the `deadline_s` a caller passes) | 50 s | How long the engine lets one MCP tool call run: Codex `tool_timeout_sec` (default 60 s, set by Skuld from `forge_mcp.tool_timeout_s` = 120 s), Skuld's stdio server wait on the broker (`forge_mcp.broker_request_timeout_s` = 60 s), and Claude Code's `MCP_TOOL_TIMEOUT` (about 28 h) / 30 min idle for stdio; Skuld sets the Forge server's Claude Code per-server `timeout` from `forge_mcp.tool_timeout_s` too, so both engines are bounded by config. Startup fails unless it is shorter than the broker and tool timeouts. The same ceiling bounds an ask over the mesh | Kept: a wall-clock deadline on one call. A slow recipient is waited for by calling `ask` again with the same `request_id`, never by a longer call | A larger `deadline_s` is refused naming the knob. A wait that ends at the deadline returns `deadline`, `not_started` or `delivery_unknown` (`isError`, never an empty answer); each counts on `skuld.ask.outcomes`, and on `ravn.cap.hits` (`ask.wait_max_s`) when the wait ran to the ceiling. The message stays sent and a later answer is delivered at the turn boundary |
| Mesh certificate lifetime (`forge_mcp.mesh_identity.ttl_seconds`, Forge) | 900 s | How long a leaked or stale certificate may be believed: peers renew in the background at half of it, and a stopped session is also revoked at once by a signed event | Kept: a security bound, not a throughput one | A certificate that expired before renewal succeeded makes signing raise and peers refuse the sender (`niuu.mesh.attestation.refusals{reason="cert_expired"}`); a failed renewal counts on `ravn.retries` / `ravn.degraded` (`mesh.attestation.renew`) |
| Slack section and message text (`slack.api.section_text_max_chars` / `slack.api.message_text_max_chars`, the team Slack sink) | 3000 / 40000 characters | Slack API: a section block's text is at most 3000 characters, a message's `text` at most 40000 | Kept: limits Slack enforces | The text ends with a visible "…", the hit logs at error and counts on `ravn.cap.hits`; the message still goes out |
| Members per OpenShell unit (`MAX_PEER_WORKLOADS` of the OpenShell fork) | 16 | The fork admits at most 16 peer workloads per sandbox, and a workflow run's members are its peer workloads | Kept: a bound of the runtime, not a throughput one | A run with more members is refused at launch, before anything is created, with an error naming the bound and the way out (fewer members, or a runtime without it); the refusal counts on `ravn.cap.hits` (`openshell.max_peer_workloads`) |
| Restart of one member of a unit (`pod_manager.kwargs.hosted_member_restart_wait_seconds`, Flux, direct Kubernetes and OpenShell) | 180 s | How long a changed credential Secret takes to reach a running container (the kubelet's sync period plus its cache), and the member's Skuld to come back ready | Kept: asked again every `hosted_member_poll_seconds` until then | Forge's restart of the member raises, naming the knob and saying the member is not restarted; the hit counts on `ravn.cap.hits`; the member's row is put back as it was |
| Workflow step deadline (`workflow_steps.step_deadline_seconds`, Ting) | 14400 s | The longest single agent step an operator tolerates without a close; a node may override it with `deadlineSeconds` | Kept: stamped on every offer and pickup | An active step past it is flagged overdue for an operator; an offer past it with no pickup fails the step naming the knob |
| Workflow offer pickup grace (`workflow_steps.pickup_grace_seconds`, Ting) | 300 s | How long a member's Skuld takes to receive an offer on the mesh and report its pickup, which is seconds | Not a cap: it is when to look again, and the offer is published again for as long as nobody takes it, until the step deadline fails it | Flagged `unclaimed` and a person is told once; each republish is counted |
| Workflow catch-up page (`workflow_steps.catch_up_page_size`, Ting) | 200 | Nothing: it is the page size of the read of a session's durable log | Not a cap: the read continues until the log has no more | A log that reports more and does not advance is an error |
| Mesh request freshness (`mesh.attestation.clock_skew_s`) | 300 s | Bounds replay of a captured request or reply, and tolerated clock drift | Kept | The request is refused as `stale` and counted |
| Mesh event age (`mesh.attestation.event_max_age_s`) | 3600 s | Bounds replay of a captured event; must exceed the transport's redelivery window | Kept | The event is not handled, logged at error and counted as `stale` |
| Mímir fact recall (`recall_limit` of the gbrain fact adapter, knob `mimir.facts.gbrain.recall_limit`) | 100 | gbrain caps `recall` at 100 rows per call and cannot page | Kept: it is gbrain's own cap, so the adapter sends exactly that and does not guess a lower one | Reported as a `backend_limit` omission in the result, error log, counted; `get` raises instead of answering "not found" when the cap may have hidden the fact |
| Memory gateway recall per layer (`memory.systems[].recall_limit` of the gbrain backend) | 100 | gbrain caps `recall` at 100 rows per call and cannot page; each layer's facts are recalled separately | Kept: gbrain's own cap | A layer that returns that many is reported as an omission in the result, logged at error and counted on `ravn.cap.hits` |
| Memory gateway timeline read (`memory.systems[].timeline_limit`) | 1000 | `get_timeline` takes a limit and no offset | Kept: the size of the read, since gbrain cannot page it | A page whose timeline returns that many rows is reported as cut and counted |
| Memory gateway page slug (`memory.systems[].max_slug_chars`) | 255 | gbrain's limit on a page slug, which includes the layer prefix | Kept: gbrain's limit | The write is refused naming the knob and the slug; counted |
| Memory context pack (`memory.context_pack_share` × the session model's catalog `context_window`) | 0.05 of the window | The model's context window, which also has to hold the harness's own prompt and tools and the session's work | Kept: derived from the catalog, never a literal; a model without `context_window` is an error naming it | Records that do not fit are listed in the pack's `omitted` (and in a line of the pack), logged at error and counted on `ravn.cap.hits` (`memory.context_pack_share`) |
| Memory context pack entities (`memory.default_result_count`) | 10 | Nothing: it is how many pages the session's objective and brief are searched for | Kept as the count of a search the caller cannot name | Reaching it is listed in `omitted` and counted (`memory.default_result_count`) |
| memory_ask retrieval (`memory.ask_retrieval_count`) | 10 | The answer model's context, which must hold every record read | Kept | A search that returns that many is a gap in the answer naming the knob, logged at error and counted (`memory.ask_retrieval_count`) |
| memory_ask answer (`memory.answer_model`) | no cap sent | The model's own output limit or the rest of its window | Not a cap: none is sent | A cut-off answer raises `OutputTruncatedError` naming the knob (`require_complete`) and is counted |
| Bifröst calls of the memory gateway (`memory.bifrost_timeout_seconds`) | 300 s | The longest answer memory_ask waits for | Kept: a wall-clock bound | The call fails as a tool error (or a 502 for the context pack) naming Bifröst |
| Memory retention (`memory.retention_days`, swept every `memory.retention_sweep_seconds`) | 14 days, hourly | Design decision of 2026-11-12 | Kept: a decision, not a guess | Layers past it are deleted and logged; a sweep that cannot ask Forge or list a system deletes nothing there, logs at error, counts on `ravn.degraded` (`memory.retention_sweep`) and is tried again next interval |
| Mímir fact provenance (`provenance_max_chars`, knob `mimir.facts.gbrain.provenance_max_chars`) | 500 | gbrain stores at most 500 characters of `provenance` | Kept: the structured envelope (author, trust, evidence, chain) has to fit | `remember` raises naming the limit and the remedy (cite fewer or shorter evidence references); error log, counted |

## Remaining fallbacks

These broad `except` handlers log at warning or below and continue around
stateful work. They are tracked as follow-up:

- Skuld: `ChannelRegistry.broadcast` removes a channel for good on one send
  error; the directed-message WebSocket send to a resident returns `False`
  with only a warning (`collaboration_adapter.py`); room-event and
  `ravn.help.needed` publishes log at warning.
