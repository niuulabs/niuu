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
