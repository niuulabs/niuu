# Session lifecycle API

Session operations use `/api/v1/forge/sessions`. The following reads apply to a
local mini-mode host with its default port:

```bash
curl --fail --silent --show-error http://127.0.0.1:8080/api/v1/forge/sessions
curl --fail --silent --show-error http://127.0.0.1:8080/api/v1/volundr/session-definitions
```

The second response supplies runtime definitions used when constructing a launch.
Choose from the server's available definitions rather than assuming a display
name is an API identifier.

## Lifecycle operations

| Method | Path | Effect |
| --- | --- | --- |
| GET | `/api/v1/forge/sessions` | List visible sessions |
| POST | `/api/v1/forge/sessions` | Create a session from a launch request |
| GET | `/api/v1/forge/sessions/{id}` | Inspect one session |
| POST | `/api/v1/forge/sessions/{id}/stop` | Stop its runtime |

`{id}` denotes the ID returned by the server, not a literal path segment. Use the
[OpenAPI schema](api.md#schemas-and-their-scope) for complete request fields and
additional operations.

## State versus task success

A running session means the backend started the runtime. Provider authentication,
stream delivery, and the requested work can still fail. Inspect the agent's
response, workspace output, and logs. The quick-start smoke test exercises create,
running, and stop; its live mode additionally verifies a file written by the agent.

Use returned chat/code endpoints. They depend on the backend and platform routing
and should not be guessed from a session ID. Local workspace retention is
verified separately from OpenShell cleanup and Kubernetes storage policy.
