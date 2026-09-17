# Inspecting and gradually upgrading a session's Skuld runtime

September 17, 2026. Local-process Forge, not Kubernetes. These are separate
operations: an API-only release preserves all existing session processes; an
individual session upgrade deliberately replaces that session's gateway/native
processes and resumes its saved conversation.

## Which version is actually running?

Use the **owning host**, not an arbitrary host that happens to aggregate the
session into its list. Thor's aggregate includes sessions on other machines.
Read-only HTTP routes already exist:

| Request | Meaning |
| --- | --- |
| `GET /health` | This host's loaded central server source. After a validated local release, this is also the source used to launch new local gateways. |
| `GET /s/{session_id}/health` | That session's loaded Skuld source, not its workspace's current Git HEAD. |
| `GET /api/v1/forge/sessions/{session_id}` | Persistent Forge identity, status, recorded native `cli_session_id` and session configuration. |
| `GET /s/{session_id}/api/control-state` | Pending/recovered question and permission controls; not a complete idle/upgrade eligibility check. |

Both health responses expose `revision`, `source_sha256`, `build` and `dirty`.
Session health also supplies `session_id`; validate it against the requested ID.
The identity is captured at process startup. Editing a checkout does not make an
already-running gateway adopt those edits.

Compare **source digests**, not just a release label or Git revision: a later
documentation-only commit need not change executable source. An unknown identity,
dirty source or unreachable runtime is **unverified**, not automatically current
or safe to upgrade. A different clean digest means it is not the target build;
do not assume every divergent/custom build is merely an older release. This digest
does not independently attest dependency versions, native CLI version or settings.

Example reads, using your existing credential and an explicitly selected host:

```sh
BASE=http://thor.tail737f2a.ts.net:8080
: "${SID:?Set SID to the exact Forge session UUID}"
: "${FORGE_TOKEN:?Use the existing Forge credential}"
curl --fail --silent --show-error -H "Authorization: Bearer $FORGE_TOKEN" "$BASE/health"
curl --fail --silent --show-error -H "Authorization: Bearer $FORGE_TOKEN" "$BASE/s/$SID/health"
```

Stopped/archived sessions normally have no live runtime version. If one still has
a live gateway, preserve it and resolve the status discrepancy with its owner;
neither its status nor a version mismatch authorizes cleanup or resurrection.

## Existing, supervised per-session upgrade process

1. Select exactly one session and agree a pause with its owner. Exclude archived,
   intentionally stopped, uncertain and custom-runtime sessions from automatic
   selection. Do not select this deployment worker while it is executing.
2. Prevent **all** new ingress to the selected session for the whole handoff:
   user clients, CLI messages, steering, scheduled work and other senders. A single
   client's paused UI is not an ingress lock.
3. Confirm the native turn has finished, not merely that an activity hint says
   `idle`. Check pending questions/permissions, queued or unacknowledged input,
   running tools/agents and terminal drafts. A paused question is not a safe idle
   boundary. Do not automatically answer it to make the session eligible.
4. Record the Forge ID, native thread/session ID, workspace, definition, model and
   runtime options, plus the durable conversation tail. Confirm the native history
   is available to the replacement process. A missing native ID is a stop condition,
   not permission to start a fresh conversation. Preserve saved uncommitted files;
   this operation does not require a Git push, reset or clean checkout.
5. Stop **only this session** using the existing route, wait for successful stopped
   status, then start/resume the **same Forge ID**. Never delete/recreate it:

   ```text
   POST /api/v1/forge/sessions/{session_id}/stop
   GET  /api/v1/forge/sessions/{session_id}          # verify stopped
   POST /api/v1/forge/sessions/{session_id}/resume  # alias of /start
   GET  /api/v1/forge/sessions/{session_id}          # wait for running or failure
   ```

   This is a deliberate per-session stop/start, not uninterrupted execution.
   `resume` returns before provisioning completes. The server reuses the persisted
   definition and overlays the saved native identity. Codex's adapter uses native
   `thread/resume` and rejects a replacement thread ID; it does not silently fall
   back to a fresh conversation. A start with **no** saved native ID can create a
   new conversation, which is why the precondition above is essential.
6. Verify the new gateway health matches the selected target source, the Forge
   identity/workspace/configuration are retained, the native thread ID is unchanged,
   and the saved history tail is present. Check model/effort and other runtime
   options rather than assuming every transient native setting was persisted.
   Reconnect clients only after these checks. Do not resend the last prompt or an
   old approval answer. On uncertain stop/start results, inspect state first; no
   blind retry loop. Keep ingress paused and report any failed resume.

These are existing API primitives, **not an atomic upgrade transaction**. This
release does not perform this operation on any existing session and does not claim
a fresh provider-backed migration test for every retained harness/configuration.

## What is missing for safe unattended “upgrade when idle”?

Do **not** implement `if activity_state == idle: stop(); resume()` as an unattended
job. A new message can arrive between the check and stop, and the current stop
endpoint does not atomically check idle state or the expected gateway generation.
The observed activity hint can also be stale. `/resume` is not an upgrade scheduler.

Before unattended upgrades, the lifecycle contract needs a per-session ingress
fence/drain, authoritative native quiescence plus pending-input/control checks,
an expected runtime identity, a pinned target, durable operation identity and
status, and verification of same-thread/history recovery before releasing ingress.
Failures must remain explicit without duplicating input or silently changing
conversation identity. This is ordinary generic session lifecycle infrastructure,
not a coordinator workflow engine. It is **not implemented by this release**.

Until that exists, use the supervised one-session procedure above at an agreed
pause. Read-only version inventory can be automated independently and safely.
