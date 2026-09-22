# Forge session activity and elapsed time

## User-visible contract

| Display | Meaning | Elapsed anchor |
| --- | --- | --- |
| Working | A turn is in progress: thinking, streaming, or executing tools | `turn_started_at`, then `activity_state_since` for older brokers |
| Idle | The runtime is alive and the last turn has completed | `activity_state_since` |
| Needs you | A question, permission, or confirmation blocks progress | `activity_state_since` |
| Starting | The runtime/CLI is not ready yet | `activity_state_since`, when known |
| Connected | Lifecycle is running but no actionable activity report is available | None; do not invent a working timer |
| Stopped / Error / Archived | Terminal lifecycle state | Recorded last activity, not a running timer |

The wire vocabulary stays compatible: `active` and `tool_executing` both display
as **Working**. Lifecycle `status=running` alone does not mean work is in progress.
`last_active` includes liveness/usage writes and must not be used as a turn or idle
start. Idle elapsed measures time since completion, rather than time since the
most recent heartbeat. A temporary human gate preserves the original turn start.

## Live audit

On September 19, a read-only audit compared the Guild list to the owning gateways'
one-message shallow conversation envelopes. No prompts or lifecycle operations
were sent. Fifteen running-lifecycle sessions were checked across the four hosts:

| Host | Checked | List said busy while its gateway said idle |
| --- | ---: | ---: |
| Thor | 7 | 4 |
| Build | 2 | 1 |
| Build Bro | 5 | 3 |
| Build-Kit | 1 | 0 |

This is server-side stale activity, not missing conversation persistence. The
audit is a point-in-time sample; it is not a claim that all future activity is
known when a host is offline.

## Causes and corrections

1. Generic PostgreSQL session updates wrote the activity tuple from a stale
   read-modify-write snapshot. A usage update could restore `active` after the
   idle report had committed. The update now conditionally accepts the activity
   tuple only when its timestamp is at least as recent, and returns the tuple
   actually stored. `last_active` cannot move backwards either.
2. Broker activity reporting awaited HTTP-client acquisition before capturing
   shared timestamps, pairing an old state with a newer anchor. Capture the
   tuple before any await and serialize the POSTs. Failed terminal/idle reports
   are retried by the existing heartbeat loop until acknowledged. Successful
   idle sessions do not gain new periodic liveness heartbeats.
3. Service-level delayed reports are ignored before publication. Unstamped
   same-state/coarse-busy heartbeats retain their anchor. Idle/stopped/error clear
   the turn start; tool changes do not restart it. Streaming text deltas count
   as work unless an outstanding human gate blocks the session. Startup now
   actually reports provisioning rather than deduplicating its own first report.
4. The facade streamed only its default host. Web now requests
   `GET /api/v1/forge/sessions/stream?all_instances=true`. Each visible enabled
   Guild host has an independent reader, bounded queue, reconnect and cleanup.
   A failed host cannot block healthy ones. Each event carries registry host
   identity. Remote requests omit `all_instances` to avoid recursive federation.
   Default and `instance_id`-scoped streams remain available to native clients.
5. Web normalization discarded both elapsed timestamps. They now survive REST,
   SSE, domain mapping, rows and the session header. Older snapshots cannot
   overwrite newer activity; mismatched-host activity events are rejected.
   State changes reach mounted queries immediately without fetching archives.
   Identical snapshots do not reset poll deadlines or clear another source's
   errors. Existing independent per-host polling remains the recovery path.
6. iOS/macOS already had canonical ForgeKit state, per-host SSE, stale-event
   guards and broker snapshot reconciliation. The native follow-up on
   `forge/session-activity-ux` aligns Working/Connected labels, adds Mac idle
   elapsed, and removes heartbeat-based anchors from iOS Live Activities.

## Verification and rollout

Regression coverage includes stale usage writes, delayed reports, a credential
await race, retrying idle without moving its anchor, streamed text during a human
gate, host-scoped SSE forwarding, reconnect/cancellation, REST/SSE ordering, and
unchanged snapshots preserving polling/error state. The PostgreSQL concurrency
case is also in the CI-only integration suite; no live database was mutated for
testing. Browser coverage drives four hosts through a remote Working → Idle event
before polling, then verifies a stale REST response cannot undo it. Separate
desktop/phone checks keep healthy hosts visible while another host times out.

The web release can be published independently. Server correctness requires the
API persistence fix on **every owning Forge host**, plus the broker fix for newly
started/upgraded gateways. An API-only restart does not replace loaded gateways.
Already-stale rows on old gateways must be refreshed by an authoritative activity
report; renaming a label alone cannot repair them. Follow
[the API preservation release procedure](local-api-release.md) for cutover,
including an explicit maintenance window and a safe rollback. Native app builds
need their normal review/distribution step. This change does not claim that those
server or native releases have already been installed.

Validation on Thor/Mini: 19,851 backend tests passed (86.58% coverage), 6,323 web tests passed (all four 85% gates), 301 ForgeKit tests passed and 29 iOS host tests passed. The three broad-backend warnings came from HTTP response mocks lacking synchronous `raise_for_status`; those mocks were corrected and the affected suites rerun with warnings treated as errors. Native iOS and Mac apps compile; iOS simulator and Mac component renders were inspected. Mac host test bundles compile, but executing the Mac XCTest host over this headless connection did not complete; shared behavior ran through ForgeKit and iOS tests. The PostgreSQL integration regression remains CI-only.
