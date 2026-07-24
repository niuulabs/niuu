# Valkyrie observability

The resident runtime emits one causal OpenTelemetry trace from external signal
receipt through final judgment and transport acknowledgement. Metrics and traces
are exported independently so an unavailable backend cannot become decision
logic.

## Trace shape

A normal durable trajectory is visible as:

```text
ravn.environment.collect
  receive <NATS subject>
  ravn.signal.adapter.collect
  ravn.signal.normalize
  ravn.signal.publish
    publish sleipnir batch
      process <event type>                 # each subscribed operator
  ravn.signal.enqueue
    ravn.queue.enqueue
  ravn.signal.commit
    ack JetStream signals

invoke_agent <persona>                     # same trace after queueing
  chat <model>                             # iteration number + request/response event
  execute_tool capability_list
    GET /api/v1/niuu/observatory/agents
      ravn.http.resolve_auth               # cached vs real workload exchange
  ...ordered model/tool iterations...
  ravn.resident.complete_turn
    ravn.port.resident_state.write_turn
    ravn.port.resident_state.write_budget
    ravn.port.resident_inbox.acknowledge
  publish valkyrie.judgment.proposed
    process valkyrie.judgment.proposed
      ravn.odin.resolve_case
```

The exact tree varies with the model's choices. There is no span sequence that
the model must follow. Runtime invariants—durability, authority, provenance,
authentication, and bounds—remain deterministic.

In-process tool execution produces the nested tool and dependency spans shown
above. CLI transports also record the exact tool request and response as events
on the model span. The CLI MCP subprocess inherits the current task trace
context, so its tool, Guild, A2A, and tool-build spans remain part of the same
causal trace. `ravn.trace.boundaries` records that cross-process remote-parent
boundary.

Every Sleipnir event carries W3C trace context in its envelope. Publish and
subscriber port decorators create producer/consumer spans, so ResidentLearning,
ODIN, feedback, huddle, and other operators remain causally connected without
each operator inventing its own propagation scheme. Queue journals retain trace
context across process restarts, but recovered work starts a bounded new trace
with an OpenTelemetry link to the pre-restart trace. This avoids an indefinitely
open trace while preserving causality.

## Content capture and safety

`observability.capture_content` controls trace events containing signal
payloads, prompts, model responses, tool inputs/results, HTTP JSON, and event
payloads. It is disabled by default. When enabled, content is recursively
redacted by sensitive key name, bearer/JWT-shaped strings are removed, and each
event is bounded by `observability.content_max_chars`.

Local behavioral proof configurations opt in because trajectory inspection is
the purpose of those runs. Production should enable it only after reviewing the
data classification and backend access policy. Authorization headers and token
values are never recorded.

## Metrics

The runtime exports low-cardinality metrics for:

- liveness, active work, queue depth, admission and rejection reason;
- signals by configured source/type/severity and JetStream receive/ACK/NAK;
- tasks by outcome and latency;
- LLM calls, latency, input/output tokens, and model;
- tool calls, outcomes, and latency;
- judgment, continuation, tier, and authority choices;
- resident dispositions and ODIN decisions;
- capabilities visible by kind;
- authenticated HTTP dependency calls by host/path/status;
- A2A operations, task states, polls, and tool-build outcomes;
- event-bus publish/consume activity and operator handler.

Task ids, free-form selected actions, prompts, and A2A task ids are trace-only;
they are deliberately excluded from metric labels to avoid unbounded series.

## Grafana

Import
[`valkyrie-runtime.json`](grafana/valkyrie-runtime.json) into Grafana or provision
it through the existing dashboard sidecar with label `grafana_dashboard: "1"`
and folder annotation `grafana_folder: Valkyries`. The dashboard uses selectable
Prometheus/Mimir and Tempo data sources, so it is not tied to a tenant-specific
UID; the provisioned instance defaults to the Eitri tenant.

The dashboard covers current resident/queue state, task outcomes and latency,
judgment choices, signals, tool behavior, capability visibility, model usage,
durability handoff, dependency health, A2A/tool evolution, resident/ODIN
dispositions, operator/event-bus activity, and recent task traces. Prometheus
exemplars can link latency samples directly to Tempo when the Grafana data
source has exemplar linking enabled.

The existing embedded Valkyrie dashboard remains a product/event projection.
Grafana/Tempo is the diagnostic source for claims about what the model actually
saw, chose, called, received, persisted, and acknowledged.

## Live verification record

On 2026-07-20 the local Ivaldi configuration exported this implementation to
the Eitri tenant in Glitnir while consuming the deployed Laevateinn JetStream
and calling the configured Nemotron endpoint.

- Trace `5cd44ef60861372fa135416d665abd29` records the retained case, both model
  turns, the model-selected `web_search`, its real result, the parsed judgment,
  resident disposition, and published outcome events.
- Trace `77e58033290d865b891a22d033bbaef8` records a new JetStream receive,
  normalization and publication, `queue_full` rejection, failed intake span,
  and NAK. This verifies that the event was retained rather than falsely ACKed.
- The tenant exposed the expected Ravn, GenAI, signal-transport, HTTP, resident,
  ODIN, and event-bus series. Every PromQL expression in the dashboard returned
  a successful query response. Empty A2A/tool-build panels were expected: no
  real build trajectory occurred during this run. The dashboard TraceQL query
  returned the task traces.

The traces immediately exposed two unresolved correctness gaps: an empty
`signal_refs` list was still classified as a valid judgment, and a requested
continuation became a stop disposition when its enqueue hit a full queue while
the parent task was nevertheless recorded as successful. They are evidence
that the telemetry is diagnostic, not evidence that the resident behavior is
already correct.

On 2026-07-21 a Codex-backed Ivaldi consumed unprompted events produced by a
simulated Sindri printer through the real Laevateinn JetStream. Repeated print
attempts independently failed with `RELEASE_OVERCOUNT` after the simulated
release-film counter exceeded its declared maximum. No scenario name or tool
building instruction was supplied to the resident.

- The Observatory workload-identity chart was deployed through GitOps. Guild
  then returned 2 real agents and 10 peer skills from 7 healthy sources; one
  unrelated Valaskjalf source remained unavailable.
- The trial exposed a harness defect: `build_tool` was described to the model
  but absent from the CLI MCP tool server. After the server gained dynamic tool
  registration, a real MCP `tools/list` included `build_tool` and capability
  discovery shared the same live registry.
- Trace `b0c5850ed42cc662ed2824c9c5218e07` records the exact operator answer,
  Codex request and response, targeted `capability_list` call, and its truthful
  result: 25 catalog entries including `web_search`, `a2a_task`, `build_tool`,
  and 10 peer Agent Card skills.
- Codex did not build or delegate. It revised an earlier physical-maintenance
  assumption after learning the printer was simulated, preserved the repeated
  failure pattern, prohibited another retry, and slept for an external event.
  The source event exposed no addressable management interface, documentation,
  object reference, or provenance link from which a safe reset capability
  could be researched or verified. This is a blocked evolution trajectory, not
  evidence of autonomous tool evolution.
- That historical trace predates MCP subprocess instrumentation. Current
  Codex-backed turns start the tool MCP process with the parent trace context,
  emit the same tool-call metrics as native Ravn execution, and preserve
  internal Guild/A2A/tool-build spans in the causal trace. A client may supply
  fresher per-call context through MCP request metadata.

The dashboard is provisioned in the separately managed Glitnir Grafana release
through its existing dashboard sidecar and GitOps configuration. Open the live
[`Valkyrie Runtime and Judgment`](https://grafana.glitnir.alfheim.niuu.world/d/valkyrie-runtime-judgment/valkyrie-runtime-and-judgment)
dashboard, select a resident service, and click a Trace ID in **Recent resident
task traces** to inspect its complete Tempo span timeline. **Installed learned
tools** distinguishes the durable artifact envelope from the separately
materialized Python tool. **Trace boundaries in range** shows the remote-parent
MCP crossings and restart-recovery links where a trajectory crosses a process
or lifetime boundary. Tempo collector warning metrics are not ingested into the
`eitri` Mimir tenant, so the dashboard does not fabricate a zero-valued warning
panel.
