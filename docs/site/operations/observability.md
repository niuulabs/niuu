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
