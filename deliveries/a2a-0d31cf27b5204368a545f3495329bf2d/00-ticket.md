# NIU-1107 — Regin reconciliation

- Tracker: [NIU-1107](https://linear.app/niuu/issue/NIU-1107/add-a-principal-aware-a2a-agent-directory-to-observatory)
- Base branch: `regin`
- Status at start: `In Progress`

## Scope

Verify that the principal-aware A2A Agent Directory is present on `regin`; preserve the existing Observatory entity-type registry; and record current official A2A discovery guidance.

## Branch reconciliation decision

`feat(observatory): add principal-aware A2A agent directory` (`4d4abb0f`), its identity hardening (`3a2c083d`), and HelmRelease metadata projection (`a992872f`) are already ancestors of `origin/regin`. They are also ancestors of `origin/dev`. Therefore no ticket implementation remains to bring from `dev`.

At verification, `origin/dev` had diverged from the merge base `dee8c851` with 143 changed files. Its changes cover unrelated Bifrost, Ravn, Ting, Skuld, Volundr, chart, and dependency behavior, including public/runtime contracts. Merging that branch would violate NIU-1107's restricted reconciliation scope, so it was deliberately not merged.

## Official A2A guidance check — 2026-10-25

The official A2A specification lists 1.0.1 as its latest released version. It standardizes Agent Cards and the `/.well-known/agent-card.json` discovery location. Its discovery guide explicitly permits curated registries but says it does not prescribe a standard registry API. NIU-1107's Observatory and Guild routes therefore remain documented Niuu-specific directory APIs, while Agent Cards retain the standard schema, caching, authentication, and signature semantics.

Sources:

- [A2A 1.0 specification](https://a2a-protocol.org/v1.0.1/specification)
- [A2A Agent Discovery guide](https://a2a-protocol.org/latest/topics/agent-discovery/)
- [A2A releases](https://github.com/a2aproject/A2A/releases)
