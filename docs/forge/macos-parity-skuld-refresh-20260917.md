# macOS parity session: supervised Skuld refresh

September 17, 2026, Thor. The user explicitly selected
`lexi-macos-interface-parity` for refresh onto the latest deployed Codex Skuld.
Only this session was stopped and resumed; the central API was not restarted.

## Result

| Identity | Preserved / current value |
| --- | --- |
| Forge session | `523eb3cd-c325-5aa4-a162-7927438eb61e` |
| Native Codex thread | `01a0a1ed-f93c-7dd3-8312-8dca84510ca1` |
| Workspace | `/home/thor/repos/worktrees/lexi-macos-parity-20260914` |
| Definition | `skuldCodex` |
| Model / effort | `gpt-6-astra` / `xhigh`; native runtime-options readback |
| New gateway | PID `765507`, local port `9106` |
| New native app-server | PID `765560`, CLI `0.154.0` |
| Loaded release | `forge-codex-ready-20260917-v1` |
| Loaded revision | `45ea08f69bca5fb8c63ed2ef4d98bcf707aa2029`, clean |
| Loaded source SHA-256 | `1f1156a7d0a9063ffb97e36678d410f58ace879851b9c3584e03296defba4c1c` |

The old clean gateway was `c6d80980` / `forge-project-discovery-44f85dc3`.
The new gateway's source digest exactly matches Thor's deployed API release.
This upgrades the session adapter, not the Codex CLI package or its model.

## Operation and checks

- Fresh preflight at **20:39:44 UTC**: completed native turn, unchanged native
  rollout, idle session, no pending questions/permissions or reported agents,
  unchanged target process identities and all 39 other protected owners present.
- Saved private snapshots of full replay, native rollout, workspace file hashes,
  Git status/diffs, configuration and process identities before mutation.
- One `POST /sessions/{id}/stop`; stopped by **20:39:45**. All three old target
  processes exited. No blind retry, signal to another owner or shared restart.
- One `POST /sessions/{id}/resume` at **20:39:50**. Startup explicitly resumed
  the **same native thread at 20:39:56**, then reported idle. No fresh conversation,
  prompt, steering, approval answer or test model turn was sent.
- All **72 replay rows are deeply equal** before/after, including IDs and content.
  The **44,746,418-byte native rollout is byte-identical**; nothing was appended.
- All **4,311 tracked workspace files** retain their bytes/symlink targets; Git
  HEAD and preexisting dirty status are unchanged. Unfinished work was not reset,
  staged or committed. Only ordinary ignored runtime artifacts may change.
- Forge ID, name, native ID, workspace/source, model, coordination metadata,
  ownership and creation time are unchanged. Final status is **running / idle**,
  with no active turn or attention/error state. Native options still say xhigh;
  no corrective settings mutation was needed.
- All **39 other protected process identities** (PID/start ticks/boot/argv digest)
  remained unchanged. Old target processes are gone; new gateway health is clean.
- A read-only WebSocket reconnect received handshake, capabilities and a bounded
  `conversation_history` frame (**200,445 bytes**, six recent rows). No application
  input frame was sent. Explicit REST `history_protocol=2` returned protocol 2,
  six rows and cursor/window metadata in **200,960 bytes**.

This is operational resume/replay/connection proof, not a new provider-response
test or a visual iOS/macOS UI acceptance test. The user can reopen the same session
and continue normally. The selected session had about 12 seconds of runtime
replacement; this was not uninterrupted execution of that session.

## Retained legacy-history caveat

Startup logged that durable-history hydration could not safely reconcile unmatched
legacy local turns, so it **preserved the existing local cache**. The equality and
native-rollout checks above establish preservation; they do not claim every old
projection has been rebuilt using new interleaving semantics. A missing optional
present-file registry also produced a startup warning. Neither prevented native
resume or replay. No history deletion, repair or rewriting was attempted here.

The current APIs still lack an atomic per-session ingress drain/idle-upgrade
transaction. This was the explicitly selected supervised refresh, not authorization
for unattended upgrades or replacement of any other session. See
[the gradual-upgrade procedure](session-runtime-upgrades-20260917.md).

## Evidence

Private local evidence (contains conversation/workspace data; not committed):
`/home/thor/repos/worktrees/niuu-forge-runtime-management-20260913/.local/macos-parity-refresh-20260917/`.
Key records: `preflight-passed.json`, saved stop/resume intents and HTTP results,
`gateway-startup.log`, `verification.json`, `protected-final.json`,
`ws-reconnect-check.json` and `history-protocol2-after.json`.

Operator: `thor:4ab99660-05e8-52c3-90d3-85d7463ce8a2`.
Parent: `thor:8f20102d-6da7-58aa-98c2-e0bce2deab97`.
