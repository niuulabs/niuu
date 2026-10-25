# NIU-1107 reconciliation verification

## Actual product diff on `regin`

The implementation landed before this delivery pass in `4d4abb0f` and was
hardened in `3a2c083d`. It changes the Observatory discovery/domain/service/HTTP
and plugin paths, Niuu Guild aggregation/auth/cache paths, Volundr session
discovery response, Helm configuration, and the `web-next` Observatory data
layer. It does not add A2A task execution, public registration, a federation
service, a UI redesign, or persistence.

The current PR diff is delivery evidence plus the existing operator-document
correction; no NIU-1107 product source is being duplicated. `git diff --stat
origin/regin...HEAD` before this record showed eight prior evidence/doc files,
364 additions, and two deletions. This pass adds the two artifacts in this
directory after rebasing onto `origin/regin` `b746f9d5`.

## Focused verification on current `regin`

```text
$ uv run pytest -q tests/test_observatory/test_agent_directory.py \
    tests/test_observatory/test_app.py tests/test_niuu/test_agent_directory.py \
    tests/test_niuu/test_rest_instances.py tests/test_volundr/test_session_response_a2a.py \
    tests/test_config.py
159 passed, 1 warning in 3.93s

$ uv run ruff check src/observatory/agent_directory.py src/observatory/a2a_cards.py \
    src/niuu/domain/agent_directory.py src/niuu/domain/services/agent_directory.py \
    src/niuu/adapters/inbound/rest_instances.py src/niuu/adapters/outbound/http_agent_directory.py \
    src/volundr/adapters/inbound/rest.py tests/test_observatory/test_agent_directory.py \
    tests/test_niuu/test_agent_directory.py tests/test_niuu/test_rest_instances.py \
    tests/test_volundr/test_session_response_a2a.py
All checks passed!

$ cd web-next && corepack pnpm exec vitest run \
    packages/plugin-observatory/src/adapters/http.test.ts \
    packages/plugin-observatory/src/application/useAgents.test.tsx \
    packages/plugin-observatory/src/domain/agentDirectory.test.ts \
    apps/niuu/src/services.test.ts
Test Files  4 passed (4)
Tests  101 passed (101)
```

The pytest warning is the existing Starlette TestClient/httpx deprecation in
`tests/test_observatory/test_app.py`. Vitest reports an existing Vite
`__dirname` future-default warning. Neither is a test failure.

## Acceptance-criteria assessment

| Acceptance criterion | Evidence |
| --- | --- |
| A2A guidance and Niuu-specific documentation | `docs/operator/a2a-agent-directory.md`, guidance check in `00-ticket.md`, official SDK in `pyproject.toml` |
| Registry remains unchanged; local list/detail plugin routes | `observatory/app.py`; `tests/test_observatory/test_app.py` |
| Guild list/detail uses existing instances | `niuu/adapters/inbound/rest_instances.py`, `AgentDirectoryAggregationService`; REST tests |
| Stable provenance and full card/observed metadata | shared `AgentDirectoryEntry` and `AgentProvenance` contracts; directory tests |
| All local and aggregate filters | shared `AgentDirectoryFilters`, HTTP propagation, focused Observatory/Niuu/REST tests |
| Owner, tenant, Environment, detail, and cache isolation | local visibility, Guild recheck, principal-keyed card cache tests |
| Collision safety and deterministic verified reconciliation | aggregate collision, verified-card, and different-key tests |
| Bounded failed/slow source partial result | semaphore fan-out, source health/warning tests |
| Duplicate names stay separate | `test_aggregate_preserves_colliding_names_and_registry_provenance` |
| Single topology and directory projection | Volundr session adapter and `topologyNodeId` assertion test |
| UI topology association/context | `agentDirectory.ts`, `useAgents.ts`, HTTP/services Vitest tests |
| Invalid/unreachable cards remain bounded | resolver validation and directory warning tests |
| Official schema plus no credential/internal inventory indexing | `a2a.client.card_resolver.parse_agent_card`, URL validation, resolver tests |
| Dynamic configuration shared by local/Helm | discovery adapter configuration and Observatory/Guild chart tests |
| Contract/service/REST/adapter/Guild/UI/multi-instance coverage | the 159 pytest and 101 Vitest focused passing tests above |

All code-facing criteria are represented by the current `regin` implementation
and named focused tests. The remaining delivery criteria are external: CI must
run for the rebased PR head and an authorized reviewer must review PR #1171.
