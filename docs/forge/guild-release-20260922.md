# Guild API release — 2026-09-22

Release source: `636017fb38a284ea999fd8a9978454f4904f0c3a` on
`forge/ux-improvement`, reviewed in [xteo/niuu#3](https://github.com/xteo/niuu/pull/3).
Python source digest:
`f15f4700ab52bbc4be9372539749c748256b48ac3540ee32c0cc4c6dbd50298e`.
Subsequent design/report commits do not change that deployed source.

## Deployment status

| Node | API result | Preservation at API cutover |
| --- | --- | --- |
| Spark | Verified `636017fb`, 03:23 UTC | 4 protected processes, 15 session records, 2 inactive replay samples. No live Skuld gateway existed. |
| Thor | Verified `636017fb`, 03:28 UTC | 48 protected processes, 16 live gateways, 644 local session records, 2 inactive replay samples and retained event prefixes. |
| BuildBro | Verified `636017fb`, 04:01 UTC | 24 protected processes, 2 live gateways, 13 session records, 11 inactive histories and raw event prefixes. |
| Build-Kit | Verified `636017fb`, 04:07 UTC | 17 protected processes, 3 live gateways/session records, 30 completed turns and 64,446 retained events. |
| Build | Blocked; API remains `f44f62d5` | Its configured four-live-session limit rejected the new maintenance runner. No existing session was stopped or reassigned. |

These are API updates, not claims that existing Skuld/native processes adopted the
new source. Stopped sessions use the selected source on their next explicit start;
none was launched merely to update it. The API phases preserve even live processes
whose database lifecycle rows are terminal. PostgreSQL and Docker were not restarted.

BuildBro's runner subsequently received a separate request in its own conversation
to refresh other active/idle gateways while excluding itself and stopped/archived
sessions. That later operation has a separate owner and result; the table records
preservation at the API cutover, not a promise that no authorized later runtime
refresh occurred.

## Web and runtime visibility

Thor's static UI is published at
[the existing Tailscale URL](https://thor.tail737f2a.ts.net:3001/volundr/).
Nginx was gracefully reloaded with the `636017fb` assets and existing runtime
configuration. Its Build upstream now uses the current `100.117.154.99` address.
A live browser check showed the running broker's old build and available API
source in the new **Runtime update available** popover, with no page errors.

The owning-node `/sessions/{id}/runtime-version` endpoint validates session access
and broker identity, reports failed probes explicitly, and compares loaded source
with available local-process source. It does not refresh a session. Container
backends still need a deployable-image identity contract before reporting a
candidate; see the [container fleet design](fleet-container-deployment-design.md).

## BuildBro replay qualification

The first attempt stopped before candidate startup because the host adapter
rejected Supervisor's exited-process return code. Its qualified safety rollback
recovered. A subsequent candidate reached readiness but failed exact archived
replay equality and was automatically rolled back again. Both attempts remain
failed records; recovery was checked separately.

The difference was exactly ten old transport controls: three and seven trailing
`conversation_history_too_large` errors in two archived sessions. All 260 retained
turns across 11 samples matched every field and order. The host-local acceptance
proof checked each omitted entry's original ledger kind/code, deterministic turn
ID and complete previous projection. Negative checks reject missing real content,
changed metadata, other error codes and quoted notice text.

The final cutover used exact frozen candidate replay hashes only for those two
proved cases; every other replay/process/config guard remained strict. Raw ledger
prefixes remained unchanged, and rollback retained its original full-history
contract. Coordinator readback independently checked health, local inventory,
runtime versions and a full retained archive item. No ledger data was deleted.

## Validation and migration compatibility

- Backend: 19,998 passed, 37 skipped, 174 deselected and 1 expected failure;
  86.63% coverage with the unchanged 85% gate.
- Web: 6,330 tests across 439 files; coverage gates, production build, type checks,
  formatting and push hooks passed. Runtime-version browser and live Thor checks
  passed.
- Candidate and rollback each passed four real-process API restart cases on both
  Thor and Spark. Remote operators additionally qualified their actual Supervisor
  stop/signal paths and host-specific preservation guards.
- Migration `000067_session_read_state` retains deployed SQL unchanged;
  coordination revision is `000068_session_coordination_revision`, with an
  immutable alias for its prior `000067` filename. Existing ledger entries were
  preserved. BuildBro's additive migrations remained after rollback; no destructive
  downgrade was attempted. (Numbers above are this fork's own sequence at the time
  of that deployment. On `dev`, the session-coordination-revision migration
  already shipped as `000074_session_coordination_revision`, so the inbox
  migration merged into `dev` as `000075_session_read_state` with identical SQL;
  the fork's `000068` rename was not carried forward since it duplicated `000074`.)

The native companion is pushed to
[xteo/lexi-ios:feat/forge-grok-guild](https://github.com/xteo/lexi-ios/tree/feat/forge-grok-guild)
at `c5ec528e`, with 366 tests passed. No iOS build number or Apple release changed.

Private process manifests, backup inventories, replay hashes and coordinator
receipts are under `/home/thor/.local/share/niuu/rollouts/forge-guild-20260922/`, with
corresponding evidence on each operated node. No finite sampled replay check proves
every unflushed frame or physical iOS reconnection. Build-Kit has no inactive
history samples; its acceptance uses retained live-history and database prefixes.

The [Forge MCP/skills/notification proposal](mcp-skills-notifications-design.md)
and container fleet proposal are designs. No new MCP service, notification feed,
GBrain deployment or container publication is claimed by this release.
