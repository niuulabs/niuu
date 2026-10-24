# NIU-1107 verification on regin

## Implementation provenance

The complete directory implementation is already in the `regin` history:

- `4d4abb0f feat(observatory): add principal-aware A2A agent directory`
- `3a2c083d fix(observatory): harden agent directory identity`
- `a992872f fix(observatory): project A2A metadata from HelmReleases`

The implementation uses the official `a2a-sdk` (installed version `1.1.2`) for Agent Card parsing. It projects workflow-session entities into the directory without a duplicate topology node, principal-filters before card resolution, respects ETags/conditional cache validation, isolates card caches by principal, verifies supported signatures, and aggregates through Guild's existing instance facade with source provenance and partial-source warnings. The UI data layer retains topology, cluster, and instance identity. Observatory's existing `/api/v1/observatory/registry` is unaffected.

`docs/operator/a2a-agent-directory.md` records the Niuu-specific registry status, official discovery semantics, safe card handling, endpoints, and shared Observatory/Guild dynamic-adapter configuration. This pass corrected its current-protocol statement to the official 1.0.0 release.

## Verification artifacts

Commands were run from this reconciliation checkout on 2026-10-24:

```text
uv run --extra dev pytest -q tests/test_observatory/test_agent_directory.py tests/test_observatory/test_app.py tests/test_observatory/test_entity_discovery.py tests/test_observatory/test_plugin.py tests/test_niuu/test_agent_directory.py tests/test_niuu/test_rest_instances.py tests/test_volundr/test_session_response_a2a.py
185 passed in 3.74s

cd web-next && corepack pnpm exec vitest run packages/plugin-observatory/src/adapters/http.test.ts packages/plugin-observatory/src/application/useAgents.test.tsx packages/plugin-observatory/src/domain/agentDirectory.test.ts --coverage=false
3 files passed, 22 tests passed in 1.62s
```

Backend coverage includes local filters, owner/tenant/Environment filtering, inaccessible detail behavior, principal-scoped cache behavior, ETag conditional revalidation, signature validation, invalid-card warnings, topology identity, Guild authorization forwarding, collision and verified-duplicate rules, and partial source failures. The UI tests cover aggregate-agent decoding and topology association.

## Delivery decision

The current `dev` tip is intentionally not merged: its 143-file divergence is unrelated to this ticket and changes public/runtime behavior outside the permitted reconciliation. The ticket implementation was already present on `regin`; this branch only records the current official-protocol verification and reconciliation evidence.

## Pull request and CI status

PR [#1171](https://github.com/niuulabs/niuu/pull/1171) targets `regin` from
`codex/niu-1107-regin-reconciliation`. The initial GitHub Actions run reported
`Python / Python: Lint` failed specifically at `ruff format --check src/ tests/`.
The branch has no `src/` or `tests/` diff from `origin/regin`; the same command
locally reports pre-existing formatting drift in
`src/ting/domain/services/workflow_campaign_projector.py`,
`tests/test_ting/test_system_workflows.py`, and
`tests/test_ting/test_workflow_campaign_projector.py`. This ticket must not
reformat those unrelated files, so full CI cannot be green until that base-branch
drift is repaired separately. All NIU-1107-focused checks above passed.
