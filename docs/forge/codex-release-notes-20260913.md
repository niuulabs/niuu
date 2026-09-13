# Codex / Skuld release: what improves

## User-visible changes in the candidate

| Your reported problem | What changed | What you should notice |
| --- | --- | --- |
| Assistant explanations disappear between commands | Preserve separate public commentary/final items, their identities and ordering across tool events, live channels and replay. Scope concurrent streaming blocks instead of closing whichever block happened to be last. | You can follow the explanation before, between and after tools rather than seeing only a wall of commands. |
| Tool details are incomplete or misleading | Preserve exact command arguments, incremental/final output and native completion/error status for commands, file changes and MCP tools. Upsert repeated starts/completions instead of duplicating input or executing observed tool notifications again. | What Forge shows agrees with what Codex actually ran; failed tools are not silently presented as successful. |
| Steering differs from native Codex | Route input during an active turn through native `turn/steer`, retain turn/request correlation, and distinguish it from interrupt, replacement and queued next-turn input. | Add direction while work continues without cancelling and restarting the turn. Acceptance means native delivery, not a claim that the task is finished. |
| Reconnecting loses useful controls | Retain pending approvals/questions for disconnected clients, distinguish one-time/session approval and denial, clear controls resolved natively, and handle native server requests without deadlocking the message reader. | A disconnect does not silently answer or discard the question. Controls after actual native-process loss can correctly become non-answerable rather than pretend to work. |
| Model/effort controls do not reflect the running engine | Discover Codex's native model/effort/service-tier options; validate and acknowledge settings together through native thread settings. Preserve next-turn choices separately from the actual running model, including reroutes. | Settings are real, supported choices—not guessed menus or silent substitutions. The new catalog UI is optional iOS work, not included in this server release. |
| Recovery risks starting another conversation | Fail explicitly rather than silently switch from the app server to a different execution path. Verify native thread identity on resume and retain compatibility with native persisted history. | Recovery keeps the intended conversation or reports a concrete problem; it does not quietly create a replacement. |

Retry notices remain nonterminal, failed turn completion is scoped/idempotent,
and current-turn usage is not replaced with cumulative totals. Existing supported
command mappings also retain native arguments and errors. These are correctness
improvements, not a claim that every Codex app-server feature is implemented.

## Evidence already obtained

Before the attempted rollout, an owned native Codex run exercised two real shell
commands, separate public explanations between them, and accepted live steering in
the **same turn**, comparing native items with normalized/replayed public output.
Native model discovery/settings acknowledgement also passed. A separate owned
Codex process-recovery test resumed the same thread and recalled prior context.
These were authorized provider tests, not tests of unrelated live sessions.

Thor's native proof used Codex 0.154.0; Spark's configured native binary was 0.153.4.
This release does not silently replace the host's Codex binary or authentication.
The code and test evidence are described in the [integration review](codex-integration-review-20260913.md).

## Deployment repair

The first Thor cutover exposed a pre-existing wrong-backend startup cleanup bug.
The release now includes explicit local-process backend identification, plus
real-process startup/reconnection regressions and tested fail-closed deployment
tooling. A separate preservation-safe rollback source includes the same safety fix
so rollback cannot knowingly reintroduce that cleanup defect.

See the [local API release procedure](local-api-release.md) for exact behavior,
validation limits and adoption requirements. The original [incident report](codex-rollout-incident-20260913.md)
remains intact; later test success does not erase its impact.

## What is not changing

- **Slash-command expansion/redesign is deferred**, as requested. Its inventory and
  design questions are in [future command capabilities](codex-future-command-capabilities.md).
- **No iOS source, build or release** is included. Existing message envelopes are
  retained; [native-client guidance](codex-ios-runtime-handoff.md) covers QA and any
  optional catalog/form UI work for a separate session.
- No Voice/Live, dictation service, web preview, authentication or database rollout.
- No automatic restart of existing session gateways. An API upgrade preserves them
  on their loaded source; adopting new gateway code requires a saved-boundary,
  owner-approved transition. New sessions can use the new code after API cutover.

## Current release status

The repaired candidate and safety-only rollback are **prepared and staged on both
hosts**, with validation and current preservation checks recorded in the
[release-readiness checkpoint](codex-release-readiness-20260913.md). Thor remains
on its restored old API; Spark retains the earlier API canary. A new coordinated
maintenance window and fresh guards are still required before activation. No
successful Thor deployment is claimed by these release notes.
