# NIU-1107 reconciliation verification

## Focused verification (2026-10-25)

```text
$ uv run pytest -q tests/test_observatory/test_agent_directory.py tests/test_observatory/test_app.py tests/test_niuu/test_agent_directory.py tests/test_niuu/test_rest_instances.py tests/test_volundr/test_session_response_a2a.py tests/test_config.py
159 passed, 1 warning in 4.52s

$ uv run ruff check src/observatory/agent_directory.py src/observatory/a2a_cards.py src/niuu/domain/agent_directory.py src/niuu/domain/services/agent_directory.py src/niuu/adapters/inbound/rest_instances.py src/niuu/adapters/outbound/http_agent_directory.py src/volundr/adapters/inbound/rest.py tests/test_observatory/test_agent_directory.py tests/test_niuu/test_agent_directory.py tests/test_niuu/test_rest_instances.py tests/test_volundr/test_session_response_a2a.py
All checks passed!

$ cd web-next && corepack pnpm exec vitest run packages/plugin-observatory/src/adapters/http.test.ts packages/plugin-observatory/src/application/useAgents.test.tsx packages/plugin-observatory/src/domain/agentDirectory.test.ts apps/niuu/src/services.test.ts
Test Files  4 passed (4)
Tests  101 passed (101)
```

The pytest warning is Starlette's existing `httpx` TestClient deprecation from
`tests/test_observatory/test_app.py`; it does not fail the focused suite and was
not introduced by this reconciliation.

## Acceptance evidence matrix

| Criterion | Evidence on `regin` |
| --- | --- |
| Local list/detail and unchanged entity registry | Observatory plugin registers `/api/v1/observatory/agents`; `observatory/app.py` supplies list/detail while existing registry tests pass. |
| Real workflow-session source, no duplicate node | `VolundrSessionsDiscoveryAdapter` supplies only configured live session `a2aCard`; `topologyNodeId` is the existing discovery entity ID. |
| Card correctness and bounded failures | `observatory/a2a_cards.py` uses the A2A SDK resolver; focused tests cover invalid cards, credential-bearing URLs, signature verification, ETag/conditional cache, trusted auth forwarding, and warnings. |
| Local filters and principal isolation | `AgentDirectoryService` filters kind/status/Environment/cluster/instance before cards, then skill/tag; tests cover owner, tenant, Environment membership, hidden detail, and principal cache keys. |
| Guild aggregate list/detail and auth forwarding | `AgentDirectoryAggregationService` fans out through registered Observatory instances with bounded concurrency and forwarded headers; REST tests cover both routes. |
| Provenance and collisions | `AgentDirectoryEntry` retains aggregate/source IDs, instance, cluster, Environment, card hash/signature, and topology ID; tests keep duplicate display names separate and merge only verified equivalent signed cards. |
| Partial results | Failed/local degraded sources produce source-scoped warnings and source health while healthy entries remain. |
| UI data layer | The Observatory DI port, HTTP adapter, `useAgents`, and topology join helper preserve aggregate placement and topology association; the focused Vitest suite passes. |
| Dynamic config and boundaries | Discovery remains configured through Observatory's adapter configuration and Helm values; domain services use Niuu ports, Guild composes HTTP adapters, and no Ting-to-Volundr dependency is introduced. |

## Required-CI gate — blocking

Before this reconciliation update, PR #1171's latest completed CI run was
[40148731328](https://github.com/niuulabs/niuu/actions/runs/40148731328). Its
completed checks include successes for all web, Volundr, Helm, container, Forge,
and security jobs, but required CI is red:

```text
Python / Python: Lint
  ruff format --check reports 3 unrelated files would be reformatted:
  src/ting/domain/services/workflow_campaign_projector.py
  tests/test_ting/test_system_workflows.py
  tests/test_ting/test_workflow_campaign_projector.py

Python / Python: Test Ting
  1 failed, 3027 passed
  test_bundled_workflow_content_matches_its_locked_version:
  released workflow 3f444455-6b63-53c2-b057-3205345a56da@1.0.0 differs from its lock.

Python / Python: Test Ravn
  1 failed, 8358 passed, 5 skipped
  TestBuildMimirTools.test_build_without_extractor: expected 7 tools, got 8.
```

These paths are outside PR #1171's product diff. The currently fetched `dev` tip
cannot fix the blocker by merge: it is already an ancestor of `regin`; all directly
implicated blobs except a `regin`-only test formatting revision are identical, and
merging `dev` yields no change. No unrelated CI repair was added to NIU-1107.

This existing PR is rebased onto `origin/regin` and receives this evidence only.
Its new current-head CI must complete green before the review gate can approve or
merge it. Until then, NIU-1107 and dependent NIU-1116 remain blocked; no merge has
been performed and the tracker remains `In Progress`.
