# Preserving local sessions during an API release

This procedure is for the **local-process** Forge API. It is not a Kubernetes
deployment, a gateway upgrade, a native-session restart, or a database operation.
It replaces the untracked orchestration used in the [September 13 incident](codex-rollout-incident-20260913.md).

## What must remain alive

An API restart can disconnect its HTTP/WebSocket clients. It must **not** terminate
Skuld gateways, native Codex/Claude processes, tmux owners, or database postmasters.
The restarted API reconstructs local proxy routes; a client reconnects to the same
gateway and running turn without resending the prompt. Existing live gateways keep
their loaded source. Shipping a new API is not a claim that they adopted new code.

A terminal database row with a live process is a preservation input, not permission
to kill the process or rewrite its database status. Startup cleanup must use the
actual adapter's backend declaration. Intentional terminal rows are not
automatically resurrected either. Resolve any status discrepancy separately with
the session owner.

## Release artifacts

Freeze and test two immutable sources and independent environments on each host:

1. The complete candidate, including the local-backend preservation fix.
2. A **safety-only rollback**: the host's previous source plus that preservation
   fix. Returning to the unpatched classifier can repeat the same incident during
   rollback startup. Merely removing the source override is therefore unsafe.

Keep each host's original unit, launcher, configuration, authentication, mounts,
database selection and unrelated drop-ins unchanged. Relocate only copied virtual
environments. Audit module paths and source digest in each staged environment.
Test the same full-startup preservation fixture against both sources.

## Guarded tooling

`scripts/forge_api_release.py` provides two explicit modes:

```sh
python3 scripts/forge_api_release.py prepare "$MANIFEST" "$PREPARATION"
python3 scripts/forge_api_release.py apply "$MANIFEST" "$ATTEMPT" --prepared "$PREPARATION"
```

`prepare` never modifies service configuration or restarts anything. It saves
private evidence and runs isolated release import/composition audits in temporary
homes. `apply` requires successful evidence for the exact unchanged manifest and
a newly coordinated window with enough time for recovery. No new window is implied
by creating release files. Evidence filenames must be new for every attempt.

Manifest fields (host-local, saved privately; never include credentials):

| Field | Required meaning |
| --- | --- |
| `unit`, `api_url` | Exact user systemd API service and reachable configured HTTP/HTTPS origin. |
| `expected_health` | Fresh previous API revision, Python source digest, `dirty: false`, `failed_plugins: []`. |
| `candidate`, `rollback` | Each has absolute `root`, pinned `health`, and the complete source-only `override_content`. Both must pass actual local-adapter composition audit. |
| `override_path` | One new owned drop-in under that service's user unit directory. Existing drop-ins are never removed. |
| `state_file` | Existing local-process state file, read-only to the operator script. |
| `protected_pids` | Additional known native owners and database postmasters; identify them, not transient database connection children. |
| `stable_files` | Existing configuration/launcher/unit inputs whose byte hashes must not change. |
| `replay_ids` | Inactive stored conversations selected for exact turn comparison. Include important archived acceptance histories. |
| `checks` | Named independent URL checks with nonempty stable `json_fields`, or a `sha256` only for static content. Correct TLS schemes and system CA validation are mandatory. |
| `window` | Explicit timezone-qualified `start` / `end`, or null during preparation. Null cannot be applied. |
| `command_timeout_seconds`, `http_timeout_seconds`, `ready_timeout_seconds`, `poll_interval_seconds` | Explicit bounded operation/readiness timing. Reserve time for candidate startup and recovery. |

The script reads all sessions with **`include_archived=true`**, protects PID/start
ticks/boot ID/command hashes, includes every discovered live gateway regardless of
stored status, and verifies its direct health identity. It also discovers native
Codex app servers, Claude processes and tmux owners belonging to the service user.
Unknown harness owners must be explicitly inventoried in `protected_pids`.
It hashes archived replay samples without saving their conversation text.

All prechecks execute in the same fail-closed control flow as mutation. A failed
TLS check, changed process, source drift, replay difference, unavailable service or
missing window prevents **every** later install/restart. Dynamic uptime and
connection counts are not static-body guards. Immediately before applying, it
repeats source, unit and preservation checks and rejects new runtime owners or
changed sessions; regenerate preparation after legitimate concurrent changes.

Only the selected API is restarted. Both candidate and safe rollback use bounded
readiness polling, not a single immediate health read. A failed cutover remains a
failed attempt even if rollback is healthy. Recovery errors are recorded separately.
The script never signals protected processes or automatically resumes/resends.

## Proof and boundaries

The real-process regression creates an API that owns a real Skuld subprocess,
driven by an explicitly test-only transport with its own native-process fixture.
Database/catalog infrastructure is mocked; no model provider is called. It covers:

- Running, stopped, archived and failed database rows with live gateways.
- Actual Volundr composition, local adapter state reload and startup reconciliation.
- Graceful API termination and abrupt API death while a proxied WebSocket is attached.
- Reconnection through rebuilt production proxy routes to the same in-flight turn.
- Output produced while the API is absent, replayed after reconnection.
- Subsequent periodic reconciliation, unchanged gateway/native process identities,
  unchanged stored status, completed conversation content and exactly one native send.

The same test rejects the original backend misclassification. This is materially
stronger than testing only parent-process shutdown, but **not** a real PostgreSQL
restart/durability test or a physical iOS reconnection test. Existing event-log
retry/overflow, native Codex, approval and history tests are separate evidence.

No finite test proves unlimited outage buffering. Keep the API outage within its
tested/observed bounds, inspect event-log gaps/conflicts and compare retained
prefixes at cutover. This script checks identities, direct health and selected
replay, not every historical frame or every unflushed buffer. Do not claim complete
data preservation solely from green health. New or naturally finished work can
invalidate a snapshot; stop and inspect rather than change records to pass.

## Adoption after API cutover

Healthy existing gateways remain running on their previous code. New sessions use
the newly selected source. Migrate an existing gateway only at an owner-approved
saved turn boundary, preserving its Forge ID, native thread/session ID, workspace,
history and pending-control disposition. Do not replay uncertain prompts or old
approval answers. This release procedure does not perform that migration.
