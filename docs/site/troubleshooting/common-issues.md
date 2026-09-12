# Fix a failed launch

Find the first failing boundary: platform startup, runtime startup, provider
request, or the task itself. Keep the session ID and the original error while
checking it; recreating the session can hide useful evidence.

## The platform will not start

For `niuu platform up`, read the foreground terminal. For the development stack:

```bash
tail -n 100 build/dev-run/logs/platform.log
```

If preflight names a missing tool, install that prerequisite from the
[installation guide](../get-started/install.md). Embedded PostgreSQL must run as
an ordinary user. A port conflict requires stopping the process that owns the
port or configuring a different host port; do not delete database files to fix it.

**macOS v1.3.0:** a missing `libpq.5.dylib` from the extracted binary is a known
packaging failure. The download can pass its checksum and still have this defect.
Use the documented source route until a corrected artifact passes startup checks.

## The browser does not open the platform

Use the exact URL printed at startup. `start-dev` may select a different host or
UI configuration query string from a plain foreground run. Check `/health` at
that origin. If it succeeds but a deep UI URL shows JSON, open the root URL and
navigate through the application.

## The session is running but the agent cannot answer

Check the agent error and session logs. For the local Claude subscription path,
run this from the same OS account as Niuu:

```bash
claude -p 'Reply with exactly OK.' --model sonnet --max-turns 1
```

If it reports an expired token, refresh with `claude auth login`, then repeat the
request. `claude auth status` can report a stored login while its token is expired.
For a remote sandbox, test its configured credential path; your host login does
not automatically apply there.

## The file is missing

Check the task result and the workspace belonging to this session ID. A running
process or successful chat connection is not proof the agent wrote a file. On a
local mount, inspect the mounted path; on a blank local workspace, use the path
shown in the [quick start](../get-started/first-local-stack.md).

## A route returns 404 or 503

Check the route owner and enabled services in the [API reference](../reference/api.md).
A missing route can mean the service is disabled or the route was guessed from a
UI label. A configured route with an unavailable dependency needs its service logs.
Do not bypass a shared deployment's gateway to avoid its authentication policy.

## Stopping from another terminal does nothing

A foreground `niuu platform up` owns its in-process service manager. Stop that
process with Ctrl+C. Use `./stop-dev` for a stack started by `./start-dev`.
The CLI `platform down` command does not control an unrelated foreground process.

## Report a reproducible issue

Include the Niuu version or source revision, OS, runtime backend, exact command or
UI action, session/run ID, expected result, and the first error. State whether a
standalone provider request works. Remove credential values from logs before
sharing them.
