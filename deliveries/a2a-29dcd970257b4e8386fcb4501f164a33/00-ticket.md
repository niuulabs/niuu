# NIU-1112 — A2A gate continuation via INPUT_REQUIRED replies

- Delivery slug: `a2a-29dcd970257b4e8386fcb4501f164a33`
- Tracker: [NIU-1112](https://linear.app/niuu/issue/NIU-1112/a2a-gate-continuation-via-input-required-replies)
- Ticket UUID: `75faa76e-4b65-4725-8826-787afd1f5728`
- Required integration branch and PR base: `regin`
- Tracker status: `In Progress`

## Reconciliation

This is a reconciliation of the prior delivery, not a new implementation.
`regin` and `origin/regin` started at
`b746f9d5bfbf261156cc5e2255684b4dc810c814`. The branch contains the earlier
NIU-1112 implementation and verification history. In particular:

- `c1253e9d` contains the original gate-continuation implementation.
- `5a6f2b00` rejects ambiguous replies when more than one gate is pending.
- `06c298ac` commits the parameterized A2A-to-Skuld terminal-completion test.
- `0b83699d` and `b6639c1c` are the preexisting direct-to-`regin` test-isolation
  commits; `b6639c1c7dc5b7f2107d9e8a716a8fc57545d47f` is the reported
  persona-ASGI/ambient-workload-identity isolation push.

`origin/dev` at `f75d5b0b824df5e11741b4d9c2f5c64852b531fe` is an ancestor of
`regin` through `82f6384c` (`chore: merge dev into regin`). Therefore no
additional `dev` repair exists to merge into `regin`, and nothing was merged
outward to `dev` or `main`.

## Acceptance scope

- An A2A `SendMessage` reply to an `INPUT_REQUIRED` task translates
  `metadata.gateDecision` (`approve` or `request_changes`) to the configured
  Skuld gate event; message text is passed as the reviewer comment.
- Other task states receive a clear JSON-RPC invalid-params error.
- Agent-card skill descriptions and the operator guide document the reply
  convention.
- The A2A-only test covers both decisions and terminal completion.
