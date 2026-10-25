# NIU-1107 verification on `regin`

## Implementation and specification status

The principal-aware A2A Agent Directory implementation is present in `regin`
through [4d4abb0f](https://github.com/niuulabs/niuu/commit/4d4abb0f382f21022c6a7afe74f7f30e3c0bcae1),
[3a2c083d](https://github.com/niuulabs/niuu/commit/3a2c083d19cf01dc0dc86da21ccc0805976b91e9), and
[a992872f](https://github.com/niuulabs/niuu/commit/a992872ffbf0d8688a0bc4d6914cbcbee15219af).
They contain the full changed-file lists and diffs.  The surviving implementation
uses `a2a-sdk` 1.1.2 to parse Agent Cards; it does not use a parallel card model.

The latest official release is [A2A v1.0.1](https://github.com/a2aproject/A2A/releases/tag/v1.0.1).
The official [Agent Discovery guide](https://github.com/a2aproject/A2A/blob/main/docs/topics/agent-discovery.md)
standardizes self-description by Agent Card and the well-known URI, while treating
proprietary API discovery as non-standard. The Niuu list/detail APIs are therefore
documented as Niuu-specific directory APIs; they preserve the standard card URL,
interfaces, cache validators, signatures, authentication and extended-card access
rather than claiming a standard registry protocol.

## Acceptance evidence

| Criterion | Evidence on `regin` |
| --- | --- |
| Official discovery/registry check and Niuu-specific status | The official links above and `docs/operator/a2a-agent-directory.md`; regression documentation is in the implementation commits. |
| Existing entity-type registry unchanged; local list/detail routes | `src/observatory/app.py`; `tests/test_observatory/test_app.py` in the 185-test run. |
| Guild list/detail through existing instances | `src/niuu/adapters/inbound/rest_instances.py`, `AgentDirectoryAggregationService`; `tests/test_niuu/test_rest_instances.py`. |
| Stable aggregate/source identity and provenance metadata | `src/niuu/domain/agent_directory.py`; `tests/test_niuu/test_agent_directory.py`. |
| Skill, tag, kind, status, Environment, cluster and instance filters | Shared `AgentDirectoryFilters`, local projection and HTTP propagation; Observatory, Niuu and REST tests in the 185-test run. |
| Owner, tenant, Environment and cache isolation; inaccessible detail | `test_directory_hides_cross_tenant_owner_and_environment_non_member`, `test_directory_fails_closed_without_authoritative_environment_membership`, `test_http_resolver_isolates_principals_and_honors_no_store`, and Guild visibility/detail tests. |
| Multi-instance provenance, collision safety and deterministic verified duplicate handling | `test_aggregate_preserves_colliding_names_and_registry_provenance`, `test_aggregate_reconciles_only_verified_card_identity`, and `test_aggregate_does_not_merge_same_card_signed_by_different_keys`. |
| Failed/slow source remains explicit partial result | Bounded semaphore fan-out and `test_aggregate_returns_healthy_results_with_failed_source_warning`. |
| Duplicate display names remain separate | `test_aggregate_preserves_colliding_names_and_registry_provenance`. |
| One topology entity and one directory projection | `test_directory_filters_before_card_fetch_and_preserves_topology_identity`; Volundr response tests. |
| UI topology linkage plus cluster/instance context | `agentDirectory.ts` and `useAgents.ts`; 22 focused Vitest assertions below. |
| Invalid/unreachable card is bounded warning | `test_directory_returns_visible_card_warning_without_failing_page` and `test_http_resolver_rejects_invalid_or_credential_bearing_cards`. |
| Official validation, safe cards and signature provenance | `a2a.client.card_resolver.parse_agent_card`, trusted-origin forwarding and `test_http_resolver_verifies_signed_agent_card`. |
| Shared dynamic local/Helm configuration | Observatory/Guild chart changes in 4d4abb0f and `tests/test_config.py`/chart entrypoint coverage. |
| Contract, service, REST, adapter, aggregation and UI coverage | The backend and UI commands below. |

## Local verification

```text
$ uv run --extra dev pytest -q tests/test_observatory/test_agent_directory.py \
    tests/test_observatory/test_app.py tests/test_observatory/test_entity_discovery.py \
    tests/test_observatory/test_plugin.py tests/test_niuu/test_agent_directory.py \
    tests/test_niuu/test_rest_instances.py tests/test_volundr/test_session_response_a2a.py
185 passed in 3.72s

$ cd web-next && corepack pnpm exec vitest run \
    packages/plugin-observatory/src/adapters/http.test.ts \
    packages/plugin-observatory/src/application/useAgents.test.tsx \
    packages/plugin-observatory/src/domain/agentDirectory.test.ts --coverage=false
3 files passed, 22 tests passed in 1.65s
```

Vitest emitted the pre-existing Vite `__dirname` future-default warning; no test
failed. The current PR head's full required CI is also complete as listed below,
so focused checks are not being represented as required-check results.

## Current-head PR CI

PR [#1171](https://github.com/niuulabs/niuu/pull/1171) is open, non-draft,
mergeable and targets `regin`. Its checked head is
[`a7f8a702`](https://github.com/niuulabs/niuu/commit/a7f8a7022ac48990859d4596df94b52c768bfab8).
The GitHub check API reported 56 runs: 54 `success` and two intentionally
`skipped` manifest jobs; `ci-gate` succeeded. Each current-head result and log:

| Check | Result | Log |
| --- | --- | --- |
| `Build Helm Charts` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105248418 |
| `ci-gate` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121106782066 |
| `Containers (amd64) / Build agent (amd64)` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105189431 |
| `Containers (amd64) / Build devrunner (amd64)` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105189446 |
| `Containers (amd64) / Build gbrain (amd64)` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105189296 |
| `Containers (amd64) / Build niuu-web (amd64)` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105189332 |
| `Containers (amd64) / Build openshell (amd64)` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105189406 |
| `Containers (amd64) / Manifests` | skipped | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105794522 |
| `Containers (arm64) / Build agent (arm64)` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105820649 |
| `Containers (arm64) / Build devrunner (arm64)` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105820736 |
| `Containers (arm64) / Build gbrain (arm64)` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105820687 |
| `Containers (arm64) / Build niuu (arm64)` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105820666 |
| `Containers (arm64) / Build niuu-web (arm64)` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105820701 |
| `Containers (arm64) / Build openshell (arm64)` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105820698 |
| `Containers (arm64) / Build skuld (arm64)` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105820697 |
| `Containers (arm64) / Manifests` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121106347741 |
| `Containers (smoke-test images) / Build niuu (amd64)` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105189363 |
| `Containers (smoke-test images) / Build skuld (amd64)` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105189312 |
| `Containers (smoke-test images) / Manifests` | skipped | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105821596 |
| `Dependency Review` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105189160 |
| `Detect Changes` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105189287 |
| `Forge browser and web contracts` | success | https://github.com/niuulabs/niuu/actions/runs/40172187211/job/121105188828 |
| `Forge PostgreSQL boundaries` | success | https://github.com/niuulabs/niuu/actions/runs/40172187211/job/121105189071 |
| `Forge tmux / Python 3.12` | success | https://github.com/niuulabs/niuu/actions/runs/40172187211/job/121105189092 |
| `Forge unit / Python 3.12` | success | https://github.com/niuulabs/niuu/actions/runs/40172187211/job/121105188958 |
| `grype` | success | https://github.com/niuulabs/niuu/runs/121106234925 |
| `Helm Lint / Helm: Lint` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105248719 |
| `Helm: Smoke Test` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105820510 |
| `Python / Mímir: Retrieval eval (gated)` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105248579 |
| `Python / Python: Lint` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105248526 |
| `Python / Python: Test Bifrost` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105248693 |
| `Python / Python: Test Ravn` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105248744 |
| `Python / Python: Test Sleipnir` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105248696 |
| `Python / Python: Test Ting` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105248730 |
| `Python / Python: Test Volundr` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121106348246 |
| `Python / Python: Test Volundr shard 1/4` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105248782 |
| `Python / Python: Test Volundr shard 2/4` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105248656 |
| `Python / Python: Test Volundr shard 3/4` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105248697 |
| `Python / Python: Test Volundr shard 4/4` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105248613 |
| `Python: Integration Test Sleipnir` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105248420 |
| `Python: Integration Test Ting` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105248501 |
| `Python: Integration Test Volundr` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105248408 |
| `Scan / Scan: agent` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105820637 |
| `Scan / Scan: devrunner` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105820627 |
| `Scan / Scan: gbrain` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105820783 |
| `Scan / Scan: niuu` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105820725 |
| `Scan / Scan: niuu-web` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105820645 |
| `Scan / Scan: openshell` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105820745 |
| `Scan / Scan: skuld` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105820738 |
| `web-next / web-next: Lint` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105248562 |
| `web-next / web-next: Test` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121106309708 |
| `web-next / web-next: Test shard 1/3` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105248686 |
| `web-next / web-next: Test shard 2/3` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105248705 |
| `web-next / web-next: Test shard 3/3` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105248742 |
| `web-next / web-next: Typecheck` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105248568 |
| `web-next: Publish dry-run + Consumer E2E` | success | https://github.com/niuulabs/niuu/actions/runs/40172187226/job/121105248415 |

## Gate

No implementation or CI blocker remains. PR #1171 is intentionally left open and
unmerged pending the requested reviewer gate; no PR was opened, merged, pushed or
prepared against `dev` or `main`.


## Security revision — default-deny card retrieval (2026-10-25)

Reviewer finding `20-review.md` is addressed with a distinct, deployment-owned
`directory.allowedCardOrigins` policy. It defaults to `[]`, so Observatory makes
no Agent Card or JWKS request until an operator allowlists the exact HTTP(S)
origin. `authenticatedCardOrigins` remains independent and only controls
forwarding caller authentication to an already allowed origin. Both initial
card URLs and `jku` URLs are validated before `httpx` is invoked; rejected URLs
produce the fixed bounded warning `Agent Card retrieval origin is not allowed`.

Changed product/configuration files:

- `src/observatory/a2a_cards.py`
- `src/observatory/app.py`
- `src/volundr/config.py`
- `charts/observatory/{values.yaml,templates/configmap.yaml}`
- `charts/guild/{values.yaml,templates/configmap.yaml}`
- `docs/operator/a2a-agent-directory.md`

Regression coverage in `tests/test_observatory/test_agent_directory.py` proves
loopback, link-local, RFC1918, and unallowlisted Card origins make no request;
it also proves a blocked Card yields only the fixed directory warning and that a
signed Card cannot cause an unallowlisted `jku` request. Configuration and chart
coverage verify the policy is rendered through the shared settings shape.

Official A2A discovery and release guidance were rechecked on 2026-10-25:
version 1.0.1 remains latest and the guidance continues to describe curated
registries while explicitly not prescribing a standard registry API. The existing
Niuu-specific directory contract remains appropriate.

### Revision verification

```text
$ uv run ruff check src/observatory/a2a_cards.py src/observatory/app.py \
    src/volundr/config.py tests/test_observatory/test_agent_directory.py \
    tests/test_config.py tests/test_charts/test_service_chart_entrypoints.py
All checks passed!

$ uv run --extra dev pytest -q tests/test_observatory/test_agent_directory.py \
    tests/test_niuu/test_agent_directory.py \
    tests/test_volundr/test_session_response_a2a.py tests/test_config.py \
    tests/test_charts/test_service_chart_entrypoints.py
148 passed in 1.61s
```

CI for the post-revision `regin` head must be reviewed before merge approval;
this local verification is not represented as required-check status.

### Handoff

The security implementation is commit
[`7f201253`](https://github.com/niuulabs/niuu/commit/7f201253); after integrating
an unrelated upstream `regin` documentation commit, its pushed branch head is
[`b37866de`](https://github.com/niuulabs/niuu/commit/b37866de). The existing
hosted PR remains [#1171](https://github.com/niuulabs/niuu/pull/1171), targeting
`regin`; the requested re-review, including the precise security commit and
focused test result, is recorded in [its review comment](https://github.com/niuulabs/niuu/pull/1171#issuecomment-6396023556).

Immediately after the push, `gh run list --branch regin --commit b37866de`
reported no current-head workflow runs. The prior PR checks belong to its older
documentation head and are not evidence for this security revision. No merge was
performed; review and post-push CI are still required before approval.
