# NIU-1112 — A2A gate continuation via INPUT_REQUIRED replies

- Delivery slug: `a2a-c8cf280c5d53430490670bed3ebf93f3`
- Tracker: [NIU-1112](https://linear.app/niuu/issue/NIU-1112/a2a-gate-continuation-via-input-required-replies)
- Ticket UUID: `75faa76e-4b65-4725-8826-787afd1f5728`
- Integration branch: `regin`
- Reconciliation PR: [#1173](https://github.com/niuulabs/niuu/pull/1173), merged into `regin`

## Reconciliation result

The prior delivery did not require a duplicate implementation. PR #1173's merge
commit, `cbcd21fdf56152fcb1b201495b2c5fb53966af6d`, was the reconciled
`origin/regin` head before this evidence-only commit. Its parents are the pre-PR `regin` head
`b746f9d5bfbf261156cc5e2255684b4dc810c814` and the PR head
`079b2be10ca7c922c8fb71831a3b76163e33865a`; both implementation and review
commits are therefore in the merged ancestry.

The implementation is already on `regin`: Ting accepts `SendMessage` replies
for `INPUT_REQUIRED` tasks, maps `metadata.gateDecision` to the configured
Skuld decision, forwards the message text as reviewer notes, and rejects other
task states as JSON-RPC invalid parameters. The A2A card and operator guide
state the reply convention.

`origin/dev` is an ancestor of `origin/regin` at this reconciliation, so there
was no newer dev repair to merge into `regin` and no branch was merged in the
prohibited direction.
