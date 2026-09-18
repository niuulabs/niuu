# iOS voice and coordinator: supervised Skuld refresh

September 17, 2026, Thor. The user explicitly requested upgrades of
`lexi-ios-chat-voice` and `lexi-coordinator`, stating they were inactive. The exact
iOS session is `lexi-ios-chat-voice-ux-improvement`. Both were upgraded one at a
time onto Thor's deployed **forge-codex-ready-20260917-v1** and are running/idle.
No central API, database, Tailscale or unrelated session restart was performed.

## Selected identities and preserved work

| | iOS voice | Coordinator |
| --- | --- | --- |
| Forge ID | `c8103dc7-a834-5e32-a06d-1a98d7a8e2b5` | `8f20102d-6da7-58aa-98c2-e0bce2deab97` |
| Native Codex thread | `01a0a982-d791-7fa2-9fe3-e400590f1fb5` | `01a092a1-ddbe-7e00-9e16-a5db2861f376` |
| Workspace | `/home/thor/repos/worktrees/lexi-ios-chat-voice-ux-20260916` | `/home/thor/projects/lexi` |
| Old gateway revision | `c6d80980`, clean | `673b5000`, modified startup source |
| Preserved replay rows | **26**, deeply equal | **291**, deeply equal |
| Unchanged native rollout bytes | **20,679,572** | **107,113,301** |
| Tracked workspace files unchanged | **4,510** | **446** |
| Nonignored untracked files unchanged | **1,247** | **1** |

Both retain their Forge/native identities, names, `skuldCodex` definitions,
**Astra/xhigh** options, workspaces, project/parent metadata and ownership.
Git HEAD, preexisting dirty status and all inventoried file bytes/symlink targets
are unchanged. Existing unfinished work was not reset, staged or committed.
Neither native rollout received any appended records. No prompt, steering,
approval answer, test model turn or old input was sent/replayed.

Both new gateways report clean loaded revision
`45ea08f69bca5fb8c63ed2ef4d98bcf707aa2029`, source SHA-256
`1f1156a7d0a9063ffb97e36678d410f58ace879851b9c3584e03296defba4c1c`,
matching the central Thor API. This replaces the coordinator's previously modified
runtime source with the validated release; it does not edit its source checkout.

## Safety and actual timeline

The central session list had stale active-turn hints for both sessions, and an old
`Reconnecting... 2/5` error on the coordinator. These hints were not used as idle
proof. Each live gateway reported idle; native transcripts ended in `task_complete`
(iOS September 17 14:25:24; coordinator September 16 09:21:02), with no open native
turns, pending questions, permissions or reported agents. Each process tree held
only its gateway, native app-server and persistent code-mode child. Fresh checks
immediately before each stop confirmed the transcript and identities had not changed.

| UTC, September 17 | iOS voice | Coordinator |
| --- | --- | --- |
| Preflight passed / stop requested | 21:13:30 | 21:14:19 |
| Stop verified, old three target processes exited | 21:13:30 | 21:14:21 |
| Same-ID resume accepted | 21:13:34 | 21:14:21 |
| Preservation/reconnect checks complete | 21:13:58 | 21:14:28 |

Each session received exactly one stop and one resume request, with saved intents
before mutation. An early iOS health read returned 502 while startup was underway;
read-only follow-up verified successful native resume, without retrying the start.
Every refresh preserved all **37 other protected process identities** in its fresh
baseline, including the central API and previously upgraded macOS session. The
coordinator baseline also included the newly upgraded iOS gateway/native processes.
Two process identities from the earlier macOS-task snapshot were already
absent before this request's first mutation; that prior observation was not silently
reused as the current baseline. Their absence and observation time are saved privately.

New activity reports are **running / idle**, with no active turn or error. Native
runtime-options readback confirms Astra/xhigh; no corrective settings mutation was
needed. This is supervised runtime replacement, not uninterrupted execution or an
implementation of atomic unattended idle upgrades.

## Replay and reconnect validation

- All 317 selected historical replay rows retain their exact IDs/content/fields.
- Both native rollout files are byte-identical before and after resume.
- Read-only WebSocket reconnects received handshake, capabilities and bounded
  recent `conversation_history` frames. No application input frames were sent.
- Explicit REST protocol 2 reads acknowledged protocol 2 with cursors/window
  metadata: iOS **246,087 bytes / 8 rows**; coordinator **256,477 bytes / 15 rows**,
  both within the requested 262,144-byte bound.
- No visual iOS/macOS acceptance or new provider-response turn was attempted.
  Both sessions are ready for the user to reopen and continue normally.

Startup retained existing local replay caches rather than replacing them with an
incomplete durable projection: the iOS cache had unmatched legacy turns (as in the
[macOS parity refresh](macos-parity-skuld-refresh-20260917.md)); the coordinator's
durable history exceeded the configured hydration byte budget. Missing optional
present-file registry warnings also occurred. Exact replay equality establishes
preservation, not a claim that old projection defects or the full-history hydration
limit were repaired. No history rewriting or deletion was performed.

## Evidence and ownership

Private evidence root (conversation/workspace data, not committed):
`/home/thor/repos/worktrees/niuu-forge-runtime-management-20260913/.local/ios-coordinator-refresh-20260917/`.
Each `ios/` and `coordinator/` directory contains before/after snapshots,
`preflight-passed.json`, saved stop/resume requests/results, `gateway-startup.log`,
`verification.json`, `protected-final.json` and `ws-reconnect-check.json`.

Operator: `thor:4ab99660-05e8-52c3-90d3-85d7463ce8a2`. Passive receipt to
`thor:8f20102d-6da7-58aa-98c2-e0bce2deab97`; no injected instruction or wake.
This report changes no product code or shared project checkpoint.
