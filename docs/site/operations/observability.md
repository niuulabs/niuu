# Diagnose an operation across services

Start with a concrete session, run, or resident case. Its ID links the user's
symptom to the service that owns the work.

## Follow the failure path

| Symptom | First evidence | Next boundary |
| --- | --- | --- |
| Platform does not start | Foreground output or platform log | Preflight, database, migrations |
| Session does not start | Forge session state and backend logs | Local process, sandbox, or pod |
| Running session has no answer | Skuld/runtime logs | Provider auth, model request, stream |
| Workflow is stuck | Ting run and stage state | Executor, output condition, gate |
| Resident waits without progressing | Ravn case and requested input | Delivery and exact continuation context |
| Knowledge is missing | Mímir mount, source, page, and search | Write routing, synthesis, index |
| Target is missing | Guild instance and target records | Registration, reachability, profile eligibility |

## Local logs

For the development stack:

```bash
tail -n 100 build/dev-run/logs/platform.log
```

For a foreground host, inspect its terminal output. Use the session's log and
telemetry surfaces for runtime-specific failures; do not infer runtime health
from a successful host health response.

## Topology and traces

Observatory shows topology and observable relationships. Follow the owning
service, target, and related run or agent rather than assuming similarly named
entities are the same instance. Where tracing is configured, preserve W3C trace
context through model calls, tools, mesh, room delivery, and resumed cases.

Record the operation ID, timestamp, failing route or action, and error. Exclude
credential values and private prompt content from shared diagnostics unless that
content is necessary and approved for that audience.

## Point the stack at a collector

Set `observability.enabled: true`, `trace_endpoint`, and `metric_endpoint` in
each service's config (see [Configuration](../reference/configuration.md#observability-opentelemetry))
and point them at any OTLP-compatible backend — Tempo, Jaeger, Grafana Cloud,
Honeycomb, or a local `otel-collector`. A minimal local setup, one collector
receiving from every service:

```yaml
observability:
  enabled: true
  trace_endpoint: "http://localhost:4317"            # OTLP/gRPC — Tempo/Jaeger default
  metric_endpoint: "http://localhost:4318/v1/metrics" # OTLP/HTTP
  insecure: true
```

Nothing exports until `enabled: true` is set explicitly — the default is off.
`enabled: true` with `trace_endpoint` or `metric_endpoint` left blank fails
config validation at startup (`niuu.domain.observability.ObservabilityConfig`).
An endpoint that validates but is actually unreachable does not fail startup
— the OTLP exporters connect lazily and retry in the background, so a wrong
host or a collector that's down yet shows up as spans/metrics silently not
arriving, not a startup error. Verify a new collector is actually receiving
data after pointing a service at it.

## What one trace now shows

A single W3C trace id follows one piece of work end to end:

1. **Resident judgment (Ravn)** — the case/run span, tool calls, and each
   model request. `src/ravn/adapters/llm/{anthropic,openai}.py` inject the
   active `traceparent`/`tracestate` onto every model HTTP request.
2. **Bifröst** — once the gateway's own FastAPI app is instrumented
   (`niuu.observability.instrument_fastapi_app`), the inbound completion
   request is a child span carrying `gen_ai.*` attributes: requested
   model/alias, chosen provider, `bifrost.failover_attempts`,
   `bifrost.cache_hit`, and token usage (`src/bifrost/router.py`).
3. **Völundr → Skuld → Claude Code** — Völundr's `CoreSessionContributor`
   (`src/volundr/adapters/outbound/contributors/core.py`) carries the trace
   context active when a session is created into that session's own pod/
   process env as `TRACEPARENT`/`TRACESTATE`, for every pod type (via
   `LocalProcessPodManager._session_env`), not only `ravn_flock` sessions.
   Skuld, running as that pod/process, inherits those env vars and forwards
   them onto the Claude CLI/SDK env it spawns
   (`src/skuld/transports/claude_env.py`); Skuld also attaches them as its
   own ambient context at startup (`src/skuld/broker_api.py`), so its own
   spans nest under the same trace. Claude Code itself reads
   `TRACEPARENT`/`TRACESTATE` in Agent SDK and non-interactive (`-p`)
   sessions and parents its own `claude_code.*` spans under them — see
   [Claude Code's OpenTelemetry docs](https://code.claude.com/docs/en/agent-sdk/observability).
   Interactive sessions ignore inbound `TRACEPARENT` by design, so this link
   only closes for non-interactive/Agent-SDK-driven sessions today.
4. **Ting and Völundr** — every inbound request to either service's own
   FastAPI app (session/workflow REST calls) is a span in the same trace,
   once each composition root's `instrument_fastapi_app` call is active.
5. **Forge session spans** — the existing Postgres-backed trace shown on a
   session's Trace tab is a *separate* system, keyed by the session's own
   UUID (`trace_id`), not a W3C trace id — it predates OTel adoption here and
   changing what `trace_id` means would break every existing consumer of
   `GET /sessions/{id}/trace`. Each span additionally stores the W3C trace id
   active when it was recorded, as `w3c_trace_id`, so a Forge span can be
   cross-referenced with the OTLP trace above by pasting `w3c_trace_id` into
   your trace backend's search.

## Völundr's two OTel pipelines

Völundr has two independent OTel exporters, each answering a different
question — not consolidated, see `volundr.config.OtelConfig`'s docstring for
why:

- `observability` (this doc, `Settings.observability`) — live request/client
  spans via the shared `niuu.observability` facade. This is what makes one
  trace id follow a piece of work end to end.
- `event_pipeline.otel` (`Settings.event_pipeline.otel`) — a separate sink
  that replays already-recorded `SessionEvent` rows as GenAI spans after the
  fact, keyed by `session_id`, not tied to any live trace. Independent
  `enabled` flag, independent endpoint.

Known gaps (not yet closed): Codex has no confirmed, documented mechanism for
accepting inbound trace context, so nothing is injected into its env — only
`OTEL_EXPORTER_OTLP_ENDPOINT`-style config in its own `config.toml` `[otel]`
section is documented, and that was not wired here to avoid guessing at an
unconfirmed contract. Mímir and Observatory get server spans on their own
FastAPI apps, but calls between Mímir and the resident/gateway that queries it
are not yet threaded with explicit trace-context propagation.
