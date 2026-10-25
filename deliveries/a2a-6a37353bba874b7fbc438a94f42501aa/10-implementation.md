# NIU-1107 reconciliation verification

## Scope result

The agent-directory implementation is present on `regin`. Focused verification on 2026-10-25 independently passed:

```text
uv run --extra dev pytest -q tests/test_observatory/test_agent_directory.py tests/test_observatory/test_app.py tests/test_observatory/test_entity_discovery.py tests/test_observatory/test_plugin.py tests/test_niuu/test_agent_directory.py tests/test_niuu/test_rest_instances.py tests/test_volundr/test_session_response_a2a.py
185 passed in 3.66s

cd web-next && corepack pnpm exec vitest run packages/plugin-observatory/src/adapters/http.test.ts packages/plugin-observatory/src/application/useAgents.test.tsx packages/plugin-observatory/src/domain/agentDirectory.test.ts --coverage=false
3 files passed, 22 tests passed in 1.62s
```

The focused tests cover local filters and visibility/cache isolation; official Agent Card parsing, cache validators and signatures; workflow-session topology identity; Guild filtering, caller forwarding, provenance, collision reconciliation and partial-source warnings; and the UI aggregate/topology data layer.

## Required-CI blocker

PR [#1171](https://github.com/niuulabs/niuu/pull/1171) targets `regin`, but its CI run [40138560609](https://github.com/niuulabs/niuu/actions/runs/40138560609) is not green. It fails outside this ticket in three checks:

- `Python / Python: Lint`: `ruff format --check src/ tests/` reports formatting drift in `src/ting/domain/services/workflow_campaign_projector.py`, `tests/test_ting/test_system_workflows.py`, and `tests/test_ting/test_workflow_campaign_projector.py`.
- `Python / Python: Test Ting`: released bundled workflow `3f444455-6b63-53c2-b057-3205345a56da@1.0.0` differs from its lock.
- `Python / Python: Test Ravn`: `TestBuildMimirTools.test_build_without_extractor` expects 7 tools but receives 8.

Each failure reproduces unchanged at `origin/regin`: the PR has no diff for the affected files. Correcting any one is unrelated to NIU-1107 and requires an operator scope decision. Do not merge #1171 until required CI is green.

The CI run [40148007518] created for `c62a9f83`(https://github.com/niuulabs/niuu/actions/runs/40148007518) and Forge Stability run [40148007507](https://github.com/niuulabs/niuu/actions/runs/40148007507) are pending for the current documentation-only head, so they are not evidence of green CI. The detailed failures below come from completed CI run [40138560609](https://github.com/niuulabs/niuu/actions/runs/40138560609), then independently reproduce at the exact `origin/regin` base.

## PR identity, target, and actual diff

PR [#1171](https://github.com/niuulabs/niuu/pull/1171) is open, non-draft, and
has no merge commit. Its base and merge target are both `regin`; the head is
`codex/niu-1107-regin-reconciliation`. At evidence capture, `origin/regin`
was `fd3fff955f3953a7769c7099229a576cc408c908` and `git merge-base
origin/regin HEAD` returned that same commit, proving `regin` is an ancestor
of the PR branch. The reconciliation is not based on, targeted at, or merged
into `dev` or `main`.

The entire PR diff is documentation/evidence, not product code:

| Path | Change |
| --- | --- |
| `deliveries/a2a-0d31cf27b5204368a545f3495329bf2d/00-ticket.md` | added reconciliation evidence |
| `deliveries/a2a-0d31cf27b5204368a545f3495329bf2d/10-implementation.md` | added reconciliation evidence |
| `deliveries/a2a-6a37353bba874b7fbc438a94f42501aa/00-ticket.md` | added ticket and operator-decision evidence |
| `deliveries/a2a-6a37353bba874b7fbc438a94f42501aa/10-implementation.md` | added verification and baseline evidence |
| `deliveries/a2a-6a37353bba874b7fbc438a94f42501aa/20-review.md` | added unresolved-CI review finding |
| `docs/operator/a2a-agent-directory.md` | corrected A2A version evidence |

The evidence commits are `566209bc` (record NIU-1107 regin verification),
`0087bdee` (correct A2A reconciliation evidence), `e8336b7f` (record CI
baseline), and `c62a9f83` (record review). The
actual NIU-1107 implementation commits `4d4abb0f`, `3a2c083d`, and `a992872f`
are all ancestors of `origin/regin`, so nothing is brought from `dev` by this
PR.

## Acceptance evidence matrix

| Acceptance criterion | Evidence on `origin/regin` |
| --- | --- |
| Latest A2A guidance recorded; no standard registry claimed | `00-ticket.md` records official Agent Discovery, specification, and releases; `docs/operator/a2a-agent-directory.md` identifies the Niuu API as a directory and A2A cards as standard payloads. |
| Existing entity-type registry unchanged | `TestObservatoryApp::test_registry_returns_seed_payload`, `test_registry_put_persists_changes`, and registry mutation tests passed. |
| Local list/detail plugin endpoints | `observatory/app.py` exposes `/api/v1/observatory/agents` and `/{agent_id}`; principal/filter and hidden-detail tests passed. |
| Guild aggregate list/detail through existing mechanisms | `niuu/adapters/inbound/rest_instances.py`, `guild/app.py`, and aggregate list/detail REST tests passed. |
| Stable IDs, provenance, card metadata, observation and visibility fields | `niuu/domain/agent_directory.py`, colliding-name/provenance, and topology-identity tests passed. |
| Local and aggregate filters | Observatory and Niuu forwarding tests cover skill, tag, kind, status, Environment, cluster, and instance. |
| Owner/tenant visibility, fan-out, detail, and cache isolation | Cross-tenant, fail-closed Environment membership, aggregate recheck, and principal cache-isolation tests passed. |
| Multi-instance provenance and collision-safe IDs | `test_aggregate_preserves_colliding_names_and_registry_provenance` passed. |
| Failed/slow source gives explicit partial result | `test_aggregate_returns_healthy_results_with_failed_source_warning` and partial-health test passed. |
| Display-name collisions separate; verified duplicates reconcile | Colliding-name, verified-card-identity, and different-signing-key tests passed. |
| One workflow session topology node and matching directory projection | Topology-identity and `test_volundr_sessions_adapter_projects_running_sessions` passed. |
| UI joins directory entries to topology with source context | `agentDirectory.test.ts`, `useAgents.test.tsx`, and `http.test.ts` passed (22 UI tests). |
| Invalid/unreachable cards warn without failing page | Visible-card-warning and invalid/credential-bearing-card tests passed. |
| Official Agent Card validation and no secret indexing | `observatory/a2a_cards.py`; validator, conditional-cache, trusted-origin, signature, and credential-bearing-card tests passed. |
| Dynamic local/Helm adapter configuration | `VolundrSessionsDiscoveryAdapter` configuration test and `tests/test_charts/test_service_chart_entrypoints.py` cover the configured path. |
| Contract/service/REST/adapter/Guild/UI/multi-instance coverage | Focused backend command ran seven Observatory/Niuu/Volundr files (185 passed); UI command ran three data-layer files (22 passed). |

## Required-CI baseline proof

The independent baseline was checked out at exactly `origin/regin`
(`fd3fff955f3953a7769c7099229a576cc408c908`) in a detached worktree. It
reproduced the three required-CI blockers without PR documentation:

```text
ruff format --check ...
  src/ting/domain/services/workflow_campaign_projector.py
  tests/test_ting/test_system_workflows.py
  tests/test_ting/test_workflow_campaign_projector.py
  -> 3 files would be reformatted

pytest -q tests/test_ting/test_bundled_workflow_versions_lock.py \
  tests/test_ravn/test_entity_extractor.py::TestBuildMimirTools::test_build_without_extractor
  -> 2 failed, 2 passed
  -> released workflow 3f444455-6b63-53c2-b057-3205345a56da@1.0.0 differs from lock
  -> expected Mimir tool count 7, actual 8
```

The completed CI log records: `3 files would be reformatted`; the Ting
assertion that the released `3f444455-6b63-53c2-b057-3205345a56da@1.0.0`
content changed and must be version-bumped/locked; and the Ravn assertion
`assert 8 == 7`. All eight implicated source/test/lock blobs are
byte-identical between `origin/regin` and the PR branch: the three formatting
files, `test_bundled_workflow_versions_lock.py`, `tracker-delivery-flow.yaml`,
its lock JSON, `test_entity_extractor.py`, and `mimir_tools.py`. The PR has
no path diff for any of them. This proves the failures pre-exist this
documentation-only reconciliation PR. The operator declined scope expansion;
leave them for separate triage, do not claim CI is green, and do not merge
until green CI and review acceptance exist.
