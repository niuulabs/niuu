# NIU-1107 — Regin reconciliation handoff

- Tracker: [NIU-1107](https://linear.app/niuu/issue/NIU-1107/add-a-principal-aware-a2a-agent-directory-to-observatory)
- Pull request: [#1171](https://github.com/niuulabs/niuu/pull/1171)
- Source / target: `codex/niu-1107-regin-reconciliation` → `regin`
- Tracker status: `In Progress`
- Evidence captured: 2026-10-25

## Reconciliation facts

The NIU-1107 implementation is already on `regin`: commits `4d4abb0f`
(`feat(observatory): add principal-aware A2A agent directory`) and `3a2c083d`
(`fix(observatory): harden agent directory identity`) are ancestors of the target.
`origin/dev` at `f75d5b0b` is also an ancestor of `origin/regin` at `b746f9d5`;
the reverse is false. Merging `dev` into `regin` would therefore be empty and was
not performed. PR #1171 originally contained delivery evidence and a two-line
documentation correction, not missing NIU-1107 product code.

This pass rebased PR #1171's source on `b746f9d5` so its next CI run evaluates the
current `regin` aggregate rather than the former base `0b83699d`. No PR targets
`dev` or `main`, and no target branch was promoted or directly changed.

## A2A guidance check

The official A2A specification and discovery guide recognize curated
registries/catalogs but define no registry API. The standard card discovery path
is `/.well-known/agent-card.json`; the repository uses the official `a2a-sdk`
(`a2a-sdk>=1.1.2,<2`) to parse cards. Consequently the local Observatory and
Guild aggregate routes remain documented Niuu-specific directory APIs, while
cards retain standard discovery, cache, signature, interface, and access-control
semantics.

- [A2A specification](https://github.com/a2aproject/A2A/blob/main/docs/specification.md)
- [A2A agent discovery](https://github.com/a2aproject/A2A/blob/main/docs/topics/agent-discovery.md)
- [A2A releases](https://github.com/a2aproject/A2A/releases)

## Delivery gate

Before this rebase, PR #1171's head `b4669afa` had 56 completed checks with no
failed or pending check, including the required CI gate:
[CI gate](https://github.com/niuulabs/niuu/actions/runs/40164277934/job/121083111367),
[Python suite](https://github.com/niuulabs/niuu/actions/runs/40164277934),
[Forge stability](https://github.com/niuulabs/niuu/actions/runs/40164277939), and
[web checks](https://github.com/niuulabs/niuu/actions/runs/40164277934).

The rebase creates a new PR head, so that historical green result is not evidence
that the new head is green. PR #1171 has no submitted review (`reviewDecision` is
empty). Do not merge, close NIU-1107, or unblock NIU-1116 until the rebased head's
required checks are green and an authorized reviewer approves it.
