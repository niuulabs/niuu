# NIU-1107 — Regin reconciliation

- Tracker: [NIU-1107](https://linear.app/niuu/issue/NIU-1107/add-a-principal-aware-a2a-agent-directory-to-observatory)
- Pull request: [#1171](https://github.com/niuulabs/niuu/pull/1171) (`codex/niu-1107-regin-reconciliation` → `regin`)
- Starting and delivery branch: `regin`
- Starting `regin` head: `cbcd21fdf56152fcb1b201495b2c5fb53966af6d`

## Reconciliation decision

The product implementation is already on `regin`, rather than in PR #1171's
source diff.  Its commits are
[`4d4abb0f`](https://github.com/niuulabs/niuu/commit/4d4abb0f382f21022c6a7afe74f7f30e3c0bcae1),
[`3a2c083d`](https://github.com/niuulabs/niuu/commit/3a2c083d19cf01dc0dc86da21ccc0805976b91e9), and
[`a992872f`](https://github.com/niuulabs/niuu/commit/a992872ffbf0d8688a0bc4d6914cbcbee15219af).
Their GitHub file views are the inspectable complete product change lists.

The open PR's current diff is explicitly evidence/docs only: [changed files](https://github.com/niuulabs/niuu/pull/1171/files).
It must not be mistaken for the implementation itself.

`origin/dev` (`f75d5b0b824df5e11741b4d9c2f5c64852b531fe`) is an ancestor of
`regin`, at merge base `f75d5b0b824df5e11741b4d9c2f5c64852b531fe`; `regin...dev`
was `33 0`. Therefore there is no unmerged `dev` fix or divergent baseline failure
to merge in this delivery, and no dev-vs-regin CI failure comparison applies.
