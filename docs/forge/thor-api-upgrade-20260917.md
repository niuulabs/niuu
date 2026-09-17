# Thor API upgrade — sessions preserved

September 17, 2026. **Deployed successfully; no rollback.** Direct user authority
accepted a brief API/reconnect interruption while preserving existing session
processes. This was not a machine reboot or a gateway migration.

## Actual result

- API candidate **45ea08f69bca5fb8c63ed2ef4d98bcf707aa2029**, the latest remote
  `forge/dev-integration` product source at cutover.
- Build `forge-codex-ready-20260917-v1`; source digest
  `1f1156a7d0a9063ffb97e36678d410f58ace879851b9c3584e03296defba4c1c`.
- Frozen source/environment:
  `/home/thor/.local/share/niuu/releases/forge-codex-ready-20260917-v1`.
- Announced window **14:15–14:30 UTC**. Guarded apply ran 14:15:33–14:15:55 UTC.
  Independent preservation readback passed at 14:17:03 UTC.
- API health sampling observed an approximately **18-second HTTP interruption**:
  first failed sample 14:15:36, first new-build success 14:15:54. This is not a
  measurement of every client's WebSocket reconnect time.
- API PID changed from 61138 to 370108; `KillMode=process` remained unchanged.
  Candidate health is clean with no failed plugins. No automatic restart loop.
- **All 13 preexisting local gateways and all 40 protected process identities
  survived unchanged**, including native CLI/tmux owners and both database
  postmasters. No existing session received stop/start, resume, input or approval.
- **All 663 aggregate session records retained their statuses and the checked
  identity/configuration fields**. This includes other-host records; it is not
  a claim that 663 CLI processes run locally. The 13 local gateways correspond to
  five running, six archived and two stopped records. Existing discrepancies were
  preserved, not used as permission to clean up or resurrect sessions.
- Both archived samples retained exact hashes of their 20- and 96-turn payloads.
  Agent HTTP 9500, trusted HTTPS 9501 and the unchanged web preview on 5300 passed.
  Original unit/drop-in/config/launcher files were unchanged; only the single
  source-selection drop-in for this release was added.

The current runner continued its existing native turn across this cutover. This
does not substitute for a physical iOS reconnect test or prove every unflushed
byte from every process. No Tailscale, database, Voice/Live service or other host
was restarted.

## What is now available

The central API now contains the integrated replay/paging/reconnection and
preservation improvements. New local sessions are configured to launch from the
new isolated source/environment, which includes the Codex steering,
assistant/tool-interleaving and command-fidelity improvements, plus the Claude tmux
single-key fix. Existing gateways intentionally retain their old loaded code.
This release did not create a provider-backed canary session or migrate one.

See [feature review](dev-integration-review-20260916.md),
[Codex release notes](codex-release-notes-20260913.md), and
[bounded-history contract](bounded-history-protocol-20260914.md). Slash-command
workflow expansion remains deferred; tmux catalog-input interference remains an
open defect, not something this release claims fixed.

## Session runtime versions after the API upgrade

These are direct session-health reads, confirmed unchanged before/after cutover.
None of these retained runtimes matches the new server's source digest; that is
expected preservation behavior, not a failed API deployment. A dirty/custom source
needs individual review rather than automatic replacement.

| Local gateway | Stored Forge status | Loaded revision |
| --- | --- | --- |
| lexi-coordinator | running | 673b5000 (dirty source) |
| lexi-web-chat-restoration | archived | 49ed405f |
| physics-coordination | stopped | c6d80980 |
| physics-architecture-review | archived | c6d80980 |
| physics-delivery-review | archived | c6d80980 |
| lexi-forge-niuu-management | running | c6d80980 |
| physics-gpt-live-upgrade | archived | c6d80980 |
| physics-pmt-capacitance | archived | c6d80980 |
| physics-pmt-magnetic-fields | archived | c6d80980 |
| lexi-codex-subagents-workflow | stopped | c6d80980 |
| lexi-macos-interface-parity | running | c6d80980 |
| lexi-ios-chat-voice-ux-improvement | running | c6d80980 |
| niuu-ux-improvements | running | c6d80980 |

A session on another host must be inspected/upgraded against that owning host.
A running record in Thor's aggregate is not proof of a local gateway. No native
UI version badge or unattended migration scheduler was implemented here.

## How to upgrade one selected paused session

The [operator guide](session-runtime-upgrades-20260917.md) gives the exact existing
read and stop/resume routes, identity/history checks and failure rules.

In brief: hold new input, establish genuine native quiescence, record the native
thread ID and durable history, stop/resume that one **same Forge ID**, verify the
new source and same native conversation, then release input. This deliberately
restarts that one engine and briefly changes its lifecycle status.

**Do not automate on an idle flag alone.** Current stop/resume are separate calls,
not an atomic check-and-upgrade operation. A race-free unattended workflow still
needs an ingress fence/drain and durable conditional upgrade semantics. No existing
session was selected for migration merely because it looked idle.

## Validation and evidence

Fresh staged candidate: **5,387 passed, 24 skipped, 95 deselected, one expected
failure**, zero warnings under warnings-as-errors. Unchanged 85% gate passed at
**85.14%** over the same 17 reported server modules with branch measurement enabled;
not whole-repository coverage. Candidate and safety rollback each passed all four
real-process API restart/reconnect cases. The rollback remains staged and was not
used. Both release environments import their exact sources and have equal dependency
sets; eighteen candidate runtime modules were audited.

[Machine-readable result](thor-api-upgrade-20260917.json) records pins, sampled
outage, validation scope and version inventory. Private manifest, preparation,
apply, independent readback and notification evidence:
`/home/thor/repos/worktrees/niuu-forge-runtime-management-20260913/.local/thor-api-upgrade-20260917`.

The later documentation commit does not alter the deployed product source digest.
