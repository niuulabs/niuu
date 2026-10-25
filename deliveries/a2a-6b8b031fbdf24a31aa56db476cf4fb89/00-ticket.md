# NIU-1107 — regin reconciliation

- Tracker: [NIU-1107](https://linear.app/niuu/issue/NIU-1107/add-a-principal-aware-a2a-agent-directory-to-observatory)
- Existing PR: [#1171](https://github.com/niuulabs/niuu/pull/1171), `codex/niu-1107-regin-reconciliation` -> `regin`.
- Scope: reconcile the already-completed Agent Directory implementation onto `regin`; no implementation is recreated and no PR targets `dev` or `main`.

## A2A guidance check — 2026-10-25

The official [A2A Agent Discovery guide](https://a2a-protocol.org/latest/topics/agent-discovery/)
continues to describe Agent Cards, well-known discovery, and deployment-specific curated
registries. It explicitly states that the current specification does not prescribe a curated
registry API. The official [release page](https://github.com/a2aproject/A2A/releases) identifies
v1.0.1 as latest. Therefore the Observatory and Guild endpoints remain documented as Niuu-specific
metadata directories while using the official SDK for Agent Card parsing and preserving card URLs.
