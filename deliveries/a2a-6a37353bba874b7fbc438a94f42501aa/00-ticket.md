# NIU-1107 — Regin reconciliation

- Tracker: [NIU-1107](https://linear.app/niuu/issue/NIU-1107/add-a-principal-aware-a2a-agent-directory-to-observatory)
- Pull request: [#1171](https://github.com/niuulabs/niuu/pull/1171)
- Base branch: `regin`
- Head branch: `codex/niu-1107-regin-reconciliation`
- Tracker status: `In Progress`

## Operator decision — 2026-10-25

The operator declined scope expansion for the inherited Ting formatting/workflow-lock
and Ravn tool-count failures. They remain separate regressions and must not be
modified or treated as resolved by NIU-1107. Keep this ticket `In Progress`; do
not merge PR #1171 until required CI is green and review confirms acceptance.

## Reconciliation finding

The NIU-1107 implementation commits `4d4abb0f`, `3a2c083d`, and `a992872f` are already ancestors of `origin/regin`; no implementation commit must be brought from `dev`. The `origin/regin` and `origin/dev` merge base is `dee8c851`, and neither tip is an ancestor of the other. Merging the current `dev` tip would bring unrelated changes and is not part of this reconciliation.

PR #1171 itself is based on and targets `regin`: its merge base with the PR
head is `fd3fff955f3953a7769c7099229a576cc408c908` (`origin/regin`), and that
base is an ancestor of head `0087bdee1b73b0613e2040768992a5629cec4963`.
The implementation commits `4d4abb0f`, `3a2c083d`, and `a992872f` are each
ancestors of `origin/regin`; the PR adds reconciliation evidence only.

## A2A discovery guidance recheck — 2026-10-25

The official A2A release page identifies v1.0.1 as latest. The official discovery guide standardizes Agent Cards and the `/.well-known/agent-card.json` location, recognizes curated registries, and explicitly says the A2A specification does not prescribe their API. The Observatory and Guild directory endpoints are therefore Niuu-specific APIs; cards remain standard A2A payloads.

Sources:

- [A2A Agent Discovery](https://a2a-protocol.org/latest/topics/agent-discovery/)
- [A2A specification](https://a2a-protocol.org/latest/specification/)
- [A2A releases](https://github.com/a2aproject/A2A/releases)
