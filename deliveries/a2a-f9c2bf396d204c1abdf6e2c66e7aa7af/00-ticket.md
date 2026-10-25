# NIU-1107 — Regin reconciliation handoff

- Tracker: [NIU-1107](https://linear.app/niuu/issue/NIU-1107/add-a-principal-aware-a2a-agent-directory-to-observatory)
- Pull request: [#1171](https://github.com/niuulabs/niuu/pull/1171)
- Target: `regin` only
- Status: `In Progress` — blocked on required CI; do not merge
- Evidence captured: 2026-10-25

## Ancestry and actual implementation

At capture, `origin/regin` is `66c3d35e` and `origin/dev` is `f75d5b0b`.
`git merge-base --is-ancestor origin/dev origin/regin` succeeds, while the reverse
fails: `dev` is already an ancestor of `regin`. `git diff --stat
origin/regin...origin/dev` is empty. Therefore merging `dev` into `regin` would be
an empty merge and is neither needed nor performed.

The product implementation commits `4d4abb0f` (`feat(observatory): add
principal-aware A2A agent directory`) and `3a2c083d` (`fix(observatory): harden
agent directory identity`) are ancestors of `origin/regin`. PR #1171's prior head
contained only delivery evidence and a two-line A2A-documentation correction; no
NIU-1107 product code is missing from `regin`.

## Current A2A guidance

The official current A2A discovery guide continues to state that curated registry
APIs are not standardized. The standard is an Agent Card discoverable at
`/.well-known/agent-card.json`; it recognizes registries/catalogs but does not
specify their API. Thus the Observatory and Guild endpoints remain explicitly
Niuu-specific discovery APIs, while their referenced cards retain standard A2A
semantics. The implementation uses the official SDK/schema path, preserves cache
validators and signatures, and keeps Niuu placement/visibility metadata out of
cards.

- [A2A Agent Discovery](https://a2a-protocol.org/latest/topics/agent-discovery/)
- [A2A specification](https://a2a-protocol.org/latest/specification/)
- [A2A Python SDK card resolver](https://a2a-protocol.org/latest/sdk/python/api/a2a.client.card_resolver.html)
