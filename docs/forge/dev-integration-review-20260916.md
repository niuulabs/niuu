# Forge / Codex: development integration review

September 16, 2026. Prepared for Joseph's internal review at the user's request.
**Source integration only; Thor remains unchanged and deployment remains on HOLD.**

## Review target and scope

Repository: `xteo/niuu`, remote branch **`forge/dev-integration`**.

The freshly fetched integration tip was `f44f62d5d309aa8f233744dd2a44f53cb04b2c8b`.
The dedicated runtime branch at `3b4ff1976cc29adc7e4dbb539785db20cddb3cf0`
already contained that entire history, plus **39 commits**. This is a straight
fast-forward integration: no existing integration commits are dropped, rewritten or
conflict-resolved. This review document and its evidence summary are the only new
tracked changes made for integration; product source remains exactly the tested tree.
The remote push/readback result is recorded separately in the final project receipt.

Included: this Forge/Codex runtime lane and its previously pinned Project context
and live-steering integration (`11e9e0c6`). Not included: other runners' unfinished
branches, native iOS/Mac changes, or untracked files in the shared development
checkout. That shared checkout is deliberately not reset or pulled while other
work uses it. The remote integration branch is the review target, not a claim that
every local checkout or live server has updated itself.

### Main changes to review

| Area | Improvement | Main reference |
|---|---|---|
| Assistant/tool interleaving | Keep public explanations between tools, stable item identities, scoped concurrent blocks and correct completion/error state. | [Codex release notes](codex-release-notes-20260913.md) |
| Command and native protocol fidelity | Preserve command arguments/output, native tool outcomes, approval/question lifecycle, model/effort options and explicit recovery identity. | [Integration review](codex-integration-review-20260913.md) |
| Steering | Use live `turn/steer` rather than implicit interrupt/replacement; preserve the exact request and native turn correlation. | [Routing contract](../design/forge-projects/live-steering-routing.md) |
| Chronology and archived replay | Preserve steering insertion chronology and project timed filesystem archives consistently. | [Timeline](codex-steering-timeline-20260914.md), [archive follow-up](codex-live-dedup-followup-20260914.md) |
| Large histories and reconnects | Bound recent history by rows and bytes; stable older/refresh cursors, explicit large-item references, typed recovery controls, sender-only ingress and WebSocket pump cleanup. | [Protocol v2](bounded-history-protocol-20260914.md) |
| Local deployment preservation | Identify the real local-process backend, preserve gateways across API startup/reconciliation, and fail closed in guarded release tooling. | [API release procedure](local-api-release.md) |
| Generic Projects infrastructure | Persistent projects, receipts, parent links, CLI and repository-selected launch context; workflow remains in project instructions. | [Projects implementation](../design/forge-projects/IMPLEMENTATION.md) |

Slash-command expansion is intentionally **deferred**, not silently missing from
this integration: [future command capabilities](codex-future-command-capabilities.md).

## Fresh validation

A new isolated sweep on the exact source above passed:

- **5,378 passed**, 24 skipped, 95 deselected, one expected failure; exit zero.
- Warnings treated as errors; zero warnings.
- **85.14% scoped coverage**, branch measurement enabled, unchanged 85% gate.
  Coverage is over the same 17 explicitly reported server modules, not the whole
  monorepo. Codex, Projects, adapters, services and lifecycle regression tests are
  included in the broader selected test run.
- Ruff lint and formatting passed for **all 80 changed Python files** since the
  integration base, not only the latest bounded-history patch. `git diff --check`
  also passed.
- Eighteen runtime-module imports were verified to originate from this owned
  checkout rather than the inherited live-server `PYTHONPATH`.
- Four provider-free, real-process lifecycle cases passed for running, stopped,
  archived and failed stored statuses with live gateway fixtures. Each survives
  graceful and abrupt **test API** termination, two proxy reconnections, the same
  gateway/native identity, output produced during the gap, unchanged stored status
  and exactly one native input send.

[Machine-readable evidence](dev-integration-review-20260916.json) includes exact
source/tree IDs, pytest arguments, the coverage configuration, source import audit
and private log checksums. Test process fixtures use temporary homes, isolated
ports/state and mocked database/model infrastructure. They do not restart Thor,
call model providers or interact with another person's sessions.

This is not a fresh live PostgreSQL, Helm, provider, physical-device or whole-repo
CI run. Existing integration/broker/kind/live-CLI exclusions remain explicit.
The repository's ordinary CI is PR-triggered; the Forge Stability push pattern
matches `dev-integration`, **not** `forge/dev-integration`. A branch push alone
must not be described as a passed GitHub CI run. No workflow, tag or release was
manually dispatched here.

## What upgrading means for your existing sessions

**No: the repaired API-only release is not supposed to stop all sessions or make
you choose which ones to resurrect.** These are three different operations:

| Operation | What changes | Effect on existing sessions |
|---|---|---|
| Push source / update a CLI checkout | Git or future CLI invocations | Does not deploy the API, stop gateways or upgrade already-running sessions. This task only pushes source. |
| Guarded local API-only upgrade | Forge HTTP/REST and WebSocket proxy process; source used for new gateways | Keep existing gateway/native processes and stored identities/statuses intact. Proxy connections disconnect briefly; clients reconnect to the same sessions. Already-stopped/archived sessions are not automatically resumed. |
| Upgrade an existing Skuld/Codex gateway | That session's loaded runtime adapter | Separate owner-approved saved-boundary operation. Not performed by the API-only procedure, and not safe to hide inside a blanket restart. |

The previous failed cutover was a startup cleanup misclassification: local
processes were treated using the wrong backend cleanup semantics. The integrated
fix and lifecycle tests address that defect. They do **not** make a single-process
API restart seamless or prove that every possible in-flight byte survives.

Fresh read-only Thor checks still show the existing API PID **61138**, revision
`c6d80980`, `KillMode=process`, `CanReload=no` and no socket-activation trigger.
Its existing API/proxy connections cannot be handed transparently to a replacement
through the current release path. A short API/stream/control interruption is still
required; a full stack stop/restart is not the approved procedure.

Existing gateways intentionally keep their old loaded code after an API-only
upgrade. That preserves the work in progress, but means the newer **gateway-side**
Codex fixes immediately apply to new sessions, not magically to every open session.
API-side replay/paging improvements can help retained gateways without replacing
those processes, within the documented old-gateway limitations.

**No deployment is performed by this integration.** Before any later cutover we
still need a fresh preservation inventory, exact candidate and safety-patched
rollback validation, and an explicitly accepted API/reconnect maintenance window.
No bulk stop/start, native input resend, database reset or Tailscale restart.
See the [zero-disturbance assessment](thor-zero-disruption-hold-20260914.md).

## Remaining limits, not hidden completion claims

- Older retained gateways still materialize their upstream full REST response;
  bounded delivery does not eliminate all upstream memory/scan costs.
- DB-only recovery still has the existing newest-50,000-frame ceiling. Complete
  DB-native older-history backfill is not implemented by presentation cursors.
- End-to-end trusted human/agent authorship is not implemented. Ordinary message
  role and text prefixes are not authenticated origin; receipts do not wake or
  inject instructions. The September 15 mechanism audit remains an assessment,
  not an implementation commit in this release.
- Native UI deduplication/decoder/recovery acceptance belongs to its separate lane.
  This source push is not an iOS release or proof of the current phone's behavior.
- Spark previously received `650088f3`; the later bounded-history candidate is not
  claimed deployed there or on Thor. No runtime source was switched for this task.
