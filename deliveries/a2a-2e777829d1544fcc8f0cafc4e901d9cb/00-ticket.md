# NIU-1112 — A2A gate continuation via INPUT_REQUIRED replies

- Delivery slug: `a2a-2e777829d1544fcc8f0cafc4e901d9cb`
- Tracker: [NIU-1112](https://linear.app/niuu/issue/NIU-1112/a2a-gate-continuation-via-input-required-replies)
- Required source and integration branch: `regin`
- Hosted PR: none; this delivery is integrated directly on `regin` as required.

## Tracker evidence

The required tracker shim resolved the ticket to
`75faa76e-4b65-4725-8826-787afd1f5728`; it was already `In Progress` when
this delivery began.

```sh
./.skuld-tools/bin/tracker_issue search "NIU-1112"
./.skuld-tools/bin/tracker_issue get "75faa76e-4b65-4725-8826-787afd1f5728"
```

## Acceptance scope

- An A2A `SendMessage` reply on an `INPUT_REQUIRED` task accepts
  `metadata.gateDecision` as `approve` or `request_changes` and uses message
  text as the reviewer comment.
- Ting routes the normalized decision through its Volundr port to Skuld's
  configured gate resolution, which emits the gate's `approvalEvent` or
  `changesRequestedEvent`.
- Replies outside `INPUT_REQUIRED` return a clear JSON-RPC invalid-params
  error.
- Agent Card skill descriptions and the A2A operator guide document the
  reply convention.

## Context and integration

The underlying gate-continuation implementation is already present in
`c1253e9d` (`feat(ravn): deliver the resident judgment and evolution loop`),
whose commit message explicitly records NIU-1112. This delivery adds the
missing per-skill Agent Card description proof and integrates the current
`origin/dev` into `regin` because the requested prerequisite check found green
current dev CI after the earlier unrelated Ting/Ravn/lint failures.

The requested A2A-cluster context is cited at
`deliveries/2026-10-18-regin-a2a-cluster-inflight.md`; live tracker and branch
state above take precedence.

## 2026-10-25 verification follow-up

NIU-1112 remains **In Progress**. The operator declined both a waiver and any
unrelated production persona change. The required current-head verification is
not green, so this delivery is not closed.

At investigation start, `HEAD` and `origin/regin` were both
`66c3d35ee6beffee978ee28ee6a5adb64888b851`; `origin/dev` was
`f75d5b0b824df5e11741b4d9c2f5c64852b531fe`. `origin/dev` is an ancestor of
HEAD through merge `82f6384c`; no merge out of `regin` occurred.

The current `regin` head retains that ancestry and adds test-only isolation for
the coding worker's mounted service-account token. It does not change persona
or platform-tool production behavior. NIU-1112 remains In Progress pending a
green current-head CI run. The post-isolation full suite passed 27,596 tests
at 88.47% coverage but has six unrelated failures recorded in
`10-implementation.md`.

## 2026-10-25 review revision

Review finding that the A2A-only completion coverage was not committed is now
resolved by `06c298ac21b59e71d7f6d8bd55c956b202ffc667`
(`test(ting): cover A2A gate completion`) on `regin` and `origin/regin`. The
parameterized test drives both `approve` and `request_changes` through Ting's
HTTP adapter, Skuld's workflow runtime, and Ting's campaign projector, then
asserts `TASK_STATE_COMPLETED`.

The focused verification at that exact head passed 116 tests. The repository's
`CI` workflow is `pull_request`-only, while this regin-only delivery is
integrated directly on `regin`; `gh run list` therefore returns no current-head
CI run. The previous full suite's six unrelated failures remain the blocking
condition. No waiver or unrelated repair is recorded, so the ticket remains
**In Progress** rather than being closed.
