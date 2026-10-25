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
